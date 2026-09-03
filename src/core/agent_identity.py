"""The ONE canonical agent identity for a tenant (#1291, trust-root work item A3).

Every trust-root document this agent publishes, and every URL a counterparty
byte-matches against, is derived from :func:`canonical_agent_url`. That is the
whole point of this module: the byte-equal ``agents[].url`` match a verifier
performs (AdCP 3.1.1 ``security.mdx``:1104 step 5, "no canonicalization at this
step"), the eTLD+1 origin binding (step 3), and the ``key_origins`` consistency
check can never disagree with each other or with where our keys actually
resolve — because they are one string plus a suffix, by construction.

Why a module rather than a helper next to its first caller: before this, FOUR
independent derivations existed (the agent card's per-request header ladder, the
A2A static default, the admin blueprint's PRODUCTION/localhost ladder, and the
env-only ``domain_config`` primitives). N derivations means N chances that the
URL we publish and the URL a counterparty invoked differ by a scheme, a port or
a trailing slash — and each of those is a failed verification with no
diagnostic.

Two rules that look like details and are not:

* **The endpoint paths are the paths the app actually serves.** Starlette mounts
  ``/mcp`` and answers ``GET /mcp`` with a 307 to ``/mcp/``; ``/a2a`` is a
  JSON-RPC route served at exactly that path. The counterparty invoked
  ``get_adcp_capabilities`` at one of those, so those are the strings that must
  appear in ``brand.json``. A trailing-slash mismatch is the spec's own worked
  failure example (``https://x.com/mcp`` vs ``https://x.com/mcp/``).
* **The scheme is derived from the host, never from request headers.**
  ``security.mdx`` step 10 forbids deriving identity from reverse-proxy routing
  state; a tenant that answers on several hosts would otherwise publish several
  different identities.

``ADCP_AGENT_URL`` survives here as a DEPLOYMENT-level base for installs that
have no per-tenant host at all — deliberately ranked BELOW the tenant's own
host, because a deployment-wide literal that overrode a per-tenant identity
would collapse every tenant onto one URL, which is the defect this module
exists to remove.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from src.core.domain_config import _get_protocol_for_domain, get_sales_agent_domain

if TYPE_CHECKING:  # pragma: no cover - typing only
    from src.core.database.models import Tenant

# The paths a counterparty actually reaches this agent at, keyed by transport.
# Values are what the running app resolves to AFTER any redirect it issues —
# ``/mcp`` 307s to ``/mcp/``, ``/a2a`` does not redirect. Changing a mount in
# ``src/app.py`` without changing this table republishes a brand.json whose
# entries byte-equal nothing, so the integration test discovers these from the
# running app rather than rebuilding them the way this module does.
AGENT_ENDPOINT_PATHS: dict[str, str] = {"mcp": "/mcp/", "a2a": "/a2a"}

BRAND_JSON_PATH = "/.well-known/brand.json"
ADAGENTS_JSON_PATH = "/.well-known/adagents.json"
JWKS_PATH = "/.well-known/jwks.json"
#: The combined revocation list we PUBLISH as a SIGNER (#1291 A5 follow-up,
#: security.mdx :1543). The literal matches the URI
#: ``CachingRevocationChecker.from_issuer_origin`` derives
#: (``adcp/signing/revocation_fetcher.py``) — a parity test pins this against
#: that private derivation, since (unlike the three paths above) the SDK
#: exposes no path CONSTANT to import.
GOVERNANCE_REVOCATIONS_PATH = "/.well-known/governance-revocations.json"

# ``brand_agent_entry.id`` is ``^[a-z0-9_]+$``, maxLength 100. Tenant ids and
# subdomains routinely carry hyphens, which are ILLEGAL there.
_ID_ILLEGAL = re.compile(r"[^a-z0-9]+")
_AGENT_ENTRY_ID_MAX_LENGTH = 100


def _agent_host(tenant: Tenant) -> str | None:
    """The host this tenant is reachable at, or None when nothing is configured.

    ``virtual_host`` is the tenant's own host (it may carry a port). Otherwise
    the subdomain under the deployment's sales-agent domain. Both are stored
    state, never request state.
    """
    if tenant.virtual_host:
        return tenant.virtual_host
    sales_agent_domain = get_sales_agent_domain()
    if sales_agent_domain and tenant.subdomain:
        return f"{tenant.subdomain}.{sales_agent_domain}"
    return None


def canonical_agent_url(tenant: Tenant) -> str:
    """The tenant's canonical ORIGIN — scheme + host, no path, no trailing slash.

    This is the anchor, not an endpoint: brand.json is served here, the JWKS
    resolves here, and every ``agents[].url`` is this string plus an endpoint
    path. Because the Brand Agent variant of brand.json has no
    ``authorized_operators[]`` escape hatch, brand.json and the agent MUST share
    an origin — which this function makes true by construction.
    """
    host = _agent_host(tenant)
    if host:
        return f"{_get_protocol_for_domain(host)}://{host}"

    # Deployment-level base: single-tenant installs with no per-tenant host.
    if deployment_base := os.environ.get("ADCP_AGENT_URL"):
        return deployment_base.rstrip("/")

    # Development default — the port the sales agent serves on locally.
    return f"http://localhost:{os.environ.get('ADCP_SALES_PORT', '8080')}"


def agent_origin_host(tenant: Tenant) -> str:
    """The host (with port, if any) of the tenant's canonical agent URL.

    Derived by splitting the canonical URL rather than re-deriving the host, so
    "the host we are reachable at" and "the origin we publish" cannot disagree —
    including on the deployment-base and development-default branches, where the
    host is not stored tenant state at all.
    """
    return canonical_agent_url(tenant).split("://", 1)[1]


def agent_endpoint_urls(tenant: Tenant) -> dict[str, str]:
    """The URLs a counterparty invokes this tenant at, keyed by transport.

    One entry per endpoint we actually serve. brand.json publishes one
    ``agents[]`` entry per member of this mapping, so the byte-equal match at
    ``security.mdx`` step 5 succeeds for a caller of either transport.
    """
    origin = canonical_agent_url(tenant)
    return {transport: origin + path for transport, path in AGENT_ENDPOINT_PATHS.items()}


@dataclass(frozen=True, slots=True)
class AgentIdentity:
    """One tenant's published identity: the origin, and the endpoints under it.

    TWO FACTS ABOUT ONE IDENTITY, not one fact spelled two ways. Callers want different
    ones — the admin authorized-properties view wants the ORIGIN, the dynamic agent card
    wants the A2A ENDPOINT — so this surface exposes both rather than a single
    "identity URL" that would have to pick one and lose the distinction.

    What the callers were repeating is the RE-READ and the derivation surface, not the
    field. See :func:`agent_identity_for_tenant_id`.
    """

    origin: str
    endpoints: dict[str, str]


def agent_identity_for_tenant(tenant: Tenant) -> AgentIdentity:
    """*tenant*'s published identity — PURE, and reads no session.

    Takes an already-loaded row deliberately. A caller that holds its own session must be
    able to derive identity INSIDE its own transaction — ``SigningKeyRepository.
    canonical_origin`` resolves the origin in the same transaction that produced the key
    row it is about to sign with, and an identity helper that opened a UoW of its own
    would silently break that (it is graded by
    ``TestRevocationBeatsTheProviderCache``'s sibling, the flush-visibility test in
    ``tests/integration/test_signing_key_repository.py``). So the UoW-opening convenience
    is a separate function below, and this one never opens anything.
    """
    return AgentIdentity(origin=canonical_agent_url(tenant), endpoints=agent_endpoint_urls(tenant))


def agent_identity_for_tenant_id(tenant_id: str) -> AgentIdentity | None:
    """Re-read *tenant_id* in its own unit of work and derive its identity, or ``None``.

    The convenience half, for callers that hold NO session and were each writing the same
    four lines: open a ``TenantConfigUoW``, assert the repository, re-read the tenant, derive.
    ``None`` when the tenant does not resolve — every caller of this shape already had to
    handle a vanished tenant, and each did it differently (a ``None`` return, a
    ``ValueError``); the shape is preserved at the call sites rather than decided here.

    Deliberately NOT used by callers that already hold a session — see
    :func:`agent_identity_for_tenant`.
    """
    from src.core.database.repositories.uow import TenantConfigUoW

    with TenantConfigUoW(tenant_id) as uow:
        assert uow.tenant_config is not None
        tenant = uow.tenant_config.get_tenant()
        return agent_identity_for_tenant(tenant) if tenant is not None else None


def agent_entry_id(tenant: Tenant, transport: str) -> str:
    """The ``brand_agent_entry.id`` for this tenant's *transport* endpoint.

    Distinct per endpoint because the SDK's ``_pick_agent`` disambiguates
    same-type entries by ``id`` alone. Slugged to ``^[a-z0-9_]+$`` because the
    schema rejects the hyphens tenant ids routinely carry — and stable across
    deployments, because counterparties may pin it.
    """
    slug = _ID_ILLEGAL.sub("_", f"{tenant.tenant_id}_{transport}".lower()).strip("_")
    return slug[:_AGENT_ENTRY_ID_MAX_LENGTH]


def brand_json_url(tenant: Tenant) -> str:
    """Where this tenant's brand.json is served — D1 publishes this as ``identity.brand_json_url``."""
    return canonical_agent_url(tenant) + BRAND_JSON_PATH


def adagents_json_url(tenant: Tenant) -> str:
    """Where this tenant's adagents.json is served."""
    return canonical_agent_url(tenant) + ADAGENTS_JSON_PATH


def jwks_uri(tenant: Tenant) -> str:
    """Where this tenant's JWKS is served.

    Emitted EXPLICITLY on every ``agents[]`` entry rather than relying on the
    verifier's documented default, so a verifier never has to reconstruct it.
    """
    return canonical_agent_url(tenant) + JWKS_PATH


def jwks_origin(tenant: Tenant) -> str:
    """The origin the JWKS resolves at — D1's ``identity.key_origins.request_signing``.

    Imported rather than re-literalled: a second literal is a
    ``request_signature_key_origin_mismatch`` waiting to happen.
    """
    return canonical_agent_url(tenant)
