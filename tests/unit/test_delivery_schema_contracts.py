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
    PackageDelivery,
    PackageRequest,
    ReportingPeriod,
)
from tests.factories.media_buy import package_pricing_fields

# ---------------------------------------------------------------------------
# Enum completeness
# ---------------------------------------------------------------------------


class TestDeliveryStatusEnum:
    EXPECTED_MEMBERS = {"delivering", "not_delivering", "completed", "budget_exhausted", "flight_ended", "goal_met"}

    def test_has_all_six_members(self):
        actual = {m.value for m in DeliveryStatus}
        assert actual == self.EXPECTED_MEMBERS

    def test_values_are_strings(self):
        """DeliveryStatus members have string values (adcp library uses plain Enum, not str Enum)."""
        assert all(isinstance(m.value, str) for m in DeliveryStatus)
        assert DeliveryStatus.delivering.value == "delivering"


class TestDeliveryTypeEnum:
    def test_has_two_members(self):
        assert {m.value for m in DeliveryType} == {"guaranteed", "non_guaranteed"}

    def test_is_str_enum(self):
        assert issubclass(DeliveryType, str)
        assert DeliveryType.GUARANTEED == "guaranteed"


# ---------------------------------------------------------------------------
# Field presence
# ---------------------------------------------------------------------------


# The four ``test_field_names`` cases are RETIRED, and the EXPECTED_FIELDS sets with them.
#
# Each asserted that a local model's field set equals a hand-written literal. Those models
# now EXTEND the SDK's (critical pattern #1) instead of redeclaring a subset of it, so the
# set is whatever the pinned schema declares -- 41 fields on DeliveryTotals where the
# literal named 9 -- and re-listing it here only asks whether someone retyped the pin
# correctly. CLAUDE.md rules that out by name: "There is deliberately no suite comparing a
# model's field set to the pinned schema ... a comparison would assert that Python
# inheritance works."
#
# What those sets stood in for is graded where it can fail: the inheritance guard
# (tests/unit/test_architecture_schema_inheritance.py) grades every REDECLARATION against
# its library parent, and the round-trip and construction cases below still exercise values.


class TestDeliveryTotalsFields:
    # ``test_round_trip`` is RETIRED: it hand-built a DeliveryTotals, dumped it and
    # rebuilt it, which grades Pydantic round-tripping rather than any production path.

    def test_minimal_construction(self):
        obj = DeliveryTotals(impressions=0, spend=0)
        assert obj.impressions == 0
        assert obj.conversions is None
        assert obj.viewability is None


class TestPackageDeliveryFields:
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
    def test_round_trip(self):
        data = {"date": "2025-01-15", "impressions": 100, "spend": 0.5}
        obj = DailyBreakdown(**data)
        dumped = obj.model_dump()
        assert DailyBreakdown(**dumped).model_dump() == dumped


# ``TestMediaBuyDeliveryDataFields`` is RETIRED in full, and the MediaBuyDeliveryData
# import with it. ``test_ext_defaults_to_empty_dict`` and ``test_pricing_options_present``
# graded fields the pin does not declare on a media_buy_deliveries item -- its properties
# are media_buy_id, status, totals, by_package, daily_breakdown, windows, pricing_model,
# is_final, is_adjusted, finalized_at, expected_availability -- and both were removed when
# the model started extending the library type (critical pattern #1). The third case only
# echoed its own constructor kwargs back.


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
        "reporting_period": {"start": "2025-01-01T00:00:00Z", "end": "2025-01-31T23:59:59Z"},
        "currency": "USD",
        "aggregated_totals": {"impressions": 1000, "spend": 5.0, "media_buy_count": 1},
        "media_buy_deliveries": [
            {
                "media_buy_id": "buy_1",
                "status": "active",
                "totals": {"impressions": 1000, "spend": 5.0},
                # pricing_model/rate/currency are in the by_package item's `required` set
                # and are non-nullable, so a minimal entry still carries all three.
                "by_package": [{"package_id": "pkg_1", "impressions": 1000, "spend": 5.0, **package_pricing_fields()}],
            }
        ],
    }
    defaults.update(overrides)
    return GetMediaBuyDeliveryResponse(**defaults)


class TestGetMediaBuyDeliveryResponseMethods:
    # next_expected_at is graded against the pin, not against a hand-declared set.
    # get-media-buy-delivery-response.json types it {"type": "string"} — NOT nullable —
    # omits it from `required`, and its description scopes it to "webhook deliveries
    # when notification_type is not 'final'". So null is never a valid wire value, and
    # 'final' is precisely the case that must NOT carry the field. These three tests
    # pin that contract; an earlier pair asserted `next_expected_at: null` for
    # notification_type='final', which the pin forbids on both counts.

    def test_model_dump_omits_next_expected_at_for_final(self):
        """'final' is the one notification_type the pin's description excludes."""
        resp = _make_delivery_response(notification_type="final")
        dumped = resp.model_dump()
        assert "next_expected_at" not in dumped, (
            f"The pin scopes next_expected_at to notification_type != 'final'; emitting it "
            f"for a final report tells the buyer to expect another one. Got: {dumped.get('next_expected_at')!r}"
        )

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
        assert set(AdapterPackageDelivery.model_fields.keys()) == {
            "package_id",
            "impressions",
            "spend",
            "by_placement",
            "by_geo",
            "by_device_type",
        }

    def test_construction(self):
        obj = AdapterPackageDelivery(package_id="pkg_1", impressions=1000, spend=5.0)
        assert obj.package_id == "pkg_1"

    def test_by_device_type_defaults_to_none(self):
        obj = AdapterPackageDelivery(package_id="pkg_1", impressions=0, spend=0.0)
        assert obj.by_device_type is None

    def test_by_device_type_accepts_raw_dicts(self):
        raw = [{"device_type": "mobile", "impressions": 500.0, "spend": 2.5}]
        obj = AdapterPackageDelivery(package_id="pkg_1", impressions=1000, spend=5.0, by_device_type=raw)
        assert obj.by_device_type == raw


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
# format_ids coercion
# ---------------------------------------------------------------------------


class TestFormatIdsCoercion:
    """A dict reaches these three request models as the annotated FormatId type.

    Three sibling cases asserting ``isinstance(..., FormatId)`` against OUR
    subclass were deleted with the ``upgrade_legacy_format_ids`` validator they
    graded. All three fields are annotated ``list[FormatReferenceStructuredObject]``
    -- the library type -- so the subclass they demanded was something the
    annotation never promised, and only a ``mode="before"`` validator overriding
    the annotation made them pass. Pydantic performs the dict coercion unaided.
    """

    def test_dict_format_ids_coerce_to_the_annotated_type(self):
        pkg = PackageRequest(
            budget=1000,
            pricing_option_id="po_1",
            product_id="prod_1",
            format_ids=[{"agent_url": "https://example.com/agent", "id": "fmt_banner"}],
        )
        assert len(pkg.format_ids) == 1
        assert pkg.format_ids[0].id == "fmt_banner"
        assert str(pkg.format_ids[0].agent_url).rstrip("/") == "https://example.com/agent"

    def test_already_object_format_ids_pass_through(self):
        from src.core.schemas import FormatId

        fmt = FormatId(agent_url="https://example.com/agent", id="fmt_video")
        pkg = PackageRequest(
            budget=1000,
            pricing_option_id="po_1",
            product_id="prod_1",
            format_ids=[fmt],
        )
        assert pkg.format_ids[0].id == "fmt_video"
