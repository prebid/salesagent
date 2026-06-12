"""Tests for SpringServe adapter reporting-cache reads.

Verifies ``get_media_buy_delivery`` aggregation and ``get_packages_snapshot``
mapping against a mocked SpringServeDemandTagStatsRepository.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from src.adapters.base import DeliveryDataUnavailable
from src.adapters.springserve import SpringServeAdapter
from src.core.schemas import DeliveryStatus, ReportingPeriod


@pytest.fixture
def mock_principal():
    p = MagicMock()
    p.name = "video_advertiser"
    p.principal_id = "principal_ss_1"
    p.get_adapter_id.return_value = "88061"
    return p


@pytest.fixture
def adapter(mock_principal):
    a = SpringServeAdapter(
        config={"api_token": "tok"},
        principal=mock_principal,
        dry_run=False,
        tenant_id="tenant_ss_1",
    )
    a._client = MagicMock()
    return a


def _stat_row(**overrides):
    row = MagicMock()
    row.demand_tag_id = "2149077"
    row.campaign_id = "900001"
    row.impressions = 1000
    row.completed_views = 850
    row.clicks = 5
    row.spend_micros = 27_500_000
    row.currency = "EUR"
    row.delivery_status = None
    row.as_of = datetime(2026, 5, 14, 12, 0, 0, tzinfo=UTC)
    for k, v in overrides.items():
        setattr(row, k, v)
    return row


class TestGetMediaBuyDelivery:
    def test_empty_cache_raises_delivery_data_unavailable(self, adapter):
        period = ReportingPeriod(start=datetime(2026, 5, 14, tzinfo=UTC), end=datetime(2026, 5, 14, tzinfo=UTC))
        with patch("src.adapters.springserve.adapter.SpringServeDemandTagStatsRepository") as repo_cls:
            repo_cls.return_value.list_by_campaign.return_value = []
            with patch("src.adapters.springserve.adapter.get_db_session"):
                with pytest.raises(DeliveryDataUnavailable):
                    adapter.get_media_buy_delivery("springserve_900001", period, today=datetime.now(UTC))

    def test_aggregates_totals_across_packages(self, adapter):
        period = ReportingPeriod(start=datetime(2026, 5, 14, tzinfo=UTC), end=datetime(2026, 5, 14, tzinfo=UTC))
        rows = [
            _stat_row(demand_tag_id="2149077", impressions=1000, completed_views=850, spend_micros=27_500_000),
            _stat_row(demand_tag_id="2149080", impressions=500, completed_views=400, spend_micros=12_000_000),
        ]
        with patch("src.adapters.springserve.adapter.SpringServeDemandTagStatsRepository") as repo_cls:
            repo_cls.return_value.list_by_campaign.return_value = rows
            with patch("src.adapters.springserve.adapter.get_db_session"):
                result = adapter.get_media_buy_delivery("springserve_900001", period, today=datetime.now(UTC))

        assert result.media_buy_id == "springserve_900001"
        assert result.totals.impressions == 1500.0
        # 27.5 + 12.0 = 39.5 EUR
        assert result.totals.spend == 39.5
        assert result.totals.completed_views == 1250.0
        assert result.currency == "EUR"
        assert len(result.by_package) == 2
        # Per-package values from the rows
        package_ids = {p.package_id for p in result.by_package}
        assert package_ids == {"2149077", "2149080"}


class TestGetPackagesSnapshot:
    def test_missing_rows_surface_as_none(self, adapter):
        with patch("src.adapters.springserve.adapter.SpringServeDemandTagStatsRepository") as repo_cls:
            repo_cls.return_value.get_by_demand_tag_ids.return_value = {}
            with patch("src.adapters.springserve.adapter.get_db_session"):
                result = adapter.get_packages_snapshot([("springserve_900001", "pkg_1", "2149077")])

        assert result["springserve_900001"]["pkg_1"] is None

    def test_package_refs_without_platform_id_returns_none(self, adapter):
        """When no platform-side demand_tag_id is set yet (package not pushed),
        snapshot is None -- no DB roundtrip needed."""
        result = adapter.get_packages_snapshot([("springserve_900001", "pkg_1", None)])
        assert result["springserve_900001"]["pkg_1"] is None

    def test_populated_row_produces_snapshot(self, adapter):
        with patch("src.adapters.springserve.adapter.SpringServeDemandTagStatsRepository") as repo_cls:
            repo_cls.return_value.get_by_demand_tag_ids.return_value = {"2149077": _stat_row()}
            with patch("src.adapters.springserve.adapter.get_db_session"):
                result = adapter.get_packages_snapshot([("springserve_900001", "pkg_1", "2149077")])

        snap = result["springserve_900001"]["pkg_1"]
        assert snap is not None
        assert snap.impressions == 1000.0
        assert snap.spend == 27.5
        assert snap.clicks == 5.0
        assert snap.currency == "EUR"

    def test_delivery_status_maps_to_enum(self, adapter):
        with patch("src.adapters.springserve.adapter.SpringServeDemandTagStatsRepository") as repo_cls:
            repo_cls.return_value.get_by_demand_tag_ids.return_value = {
                "2149077": _stat_row(delivery_status="delivering"),
                "2149080": _stat_row(demand_tag_id="2149080", delivery_status="paused"),
                "2149081": _stat_row(demand_tag_id="2149081", delivery_status="budget_exhausted"),
            }
            with patch("src.adapters.springserve.adapter.get_db_session"):
                result = adapter.get_packages_snapshot(
                    [
                        ("buy", "delivering_pkg", "2149077"),
                        ("buy", "paused_pkg", "2149080"),
                        ("buy", "exhausted_pkg", "2149081"),
                    ]
                )

        assert result["buy"]["delivering_pkg"].delivery_status == DeliveryStatus.delivering
        assert result["buy"]["paused_pkg"].delivery_status == DeliveryStatus.not_delivering
        assert result["buy"]["exhausted_pkg"].delivery_status == DeliveryStatus.budget_exhausted


class TestRunReportingSync:
    def test_dry_run_returns_soft_failed_result(self, mock_principal):
        a = SpringServeAdapter(
            config={"api_token": "tok"},
            principal=mock_principal,
            dry_run=True,
            tenant_id="tenant_ss_1",
        )
        result = a.run_reporting_sync()
        assert result.sync_kind == "reporting"
        assert result.succeeded is False
        assert "dry-run" in next(iter(result.errors.values()))

    def test_scope_not_granted_produces_scope_pending_metadata(self, adapter):
        from src.adapters.springserve.reporting_sync import ReportingScopeNotGranted

        with patch("src.adapters.springserve.adapter.get_db_session") as mock_db:
            mock_db.return_value.__enter__.return_value = MagicMock()
            with patch("src.adapters.springserve.reporting_sync.SpringServeReportingSync") as sync_cls:
                sync_cls.return_value.run.side_effect = ReportingScopeNotGranted()
                result = adapter.run_reporting_sync()

        assert result.succeeded is False
        assert result.metadata == {"scope_pending": True}
        assert "scope" in result.errors

    def test_successful_sync_reports_rows_updated(self, adapter):
        from src.adapters.springserve.reporting_sync import ReportingSyncResult

        with patch("src.adapters.springserve.adapter.get_db_session") as mock_db:
            mock_db.return_value.__enter__.return_value = MagicMock()
            with patch("src.adapters.springserve.reporting_sync.SpringServeReportingSync") as sync_cls:
                sync_cls.return_value.run.return_value = ReportingSyncResult(
                    rows_updated=42, report_id="rpt-1", error=None
                )
                result = adapter.run_reporting_sync()

        assert result.succeeded is True
        assert result.counts == {"demand_tags": 42}
        assert result.metadata == {"report_id": "rpt-1"}

    def test_no_demand_tags_is_successful_noop(self):
        from src.adapters.springserve.reporting_sync import SpringServeReportingSync

        syncer = SpringServeReportingSync(
            client=MagicMock(),
            tenant_id="tenant_ss_1",
            session=MagicMock(),
        )

        result = syncer.run(demand_tag_ids=[])

        assert result.rows_updated == 0
        assert result.report_id is None
        assert result.error is None
