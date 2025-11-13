"""Integration tests for delivery webhook scheduler."""

from unittest.mock import AsyncMock, Mock, patch

import pytest

from src.services.delivery_webhook_scheduler import DeliveryWebhookScheduler


@pytest.fixture
def scheduler():
    """Create a delivery webhook scheduler for testing."""
    return DeliveryWebhookScheduler()


@pytest.mark.asyncio
async def test_scheduler_start_stop(scheduler):
    """Test starting and stopping the scheduler."""
    # Start scheduler
    await scheduler.start()
    assert scheduler.is_running is True
    assert scheduler._task is not None

    # Stop scheduler
    await scheduler.stop()
    assert scheduler.is_running is False


@pytest.mark.asyncio
async def test_scheduler_start_when_already_running(scheduler):
    """Test that starting an already running scheduler is handled gracefully."""
    await scheduler.start()
    assert scheduler.is_running is True

    # Try to start again - should log warning but not fail
    await scheduler.start()
    assert scheduler.is_running is True

    # Cleanup
    await scheduler.stop()


@pytest.mark.asyncio
@patch("src.services.delivery_webhook_scheduler.get_db_session")
async def test_send_daily_reports_no_webhooks(mock_db_session, scheduler):
    """Test sending daily reports when no media buys have webhooks configured."""
    # Setup mock session with no media buys
    mock_session = Mock()
    mock_db_session.return_value.__enter__.return_value = mock_session
    mock_session.scalars.return_value.all.return_value = []

    # Execute
    await scheduler._send_daily_reports()

    # Should complete without errors


@pytest.mark.asyncio
@patch("src.services.delivery_webhook_scheduler.get_db_session")
async def test_send_daily_reports_with_media_buys(mock_db_session, scheduler):
    """Test sending daily reports for media buys with webhooks."""
    # Setup mock media buys
    mock_media_buy_1 = Mock()
    mock_media_buy_1.media_buy_id = "mb_1"
    mock_media_buy_1.raw_request = {
        "reporting_webhook": {
            "url": "https://example.com/webhook1",
        }
    }

    mock_media_buy_2 = Mock()
    mock_media_buy_2.media_buy_id = "mb_2"
    mock_media_buy_2.raw_request = {}  # No webhook

    mock_media_buy_3 = Mock()
    mock_media_buy_3.media_buy_id = "mb_3"
    mock_media_buy_3.raw_request = {
        "reporting_webhook": {
            "url": "https://example.com/webhook3",
        }
    }

    # Setup mock session
    mock_session = Mock()
    mock_db_session.return_value.__enter__.return_value = mock_session
    mock_session.scalars.return_value.all.return_value = [
        mock_media_buy_1,
        mock_media_buy_2,
        mock_media_buy_3,
    ]

    # Mock the send_report_for_media_buy method
    with patch.object(scheduler, "_send_report_for_media_buy", new_callable=AsyncMock) as mock_send:
        # Execute
        await scheduler._send_daily_reports()

        # Assert - should only send for media buys with webhooks (mb_1 and mb_3)
        assert mock_send.call_count == 2


# Removed - test was over-mocking internal _impl functions
