"""Tests for Kevel adapter's property_list compilation (B3).

Backs the AdCP honest-declaration contract item that "an enabled adapter MUST
translate property_list" — Kevel is the first salesagent adapter to satisfy
that side of the contract (vs B4's UNSUPPORTED_FEATURE raises for adapters
that can't).

These tests pin the three observable behaviors:

1. Kevel advertises ``supports_property_list_filtering=True`` so
   ``get_adcp_capabilities`` flips ``property_list_filtering`` to True for
   Kevel-configured tenants.
2. Identifier-type validation at ``_check_property_list_supported`` rejects
   property lists whose contents Kevel cannot compile.
3. ``_build_targeting`` writes the resolved Kevel ``siteIds`` into the
   native targeting payload.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from adcp.types import PropertyListReference

from src.adapters.kevel import Kevel
from src.core.schemas import CreateMediaBuyError, MediaPackage, Principal
from src.services.kevel_site_resolver import ResolvedSiteIds

pytestmark = pytest.mark.unit


def _principal() -> Principal:
    return Principal(
        principal_id="p_test",
        name="Test Principal",
        platform_mappings={"kevel": {"advertiser_id": "kevel_adv_1"}},
    )


def _kevel(*, dry_run: bool = False) -> Kevel:
    return Kevel(
        config={"network_id": "100", "api_key": "test-key"},
        principal=_principal(),
        dry_run=dry_run,
        tenant_id="t_test",
    )


def _package_with_ref(ref: PropertyListReference) -> MediaPackage:
    pkg = MagicMock(spec=MediaPackage)
    pkg.package_id = "pkg_pl"
    pkg.targeting_overlay = MagicMock()
    pkg.targeting_overlay.property_list = ref
    return pkg


def _ref() -> PropertyListReference:
    return PropertyListReference(agent_url="https://gov.example/lists", list_id="cb_news_v1")


class TestSupportsPropertyListFilteringFlag:
    """Class attribute drives the capability declaration."""

    def test_kevel_class_advertises_native_support(self):
        assert Kevel.supports_property_list_filtering is True


class TestCheckPropertyListSupportedAcceptsCompilable:
    """All-domain/subdomain lists pass pre-flight."""

    def test_returns_none_when_all_identifiers_are_domain(self):
        adapter = _kevel()
        with patch.object(adapter, "_site_resolver") as mock_resolver:
            mock_resolver.resolve.return_value = ResolvedSiteIds(
                site_ids={42, 99}, unsupported_types=set(), unresolvable_values=[]
            )
            result = adapter._check_property_list_supported([_package_with_ref(_ref())])

        assert result is None

    def test_returns_none_when_zero_sites_match(self):
        """Zero-match must be accept-with-context (SD2 in the inventory-targeting plan)."""
        adapter = _kevel()
        with patch.object(adapter, "_site_resolver") as mock_resolver:
            mock_resolver.resolve.return_value = ResolvedSiteIds(
                site_ids=set(), unsupported_types=set(), unresolvable_values=["never-onboarded.example"]
            )
            result = adapter._check_property_list_supported([_package_with_ref(_ref())])

        assert result is None, "Zero-match must not reject; downstream handles inventory_list_no_match"

    def test_no_property_list_skipped(self):
        adapter = _kevel()
        pkg = MagicMock(spec=MediaPackage)
        pkg.targeting_overlay = MagicMock()
        pkg.targeting_overlay.property_list = None
        assert adapter._check_property_list_supported([pkg]) is None


class TestCheckPropertyListSupportedRejectsIncompatible:
    """Lists containing identifier types Kevel can't compile must surface UNSUPPORTED_FEATURE."""

    def test_returns_error_when_list_contains_unsupported_types(self):
        adapter = _kevel()
        with patch.object(adapter, "_site_resolver") as mock_resolver:
            mock_resolver.resolve.return_value = ResolvedSiteIds(
                site_ids=set(), unsupported_types={"ios_bundle", "podcast_guid"}, unresolvable_values=[]
            )
            result = adapter._check_property_list_supported([_package_with_ref(_ref())])

        assert isinstance(result, CreateMediaBuyError)
        assert result.errors[0].code == "UNSUPPORTED_FEATURE"
        assert "ios_bundle" in result.errors[0].message
        assert "podcast_guid" in result.errors[0].message

    def test_dry_run_still_rejects_unsupported_identifier_types(self):
        """Dry-run mode must still validate identifier types (SHOULD-FIX-04).

        Pre-fix contract: dry-run fell back to ``super()._check_property_list_supported``
        which short-circuits when ``supports_property_list_filtering=True``, so
        ``ios_bundle``-only lists silently passed dry-run even though the live
        path would reject them. That meant test-mode validation gave a false
        green for buyers who would fail in prod — exactly the kind of
        dry_run-only short-circuit Konstantine #1313 flagged as illusory
        coverage.

        Post-fix: ``_resolve_property_list`` runs the typed-identifier fetch
        (no Kevel HTTP) even in dry-run, populates ``unsupported_types``, and
        ``_check_property_list_supported`` rejects with UNSUPPORTED_FEATURE.
        """
        from adcp.types import Identifier, PropertyIdentifierTypes

        adapter = _kevel(dry_run=True)
        with patch(
            "src.adapters.kevel.resolve_property_list_typed_sync",
            return_value=[
                Identifier(type=PropertyIdentifierTypes.ios_bundle, value="com.example.app"),
                Identifier(type=PropertyIdentifierTypes.podcast_guid, value="abc-123"),
            ],
        ):
            result = adapter._check_property_list_supported([_package_with_ref(_ref())])

        assert isinstance(result, CreateMediaBuyError)
        assert result.errors[0].code == "UNSUPPORTED_FEATURE"
        assert "ios_bundle" in result.errors[0].message
        assert "podcast_guid" in result.errors[0].message

    def test_dry_run_with_supported_types_passes_validation(self):
        """Dry-run + only domain/subdomain identifiers → pre-flight passes (no rejection)."""
        from adcp.types import Identifier, PropertyIdentifierTypes

        adapter = _kevel(dry_run=True)
        with patch(
            "src.adapters.kevel.resolve_property_list_typed_sync",
            return_value=[Identifier(type=PropertyIdentifierTypes.domain, value="espn.com")],
        ):
            result = adapter._check_property_list_supported([_package_with_ref(_ref())])

        assert result is None


class TestBuildTargetingCompilesPropertyList:
    """_build_targeting writes resolved siteIds into the Kevel native payload."""

    def _make_overlay(self, ref: PropertyListReference | None):
        overlay = MagicMock()
        overlay.geo_countries = None
        overlay.geo_regions = None
        overlay.geo_metros = None
        overlay.keywords_any_of = None
        overlay.device_type_any_of = None
        overlay.audiences_any_of = None
        overlay.frequency_cap = None
        overlay.custom = None
        overlay.key_value_pairs = None
        overlay.property_list = ref
        return overlay

    def test_resolved_site_ids_land_in_kevel_targeting(self):
        adapter = _kevel()
        with patch.object(adapter, "_site_resolver") as mock_resolver:
            mock_resolver.resolve.return_value = ResolvedSiteIds(
                site_ids={42, 99, 7}, unsupported_types=set(), unresolvable_values=[]
            )
            result = adapter._build_targeting(self._make_overlay(_ref()))

        assert result.get("siteIds") == [7, 42, 99], "siteIds must be sorted for deterministic output"

    def test_property_list_site_ids_union_with_custom_site_ids(self):
        """When buyer specifies both targeting_overlay.property_list and custom.kevel.site_ids, both contribute."""
        adapter = _kevel()
        overlay = self._make_overlay(_ref())
        overlay.custom = {"kevel": {"site_ids": [1, 2, 42]}}

        with patch.object(adapter, "_site_resolver") as mock_resolver:
            mock_resolver.resolve.return_value = ResolvedSiteIds(
                site_ids={42, 99}, unsupported_types=set(), unresolvable_values=[]
            )
            result = adapter._build_targeting(overlay)

        assert result["siteIds"] == [1, 2, 42, 99], "custom site_ids and property_list-resolved siteIds must union"

    def test_no_property_list_leaves_targeting_unchanged(self):
        adapter = _kevel()
        result = adapter._build_targeting(self._make_overlay(None))
        assert "siteIds" not in result

    def test_resolver_called_once_per_request_across_check_and_build(self):
        """The resolver fires once per (agent_url, list_id), not once in
        ``_check_property_list_supported`` and again in ``_build_targeting``
        (SHOULD-FIX-05).

        ``_resolve_property_list`` memoizes by ``(agent_url, list_id)`` on
        the adapter instance, so the underlying ``KevelSiteResolver.resolve``
        is called exactly once even though both lifecycle methods need the
        result. The previous shape called the resolver twice (the cache made
        the second call free in terms of HTTP, but the redundant walk
        through identifiers and the cache-lookup contention were avoidable
        — and the duplicate calls would survive a future refactor that
        removed caching).

        Pin: removing memoization from ``_resolve_property_list`` makes this
        test fail (call_count becomes 2 — the check call + the build call).
        """
        adapter = _kevel()
        package = _package_with_ref(_ref())
        overlay = self._make_overlay(_ref())

        with patch.object(adapter, "_site_resolver") as mock_resolver:
            mock_resolver.resolve.return_value = ResolvedSiteIds(
                site_ids={42}, unsupported_types=set(), unresolvable_values=[]
            )

            check_result = adapter._check_property_list_supported([package])
            build_result = adapter._build_targeting(overlay)

        assert check_result is None
        assert build_result.get("siteIds") == [42]
        assert mock_resolver.resolve.call_count == 1, (
            f"Resolver should fire once per (agent_url, list_id) per request — "
            f"check + build_targeting share the cached resolution. Got "
            f"{mock_resolver.resolve.call_count} calls."
        )

    def test_dry_run_writes_empty_site_ids(self):
        """In dry-run mode the resolver doesn't hit Kevel's /v1/site, but the
        compilation step DOES run — it writes ``siteIds=[]`` (the empty set
        from ``ResolvedSiteIds`` constructed by the dry-run path).

        Pre-fix: ``_build_targeting`` guarded on ``self._site_resolver is not None``
        and produced no ``siteIds`` key in dry-run. After SHOULD-FIX-05's
        memoizing ``_resolve_property_list``, dry-run still calls it (returns
        empty ``site_ids``) and writes the empty list. This matches plan SD2:
        zero-match is accept-with-context, not a no-op — the buy is created
        with ``siteIds=[]`` and the downstream ``inventory_list_no_match``
        contract surfaces it.
        """
        from adcp.types import Identifier, PropertyIdentifierTypes

        adapter = _kevel(dry_run=True)
        with patch(
            "src.adapters.kevel.resolve_property_list_typed_sync",
            return_value=[Identifier(type=PropertyIdentifierTypes.domain, value="espn.com")],
        ):
            result = adapter._build_targeting(self._make_overlay(_ref()))

        assert result.get("siteIds") == [], (
            "Dry-run must write siteIds=[] (accept-with-empty per plan SD2), "
            f"not skip the key entirely; got {result.get('siteIds')!r}"
        )


class TestEndToEndWiringSiteIdsLandInFlightPayload:
    """The full chain: ``create_media_buy → _check → _build_targeting → POST /flight``
    must put resolved ``siteIds`` into the Kevel flight payload sent on the wire.

    Pre-#1314 BLOCKER-02 (per audit): all 28 unit tests mocked the resolver
    directly. There was no test where the resolver could fire, the adapter
    could call ``_build_targeting``, and the resulting Kevel flight payload
    body could be inspected. Removing the ``siteIds`` write at
    ``kevel.py:228-236`` would have left every test green. Konstantine's
    P14 + P23 invariants apply directly: the PR's stated purpose
    ("compile property_list to native siteIds") needs ONE test that fails
    when the compilation is reverted.

    This test boots a non-dry-run adapter, mocks ``requests.post`` /
    ``requests.get`` at the adapter boundary, mocks
    ``KevelSiteResolver.resolve`` at the resolver boundary, runs the full
    ``create_media_buy`` path, and asserts the captured POST body to
    ``/v1/flight`` contains the expected ``siteIds`` list.

    Pin: revert ``kevel.py:228-236`` (the ``_build_targeting`` siteIds write)
    and ``test_flight_payload_contains_resolved_site_ids`` must fail with
    ``"siteIds" not in payload``.
    """

    def _build_request(self):
        """Build a minimal CreateMediaBuyRequest + MediaPackage with property_list."""
        from datetime import UTC, datetime, timedelta

        from src.core.schemas import CreateMediaBuyRequest, MediaPackage, Targeting

        ref = PropertyListReference(agent_url="https://gov.example/lists", list_id="cb_news_v1")
        targeting = Targeting(property_list=ref)
        package = MediaPackage(
            package_id="pkg_wire_test",
            name="Wire Test Package",
            delivery_type="non_guaranteed",
            cpm=10.0,
            impressions=100_000,
            format_ids=[],
            targeting_overlay=targeting,
        )
        start = datetime.now(UTC) + timedelta(days=1)
        end = start + timedelta(days=30)
        request = CreateMediaBuyRequest(
            brand={"domain": "wiretest.example.com"},
            packages=[],  # adapter takes packages separately
            start_time=start.isoformat(),
            end_time=end.isoformat(),
            po_number="WIRE-TEST-001",
        )
        return request, package

    def test_flight_payload_contains_resolved_site_ids(self):
        """End-to-end pin: resolver siteIds end up in the Kevel flight POST body.

        Mocks the HTTP boundary (``requests.post`` / ``requests.get``) so no
        real Kevel API call fires, and mocks ``KevelSiteResolver.resolve``
        to return a controlled siteIds set. The body captured at
        ``requests.post(.../flight, json=...)`` is the wire shape Kevel
        would receive — asserting on it is asserting on the contract this
        PR exists to deliver.
        """
        from datetime import UTC, datetime, timedelta

        # Use a numeric advertiser_id so the Kevel campaign payload's
        # int(self.advertiser_id) cast succeeds.
        principal = Principal(
            principal_id="p_wire_test",
            name="Wire Test Principal",
            platform_mappings={"kevel": {"advertiser_id": "42424242"}},
        )
        adapter = Kevel(
            config={"network_id": "100", "api_key": "test-key"},
            principal=principal,
            dry_run=False,
            tenant_id="t_test",
        )
        request, package = self._build_request()
        start = datetime.now(UTC) + timedelta(days=1)
        end = start + timedelta(days=30)

        # Mock requests.post: campaign create returns Id=555, flight create returns Id=999
        mock_campaign_response = MagicMock()
        mock_campaign_response.json.return_value = {"Id": 555}
        mock_campaign_response.raise_for_status.return_value = None
        mock_flight_response = MagicMock()
        mock_flight_response.json.return_value = {"Id": 999}
        mock_flight_response.raise_for_status.return_value = None

        with (
            patch.object(adapter, "_site_resolver") as mock_resolver,
            patch("src.adapters.kevel.requests.post") as mock_post,
        ):
            mock_resolver.resolve.return_value = ResolvedSiteIds(
                site_ids={42, 99, 7}, unsupported_types=set(), unresolvable_values=[]
            )
            # First post = /campaign, subsequent posts = /flight per package
            mock_post.side_effect = [mock_campaign_response, mock_flight_response]

            response = adapter.create_media_buy(request, [package], start, end)

        # Find the /flight POST call and inspect its json= payload
        flight_calls = [
            c for c in mock_post.call_args_list if "flight" in (c.args[0] if c.args else c.kwargs.get("url", ""))
        ]
        assert len(flight_calls) == 1, (
            f"Expected exactly one POST to /flight; got {len(flight_calls)} flight calls "
            f"out of {len(mock_post.call_args_list)} total POSTs"
        )
        flight_payload = flight_calls[0].kwargs["json"]
        assert "siteIds" in flight_payload, (
            "Flight payload missing siteIds — _build_targeting did not write the resolved set. "
            "This is the pin: removing kevel.py's _build_targeting siteIds write breaks this test."
        )
        assert flight_payload["siteIds"] == [
            7,
            42,
            99,
        ], f"siteIds in flight payload must be sorted resolver output {{7, 42, 99}}; got {flight_payload['siteIds']!r}"

        # Sanity: the response wasn't an error envelope.
        assert not isinstance(response, CreateMediaBuyError), (
            f"Expected success after wiring resolved siteIds; got error: "
            f"{[err.message for err in (response.errors if hasattr(response, 'errors') else [])]}"
        )
