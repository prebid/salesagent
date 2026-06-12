"""Unit tests for product-level GAM pricing/availability persistence helpers."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock

from adcp.types import DeliveryForecast

from src.core.database.models import PricingOption, Product
from src.services.gam_pricing_availability_sync import (
    _apply_pricing_guidance,
    _get_complete_price_guidance_report,
    _product_guidance_from_line_items,
    _product_targeted_ad_unit_ids,
    _product_targeted_placement_ids,
    _report_country_filters,
    _rows_for_product,
)


def _product(*, countries: list[str] | None = None) -> Product:
    product = Product(
        tenant_id="tenant_1",
        product_id="sports_display",
        name="Sports Display",
        description="Sports display inventory",
        format_ids=[{"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"}],
        targeting_template={},
        delivery_type="non_guaranteed",
        property_tags=["all_inventory"],
        implementation_config={"targeted_placement_ids": ["123", "456"]},
        countries=countries,
        delivery_measurement={"provider": "google_ad_manager"},
    )
    product.pricing_options = [
        PricingOption(
            tenant_id="tenant_1",
            product_id="sports_display",
            pricing_model="cpm",
            rate=None,
            currency="USD",
            is_fixed=False,
            price_guidance={"floor": 0.5},
            min_spend_per_package=Decimal("20.00"),
        ),
        PricingOption(
            tenant_id="tenant_1",
            product_id="sports_display",
            pricing_model="cpc",
            rate=None,
            currency="USD",
            is_fixed=False,
            price_guidance={"floor": 0.05},
        ),
    ]
    return product


def _line_item(
    *,
    placement_id: str,
    ad_unit_id: str = "",
    country_code: str = "US",
    country: str = "United States",
    impressions: int,
    cpm: float,
    clicks: int = 0,
    viewable_impressions: int = 0,
    measurable_impressions: int = 0,
    completed_views: int = 0,
) -> dict:
    revenue = round(impressions / 1000 * cpm, 2)
    return {
        "placement_id": placement_id,
        "placement_name": f"Placement {placement_id}",
        "ad_unit_id": ad_unit_id,
        "ad_unit_name": f"Ad unit {ad_unit_id}" if ad_unit_id else "",
        "country_code": country_code,
        "country": country,
        "line_item_id": f"li_{placement_id}_{impressions}",
        "line_item_name": "Line item",
        "line_item_type": "PRICE_PRIORITY",
        "impressions": impressions,
        "viewable_impressions": viewable_impressions,
        "measurable_impressions": measurable_impressions,
        "clicks": clicks,
        "completed_views": completed_views,
        "revenue": revenue,
        "cpm": cpm,
        "vcpm": round(revenue / viewable_impressions * 1000, 4) if viewable_impressions else None,
        "cpc": round(revenue / clicks, 4) if clicks else None,
        "cpcv": round(revenue / completed_views, 4) if completed_views else None,
    }


def test_product_targeted_placement_ids_reads_effective_config() -> None:
    assert _product_targeted_placement_ids(_product()) == ["123", "456"]


def test_product_targeted_ad_unit_ids_reads_effective_config() -> None:
    product = _product()
    product.implementation_config = {"targeted_ad_unit_ids": ["987", "654", "987"]}

    assert _product_targeted_ad_unit_ids(product) == ["654", "987"]


def test_rows_for_product_filters_by_placement_and_country() -> None:
    rows = [
        _line_item(placement_id="123", country_code="US", impressions=10_000, cpm=1.0),
        _line_item(placement_id="123", country_code="CA", country="Canada", impressions=10_000, cpm=1.0),
        _line_item(placement_id="999", country_code="US", impressions=10_000, cpm=1.0),
    ]

    filtered = _rows_for_product(rows, {"placement_ids": ["123"], "ad_unit_ids": [], "countries": {"us"}})

    assert [row["placement_id"] for row in filtered] == ["123"]
    assert [row["country_code"] for row in filtered] == ["US"]


def test_rows_for_product_filters_by_ad_unit_and_country() -> None:
    rows = [
        _line_item(placement_id="", ad_unit_id="987", country_code="US", impressions=10_000, cpm=1.0),
        _line_item(
            placement_id="",
            ad_unit_id="987",
            country_code="CA",
            country="Canada",
            impressions=10_000,
            cpm=1.0,
        ),
        _line_item(placement_id="", ad_unit_id="654", country_code="US", impressions=10_000, cpm=1.0),
    ]

    filtered = _rows_for_product(rows, {"placement_ids": [], "ad_unit_ids": ["987"], "countries": {"us"}})

    assert [row["ad_unit_id"] for row in filtered] == ["987"]
    assert [row["country_code"] for row in filtered] == ["US"]


def test_report_country_filters_only_when_every_product_has_country_targeting() -> None:
    assert _report_country_filters(
        [
            {"report_countries": ["US"]},
            {"report_countries": ["Canada"]},
        ]
    ) == ["Canada", "US"]
    assert _report_country_filters([{"report_countries": ["US"]}, {"report_countries": []}]) is None


def _report(
    *,
    placement_ids: list[str],
    possibly_truncated: bool,
    line_item_rows: list[dict] | None = None,
) -> dict:
    rows = list(line_item_rows or [])
    return {
        "possibly_truncated": possibly_truncated,
        "date_range": "this_month",
        "window_start": "2026-05-01",
        "window_end": "2026-05-28",
        "filters": {"placement_ids": placement_ids, "line_item_types": ["PRICE_PRIORITY"]},
        "line_item_rows": rows,
        "raw_rows": len(rows),
        "eligible_line_item_rows": len(rows),
    }


def _reporting_with_reports(*reports: dict) -> MagicMock:
    reporting = MagicMock()
    reporting.network_timezone = "UTC"
    reporting.get_placement_country_price_guidance.side_effect = list(reports)
    return reporting


def test_complete_price_guidance_report_splits_truncated_report_into_smaller_batches() -> None:
    reporting = _reporting_with_reports(
        _report(placement_ids=["1", "2", "3", "4"], possibly_truncated=True),
        _report(
            placement_ids=["1", "2"],
            possibly_truncated=False,
            line_item_rows=[_line_item(placement_id="1", impressions=10_000, cpm=1.0)],
        ),
        _report(
            placement_ids=["3", "4"],
            possibly_truncated=False,
            line_item_rows=[_line_item(placement_id="4", impressions=20_000, cpm=2.0)],
        ),
    )

    report = _get_complete_price_guidance_report(
        reporting,
        date_range="this_month",
        placement_ids=["1", "2", "3", "4"],
        ad_unit_ids=[],
        countries=None,
        line_item_types=["PRICE_PRIORITY"],
        min_group_impressions=1,
        min_line_item_impressions=1,
        bookability_safety_factor=1.0,
        currency="USD",
    )

    assert report["possibly_truncated"] is False
    assert report["chunked"] is True
    assert report["chunk_count"] == 2
    assert report["raw_rows"] == 2
    assert report["eligible_line_item_rows"] == 2
    assert [row["placement_id"] for row in report["line_item_rows"]] == ["1", "4"]
    assert report["filters"]["placement_ids"] == ["1", "2", "3", "4"]
    assert [call.kwargs["placement_ids"] for call in reporting.get_placement_country_price_guidance.call_args_list] == [
        ["1", "2", "3", "4"],
        ["1", "2"],
        ["3", "4"],
    ]
    assert [call.kwargs["ad_unit_ids"] for call in reporting.get_placement_country_price_guidance.call_args_list] == [
        [],
        [],
        [],
    ]


def test_complete_price_guidance_report_recursively_splits_large_truncated_chunks() -> None:
    reporting = _reporting_with_reports(
        _report(placement_ids=["1", "2", "3", "4"], possibly_truncated=True),
        _report(placement_ids=["1", "2"], possibly_truncated=True),
        _report(
            placement_ids=["1"],
            possibly_truncated=False,
            line_item_rows=[_line_item(placement_id="1", impressions=10_000, cpm=1.0)],
        ),
        _report(
            placement_ids=["2"],
            possibly_truncated=False,
            line_item_rows=[_line_item(placement_id="2", impressions=10_000, cpm=1.0)],
        ),
        _report(
            placement_ids=["3", "4"],
            possibly_truncated=False,
            line_item_rows=[_line_item(placement_id="4", impressions=20_000, cpm=2.0)],
        ),
    )

    report = _get_complete_price_guidance_report(
        reporting,
        date_range="this_month",
        placement_ids=["1", "2", "3", "4"],
        ad_unit_ids=[],
        countries=None,
        line_item_types=["PRICE_PRIORITY"],
        min_group_impressions=1,
        min_line_item_impressions=1,
        bookability_safety_factor=1.0,
        currency="USD",
    )

    assert report["chunk_count"] == 3
    assert [row["placement_id"] for row in report["line_item_rows"]] == ["1", "2", "4"]
    assert [call.kwargs["placement_ids"] for call in reporting.get_placement_country_price_guidance.call_args_list] == [
        ["1", "2", "3", "4"],
        ["1", "2"],
        ["1"],
        ["2"],
        ["3", "4"],
    ]


def test_complete_price_guidance_report_marks_single_placement_truncation_incomplete() -> None:
    reporting = _reporting_with_reports(
        _report(
            placement_ids=["1"],
            possibly_truncated=True,
            line_item_rows=[_line_item(placement_id="1", impressions=100_000, cpm=1.0)],
        )
    )

    report = _get_complete_price_guidance_report(
        reporting,
        date_range="this_month",
        placement_ids=["1"],
        ad_unit_ids=[],
        countries=None,
        line_item_types=["PRICE_PRIORITY"],
        min_group_impressions=1,
        min_line_item_impressions=1,
        bookability_safety_factor=1.0,
        currency="USD",
    )

    assert report["possibly_truncated"] is False
    assert report["incomplete_report"] is True
    assert report["incomplete_report_chunks"] == 1
    assert report["truncated_single_placement_ids"] == ["1"]
    assert report["truncated_single_ad_unit_ids"] == []
    assert [row["placement_id"] for row in report["line_item_rows"]] == ["1"]


def test_complete_price_guidance_report_combines_incomplete_single_placement_chunks() -> None:
    reporting = _reporting_with_reports(
        _report(placement_ids=["1", "2"], possibly_truncated=True),
        _report(
            placement_ids=["1"],
            possibly_truncated=True,
            line_item_rows=[_line_item(placement_id="1", impressions=100_000, cpm=1.0)],
        ),
        _report(
            placement_ids=["2"],
            possibly_truncated=False,
            line_item_rows=[_line_item(placement_id="2", impressions=10_000, cpm=2.0)],
        ),
    )

    report = _get_complete_price_guidance_report(
        reporting,
        date_range="this_month",
        placement_ids=["1", "2"],
        ad_unit_ids=[],
        countries=None,
        line_item_types=["PRICE_PRIORITY"],
        min_group_impressions=1,
        min_line_item_impressions=1,
        bookability_safety_factor=1.0,
        currency="USD",
    )

    assert report["chunked"] is True
    assert report["chunk_count"] == 2
    assert report["incomplete_report"] is True
    assert report["incomplete_report_chunks"] == 1
    assert report["truncated_single_placement_ids"] == ["1"]
    assert report["truncated_single_ad_unit_ids"] == []
    assert [row["placement_id"] for row in report["line_item_rows"]] == ["1", "2"]


def test_complete_price_guidance_report_passes_and_splits_ad_unit_targets() -> None:
    reporting = _reporting_with_reports(
        _report(placement_ids=["1"], possibly_truncated=True),
        _report(
            placement_ids=["1"],
            possibly_truncated=False,
            line_item_rows=[_line_item(placement_id="1", impressions=10_000, cpm=1.0)],
        ),
        {
            **_report(placement_ids=[], possibly_truncated=False),
            "filters": {"placement_ids": [], "ad_unit_ids": ["987"], "line_item_types": ["PRICE_PRIORITY"]},
            "line_item_rows": [
                _line_item(placement_id="", ad_unit_id="987", impressions=20_000, cpm=2.0),
            ],
            "raw_rows": 1,
            "eligible_line_item_rows": 1,
        },
    )

    report = _get_complete_price_guidance_report(
        reporting,
        date_range="this_month",
        placement_ids=["1"],
        ad_unit_ids=["987"],
        countries=None,
        line_item_types=["PRICE_PRIORITY"],
        min_group_impressions=1,
        min_line_item_impressions=1,
        bookability_safety_factor=1.0,
        currency="USD",
    )

    assert report["filters"]["placement_ids"] == ["1"]
    assert report["filters"]["ad_unit_ids"] == ["987"]
    assert [row["ad_unit_id"] for row in report["line_item_rows"]] == ["", "987"]
    assert [row["placement_id"] for row in report["line_item_rows"]] == ["1", ""]
    assert [call.kwargs["placement_ids"] for call in reporting.get_placement_country_price_guidance.call_args_list] == [
        ["1"],
        ["1"],
        [],
    ]
    assert [call.kwargs["ad_unit_ids"] for call in reporting.get_placement_country_price_guidance.call_args_list] == [
        ["987"],
        [],
        ["987"],
    ]


def test_product_guidance_builds_forecast_bookability_and_pricing_guidance() -> None:
    product = _product()
    rows = [
        _line_item(
            placement_id="123",
            impressions=10_000,
            cpm=1.0,
            clicks=20,
            viewable_impressions=6_000,
            measurable_impressions=8_000,
            completed_views=500,
        ),
        _line_item(
            placement_id="456",
            impressions=30_000,
            cpm=2.0,
            clicks=90,
            viewable_impressions=20_000,
            measurable_impressions=25_000,
            completed_views=2_000,
        ),
    ]

    guidance = _product_guidance_from_line_items(
        product=product,
        line_item_rows=rows,
        currency="USD",
        capacity_guidance={"minimum_package_budget": 20.0},
        min_group_impressions=1,
        bookability_safety_factor=1.0,
        generated_at=datetime(2026, 5, 28, tzinfo=UTC),
        valid_until=datetime(2026, 5, 28, tzinfo=UTC) + timedelta(hours=6),
        report={
            "date_range": "this_month",
            "window_start": "2026-05-01",
            "window_end": "2026-05-28",
            "filters": {"line_item_types": ["PRICE_PRIORITY"]},
            "chunked": True,
            "chunk_count": 2,
            "incomplete_report": True,
            "incomplete_report_chunks": 1,
            "truncated_single_placement_ids": ["123"],
        },
        currency_limits={},
    )

    forecast = DeliveryForecast.model_validate(guidance["forecast"])
    assert forecast.points[0].product_id == "sports_display"
    assert forecast.points[0].metrics.impressions.mid == 40_000
    assert forecast.points[0].metrics.clicks.mid == 110
    assert forecast.points[0].viewability is not None
    assert forecast.points[0].viewability.viewable_impressions.mid == 26_000
    assert forecast.points[0].viewability.viewable_rate.mid == 0.787879
    assert guidance["forecast"]["ext"]["source"]["incomplete_report"] is True
    assert guidance["forecast"]["ext"]["source"]["truncated_single_placement_ids"] == ["123"]
    assert guidance["pricing_guidance_by_model"]["cpm"] == {"p25": 1.0, "p50": 2.0, "p75": 2.0, "p90": 2.0}
    assert guidance["pricing_guidance_by_model"]["cpc"]["p25"] == 0.67
    assert guidance["bookability"]["bookable"] is True
    assert guidance["bookability"]["options"][0]["required_units"] == 20_000

    assert _apply_pricing_guidance(product.pricing_options, guidance) == 2
    assert product.pricing_options[0].price_guidance == {"floor": 0.5, "p25": 1.0, "p50": 2.0, "p75": 2.0, "p90": 2.0}


def test_apply_pricing_guidance_adds_floor_fallback_for_legacy_auction_options() -> None:
    option = PricingOption(
        tenant_id="tenant_1",
        product_id="sports_display",
        pricing_model="cpm",
        rate=None,
        currency="USD",
        is_fixed=False,
        price_guidance={},
    )

    assert (
        _apply_pricing_guidance(
            [option],
            {"pricing_guidance_by_model": {"cpm": {"p25": 1.0, "p50": 2.0, "p75": 2.0, "p90": 2.0}}},
        )
        == 1
    )
    assert option.price_guidance == {"floor": 1.0, "p25": 1.0, "p50": 2.0, "p75": 2.0, "p90": 2.0}


def test_product_guidance_marks_product_unbookable_when_conservative_capacity_is_too_small() -> None:
    product = _product()
    rows = [_line_item(placement_id="123", impressions=10_000, cpm=1.0)]

    guidance = _product_guidance_from_line_items(
        product=product,
        line_item_rows=rows,
        currency="USD",
        capacity_guidance={},
        min_group_impressions=1,
        bookability_safety_factor=1.0,
        generated_at=datetime(2026, 5, 28, tzinfo=UTC),
        valid_until=datetime(2026, 5, 28, tzinfo=UTC) + timedelta(hours=6),
        report={"date_range": "this_month", "filters": {}},
        currency_limits={},
    )

    assert guidance["bookability"]["bookable"] is False
    assert guidance["bookability"]["options"][0]["reason"] == "insufficient_capacity"


def test_product_guidance_uses_capacity_minimum_for_bookability() -> None:
    product = _product()
    rows = [_line_item(placement_id="123", impressions=100_000, cpm=1.0)]

    guidance = _product_guidance_from_line_items(
        product=product,
        line_item_rows=rows,
        currency="USD",
        capacity_guidance={"minimum_package_budget": 620.0},
        min_group_impressions=1,
        bookability_safety_factor=1.0,
        generated_at=datetime(2026, 5, 28, tzinfo=UTC),
        valid_until=datetime(2026, 5, 28, tzinfo=UTC) + timedelta(hours=6),
        report={"date_range": "this_month", "filters": {}},
        currency_limits={},
    )

    cpm_option = guidance["bookability"]["options"][0]
    assert cpm_option["minimum_package_budget"] == 620.0
    assert cpm_option["required_units"] == 620_000
    assert cpm_option["reason"] == "insufficient_capacity"


def test_product_guidance_marks_mismatched_currency_unbookable_and_skips_guidance() -> None:
    product = _product()
    product.pricing_options[0].currency = "EUR"
    rows = [_line_item(placement_id="123", impressions=100_000, cpm=1.0)]

    guidance = _product_guidance_from_line_items(
        product=product,
        line_item_rows=rows,
        currency="USD",
        capacity_guidance={},
        min_group_impressions=1,
        bookability_safety_factor=1.0,
        generated_at=datetime(2026, 5, 28, tzinfo=UTC),
        valid_until=datetime(2026, 5, 28, tzinfo=UTC) + timedelta(hours=6),
        report={"date_range": "this_month", "filters": {}},
        currency_limits={},
    )

    assert guidance["bookability"]["options"][0]["reason"] == "currency_conversion_unavailable"
    assert _apply_pricing_guidance(product.pricing_options, guidance) == 0
    assert product.pricing_options[0].price_guidance == {"floor": 0.5}
