"""Tests for Kevel adapter's property_list compilation (B3).

Backs the AdCP honest-declaration contract item that "an enabled adapter MUST
translate property_list" — Kevel is the first salesagent adapter to satisfy
that side of the contract (vs B4's UNSUPPORTED_FEATURE raises for adapters
that can't).

These tests pin the three observable behaviors:

1. Kevel advertises ``supports_property_list_targeting=True`` so
   ``get_adcp_capabilities`` flips ``property_list_filtering`` to True for
   Kevel-configured tenants.
2. Identifier-type validation at ``_raise_if_property_list_uncompilable``
   rejects property lists whose contents Kevel cannot compile.
3. ``_build_targeting`` writes the resolved Kevel ``siteIds`` into the
   native targeting payload.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from adcp.types import PropertyListReference

from src.adapters.base import AdServerAdapter
from src.adapters.kevel import Kevel
from src.core.exceptions import AdCPAdapterError, AdCPCapabilityNotSupportedError, AdCPPackageNotFoundError
from src.core.schemas import CreateMediaBuyError, CreateMediaBuyRequest, MediaPackage, Principal, Targeting
from src.services.kevel_site_resolver import ResolvedSiteIds
from tests.helpers.adcp_factories import create_test_identifier

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


class TestSupportsPropertyListTargetingFlag:
    """Class attribute drives the capability declaration."""

    def test_kevel_class_advertises_native_support(self):
        assert Kevel.supports_property_list_targeting is True


class TestRaiseIfUncompilableAcceptsCompilable:
    """All-domain/subdomain lists pass pre-flight without raising."""

    def test_no_raise_when_all_identifiers_are_domain(self):
        adapter = _kevel()
        with patch.object(adapter, "_site_resolver") as mock_resolver:
            mock_resolver.resolve.return_value = ResolvedSiteIds(
                site_ids={42, 99}, unsupported_types=set(), unresolvable_values=[]
            )
            assert adapter._raise_if_property_list_uncompilable([_package_with_ref(_ref())]) is None

    def test_no_raise_when_zero_sites_match(self):
        """Zero-match must be accept-with-context — the buy is created with empty siteIds rather than rejected."""
        adapter = _kevel()
        with patch.object(adapter, "_site_resolver") as mock_resolver:
            mock_resolver.resolve.return_value = ResolvedSiteIds(
                site_ids=set(), unsupported_types=set(), unresolvable_values=["never-onboarded.example"]
            )
            # Zero-match must not reject; downstream handles inventory_list_no_match.
            assert adapter._raise_if_property_list_uncompilable([_package_with_ref(_ref())]) is None

    def test_no_property_list_skipped(self):
        adapter = _kevel()
        pkg = MagicMock(spec=MediaPackage)
        pkg.targeting_overlay = MagicMock()
        pkg.targeting_overlay.property_list = None
        assert adapter._raise_if_property_list_uncompilable([pkg]) is None


class TestRaiseIfUncompilableRejectsIncompatible:
    """Lists containing identifier types Kevel can't compile must raise UNSUPPORTED_FEATURE.

    These are direct adapter-method unit tests (no wire), so asserting on the
    raised ``AdCPCapabilityNotSupportedError`` is appropriate per the Error
    Verification Policy. The buyer-facing wire-envelope shape for an
    ``AdCPCapabilityNotSupportedError`` is covered across REST/A2A/MCP by the
    _impl-boundary wire tests; Kevel's per-type raise serializes through the
    same path.
    """

    def test_raises_when_list_contains_unsupported_types(self):
        adapter = _kevel()
        with patch.object(adapter, "_site_resolver") as mock_resolver:
            mock_resolver.resolve.return_value = ResolvedSiteIds(
                site_ids=set(), unsupported_types={"ios_bundle", "podcast_guid"}, unresolvable_values=[]
            )
            with pytest.raises(AdCPCapabilityNotSupportedError) as exc_info:
                adapter._raise_if_property_list_uncompilable([_package_with_ref(_ref())])

        assert exc_info.value.error_code == "UNSUPPORTED_FEATURE"
        assert "ios_bundle" in exc_info.value.message
        assert "podcast_guid" in exc_info.value.message

    def test_dry_run_still_rejects_unsupported_identifier_types(self):
        """Dry-run mode must still validate identifier types.

        ``_resolve_property_list`` runs the typed-identifier fetch (no Kevel
        HTTP) even in dry-run, populates ``unsupported_types``, and
        ``_raise_if_property_list_uncompilable`` raises UNSUPPORTED_FEATURE.
        Otherwise dry-run would silently accept ``ios_bundle``-only lists
        even though the live path rejects them — test-mode would give a
        false-green for buyers who would fail in prod.
        """
        adapter = _kevel(dry_run=True)
        with patch(
            "src.adapters.kevel.resolve_property_list_typed_sync",
            return_value=[
                create_test_identifier("com.example.app", type_="ios_bundle"),
                create_test_identifier("abc-123", type_="podcast_guid"),
            ],
        ):
            with pytest.raises(AdCPCapabilityNotSupportedError) as exc_info:
                adapter._raise_if_property_list_uncompilable([_package_with_ref(_ref())])

        assert exc_info.value.error_code == "UNSUPPORTED_FEATURE"
        assert "ios_bundle" in exc_info.value.message
        assert "podcast_guid" in exc_info.value.message

    def test_dry_run_with_supported_types_passes_validation(self):
        """Dry-run + only domain/subdomain identifiers → pre-flight passes (no rejection)."""
        adapter = _kevel(dry_run=True)
        with patch(
            "src.adapters.kevel.resolve_property_list_typed_sync",
            return_value=[create_test_identifier("espn.com")],
        ):
            assert adapter._raise_if_property_list_uncompilable([_package_with_ref(_ref())]) is None

    def test_field_index_points_to_offending_package(self):
        """Multi-package: the rejection's field identifies the offending package, not always packages[0]."""
        adapter = _kevel()
        pkg0 = MagicMock(spec=MediaPackage)
        pkg0.package_id = "pkg0"
        pkg0.targeting_overlay = MagicMock()
        pkg0.targeting_overlay.property_list = None  # no property_list → skipped
        pkg1 = _package_with_ref(_ref())

        with patch.object(adapter, "_site_resolver") as mock_resolver:
            mock_resolver.resolve.return_value = ResolvedSiteIds(
                site_ids=set(), unsupported_types={"ios_bundle"}, unresolvable_values=[]
            )
            with pytest.raises(AdCPCapabilityNotSupportedError) as exc_info:
                adapter._raise_if_property_list_uncompilable([pkg0, pkg1])

        assert exc_info.value.field == "packages[1].targeting_overlay.property_list", (
            f"field must identify the offending package index; got {exc_info.value.field!r}"
        )


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
        ``_raise_if_property_list_uncompilable`` and again in ``_build_targeting``.

        ``_resolve_property_list`` memoizes by ``(agent_url, list_id)`` on
        the adapter instance, so the underlying ``KevelSiteResolver.resolve``
        is called exactly once even though both lifecycle methods need the
        result. Without memoization the resolver fires twice (the cache
        makes the second call free in HTTP terms, but the redundant walk
        through identifiers and cache-lookup contention are avoidable — and
        the duplicate calls would survive a future refactor that removed
        the underlying cache).

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

            adapter._raise_if_property_list_uncompilable([package])
            build_result = adapter._build_targeting(overlay)

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

        ``_build_targeting`` calls ``_resolve_property_list`` which returns
        an empty ``site_ids`` set in dry-run, and the empty list is written
        to the Kevel targeting payload. Zero-match is accept-with-context,
        not a no-op — the buy is created with ``siteIds=[]`` and the
        downstream ``inventory_list_no_match`` contract surfaces it.
        """
        adapter = _kevel(dry_run=True)
        with patch(
            "src.adapters.kevel.resolve_property_list_typed_sync",
            return_value=[create_test_identifier("espn.com")],
        ):
            result = adapter._build_targeting(self._make_overlay(_ref()))

        assert result.get("siteIds") == [], (
            "Dry-run must write siteIds=[] (accept-with-empty), "
            f"not skip the key entirely; got {result.get('siteIds')!r}"
        )


class TestEndToEndWiringSiteIdsLandInFlightPayload:
    """The full chain: ``create_media_buy → _check → _build_targeting → POST /flight``
    must put resolved ``siteIds`` into the Kevel flight payload sent on the wire.

    Sibling unit tests mock ``KevelSiteResolver.resolve`` at the adapter
    boundary, which proves the branching logic but does not prove that the
    resolved set actually lands in the HTTP body Kevel receives. Removing
    the ``siteIds`` write in ``_build_targeting`` would leave those tests
    green. This class fills that gap by mocking only the HTTP boundary
    (``requests.post`` / ``requests.get``) so the resolver-to-payload
    plumbing is exercised end-to-end.

    This test boots a non-dry-run adapter, mocks ``requests.post`` /
    ``requests.get`` at the adapter boundary, mocks
    ``KevelSiteResolver.resolve`` at the resolver boundary, runs the full
    ``create_media_buy`` path, and asserts the captured POST body to
    ``/v1/flight`` contains the expected ``siteIds`` list.

    Pin: reverting the ``_build_targeting`` siteIds write makes
    ``test_flight_payload_contains_resolved_site_ids`` fail with
    ``"siteIds" not in payload``.
    """

    def _build_request(self):
        """Build a minimal CreateMediaBuyRequest + MediaPackage with property_list."""
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
            idempotency_key=f"kevel-compile-{uuid.uuid4().hex}",
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
        would receive — asserting on it pins the contract that resolved
        ``siteIds`` actually land in the native targeting payload.
        """
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


class TestUpdateTargetingRecompile:
    """E3 update parity: update-side recompile mirrors create's compile path.

    Create runs type-gate → compile → write targeting into the flight payload.
    Update must do the same: ``validate_targeting_update`` is the pre-write
    type gate, ``update_package_targeting`` recompiles and pushes the flight.
    Without this pair, an updated property_list persists in package_config
    while the live flight keeps serving the old siteIds.
    """

    def test_validate_targeting_update_runs_type_gate(self):
        adapter = _kevel()
        with patch.object(adapter, "_site_resolver") as mock_resolver:
            mock_resolver.resolve.return_value = ResolvedSiteIds(
                site_ids=set(), unsupported_types={"ios_bundle"}, unresolvable_values=[]
            )
            with pytest.raises(AdCPCapabilityNotSupportedError) as excinfo:
                adapter.validate_targeting_update([_package_with_ref(_ref())])
        assert excinfo.value.field == "packages[0].targeting_overlay.property_list"

    def test_dry_run_logs_recompiled_site_ids(self):
        adapter = _kevel(dry_run=True)
        overlay = Targeting(property_list=_ref())
        with patch.object(adapter, "_site_resolver") as mock_resolver:
            mock_resolver.resolve.return_value = ResolvedSiteIds(
                site_ids={42, 99}, unsupported_types=set(), unresolvable_values=[]
            )
            with patch.object(adapter, "log") as mock_log:
                adapter.update_package_targeting("kevel_77", "flight_9", overlay, datetime.now(UTC))
        logged = " ".join(str(c.args[0]) for c in mock_log.call_args_list)
        assert "PUT" in logged and "flight_9" in logged
        assert "42" in logged and "99" in logged

    def test_real_mode_puts_recompiled_targeting_to_resolved_flight_id(self):
        # The AdCP package id ("flight_9") is the Kevel flight *Name*, not its
        # numeric *Id* (555). The recompiled overlay must land on /flight/555 —
        # PUTting /flight/flight_9 404s and the flight keeps serving stale siteIds.
        adapter = _kevel()
        overlay = Targeting(property_list=_ref())
        with patch.object(adapter, "_site_resolver") as mock_resolver:
            mock_resolver.resolve.return_value = ResolvedSiteIds(
                site_ids={42, 99}, unsupported_types=set(), unresolvable_values=[]
            )
            with patch("src.adapters.kevel.requests.get") as mock_get:
                mock_get.return_value.raise_for_status = MagicMock()
                mock_get.return_value.json.return_value = {"items": [{"Name": "flight_9", "Id": 555}]}
                with patch("src.adapters.kevel.requests.put") as mock_put:
                    mock_put.return_value.raise_for_status = MagicMock()
                    adapter.update_package_targeting("kevel_77", "flight_9", overlay, datetime.now(UTC))
        # Resolve uses the campaign id (kevel_ prefix stripped), not the media_buy_id.
        mock_get.assert_called_once_with(
            f"{adapter.base_url}/flight",
            headers=adapter.headers,
            params={"campaignId": "77"},
        )
        # PUT targets the numeric flight Id, NOT the AdCP package id / Name.
        mock_put.assert_called_once_with(
            f"{adapter.base_url}/flight/555",
            headers=adapter.headers,
            json={"siteIds": [42, 99]},
        )

    def test_real_mode_raises_when_flight_name_absent(self):
        # No flight whose Name matches the package id -> resolve raises instead of
        # PUTting to a bogus URL (the recompiled siteIds must not silently vanish).
        adapter = _kevel()
        overlay = Targeting(property_list=_ref())
        with patch.object(adapter, "_site_resolver") as mock_resolver:
            mock_resolver.resolve.return_value = ResolvedSiteIds(
                site_ids={42, 99}, unsupported_types=set(), unresolvable_values=[]
            )
            with patch("src.adapters.kevel.requests.get") as mock_get:
                mock_get.return_value.raise_for_status = MagicMock()
                mock_get.return_value.json.return_value = {"items": [{"Name": "other_flight", "Id": 555}]}
                with patch("src.adapters.kevel.requests.put") as mock_put:
                    with pytest.raises(AdCPPackageNotFoundError):
                        adapter.update_package_targeting("kevel_77", "flight_9", overlay, datetime.now(UTC))
        mock_put.assert_not_called()


class TestBaseAdapterUpdateTargetingContract:
    """The base seam fails loud: a capable adapter that forgets to implement
    ``update_package_targeting`` rejects instead of silently persisting."""

    def test_base_update_package_targeting_raises(self):
        from src.adapters.base import AdServerAdapter

        with pytest.raises(AdCPCapabilityNotSupportedError):
            AdServerAdapter.update_package_targeting(MagicMock(), "mb_1", "pkg_1", MagicMock(), datetime.now(UTC))

    def test_base_validate_targeting_update_accepts(self):
        from src.adapters.base import AdServerAdapter

        assert AdServerAdapter.validate_targeting_update(MagicMock(), [MagicMock()]) is None


class TestPrewarmTargeting:
    """The off-loop site-index prewarm (PAT-03): warm the cache before the sync compile."""

    def test_base_default_is_noop(self):
        # Adapters without an external targeting index rely on the base no-op; it
        # must neither raise nor require an override.
        assert AdServerAdapter.prewarm_targeting(MagicMock(), [MagicMock()]) is None

    def test_kevel_warms_resolver_cache_for_each_ref(self):
        adapter = _kevel()
        ref = _ref()
        with patch.object(adapter, "_site_resolver") as mock_resolver:
            mock_resolver.resolve.return_value = ResolvedSiteIds(
                site_ids={42}, unsupported_types=set(), unresolvable_values=[]
            )
            adapter.prewarm_targeting([_package_with_ref(ref)])
            mock_resolver.resolve.assert_called_once_with(ref)
        # The per-request cache is now warm, so the later synchronous compile
        # does not re-hit the multi-second /v1/site fetch on the event loop.
        assert adapter._property_list_cache

    def test_kevel_dry_run_is_noop(self):
        # No site resolver in dry-run -> nothing expensive to warm.
        adapter = _kevel(dry_run=True)
        adapter.prewarm_targeting([_package_with_ref(_ref())])
        assert adapter._property_list_cache == {}

    def test_kevel_swallows_resolution_error(self):
        # Best-effort: a prewarm fetch failure must not raise — the real compile
        # path re-resolves and surfaces the error in its normal place.
        adapter = _kevel()
        with patch.object(adapter, "_site_resolver") as mock_resolver:
            mock_resolver.resolve.side_effect = AdCPAdapterError("site index unreachable")
            adapter.prewarm_targeting([_package_with_ref(_ref())])  # must not raise
