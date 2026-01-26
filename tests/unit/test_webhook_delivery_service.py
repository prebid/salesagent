"""Unit tests for webhook delivery service.

Tests the thread-safe webhook delivery service that's shared by all adapters.
"""

import threading
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from src.services.webhook_delivery_service import CircuitState, WebhookDeliveryService


@pytest.fixture
def webhook_service():
    """Create a fresh webhook service for each test."""
    return WebhookDeliveryService()


@pytest.fixture
def mock_db_session(mocker):
    """Mock database session for SQLAlchemy 2.0 (select() + scalars())."""
    mock_session = MagicMock()

    # Mock SQLAlchemy 2.0 pattern: session.scalars(stmt).all()
    mock_scalars = MagicMock()
    mock_scalars.all.return_value = []  # No webhooks configured by default
    mock_session.scalars.return_value = mock_scalars

    # Mock the database session context manager
    mock_context = MagicMock()
    mock_context.__enter__.return_value = mock_session
    mock_context.__exit__.return_value = None

    mocker.patch("src.core.database.database_session.get_db_session", return_value=mock_context)
    return mock_session


def test_sequence_number_increments(webhook_service, mock_db_session):
    """Test that sequence numbers increment correctly."""
    media_buy_id = "buy_123"
    start_time = datetime.now(UTC)

    # Send 3 webhooks
    for _ in range(3):
        webhook_service.send_delivery_webhook(
            media_buy_id=media_buy_id,
            tenant_id="tenant1",
            principal_id="principal1",
            reporting_period_start=start_time,
            reporting_period_end=start_time,
            impressions=1000,
            spend=100.0,
        )

    # Sequence should be at 3
    with webhook_service._lock:
        assert webhook_service._sequence_numbers[media_buy_id] == 3


def test_thread_safety(webhook_service, mock_db_session):
    """Test that service is thread-safe with concurrent calls."""
    media_buy_id = "buy_concurrent"
    start_time = datetime.now(UTC)
    num_threads = 10

    def send_webhook():
        webhook_service.send_delivery_webhook(
            media_buy_id=media_buy_id,
            tenant_id="tenant1",
            principal_id="principal1",
            reporting_period_start=start_time,
            reporting_period_end=start_time,
            impressions=1000,
            spend=100.0,
        )

    # Send webhooks from multiple threads
    threads = [threading.Thread(target=send_webhook) for _ in range(num_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Should have exactly num_threads webhooks sent
    with webhook_service._lock:
        assert webhook_service._sequence_numbers[media_buy_id] == num_threads


def test_adcp_payload_structure(webhook_service, mock_db_session):
    """Test that payload follows AdCP V2.3 structure with enhanced security (PR #86)."""
    media_buy_id = "buy_adcp"
    start_time = datetime.now(UTC)

    # Mock httpx to capture the payload
    with patch("src.services.webhook_delivery_service.httpx.Client") as mock_client:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_client.return_value.__enter__.return_value.post.return_value = mock_response

        # Mock webhook config
        mock_config = MagicMock()
        mock_config.url = "https://example.com/webhook"
        mock_config.authentication_type = None
        mock_config.validation_token = None
        mock_config.webhook_secret = None  # No HMAC for this test

        # Update mock to return config for SQLAlchemy 2.0
        mock_db_session.scalars.return_value.all.return_value = [mock_config]

        # Send webhook
        webhook_service.send_delivery_webhook(
            media_buy_id=media_buy_id,
            tenant_id="tenant1",
            principal_id="principal1",
            reporting_period_start=start_time,
            reporting_period_end=start_time,
            impressions=5000,
            spend=500.0,
            clicks=50,
            ctr=0.01,
            is_final=False,
            next_expected_interval_seconds=60.0,
        )

        # Verify httpx was called
        assert mock_client.return_value.__enter__.return_value.post.called
        call_args = mock_client.return_value.__enter__.return_value.post.call_args

        # Check new payload structure (PR #86 - no wrapper, direct payload)
        # Version should match what's reported by the adcp library
        from adcp import get_adcp_version

        payload = call_args.kwargs["json"]
        assert payload["adcp_version"] == get_adcp_version()
        assert payload["notification_type"] == "scheduled"
        assert payload["is_adjusted"] is False  # NEW in PR #86
        assert payload["sequence_number"] == 1
        assert "reporting_period" in payload
        assert payload["reporting_period"]["start"] == start_time.isoformat()
        assert "media_buy_deliveries" in payload
        assert len(payload["media_buy_deliveries"]) == 1

        # Check delivery data
        delivery = payload["media_buy_deliveries"][0]
        assert delivery["media_buy_id"] == media_buy_id
        assert delivery["status"] == "active"
        assert delivery["totals"]["impressions"] == 5000
        assert delivery["totals"]["spend"] == 500.0
        assert delivery["totals"]["clicks"] == 50
        assert delivery["totals"]["ctr"] == 0.01


def test_final_notification_type(webhook_service, mock_db_session):
    """Test that is_final sets notification_type to 'final' (PR #86)."""
    media_buy_id = "buy_final"
    start_time = datetime.now(UTC)

    with patch("src.services.webhook_delivery_service.httpx.Client") as mock_client:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_client.return_value.__enter__.return_value.post.return_value = mock_response

        mock_config = MagicMock()
        mock_config.url = "https://example.com/webhook"
        mock_config.authentication_type = None
        mock_config.validation_token = None
        mock_config.webhook_secret = None
        mock_db_session.scalars.return_value.all.return_value = [mock_config]

        # Send final webhook
        webhook_service.send_delivery_webhook(
            media_buy_id=media_buy_id,
            tenant_id="tenant1",
            principal_id="principal1",
            reporting_period_start=start_time,
            reporting_period_end=start_time,
            impressions=10000,
            spend=1000.0,
            status="completed",
            is_final=True,
        )

        # Check notification_type (direct payload structure in PR #86)
        payload = mock_client.return_value.__enter__.return_value.post.call_args.kwargs["json"]
        assert payload["notification_type"] == "final"
        assert payload["is_adjusted"] is False
        assert "next_expected_at" not in payload


def test_reset_sequence(webhook_service, mock_db_session):
    """Test that reset_sequence clears sequence numbers (PR #86)."""
    media_buy_id = "buy_reset"
    start_time = datetime.now(UTC)

    # Send 3 webhooks
    for _ in range(3):
        webhook_service.send_delivery_webhook(
            media_buy_id=media_buy_id,
            tenant_id="tenant1",
            principal_id="principal1",
            reporting_period_start=start_time,
            reporting_period_end=start_time,
            impressions=1000,
            spend=100.0,
        )

    # Reset
    webhook_service.reset_sequence(media_buy_id)

    # Verify sequence number cleared (PR #86: failure tracking is per-endpoint via circuit breakers)
    with webhook_service._lock:
        assert media_buy_id not in webhook_service._sequence_numbers


def test_failure_tracking(webhook_service, mock_db_session):
    """Test that failures are tracked correctly with circuit breaker (PR #86)."""
    media_buy_id = "buy_fail"
    start_time = datetime.now(UTC)

    with patch("src.services.webhook_delivery_service.httpx.Client") as mock_client:
        # First call succeeds
        mock_response_ok = MagicMock()
        mock_response_ok.status_code = 200

        # Second call fails (with retries)
        mock_response_fail = MagicMock()
        mock_response_fail.status_code = 500

        # Mock will be called 3 times total (1 success, then 2 failure attempts with retries)
        mock_client.return_value.__enter__.return_value.post.side_effect = [
            mock_response_ok,  # First webhook succeeds
            mock_response_fail,  # Second webhook attempt 1 fails
            mock_response_fail,  # Second webhook attempt 2 fails (retry)
            mock_response_fail,  # Second webhook attempt 3 fails (retry)
        ]

        mock_config = MagicMock()
        mock_config.url = "https://example.com/webhook"
        mock_config.authentication_type = None
        mock_config.validation_token = None
        mock_config.webhook_secret = None
        mock_db_session.scalars.return_value.all.return_value = [mock_config]

        # First webhook - success
        result1 = webhook_service.send_delivery_webhook(
            media_buy_id=media_buy_id,
            tenant_id="tenant1",
            principal_id="principal1",
            reporting_period_start=start_time,
            reporting_period_end=start_time,
            impressions=1000,
            spend=100.0,
        )
        assert result1 is True

        # Check circuit breaker state after success (should be CLOSED)
        endpoint_key = "tenant1:https://example.com/webhook"
        state, failures = webhook_service.get_circuit_breaker_state(endpoint_key)
        assert state == CircuitState.CLOSED
        assert failures == 0

        # Second webhook - failure (will retry 3 times)
        result2 = webhook_service.send_delivery_webhook(
            media_buy_id=media_buy_id,
            tenant_id="tenant1",
            principal_id="principal1",
            reporting_period_start=start_time,
            reporting_period_end=start_time,
            impressions=2000,
            spend=200.0,
        )
        assert result2 is False

        # Check circuit breaker recorded the failure
        state, failures = webhook_service.get_circuit_breaker_state(endpoint_key)
        assert state == CircuitState.CLOSED  # Still closed (threshold is 5)
        assert failures == 1


def test_authentication_headers(webhook_service, mock_db_session):
    """Test that authentication headers are set correctly (PR #86)."""
    media_buy_id = "buy_auth"
    start_time = datetime.now(UTC)

    with patch("src.services.webhook_delivery_service.httpx.Client") as mock_client:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_client.return_value.__enter__.return_value.post.return_value = mock_response

        # Test bearer auth
        mock_config = MagicMock()
        mock_config.url = "https://example.com/webhook"
        mock_config.authentication_type = "bearer"
        mock_config.authentication_token = "secret_token"
        mock_config.validation_token = "validation_token"
        mock_config.webhook_secret = None
        mock_db_session.scalars.return_value.all.return_value = [mock_config]

        webhook_service.send_delivery_webhook(
            media_buy_id=media_buy_id,
            tenant_id="tenant1",
            principal_id="principal1",
            reporting_period_start=start_time,
            reporting_period_end=start_time,
            impressions=1000,
            spend=100.0,
        )

        # Verify headers (PR #86 added X-ADCP-Timestamp, no longer uses X-Webhook-Token)
        call_args = mock_client.return_value.__enter__.return_value.post.call_args
        headers = call_args.kwargs["headers"]
        assert headers["Authorization"] == "Bearer secret_token"
        assert "X-ADCP-Timestamp" in headers  # NEW in PR #86


def test_no_webhooks_configured(webhook_service, mock_db_session):
    """Test behavior when no webhooks are configured."""
    media_buy_id = "buy_no_config"
    start_time = datetime.now(UTC)

    # No webhooks configured (default mock behavior)
    result = webhook_service.send_delivery_webhook(
        media_buy_id=media_buy_id,
        tenant_id="tenant1",
        principal_id="principal1",
        reporting_period_start=start_time,
        reporting_period_end=start_time,
        impressions=1000,
        spend=100.0,
    )

    # Should return False but not error
    assert result is False
