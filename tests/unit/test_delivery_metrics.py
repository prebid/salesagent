"""Tests for get_media_buy_delivery with real GAM metrics."""

from datetime import datetime, timedelta
from unittest.mock import MagicMock, Mock, patch

import pytest

from src.adapters.gam_reporting_service import ReportingData
from src.adapters.google_ad_manager import GoogleAdManager
from src.core.schemas import Principal, ReportingPeriod
from freezegun import freeze_time

@pytest.fixture
def mock_principal():
    """Create a mock principal for testing."""
    principal = Mock(spec=Principal)
    principal.principal_id = "test_principal"
    principal.platform_mappings = {}
    return principal


@pytest.fixture
def gam_adapter(mock_principal):
    """Create a GAM adapter for testing."""
    config = {
        "network_code": "123456",
        "refresh_token": "test_token",
        "enabled": True,
    }
    return GoogleAdManager(
        config=config,
        principal=mock_principal,
        network_code="123456",
        advertiser_id="789",
        trafficker_id="101112",
        dry_run=True,  # Use dry-run mode for testing
        tenant_id="test_tenant",
    )


def test_get_media_buy_delivery_dry_run_mode(gam_adapter):
    """Test get_media_buy_delivery returns simulated metrics in dry-run mode."""
    # Setup
    media_buy_id = "mb_test_123"
    date_range = ReportingPeriod(
        start=datetime.now().isoformat(),
        end=(datetime.now() + timedelta(days=7)).isoformat(),
    )

    # Mock database query - patch where it's used in the function
    with patch("src.core.database.database_session.get_db_session") as mock_db:
        mock_session = MagicMock()
        mock_db.return_value.__enter__.return_value = mock_session

        # Create mock media buy
        mock_media_buy = Mock()
        mock_media_buy.media_buy_id = media_buy_id
        mock_media_buy.budget = 1000.0
        mock_media_buy.currency = "USD"
        mock_media_buy.raw_request = {"packages": []}

        mock_session.scalars.return_value.first.return_value = mock_media_buy

        # Execute
        result = gam_adapter.get_media_buy_delivery(media_buy_id, date_range, datetime.now())

        # Assert
        assert result is not None
        assert result.media_buy_id == media_buy_id
        assert result.totals.impressions > 0  # Should have simulated metrics
        assert result.totals.spend > 0
        assert result.currency == "USD"


def test_get_media_buy_delivery_media_buy_not_found(gam_adapter):
    """Test get_media_buy_delivery handles missing media buy gracefully."""
    # Setup
    media_buy_id = "mb_nonexistent"
    date_range = ReportingPeriod(
        start=datetime.now().isoformat(),
        end=(datetime.now() + timedelta(days=7)).isoformat(),
    )

    # Mock database query to return None
    with patch("src.core.database.database_session.get_db_session") as mock_db:
        mock_session = MagicMock()
        mock_db.return_value.__enter__.return_value = mock_session
        mock_session.scalars.return_value.first.return_value = None

        # Execute
        result = gam_adapter.get_media_buy_delivery(media_buy_id, date_range, datetime.now())

        # Assert - should return empty metrics
        assert result is not None
        assert result.media_buy_id == media_buy_id
        assert result.totals.impressions == 0
        assert result.totals.spend == 0


@patch("src.core.database.database_session.get_db_session")
@patch("src.adapters.gam_reporting_service.GAMReportingService")
def test_get_media_buy_delivery_with_real_gam_data(mock_reporting_service_class, mock_db, mock_principal):
    """Test get_media_buy_delivery with real GAM reporting data."""

    with freeze_time("2024-1-15 03:00:00"):            
        # Setup adapter in non-dry-run mode
        config = {
            "network_code": "123456",
            "refresh_token": "test_token",
            "enabled": True,
        }

        # Create mock client
        mock_client = Mock()

        # Create adapter with mocked client
        adapter = GoogleAdManager(
            config=config,
            principal=mock_principal,
            network_code="123456",
            advertiser_id="789",
            trafficker_id="101112",
            dry_run=False,
            tenant_id="test_tenant",
        )
        adapter.client = mock_client

        # Mock database query
        mock_session = MagicMock()
        mock_db.return_value.__enter__.return_value = mock_session

        mock_media_buy = Mock()
        mock_media_buy.media_buy_id = "mb_test_123"
        mock_media_buy.budget = 1000.0
        mock_media_buy.currency = "USD"
        mock_media_buy.raw_request = {
            "packages": [
                {"package_id": "pkg_1", "platform_line_item_id": "111"},
                {"package_id": "pkg_2", "platform_line_item_id": "222"},
            ]
        }

        mock_session.scalars.return_value.first.return_value = mock_media_buy

        # Mock GAM reporting data
        mock_reporting_instance = Mock()
        mock_reporting_service_class.return_value = mock_reporting_instance

        mock_reporting_data = ReportingData(
            data=[
                {
                    "line_item_id": "111",
                    "impressions": 5000,
                    "clicks": 50,
                    "spend": 250.0,
                    "ctr": 1.0,
                },
                {
                    "line_item_id": "222",
                    "impressions": 3000,
                    "clicks": 30,
                    "spend": 150.0,
                    "ctr": 1.0,
                },
            ],
            start_date=datetime.now(),
            end_date=datetime.now() + timedelta(days=7),
            requested_timezone="America/New_York",
            data_timezone="America/New_York",
            data_valid_until=datetime.now() + timedelta(days=7),
            query_type="lifetime",
            dimensions=["LINE_ITEM_ID"],
            metrics={
                "total_impressions": 8000,
                "total_clicks": 80,
                "total_spend": 400.0,
                "average_ctr": 1.0,
            },
        )

        mock_reporting_instance.get_reporting_data.return_value = mock_reporting_data

        # Execute
        date_range = ReportingPeriod(
            start=datetime.now().isoformat(),
            end=(datetime.now() + timedelta(days=7)).isoformat(),
        )
        result = adapter.get_media_buy_delivery("mb_test_123", date_range, datetime.now())

        # Assert
        assert result is not None
        assert result.media_buy_id == "mb_test_123"
        assert result.totals.impressions == 8000
        assert result.totals.spend == 400.0
        assert result.totals.clicks == 80
        assert len(result.by_package) == 2  # Should have package-level breakdowns

        # Verify package-level metrics
        pkg_1 = next(p for p in result.by_package if p.package_id == "pkg_1")
        assert pkg_1.impressions == 5000
        assert pkg_1.spend == 250.0

        pkg_2 = next(p for p in result.by_package if p.package_id == "pkg_2")
        assert pkg_2.impressions == 3000
        assert pkg_2.spend == 150.0
