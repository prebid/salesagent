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

import hashlib
import hmac
import json
import logging
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any, NoReturn

import httpx
from adcp.types import GetPropertyListResponse, Identifier, PropertyListReference

from src.core.exceptions import AdCPAdapterError, AdCPValidationError
from src.core.security.url_validator import check_url_ssrf
from src.core.ttl_cache import ThreadSafeTTLCache

logger = logging.getLogger(__name__)

# Default timeout for HTTP requests (seconds)
_DEFAULT_TIMEOUT = 10.0

# Default cache TTL when cache_valid_until is not provided (seconds)
_DEFAULT_CACHE_TTL_SECONDS = 300  # 5 minutes

# Upper clamp on a buyer/list-service-supplied cache_valid_until: a buyer cannot
# pin an entry in the process-global cache indefinitely (cardinality is separately
# bounded by ThreadSafeTTLCache's maxsize; this bounds per-entry lifetime).
_MAX_CACHE_TTL_SECONDS = 3600  # 1 hour

# Cache: (agent_url, list_id, auth_partition) -> typed Identifier objects, so both
# .value (for products discovery) and .type (for adapter compilation) are available
# without re-fetching. The auth_partition (an HMAC of the buyer's auth_token) keeps
# one principal from reading another's access-gated list out of this process-global
# cache. The async and sync resolver paths plus the create-media-buy advisory share
# this module-level cache from different threads/event loops; the locking/expiry
# contract lives in ThreadSafeTTLCache.
_cache: ThreadSafeTTLCache[tuple[str, str, str], list[Identifier]] = ThreadSafeTTLCache()


def loggable_list_id(list_id: str) -> str:
    """Strip control characters from a buyer-supplied list_id before logging.

    ``PropertyListReference.list_id`` has no charset constraint, so embedded
    newlines would otherwise let a buyer forge operator log lines (CWE-117).
    """
    return "".join(ch for ch in list_id if ch.isprintable())[:128]


def package_property_list_ref(package: Any) -> PropertyListReference | None:
    """The package's ``targeting_overlay.property_list`` reference, or ``None`` at any missing link.

    Single home for the selector traversal shared by the create-side resolver
    walk, the advisory builder, and the Kevel compile gate.
    """
    overlay = getattr(package, "targeting_overlay", None)
    return getattr(overlay, "property_list", None) if overlay else None


# Constant purpose label binding the auth HMAC to this cache (domain separation).
_CACHE_PARTITION_LABEL = b"property-list-cache-partition"


def _auth_partition(auth_token: str | None) -> str:
    """Non-reversible cache partition for a buyer credential.

    The bearer token is used as the HMAC KEY over a constant purpose label (a
    keyed PRF, HKDF-extract shape) so the plaintext token never persists as a
    process-global cache key, while two principals with different tokens for the
    same ``(agent_url, list_id)`` partition into separate cache entries. Without
    this the second principal reads the first's access-gated identifiers from the
    shared cache before the list service can reject its token. Mirrors
    ``KevelSiteResolver._cache_partition``. A missing token maps to a stable
    sentinel so unauthenticated lists still share a single partition.
    """
    return hmac.new((auth_token or "").encode(), _CACHE_PARTITION_LABEL, hashlib.sha256).hexdigest()[:16]


def property_list_cache_key(ref: PropertyListReference) -> tuple[str, str, str]:
    """The canonical ``(agent_url, list_id, auth_partition)`` dedup/cache key for a property-list ref.

    Single source of the key so the create-side prefetch, the zero-overlap
    advisory builder, the Kevel compile cache, and the resolver fetch cache index
    identically. ``agent_url`` is an ``AnyUrl`` so it is stringified; ``list_id``
    is already ``str``; ``auth_partition`` is a non-reversible HMAC of the buyer's
    ``auth_token`` so one principal cannot read another's access-gated list out of
    the shared cache.
    """
    return (str(ref.agent_url), ref.list_id, _auth_partition(ref.auth_token))


def iter_package_property_list_refs(
    packages: Iterable[Any],
) -> Iterator[tuple[int, Any, PropertyListReference, tuple[str, str, str]]]:
    """Yield ``(index, package, ref, key)`` for each package carrying a property_list ref.

    Single home for the "walk packages → pluck ref → key it" skeleton shared by
    the create-side prefetch, the advisory builder, and the Kevel compile gate.
    Packages without a property_list ref are skipped; the index is the position in
    the original sequence (used for ``packages[i]`` error field paths). Consumers
    keep their own bodies (resolve / look up / raise) and dedup on ``key`` if they
    need to — the generator never dedups.
    """
    for index, package in enumerate(packages):
        ref = package_property_list_ref(package)
        if ref is None:
            continue
        yield index, package, ref, property_list_cache_key(ref)


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


def _build_request(ref: PropertyListReference) -> tuple[str, dict[str, str]]:
    """Build (request_url, headers) for the property list fetch (validates agent_url for SSRF)."""
    agent_url_str = str(ref.agent_url)
    _validate_agent_url(agent_url_str)
    request_url = agent_url_str.rstrip("/") + "/lists/" + ref.list_id
    headers: dict[str, str] = {}
    if ref.auth_token:
        headers["Authorization"] = f"Bearer {ref.auth_token}"
    return request_url, headers


def _check_cache(ref: PropertyListReference) -> list[Identifier] | None:
    """Return cached identifiers if present and not expired.

    Keyed via ``property_list_cache_key`` so the lookup is partitioned by the
    buyer's auth_token — a different principal's token misses this entry.
    """
    identifiers = _cache.get(property_list_cache_key(ref))
    if identifiers is None:
        return None
    logger.debug("Cache hit for property list %s/%s", ref.agent_url, loggable_list_id(ref.list_id))
    return identifiers


def _store_in_cache(ref: PropertyListReference, response_data: dict) -> list[Identifier]:
    """Parse the fetched payload, cache it with the right TTL, return the typed identifiers."""
    parsed = GetPropertyListResponse.model_validate(response_data)
    identifiers = parsed.identifiers or []
    now = datetime.now(UTC)
    # Clamp a buyer/list-service-supplied cache_valid_until to a max lifetime so it
    # cannot pin an entry in the process-global cache; fall back to the default TTL
    # when the service omits it.
    expires_at = min(
        parsed.cache_valid_until or (now + timedelta(seconds=_DEFAULT_CACHE_TTL_SECONDS)),
        now + timedelta(seconds=_MAX_CACHE_TTL_SECONDS),
    )
    _cache.store(property_list_cache_key(ref), identifiers, expires_at)
    logger.debug(
        "Resolved property list %s/%s: %d identifiers (cached until %s)",
        ref.agent_url,
        loggable_list_id(ref.list_id),
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


def _raise_fetch_error(ref: PropertyListReference, url: str, exc: Exception) -> NoReturn:
    """Map an HTTP fetch failure to the typed error class, shared by both paths.

    The split is the recovery taxonomy: a 4xx is the list service rejecting
    the BUYER's reference (unknown/forbidden/expired list_id) — correctable,
    so the buyer fixes the reference instead of retrying forever. 5xx,
    timeouts, and connection failures are the service misbehaving — transient.
    """
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if 400 <= status < 500:
            raise AdCPValidationError(
                f"The property list service rejected list_id '{ref.list_id}' "
                f"(HTTP {status} from {ref.agent_url}). The list may not exist or "
                "may not be accessible to this agent.",
                field="property_list",
                suggestion="Check the property list reference (agent_url and list_id), or pick a different list.",
            ) from exc
        raise AdCPAdapterError(f"Failed to fetch property list from {url}: HTTP {status}") from exc
    if isinstance(exc, httpx.TimeoutException):
        raise AdCPAdapterError(f"Request to property list service timed out: {url}") from exc
    raise AdCPAdapterError(f"Failed to connect to property list service: {url} — {exc}") from exc


async def resolve_property_list_typed(ref: PropertyListReference) -> list[Identifier]:
    """Resolve a property list reference to a list of typed ``Identifier`` objects.

    Async path. Use the sync variant from synchronous code (e.g. ad-server
    adapters whose ``create_media_buy`` API is sync).
    """
    request_url, headers = _build_request(ref)
    cached = _check_cache(ref)
    if cached is not None:
        return cached

    try:
        async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT) as client:
            response = await client.get(request_url, headers=headers)
            response.raise_for_status()
    except (httpx.HTTPStatusError, httpx.TimeoutException, httpx.RequestError) as exc:
        _raise_fetch_error(ref, request_url, exc)

    return _store_in_cache(ref, _payload_or_raise(response, request_url))


def resolve_property_list_typed_sync(ref: PropertyListReference) -> list[Identifier]:
    """Resolve a property list reference synchronously, returning typed identifiers.

    Used by adapter-side compilation (e.g. ``KevelSiteResolver``) whose
    ``_build_targeting`` is called from a sync ad-server adapter API. Shares
    the module-level cache with the async variant, so back-to-back async and
    sync lookups for the same list reference only fetch once.
    """
    request_url, headers = _build_request(ref)
    cached = _check_cache(ref)
    if cached is not None:
        return cached

    try:
        with httpx.Client(timeout=_DEFAULT_TIMEOUT) as client:
            response = client.get(request_url, headers=headers)
            response.raise_for_status()
    except (httpx.HTTPStatusError, httpx.TimeoutException, httpx.RequestError) as exc:
        _raise_fetch_error(ref, request_url, exc)

    return _store_in_cache(ref, _payload_or_raise(response, request_url))


def clear_cache() -> None:
    """Clear the property list cache."""
    _cache.clear()
