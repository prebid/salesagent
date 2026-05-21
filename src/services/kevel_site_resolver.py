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
   lookup ``{normalized_domain: site_id}``, and intersect with the list's
   identifier values.

The Kevel site index is cached per ``(base_url, network_id)`` with a 5-minute
TTL — long enough to amortize a multi-page list fetch across the burst of
``create_media_buy`` calls a typical buyer makes, short enough to pick up
publisher onboarding within the same operator's session.
"""

from __future__ import annotations

import logging
import urllib.parse
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import httpx
from adcp.types import PropertyListReference

from src.core.exceptions import AdCPAdapterError
from src.core.property_list_resolver import resolve_property_list_typed_sync

logger = logging.getLogger(__name__)

# Kevel identifier types we can compile to native siteIds.
# Domain/subdomain map cleanly to Site.Url; other types (ios_bundle, podcast,
# etc.) would need separate Kevel inventory primitives that aren't wired here.
_SUPPORTED_IDENTIFIER_TYPES = frozenset({"domain", "subdomain"})

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
    ``(base_url, network_id)`` so multiple resolvers in the same process for
    different networks don't collide.
    """

    # Module-level cache across resolver instances: (base_url, network_id) ->
    # ({normalized_domain: site_id}, expires_at).
    _site_cache: dict[tuple[str, str], tuple[dict[str, int], datetime]] = {}

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

    def resolve(self, ref: PropertyListReference) -> ResolvedSiteIds:
        """Resolve a property list reference to the set of Kevel siteIds it covers."""
        identifiers = resolve_property_list_typed_sync(ref)
        site_lookup = self._get_site_lookup()

        site_ids: set[int] = set()
        unsupported_types: set[str] = set()
        unresolvable_values: list[str] = []

        for ident in identifiers:
            ident_type = ident.type.value if hasattr(ident.type, "value") else str(ident.type)
            if ident_type not in _SUPPORTED_IDENTIFIER_TYPES:
                unsupported_types.add(ident_type)
                continue
            normalized = _normalize_domain(ident.value)
            site_id = site_lookup.get(normalized)
            if site_id is None:
                unresolvable_values.append(ident.value)
            else:
                site_ids.add(site_id)

        logger.debug(
            "Kevel resolve %s/%s → %d sites, %d unsupported types, %d unresolvable values",
            ref.agent_url,
            ref.list_id,
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
        """Return the cached ``{normalized_domain: site_id}`` index, fetching if needed."""
        cache_key = (self.base_url, self.network_id)
        cached = self._site_cache.get(cache_key)
        if cached is not None:
            cached_lookup, cached_expires_at = cached
            if datetime.now(UTC) < cached_expires_at:
                return cached_lookup
            # Expired — drop and re-fetch below.
            del self._site_cache[cache_key]

        sites = self._fetch_all_sites()
        lookup: dict[str, int] = {}
        for site in sites:
            site_id = site.get("Id")
            url = site.get("Url")
            if site_id is None or not url:
                continue
            normalized = _normalize_domain(url)
            if normalized:
                lookup[normalized] = int(site_id)

        expires_at = datetime.now(UTC) + timedelta(seconds=self.cache_ttl_seconds)
        self._site_cache[cache_key] = (lookup, expires_at)
        logger.debug(
            "Cached Kevel site index for network %s: %d sites (expires %s)",
            self.network_id,
            len(lookup),
            expires_at.isoformat(),
        )
        return lookup

    def _fetch_all_sites(self) -> list[dict]:
        """Page through Kevel ``GET /v1/site`` and return the aggregated list."""
        sites: list[dict] = []
        page = 1
        while True:
            url = f"{self.base_url}/site?page={page}&pageSize={_KEVEL_SITE_PAGE_SIZE}"
            try:
                with httpx.Client(timeout=self.timeout_seconds) as client:
                    response = client.get(url, headers={"X-Adzerk-ApiKey": self.api_key})
                    response.raise_for_status()
            except (httpx.HTTPStatusError, httpx.TimeoutException, httpx.RequestError) as exc:
                raise AdCPAdapterError(
                    f"Failed to fetch Kevel site list (network {self.network_id}, page {page}): {exc}"
                ) from exc

            payload = response.json()
            items = payload.get("items") or []
            sites.extend(items)
            total_pages = payload.get("totalPages") or 1
            if page >= total_pages:
                break
            page += 1
        return sites

    @classmethod
    def clear_cache(cls) -> None:
        """Reset the module-level site index cache. Test-only utility."""
        cls._site_cache.clear()


def _normalize_domain(value: str) -> str:
    """Normalize a URL-or-host string to a bare lowercase host without ``www.``.

    Examples::

        _normalize_domain("https://www.espn.com/sports") == "espn.com"
        _normalize_domain("www.espn.com") == "espn.com"
        _normalize_domain("ESPN.COM") == "espn.com"

    Returns an empty string when the input parses to nothing meaningful.
    """
    if not value:
        return ""
    if "://" in value:
        parsed = urllib.parse.urlparse(value)
        host = parsed.hostname or ""
    else:
        host = value.split("/", 1)[0]
    host = host.lower().strip()
    if host.startswith("www."):
        host = host[4:]
    return host
