"""Tests for KevelSiteResolver — AdCP property_list → Kevel siteIds.

The resolver is the bridge between AdCP's portable PropertyListReference
shape and Kevel's native ``Site.Id`` integers. These tests cover:

- Domain normalization (URLs, www-stripping, case-folding)
- Identifier-type routing (domain/subdomain compile; others go to unsupported)
- Site index caching + TTL expiry
- Pagination of Kevel /v1/site
- HTTP error translation to AdCPAdapterError
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
import pytest
from adcp.types import Identifier, PropertyIdentifierTypes, PropertyListReference

from src.core.exceptions import AdCPAdapterError
from src.core.property_list_resolver import clear_cache as clear_property_list_cache
from src.services.kevel_site_resolver import (
    KevelSiteResolver,
    ResolvedSiteIds,
    _normalize_domain,
    identifier_type_str,
)

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _clear_caches():
    """Each test starts with empty caches across the module-level resolver state."""
    KevelSiteResolver.clear_cache()
    clear_property_list_cache()
    yield
    KevelSiteResolver.clear_cache()
    clear_property_list_cache()


def _resolver() -> KevelSiteResolver:
    return KevelSiteResolver(network_id="123", api_key="test-key", base_url="https://api.kevel.co/v1")


def _kevel_site(site_id: int, url: str) -> dict:
    return {"Id": site_id, "Title": f"Site {site_id}", "Url": url}


def _identifier(value: str, type_: str = "domain") -> Identifier:
    return Identifier(type=PropertyIdentifierTypes(type_), value=value)


def _ref() -> PropertyListReference:
    return PropertyListReference(agent_url="https://gov.example/lists", list_id="cb_premium_news_v1")


class TestNormalizeDomain:
    """The host-extraction helper underlying the Site.Url ↔ identifier-value match."""

    def test_strips_scheme_and_path(self):
        assert _normalize_domain("https://www.espn.com/sports/nba") == "espn.com"

    def test_strips_www(self):
        assert _normalize_domain("www.espn.com") == "espn.com"

    def test_lowercases(self):
        assert _normalize_domain("ESPN.COM") == "espn.com"

    def test_bare_host(self):
        assert _normalize_domain("espn.com") == "espn.com"

    def test_empty_input_returns_empty(self):
        assert _normalize_domain("") == ""

    def test_keeps_non_www_subdomain(self):
        assert _normalize_domain("https://edition.cnn.com/world") == "edition.cnn.com"


class TestIdentifierTypeStr:
    """identifier_type_str normalizes enum-typed and bare-string identifier types."""

    def test_enum_type_returns_value(self):
        ident = Identifier(type=PropertyIdentifierTypes("domain"), value="espn.com")
        assert identifier_type_str(ident) == "domain"

    def test_bare_string_type_returns_str(self):
        # Defensive path: some deserializations leave ``.type`` as a bare string
        # (no ``.value``). SimpleNamespace mimics that shape without an enum.
        bare = SimpleNamespace(type="ios_bundle", value="com.example.app")
        assert identifier_type_str(bare) == "ios_bundle"


class TestResolveSupportedTypes:
    """domain and subdomain identifiers compile to siteIds via lookup."""

    def test_resolves_known_domain(self):
        resolver = _resolver()
        with (
            patch(
                "src.services.kevel_site_resolver.resolve_property_list_typed_sync",
                return_value=[_identifier("espn.com")],
            ),
            patch.object(resolver, "_fetch_all_sites", return_value=[_kevel_site(42, "https://www.espn.com")]),
        ):
            result = resolver.resolve(_ref())

        assert result.site_ids == {42}
        assert result.unsupported_types == set()
        assert result.unresolvable_values == []

    def test_resolves_multiple_domains(self):
        resolver = _resolver()
        with (
            patch(
                "src.services.kevel_site_resolver.resolve_property_list_typed_sync",
                return_value=[_identifier("espn.com"), _identifier("nytimes.com")],
            ),
            patch.object(
                resolver,
                "_fetch_all_sites",
                return_value=[
                    _kevel_site(42, "https://www.espn.com"),
                    _kevel_site(99, "https://www.nytimes.com"),
                ],
            ),
        ):
            result = resolver.resolve(_ref())

        assert result.site_ids == {42, 99}

    def test_subdomain_identifier_compiles(self):
        resolver = _resolver()
        with (
            patch(
                "src.services.kevel_site_resolver.resolve_property_list_typed_sync",
                return_value=[_identifier("edition.cnn.com", type_="subdomain")],
            ),
            patch.object(resolver, "_fetch_all_sites", return_value=[_kevel_site(7, "https://edition.cnn.com")]),
        ):
            result = resolver.resolve(_ref())

        assert result.site_ids == {7}
        assert result.unsupported_types == set()


class TestResolveUnsupportedTypes:
    """ios_bundle, podcast_guid, etc. don't map to Kevel Site — surface as unsupported."""

    def test_ios_bundle_routed_to_unsupported(self):
        resolver = _resolver()
        with (
            patch(
                "src.services.kevel_site_resolver.resolve_property_list_typed_sync",
                return_value=[_identifier("com.example.app", type_="ios_bundle")],
            ),
            patch.object(resolver, "_fetch_all_sites", return_value=[]),
        ):
            result = resolver.resolve(_ref())

        assert result.site_ids == set()
        assert result.unsupported_types == {"ios_bundle"}

    def test_mixed_types_split_by_support(self):
        resolver = _resolver()
        with (
            patch(
                "src.services.kevel_site_resolver.resolve_property_list_typed_sync",
                return_value=[
                    _identifier("espn.com"),
                    _identifier("com.example.app", type_="ios_bundle"),
                    _identifier("podcast-guid-xyz", type_="podcast_guid"),
                ],
            ),
            patch.object(resolver, "_fetch_all_sites", return_value=[_kevel_site(42, "https://www.espn.com")]),
        ):
            result = resolver.resolve(_ref())

        assert result.site_ids == {42}
        assert result.unsupported_types == {"ios_bundle", "podcast_guid"}


class TestUnresolvableValues:
    """Supported types whose value has no matching Kevel Site → unresolvable_values."""

    def test_domain_not_in_kevel_index(self):
        resolver = _resolver()
        with (
            patch(
                "src.services.kevel_site_resolver.resolve_property_list_typed_sync",
                return_value=[_identifier("never-onboarded.example")],
            ),
            patch.object(resolver, "_fetch_all_sites", return_value=[_kevel_site(42, "https://www.espn.com")]),
        ):
            result = resolver.resolve(_ref())

        assert result.site_ids == set()
        assert result.unsupported_types == set()
        assert result.unresolvable_values == ["never-onboarded.example"]


class TestSiteIndexCache:
    """The Kevel /v1/site fetch is cached per (base_url, network_id) with TTL."""

    def test_repeated_resolve_hits_cache(self):
        resolver = _resolver()
        fetch_mock = MagicMock(return_value=[_kevel_site(42, "https://www.espn.com")])
        with (
            patch(
                "src.services.kevel_site_resolver.resolve_property_list_typed_sync",
                return_value=[_identifier("espn.com")],
            ),
            patch.object(resolver, "_fetch_all_sites", fetch_mock),
        ):
            resolver.resolve(_ref())
            resolver.resolve(_ref())
            resolver.resolve(_ref())

        assert fetch_mock.call_count == 1, "Kevel /v1/site should only be fetched once across repeated resolves"

    def test_separate_networks_have_separate_caches(self):
        r1 = KevelSiteResolver(network_id="net_a", api_key="k", base_url="https://api.kevel.co/v1")
        r2 = KevelSiteResolver(network_id="net_b", api_key="k", base_url="https://api.kevel.co/v1")
        with (
            patch(
                "src.services.kevel_site_resolver.resolve_property_list_typed_sync",
                return_value=[_identifier("espn.com")],
            ),
            patch.object(r1, "_fetch_all_sites", return_value=[_kevel_site(42, "https://www.espn.com")]),
            patch.object(r2, "_fetch_all_sites", return_value=[_kevel_site(99, "https://www.espn.com")]),
        ):
            assert r1.resolve(_ref()).site_ids == {42}
            assert r2.resolve(_ref()).site_ids == {99}

    def test_expired_cache_entry_is_refetched(self):
        resolver = _resolver()
        fetch_mock = MagicMock(return_value=[_kevel_site(42, "https://www.espn.com")])
        with (
            patch(
                "src.services.kevel_site_resolver.resolve_property_list_typed_sync",
                return_value=[_identifier("espn.com")],
            ),
            patch.object(resolver, "_fetch_all_sites", fetch_mock),
        ):
            resolver.resolve(_ref())
            # Manually expire the cache entry
            cache_key = (resolver.base_url, resolver.network_id)
            lookup, _expires = KevelSiteResolver._site_cache[cache_key]
            KevelSiteResolver._site_cache[cache_key] = (lookup, datetime.now(UTC) - timedelta(seconds=1))
            resolver.resolve(_ref())

        assert fetch_mock.call_count == 2

    def test_concurrent_cold_cache_resolves_share_same_lookup(self):
        """Two threads racing on a cold cache produce a consistent final state.

        ``_cache_lock`` guards cache reads/writes; HTTP stays OUTSIDE the
        lock so two threads on a cold cache may still both fetch (acceptable
        — Kevel returns the same data), but the cache write is atomic and
        the expiry-pop uses ``dict.pop(key, None)`` so it cannot
        ``KeyError``. This test pins the post-condition: after concurrent
        resolves both threads converge on a non-empty resolved set and the
        cache holds a single coherent lookup.

        Without the lock, two simultaneous resolves on the same network
        could observe ``cached is None`` simultaneously, both fetch, and
        last-write-wins clobber the cache; the expiry-drop branch using
        ``del`` could also race a concurrent refresh and raise ``KeyError``.
        """
        resolver = _resolver()
        fetch_mock = MagicMock(return_value=[_kevel_site(42, "https://www.espn.com")])
        results: list[ResolvedSiteIds] = []
        results_lock = threading.Lock()

        def _run():
            res = resolver.resolve(_ref())
            with results_lock:
                results.append(res)

        with (
            patch(
                "src.services.kevel_site_resolver.resolve_property_list_typed_sync",
                return_value=[_identifier("espn.com")],
            ),
            patch.object(resolver, "_fetch_all_sites", fetch_mock),
        ):
            threads = [threading.Thread(target=_run) for _ in range(8)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=5.0)

        # All 8 threads completed without raising (no KeyError on expiry race).
        assert len(results) == 8, f"Expected 8 thread results, got {len(results)} — some threads raised/hung"
        # Every thread observes the same resolved set — no torn write.
        assert all(r.site_ids == {42} for r in results), (
            "Concurrent resolvers returned inconsistent site_ids — cache write was not atomic"
        )
        # Final cache state holds the lookup once with no torn entries.
        cache_key = (resolver.base_url, resolver.network_id)
        cached_lookup, _expires_at = KevelSiteResolver._site_cache[cache_key]
        assert cached_lookup == {"espn.com": 42}, f"Cache state torn after concurrent writes: {cached_lookup!r}"

    def test_concurrent_expired_cache_drop_does_not_raise(self):
        """Concurrent expiry drops use ``pop`` not ``del`` so a second thread
        finding an already-popped key cannot ``KeyError``.

        Both threads call ``self._site_cache.pop(cache_key, None)`` which is
        a no-op on the second pop. Using ``del`` instead would raise
        ``KeyError`` when a second thread reaches the expiry branch after
        the first has already removed the key.
        """
        resolver = _resolver()
        cache_key = (resolver.base_url, resolver.network_id)

        # Pre-populate cache with an expired entry to force the pop branch.
        KevelSiteResolver._site_cache[cache_key] = ({"old.com": 1}, datetime.now(UTC) - timedelta(seconds=1))

        fetch_mock = MagicMock(return_value=[_kevel_site(42, "https://www.espn.com")])
        with (
            patch(
                "src.services.kevel_site_resolver.resolve_property_list_typed_sync",
                return_value=[_identifier("espn.com")],
            ),
            patch.object(resolver, "_fetch_all_sites", fetch_mock),
        ):
            # Two sequential calls both see expired entry on entry to the
            # locked block (the first call repopulates inside the lock; the
            # second call sees the fresh entry). The test mainly proves that
            # the expiry-pop branch is safe; the threaded test above proves
            # concurrent safety end-to-end.
            r1 = resolver.resolve(_ref())
            r2 = resolver.resolve(_ref())

        assert r1.site_ids == {42}
        assert r2.site_ids == {42}


class TestPaginationAndFetch:
    """_fetch_all_sites pages through Kevel until totalPages."""

    def test_pages_through_multi_page_response(self):
        resolver = _resolver()
        responses = [
            MagicMock(json=MagicMock(return_value={"items": [_kevel_site(1, "https://a.example")], "totalPages": 2})),
            MagicMock(json=MagicMock(return_value={"items": [_kevel_site(2, "https://b.example")], "totalPages": 2})),
        ]
        for response in responses:
            response.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=None)
        mock_client.get = MagicMock(side_effect=responses)

        with patch("src.services.kevel_site_resolver.httpx.Client", return_value=mock_client):
            sites = resolver._fetch_all_sites()

        assert [s["Id"] for s in sites] == [1, 2]
        assert mock_client.get.call_count == 2

    def test_http_error_raises_adcp_adapter_error(self):
        resolver = _resolver()
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=None)
        mock_response = MagicMock(status_code=500)
        mock_response.raise_for_status = MagicMock(
            side_effect=httpx.HTTPStatusError("server error", request=MagicMock(), response=mock_response)
        )
        mock_client.get = MagicMock(return_value=mock_response)

        with (
            patch("src.services.kevel_site_resolver.httpx.Client", return_value=mock_client),
            pytest.raises(AdCPAdapterError, match="Failed to fetch Kevel site list"),
        ):
            resolver._fetch_all_sites()

    def test_malformed_page_missing_key_raises(self):
        """A 2xx page missing 'items' or 'totalPages' is malformed → raise.

        Silently coercing it to "no sites / single page" would cache a
        truncated site index, making every domain identifier mis-resolve to
        no-match (a quiet failure). The resolver must surface it instead.
        """
        resolver = _resolver()
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=None)
        # 'items' present but 'totalPages' absent → malformed.
        mock_response = MagicMock(json=MagicMock(return_value={"items": [_kevel_site(1, "https://a.example")]}))
        mock_response.raise_for_status = MagicMock()
        mock_client.get = MagicMock(return_value=mock_response)

        with (
            patch("src.services.kevel_site_resolver.httpx.Client", return_value=mock_client),
            pytest.raises(AdCPAdapterError, match="Malformed Kevel site list"),
        ):
            resolver._fetch_all_sites()

    def test_empty_items_page_is_valid(self):
        """An explicit empty page ('items': []) is valid — not malformed.

        Distinguishes present-but-empty (no sites onboarded) from the
        missing-key malformed case above.
        """
        resolver = _resolver()
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=None)
        mock_response = MagicMock(json=MagicMock(return_value={"items": [], "totalPages": 1}))
        mock_response.raise_for_status = MagicMock()
        mock_client.get = MagicMock(return_value=mock_response)

        with patch("src.services.kevel_site_resolver.httpx.Client", return_value=mock_client):
            sites = resolver._fetch_all_sites()

        assert sites == []


class TestResolvedSiteIdsDefaults:
    """ResolvedSiteIds carries useful defaults so callers can treat it like a value object."""

    def test_default_is_empty(self):
        result = ResolvedSiteIds()
        assert result.site_ids == set()
        assert result.unsupported_types == set()
        assert result.unresolvable_values == []
