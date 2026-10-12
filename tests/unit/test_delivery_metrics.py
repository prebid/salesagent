"""Tests for get_media_buy_delivery with real GAM metrics."""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, Mock, patch

import pytest

from src.adapters.google_ad_manager import GoogleAdManager
from src.core.schemas import Principal, ReportingPeriod


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
        tenant_id="test_tenant",
    )


def test_get_media_buy_delivery_media_buy_not_found(gam_adapter):
    """Test get_media_buy_delivery handles missing media buy gracefully."""
    # Setup
    media_buy_id = "mb_nonexistent"
    date_range = ReportingPeriod(
        start=datetime.now(UTC),
        end=datetime.now(UTC) + timedelta(days=7),
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
