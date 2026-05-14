"""Regression test for H5: GAM reporting must not silently drop rows with data.

The zero-impression filter in gam_reporting_service.py:445-448 drops rows where
impressions=0, even if clicks or revenue are non-zero. This causes FLAT_RATE/SPONSORSHIP
campaigns to show $0 spend and click-only campaigns to lose click data.

GH #1078 H5.
"""

from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.unit


def _make_service():
    """Create a GAMReportingService with mocked dependencies."""
    from src.adapters.gam_reporting_service import GAMReportingService

    service = GAMReportingService.__new__(GAMReportingService)
    service.gam_client = MagicMock()
    service.network_code = "12345"
    service.logger = MagicMock()
    return service


class TestZeroImpressionRowRetention:
    """Rows with zero impressions but non-zero clicks or revenue must be retained."""

    def test_zero_impressions_nonzero_revenue_retained(self):
        """FLAT_RATE/SPONSORSHIP rows (0 impressions, real revenue) must not be dropped."""
        service = _make_service()

        rows = [
            {
                "AD_SERVER_IMPRESSIONS": "0",
                "AD_SERVER_CLICKS": "0",
                "AD_SERVER_CPM_AND_CPC_REVENUE": "5000000",  # $5.00 in micros
                "ADVERTISER_ID": "adv1",
                "ORDER_ID": "order1",
                "LINE_ITEM_ID": "li1",
                "LINE_ITEM_NAME": "Sponsorship Deal",
                "DATE": "2026-04-15",
            },
        ]

        result = service._process_report_data(rows, granularity="daily", requested_tz="UTC")

        assert len(result) >= 1, (
            "Zero-impression row with revenue was dropped — FLAT_RATE/SPONSORSHIP spend is silently lost"
        )
        assert result[0]["spend"] == 5.0

    def test_zero_impressions_nonzero_clicks_retained(self):
        """Click-only rows (0 impressions, real clicks) must not be dropped."""
        service = _make_service()

        rows = [
            {
                "AD_SERVER_IMPRESSIONS": "0",
                "AD_SERVER_CLICKS": "42",
                "AD_SERVER_CPM_AND_CPC_REVENUE": "0",
                "ADVERTISER_ID": "adv1",
                "ORDER_ID": "order1",
                "LINE_ITEM_ID": "li2",
                "LINE_ITEM_NAME": "Click Tracker",
                "DATE": "2026-04-15",
            },
        ]

        result = service._process_report_data(rows, granularity="daily", requested_tz="UTC")

        assert len(result) >= 1, (
            "Zero-impression row with clicks was dropped — click-only campaign data is silently lost"
        )
        assert result[0]["clicks"] == 42

    def test_all_zeros_row_dropped(self):
        """Rows with ALL metrics at zero may be dropped (volume reduction)."""
        service = _make_service()

        rows = [
            {
                "AD_SERVER_IMPRESSIONS": "0",
                "AD_SERVER_CLICKS": "0",
                "AD_SERVER_CPM_AND_CPC_REVENUE": "0",
                "ADVERTISER_ID": "adv1",
                "ORDER_ID": "order1",
                "LINE_ITEM_ID": "li3",
                "DATE": "2026-04-15",
            },
        ]

        result = service._process_report_data(rows, granularity="daily", requested_tz="UTC")

        assert len(result) == 0, "All-zero rows should be dropped for volume reduction"
