"""Behavioral contract tests for delivery schema classes.

These tests pin the runtime shape of every delivery schema class imported
from ``src.core.schemas`` (the public API).  They verify field presence,
round-trip serialization, custom methods, enum completeness, inheritance
chains, and the ``upgrade_legacy_format_ids`` validator.

Written *before* the refactoring that removes duplicate delivery classes
from ``_base.py``.  After the refactoring the same tests must pass,
proving zero behavioral regression.
"""

from datetime import UTC, date, datetime

from src.core.schemas import (
    AdapterGetMediaBuyDeliveryResponse,
    AdapterPackageDelivery,
    AggregatedTotals,
    DailyBreakdown,
    DeliveryMeasurement,
    DeliveryStatus,
    DeliveryTotals,
    DeliveryType,
    GetAllMediaBuyDeliveryRequest,
    GetAllMediaBuyDeliveryResponse,
    GetMediaBuyDeliveryRequest,
    GetMediaBuyDeliveryResponse,
    ListCreativeFormatsRequest,
    MediaBuyDeliveryData,
    PackageDelivery,
    PackageRequest,
    ReportingPeriod,
)
from src.core.schemas.product import ProductFilters

# ---------------------------------------------------------------------------
# Enum completeness
# ---------------------------------------------------------------------------


class TestDeliveryStatusEnum:
    EXPECTED_MEMBERS = {
        "delivering",
        "not_delivering",
        "completed",
        "budget_exhausted",
        "flight_ended",
        "goal_met",
    }

    def test_has_all_six_members(self):
        actual = {m.value for m in DeliveryStatus}
        assert actual == self.EXPECTED_MEMBERS

    def test_is_str_enum(self):
        assert issubclass(DeliveryStatus, str)
        assert DeliveryStatus.delivering == "delivering"


class TestDeliveryTypeEnum:
    def test_has_two_members(self):
        assert {m.value for m in DeliveryType} == {"guaranteed", "non_guaranteed"}

    def test_is_str_enum(self):
        assert issubclass(DeliveryType, str)
        assert DeliveryType.GUARANTEED == "guaranteed"


# ---------------------------------------------------------------------------
# Field presence
# ---------------------------------------------------------------------------


class TestDeliveryTotalsFields:
    EXPECTED_FIELDS = {
        "impressions",
        "spend",
        "clicks",
        "ctr",
        "video_completions",
        "completion_rate",
        "conversions",
        "viewability",
    }

    def test_field_names(self):
        assert set(DeliveryTotals.model_fields.keys()) == self.EXPECTED_FIELDS

    def test_round_trip(self):
        data = {"impressions": 1000, "spend": 5.0, "conversions": 12, "viewability": 0.85}
        obj = DeliveryTotals(**data)
        dumped = obj.model_dump()
        reconstructed = DeliveryTotals(**dumped)
        assert reconstructed.model_dump() == dumped

    def test_minimal_construction(self):
        obj = DeliveryTotals(impressions=0, spend=0)
        assert obj.impressions == 0
        assert obj.conversions is None
        assert obj.viewability is None


class TestPackageDeliveryFields:
    EXPECTED_FIELDS = {
        "package_id",
        "buyer_ref",
        "impressions",
        "spend",
        "clicks",
        "video_completions",
        "pacing_index",
        "pricing_model",
        "rate",
        "currency",
        "by_placement",
    }

    def test_field_names(self):
        assert set(PackageDelivery.model_fields.keys()) == self.EXPECTED_FIELDS

    def test_round_trip(self):
        data = {
            "package_id": "pkg_1",
            "impressions": 500,
            "spend": 2.5,
            "pricing_model": "cpm",
            "rate": 5.0,
            "currency": "USD",
        }
        obj = PackageDelivery(**data)
        dumped = obj.model_dump()
        assert PackageDelivery(**dumped).model_dump() == dumped


class TestDailyBreakdownFields:
    EXPECTED_FIELDS = {"date", "impressions", "spend"}

    def test_field_names(self):
        assert set(DailyBreakdown.model_fields.keys()) == self.EXPECTED_FIELDS

    def test_round_trip(self):
        data = {"date": "2025-01-15", "impressions": 100, "spend": 0.5}
        obj = DailyBreakdown(**data)
        dumped = obj.model_dump()
        assert DailyBreakdown(**dumped).model_dump() == dumped


class TestMediaBuyDeliveryDataFields:
    EXPECTED_FIELDS = {
        "media_buy_id",
        "buyer_ref",
        "status",
        "expected_availability",
        "is_adjusted",
        "pricing_model",
        "pricing_options",
        "totals",
        "by_package",
        "daily_breakdown",
        "ext",
    }

    def test_field_names(self):
        assert set(MediaBuyDeliveryData.model_fields.keys()) == self.EXPECTED_FIELDS

    def test_ext_defaults_to_empty_dict(self):
        obj = MediaBuyDeliveryData(
            media_buy_id="buy_1",
            status="active",
            totals=DeliveryTotals(impressions=0, spend=0),
            by_package=[],
        )
        assert obj.ext == {}

    def test_pricing_options_present(self):
        obj = MediaBuyDeliveryData(
            media_buy_id="buy_1",
            status="active",
            pricing_options=[{"id": "po_1", "model": "cpm"}],
            totals=DeliveryTotals(impressions=0, spend=0),
            by_package=[],
        )
        assert obj.pricing_options == [{"id": "po_1", "model": "cpm"}]


class TestReportingPeriodFields:
    def test_extends_library(self):
        from adcp.types import ReportingPeriod as LibraryReportingPeriod

        assert issubclass(ReportingPeriod, LibraryReportingPeriod)

    def test_construction(self):
        rp = ReportingPeriod(
            start=datetime(2025, 1, 1, tzinfo=UTC),
            end=datetime(2025, 1, 31, tzinfo=UTC),
        )
        assert rp.start.year == 2025


class TestAggregatedTotalsFields:
    def test_extends_library(self):
        from adcp.types import AggregatedTotals as LibraryAggregatedTotals

        assert issubclass(AggregatedTotals, LibraryAggregatedTotals)

    def test_field_names_include_library_fields(self):
        fields = set(AggregatedTotals.model_fields.keys())
        assert "impressions" in fields
        assert "spend" in fields
        assert "media_buy_count" in fields


class TestDeliveryMeasurementFields:
    def test_extends_library(self):
        from adcp.types import DeliveryMeasurement as LibraryDeliveryMeasurement

        assert issubclass(DeliveryMeasurement, LibraryDeliveryMeasurement)

    def test_has_provider_field(self):
        assert "provider" in DeliveryMeasurement.model_fields


class TestGetMediaBuyDeliveryRequestFields:
    EXPECTED_EXTENSION_FIELDS = {
        "account",
        "reporting_dimensions",
        "include_package_daily_breakdown",
        "attribution_window",
    }

    def test_extends_library(self):
        from adcp.types import GetMediaBuyDeliveryRequest as LibraryReq

        assert issubclass(GetMediaBuyDeliveryRequest, LibraryReq)

    def test_extension_fields_present(self):
        fields = set(GetMediaBuyDeliveryRequest.model_fields.keys())
        assert self.EXPECTED_EXTENSION_FIELDS.issubset(fields)


# ---------------------------------------------------------------------------
# Custom methods
# ---------------------------------------------------------------------------


def _make_delivery_response(**overrides):
    """Build a minimal GetMediaBuyDeliveryResponse for testing."""
    defaults = {
        "reporting_period": {
            "start": "2025-01-01T00:00:00Z",
            "end": "2025-01-31T23:59:59Z",
        },
        "currency": "USD",
        "aggregated_totals": {"impressions": 1000, "spend": 5.0, "media_buy_count": 1},
        "media_buy_deliveries": [
            {
                "media_buy_id": "buy_1",
                "status": "active",
                "totals": {"impressions": 1000, "spend": 5.0},
                "by_package": [{"package_id": "pkg_1", "impressions": 1000, "spend": 5.0}],
            }
        ],
    }
    defaults.update(overrides)
    return GetMediaBuyDeliveryResponse(**defaults)


class TestGetMediaBuyDeliveryResponseMethods:
    def test_str_zero_deliveries(self):
        resp = _make_delivery_response(media_buy_deliveries=[])
        assert str(resp) == "No delivery data found for the specified period."

    def test_str_one_delivery(self):
        resp = _make_delivery_response()
        assert str(resp) == "Retrieved delivery data for 1 media buy."

    def test_str_multiple_deliveries(self):
        deliveries = [
            {
                "media_buy_id": f"buy_{i}",
                "status": "active",
                "totals": {"impressions": 100, "spend": 1.0},
                "by_package": [],
            }
            for i in range(3)
        ]
        resp = _make_delivery_response(media_buy_deliveries=deliveries)
        assert str(resp) == "Retrieved delivery data for 3 media buys."

    def test_model_dump_includes_next_expected_at_when_notification_type_set(self):
        resp = _make_delivery_response(notification_type="final")
        dumped = resp.model_dump()
        assert "next_expected_at" in dumped
        assert dumped["next_expected_at"] is None

    def test_model_dump_omits_next_expected_at_when_no_notification_type(self):
        resp = _make_delivery_response()
        dumped = resp.model_dump()
        assert resp.notification_type is None
        assert "next_expected_at" not in dumped

    def test_webhook_payload_excludes_aggregated_totals(self):
        resp = _make_delivery_response()
        payload = resp.webhook_payload()
        assert "aggregated_totals" not in payload
        assert "media_buy_deliveries" in payload

    def test_webhook_payload_filters_metrics(self):
        resp = _make_delivery_response()
        payload = resp.webhook_payload(requested_metrics=["impressions"])
        for delivery in payload["media_buy_deliveries"]:
            totals = delivery["totals"]
            assert "impressions" in totals
            assert "spend" not in totals

    def test_round_trip_serialization(self):
        resp = _make_delivery_response()
        dumped = resp.model_dump()
        reconstructed = GetMediaBuyDeliveryResponse(**dumped)
        assert reconstructed.model_dump() == dumped


# ---------------------------------------------------------------------------
# Adapter schemas
# ---------------------------------------------------------------------------


class TestAdapterPackageDelivery:
    def test_fields(self):
        assert set(AdapterPackageDelivery.model_fields.keys()) == {"package_id", "impressions", "spend", "by_placement"}

    def test_construction(self):
        obj = AdapterPackageDelivery(package_id="pkg_1", impressions=1000, spend=5.0)
        assert obj.package_id == "pkg_1"


class TestAdapterGetMediaBuyDeliveryResponse:
    def test_fields(self):
        expected = {"media_buy_id", "reporting_period", "totals", "by_package", "currency", "daily_breakdown"}
        assert set(AdapterGetMediaBuyDeliveryResponse.model_fields.keys()) == expected

    def test_construction(self):
        obj = AdapterGetMediaBuyDeliveryResponse(
            media_buy_id="buy_1",
            reporting_period=ReportingPeriod(
                start=datetime(2025, 1, 1, tzinfo=UTC),
                end=datetime(2025, 1, 31, tzinfo=UTC),
            ),
            totals=DeliveryTotals(impressions=100, spend=1.0),
            by_package=[AdapterPackageDelivery(package_id="pkg_1", impressions=100, spend=1.0)],
            currency="USD",
        )
        assert obj.media_buy_id == "buy_1"


# ---------------------------------------------------------------------------
# Deprecated schemas
# ---------------------------------------------------------------------------


class TestDeprecatedDeliverySchemas:
    def test_get_all_media_buy_delivery_request(self):
        obj = GetAllMediaBuyDeliveryRequest(today=date(2025, 1, 15))
        assert obj.today == date(2025, 1, 15)
        assert obj.media_buy_ids is None

    def test_get_all_media_buy_delivery_response(self):
        obj = GetAllMediaBuyDeliveryResponse(
            deliveries=[],
            total_spend=0,
            total_impressions=0,
            active_count=0,
            summary_date=date(2025, 1, 15),
        )
        assert obj.active_count == 0


# ---------------------------------------------------------------------------
# upgrade_legacy_format_ids validator
# ---------------------------------------------------------------------------


class TestUpgradeLegacyFormatIds:
    """Tests that the upgrade_legacy_format_ids validator works on all 3 classes."""

    LEGACY_FORMAT_ID = {"agent_url": "https://example.com/agent", "id": "fmt_banner"}

    def test_package_request_upgrades_dict_format_ids(self):
        from src.core.schemas import FormatId

        pkg = PackageRequest(
            budget=1000,
            buyer_ref="ref_1",
            pricing_option_id="po_1",
            product_id="prod_1",
            format_ids=[self.LEGACY_FORMAT_ID],
        )
        assert len(pkg.format_ids) == 1
        assert isinstance(pkg.format_ids[0], FormatId)
        assert pkg.format_ids[0].id == "fmt_banner"

    def test_product_filters_upgrades_dict_format_ids(self):
        from src.core.schemas import FormatId

        filters = ProductFilters(format_ids=[self.LEGACY_FORMAT_ID])
        assert len(filters.format_ids) == 1
        assert isinstance(filters.format_ids[0], FormatId)
        assert filters.format_ids[0].id == "fmt_banner"

    def test_list_creative_formats_request_upgrades_dict_format_ids(self):
        from src.core.schemas import FormatId

        req = ListCreativeFormatsRequest(format_ids=[self.LEGACY_FORMAT_ID])
        assert len(req.format_ids) == 1
        assert isinstance(req.format_ids[0], FormatId)
        assert req.format_ids[0].id == "fmt_banner"

    def test_already_object_format_ids_pass_through(self):
        from src.core.schemas import FormatId

        fmt = FormatId(agent_url="https://example.com/agent", id="fmt_video")
        pkg = PackageRequest(
            budget=1000,
            buyer_ref="ref_1",
            pricing_option_id="po_1",
            product_id="prod_1",
            format_ids=[fmt],
        )
        assert pkg.format_ids[0].id == "fmt_video"
