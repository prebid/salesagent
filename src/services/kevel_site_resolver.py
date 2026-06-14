"""Resolve AdCP ``PropertyListReference`` to Kevel ``siteId`` integers.

The Kevel ad server represents publisher properties as ``Site`` entities,
each with a numeric ``Id`` that Kevel's targeting accepts in the ``siteIds``
field. To honor an AdCP ``targeting_overlay.property_list`` request against
a Kevel-backed campaign, we need to translate the referenced property list
(``domain``/``subdomain`` identifiers, fetched via the agent_url) into the
matching set of Kevel ``siteId`` integers.

Two-stage flow:

1. Fetch the property list via ``resolve_property_list_typed_sync`` — shared
   with the discovery-side resolver, so a list is only fetched once across
   both paths.
2. Fetch Kevel's full ``Site`` index via the ``/v1/site`` endpoint, build a
   lookup ``{host: site_id}`` keyed by each site's TRUE host (URL-parsed,
   lowercased — never prefix-stripped), and match the list's identifiers
   against it with the spec ``Identifier.value`` grammar
   (``src/services/identifier_matching.py``): a bare ``espn.com`` also selects
   ``www.``/``m.`` hosts, ``*.espn.com`` selects every subdomain host.

The Kevel site index is cached per ``(base_url, network_id, api_key)`` with a
5-minute TTL — long enough to amortize a multi-page list fetch across the
burst of ``create_media_buy`` calls a typical buyer makes, short enough to
pick up publisher onboarding within the same operator's session. The
``api_key`` participates in the key so two tenants pointing at the same
network id with different credentials can never read each other's cached
index.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import ClassVar

import httpx
from adcp.types import Identifier, PropertyListReference

from src.core.exceptions import AdCPAdapterError
from src.core.property_list_resolver import loggable_list_id, resolve_property_list_typed_sync
from src.core.ttl_cache import ThreadSafeTTLCache
from src.services.identifier_matching import (
    buyer_identifier_matches_host,
    host_from_url_or_host,
    identifier_type_str,
)

logger = logging.getLogger(__name__)

# Kevel identifier types we can compile to native siteIds.
# Domain/subdomain map cleanly to Site.Url; other types (ios_bundle, podcast,
# etc.) would need separate Kevel inventory primitives that aren't wired here.
# Internal to this module: the adapter classifies identifier types via
# ``KevelSiteResolver.classify_identifier_types`` rather than importing this set.
SUPPORTED_IDENTIFIER_TYPES: frozenset[str] = frozenset({"domain", "subdomain"})


_KEVEL_SITE_PAGE_SIZE = 200
_DEFAULT_HTTP_TIMEOUT = 10.0
_DEFAULT_CACHE_TTL_SECONDS = 300  # 5 minutes
# Overall wall-clock budget for a full (multi-page) site-index fetch, independent
# of the per-page HTTP timeout: bounds how long a cold-cache prewarm can hold a
# worker thread when Kevel is degraded. The page cap is a second backstop so a
# bogus ``totalPages`` cannot spin the pagination loop unboundedly (200 * 1000 =
# 200k sites, far beyond any real network).
_KEVEL_SITE_FETCH_DEADLINE_SECONDS = 60.0
_KEVEL_SITE_MAX_PAGES = 1000


@dataclass(frozen=True)
class ResolvedSiteIds:
    """Outcome of resolving a ``PropertyListReference`` against Kevel's site index.

    Attributes:
        site_ids: Kevel ``Site.Id`` integers matched by identifier values.
        unsupported_types: Identifier types in the list that Kevel cannot
            compile to native targeting today (e.g. ``ios_bundle``). Non-empty
            means the buyer asked for inventory Kevel cannot serve.
        unresolvable_values: Identifier values whose type was supported but
            for which Kevel has no matching ``Site`` (publisher hasn't been
            onboarded to the network). Empty when every supported-type
            identifier resolved.
    """

    site_ids: set[int] = field(default_factory=set)
    unsupported_types: set[str] = field(default_factory=set)
    unresolvable_values: list[str] = field(default_factory=list)


class KevelSiteResolver:
    """Stateful resolver scoped to a single Kevel network.

    Instantiate once per adapter; the underlying site-index cache is keyed by
    ``(base_url, network_id, api_key)`` so multiple resolvers in the same
    process for different networks — or different credentials against the same
    network id — never share an index.
    """

    # Module-level cache across resolver instances:
    # (base_url, network_id, hmac(api_key, purpose)[:16]) -> ({host: site_id}, expires_at).
    # Concurrent ``create_media_buy`` calls on the same network share this
    # index; the locking/expiry/last-write-wins contract lives in
    # ThreadSafeTTLCache.
    _site_cache: ClassVar[ThreadSafeTTLCache[tuple[str, str, str], dict[str, int]]] = ThreadSafeTTLCache()

    def __init__(
        self,
        *,
        network_id: str,
        api_key: str,
        base_url: str = "https://api.kevel.co/v1",
        cache_ttl_seconds: int = _DEFAULT_CACHE_TTL_SECONDS,
        timeout_seconds: float = _DEFAULT_HTTP_TIMEOUT,
    ) -> None:
        self.network_id = network_id
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.cache_ttl_seconds = cache_ttl_seconds
        self.timeout_seconds = timeout_seconds
        # Cache-partition token derived from the credential as an HMAC KEY
        # over a constant purpose label (HKDF-extract shape): a keyed PRF
        # whose output reveals nothing about the key, unlike hashing the
        # credential as data. The plaintext api_key thus never persists in
        # the process-global cache, while two credentials on the same
        # network id still partition into separate indexes.
        self._cache_partition = hmac.new(
            self.api_key.encode(), b"kevel-site-cache-partition", hashlib.sha256
        ).hexdigest()[:16]

    @classmethod
    def classify_identifier_types(cls, identifiers: list[Identifier]) -> set[str]:
        """Return the identifier types Kevel cannot compile to siteIds.

        Shared by the live ``resolve()`` and the adapter's dry-run branch so the
        membership check against ``SUPPORTED_IDENTIFIER_TYPES`` lives in exactly
        one place (callable without instantiating a resolver — no HTTP needed).
        """
        return {
            ident_type
            for ident in identifiers
            if (ident_type := identifier_type_str(ident)) not in SUPPORTED_IDENTIFIER_TYPES
        }

    def resolve(self, ref: PropertyListReference) -> ResolvedSiteIds:
        """Resolve a property list reference to the set of Kevel siteIds it covers."""
        identifiers = resolve_property_list_typed_sync(ref)
        site_lookup = self._get_site_lookup()

        unsupported_types = self.classify_identifier_types(identifiers)
        site_ids: set[int] = set()
        unresolvable_values: list[str] = []

        for ident in identifiers:
            if identifier_type_str(ident) not in SUPPORTED_IDENTIFIER_TYPES:
                continue
            # Spec value grammar via the SDK matcher: one identifier can select
            # multiple sites (``*.espn.com``) or none. A linear scan over the
            # cached index is fine — identifiers are dozens, sites are at most
            # a few thousand, and the index fetch is the amortized cost.
            matched = {site_id for host, site_id in site_lookup.items() if buyer_identifier_matches_host(ident, host)}
            if matched:
                site_ids.update(matched)
            else:
                unresolvable_values.append(ident.value)

        logger.debug(
            "Kevel resolve %s/%s → %d sites, %d unsupported types, %d unresolvable values",
            ref.agent_url,
            loggable_list_id(ref.list_id),
            len(site_ids),
            len(unsupported_types),
            len(unresolvable_values),
        )
        return ResolvedSiteIds(
            site_ids=site_ids,
            unsupported_types=unsupported_types,
            unresolvable_values=unresolvable_values,
        )

    def _get_site_lookup(self) -> dict[str, int]:
        """Return the cached ``{host: site_id}`` index, fetching if needed.

        Threading: the cache read + expiry-drop runs under ``_cache_lock`` so
        concurrent callers see a consistent view and the expiry ``pop`` cannot
        race a fresh write. The HTTP fetch happens OUTSIDE the lock — two
        concurrent cold-cache callers may both fetch (acceptable: both
        produce the same lookup), but the cache write back into ``_site_cache``
        is atomic and last-write-wins.
        """
        cache_key = (self.base_url, self.network_id, self._cache_partition)

        cached_lookup = self._site_cache.get(cache_key)
        if cached_lookup is not None:
            return cached_lookup

        # HTTP fetch outside the cache lock — Kevel pagination can take
        # seconds and holding it that long would serialize unrelated networks.
        sites = self._fetch_all_sites()
        lookup: dict[str, int] = {}
        for site in sites:
            site_id = site.get("Id")
            url = site.get("Url")
            if site_id is None or not url:
                continue
            # Index by the TRUE host — ``www.espn.com`` and ``espn.com`` are
            # distinct sites; the spec grammar (not prefix-stripping) decides
            # which buyer patterns select which hosts.
            host = host_from_url_or_host(url)
            if host:
                lookup[host] = int(site_id)

        expires_at = datetime.now(UTC) + timedelta(seconds=self.cache_ttl_seconds)
        self._site_cache.store(cache_key, lookup, expires_at)
        logger.debug(
            "Cached Kevel site index for network %s: %d sites (expires %s)",
            self.network_id,
            len(lookup),
            expires_at.isoformat(),
        )
        return lookup

    def _fetch_all_sites(self) -> list[dict]:
        """Page through Kevel ``GET /v1/site`` and return the aggregated list.

        The HTTP client is created once outside the pagination loop so pages
        reuse the connection (keep-alive). A per-page client would reconnect
        every page, defeating the cache lock that exists to amortize this fetch.
        """
        sites: list[dict] = []
        page = 1
        with httpx.Client(
            timeout=self.timeout_seconds,
            headers={"X-Adzerk-ApiKey": self.api_key},
        ) as client:
            deadline = datetime.now(UTC) + timedelta(seconds=_KEVEL_SITE_FETCH_DEADLINE_SECONDS)
            while True:
                if datetime.now(UTC) > deadline:
                    raise AdCPAdapterError(
                        f"Kevel site list fetch exceeded the {_KEVEL_SITE_FETCH_DEADLINE_SECONDS:.0f}s overall "
                        f"deadline (network {self.network_id}, page {page})"
                    )
                if page > _KEVEL_SITE_MAX_PAGES:
                    raise AdCPAdapterError(
                        f"Kevel site list exceeded the {_KEVEL_SITE_MAX_PAGES}-page cap "
                        f"(network {self.network_id}); refusing to page unboundedly"
                    )
                url = f"{self.base_url}/site?page={page}&pageSize={_KEVEL_SITE_PAGE_SIZE}"
                try:
                    response = client.get(url)
                    response.raise_for_status()
                except (httpx.HTTPStatusError, httpx.TimeoutException, httpx.RequestError) as exc:
                    raise AdCPAdapterError(
                        f"Failed to fetch Kevel site list (network {self.network_id}, page {page}): {exc}"
                    ) from exc

                try:
                    payload = response.json()
                except json.JSONDecodeError as exc:
                    raise AdCPAdapterError(
                        f"Kevel site list returned a non-JSON response (network {self.network_id}, page {page})"
                    ) from exc
                if not isinstance(payload, dict):
                    raise AdCPAdapterError(
                        f"Malformed Kevel site list response (network {self.network_id}, page {page}): "
                        f"expected an object, got {type(payload).__name__}"
                    )
                items = payload.get("items")
                total_pages = payload.get("totalPages")
                if items is None or total_pages is None:
                    # Distinguish a malformed page (missing/null key) from a
                    # legitimately empty one (``items: []``). Silently coercing a
                    # malformed page to "no sites / single page" would cache a
                    # truncated site index, making every domain identifier resolve
                    # to no-match — a quiet failure. Raise instead.
                    raise AdCPAdapterError(
                        f"Malformed Kevel site list response (network {self.network_id}, page {page}): "
                        f"expected 'items' and 'totalPages', got keys {sorted(payload.keys())}"
                    )
                if not isinstance(items, list) or not isinstance(total_pages, int | float):
                    # Guard the TYPE, not just presence: a string ``totalPages``
                    # ("5") makes ``page >= total_pages`` raise TypeError and a
                    # non-list ``items`` makes ``sites.extend`` add garbage — both
                    # escaping as an untyped INTERNAL_ERROR/terminal instead of this
                    # typed transient. (bool is an int subclass, but Kevel never
                    # serializes these keys as bool, so no special-casing.)
                    raise AdCPAdapterError(
                        f"Malformed Kevel site list response (network {self.network_id}, page {page}): "
                        f"expected list 'items' and numeric 'totalPages', got "
                        f"items={type(items).__name__}, totalPages={type(total_pages).__name__}"
                    )
                sites.extend(items)
                if page >= total_pages:
                    break
                page += 1
        return sites

    @classmethod
    def clear_cache(cls) -> None:
        """Reset the module-level site index cache. Test-only utility."""
        cls._site_cache.clear()
