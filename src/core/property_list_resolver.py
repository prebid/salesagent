"""Property list resolver with caching.

Fetches buyer property lists from external agent services and caches
the results using the cache_valid_until TTL from the response.
"""

import logging
from datetime import UTC, datetime, timedelta

from adcp.types import GetPropertyListResponse, PropertyListReference

from src.core.security.outbound_http import CounterpartyUrl, asend

logger = logging.getLogger(__name__)

# Default timeout for HTTP requests (seconds)
_DEFAULT_TIMEOUT = 10.0

# Default cache TTL when cache_valid_until is not provided (seconds)
_DEFAULT_CACHE_TTL_SECONDS = 300  # 5 minutes

# Where this URL sits in the buyer's request, in AdCP JSONPath-lite. ``property_list``
# is a TOP-LEVEL property of get-products-request.json (3.1.1), $ref-ing
# core/property-list-ref.json whose required keys are agent_url and list_id — so the
# path is not a packages[i] one. An egress refusal carries this to the buyer as
# error.field; the seam cannot derive it, because it sees a URL and not a request.
_REFUSED_FIELD_PATH = "property_list.agent_url"

# Cache: (agent_url, list_id) -> (identifier_values, expires_at)
_cache: dict[tuple[str, str], tuple[list[str], datetime]] = {}


async def resolve_property_list(ref: PropertyListReference) -> list[str]:
    """Resolve a property list reference to a list of property identifier strings.

    Fetches the property list from the agent service identified by ref.agent_url,
    caches the result using cache_valid_until from the response, and returns
    the identifier value strings.

    Args:
        ref: PropertyListReference containing agent_url, list_id, and optional auth_token.

    Returns:
        List of property identifier value strings.

    Raises:
        OutboundRequestBlocked: The buyer-supplied ``agent_url`` was refused by
            egress policy (non-HTTPS scheme, or an address the SDK validator
            rejects). VALIDATION_ERROR / correctable: the buyer supplied the URL,
            so the buyer is the only party who can fix it. The refusal carries
            ``error.field = "property_list.agent_url"`` on both envelope layers,
            which is the only channel that can name the offending input — the
            message says nothing about the cause on purpose (AdCP 3.1.1 L1
            security, point 6).
        OutboundDeliveryFailed: The agent service was reachable but did not
            answer — SERVICE_UNAVAILABLE / transient.
    """
    agent_url_str = str(ref.agent_url)

    cache_key = (agent_url_str, ref.list_id)

    # Check cache
    if cache_key in _cache:
        identifiers, expires_at = _cache[cache_key]
        if datetime.now(UTC) < expires_at:
            logger.debug("Cache hit for property list %s/%s", ref.agent_url, ref.list_id)
            return identifiers
        else:
            del _cache[cache_key]

    # DELIBERATELY UNSIGNED, permanently, with the reason stated here rather than
    # left as an omission (salesagent-z6nr.35 site 2, #1291).
    #
    # This is a bespoke REST GET against {agent_url}/lists/{list_id}. It is NOT a
    # conformant AdCP get_property_list tool call (the spec defines that over
    # MCP/A2A — v3.1.1:docs/governance/property/tasks/property_lists.mdx), so
    # there is no AdCP operation name to attribute a signature to. AdCP 3.1.1
    # building/by-layer/L1/security.mdx:1043 is explicit that operation names must
    # be protocol-defined and that "Verifiers MUST NOT accept operation names that
    # are not defined by the AdCP protocol spec" — so signing this would put a
    # signature on the wire that a conformant receiver is required to reject.
    #
    # Specifically DO NOT "fix" this by installing install_signing_event_hook with
    # capability_provider=lambda: None: that provider makes the SDK skip every
    # operation (adcp/signing/client.py:116-117), so it is dead code wearing the
    # appearance of signing — the exact trap this ticket's acceptance forbids.
    #
    # Signing becomes possible only if this fetch migrates onto the real AdCP
    # client and a conformant get_property_list call, which is its own larger
    # ticket.
    url = agent_url_str.rstrip("/") + "/lists/" + ref.list_id
    headers: dict[str, str] = {}
    if ref.auth_token:
        headers["Authorization"] = f"Bearer {ref.auth_token}"

    # Fetch. Scheme policy, address validation, IP pinning, redirect refusal,
    # the response-size cap and retry classification are all the seam's — a
    # refusal or a delivery failure arrives here already typed as an AdCPError
    # with the right wire code, so there is nothing left to catch and rewrap.
    result = await asend(
        url,
        method="GET",
        headers=headers,
        timeout=_DEFAULT_TIMEOUT,
        provenance=CounterpartyUrl(field=_REFUSED_FIELD_PATH),
    )

    # Parse response
    parsed = GetPropertyListResponse.model_validate(result.json())

    # Extract identifier values
    identifier_values = [ident.value for ident in parsed.identifiers] if parsed.identifiers else []

    # Cache with TTL
    if parsed.cache_valid_until is not None:
        expires_at = parsed.cache_valid_until
    else:
        expires_at = datetime.now(UTC) + timedelta(seconds=_DEFAULT_CACHE_TTL_SECONDS)

    _cache[cache_key] = (identifier_values, expires_at)

    logger.debug(
        "Resolved property list %s/%s: %d identifiers (cached until %s)",
        ref.agent_url,
        ref.list_id,
        len(identifier_values),
        expires_at.isoformat(),
    )

    return identifier_values


def clear_cache() -> None:
    """Clear the property list cache."""
    _cache.clear()
