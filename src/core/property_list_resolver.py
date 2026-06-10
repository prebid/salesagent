"""Property list resolver with caching.

Fetches buyer property lists from external agent services and caches
the resolved identifiers using the cache_valid_until TTL from the response.

Two access modes, both returning the full typed ``Identifier`` objects —
``.type`` participates in downstream matching, so the type is never stripped at
this boundary:
- ``resolve_property_list_typed(ref)``: async (products discovery, advisories).
- ``resolve_property_list_typed_sync(ref)``: sync (adapter-side compilation
  whose ad-server APIs are sync, e.g. Kevel's domain → siteId mapping).

Both share a single module-level cache so a typed lookup populates the cache
for the other mode's subsequent lookups.
"""

import json
import logging
import threading
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

# Guards every read/write/clear of ``_cache``. The async and sync resolver
# paths plus the create-media-buy advisory share this module-level cache from
# different threads/event loops; without the lock the expiry-drop could ``del``
# a key another caller already removed (``KeyError``) or read a torn entry. The
# HTTP fetch stays OUTSIDE the lock — a concurrent cold-cache double-fetch is
# acceptable (both produce the same identifiers), only the dict op is atomic.
_cache_lock = threading.Lock()


def loggable_list_id(list_id: str) -> str:
    """Strip control characters from a buyer-supplied list_id before logging.

    ``PropertyListReference.list_id`` has no charset constraint, so embedded
    newlines would otherwise let a buyer forge operator log lines (CWE-117).
    """
    return "".join(ch for ch in list_id if ch.isprintable())[:128]


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
    with _cache_lock:
        cached = _cache.get(cache_key)
        if cached is None:
            return None
        identifiers, expires_at = cached
        if datetime.now(UTC) >= expires_at:
            # ``pop`` (not ``del``) so a concurrent caller that already
            # refreshed or removed this key cannot raise ``KeyError``.
            _cache.pop(cache_key, None)
            return None
    logger.debug("Cache hit for property list %s/%s", agent_url, list_id)
    return identifiers


def _store_in_cache(agent_url: str, list_id: str, response_data: dict) -> list[Identifier]:
    """Parse the fetched payload, cache it with the right TTL, return the typed identifiers."""
    parsed = GetPropertyListResponse.model_validate(response_data)
    identifiers = parsed.identifiers or []
    expires_at = parsed.cache_valid_until or (datetime.now(UTC) + timedelta(seconds=_DEFAULT_CACHE_TTL_SECONDS))
    with _cache_lock:
        _cache[(agent_url, list_id)] = (identifiers, expires_at)
    logger.debug(
        "Resolved property list %s/%s: %d identifiers (cached until %s)",
        agent_url,
        list_id,
        len(identifiers),
        expires_at.isoformat(),
    )
    return identifiers


def _payload_or_raise(response: httpx.Response, request_url: str) -> dict:
    """Decode the JSON payload, mapping a non-JSON 2xx to a typed adapter error.

    Without this, ``response.json()`` raises ``json.JSONDecodeError`` past every
    typed arm and surfaces as an internal error instead of naming the buyer's
    list service as the failing party.
    """
    try:
        return response.json()
    except json.JSONDecodeError as exc:
        raise AdCPAdapterError(f"Property list service returned a non-JSON response: {request_url}") from exc


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

    return _store_in_cache(agent_url, ref.list_id, _payload_or_raise(response, request_url))


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

    return _store_in_cache(agent_url, ref.list_id, _payload_or_raise(response, request_url))


def clear_cache() -> None:
    """Clear the property list cache."""
    with _cache_lock:
        _cache.clear()
