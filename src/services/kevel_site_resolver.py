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
import json
import logging
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import ClassVar

import httpx
from adcp.types import Identifier, PropertyListReference

from src.core.exceptions import AdCPAdapterError
from src.core.property_list_resolver import loggable_list_id, resolve_property_list_typed_sync
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
    # (base_url, network_id, sha256(api_key)[:16]) -> ({host: site_id}, expires_at).
    _site_cache: ClassVar[dict[tuple[str, str, str], tuple[dict[str, int], datetime]]] = {}

    # Guards reads/writes on ``_site_cache`` so concurrent ``create_media_buy``
    # calls on the same network can't race. Without the lock, two threads
    # could observe ``cached is None`` simultaneously, both call
    # ``_fetch_all_sites()`` (double HTTP), and both write to the cache
    # (last-write-wins clobber). The expiry-drop branch could also ``del``
    # a key another thread had already refreshed, raising ``KeyError``. The
    # HTTP fetch stays OUTSIDE the lock (slow) — two concurrent fetches on
    # a cold cache are still possible and acceptable (both produce the same
    # data), but the cache pop/write is atomic.
    _cache_lock: ClassVar[threading.Lock] = threading.Lock()

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
        # Digest, not the raw credential: the key must isolate per-credential
        # but the plaintext api_key has no business persisting in a process-
        # global ClassVar beyond the instance that owns it.
        api_key_digest = hashlib.sha256(self.api_key.encode()).hexdigest()[:16]
        cache_key = (self.base_url, self.network_id, api_key_digest)

        with self._cache_lock:
            cached = self._site_cache.get(cache_key)
            if cached is not None:
                cached_lookup, cached_expires_at = cached
                if datetime.now(UTC) < cached_expires_at:
                    return cached_lookup
                # Expired — drop and re-fetch below. ``pop`` (not ``del``) so
                # we don't raise ``KeyError`` if another thread already
                # repopulated the entry between the read above and now.
                self._site_cache.pop(cache_key, None)

        # HTTP fetch outside the lock — Kevel pagination can take seconds and
        # holding the lock that long would serialize unrelated networks too.
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
        with self._cache_lock:
            # Last-write-wins: a concurrent fetch may have populated this key
            # in the brief window we were doing HTTP. Both fetches produce
            # the same data (within Kevel's API consistency), so overwriting
            # is safe and avoids the complexity of a fetch-in-progress
            # sentinel.
            self._site_cache[cache_key] = (lookup, expires_at)
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
            while True:
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
                sites.extend(items)
                if page >= total_pages:
                    break
                page += 1
        return sites

    @classmethod
    def clear_cache(cls) -> None:
        """Reset the module-level site index cache. Test-only utility."""
        with cls._cache_lock:
            cls._site_cache.clear()
