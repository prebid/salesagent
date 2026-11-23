"""Unit tests for device-type breakdown helpers (issue #1376).

Covers:
- ``_apply_breakdown_limit`` shared helper (DRY extraction)
- ``_build_device_type_breakdown`` synthesised and adapter-supplied paths
- ``_build_geo_breakdown`` tuple-return contract (regression guard)
- ``DeviceTypeBreakdown`` schema inheritance and field contract
- ``AdapterPackageDelivery.by_device_type`` pass-through contract
"""

from unittest.mock import MagicMock

from src.adapters.mock_ad_server import simulate_breakdowns
from src.core.schemas.delivery import (
    DeviceTypeBreakdown,
    GeoBreakdown,
    PackageDelivery,
)
from src.core.tools.media_buy_delivery import (
    _apply_breakdown_limit,
    _build_device_type_breakdown,
    _build_geo_breakdown,
    _build_placement_breakdown,
)


def _device_type_value(entry) -> str:
    """Return the string value of a device_type field regardless of enum vs str."""
    dt = entry.device_type
    return dt.value if hasattr(dt, "value") else str(dt)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_req(*, geo=None, device_type=None):
    """Build a minimal GetMediaBuyDeliveryRequest mock with reporting_dimensions."""
    req = MagicMock()
    dims = MagicMock()
    dims.geo = geo
    dims.device_type = device_type
    req.reporting_dimensions = dims
    return req


def _make_dim(*, limit=None, sort_by=None):
    """Build a reporting dimension mock with an optional limit and sort_by."""
    dim = MagicMock()
    dim.limit = limit
    dim.sort_by = sort_by
    return dim


# ---------------------------------------------------------------------------
# _apply_breakdown_limit
# ---------------------------------------------------------------------------


def _make_dt_entry(device_type: str, impressions: float, spend: float) -> DeviceTypeBreakdown:
    return DeviceTypeBreakdown(device_type=device_type, impressions=impressions, spend=spend)


class TestApplyBreakdownLimit:
    def test_no_limit_returns_all_entries_and_false(self):
        entries = [_make_dt_entry("mobile", 500, 5), _make_dt_entry("desktop", 300, 3)]
        dim = _make_dim(limit=None)
        result, truncated = _apply_breakdown_limit(entries, dim)
        assert len(result) == 2
        assert truncated is False

    def test_limit_larger_than_entries_returns_all_and_false(self):
        entries = [_make_dt_entry("mobile", 500, 5), _make_dt_entry("desktop", 300, 3)]
        dim = _make_dim(limit=5)
        result, truncated = _apply_breakdown_limit(entries, dim)
        assert len(result) == 2
        assert truncated is False

    def test_limit_equal_to_entries_returns_all_and_false(self):
        entries = [
            _make_dt_entry("mobile", 500, 5),
            _make_dt_entry("desktop", 300, 3),
            _make_dt_entry("tablet", 200, 2),
        ]
        dim = _make_dim(limit=3)
        result, truncated = _apply_breakdown_limit(entries, dim)
        assert len(result) == 3
        assert truncated is False

    def test_limit_smaller_than_entries_truncates_and_returns_true(self):
        """Unsorted input: helper must sort by spend desc then keep top-N."""
        entries = [
            _make_dt_entry("tablet", 200, 2),  # lowest spend
            _make_dt_entry("desktop", 300, 3),
            _make_dt_entry("mobile", 500, 5),  # highest spend
            _make_dt_entry("ctv", 100, 1),
        ]
        dim = _make_dim(limit=2)
        result, truncated = _apply_breakdown_limit(entries, dim)
        assert len(result) == 2
        assert truncated is True
        # Survivors must be the top-2 by spend (mobile, desktop)
        device_types = [_device_type_value(e) for e in result]
        assert device_types == ["mobile", "desktop"]

    def test_limit_of_one_returns_top_entry_by_spend(self):
        entries = [
            _make_dt_entry("desktop", 300, 3),
            _make_dt_entry("mobile", 500, 5),
        ]
        dim = _make_dim(limit=1)
        result, truncated = _apply_breakdown_limit(entries, dim)
        assert len(result) == 1
        assert truncated is True
        assert _device_type_value(result[0]) == "mobile"

    def test_sort_by_impressions_reorders_before_truncation(self):
        """sort_by=impressions overrides default spend ordering."""
        entries = [
            _make_dt_entry("desktop", 900, 1),  # high impressions, low spend
            _make_dt_entry("mobile", 100, 10),  # low impressions, high spend
        ]
        mock_sort_by = MagicMock()
        mock_sort_by.value = "impressions"
        dim = _make_dim(limit=1, sort_by=mock_sort_by)
        result, truncated = _apply_breakdown_limit(entries, dim)
        assert len(result) == 1
        assert truncated is True
        assert _device_type_value(result[0]) == "desktop"  # highest impressions wins

    def test_unknown_sort_metric_falls_back_to_spend(self):
        """Unrecognised sort_by metric falls back to spend."""
        entries = [
            _make_dt_entry("tablet", 200, 2),
            _make_dt_entry("mobile", 500, 5),
        ]
        mock_sort_by = MagicMock()
        mock_sort_by.value = "conversions"  # not in _BREAKDOWN_SORTABLE_METRICS
        dim = _make_dim(limit=1, sort_by=mock_sort_by)
        result, _ = _apply_breakdown_limit(entries, dim)
        assert _device_type_value(result[0]) == "mobile"  # highest spend wins

    def test_dim_without_limit_attr_returns_all_sorted(self):
        """Dims that have no ``limit`` attribute at all (getattr fallback)."""
        entries = [
            _make_dt_entry("tablet", 200, 2),
            _make_dt_entry("mobile", 500, 5),
        ]
        dim = object()  # no .limit or .sort_by attribute
        result, truncated = _apply_breakdown_limit(entries, dim)
        assert len(result) == 2
        assert truncated is False
        # Still sorted by spend desc
        assert _device_type_value(result[0]) == "mobile"


# ---------------------------------------------------------------------------
# _build_device_type_breakdown — dimension absent
# ---------------------------------------------------------------------------


class TestBuildDeviceTypeBreakdownNoDimension:
    def test_returns_none_none_when_no_reporting_dimensions(self):
        req = MagicMock()
        req.reporting_dimensions = None
        result, truncated = _build_device_type_breakdown(req, 1000, 5.0)
        assert result is None
        assert truncated is None

    def test_returns_none_none_when_device_type_dim_is_none(self):
        req = _make_req(device_type=None)
        result, truncated = _build_device_type_breakdown(req, 1000, 5.0)
        assert result is None
        assert truncated is None


# ---------------------------------------------------------------------------
# _build_device_type_breakdown — no adapter data (omit path)
# ---------------------------------------------------------------------------


class TestBuildDeviceTypeBreakdownNoAdapterData:
    def _req_with_dim(self, limit=None):
        dim = _make_dim(limit=limit)
        return _make_req(device_type=dim), dim

    def test_returns_none_when_no_adapter_data(self):
        """No adapter data — dimension omitted (None) rather than empty array.

        An empty array would assert a complete zero-row breakdown for a package
        that delivered impressions, contradicting the spec invariant that rows
        should sum to the package total.
        """
        req, _ = self._req_with_dim()
        entries, truncated = _build_device_type_breakdown(req, 1000.0, 10.0)
        assert entries is None
        assert truncated is None

    def test_returns_none_regardless_of_package_totals(self):
        req, _ = self._req_with_dim()
        entries, truncated = _build_device_type_breakdown(req, 0, 0)
        assert entries is None
        assert truncated is None

    def test_none_package_metrics_still_returns_none(self):
        req, _ = self._req_with_dim()
        entries, truncated = _build_device_type_breakdown(req, None, None)
        assert entries is None
        assert truncated is None

    def test_limit_with_no_adapter_data_still_returns_none(self):
        """Limit is irrelevant when there is no adapter data — dimension omitted."""
        req, _ = self._req_with_dim(limit=2)
        entries, truncated = _build_device_type_breakdown(req, 1000.0, 10.0)
        assert entries is None
        assert truncated is None


# ---------------------------------------------------------------------------
# _build_device_type_breakdown — adapter-supplied path
# ---------------------------------------------------------------------------


class TestBuildDeviceTypeBreakdownAdapterSupplied:
    def _req_with_dim(self, limit=None):
        dim = _make_dim(limit=limit)
        return _make_req(device_type=dim)

    def test_uses_adapter_data_when_provided(self):
        """Adapter data is used; entries are sorted by spend desc."""
        req = self._req_with_dim()
        raw = [
            {"device_type": "mobile", "impressions": 200.0, "spend": 2.0},
            {"device_type": "ctv", "impressions": 800.0, "spend": 8.0},
        ]
        entries, truncated = _build_device_type_breakdown(req, 1000.0, 10.0, raw_device_type=raw)
        assert len(entries) == 2
        # Sorted by spend desc: ctv (8.0) before mobile (2.0)
        assert _device_type_value(entries[0]) == "ctv"
        assert entries[0].impressions == 800.0
        assert truncated is False

    def test_adapter_data_overrides_synthesised_split(self):
        """When raw_device_type is supplied, the synthesised 3-way split is NOT used."""
        req = self._req_with_dim()
        raw = [{"device_type": "dooh", "impressions": 1000.0, "spend": 10.0}]
        entries, _ = _build_device_type_breakdown(req, 1000.0, 10.0, raw_device_type=raw)
        assert len(entries) == 1
        assert _device_type_value(entries[0]) == "dooh"

    def test_adapter_data_with_limit_keeps_top_by_spend(self):
        """Unsorted adapter input: sort by spend desc then truncate to limit."""
        req = self._req_with_dim(limit=1)
        raw = [
            {"device_type": "desktop", "impressions": 400.0, "spend": 4.0},
            {"device_type": "mobile", "impressions": 600.0, "spend": 6.0},
        ]
        entries, truncated = _build_device_type_breakdown(req, 1000.0, 10.0, raw_device_type=raw)
        assert len(entries) == 1
        assert truncated is True
        # mobile has higher spend — must be the survivor
        assert _device_type_value(entries[0]) == "mobile"

    def test_rows_sum_to_package_total(self):
        """Adapter-supplied rows should sum to the package total (spec invariant)."""
        req = self._req_with_dim()
        imp, spd = 1000.0, 10.0
        raw = [
            {"device_type": "mobile", "impressions": imp * 0.50, "spend": spd * 0.50},
            {"device_type": "desktop", "impressions": imp * 0.35, "spend": spd * 0.35},
            {"device_type": "tablet", "impressions": imp * 0.15, "spend": spd * 0.15},
        ]
        entries, _ = _build_device_type_breakdown(req, imp, spd, raw_device_type=raw)
        assert entries is not None
        total_imp = sum(e.impressions for e in entries)
        total_spd = sum(e.spend for e in entries)
        assert abs(total_imp - imp) < 0.01
        assert abs(total_spd - spd) < 0.01

    def test_empty_raw_list_omits_dimension(self):
        """Empty list is falsy — dimension omitted (None) rather than empty array."""
        req = self._req_with_dim()
        entries, truncated = _build_device_type_breakdown(req, 1000.0, 10.0, raw_device_type=[])
        assert entries is None
        assert truncated is None


# ---------------------------------------------------------------------------
# _build_geo_breakdown — tuple-return regression guard
# ---------------------------------------------------------------------------


class TestBuildGeoBreakdownTupleReturn:
    """Guard that _build_geo_breakdown still returns (list|None, bool|None)."""

    def test_returns_none_none_when_no_geo_dim(self):
        req = _make_req(geo=None)
        result, truncated = _build_geo_breakdown(req, 1000, 5.0)
        assert result is None
        assert truncated is None

    def test_returns_list_and_bool_when_geo_dim_present(self):
        geo_dim = MagicMock()
        geo_dim.geo_level = "country"
        geo_dim.system = None
        geo_dim.limit = None
        req = _make_req(geo=geo_dim)
        result, truncated = _build_geo_breakdown(req, 1000, 5.0)
        assert isinstance(result, list)
        assert len(result) == 1
        assert isinstance(result[0], GeoBreakdown)
        assert truncated is False

    def test_geo_breakdown_truncated_when_limit_applied(self):
        geo_dim = MagicMock()
        geo_dim.geo_level = "country"
        geo_dim.system = None
        geo_dim.limit = 0  # limit=0 → all entries cut → truncated=True
        req = _make_req(geo=geo_dim)
        result, truncated = _build_geo_breakdown(req, 1000, 5.0)
        assert result == []
        assert truncated is True


# ---------------------------------------------------------------------------
# DeviceTypeBreakdown schema
# ---------------------------------------------------------------------------


class TestDeviceTypeBreakdownSchema:
    def test_inherits_from_library_by_device_type_item(self):
        from adcp.types.domains.media_buy.get_media_buy_delivery_response import (
            ByDeviceTypeItem as LibraryByDeviceTypeItem,
        )

        assert issubclass(DeviceTypeBreakdown, LibraryByDeviceTypeItem)

    def test_construction_with_device_type_and_metrics(self):
        obj = DeviceTypeBreakdown(device_type="mobile", impressions=500.0, spend=2.5)
        assert _device_type_value(obj) == "mobile"
        assert obj.impressions == 500.0
        assert obj.spend == 2.5

    def test_round_trip_serialization(self):
        obj = DeviceTypeBreakdown(device_type="desktop", impressions=350.0, spend=1.75)
        dumped = obj.model_dump()
        reconstructed = DeviceTypeBreakdown(**dumped)
        assert reconstructed.device_type == obj.device_type
        assert reconstructed.impressions == obj.impressions

    def test_all_device_types_accepted(self):
        for dt in ("desktop", "mobile", "tablet", "ctv", "dooh", "unknown"):
            obj = DeviceTypeBreakdown(device_type=dt, impressions=0.0, spend=0.0)
            assert _device_type_value(obj) == dt


# ---------------------------------------------------------------------------
# PackageDelivery — new fields present and default to None
# ---------------------------------------------------------------------------


class TestPackageDeliveryNewFields:
    def _make_pkg(self, **kwargs):
        # pricing_model / rate / currency are `required` on the pinned by-package item, so
        # they belong in the baseline every case here builds on rather than in each case.
        defaults = {
            "package_id": "pkg_1",
            "impressions": 1000,
            "spend": 5.0,
            "pricing_model": "cpm",
            "rate": 5.0,
            "currency": "USD",
        }
        defaults.update(kwargs)
        return PackageDelivery(**defaults)

    def test_by_geo_truncated_defaults_to_none(self):
        pkg = self._make_pkg()
        assert pkg.by_geo_truncated is None

    def test_by_device_type_defaults_to_none(self):
        pkg = self._make_pkg()
        assert pkg.by_device_type is None

    def test_by_device_type_truncated_defaults_to_none(self):
        pkg = self._make_pkg()
        assert pkg.by_device_type_truncated is None

    def test_by_device_type_accepts_breakdown_list(self):
        entries = [DeviceTypeBreakdown(device_type="mobile", impressions=500.0, spend=2.5)]
        pkg = self._make_pkg(by_device_type=entries, by_device_type_truncated=False)
        assert len(pkg.by_device_type) == 1
        assert pkg.by_device_type_truncated is False

    def test_by_geo_truncated_true_when_geo_was_cut(self):
        pkg = self._make_pkg(by_geo_truncated=True)
        assert pkg.by_geo_truncated is True

    def test_round_trip_with_device_type_breakdown(self):
        entries = [DeviceTypeBreakdown(device_type="ctv", impressions=100.0, spend=1.0)]
        pkg = self._make_pkg(
            by_device_type=entries,
            by_device_type_truncated=False,
            by_geo_truncated=True,
        )
        dumped = pkg.model_dump()
        assert dumped["by_device_type_truncated"] is False
        assert dumped["by_geo_truncated"] is True

    def test_by_placement_truncated_defaults_to_none(self):
        pkg = self._make_pkg()
        assert pkg.by_placement_truncated is None

    def test_by_placement_truncated_true_when_placement_was_cut(self):
        pkg = self._make_pkg(by_placement_truncated=True)
        assert pkg.by_placement_truncated is True

    def test_by_placement_truncated_false_when_complete(self):
        pkg = self._make_pkg(by_placement_truncated=False)
        assert pkg.by_placement_truncated is False


# ---------------------------------------------------------------------------
# _build_placement_breakdown — truncation flag surfaced
# ---------------------------------------------------------------------------


class TestSimulateBreakdowns:
    """The mock adapter's three breakdown splits -- previously graded by NOTHING.

    Measured before adding this: ``git grep simulate_breakdowns -- tests/`` returned only
    the harness's import and call. So the geo and device_type splits every delivery
    scenario sorts were ungraded, and moving the placement split here from
    ``_build_placement_breakdown`` would have made a third ungraded one.

    The ORDER is what matters and is asserted, not just the row count. These rows exist so
    the delivery tool's sort and truncation logic has non-trivial input; a split whose
    weights stopped descending would still produce three rows and would silently make
    every ``sort_by`` assertion downstream weaker. Reversing the weights is the probe this
    is built to fail.
    """

    def test_all_three_splits_are_returned(self):
        geo, device_type, placement = simulate_breakdowns(1000.0, 50.0)
        assert [len(geo), len(device_type), len(placement)] == [10, 3, 3]

    def test_each_split_is_ordered_descending_by_impressions(self):
        """Distinct, descending weights are the property the sort tests rely on."""
        for name, rows in zip(("geo", "device_type", "placement"), simulate_breakdowns(1000.0, 50.0), strict=True):
            impressions = [r["impressions"] for r in rows]
            assert impressions == sorted(impressions, reverse=True), f"{name} split is not descending: {impressions}"
            assert len(set(impressions)) == len(impressions), f"{name} split has tied weights: {impressions}"

    def test_each_split_reconciles_to_the_package_totals(self):
        """``by_*_truncated=false`` asserts the rows account for the package, so they must."""
        geo, device_type, placement = simulate_breakdowns(1000.0, 50.0)
        for name, rows in (("geo", geo), ("device_type", device_type), ("placement", placement)):
            assert round(sum(r["impressions"] for r in rows), 6) == 1000.0, f"{name} impressions do not reconcile"
            assert round(sum(r["spend"] for r in rows), 6) == 50.0, f"{name} spend does not reconcile"

    def test_the_placement_split_carries_clicks_so_sort_by_clicks_is_substantive(self):
        """A ``sort_by=clicks`` request must have a real ordering, not an all-null sort.

        This is the one reason the placement split exists at all, and it is the reason it
        used to be synthesized in the production path.
        """
        _, _, placement = simulate_breakdowns(1000.0, 50.0)
        clicks = [r["clicks"] for r in placement]
        assert all(c is not None for c in clicks)
        assert clicks == sorted(clicks, reverse=True)
        assert clicks[0] == round(1000.0 * 0.5 * 0.01, 4)


def _make_placement_dim(*, limit=None, sort_by=None):
    """Build a placement dimension mock with an optional limit and sort_by."""
    dim = MagicMock()
    dim.limit = limit
    dim.sort_by = sort_by
    return dim


class TestBuildPlacementBreakdownTruncation:
    """Guard that _build_placement_breakdown returns (list|None, bool|None) and
    surfaces by_placement_truncated correctly (spec MUST field)."""

    def test_returns_none_none_when_no_placement_dim(self):
        """No placement dimension requested → (None, None)."""
        result, truncated = _build_placement_breakdown(None, None, 1000.0, 5.0, None)
        assert result is None
        assert truncated is None

    def test_returns_false_when_under_limit(self):
        """Fewer placements than limit → truncated=False."""
        dim = _make_placement_dim(limit=10)
        raw = [
            {"placement_id": "plc_a", "impressions": 500.0, "spend": 2.5},
            {"placement_id": "plc_b", "impressions": 300.0, "spend": 1.5},
        ]
        result, truncated = _build_placement_breakdown(dim, raw, 800.0, 4.0, None)
        assert result is not None
        assert len(result) == 2
        assert truncated is False

    def test_returns_true_when_over_limit_keeps_top_by_spend(self):
        """More placements than limit → truncated=True; survivors are top-N by spend."""
        dim = _make_placement_dim(limit=1)
        raw = [
            {"placement_id": "plc_low", "impressions": 200.0, "spend": 1.0},
            {"placement_id": "plc_high", "impressions": 800.0, "spend": 8.0},
        ]
        result, truncated = _build_placement_breakdown(dim, raw, 1000.0, 9.0, None)
        assert result is not None
        assert len(result) == 1
        assert truncated is True
        # plc_high has higher spend — must be the survivor
        assert result[0].placement_id == "plc_high"

    def test_no_adapter_data_invents_no_placements(self):
        """Requested but unreported → omitted, NOT fabricated.

        This replaces a test that asserted the opposite -- three synthesized rows with
        invented ids ``plc_a``/``plc_b``/``plc_c`` and a ``clicks`` value derived from a
        hardcoded 1% CTR. That synthesis existed so a ``sort_by=clicks`` request would
        have a substantive ordering to grade, and it ran in PRODUCTION: a buyer asking a
        GAM tenant for a placement breakdown received three placement identifiers that
        exist in no ad server. The old test asserted that behaviour, so it was deleted
        rather than re-grounded.

        ``(None, None)`` and not ``([], False)``: an empty array with ``truncated=False``
        asserts a complete zero-row breakdown for a package that delivered impressions,
        which is what ``_build_device_type_breakdown`` already refuses to do for the same
        reason. Omitting is what the pin permits -- "unsupported dimensions are silently
        omitted from the response" -- and it stays correct until an adapter supplies rows.
        """
        dim = _make_placement_dim(limit=None)
        result, truncated = _build_placement_breakdown(dim, None, 1000.0, 5.0, None)
        assert result is None, f"no adapter data must invent no placements, got {result}"
        assert truncated is None

    def test_adapter_supplied_rows_are_complete_when_no_limit(self):
        """No limit over reported rows → truncated=False.

        Keeps the coverage the deleted test incidentally had (``limit=None`` is a distinct
        input from the ``limit=10`` case above) while taking its rows from the adapter,
        which is where they belong.
        """
        dim = _make_placement_dim(limit=None)
        raw = [
            {"placement_id": "homepage_atf", "impressions": 600.0, "spend": 3.0},
            {"placement_id": "article_mid", "impressions": 400.0, "spend": 2.0},
        ]
        result, truncated = _build_placement_breakdown(dim, raw, 1000.0, 5.0, None)
        assert result is not None
        assert [e.placement_id for e in result] == ["homepage_atf", "article_mid"]
        assert truncated is False
