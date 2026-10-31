"""Where a tenant's agent is reachable — derived from stored state, once.

One function answers it, and everything that publishes or byte-matches an agent URL
reads that one answer. The point is not tidiness: a tenant that answered on several
hosts, or whose scheme was taken from a request header, would publish several
identities, and a counterparty comparing the URL it invoked against the one we
published would fail with no diagnostic.

So the scheme and host come from the tenant row, never from a request header —
not ``Host``, not ``X-Forwarded-Proto``. Every header is caller-supplied on a direct
connection, so a card derived from one publishes whatever host the caller asked for.

There is no default under the row either. A tenant always declares a host —
``Tenant.virtual_host`` refuses a blank and the column refuses NULL — so there is
nothing for a default to answer, and :func:`canonical_agent_url` cannot return a name
the tenant does not live at. The one place a host is still derived is
:func:`deployment_virtual_host`, which runs at CREATION for the tenant a deployment
bootstraps for itself and STORES its answer; see its own note for why deriving there
is sound and deriving at publish time is not (#1845).

Scope. This module is the DERIVATION, plus the one comparison against it: whether a
publisher's adagents.json entry names this agent (:func:`identifies_agent`). The agent card reads it
through :mod:`src.services.seller_capabilities`, which is also what
``get_adcp_capabilities`` renders from, so the card and the tool cannot name
different URLs for one seller. The trust-root documents that also build on this
origin — brand.json, adagents.json, the JWKS, and the entry ids addressing them —
belong to the RFC 9421 signing work (#1291) and live on its branch together with the
signing-key repository and migration they need. They are not re-declared here with
no caller.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlsplit

from src.core.config import get_settings
from src.core.domain_config import _get_protocol_for_domain
from src.core.signing.canonical import origin_of

# The paths this agent PUBLISHES itself at, keyed by transport. ONE spelling each, and
# it is the spelling the spec's own worked example publishes: ``seller-setup.mdx`` writes
# ``https://ads.streamhaus.example/mcp`` in both of the places a publisher copies from —
# ``brand.json`` ``agents[].url`` and ``adagents.json`` ``authorized_agents[].url``.
#
# PUBLISHED, not served-after-redirect. The two differ for MCP: ``/mcp`` is a Starlette
# ``Mount`` and 307s to ``/mcp/``, while ``/a2a`` answers where it is (``src/app.py``).
# A redirect is how a counterparty REACHES an endpoint and says nothing about which
# string IDENTIFIES it: security.mdx's key-discovery step 5 matches a canonicalized
# ``agents[].url`` against the canonicalized agent URL the counterparty HOLDS, and
# there "a trailing slash remains significant under the shared algorithm". Publishing
# the post-redirect ``/mcp/`` therefore meant a buyer holding the spec example's
# ``/mcp`` matched no entry of ours at all — ``request_signature_agent_not_in_brand_json``
# for a buyer that had done everything the documentation told it to.
AGENT_ENDPOINT_PATHS: dict[str, str] = {"mcp": "/mcp", "a2a": "/a2a"}

#: Where the A2A agent card is served. A2A FIXES this path (§8.2, §14.3) and the a2a-sdk
#: factory mounts it. ONE path: ``src/app.py`` routes it, the tenant landing page links it
#: and the e2e suite reads it, all from here, so no second spelling can diverge. The
#: non-canonical ``/.well-known/agent.json`` that AdCP's guide names was served too and is
#: not — a client that follows the guide reaches the card through its own fallback
#: (``@adcp/sdk``'s ``buildCardUrls`` tries both and breaks on the first success).
AGENT_CARD_PATH = "/.well-known/agent-card.json"

BRAND_JSON_PATH = "/.well-known/brand.json"
ADAGENTS_JSON_PATH = "/.well-known/adagents.json"
JWKS_PATH = "/.well-known/jwks.json"
#: The combined revocation list this seller PUBLISHES as a signer (security.mdx :1543). The
#: literal matches the URI ``CachingRevocationChecker.from_issuer_origin`` derives
#: (``adcp/signing/revocation_fetcher.py``) — a parity test pins this against that private
#: derivation, since unlike the three paths above the SDK exposes no path CONSTANT to import.
GOVERNANCE_REVOCATIONS_PATH = "/.well-known/governance-revocations.json"

# ``brand_agent_entry.id`` is ``^[a-z0-9_]+$``, maxLength 100. Tenant ids and hosts
# routinely carry hyphens, which are ILLEGAL there.
_ID_ILLEGAL = re.compile(r"[^a-z0-9]+")
_AGENT_ENTRY_ID_MAX_LENGTH = 100


class DeclaresHost(Protocol):
    """Anything that carries the one column this module reads.

    A tenant reaches this function in two shapes — the ORM row an admin view holds and
    the ``TenantContext`` projection the resolver hands a tool — and both already carry
    the host. Naming the ATTRIBUTE rather than either class is what lets every caller
    pass the row it is already holding instead of re-loading the other shape.
    """

    @property
    def virtual_host(self) -> str: ...


class DeclaresIdentity(DeclaresHost, Protocol):
    """A tenant that also names itself — what an entry id is built from.

    Named as an attribute rather than as the ORM class for the same reason
    :class:`DeclaresHost` is: both shapes a caller already holds satisfy it.
    """

    @property
    def tenant_id(self) -> str: ...


def canonical_agent_url(tenant: DeclaresHost) -> str:
    """The tenant's canonical ORIGIN — scheme + host, no path, no trailing slash.

    An anchor rather than an endpoint: every URL this agent publishes for *tenant* is
    this string plus a path from :data:`AGENT_ENDPOINT_PATHS`.

    ONE source, and no ladder under it: ``virtual_host``, the origin the tenant says it is
    served at (it may carry a port, because the card publishes this string and a client
    connects to what the card says). Stored state, never request state, and never derived.

    A second derivation of "where is this tenant" is a second chance to be wrong about it,
    and the tenant already answers the question — so the column is mandatory
    (``Tenant.virtual_host`` refuses a blank) and this reads it verbatim (#1845). This is a
    read of one column: nothing here opens a session.
    """
    return f"{_get_protocol_for_domain(tenant.virtual_host)}://{tenant.virtual_host}"


def agent_endpoint_urls(tenant: DeclaresHost) -> dict[str, str]:
    """The URLs a counterparty invokes this tenant at, keyed by transport.

    One entry per endpoint this agent publishes, in the one spelling
    :data:`AGENT_ENDPOINT_PATHS` gives it. brand.json publishes one ``agents[]`` entry per
    member of this mapping, so the canonical-equal match at security.mdx's key-discovery
    step 5 succeeds for a caller of either transport — and succeeds for exactly one entry,
    which is what that step requires (two matches are ``request_signature_brand_json_ambiguous``).
    """
    return _endpoint_urls_at(canonical_agent_url(tenant))


def _endpoint_urls_at(origin: str) -> dict[str, str]:
    return {transport: origin + path for transport, path in AGENT_ENDPOINT_PATHS.items()}


def identifies_agent(entry_url: object, agent_url: str) -> bool:
    """Whether an adagents.json ``authorized_agents[].url`` names the agent at *agent_url*.

    *agent_url* is the tenant's ``agent_url`` (its origin). The entry names this agent when
    it canonicalizes to that origin or to one of the endpoints under it. The pinned spec
    calls the entry the "Agent's API endpoint URL" (``adagents.mdx``), its seller-setup
    example authorizes ``https://ads.streamhaus.example/mcp``, and
    ``url-canonicalization.mdx`` keeps the path, so comparing against the origin alone
    reads a publisher that wrote our MCP or A2A endpoint as authorizing nobody.

    ONE spelling per endpoint and no tolerance around it. The entry is canonicalized,
    each URL this agent publishes is canonicalized, and the two are either the same string
    or they name different agents. ``core/authorized-agent-base.json`` lists the
    equivalences exhaustively — two urls "that differ only in case, default port, or
    percent-encoding of unreserved characters are the same agent" — and security.mdx's
    key-discovery step 5 closes the question this function used to leave open: "A trailing
    slash remains significant under the shared algorithm." So ``/mcp/`` does not identify
    the agent published at ``/mcp``, any more than another host, scheme or port does.

    This accepted BOTH slash spellings of each endpoint until the one-spelling change, on
    the ground that the app answers at both. That was the "additional transformation before
    comparison" the algorithm forbids, wearing a set rather than an ``rstrip``: it granted
    the ``/a`` == ``/a/`` equivalence ``canonical_agent_url`` deliberately refuses, and for
    a bare authority its two entries were byte-identical anyway. What the app answers at is
    a routing fact; what identifies this agent is :data:`AGENT_ENDPOINT_PATHS`.

    Each side goes through ``src.core.schemas.canonical_agent_url``, the one
    implementation of the canonicalization algorithm. An entry it refuses (no host, a
    malformed authority) or a value that is not a string names nobody. A tenant
    *agent_url* it refuses is not a publisher's mistake, so it raises ``ValueError``
    rather than reading as "not authorized".
    """
    from src.core.schemas import canonical_agent_url as canonical_url

    origin = origin_of(agent_url)
    if origin is None:
        raise ValueError(f"agent_url {agent_url!r} has no origin to compare adagents.json entries against")
    served = {canonical_url(origin)} | {canonical_url(url) for url in _endpoint_urls_at(origin).values()}
    if not isinstance(entry_url, str) or not entry_url:
        return False
    try:
        return canonical_url(entry_url) in served
    except ValueError:
        return False


def adagents_scoped_to_agent(adagents_data: dict[str, Any], agent_url: str) -> dict[str, Any]:
    """*adagents_data* with only the ``authorized_agents`` entries that name *agent_url*.

    The SDK's ``verify_agent_authorization`` and ``get_properties_by_agent`` match an entry
    by its URL with the scheme and a trailing slash dropped, so an entry naming our
    ``/mcp`` endpoint never matches our origin. Projecting the document first lets them
    keep their resolution (selectors, revocations, the union over entries) while
    :func:`identifies_agent` alone decides which entries are ours: each kept entry carries
    *agent_url* as its ``url``, and every other key of the document is unchanged.

    A document whose ``authorized_agents`` is not a list is returned as is, so the SDK
    refuses it exactly as it did before.
    """
    entries = adagents_data.get("authorized_agents")
    if not isinstance(entries, list):
        return adagents_data
    ours = [
        {**entry, "url": agent_url}
        for entry in entries
        if isinstance(entry, dict) and identifies_agent(entry.get("url"), agent_url)
    ]
    return {**adagents_data, "authorized_agents": ours}


def agent_entry_id(tenant: DeclaresIdentity, transport: str) -> str:
    """The ``brand_agent_entry.id`` for this tenant's *transport* endpoint.

    Distinct per endpoint because the SDK's ``_pick_agent`` disambiguates same-type
    entries by ``id`` alone. Slugged to ``^[a-z0-9_]+$`` because the schema rejects the
    hyphens tenant ids routinely carry, and stable across deployments, because
    counterparties may pin it.
    """
    slug = _ID_ILLEGAL.sub("_", f"{tenant.tenant_id}_{transport}".lower()).strip("_")
    return slug[:_AGENT_ENTRY_ID_MAX_LENGTH]


@dataclass(frozen=True, slots=True)
class AgentIdentity:
    """One tenant's published identity: the origin, and the endpoints under it.

    Two facts about one identity. Callers want different ones — the admin
    authorized-properties view wants the ORIGIN, the agent card wants the A2A
    ENDPOINT — so this surface exposes both rather than a single "identity URL" that
    has to pick one and lose the distinction.
    """

    origin: str
    endpoints: dict[str, str]


def agent_identity_for_tenant(tenant: DeclaresHost) -> AgentIdentity:
    """*tenant*'s published identity. PURE: it reads no session.

    Takes an already-loaded row deliberately. A caller holding its own session must be
    able to derive identity INSIDE its own transaction — ``SigningKeyRepository.
    canonical_origin`` resolves the origin in the same transaction that produced the key
    row it is about to sign with, and an identity helper that opened a unit of work of
    its own silently breaks that (the flush-visibility test in
    ``tests/integration/test_signing_key_repository.py`` grades it).
    """
    return AgentIdentity(origin=canonical_agent_url(tenant), endpoints=agent_endpoint_urls(tenant))


def brand_json_url(tenant: DeclaresHost) -> str:
    """Where this tenant's brand.json is served, published as ``identity.brand_json_url``."""
    return canonical_agent_url(tenant) + BRAND_JSON_PATH


def adagents_json_url(tenant: DeclaresHost) -> str:
    """Where this tenant's adagents.json is served."""
    return canonical_agent_url(tenant) + ADAGENTS_JSON_PATH


def jwks_uri(tenant: DeclaresHost) -> str:
    """Where this tenant's JWKS is served.

    Emitted EXPLICITLY on every ``agents[]`` entry rather than relying on the verifier's
    documented default, so a verifier never has to reconstruct it.
    """
    return canonical_agent_url(tenant) + JWKS_PATH


def jwks_origin(tenant: DeclaresHost) -> str:
    """The origin the JWKS resolves at — ``identity.key_origins.request_signing``.

    Read from one place rather than re-literalled: a second spelling is a
    ``request_signature_key_origin_mismatch`` waiting to happen.
    """
    return canonical_agent_url(tenant)


def deployment_virtual_host() -> str | None:
    """The host a DEPLOYMENT declares itself served at, or None when it declares none.

    For the ONE tenant a deployment bootstraps for itself. Every other tenant is created by
    somebody who knows where it answers and states it; this exists because nobody is present
    at ``init_db`` time to state anything, and the column is mandatory.

    Deriving a host is sound HERE and nowhere else: this runs once, at creation, and its
    answer is STORED in a column an operator can see and correct. The same derivation at
    PUBLISH time runs on every request and puts a host on the card that no operator ever
    sees, for a tenant that may not live at it (#1845).

    Returns None in production declaring neither ``ADCP_AGENT_URL`` nor
    ``SALES_AGENT_DOMAIN``: such an install serves nothing by ``Host`` today, and storing
    ``localhost`` would give it a row that LOOKS configured and still serves nothing. A
    wrong stored host is worse than an absent tenant — it is #1845 one step earlier. The
    bootstraps skip creating the default tenant and log why.
    """
    runtime = get_settings().runtime
    if runtime.adcp_agent_url:
        # The netloc, not the URL: this is a HOST column, and the scheme is re-derived
        # from the host by _get_protocol_for_domain on the way back out.
        return urlsplit(runtime.adcp_agent_url).netloc or None
    if runtime.sales_agent_domain:
        return runtime.sales_agent_domain
    if not runtime.is_production:
        return f"localhost:{runtime.adcp_sales_port}"
    return None
