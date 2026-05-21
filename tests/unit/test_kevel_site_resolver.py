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

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from adcp.types import Identifier, PropertyIdentifierTypes, PropertyListReference

from src.core.exceptions import AdCPAdapterError
from src.services.kevel_site_resolver import (
    KevelSiteResolver,
    ResolvedSiteIds,
    _normalize_domain,
)

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _clear_caches():
    """Each test starts with empty caches across the module-level resolver state."""
    KevelSiteResolver.clear_cache()
    from src.core.property_list_resolver import clear_cache

    clear_cache()
    yield
    KevelSiteResolver.clear_cache()
    clear_cache()


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
        import httpx

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


class TestResolvedSiteIdsDefaults:
    """ResolvedSiteIds carries useful defaults so callers can treat it like a value object."""

    def test_default_is_empty(self):
        result = ResolvedSiteIds()
        assert result.site_ids == set()
        assert result.unsupported_types == set()
        assert result.unresolvable_values == []
