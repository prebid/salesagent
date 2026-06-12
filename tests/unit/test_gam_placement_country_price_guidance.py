"""Unit tests for GAM placement-country price guidance aggregation."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from src.adapters.gam_reporting_service import GAMReportingService


def _service_with_rows(rows: list[dict[str, Any]], captured: dict[str, Any] | None = None) -> GAMReportingService:
    service = GAMReportingService.__new__(GAMReportingService)
    service.network_timezone = "America/New_York"

    def fake_run_report(report_query: dict[str, Any]) -> list[dict[str, Any]]:
        if captured is not None:
            captured["report_query"] = report_query
        return rows

    service._run_report = fake_run_report  # type: ignore[method-assign]
    return service


def _row(
    *,
    placement_id: str = "123",
    placement_name: str = "Sports ROS",
    ad_unit_id: str = "",
    ad_unit_name: str = "",
    country_code: str = "US",
    country: str = "United States",
    line_item_id: str,
    line_item_name: str,
    impressions: int,
    cpm: float,
    clicks: int = 0,
    completed_views: int = 0,
    viewable_impressions: int = 0,
    measurable_impressions: int = 0,
    line_item_type: str = "STANDARD",
) -> dict[str, str]:
    revenue_micros = int((impressions / 1000) * cpm * 1_000_000)
    return {
        "Dimension.PLACEMENT_ID": placement_id,
        "Dimension.PLACEMENT_NAME": placement_name,
        "Dimension.AD_UNIT_ID": ad_unit_id,
        "Dimension.AD_UNIT_NAME": ad_unit_name,
        "Dimension.COUNTRY_CODE": country_code,
        "Dimension.COUNTRY_NAME": country,
        "Dimension.LINE_ITEM_ID": line_item_id,
        "Dimension.LINE_ITEM_NAME": line_item_name,
        "Dimension.LINE_ITEM_TYPE": line_item_type,
        "Column.AD_SERVER_IMPRESSIONS": str(impressions),
        "Column.AD_SERVER_CLICKS": str(clicks),
        "Column.AD_SERVER_CPM_AND_CPC_REVENUE": str(revenue_micros),
        "Column.AD_SERVER_VIDEO_COMPLETIONS": str(completed_views),
        "Column.AD_SERVER_ACTIVE_VIEW_VIEWABLE_IMPRESSIONS": str(viewable_impressions),
        "Column.AD_SERVER_ACTIVE_VIEW_MEASURABLE_IMPRESSIONS": str(measurable_impressions),
    }


def test_price_guidance_uses_impression_weighted_line_item_percentiles():
    service = _service_with_rows(
        [
            _row(
                line_item_id="li_1",
                line_item_name="Small Premium",
                impressions=10_000,
                cpm=1.00,
                clicks=20,
                completed_views=2_000,
                viewable_impressions=5_000,
                measurable_impressions=8_000,
                line_item_type="PRICE_PRIORITY",
            ),
            _row(
                line_item_id="li_2",
                line_item_name="Large Base",
                impressions=283_000,
                cpm=0.28,
                clicks=120,
                completed_views=100_000,
                viewable_impressions=200_000,
                measurable_impressions=250_000,
                line_item_type="PRICE_PRIORITY",
            ),
            _row(
                line_item_id="li_3",
                line_item_name="Mid Market",
                impressions=100_000,
                cpm=0.75,
                clicks=80,
                completed_views=50_000,
                viewable_impressions=70_000,
                measurable_impressions=90_000,
                line_item_type="PRICE_PRIORITY",
            ),
            _row(line_item_id="li_4", line_item_name="Tiny Outlier", impressions=500, cpm=20.00),
        ]
    )

    result = service.get_placement_country_price_guidance(
        "this_month",
        min_group_impressions=10_000,
        min_line_item_impressions=1_000,
    )

    assert result["raw_rows"] == 4
    assert result["possibly_truncated"] is False
    assert result["eligible_line_item_rows"] == 3
    assert result["group_count"] == 1
    assert result["bookable_group_count"] == 1
    assert result["forecast"] == {
        "method": "estimate",
        "currency": "USD",
        "forecast_range_unit": "availability",
        "points": [
            {
                "label": "Sports ROS / United States",
                "dimensions": [
                    {
                        "kind": "placement",
                        "placement_ref": {"placement_id": "123"},
                        "placement_name": "Sports ROS",
                    },
                    {
                        "kind": "geo",
                        "geo_level": "country",
                        "geo_code": "US",
                        "geo_name": "United States",
                    },
                ],
                "metrics": {
                    "impressions": {"mid": 393000.0},
                    "spend": {"mid": 164.24},
                    "clicks": {"mid": 220.0},
                    "completed_views": {"mid": 152000.0},
                },
                "viewability": {
                    "vendor": {"domain": "googleadmanager.com"},
                    "standard": "mrc",
                    "measurable_impressions": {"mid": 348000.0},
                    "viewable_impressions": {"mid": 275000.0},
                    "viewable_rate": {"mid": 0.79023},
                },
            }
        ],
    }
    group = result["groups"][0]
    assert group["placement_id"] == "123"
    assert group["country_code"] == "US"
    assert group["country"] == "United States"
    assert group["bookable"] is True
    assert group["bookability"]["reason"] == "no_min_package_budget"
    assert group["total_impressions"] == 393_000
    assert group["total_viewable_impressions"] == 275_000
    assert group["total_measurable_impressions"] == 348_000
    assert group["total_clicks"] == 220
    assert group["total_completed_views"] == 152_000
    assert group["average_cpm"] == 0.42
    assert group["average_vcpm"] == 0.6
    assert group["average_cpc"] == 0.75
    assert group["average_cpcv"] == 0.0
    assert group["ctr"] == 0.00056
    assert group["viewability_rate"] == 0.79023
    assert group["completion_rate"] == 0.386768
    assert group["price_guidance"] == {"p25": 0.28, "p50": 0.28, "p75": 0.75, "p90": 0.75}
    assert group["pricing_guidance_by_model"]["cpm"] == {"p25": 0.28, "p50": 0.28, "p75": 0.75, "p90": 0.75}
    assert group["pricing_guidance_by_model"]["vcpm"] == {"p25": 0.4, "p50": 0.4, "p75": 1.07, "p90": 1.07}
    assert group["pricing_guidance_by_model"]["cpc"] == {"p25": 0.66, "p50": 0.66, "p75": 0.94, "p90": 0.94}
    assert group["pricing_guidance_by_model"]["cpcv"] == {"p25": 0.0, "p50": 0.0, "p75": 0.0, "p90": 0.0}
    assert group["delivery_guidance"] == {
        "method": "historical",
        "points": [
            {
                "label": "Sports ROS / United States",
                "metrics": {
                    "impressions": {"mid": 393000.0},
                    "viewable_impressions": {"mid": 275000.0},
                    "clicks": {"mid": 220.0},
                    "completed_views": {"mid": 152000.0},
                    "ctr": {"mid": 0.00056},
                    "viewability_rate": {"mid": 0.79023},
                    "completion_rate": {"mid": 0.386768},
                },
            }
        ],
    }
    assert group["unweighted_line_item_guidance"] == {"p25": 0.52, "p50": 0.75, "p75": 0.88, "p90": 0.95}
    assert [row["line_item_id"] for row in group["line_items"]] == ["li_2", "li_3", "li_1"]


def test_price_guidance_query_applies_placement_and_country_filters():
    captured: dict[str, Any] = {}
    service = _service_with_rows([], captured)

    service.get_placement_country_price_guidance(
        "this_month",
        placement_ids=["2", "1"],
        countries=["United States", "Canada"],
    )

    report_query = captured["report_query"]["reportQuery"]
    assert report_query["dimensions"] == [
        "PLACEMENT_ID",
        "PLACEMENT_NAME",
        "COUNTRY_CODE",
        "COUNTRY_NAME",
        "LINE_ITEM_ID",
        "LINE_ITEM_NAME",
        "LINE_ITEM_TYPE",
    ]
    assert report_query["columns"] == [
        "AD_SERVER_IMPRESSIONS",
        "AD_SERVER_CLICKS",
        "AD_SERVER_CPM_AND_CPC_REVENUE",
        "AD_SERVER_VIDEO_COMPLETIONS",
        "AD_SERVER_ACTIVE_VIEW_VIEWABLE_IMPRESSIONS",
        "AD_SERVER_ACTIVE_VIEW_MEASURABLE_IMPRESSIONS",
    ]
    assert report_query["statement"]["query"] == (
        "WHERE PLACEMENT_ID IN (1, 2) AND COUNTRY_NAME IN ('Canada', 'United States') "
        "AND LINE_ITEM_TYPE IN ('PRICE_PRIORITY')"
    )


def test_price_guidance_query_filters_iso_country_codes_by_country_code():
    captured: dict[str, Any] = {}
    service = _service_with_rows([], captured)

    service.get_placement_country_price_guidance(
        "this_month",
        placement_ids=["1"],
        countries=["US"],
    )

    assert captured["report_query"]["reportQuery"]["statement"]["query"] == (
        "WHERE PLACEMENT_ID IN (1) AND COUNTRY_CRITERIA_ID IN (2840) AND LINE_ITEM_TYPE IN ('PRICE_PRIORITY')"
    )


def test_price_guidance_query_applies_ad_unit_filter():
    captured: dict[str, Any] = {}
    service = _service_with_rows([], captured)

    service.get_placement_country_price_guidance(
        "this_month",
        ad_unit_ids=["987", "654"],
        countries=["US"],
    )

    report_query = captured["report_query"]["reportQuery"]
    assert report_query["dimensions"] == [
        "AD_UNIT_ID",
        "AD_UNIT_NAME",
        "COUNTRY_CODE",
        "COUNTRY_NAME",
        "LINE_ITEM_ID",
        "LINE_ITEM_NAME",
        "LINE_ITEM_TYPE",
    ]
    assert report_query["statement"]["query"] == (
        "WHERE AD_UNIT_ID IN (654, 987) AND COUNTRY_CRITERIA_ID IN (2840) AND LINE_ITEM_TYPE IN ('PRICE_PRIORITY')"
    )


def test_price_guidance_query_applies_combined_inventory_filter():
    captured: dict[str, Any] = {}
    service = _service_with_rows([], captured)

    service.get_placement_country_price_guidance(
        "this_month",
        placement_ids=["1"],
        ad_unit_ids=["987"],
    )

    report_query = captured["report_query"]["reportQuery"]
    assert report_query["dimensions"] == [
        "PLACEMENT_ID",
        "PLACEMENT_NAME",
        "AD_UNIT_ID",
        "AD_UNIT_NAME",
        "COUNTRY_CODE",
        "COUNTRY_NAME",
        "LINE_ITEM_ID",
        "LINE_ITEM_NAME",
        "LINE_ITEM_TYPE",
    ]
    assert report_query["statement"]["query"] == (
        "WHERE (PLACEMENT_ID IN (1) OR AD_UNIT_ID IN (987)) AND LINE_ITEM_TYPE IN ('PRICE_PRIORITY')"
    )


def test_price_guidance_groups_ad_unit_rows():
    service = _service_with_rows(
        [
            _row(
                placement_id="",
                placement_name="",
                ad_unit_id="987",
                ad_unit_name="Homepage Leaderboard",
                line_item_id="li_1",
                line_item_name="Ad unit line item",
                impressions=10_000,
                cpm=1.0,
                line_item_type="PRICE_PRIORITY",
            )
        ]
    )

    result = service.get_placement_country_price_guidance(
        "this_month",
        ad_unit_ids=["987"],
        min_group_impressions=1,
        min_line_item_impressions=1,
    )

    group = result["groups"][0]
    assert group["inventory_target_kind"] == "ad_unit"
    assert group["inventory_target_id"] == "987"
    assert group["inventory_target_name"] == "Homepage Leaderboard"
    assert group["placement_id"] == ""
    assert group["ad_unit_id"] == "987"
    assert result["forecast"]["points"][0]["label"] == "Homepage Leaderboard / United States"
    assert result["forecast"]["points"][0]["dimensions"][0]["placement_ref"] == {"ad_unit_id": "987"}


def test_price_guidance_marks_unbookable_groups_outside_minimum_budget_capacity():
    service = _service_with_rows(
        [
            _row(
                line_item_id="li_1",
                line_item_name="Low Capacity",
                impressions=10_000,
                cpm=1.00,
                line_item_type="PRICE_PRIORITY",
            )
        ]
    )

    result = service.get_placement_country_price_guidance(
        "this_month",
        min_group_impressions=1,
        min_line_item_impressions=1,
        min_package_budget=20.0,
    )

    group = result["groups"][0]
    assert group["bookable"] is False
    assert group["bookability"] == {
        "bookable": False,
        "reason": "insufficient_capacity",
        "minimum_package_budget": 20.0,
        "price_basis": "cpm_p25",
        "price": 1.0,
        "required_units": 20_000,
        "available_units": 10_000,
        "safety_factor": 1.0,
    }
    assert result["bookable_group_count"] == 0
    assert result["forecast"]["points"] == []


def test_price_guidance_can_include_all_line_item_types():
    service = _service_with_rows(
        [
            _row(
                line_item_id="li_1",
                line_item_name="Guaranteed",
                impressions=10_000,
                cpm=10.0,
                line_item_type="STANDARD",
            ),
            _row(
                line_item_id="li_2",
                line_item_name="Price Priority",
                impressions=10_000,
                cpm=1.0,
                line_item_type="PRICE_PRIORITY",
            ),
        ],
    )

    result = service.get_placement_country_price_guidance(
        "this_month",
        line_item_types=None,
        min_group_impressions=1,
        min_line_item_impressions=1,
    )

    assert result["groups"][0]["line_item_count"] == 1

    result = service.get_placement_country_price_guidance(
        "this_month",
        line_item_types=[],
        min_group_impressions=1,
        min_line_item_impressions=1,
    )

    assert result["groups"][0]["line_item_count"] == 2


def test_line_item_capacity_guidance_backs_minimum_budget_out_of_monthly_revenue():
    captured: dict[str, Any] = {}
    service = _service_with_rows(
        [
            {
                "Dimension.DATE": "2026-05-10",
                "Column.AD_SERVER_IMPRESSIONS": "10000000",
                "Column.AD_SERVER_CPM_AND_CPC_REVENUE": "100000000000",
            }
        ],
        captured,
    )

    def fake_report_config(**_: Any) -> tuple[list[str], datetime, datetime, str]:
        return [], datetime(2026, 5, 1), datetime(2026, 5, 10, 12), "daily"

    def fake_line_item_count(*, start_date: datetime | None = None, end_date: datetime | None = None) -> int:
        if start_date is not None and end_date is not None:
            return 250
        return 50_000

    service._get_report_config = fake_report_config  # type: ignore[method-assign]
    service._line_item_count = fake_line_item_count  # type: ignore[method-assign]

    result = service.get_line_item_capacity_guidance(
        "this_month",
        max_network_line_items=100_000,
        monthly_line_item_space_fraction=0.01,
        estimated_line_items_per_package=2,
    )

    assert result["network_revenue"] == {"to_date": 100_000.0, "projected_monthly": 310_000.0}
    assert captured["report_query"]["reportQuery"]["dimensions"] == ["DATE"]
    assert captured["report_query"]["reportQuery"]["columns"] == [
        "AD_SERVER_IMPRESSIONS",
        "AD_SERVER_CPM_AND_CPC_REVENUE",
    ]
    assert result["line_item_capacity"]["monthly_line_item_budget"] == 1_000
    assert result["line_item_capacity"]["effective_monthly_line_item_budget"] == 1_000
    assert result["line_item_capacity"]["total_line_items"] == 50_000
    assert result["line_item_capacity"]["total_line_item_utilization_pct"] == 50.0
    assert result["line_item_capacity"]["remaining_total_line_item_capacity"] == 50_000
    assert result["line_item_capacity"]["created_in_window"] == 250
    assert result["line_item_capacity"]["created_in_window_pct_of_monthly_budget"] == 25.0
    assert result["line_item_capacity"]["remaining_monthly_line_item_budget"] == 750
    assert result["minimum_package_budget"] == 620.0
