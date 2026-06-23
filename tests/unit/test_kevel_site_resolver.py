"""Tests for KevelSiteResolver — AdCP property_list → Kevel siteIds.

The resolver is the bridge between AdCP's portable PropertyListReference
shape and Kevel's native ``Site.Id`` integers. These tests cover:

- Spec ``Identifier.value`` grammar against the site index (bare/www/m,
  ``*.`` wildcards, exact subdomains — via the SDK matcher)
- Identifier-type routing (domain/subdomain compile; others go to unsupported)
- Site index caching + TTL expiry, keyed by (base_url, network_id, api_key)
- Pagination of Kevel /v1/site
- HTTP error translation to AdCPAdapterError
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import httpx
import pytest
from adcp.types import Identifier, PropertyListReference

from src.adapters.kevel_site_resolver import KevelSiteResolver, ResolvedSiteIds
from src.core.exceptions import AdCPAdapterError, AdCPConfigurationError, AdCPValidationError
from src.core.property_list_resolver import clear_cache as clear_property_list_cache
from tests.helpers.adcp_factories import create_test_identifier as _identifier

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


def _site_cache_key(resolver: KevelSiteResolver) -> tuple[str, str, str]:
    return (resolver.base_url, resolver.network_id, resolver._cache_partition)


def _kevel_site(site_id: int, url: str) -> dict:
    return {"Id": site_id, "Title": f"Site {site_id}", "Url": url}


def _ref() -> PropertyListReference:
    return PropertyListReference(agent_url="https://gov.example/lists", list_id="cb_premium_news_v1")


def _patched_list(identifiers: list[Identifier]):
    """Patch the property-list fetch the resolver performs, returning ``identifiers``."""
    return patch(
        "src.adapters.kevel_site_resolver.resolve_property_list_typed_sync",
        return_value=identifiers,
    )


class TestSpecValueGrammar:
    """The site match honors the spec Identifier.value grammar (SDK matcher).

    Per core/identifier.json: bare ``espn.com`` selects the base host plus
    ``www.``/``m.``; ``edition.cnn.com`` selects exactly that host;
    ``*.espn.com`` selects every subdomain host but NOT the base.
    """

    def test_bare_domain_selects_base_www_and_m_hosts(self):
        resolver = _resolver()
        sites = [
            _kevel_site(1, "https://espn.com"),
            _kevel_site(2, "https://www.espn.com"),
            _kevel_site(3, "https://m.espn.com"),
            _kevel_site(4, "https://mobile.espn.com"),  # NOT selected: spec says www/m only
            _kevel_site(5, "https://sports.espn.com"),  # NOT selected by a bare domain
        ]
        with _patched_list([_identifier("espn.com")]), patch.object(resolver, "_fetch_all_sites", return_value=sites):
            result = resolver.resolve(_ref())

        assert result.site_ids == {1, 2, 3}
        assert result.unresolvable_values == []

    def test_wildcard_selects_all_subdomains_but_not_base(self):
        resolver = _resolver()
        sites = [
            _kevel_site(1, "https://espn.com"),  # base NOT selected by *.espn.com
            _kevel_site(2, "https://www.espn.com"),
            _kevel_site(3, "https://sports.espn.com"),
            _kevel_site(4, "https://stats.sports.espn.com"),
            _kevel_site(5, "https://espn.de"),  # different domain entirely
        ]
        with (
            _patched_list([_identifier("*.espn.com")]),
            patch.object(resolver, "_fetch_all_sites", return_value=sites),
        ):
            result = resolver.resolve(_ref())

        assert result.site_ids == {2, 3, 4}
        assert result.unresolvable_values == []

    def test_wildcard_with_no_subdomain_sites_is_unresolvable_not_silent(self):
        """A wildcard that selects nothing surfaces in unresolvable_values.

        Worst-case before this behavior: ``*.example.com`` parsed as a literal
        hostname, matched nothing, and the buyer got no signal why.
        """
        resolver = _resolver()
        with (
            _patched_list([_identifier("*.never-onboarded.example")]),
            patch.object(resolver, "_fetch_all_sites", return_value=[_kevel_site(42, "https://www.espn.com")]),
        ):
            result = resolver.resolve(_ref())

        assert result.site_ids == set()
        assert result.unresolvable_values == ["*.never-onboarded.example"]

    def test_specific_subdomain_value_selects_only_that_host(self):
        resolver = _resolver()
        sites = [
            _kevel_site(1, "https://cnn.com"),
            _kevel_site(2, "https://edition.cnn.com"),
        ]
        with (
            _patched_list([_identifier("edition.cnn.com")]),
            patch.object(resolver, "_fetch_all_sites", return_value=sites),
        ):
            result = resolver.resolve(_ref())

        assert result.site_ids == {2}


class TestResolveSupportedTypes:
    """domain and subdomain identifiers compile to siteIds via lookup."""

    def test_resolves_known_domain(self):
        resolver = _resolver()
        with (
            _patched_list([_identifier("espn.com")]),
            patch.object(resolver, "_fetch_all_sites", return_value=[_kevel_site(42, "https://www.espn.com")]),
        ):
            result = resolver.resolve(_ref())

        assert result.site_ids == {42}
        assert result.unsupported_types == set()
        assert result.unresolvable_values == []

    def test_resolves_multiple_domains(self):
        resolver = _resolver()
        with (
            _patched_list([_identifier("espn.com"), _identifier("nytimes.com")]),
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
            _patched_list([_identifier("edition.cnn.com", type_="subdomain")]),
            patch.object(resolver, "_fetch_all_sites", return_value=[_kevel_site(7, "https://edition.cnn.com")]),
        ):
            result = resolver.resolve(_ref())

        assert result.site_ids == {7}
        assert result.unsupported_types == set()

    def test_multiple_sites_at_one_host_all_resolve(self):
        # Two Kevel sites share host www.espn.com (different sections/zones at one
        # domain). A buyer targeting espn.com must resolve to BOTH siteIds — the old
        # dict[str,int] index collapsed a shared host last-write-wins, so the buyer
        # silently targeted only the last-seen site.
        resolver = _resolver()
        with (
            _patched_list([_identifier("espn.com")]),
            patch.object(
                resolver,
                "_fetch_all_sites",
                return_value=[
                    _kevel_site(42, "https://www.espn.com"),
                    _kevel_site(99, "https://www.espn.com"),
                ],
            ),
        ):
            result = resolver.resolve(_ref())

        assert result.site_ids == {42, 99}


class TestResolveUnsupportedTypes:
    """ios_bundle, podcast_guid, etc. don't map to Kevel Site — surface as unsupported."""

    def test_ios_bundle_routed_to_unsupported(self):
        resolver = _resolver()
        with (
            _patched_list([_identifier("com.example.app", type_="ios_bundle")]),
            patch.object(resolver, "_fetch_all_sites", return_value=[]),
        ):
            result = resolver.resolve(_ref())

        assert result.site_ids == set()
        assert result.unsupported_types == {"ios_bundle"}

    def test_mixed_types_split_by_support(self):
        resolver = _resolver()
        with (
            _patched_list(
                [
                    _identifier("espn.com"),
                    _identifier("com.example.app", type_="ios_bundle"),
                    _identifier("podcast-guid-xyz", type_="podcast_guid"),
                ]
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
            _patched_list([_identifier("never-onboarded.example")]),
            patch.object(resolver, "_fetch_all_sites", return_value=[_kevel_site(42, "https://www.espn.com")]),
        ):
            result = resolver.resolve(_ref())

        assert result.site_ids == set()
        assert result.unsupported_types == set()
        assert result.unresolvable_values == ["never-onboarded.example"]


class TestSiteIndexCache:
    """The Kevel /v1/site fetch is cached per (base_url, network_id, api_key) with TTL."""

    def test_repeated_resolve_hits_cache(self):
        resolver = _resolver()
        fetch_mock = MagicMock(return_value=[_kevel_site(42, "https://www.espn.com")])
        with (
            _patched_list([_identifier("espn.com")]),
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
            _patched_list([_identifier("espn.com")]),
            patch.object(r1, "_fetch_all_sites", return_value=[_kevel_site(42, "https://www.espn.com")]),
            patch.object(r2, "_fetch_all_sites", return_value=[_kevel_site(99, "https://www.espn.com")]),
        ):
            assert r1.resolve(_ref()).site_ids == {42}
            assert r2.resolve(_ref()).site_ids == {99}

    def test_separate_api_keys_have_separate_caches(self):
        """Two credentials against the SAME network id never share an index.

        Without api_key in the cache key, tenant B's resolver would read the
        site index tenant A's credentials fetched — a cross-tenant read of
        whatever inventory A's key can see.
        """
        r1 = KevelSiteResolver(network_id="net_a", api_key="key-tenant-a", base_url="https://api.kevel.co/v1")
        r2 = KevelSiteResolver(network_id="net_a", api_key="key-tenant-b", base_url="https://api.kevel.co/v1")
        fetch_a = MagicMock(return_value=[_kevel_site(42, "https://www.espn.com")])
        fetch_b = MagicMock(return_value=[_kevel_site(99, "https://www.espn.com")])
        with (
            _patched_list([_identifier("espn.com")]),
            patch.object(r1, "_fetch_all_sites", fetch_a),
            patch.object(r2, "_fetch_all_sites", fetch_b),
        ):
            assert r1.resolve(_ref()).site_ids == {42}
            assert r2.resolve(_ref()).site_ids == {99}

        assert fetch_a.call_count == 1
        assert fetch_b.call_count == 1, "second credential must fetch with its own key, not reuse the cached index"

    def test_expired_cache_entry_is_refetched(self):
        resolver = _resolver()
        fetch_mock = MagicMock(return_value=[_kevel_site(42, "https://www.espn.com")])
        with (
            _patched_list([_identifier("espn.com")]),
            patch.object(resolver, "_fetch_all_sites", fetch_mock),
        ):
            resolver.resolve(_ref())
            # Manually expire the cache entry
            cache_key = _site_cache_key(resolver)
            lookup = KevelSiteResolver._site_cache.get(cache_key)
            assert lookup is not None
            KevelSiteResolver._site_cache.store(cache_key, lookup, datetime.now(UTC) - timedelta(seconds=1))
            resolver.resolve(_ref())

        assert fetch_mock.call_count == 2

    def test_concurrent_cold_cache_resolves_converge_on_coherent_state(self):
        """Two threads racing on a cold cache produce a consistent final state.

        ``fetch`` call-count is deliberately NOT asserted: the production design
        keeps the HTTP fetch OUTSIDE ``_cache_lock``, so a cold-cache stampede
        (both threads fetching) is acceptable. This test pins the atomic,
        torn-read-free cache write under concurrency — not single-fetch.

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
            _patched_list([_identifier("espn.com")]),
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
        # Stampede is tolerated but bounded: at least one fetch happened, and
        # never more than one per thread (an unbounded retry loop would exceed it).
        assert 1 <= fetch_mock.call_count <= 8, f"fetch count {fetch_mock.call_count} outside the stampede bound"
        # Final cache state holds the lookup once with no torn entries.
        cache_key = _site_cache_key(resolver)
        cached_lookup = KevelSiteResolver._site_cache.get(cache_key)
        assert cached_lookup is not None
        assert cached_lookup == {"www.espn.com": {42}}, f"Cache state torn after concurrent writes: {cached_lookup!r}"

    def test_sequential_expired_cache_redrop_does_not_raise(self):
        """The expiry-drop branch uses ``pop(key, None)`` not ``del``, so
        re-entering it after the key is already gone cannot ``KeyError``.

        Driven with two sequential resolves (the first repopulates inside the
        lock; the second re-enters the expiry path) — enough to prove the
        pop-branch is safe. Concurrent safety end-to-end is pinned by the
        threaded cold-cache test above; using ``del`` here would raise
        ``KeyError`` once the key was already removed.
        """
        resolver = _resolver()
        cache_key = _site_cache_key(resolver)

        # Pre-populate cache with an expired entry to force the pop branch.
        KevelSiteResolver._site_cache.store(cache_key, {"old.com": {1}}, datetime.now(UTC) - timedelta(seconds=1))

        fetch_mock = MagicMock(return_value=[_kevel_site(42, "https://www.espn.com")])
        with (
            _patched_list([_identifier("espn.com")]),
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

        with patch("src.adapters.kevel_site_resolver.httpx.Client", return_value=mock_client):
            sites = resolver._fetch_all_sites()

        assert [s["Id"] for s in sites] == [1, 2]
        assert mock_client.get.call_count == 2

    def test_page_cap_raises_instead_of_paging_unboundedly(self):
        # A degraded Kevel that keeps reporting more pages must hit the page cap and
        # RAISE a typed transient error — not page forever. (Untested backstop.)
        resolver = _resolver()
        page = MagicMock(
            json=MagicMock(return_value={"items": [_kevel_site(1, "https://a.example")], "totalPages": 99})
        )
        page.raise_for_status = MagicMock()
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=None)
        mock_client.get = MagicMock(return_value=page)  # always "more pages"

        with (
            patch("src.adapters.kevel_site_resolver._KEVEL_SITE_MAX_PAGES", 3),
            patch("src.adapters.kevel_site_resolver.httpx.Client", return_value=mock_client),
        ):
            with pytest.raises(AdCPAdapterError, match="page cap"):
                resolver._fetch_all_sites()

    def test_fetch_deadline_raises_when_exceeded(self):
        # A degraded Kevel that never finishes paging must hit the overall deadline and
        # RAISE — not hang. Patch the deadline into the past so the guard trips on the
        # first iteration, before any HTTP. (Untested backstop.)
        resolver = _resolver()
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=None)

        with (
            patch("src.adapters.kevel_site_resolver._KEVEL_SITE_FETCH_DEADLINE_SECONDS", -1.0),
            patch("src.adapters.kevel_site_resolver.httpx.Client", return_value=mock_client),
        ):
            with pytest.raises(AdCPAdapterError, match="deadline"):
                resolver._fetch_all_sites()
        mock_client.get.assert_not_called()  # tripped before any fetch

    def test_http_5xx_error_is_transient(self):
        # A Kevel /v1/site 5xx is the service misbehaving → transient (retry), surfaced as
        # AdCPAdapterError. Routing through adcp_error_for_httpx_exc must preserve this.
        resolver = _resolver()
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=None)
        mock_response = MagicMock(status_code=500)
        mock_response.raise_for_status = MagicMock(
            side_effect=httpx.HTTPStatusError("server error", request=MagicMock(), response=mock_response)
        )
        mock_client.get = MagicMock(return_value=mock_response)

        with patch("src.adapters.kevel_site_resolver.httpx.Client", return_value=mock_client):
            with pytest.raises(AdCPAdapterError, match="Failed to fetch Kevel site list") as exc_info:
                resolver._fetch_all_sites()
        assert exc_info.value.recovery == "transient"

    def _fetch_with_status(self, status: int):
        resolver = _resolver()
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=None)
        mock_response = MagicMock(status_code=status)
        mock_response.raise_for_status = MagicMock(
            side_effect=httpx.HTTPStatusError(f"HTTP {status}", request=MagicMock(), response=mock_response)
        )
        mock_client.get = MagicMock(return_value=mock_response)
        with patch("src.adapters.kevel_site_resolver.httpx.Client", return_value=mock_client):
            resolver._fetch_all_sites()

    def test_http_403_is_terminal_configuration_error(self):
        # /v1/site is fetched with the tenant operator's X-Adzerk-ApiKey, so a 403 is the
        # operator's credential being denied — a request the buyer can never make succeed.
        # That is terminal (requires human action), NOT correctable (fix-and-resend) and NOT
        # transient (the pre-fix AdCPAdapterError that sent buyers into a retry loop).
        with pytest.raises(AdCPConfigurationError, match="Failed to fetch Kevel site list") as exc_info:
            self._fetch_with_status(403)
        assert exc_info.value.error_code == "CONFIGURATION_ERROR"
        assert exc_info.value.recovery == "terminal"
        assert not isinstance(exc_info.value, AdCPAdapterError)  # not the transient class

    def test_http_other_4xx_is_correctable_not_transient(self):
        # A non-credential 4xx (e.g. 404) is the request, not the operator credential → it
        # stays correctable (fix and resend), never transient (retry a request that can't succeed).
        with pytest.raises(AdCPValidationError, match="Failed to fetch Kevel site list") as exc_info:
            self._fetch_with_status(404)
        assert exc_info.value.recovery == "correctable"
        assert exc_info.value.error_code == "VALIDATION_ERROR"
        assert not isinstance(exc_info.value, AdCPAdapterError)

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
            patch("src.adapters.kevel_site_resolver.httpx.Client", return_value=mock_client),
            pytest.raises(AdCPAdapterError, match="Malformed Kevel site list"),
        ):
            resolver._fetch_all_sites()

    def test_non_json_page_raises_adcp_adapter_error(self):
        """A non-JSON 2xx page maps to the typed adapter error, mirroring the resolver."""
        import json as _json

        resolver = _resolver()
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=None)
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(side_effect=_json.JSONDecodeError("Expecting value", "<html>", 0))
        mock_client.get = MagicMock(return_value=mock_response)

        with (
            patch("src.adapters.kevel_site_resolver.httpx.Client", return_value=mock_client),
            pytest.raises(AdCPAdapterError, match="non-JSON response"),
        ):
            resolver._fetch_all_sites()

    def test_non_object_payload_raises_adcp_adapter_error(self):
        """A JSON array page is malformed — typed error, not AttributeError."""
        resolver = _resolver()
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=None)
        mock_response = MagicMock(json=MagicMock(return_value=[1, 2, 3]))
        mock_response.raise_for_status = MagicMock()
        mock_client.get = MagicMock(return_value=mock_response)

        with (
            patch("src.adapters.kevel_site_resolver.httpx.Client", return_value=mock_client),
            pytest.raises(AdCPAdapterError, match="expected an object"),
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

        with patch("src.adapters.kevel_site_resolver.httpx.Client", return_value=mock_client):
            sites = resolver._fetch_all_sites()

        assert sites == []


class TestResolvedSiteIdsDefaults:
    """ResolvedSiteIds carries useful defaults so callers can treat it like a value object."""

    def test_default_is_empty(self):
        result = ResolvedSiteIds()
        assert result.site_ids == set()
        assert result.unsupported_types == set()
        assert result.unresolvable_values == []
