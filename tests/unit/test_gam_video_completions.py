"""Tests for completed-view metric flow through GAM reporting.

Covers tescoboy issue #225 (Phase 1): pre-fix `get_media_buy_delivery`
emitted ``completed_views=None`` on every video buy because the GAM
report query never requested any video columns. The Phase 1 scope ships
the ``AD_SERVER_VIDEO_COMPLETIONS`` column through the report pipeline
and surfaces it on ``DeliveryTotals`` and ``AdapterPackageDelivery``.

The GAM reporting service uses GAM-native key names internally
(``video_completions``, ``total_video_completions``); the AdCP-facing
schemas use ``completed_views`` per spec.

This closes the in-stream VAST gap (the most common case). Outstream
inventory still returns zero because VAST events don't fire on outstream
players — that's tracked as Phase 2 (classifier + viewership-merge).
These tests pin the Phase 1 contract; the outstream gap is documented
on the Phase 2 follow-up.
"""

from src.adapters.gam_reporting_service import GAMReportingService
from src.core.schemas import AdapterPackageDelivery


class TestProcessReportDataIncludesVideoCompletions:
    def test_video_completions_aggregated_into_processed_row(self):
        # Build a minimal GAMReportingService bypass: instantiate with no
        # GAM client and call the pure aggregation method directly.
        svc = GAMReportingService.__new__(GAMReportingService)
        raw = [
            {
                "Dimension.LINE_ITEM_ID": "li_1",
                "Dimension.DATE": "2026-05-08",
                "Column.AD_SERVER_IMPRESSIONS": "10000",
                "Column.AD_SERVER_CLICKS": "50",
                "Column.AD_SERVER_CPM_AND_CPC_REVENUE": "12500000",
                "Column.AD_SERVER_VIDEO_COMPLETIONS": "7500",
            }
        ]
        processed = svc._process_report_data(raw, granularity="daily", requested_tz="America/New_York")

        assert len(processed) == 1
        row = processed[0]
        assert row["impressions"] == 10000
        assert row["video_completions"] == 7500

    def test_zero_video_only_row_still_skipped_when_other_metrics_zero(self):
        svc = GAMReportingService.__new__(GAMReportingService)
        raw = [
            {
                "Dimension.LINE_ITEM_ID": "li_1",
                "Dimension.DATE": "2026-05-08",
                "Column.AD_SERVER_IMPRESSIONS": "0",
                "Column.AD_SERVER_CLICKS": "0",
                "Column.AD_SERVER_CPM_AND_CPC_REVENUE": "0",
                "Column.AD_SERVER_VIDEO_COMPLETIONS": "0",
            }
        ]
        processed = svc._process_report_data(raw, granularity="daily", requested_tz="America/New_York")
        assert processed == []

    def test_nonzero_video_keeps_row_even_when_impressions_zero(self):
        # Defensive: GAM reports occasionally surface video completions
        # without a matching impression row. Don't drop those.
        svc = GAMReportingService.__new__(GAMReportingService)
        raw = [
            {
                "Dimension.LINE_ITEM_ID": "li_1",
                "Dimension.DATE": "2026-05-08",
                "Column.AD_SERVER_IMPRESSIONS": "0",
                "Column.AD_SERVER_CLICKS": "0",
                "Column.AD_SERVER_CPM_AND_CPC_REVENUE": "0",
                "Column.AD_SERVER_VIDEO_COMPLETIONS": "42",
            }
        ]
        processed = svc._process_report_data(raw, granularity="daily", requested_tz="America/New_York")
        assert len(processed) == 1
        assert processed[0]["video_completions"] == 42


class TestCalculateMetricsTotalsVideoCompletions:
    def test_total_video_completions_summed_across_rows(self):
        svc = GAMReportingService.__new__(GAMReportingService)
        data = [
            {
                "impressions": 10000,
                "clicks": 50,
                "spend": 125.0,
                "video_completions": 7500,
                "advertiser_id": "a1",
                "order_id": "o1",
                "line_item_id": "li_1",
            },
            {
                "impressions": 5000,
                "clicks": 25,
                "spend": 60.0,
                "video_completions": 3500,
                "advertiser_id": "a1",
                "order_id": "o1",
                "line_item_id": "li_2",
            },
        ]
        metrics = svc._calculate_metrics(data)
        assert metrics["total_video_completions"] == 11000

    def test_empty_data_returns_zero_video_completions(self):
        svc = GAMReportingService.__new__(GAMReportingService)
        metrics = svc._calculate_metrics([])
        assert metrics["total_video_completions"] == 0

    def test_legacy_rows_without_video_default_to_zero(self):
        # Older cached/persisted reporting rows may predate the column
        # addition. The aggregator uses .get(..., 0) so they don't crash.
        svc = GAMReportingService.__new__(GAMReportingService)
        data = [
            {
                "impressions": 100,
                "clicks": 1,
                "spend": 1.0,
                "advertiser_id": "a1",
                "order_id": "o1",
                "line_item_id": "li_1",
            }
        ]
        metrics = svc._calculate_metrics(data)
        assert metrics["total_video_completions"] == 0


class TestAdapterPackageDeliveryVideoField:
    def test_field_optional_and_defaults_to_none(self):
        pkg = AdapterPackageDelivery(package_id="pkg_1", impressions=1000, spend=12.5)
        assert pkg.completed_views is None

    def test_explicit_value_preserved(self):
        pkg = AdapterPackageDelivery(package_id="pkg_1", impressions=1000, spend=12.5, completed_views=750)
        assert pkg.completed_views == 750

    def test_wire_dump_excludes_none_completed_views(self):
        pkg = AdapterPackageDelivery(package_id="pkg_1", impressions=1000, spend=12.5)
        wire = pkg.model_dump(exclude_none=True)
        assert "completed_views" not in wire

    def test_wire_dump_includes_zero_completed_views(self):
        # Zero is a valid measurement (e.g. early in flight); preserved.
        pkg = AdapterPackageDelivery(package_id="pkg_1", impressions=1000, spend=12.5, completed_views=0)
        wire = pkg.model_dump(exclude_none=True)
        assert wire["completed_views"] == 0
