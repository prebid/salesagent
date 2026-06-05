"""Property list resolver with caching.

Fetches buyer property lists from external agent services and caches
the resolved identifiers using the cache_valid_until TTL from the response.

Two access modes:
- ``resolve_property_list(ref) -> list[str]``: async, returns identifier values
  (used by the discovery path that needs string-set intersections).
- ``resolve_property_list_typed_sync(ref) -> list[Identifier]``: sync, returns
  the full typed Identifier objects (used by adapter-side compilation that
  needs to dispatch on identifier type, e.g. Kevel's domain → siteId mapping).

Both share a single module-level cache so a typed lookup populates the cache
for subsequent value-only lookups (and vice versa).
"""

import logging
from datetime import UTC, datetime, timedelta

import httpx
from adcp.types import GetPropertyListResponse, Identifier, PropertyListReference

from src.core.exceptions import AdCPAdapterError
from src.core.security.url_validator import check_url_ssrf

logger = logging.getLogger(__name__)

# Default timeout for HTTP requests (seconds)
_DEFAULT_TIMEOUT = 10.0

# Default cache TTL when cache_valid_until is not provided (seconds)
_DEFAULT_CACHE_TTL_SECONDS = 300  # 5 minutes

# Cache: (agent_url, list_id) -> (identifiers, expires_at)
# Stores typed Identifier objects so both .value (for products discovery) and
# .type (for adapter compilation) are available without re-fetching.
_cache: dict[tuple[str, str], tuple[list[Identifier], datetime]] = {}


def _validate_agent_url(agent_url: str) -> None:
    """Validate agent_url to prevent SSRF attacks.

    Buyer-supplied agent_url must be HTTPS and must not target private/internal
    networks or cloud metadata services.

    Raises:
        AdCPAdapterError: If the URL is not allowed.
    """
    is_safe, error = check_url_ssrf(agent_url, require_https=True)
    if not is_safe:
        raise AdCPAdapterError(f"Property list agent_url rejected: {error}")


def _build_request(ref: PropertyListReference) -> tuple[str, str, dict[str, str]]:
    """Build (cache_key_agent_url, request_url, headers) for the property list fetch."""
    agent_url_str = str(ref.agent_url)
    _validate_agent_url(agent_url_str)
    request_url = agent_url_str.rstrip("/") + "/lists/" + ref.list_id
    headers: dict[str, str] = {}
    if ref.auth_token:
        headers["Authorization"] = f"Bearer {ref.auth_token}"
    return agent_url_str, request_url, headers


def _check_cache(agent_url: str, list_id: str) -> list[Identifier] | None:
    """Return cached identifiers if present and not expired; drop the entry on expiry."""
    cache_key = (agent_url, list_id)
    if cache_key not in _cache:
        return None
    identifiers, expires_at = _cache[cache_key]
    if datetime.now(UTC) >= expires_at:
        del _cache[cache_key]
        return None
    logger.debug("Cache hit for property list %s/%s", agent_url, list_id)
    return identifiers


def _store_in_cache(agent_url: str, list_id: str, response_data: dict) -> list[Identifier]:
    """Parse the fetched payload, cache it with the right TTL, return the typed identifiers."""
    parsed = GetPropertyListResponse.model_validate(response_data)
    identifiers = parsed.identifiers or []
    expires_at = parsed.cache_valid_until or (datetime.now(UTC) + timedelta(seconds=_DEFAULT_CACHE_TTL_SECONDS))
    _cache[(agent_url, list_id)] = (identifiers, expires_at)
    logger.debug(
        "Resolved property list %s/%s: %d identifiers (cached until %s)",
        agent_url,
        list_id,
        len(identifiers),
        expires_at.isoformat(),
    )
    return identifiers


def _http_error_message(url: str, exc: Exception) -> str:
    """Uniform error message for HTTP failures across sync and async paths."""
    if isinstance(exc, httpx.HTTPStatusError):
        return f"Failed to fetch property list from {url}: HTTP {exc.response.status_code}"
    if isinstance(exc, httpx.TimeoutException):
        return f"Request to property list service timed out: {url}"
    return f"Failed to connect to property list service: {url} — {exc}"


async def resolve_property_list_typed(ref: PropertyListReference) -> list[Identifier]:
    """Resolve a property list reference to a list of typed ``Identifier`` objects.

    Async path. Use the sync variant from synchronous code (e.g. ad-server
    adapters whose ``create_media_buy`` API is sync).
    """
    agent_url, request_url, headers = _build_request(ref)
    cached = _check_cache(agent_url, ref.list_id)
    if cached is not None:
        return cached

    try:
        async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT) as client:
            response = await client.get(request_url, headers=headers)
            response.raise_for_status()
    except (httpx.HTTPStatusError, httpx.TimeoutException, httpx.RequestError) as exc:
        raise AdCPAdapterError(_http_error_message(request_url, exc)) from exc

    return _store_in_cache(agent_url, ref.list_id, response.json())


def resolve_property_list_typed_sync(ref: PropertyListReference) -> list[Identifier]:
    """Resolve a property list reference synchronously, returning typed identifiers.

    Used by adapter-side compilation (e.g. ``KevelSiteResolver``) whose
    ``_build_targeting`` is called from a sync ad-server adapter API. Shares
    the module-level cache with the async variant, so back-to-back async and
    sync lookups for the same list reference only fetch once.
    """
    agent_url, request_url, headers = _build_request(ref)
    cached = _check_cache(agent_url, ref.list_id)
    if cached is not None:
        return cached

    try:
        with httpx.Client(timeout=_DEFAULT_TIMEOUT) as client:
            response = client.get(request_url, headers=headers)
            response.raise_for_status()
    except (httpx.HTTPStatusError, httpx.TimeoutException, httpx.RequestError) as exc:
        raise AdCPAdapterError(_http_error_message(request_url, exc)) from exc

    return _store_in_cache(agent_url, ref.list_id, response.json())


async def resolve_property_list(ref: PropertyListReference) -> list[str]:
    """Resolve a property list reference to identifier value strings (async).

    Thin wrapper around ``resolve_property_list_typed`` for callers (e.g. the
    products-discovery filter) that only need the values, not the types.

    Returns:
        List of property identifier value strings.

    Raises:
        AdCPAdapterError: On HTTP errors, timeouts, connection failures, or SSRF violations.
    """
    identifiers = await resolve_property_list_typed(ref)
    return [ident.value for ident in identifiers]


def clear_cache() -> None:
    """Clear the property list cache."""
    _cache.clear()
