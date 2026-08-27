"""Unit tests for webhook delivery service.

Tests the thread-safe webhook delivery service that's shared by all adapters.
"""

import threading
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from src.services.webhook_delivery_service import CircuitState, WebhookDeliveryService
from tests.helpers.webhook_wire import capture_outbound_webhooks, constructed_http_clients


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
    # ...and .first() is the SIGNING-KEY lookup the delivery boundary makes on the
    # same session (#1291 C1). None = this tenant holds no key, so deliveries take
    # the honest unsigned path instead of trying to sign with a MagicMock.
    mock_scalars.first.return_value = None
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

    # Capture the real outbound request (only the socket is stubbed)
    with capture_outbound_webhooks() as captured:
        # Mock webhook config
        mock_config = MagicMock()
        mock_config.url = "https://example.com/webhook"
        mock_config.authentication_type = None
        mock_config.authentication_token = None
        mock_config.validation_token = None

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

        # Verify the webhook went out
        assert len(captured) == 1

        # Check new payload structure (PR #86 - no wrapper, direct payload)
        # Version should match what's reported by the adcp library
        from adcp import get_adcp_spec_version

        payload = captured[0].payload
        assert payload["adcp_version"] == get_adcp_spec_version()
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

    with capture_outbound_webhooks() as captured:
        mock_config = MagicMock()
        mock_config.url = "https://example.com/webhook"
        mock_config.authentication_type = None
        mock_config.authentication_token = None
        mock_config.validation_token = None
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
        assert len(captured) == 1
        payload = captured[0].payload
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


@patch("src.services.webhook_delivery_service.time.sleep")
def test_failure_tracking(mock_sleep, webhook_service, mock_db_session):
    """Test that failures are tracked correctly with circuit breaker (PR #86)."""
    media_buy_id = "buy_fail"
    start_time = datetime.now(UTC)

    # The receiver accepts the first webhook, then rejects every attempt of the
    # second (1 success, then 3 failing attempts with retries).
    with capture_outbound_webhooks(status_codes=(200, 500, 500, 500)) as captured:
        mock_config = MagicMock()
        mock_config.url = "https://example.com/webhook"
        mock_config.authentication_type = None
        mock_config.authentication_token = None
        mock_config.validation_token = None
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

    with capture_outbound_webhooks() as captured:
        # Test bearer auth
        mock_config = MagicMock()
        mock_config.url = "https://example.com/webhook"
        mock_config.authentication_type = "bearer"
        mock_config.authentication_token = "secret_token"
        mock_config.validation_token = "validation_token"
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

        # Verify headers. The registration says `bearer`, so the boundary must
        # select the legacy token mode (#1291 C1) — the credential rides, and NO
        # RFC 9421 signature does (security.mdx @ v3.1.1 :1425 forbids both ways).
        assert len(captured) == 1
        headers = captured[0].headers
        assert headers["authorization"] == "Bearer secret_token"
        assert "signature-input" not in headers


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


def test_deliver_rejects_metadata_url_without_post(webhook_service, mock_db_session, monkeypatch):
    """Outbound SSRF gate must refuse cloud-metadata URLs before httpx POST."""
    monkeypatch.delenv("ADCP_TESTING", raising=False)
    start_time = datetime.now(UTC)

    mock_config = MagicMock()
    mock_config.url = "http://169.254.169.254/latest/meta-data/"
    mock_config.authentication_type = None
    mock_config.authentication_token = None
    mock_config.validation_token = None
    mock_config.webhook_secret = None
    mock_db_session.scalars.return_value.all.return_value = [mock_config]

    # Graded on the SOCKET, not on a constructor mock: "no POST left the process" has
    # to stay true however the delivery path builds its client, and an
    # ``assert_not_called`` on a client this path no longer constructs would pass even
    # with the SSRF gate deleted (#1291 C1 relocated the client into the signing
    # boundary).
    with capture_outbound_webhooks() as captured:
        result = webhook_service.send_delivery_webhook(
            media_buy_id="buy_ssrf",
            tenant_id="tenant1",
            principal_id="principal1",
            reporting_period_start=start_time,
            reporting_period_end=start_time,
            impressions=1000,
            spend=100.0,
        )

    assert result is False
    assert captured == []
    endpoint_key = f"tenant1:{mock_config.url}"
    breaker = webhook_service._circuit_breakers[endpoint_key]
    assert breaker.failure_count == 1


def test_deliver_disables_httpx_redirects(webhook_service, mock_db_session):
    """The delivering client must refuse redirects, to prevent open-redirect SSRF.

    Graded on the client INSTANCE the send path actually constructs rather than on a
    constructor mock's call args. #1291 C1 moved delivery off the service's own
    ``httpx.Client`` onto the signing boundary's ``AsyncClient``, and an
    ``assert_called_with(follow_redirects=False)`` cannot survive that move: the
    boundary leaves httpx's already-``False`` default alone instead of naming the
    kwarg, so the obligation still holds while the constructor assertion goes red.
    """
    start_time = datetime.now(UTC)

    mock_config = MagicMock()
    mock_config.url = "https://example.com/webhook"
    mock_config.authentication_type = None
    mock_config.authentication_token = None
    mock_config.validation_token = None
    mock_config.webhook_secret = None
    mock_db_session.scalars.return_value.all.return_value = [mock_config]

    with (
        patch(
            "src.core.webhook_validator.WebhookURLValidator.validate_outbound_webhook_url",
            return_value=(True, ""),
        ),
        constructed_http_clients() as built,
        capture_outbound_webhooks() as captured,
    ):
        webhook_service.send_delivery_webhook(
            media_buy_id="buy_redir",
            tenant_id="tenant1",
            principal_id="principal1",
            reporting_period_start=start_time,
            reporting_period_end=start_time,
            impressions=1000,
            spend=100.0,
        )

    # "The webhook was sent at all" is asserted first — otherwise a send path that
    # silently stopped delivering would satisfy every redirect assertion below by
    # constructing nothing.
    assert len(captured) == 1
    assert built, "no HTTP client was constructed for the delivery webhook"
    assert all(client.follow_redirects is False for client in built)
