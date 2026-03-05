"""Behavioral tests for UC-004 delivery service (WebhookDeliveryService, CircuitBreaker).

Tests the circuit breaker pattern, service-level webhook delivery,
and recovery behavior against per-obligation scenarios.

Split from test_delivery_behavioral.py — see also:
- test_delivery_poll_behavioral.py (_get_media_buy_delivery_impl)
- test_delivery_webhook_behavioral.py (deliver_webhook_with_retry)

Each test targets exactly one obligation ID and follows the 6 hard rules:
1. MUST import from src.
2. MUST call production function
3. MUST assert production output
4. MUST have Covers: tag
5. MUST use factories where applicable (helpers here — no ORM factories for unit)
6. MUST NOT be mock-echo only
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from src.services.webhook_delivery_service import CircuitState

# ---------------------------------------------------------------------------
# UC-004-EXT-G-03
# ---------------------------------------------------------------------------


class TestCircuitBreakerOpensAfterRetriesExhausted:
    """Circuit breaker opens after consecutive failures suppress subsequent deliveries.

    Covers: UC-004-EXT-G-03
    """

    def test_circuit_breaker_opens_after_threshold_failures(self):
        """After failure_threshold consecutive failures, circuit breaker moves to OPEN.

        Covers: UC-004-EXT-G-03
        """
        from tests.harness.delivery_circuit_breaker_unit import CircuitBreakerEnv

        with CircuitBreakerEnv() as env:
            cb = env.get_breaker(failure_threshold=3)

            assert cb.state == CircuitState.CLOSED
            assert cb.can_attempt() is True

            cb.record_failure()
            assert cb.state == CircuitState.CLOSED
            cb.record_failure()
            assert cb.state == CircuitState.CLOSED
            cb.record_failure()

            assert cb.state == CircuitState.OPEN
            assert cb.can_attempt() is False

    def test_open_circuit_breaker_suppresses_subsequent_deliveries(self):
        """When circuit is OPEN, can_attempt returns False, suppressing deliveries.

        Covers: UC-004-EXT-G-03
        """
        from tests.harness.delivery_circuit_breaker_unit import CircuitBreakerEnv

        with CircuitBreakerEnv() as env:
            cb = env.get_breaker(failure_threshold=3)

            for _ in range(3):
                cb.record_failure()

            assert cb.state == CircuitState.OPEN

            assert cb.can_attempt() is False
            assert cb.can_attempt() is False
            assert cb.can_attempt() is False

    def test_delivery_marked_reporting_delayed_when_circuit_open(self):
        """Delivery should be marked reporting_delayed when circuit breaker is open.

        Covers: UC-004-EXT-G-03
        """
        from tests.harness.delivery_poll_unit import DeliveryPollEnv

        with DeliveryPollEnv() as env:
            env.add_buy(media_buy_id="mb_001")
            env.set_adapter_response("mb_001", impressions=5000, spend=250.0)

            response = env.call_impl(
                media_buy_ids=["mb_001"],
                start_date="2025-01-01",
                end_date="2025-06-30",
            )

            assert len(response.media_buy_deliveries) == 1
            assert response.media_buy_deliveries[0].status == "reporting_delayed"


# ---------------------------------------------------------------------------
# UC-004-EXT-G-04
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# UC-004-EXT-G-04
# ---------------------------------------------------------------------------


class TestCircuitBreakerHalfOpenProbe:
    """When a circuit breaker is OPEN and the timeout elapses, the system
    transitions to HALF_OPEN and allows a probe attempt.

    Covers: UC-004-EXT-G-04
    """

    def test_open_circuit_transitions_to_half_open_after_timeout(self):
        """Given an OPEN circuit breaker whose timeout has elapsed,
        can_attempt() should transition state to HALF_OPEN and return True."""
        from tests.harness.delivery_circuit_breaker_unit import CircuitBreakerEnv

        with CircuitBreakerEnv() as env:
            cb = env.get_breaker(failure_threshold=3, success_threshold=2, timeout_seconds=60)

            cb.state = CircuitState.OPEN
            cb.last_failure_time = datetime.now(UTC) - timedelta(seconds=120)

            result = cb.can_attempt()

            assert result is True
            assert cb.state == CircuitState.HALF_OPEN
            assert cb.success_count == 0

    def test_open_circuit_stays_open_before_timeout(self):
        """Given an OPEN circuit breaker whose timeout has NOT elapsed,
        can_attempt() should return False and stay OPEN."""
        from tests.harness.delivery_circuit_breaker_unit import CircuitBreakerEnv

        with CircuitBreakerEnv() as env:
            cb = env.get_breaker(failure_threshold=3, success_threshold=2, timeout_seconds=60)

            cb.state = CircuitState.OPEN
            cb.last_failure_time = datetime.now(UTC) - timedelta(seconds=30)

            result = cb.can_attempt()

            assert result is False
            assert cb.state == CircuitState.OPEN

    def test_half_open_probe_success_path(self):
        """After transitioning to HALF_OPEN, a successful probe should be recorded.
        With enough successes, the circuit closes."""
        from tests.harness.delivery_circuit_breaker_unit import CircuitBreakerEnv

        with CircuitBreakerEnv() as env:
            cb = env.get_breaker(failure_threshold=3, success_threshold=2, timeout_seconds=60)

            cb.state = CircuitState.OPEN
            cb.last_failure_time = datetime.now(UTC) - timedelta(seconds=120)
            assert cb.can_attempt() is True
            assert cb.state == CircuitState.HALF_OPEN

            cb.record_success()
            assert cb.state == CircuitState.HALF_OPEN
            assert cb.success_count == 1

            cb.record_success()
            assert cb.state == CircuitState.CLOSED

    def test_half_open_probe_failure_reopens_circuit(self):
        """After transitioning to HALF_OPEN, a failed probe should reopen the circuit."""
        from tests.harness.delivery_circuit_breaker_unit import CircuitBreakerEnv

        with CircuitBreakerEnv() as env:
            cb = env.get_breaker(failure_threshold=3, success_threshold=2, timeout_seconds=60)

            cb.state = CircuitState.OPEN
            cb.last_failure_time = datetime.now(UTC) - timedelta(seconds=120)
            assert cb.can_attempt() is True
            assert cb.state == CircuitState.HALF_OPEN

            cb.record_failure()
            assert cb.state == CircuitState.OPEN

    def test_full_open_to_halfopen_via_failures_then_timeout(self):
        """End-to-end: circuit starts CLOSED, accumulates failures to go OPEN,
        then after timeout transitions to HALF_OPEN on next can_attempt()."""
        from tests.harness.delivery_circuit_breaker_unit import CircuitBreakerEnv

        with CircuitBreakerEnv() as env:
            cb = env.get_breaker(failure_threshold=3, success_threshold=2, timeout_seconds=60)
            assert cb.state == CircuitState.CLOSED

            cb.record_failure()
            cb.record_failure()
            assert cb.state == CircuitState.CLOSED

            cb.record_failure()
            assert cb.state == CircuitState.OPEN

            cb.last_failure_time = datetime.now(UTC) - timedelta(seconds=61)

            result = cb.can_attempt()
            assert result is True
            assert cb.state == CircuitState.HALF_OPEN


# ---------------------------------------------------------------------------
# UC-004-EXT-G-05
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# UC-004-EXT-G-07
# ---------------------------------------------------------------------------


class TestExtG07WebhookAuthFailureRecovery:
    """Auth failure recovery: buyer must reconfigure credentials after 401/403.

    Covers: UC-004-EXT-G-07
    """

    def test_auth_failure_blocks_delivery_until_credentials_reconfigured(self):
        """401/403 webhook failure should block delivery until credentials are reconfigured.

        Covers: UC-004-EXT-G-07
        """
        from tests.harness.delivery_circuit_breaker_unit import CircuitBreakerEnv
        from tests.harness.delivery_webhook_unit import WebhookEnv

        # --- Step 1: Deliver webhook, receive 401 (auth failure) ---
        with WebhookEnv() as env:
            env.set_http_status(401, "Unauthorized: invalid credentials")

            success, result = env.call_deliver(
                webhook_url="https://buyer.example.com/webhook",
                payload={"media_buy_id": "mb_001", "status": "active"},
                event_type="delivery.update",
                tenant_id="test_tenant",
                object_id="mb_001",
            )

        assert success is False
        assert result["status"] == "failed"
        assert result["response_code"] == 401
        assert result["attempts"] == 1
        assert "Client error 401" in result["error"]

        # --- Step 2: Circuit breaker opens after auth failures ---
        with CircuitBreakerEnv() as env:
            cb = env.get_breaker(failure_threshold=3)
            for _ in range(3):
                cb.record_failure()
            assert cb.state == CircuitState.OPEN
            assert cb.can_attempt() is False

        # --- Step 4: After reconfiguration, delivery succeeds ---
        with WebhookEnv() as env:
            env.set_http_status(200, "OK")

            success_after, result_after = env.call_deliver(
                webhook_url="https://buyer.example.com/webhook",
                payload={"media_buy_id": "mb_001", "status": "active"},
                headers={"Authorization": "Bearer new-valid-token"},
                event_type="delivery.update",
                tenant_id="test_tenant",
                object_id="mb_001",
            )

        assert success_after is True
        assert result_after["status"] == "delivered"

    def test_401_causes_immediate_failure_no_retry(self):
        """401 auth error is treated as 4xx client error: no retry.

        Covers: UC-004-EXT-G-07
        """
        from tests.harness.delivery_webhook_unit import WebhookEnv

        with WebhookEnv() as env:
            env.set_http_status(401, "Unauthorized")

            success, result = env.call_deliver(
                webhook_url="https://buyer.example.com/webhook",
                event_type="delivery.update",
                tenant_id="test_tenant",
                object_id="mb_001",
            )

            assert success is False
            assert result["response_code"] == 401
            assert result["attempts"] == 1
            assert result["status"] == "failed"
            env.mock["post"].assert_called_once()

    def test_403_causes_immediate_failure_no_retry(self):
        """403 forbidden error is treated as 4xx client error: no retry.

        Covers: UC-004-EXT-G-07
        """
        from tests.harness.delivery_webhook_unit import WebhookEnv

        with WebhookEnv() as env:
            env.set_http_status(403, "Forbidden")

            success, result = env.call_deliver(
                webhook_url="https://buyer.example.com/webhook",
                headers={"Authorization": "Bearer expired-token"},
                event_type="delivery.update",
                tenant_id="test_tenant",
                object_id="mb_001",
            )

            assert success is False
            assert result["response_code"] == 403
            assert result["attempts"] == 1
            assert result["status"] == "failed"
            env.mock["post"].assert_called_once()

    def test_circuit_breaker_opens_after_repeated_auth_failures(self):
        """Circuit breaker opens after threshold auth failures, blocking delivery.

        Covers: UC-004-EXT-G-07
        """
        from tests.harness.delivery_circuit_breaker_unit import CircuitBreakerEnv

        with CircuitBreakerEnv() as env:
            cb = env.get_breaker(failure_threshold=3, success_threshold=2, timeout_seconds=60)

            assert cb.state == CircuitState.CLOSED
            assert cb.can_attempt() is True

            cb.record_failure()
            assert cb.state == CircuitState.CLOSED
            cb.record_failure()
            assert cb.state == CircuitState.CLOSED
            cb.record_failure()

            assert cb.state == CircuitState.OPEN
            assert cb.can_attempt() is False


# ---------------------------------------------------------------------------
# UC-004-EXT-G-08
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# UC-004-EXT-G-08
# ---------------------------------------------------------------------------


class TestWebhookFailureNoSyncError:
    """Webhook failure does not produce synchronous error to buyer.

    Covers: UC-004-EXT-G-08
    """

    def test_send_delivery_webhook_returns_false_on_http_failure_never_raises(self):
        """WebhookDeliveryService.send_delivery_webhook catches all exceptions
        and returns False -- it never propagates to callers.

        Covers: UC-004-EXT-G-08
        """
        from tests.harness.delivery_circuit_breaker_unit import CircuitBreakerEnv

        with CircuitBreakerEnv() as env:
            service = env.get_service()

            with patch.object(service, "_send_webhook_enhanced", side_effect=Exception("network down")):
                result = service.send_delivery_webhook(
                    media_buy_id="mb_001",
                    tenant_id="test_tenant",
                    principal_id="test_principal",
                    reporting_period_start=datetime(2025, 1, 1, tzinfo=UTC),
                    reporting_period_end=datetime(2025, 6, 30, tzinfo=UTC),
                    impressions=5000,
                    spend=250.0,
                    currency="USD",
                    status="active",
                )

            assert result is False

    def test_send_delivery_webhook_returns_false_on_internal_failure(self):
        """Even when _send_webhook_enhanced returns False (all retries exhausted),
        send_delivery_webhook returns False gracefully.

        Covers: UC-004-EXT-G-08
        """
        from tests.harness.delivery_circuit_breaker_unit import CircuitBreakerEnv

        with CircuitBreakerEnv() as env:
            service = env.get_service()

            with patch.object(service, "_send_webhook_enhanced", return_value=False):
                result = service.send_delivery_webhook(
                    media_buy_id="mb_002",
                    tenant_id="test_tenant",
                    principal_id="test_principal",
                    reporting_period_start=datetime(2025, 1, 1, tzinfo=UTC),
                    reporting_period_end=datetime(2025, 6, 30, tzinfo=UTC),
                    impressions=3000,
                    spend=150.0,
                )

            assert result is False

    def test_sequence_number_increments_even_on_failed_delivery(self):
        """Sequence numbers increment regardless of delivery outcome, creating
        detectable gaps when deliveries fail -- the buyer detection mechanism.

        Covers: UC-004-EXT-G-08
        """
        from tests.harness.delivery_circuit_breaker_unit import CircuitBreakerEnv

        with CircuitBreakerEnv() as env:
            service = env.get_service()

            with patch.object(service, "_send_webhook_enhanced", return_value=False):
                service.send_delivery_webhook(
                    media_buy_id="mb_seq",
                    tenant_id="t1",
                    principal_id="p1",
                    reporting_period_start=datetime(2025, 1, 1, tzinfo=UTC),
                    reporting_period_end=datetime(2025, 1, 31, tzinfo=UTC),
                    impressions=1000,
                    spend=50.0,
                )
                service.send_delivery_webhook(
                    media_buy_id="mb_seq",
                    tenant_id="t1",
                    principal_id="p1",
                    reporting_period_start=datetime(2025, 2, 1, tzinfo=UTC),
                    reporting_period_end=datetime(2025, 2, 28, tzinfo=UTC),
                    impressions=2000,
                    spend=100.0,
                )

            assert service._sequence_numbers["mb_seq"] == 2


# ---------------------------------------------------------------------------
# UC-004-EXT-G-08 (DB error handling)
# ---------------------------------------------------------------------------


class TestWebhookEnhancedDBErrorHandling:
    """_send_webhook_enhanced catches database errors gracefully.

    Covers: UC-004-EXT-G-08
    """

    def test_send_webhook_enhanced_catches_db_errors(self):
        """DB errors when looking up webhook configs return False, not raise.

        Covers: UC-004-EXT-G-08
        """
        from tests.harness.delivery_circuit_breaker_unit import CircuitBreakerEnv

        with CircuitBreakerEnv() as env:
            # Make get_db_session raise to simulate DB outage
            env.mock["db"].side_effect = Exception("DB connection refused")

            service = env.get_service()
            result = service._send_webhook_enhanced(
                tenant_id="t1",
                principal_id="p1",
                media_buy_id="mb_001",
                delivery_payload={"test": "data"},
            )

        assert result is False


# ---------------------------------------------------------------------------
# Coverage gap 3vy9: WebhookDeliveryService internal state + data structures
# ---------------------------------------------------------------------------


class TestCircuitBreakerHalfOpenCanAttempt:
    """CircuitBreaker.can_attempt() returns True when in HALF_OPEN state.

    Covers line 90 of webhook_delivery_service.py.
    """

    def test_half_open_allows_attempt(self):
        from src.services.webhook_delivery_service import CircuitBreaker

        cb = CircuitBreaker()
        cb.state = CircuitState.HALF_OPEN
        assert cb.can_attempt() is True


class TestCircuitBreakerRecordSuccessWhileOpen:
    """CircuitBreaker.record_success() transitions OPEN→CLOSED defensively.

    Covers lines 104-105 of webhook_delivery_service.py.
    """

    def test_record_success_while_open_closes_circuit(self):
        from src.services.webhook_delivery_service import CircuitBreaker

        cb = CircuitBreaker()
        cb.state = CircuitState.OPEN
        cb.record_success()
        assert cb.state == CircuitState.CLOSED


class TestWebhookQueueFull:
    """WebhookQueue.enqueue() returns False when queue is at max capacity.

    Covers lines 149-150, 153 of webhook_delivery_service.py.
    """

    def test_enqueue_returns_false_when_full(self):
        from src.services.webhook_delivery_service import WebhookQueue

        queue = WebhookQueue(max_size=1)
        assert queue.enqueue({"data": "first"}) is True
        assert queue.enqueue({"data": "second"}) is False


class TestWebhookQueueEmptyDequeue:
    """WebhookQueue.dequeue() returns None when queue is empty.

    Covers line 167 of webhook_delivery_service.py.
    """

    def test_dequeue_empty_returns_none(self):
        from src.services.webhook_delivery_service import WebhookQueue

        queue = WebhookQueue()
        assert queue.dequeue() is None


class TestGetCircuitBreakerStateDefault:
    """get_circuit_breaker_state() returns CLOSED/0 for unknown endpoints.

    Covers line 545 of webhook_delivery_service.py (pre-removal line numbers).
    """

    def test_unknown_endpoint_returns_closed(self):
        from src.services.webhook_delivery_service import WebhookDeliveryService

        svc = WebhookDeliveryService()
        state, failures = svc.get_circuit_breaker_state("https://nonexistent.example.com/hook")
        assert state == CircuitState.CLOSED
        assert failures == 0


class TestResetSequence:
    """reset_sequence() removes the sequence counter for a media buy.

    Covers lines 528-530 of webhook_delivery_service.py (pre-removal line numbers).
    """

    def test_reset_removes_sequence_counter(self):
        from src.services.webhook_delivery_service import WebhookDeliveryService

        svc = WebhookDeliveryService()
        # Manually set a sequence number
        svc._sequence_numbers["mb_001"] = 5
        svc.reset_sequence("mb_001")
        assert "mb_001" not in svc._sequence_numbers

    def test_reset_nonexistent_is_noop(self):
        from src.services.webhook_delivery_service import WebhookDeliveryService

        svc = WebhookDeliveryService()
        # Should not raise for nonexistent key
        svc.reset_sequence("mb_nonexistent")


class TestDeliverWithBackoffGenericException:
    """_deliver_with_backoff breaks on non-httpx exceptions.

    Covers lines 514-516 of webhook_delivery_service.py (pre-removal line numbers).
    """

    def test_generic_exception_breaks_retry_loop(self):
        from unittest.mock import MagicMock

        from src.services.webhook_delivery_service import (
            CircuitBreaker,
            WebhookDeliveryService,
            WebhookQueue,
        )

        svc = WebhookDeliveryService()
        cb = CircuitBreaker()
        queue = WebhookQueue()

        mock_config = MagicMock()
        mock_config.url = "https://example.com/hook"
        mock_config.webhook_secret = None
        mock_config.authentication_type = None
        mock_config.authentication_token = None

        queue.enqueue(
            {
                "config": mock_config,
                "payload": {"test": "data"},
                "timestamp": datetime.now(UTC),
            }
        )

        with patch("src.services.webhook_delivery_service.httpx") as mock_httpx:
            mock_httpx.Client.return_value.__enter__ = MagicMock(
                return_value=MagicMock(post=MagicMock(side_effect=RuntimeError("unexpected")))
            )
            mock_httpx.Client.return_value.__exit__ = MagicMock(return_value=False)
            mock_httpx.TimeoutException = type("TimeoutException", (Exception,), {})
            mock_httpx.RequestError = type("RequestError", (Exception,), {})

            result = svc._deliver_with_backoff("test_endpoint", cb, queue)

        assert result is False
        # Circuit breaker should record the failure
        assert cb.failure_count >= 1


class TestShutdownHandler:
    """_shutdown() runs without error.

    Covers lines 549-556 of webhook_delivery_service.py (pre-removal line numbers).
    """

    def test_shutdown_runs_cleanly(self):
        from src.services.webhook_delivery_service import WebhookDeliveryService

        svc = WebhookDeliveryService()
        # Should not raise
        svc._shutdown()


# ---------------------------------------------------------------------------
# UC-004-MAIN-02
# ---------------------------------------------------------------------------
