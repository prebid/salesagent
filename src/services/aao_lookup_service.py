"""AAO (AdCP Authorized Origins) lookup service.

Wraps the ``adcp`` library's adagents.json fetcher with the salesagent's
caching policy: 6-hour TTL per publisher_domain. PublisherPartner status
counts (``total_properties``, ``authorized_properties``) are refreshed by
the manual Refresh button + sync cron; the in-process cache covers
hot-loop verification calls between refreshes.

See ``docs/design/replace-authorized-properties-with-aao-lookup.md``.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Literal

from adcp import fetch_adagents, get_all_properties, get_properties_by_agent
from adcp.adagents import validate_adagents_structure

from src.services._adagents_shapes import find_agent_entry, is_bare_entry, top_level_properties

logger = logging.getLogger(__name__)


# Configurable so the open-source codebase isn't coupled to one operator's
# deployment. Override via env to point at your own AAO directory + extend
# the platform-host allowlist.
_AAO_PUBLISHER_DIRECTORY = os.environ.get(
    "AAO_PUBLISHER_DIRECTORY_URL",
    "https://agenticadvertising.org/publisher",
).rstrip("/")
_PLATFORM_AGENT_HOSTS_ENV = os.environ.get("EMBEDDED_PLATFORM_AGENT_HOSTS", "interchange.io")
_PLATFORM_AGENT_HOSTS = frozenset(h.strip().lower() for h in _PLATFORM_AGENT_HOSTS_ENV.split(",") if h.strip())


PublisherPartnerStatusKind = Literal[
    "authorized",
    "unbound",
    "pending",
    "no_properties",
    "unreachable",
]


@dataclass(frozen=True)
class PublisherPartnerStatus:
    """Live AAO snapshot for a single publisher partner.

    Returned by :func:`get_publisher_partner_status` and consumed by:

    - The Publisher Partnerships UI (renders ``"47 / 200 authorized"`` and the
      status chip).
    - The ``sync_publisher_partners`` cron / Verify-All endpoint (persists the
      counts on :class:`PublisherPartner` so the UI doesn't re-hit AAO on
      every page load).

    ``status`` — the five operationally-distinct cases the UI cares about
    (see salesagent#377 for the rationale on why this is finer than the
    spec's binary "valid/invalid"):

    - ``authorized`` — our agent's entry has typed binding
                       (``authorization_type`` + selector) resolving to ≥1
                       property. Spec-conformant and operational.
    - ``unbound``    — our agent is listed in ``authorized_agents`` with no
                       ``authorization_type`` (bare entry), but the file
                       has a top-level ``properties[]`` block. Not
                       spec-conformant — the SDK's strict resolver returns
                       []  — but the publisher's intent is clear and we
                       resolve permissively to all top-level properties.
                       Real-world repro: wonderstruck.org, Raptive (when
                       they ship properties). Operator should nudge the
                       publisher to add ``authorization_type`` but products
                       work today.
    - ``pending``    — file fetched cleanly, has properties, but our agent
                       isn't listed in ``authorized_agents`` at all.
                       Operator sends the AAO onboarding link
                       (:attr:`aao_onboarding_url`).
    - ``no_properties`` — file fetched cleanly but exposes zero properties
                       to anyone (no top-level array, no inline). Even if
                       we're listed there's nothing to sell. Publisher
                       must add a ``properties[]`` block before this row
                       can do anything.
    - ``unreachable`` — the adagents.json fetch failed (DNS, 404, parse
                       error, timeout). :attr:`error` carries the message.
    """

    publisher_domain: str
    total_properties: int
    authorized_properties: int
    status: PublisherPartnerStatusKind
    aao_onboarding_url: str
    error: str | None


# In-memory cache. Single-process is fine for the existing core deployment
# (single-worker). Multi-worker deployments will need a Redis-backed cache
# or accept duplicate fetches across workers (low-cost since adagents.json
# is a small static JSON file).
#
# Note: there's a benign race here — concurrent calls for the same domain
# can both miss, both fetch, both write. Final state is correct (same
# adagents.json data), just one wasted HTTP call. We don't add a lock
# because the contention window is small at 6h TTL and adagents.json is
# tiny + cacheable upstream. If profiling ever shows duplicate-fetch
# overhead, single-flight via a per-domain ``threading.Lock`` is the
# right primitive (asyncio.Lock won't coordinate across the per-request
# event loops Flask creates).
_ADAGENTS_TTL_SECONDS = 21600  # 6 hours

_ADAGENTS_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}


def invalidate_adagents_cache(publisher_domain: str | None = None) -> None:
    """Drop a single publisher_domain's cached adagents.json, or the whole
    cache if no domain given. Caller wires it on PublisherPartner upserts
    and manual-verify button presses in the Admin UI."""
    if publisher_domain is None:
        _ADAGENTS_CACHE.clear()
    else:
        _ADAGENTS_CACHE.pop(publisher_domain, None)


async def is_agent_authorized_by_publisher(
    publisher_domain: str, public_agent_url: str, *, force_refresh: bool = False
) -> tuple[bool, str | None]:
    """Verify the agent_url is listed in ``publisher_domain``'s adagents.json.

    Returns ``(True, None)`` on a clean verify, ``(False, error_message)``
    when the agent isn't listed or the fetch fails. Callers persist the
    result on :class:`PublisherPartner.is_verified`.

    The fetch is cached for :data:`_ADAGENTS_TTL_SECONDS` (6 hours) by
    publisher_domain — the existing `sync_all_tenants.py` cron drives the
    refresh, and the Admin UI's "Verify now" button calls
    ``invalidate_adagents_cache(publisher_domain)`` first.
    """
    now = time.monotonic()
    adagents: dict[str, Any] | None = None
    if not force_refresh:
        cached = _ADAGENTS_CACHE.get(publisher_domain)
        if cached is not None and now - cached[0] < _ADAGENTS_TTL_SECONDS:
            adagents = cached[1]

    if adagents is None:
        try:
            adagents = await fetch_adagents(publisher_domain)
        except Exception as exc:
            logger.info("AAO: adagents.json fetch failed for %s: %s", publisher_domain, exc)
            return False, f"adagents.json fetch failed: {exc}"
        _ADAGENTS_CACHE[publisher_domain] = (now, adagents)

    # The SDK's get_properties_by_agent() returns the property list for
    # the agent_url; an empty list means the agent isn't authorized.
    # We don't actually need the property list here — just the boolean.
    properties = get_properties_by_agent(adagents, public_agent_url)
    if not properties:
        return False, f"agent_url {public_agent_url!r} not listed in adagents.json"
    return True, None


class PublicAgentUrlMismatch(ValueError):
    """Raised when a saved ``public_agent_url`` doesn't match the tenant's
    serving hostname. Without this check, publishers' adagents.json would
    point at a host this salesagent never answers on, and every authorized
    buy would fail signature verification at admission time."""


def _normalize_hostname_for_compare(host: str) -> str:
    """Lowercase, strip trailing FQDN dot, strip ``:port`` suffix, and
    fold IDN to ASCII (punycode).

    Three normalizations on both sides of the comparison:

    - ``virtual_host`` may carry ``:port`` in dev (``localhost:8001``);
      ``urlparse`` already strips ports from URLs but the comparison
      side hasn't been parsed.
    - Trailing FQDN dot (``example.com.``) — valid DNS but
      ``urlparse`` keeps it in ``hostname`` and ``virtual_host`` storage
      generally doesn't.
    - IDN: ``bücher.example`` and ``xn--bcher-kva.example`` are the same
      domain. Without IDN folding, a unicode-stored ``virtual_host`` and
      a punycode-encoded URL hostname false-mismatch.
    """
    base = host.split(":", 1)[0].rstrip(".").lower()
    if not base or base.isascii():
        return base
    try:
        return base.encode("idna").decode("ascii")
    except UnicodeError:
        # Malformed IDN label — fall back to the lowercased unicode
        # form so the validator still produces a deterministic answer
        # (it'll just refuse to match anything).
        return base


def validate_public_agent_url_hostname(
    public_agent_url: str,
    *,
    is_embedded: bool,
    virtual_host: str | None,
    subdomain: str | None,
    sales_agent_domain: str | None,
) -> None:
    """Enforce ``urlparse(public_agent_url).hostname ∈ acceptable_hosts``.

    Acceptable hosts:

    - Embedded tenants: any host in :data:`_PLATFORM_AGENT_HOSTS`
      (configured via ``EMBEDDED_PLATFORM_AGENT_HOSTS`` env, default
      ``interchange.io``).
    - Self-hosted tenants: ``virtual_host`` (custom DNS) or
      ``{subdomain}.{sales_agent_domain}`` (platform-prefixed default).

    Raises :class:`PublicAgentUrlMismatch` when the URL points somewhere this
    salesagent doesn't serve from. Callers translate to a 422 / form error.
    """
    from urllib.parse import urlparse

    raw = (urlparse(public_agent_url).hostname or "").lower()
    if not raw:
        raise PublicAgentUrlMismatch(f"public_agent_url {public_agent_url!r} has no hostname")
    hostname = _normalize_hostname_for_compare(raw)

    acceptable: set[str] = set()
    if is_embedded:
        acceptable.update(_PLATFORM_AGENT_HOSTS)
    if virtual_host:
        acceptable.add(_normalize_hostname_for_compare(virtual_host))
    if subdomain and sales_agent_domain:
        acceptable.add(f"{subdomain.lower()}.{_normalize_hostname_for_compare(sales_agent_domain)}")

    if hostname not in acceptable:
        raise PublicAgentUrlMismatch(
            f"public_agent_url hostname {hostname!r} doesn't match any of this "
            f"tenant's serving hosts ({sorted(acceptable) or 'none configured'}). "
            "Publishers listing this URL in adagents.json wouldn't be able to "
            "reach this agent — fix virtual_host first, or use the platform "
            f"default (one of {sorted(_PLATFORM_AGENT_HOSTS)}) for embedded tenants."
        )


def _aao_onboarding_url(publisher_domain: str) -> str:
    """Deep link to the AAO publisher page — sent to publishers who list
    properties but haven't yet authorized this tenant's agent."""
    return f"{_AAO_PUBLISHER_DIRECTORY}/{publisher_domain}"


def _count_total_properties(adagents: dict[str, Any]) -> int:
    """Total properties this publisher exposes across all reference forms.

    Defers to the SDK's ``get_all_properties()``, which resolves
    ``inline_properties``, ``property_ids``, ``property_tags``, and
    ``property_signals`` against the adagents document. Counting only
    ``inline_properties`` (the previous implementation) under-reported
    publishers using the recommended brand.json + by-id pattern, producing
    nonsense ratios like "0 listed / 47 authorized"."""
    return len(get_all_properties(adagents) or [])


def _count_top_level_properties(adagents: dict[str, Any]) -> int:
    """Length of the top-level ``properties[]`` array. Used only in the
    unbound branch, where the SDK's per-agent resolver returns [] but
    operationally we treat the agent as authorized for every top-level
    property the publisher exposes."""
    return len(top_level_properties(adagents))


async def get_publisher_partner_status(
    publisher_domain: str,
    public_agent_url: str,
    *,
    force_refresh: bool = False,
) -> PublisherPartnerStatus:
    """Fetch the publisher's adagents.json once and classify it into one of
    the five :class:`PublisherPartnerStatusKind` states.

    Decision tree (see salesagent#377 for rationale):

    1. Fetch fails → ``unreachable``.
    2. SDK strict resolver returns ≥1 property for our agent → ``authorized``.
       (Covers typed bindings: ``inline_properties``, ``property_ids``,
       ``property_tags``, ``publisher_properties``.)
    3. Our agent's entry exists but is bare (no ``authorization_type``,
       no selector) AND the file has a top-level ``properties[]`` block →
       ``unbound``. Permissive resolution: treat as authorized for every
       top-level property. ``error`` carries the conformance hint so the
       operator can nudge the publisher.
    4. Our agent is listed, but neither (2) nor (3) applies (typed
       binding resolved to nothing, or bare entry with no top-level
       properties) → ``no_properties``. Publisher must add a
       ``properties[]`` block.
    5. Our agent isn't listed at all. If the publisher exposes any
       properties to anyone → ``pending`` (publisher just hasn't
       authorized us); otherwise → ``no_properties``.

    On fetch failure returns ``status="unreachable"`` with the error
    message rather than raising — callers persist it on
    :class:`PublisherPartner.last_fetch_error`.
    """
    now = time.monotonic()
    adagents: dict[str, Any] | None = None
    if not force_refresh:
        cached = _ADAGENTS_CACHE.get(publisher_domain)
        if cached is not None and now - cached[0] < _ADAGENTS_TTL_SECONDS:
            adagents = cached[1]

    if adagents is None:
        try:
            adagents = await fetch_adagents(publisher_domain)
        except Exception as exc:
            logger.info("AAO: adagents.json fetch failed for %s: %s", publisher_domain, exc)
            return PublisherPartnerStatus(
                publisher_domain=publisher_domain,
                total_properties=0,
                authorized_properties=0,
                status="unreachable",
                aao_onboarding_url=_aao_onboarding_url(publisher_domain),
                error=f"adagents.json fetch failed: {exc}",
            )
        _ADAGENTS_CACHE[publisher_domain] = (now, adagents)

    total_listed = _count_total_properties(adagents)
    authorized_props = get_properties_by_agent(adagents, public_agent_url) or []
    if authorized_props:
        return PublisherPartnerStatus(
            publisher_domain=publisher_domain,
            total_properties=total_listed,
            authorized_properties=len(authorized_props),
            status="authorized",
            aao_onboarding_url=_aao_onboarding_url(publisher_domain),
            error=None,
        )

    our_entry = find_agent_entry(adagents, public_agent_url)
    top_level_count = _count_top_level_properties(adagents)

    if our_entry is not None and is_bare_entry(our_entry) and top_level_count > 0:
        # validate_adagents_structure is informational here — we use it
        # only to surface a richer hint on the unbound chip. The
        # authorized/pending/no_properties branches don't need it, so the
        # call lives inside this branch (was hoisted on every fetch by an
        # earlier revision — measurable cost on UI polls).
        report = validate_adagents_structure(adagents)
        if report.schema_valid:
            hint = (
                "Publisher's entry has no authorization_type — products bind to all "
                "top-level properties; ask publisher to add a typed binding for spec "
                "conformance."
            )
        else:
            hint = _format_validation_error(report.errors)
        return PublisherPartnerStatus(
            publisher_domain=publisher_domain,
            total_properties=top_level_count,
            authorized_properties=top_level_count,
            status="unbound",
            aao_onboarding_url=_aao_onboarding_url(publisher_domain),
            error=hint,
        )

    if total_listed > 0 or top_level_count > 0:
        # Publisher exposes inventory; our agent isn't authorized for any
        # of it. Either we're not in authorized_agents[] at all, or we
        # are with a typed binding whose selector resolved to nothing.
        if our_entry is None:
            pending_error: str | None = None
        else:
            pending_error = (
                "Publisher's entry for our agent has a typed binding that resolves to "
                "no properties — verify the publisher's property_ids / property_tags "
                "selector matches their published inventory."
            )
        return PublisherPartnerStatus(
            publisher_domain=publisher_domain,
            total_properties=max(total_listed, top_level_count),
            authorized_properties=0,
            status="pending",
            aao_onboarding_url=_aao_onboarding_url(publisher_domain),
            error=pending_error,
        )

    return PublisherPartnerStatus(
        publisher_domain=publisher_domain,
        total_properties=0,
        authorized_properties=0,
        status="no_properties",
        aao_onboarding_url=_aao_onboarding_url(publisher_domain),
        error="Publisher's adagents.json has no properties — add a top-level properties[] block before products can bind.",
    )


def _format_validation_error(errors: list) -> str:
    """One-line summary of an AdagentsValidationReport's errors for the UI.

    Shows the first error verbatim plus "(and N more)" when multiple — fits
    in a table cell, gives the publisher something concrete to act on, and
    keeps the wording stable (SDK ``message`` may evolve but ``kind`` is
    stable, so we lean on the SDK's human-readable ``message`` for now and
    can branch on ``kind`` later if we need localized copy).
    """
    if not errors:
        return "adagents.json failed schema validation"
    first = errors[0].message
    if len(errors) == 1:
        return f"Non-conformant adagents.json: {first}"
    return f"Non-conformant adagents.json: {first} (and {len(errors) - 1} more)"
