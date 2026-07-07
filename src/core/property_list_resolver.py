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
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any, NoReturn

import httpx
from adcp.types import GetPropertyListResponse, Identifier, PropertyListReference
from pydantic import ValidationError

from src.core.exceptions import AdCPAdapterError, adcp_error_for_http_status
from src.core.log_safety import loggable
from src.core.security.url_validator import (
    resolve_validated_ip,
    ssrf_pinned_async_transport,
    ssrf_pinned_transport,
)
from src.core.ttl_cache import ThreadSafeTTLCache, cache_partition_token

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
    Caps length on top of the shared :func:`loggable` scrub.
    """
    return loggable(list_id)[:128]


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
    """Non-reversible cache partition for a buyer credential (see ``cache_partition_token``).

    Two principals with different tokens for the same ``(agent_url, list_id)``
    partition into separate cache entries — without this the second principal
    reads the first's access-gated identifiers from the shared cache before the
    list service can reject its token.
    """
    return cache_partition_token(auth_token, _CACHE_PARTITION_LABEL)


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


def _validated_agent_ip(agent_url: str) -> str:
    """Validate agent_url for SSRF and return the validated IP to pin the fetch to.

    Buyer-supplied agent_url must be HTTPS and must resolve ONLY to public IPs. The
    returned IP is connection-pinned (``ssrf_pinned_transport``) so the fetch cannot be
    redirected to a private/internal address by DNS rebinding between this check and the
    HTTP client's connect.

    Raises:
        AdCPAdapterError: If the URL is not allowed.
    """
    validated_ip, error = resolve_validated_ip(agent_url, require_https=True)
    if validated_ip is None:
        raise AdCPAdapterError(f"Property list agent_url rejected: {error}")
    return validated_ip


def _build_request(ref: PropertyListReference) -> tuple[str, dict[str, str], str]:
    """Build (request_url, headers, validated_ip) for the property list fetch.

    Validates agent_url for SSRF and resolves the IP the connection is pinned to.
    """
    agent_url_str = str(ref.agent_url)
    validated_ip = _validated_agent_ip(agent_url_str)
    request_url = agent_url_str.rstrip("/") + "/lists/" + ref.list_id
    headers: dict[str, str] = {}
    if ref.auth_token:
        headers["Authorization"] = f"Bearer {ref.auth_token}"
    return request_url, headers, validated_ip


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
    """Parse the fetched payload, cache it with the right TTL, return the typed identifiers.

    A schema-invalid (but valid-JSON) 2xx body is the list SERVICE misbehaving, not a
    buyer mistake: map the pydantic ``ValidationError`` to a transient
    ``AdCPAdapterError`` at this shared chokepoint so BOTH resolver paths surface the
    same recovery class (mirroring ``_payload_or_raise``'s non-JSON handling) instead
    of a raw ``ValidationError`` that the Kevel sync path normalizes to terminal
    ``INTERNAL_ERROR`` while the async path silently swallows it.
    """
    try:
        parsed = GetPropertyListResponse.model_validate(response_data)
    except ValidationError as exc:
        raise AdCPAdapterError(f"Property list service returned a malformed response: {ref.agent_url}") from exc
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
    """Map an HTTP fetch failure to the typed error class, shared by both resolver paths.

    The status case routes through ``adcp_error_for_http_status`` (the single
    status->recovery table: 429/5xx -> transient, other 4xx -> correctable) so the
    property-list fetch and the adapter writes report the SAME recovery for the same
    status. A 4xx names the buyer's reference (unknown/forbidden/expired list_id) and
    carries the field/suggestion so the buyer fixes it; 429 and 5xx are the service
    misbehaving (transient). Timeouts and connection failures have no status — transient.
    """
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        raise adcp_error_for_http_status(
            status,
            f"The property list service at {ref.agent_url} returned HTTP {status} "
            f"for list_id '{loggable_list_id(ref.list_id)}'.",
            field="property_list",
            suggestion="Check the property list reference (agent_url and list_id), or pick a different list.",
        ) from exc
    if isinstance(exc, httpx.TimeoutException):
        raise AdCPAdapterError(f"Request to property list service timed out: {url}") from exc
    raise AdCPAdapterError(f"Failed to connect to property list service: {url} — {exc}") from exc


async def resolve_property_list_typed(ref: PropertyListReference) -> list[Identifier]:
    """Resolve a property list reference to a list of typed ``Identifier`` objects.

    Async path. Use the sync variant from synchronous code (e.g. ad-server
    adapters whose ``create_media_buy`` API is sync).
    """
    cached = _check_cache(ref)
    if cached is not None:
        return cached
    request_url, headers, validated_ip = _build_request(ref)

    try:
        # Pin the connection to the SSRF-validated IP (the URL keeps its hostname so TLS
        # cert verification stays normal) — a rebinding host cannot redirect to a private IP.
        async with httpx.AsyncClient(
            transport=ssrf_pinned_async_transport(validated_ip), timeout=_DEFAULT_TIMEOUT
        ) as client:
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
    cached = _check_cache(ref)
    if cached is not None:
        return cached
    request_url, headers, validated_ip = _build_request(ref)

    try:
        # Pin the connection to the SSRF-validated IP (see the async variant above).
        with httpx.Client(transport=ssrf_pinned_transport(validated_ip), timeout=_DEFAULT_TIMEOUT) as client:
            response = client.get(request_url, headers=headers)
            response.raise_for_status()
    except (httpx.HTTPStatusError, httpx.TimeoutException, httpx.RequestError) as exc:
        _raise_fetch_error(ref, request_url, exc)

    return _store_in_cache(ref, _payload_or_raise(response, request_url))


def clear_cache() -> None:
    """Clear the property list cache."""
    _cache.clear()
