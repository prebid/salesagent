"""Unit tests for device-type breakdown helpers (issue #1376).

Covers:
- ``_apply_breakdown_limit`` shared helper (DRY extraction)
- ``_build_device_type_breakdown`` synthesised and adapter-supplied paths
- ``_build_geo_breakdown`` tuple-return contract (regression guard)
- ``DeviceTypeBreakdown`` schema inheritance and field contract
- ``AdapterPackageDelivery.by_device_type`` pass-through contract
"""

from unittest.mock import MagicMock

from src.core.schemas.delivery import (
    DeviceTypeBreakdown,
    GeoBreakdown,
    PackageDelivery,
)
from src.core.tools.media_buy_delivery import (
    _apply_breakdown_limit,
    _build_device_type_breakdown,
    _build_geo_breakdown,
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


def _make_dim(*, limit=None):
    """Build a reporting dimension mock with an optional limit."""
    dim = MagicMock()
    dim.limit = limit
    return dim


# ---------------------------------------------------------------------------
# _apply_breakdown_limit
# ---------------------------------------------------------------------------


class TestApplyBreakdownLimit:
    def test_no_limit_returns_all_entries_and_false(self):
        entries = [1, 2, 3]
        dim = _make_dim(limit=None)
        result, truncated = _apply_breakdown_limit(entries, dim)
        assert result == [1, 2, 3]
        assert truncated is False

    def test_limit_larger_than_entries_returns_all_and_false(self):
        entries = [1, 2]
        dim = _make_dim(limit=5)
        result, truncated = _apply_breakdown_limit(entries, dim)
        assert result == [1, 2]
        assert truncated is False

    def test_limit_equal_to_entries_returns_all_and_false(self):
        entries = [1, 2, 3]
        dim = _make_dim(limit=3)
        result, truncated = _apply_breakdown_limit(entries, dim)
        assert result == [1, 2, 3]
        assert truncated is False

    def test_limit_smaller_than_entries_truncates_and_returns_true(self):
        entries = [10, 20, 30, 40]
        dim = _make_dim(limit=2)
        result, truncated = _apply_breakdown_limit(entries, dim)
        assert result == [10, 20]
        assert truncated is True

    def test_limit_of_one_returns_first_entry(self):
        entries = ["a", "b", "c"]
        dim = _make_dim(limit=1)
        result, truncated = _apply_breakdown_limit(entries, dim)
        assert result == ["a"]
        assert truncated is True

    def test_dim_without_limit_attr_returns_all_and_false(self):
        """Dims that have no ``limit`` attribute at all (getattr fallback)."""
        entries = [1, 2]
        dim = object()  # no .limit attribute
        result, truncated = _apply_breakdown_limit(entries, dim)
        assert result == [1, 2]
        assert truncated is False


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
# _build_device_type_breakdown — no adapter data (empty list path)
# ---------------------------------------------------------------------------


class TestBuildDeviceTypeBreakdownNoAdapterData:
    def _req_with_dim(self, limit=None):
        dim = _make_dim(limit=limit)
        return _make_req(device_type=dim), dim

    def test_returns_empty_list_when_no_adapter_data(self):
        """No fabricated split — returns [] so no fake data is emitted."""
        req, _ = self._req_with_dim()
        entries, truncated = _build_device_type_breakdown(req, 1000.0, 10.0)
        assert entries == []
        assert truncated is False

    def test_returns_empty_list_regardless_of_package_totals(self):
        req, _ = self._req_with_dim()
        entries, _ = _build_device_type_breakdown(req, 0, 0)
        assert entries == []

    def test_none_package_metrics_still_returns_empty_list(self):
        req, _ = self._req_with_dim()
        entries, _ = _build_device_type_breakdown(req, None, None)
        assert entries == []

    def test_limit_on_empty_list_returns_empty_and_false(self):
        """Limit applied to empty list — nothing to truncate."""
        req, _ = self._req_with_dim(limit=2)
        entries, truncated = _build_device_type_breakdown(req, 1000.0, 10.0)
        assert entries == []
        assert truncated is False


# ---------------------------------------------------------------------------
# _build_device_type_breakdown — adapter-supplied path
# ---------------------------------------------------------------------------


class TestBuildDeviceTypeBreakdownAdapterSupplied:
    def _req_with_dim(self, limit=None):
        dim = _make_dim(limit=limit)
        return _make_req(device_type=dim)

    def test_uses_adapter_data_when_provided(self):
        req = self._req_with_dim()
        raw = [
            {"device_type": "ctv", "impressions": 800.0, "spend": 8.0},
            {"device_type": "mobile", "impressions": 200.0, "spend": 2.0},
        ]
        entries, truncated = _build_device_type_breakdown(req, 1000.0, 10.0, raw_device_type=raw)
        assert len(entries) == 2
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

    def test_adapter_data_with_limit_truncates(self):
        req = self._req_with_dim(limit=1)
        raw = [
            {"device_type": "mobile", "impressions": 600.0, "spend": 6.0},
            {"device_type": "desktop", "impressions": 400.0, "spend": 4.0},
        ]
        entries, truncated = _build_device_type_breakdown(req, 1000.0, 10.0, raw_device_type=raw)
        assert len(entries) == 1
        assert truncated is True

    def test_empty_raw_list_falls_back_to_empty(self):
        """Empty list is falsy — falls back to empty list (no fabrication)."""
        req = self._req_with_dim()
        entries, _ = _build_device_type_breakdown(req, 1000.0, 10.0, raw_device_type=[])
        assert entries == []


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
        from adcp.types.generated_poc.media_buy.get_media_buy_delivery_response import (
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
        defaults = {"package_id": "pkg_1", "impressions": 1000, "spend": 5.0}
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
