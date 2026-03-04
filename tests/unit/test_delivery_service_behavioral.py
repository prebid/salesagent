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

from datetime import UTC, date, datetime, timedelta
from unittest.mock import MagicMock, Mock, patch

import pytest

from src.core.resolved_identity import ResolvedIdentity
from src.core.schemas import (
    AdapterGetMediaBuyDeliveryResponse,
    AdapterPackageDelivery,
    DeliveryTotals,
    GetMediaBuyDeliveryRequest,
    ReportingPeriod,
)
from src.core.testing_hooks import AdCPTestContext
from src.core.tools.media_buy_delivery import (
    _get_media_buy_delivery_impl,
)
from src.core.webhook_delivery import WebhookDelivery, deliver_webhook_with_retry
from src.services.webhook_delivery_service import (
    CircuitBreaker,
    CircuitState,
    WebhookDeliveryService,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PATCH = "src.core.tools.media_buy_delivery"


def _make_identity(
    principal_id: str = "test_principal",
    tenant_id: str = "test_tenant",
) -> ResolvedIdentity:
    return ResolvedIdentity(
        principal_id=principal_id,
        tenant_id=tenant_id,
        tenant={"tenant_id": tenant_id, "name": "Test Tenant"},
        protocol="mcp",
        testing_context=AdCPTestContext(
            dry_run=False,
            mock_time=None,
            jump_to_event=None,
            test_session_id=None,
        ),
    )


def _make_buy(
    media_buy_id: str = "mb_001",
    buyer_ref: str | None = "ref_001",
    start_date: date = date(2025, 1, 1),
    end_date: date = date(2025, 12, 31),
    budget: float = 10000.0,
    currency: str = "USD",
    raw_request: dict | None = None,
) -> MagicMock:
    """Create a mock MediaBuy ORM object."""
    buy = MagicMock()
    buy.media_buy_id = media_buy_id
    buy.buyer_ref = buyer_ref
    buy.start_date = start_date
    buy.end_date = end_date
    buy.start_time = None
    buy.end_time = None
    buy.budget = budget
    buy.currency = currency
    buy.raw_request = raw_request or {
        "buyer_ref": buyer_ref,
        "packages": [{"package_id": "pkg_001", "product_id": "prod_001"}],
    }
    return buy


def _make_adapter_response(
    media_buy_id: str = "mb_001",
    impressions: int = 5000,
    spend: float = 250.0,
    package_id: str = "pkg_001",
) -> AdapterGetMediaBuyDeliveryResponse:
    return AdapterGetMediaBuyDeliveryResponse(
        media_buy_id=media_buy_id,
        reporting_period=ReportingPeriod(
            start=datetime(2025, 1, 1, tzinfo=UTC),
            end=datetime(2025, 12, 31, tzinfo=UTC),
        ),
        totals=DeliveryTotals(impressions=float(impressions), spend=spend),
        by_package=[AdapterPackageDelivery(package_id=package_id, impressions=impressions, spend=spend)],
        currency="USD",
    )


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
        cb = CircuitBreaker(failure_threshold=3)

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
        cb = CircuitBreaker(failure_threshold=3)

        for _ in range(3):
            cb.record_failure()

        assert cb.state == CircuitState.OPEN

        assert cb.can_attempt() is False
        assert cb.can_attempt() is False
        assert cb.can_attempt() is False

    @pytest.mark.xfail(
        reason="reporting_delayed status is not set by _get_media_buy_delivery_impl "
        "when circuit breaker is open. The schema supports 'reporting_delayed' "
        "(src/core/schemas/delivery.py:224) and the circuit breaker logic exists in "
        "src/services/webhook_delivery_service.py:40-121, but there is no integration "
        "between the circuit breaker state and the delivery status computation in "
        "src/core/tools/media_buy_delivery.py:219-230.",
        strict=True,
    )
    @patch(f"{_PATCH}._get_pricing_options", return_value={})
    @patch(f"{_PATCH}.get_adapter")
    @patch(f"{_PATCH}.get_principal_object")
    @patch(f"{_PATCH}.MediaBuyUoW")
    def test_delivery_marked_reporting_delayed_when_circuit_open(
        self,
        mock_uow_cls,
        mock_get_principal,
        mock_get_adapter,
        mock_get_pricing,
    ):
        """Delivery should be marked reporting_delayed when circuit breaker is open.

        Covers: UC-004-EXT-G-03
        """
        identity = _make_identity()
        buy = _make_buy(media_buy_id="mb_001")

        mock_get_principal.return_value = MagicMock()
        mock_get_adapter.return_value = MagicMock()

        mock_repo = MagicMock()
        mock_repo.get_by_principal.return_value = [buy]
        mock_repo.get_packages.return_value = []

        mock_uow = MagicMock()
        mock_uow.media_buys = mock_repo
        mock_uow.__enter__ = MagicMock(return_value=mock_uow)
        mock_uow.__exit__ = MagicMock(return_value=False)
        mock_uow_cls.return_value = mock_uow

        req = GetMediaBuyDeliveryRequest(
            media_buy_ids=["mb_001"],
            start_date="2025-01-01",
            end_date="2025-06-30",
        )

        response = _get_media_buy_delivery_impl(req, identity)

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
        cb = CircuitBreaker(failure_threshold=3, success_threshold=2, timeout_seconds=60)

        # Force circuit into OPEN state with a failure time in the past
        cb.state = CircuitState.OPEN
        cb.last_failure_time = datetime.now(UTC) - timedelta(seconds=120)

        # When can_attempt is called (simulating the timer/probe check)
        result = cb.can_attempt()

        # Then the circuit transitions to HALF_OPEN and allows the probe
        assert result is True
        assert cb.state == CircuitState.HALF_OPEN
        assert cb.success_count == 0  # Reset for tracking recovery successes

    def test_open_circuit_stays_open_before_timeout(self):
        """Given an OPEN circuit breaker whose timeout has NOT elapsed,
        can_attempt() should return False and stay OPEN."""
        cb = CircuitBreaker(failure_threshold=3, success_threshold=2, timeout_seconds=60)

        cb.state = CircuitState.OPEN
        cb.last_failure_time = datetime.now(UTC) - timedelta(seconds=30)

        result = cb.can_attempt()

        assert result is False
        assert cb.state == CircuitState.OPEN

    def test_half_open_probe_success_path(self):
        """After transitioning to HALF_OPEN, a successful probe should be recorded.
        With enough successes, the circuit closes."""
        cb = CircuitBreaker(failure_threshold=3, success_threshold=2, timeout_seconds=60)

        # Transition to HALF_OPEN via timeout expiry
        cb.state = CircuitState.OPEN
        cb.last_failure_time = datetime.now(UTC) - timedelta(seconds=120)
        assert cb.can_attempt() is True
        assert cb.state == CircuitState.HALF_OPEN

        # First successful probe
        cb.record_success()
        assert cb.state == CircuitState.HALF_OPEN  # Not yet enough successes
        assert cb.success_count == 1

        # Second successful probe -> closes the circuit
        cb.record_success()
        assert cb.state == CircuitState.CLOSED

    def test_half_open_probe_failure_reopens_circuit(self):
        """After transitioning to HALF_OPEN, a failed probe should reopen the circuit."""
        cb = CircuitBreaker(failure_threshold=3, success_threshold=2, timeout_seconds=60)

        # Transition to HALF_OPEN
        cb.state = CircuitState.OPEN
        cb.last_failure_time = datetime.now(UTC) - timedelta(seconds=120)
        assert cb.can_attempt() is True
        assert cb.state == CircuitState.HALF_OPEN

        # Probe fails
        cb.record_failure()
        assert cb.state == CircuitState.OPEN

    def test_full_open_to_halfopen_via_failures_then_timeout(self):
        """End-to-end: circuit starts CLOSED, accumulates failures to go OPEN,
        then after timeout transitions to HALF_OPEN on next can_attempt()."""
        cb = CircuitBreaker(failure_threshold=3, success_threshold=2, timeout_seconds=60)
        assert cb.state == CircuitState.CLOSED

        # Accumulate failures to trip the circuit
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitState.CLOSED  # Not yet at threshold

        cb.record_failure()
        assert cb.state == CircuitState.OPEN  # Tripped

        # Simulate time passing beyond timeout
        cb.last_failure_time = datetime.now(UTC) - timedelta(seconds=61)

        # Next attempt should transition to HALF_OPEN (the half-open probe)
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

    @pytest.mark.xfail(
        reason=(
            "No explicit auth-failure-blocks-until-reconfigured guard exists. "
            "deliver_webhook_with_retry treats 401/403 as generic 4xx (no retry), "
            "and the circuit breaker does not distinguish auth failures from other "
            "errors. Recovery via UC-003 credential update is not enforced."
        ),
        strict=False,
    )
    def test_auth_failure_blocks_delivery_until_credentials_reconfigured(self):
        """401/403 webhook failure should block delivery until credentials are reconfigured.

        Covers: UC-004-EXT-G-07

        Tests the full recovery cycle:
        1. Webhook delivery fails with 401 (auth failure)
        2. Subsequent deliveries are blocked (circuit breaker opens)
        3. Buyer reconfigures credentials via UC-003
        4. Delivery resumes with new credentials

        The missing behavior: step 3 should be REQUIRED before step 4 can succeed.
        Currently, the circuit breaker auto-recovers after timeout without requiring
        credential reconfiguration.
        """
        # --- Step 1: Deliver webhook, receive 401 (auth failure) ---
        mock_response_401 = Mock()
        mock_response_401.status_code = 401
        mock_response_401.text = "Unauthorized: invalid credentials"

        delivery = WebhookDelivery(
            webhook_url="https://buyer.example.com/webhook",
            payload={"media_buy_id": "mb_001", "status": "active"},
            headers={"Content-Type": "application/json"},
            max_retries=3,
            timeout=10,
            event_type="delivery.update",
            tenant_id="test_tenant",
            object_id="mb_001",
        )

        with (
            patch("src.core.webhook_delivery.requests.post", return_value=mock_response_401),
            patch(
                "src.core.webhook_delivery.WebhookURLValidator.validate_webhook_url",
                return_value=(True, None),
            ),
            patch("src.core.webhook_delivery._create_delivery_record"),
            patch("src.core.webhook_delivery._update_delivery_record"),
        ):
            success, result = deliver_webhook_with_retry(delivery)

        # VERIFY: 401 causes immediate failure (no retry for 4xx)
        assert success is False
        assert result["status"] == "failed"
        assert result["response_code"] == 401
        assert result["attempts"] == 1  # No retry for client errors
        assert "Client error 401" in result["error"]

        # --- Step 2: Circuit breaker should open after auth failures ---
        cb = CircuitBreaker(failure_threshold=3)

        # Simulate repeated 401 failures (as would happen with bad credentials)
        for _ in range(3):
            cb.record_failure()

        assert cb.state == CircuitState.OPEN, "Circuit breaker should be OPEN after repeated auth failures"
        assert cb.can_attempt() is False, "Delivery should be blocked while circuit is OPEN"

        # --- Step 3: This is where the MISSING behavior would be ---
        # The buyer should be REQUIRED to reconfigure credentials via UC-003
        # (update_media_buy with new push_notification_config) before delivery
        # can resume. Currently, the circuit breaker auto-recovers after timeout
        # without any credential check.

        # --- Step 4: After reconfiguration, delivery succeeds ---
        mock_response_200 = Mock()
        mock_response_200.status_code = 200
        mock_response_200.text = "OK"

        # Reset circuit breaker (simulating what would happen after credential update)
        cb_fresh = CircuitBreaker(failure_threshold=3)
        assert cb_fresh.state == CircuitState.CLOSED
        assert cb_fresh.can_attempt() is True

        delivery_after_reconfig = WebhookDelivery(
            webhook_url="https://buyer.example.com/webhook",
            payload={"media_buy_id": "mb_001", "status": "active"},
            headers={"Authorization": "Bearer new-valid-token"},
            max_retries=3,
            timeout=10,
            event_type="delivery.update",
            tenant_id="test_tenant",
            object_id="mb_001",
        )

        with (
            patch("src.core.webhook_delivery.requests.post", return_value=mock_response_200),
            patch(
                "src.core.webhook_delivery.WebhookURLValidator.validate_webhook_url",
                return_value=(True, None),
            ),
            patch("src.core.webhook_delivery._create_delivery_record"),
            patch("src.core.webhook_delivery._update_delivery_record"),
        ):
            success_after, result_after = deliver_webhook_with_retry(delivery_after_reconfig)

        assert success_after is True
        assert result_after["status"] == "delivered"

        # THE KEY MISSING ASSERTION: The system should enforce that
        # credential reconfiguration happened BEFORE allowing delivery to
        # resume. This pytest.xfail marks the gap.
        raise AssertionError(
            "No auth-failure-specific guard exists. The circuit breaker provides "
            "generic failure isolation but does not require credential reconfiguration "
            "via UC-003 before resuming delivery after 401/403."
        )

    def test_401_causes_immediate_failure_no_retry(self):
        """401 auth error is treated as 4xx client error: no retry.

        Covers: UC-004-EXT-G-07
        """
        mock_response = Mock()
        mock_response.status_code = 401
        mock_response.text = "Unauthorized"

        delivery = WebhookDelivery(
            webhook_url="https://buyer.example.com/webhook",
            payload={"event": "delivery.update", "media_buy_id": "mb_001"},
            headers={"Content-Type": "application/json"},
            max_retries=3,
            timeout=10,
            event_type="delivery.update",
            tenant_id="test_tenant",
            object_id="mb_001",
        )

        with (
            patch("src.core.webhook_delivery.requests.post", return_value=mock_response) as mock_post,
            patch(
                "src.core.webhook_delivery.WebhookURLValidator.validate_webhook_url",
                return_value=(True, None),
            ),
            patch("src.core.webhook_delivery._create_delivery_record"),
            patch("src.core.webhook_delivery._update_delivery_record") as mock_update,
        ):
            success, result = deliver_webhook_with_retry(delivery)

        # 401 -> immediate failure, single attempt, no retries
        assert success is False
        assert result["response_code"] == 401
        assert result["attempts"] == 1
        assert result["status"] == "failed"

        # Verify only ONE HTTP request was made (no retry)
        mock_post.assert_called_once()

        # Verify the failure was recorded in the database
        mock_update.assert_called_once()
        update_kwargs = mock_update.call_args
        assert update_kwargs[1]["status"] == "failed"
        assert update_kwargs[1]["response_code"] == 401

    def test_403_causes_immediate_failure_no_retry(self):
        """403 forbidden error is treated as 4xx client error: no retry.

        Covers: UC-004-EXT-G-07
        """
        mock_response = Mock()
        mock_response.status_code = 403
        mock_response.text = "Forbidden"

        delivery = WebhookDelivery(
            webhook_url="https://buyer.example.com/webhook",
            payload={"event": "delivery.update", "media_buy_id": "mb_001"},
            headers={"Authorization": "Bearer expired-token"},
            max_retries=3,
            timeout=10,
            event_type="delivery.update",
            tenant_id="test_tenant",
            object_id="mb_001",
        )

        with (
            patch("src.core.webhook_delivery.requests.post", return_value=mock_response) as mock_post,
            patch(
                "src.core.webhook_delivery.WebhookURLValidator.validate_webhook_url",
                return_value=(True, None),
            ),
            patch("src.core.webhook_delivery._create_delivery_record"),
            patch("src.core.webhook_delivery._update_delivery_record") as mock_update,
        ):
            success, result = deliver_webhook_with_retry(delivery)

        assert success is False
        assert result["response_code"] == 403
        assert result["attempts"] == 1
        assert result["status"] == "failed"
        mock_post.assert_called_once()

    def test_circuit_breaker_opens_after_repeated_auth_failures(self):
        """Circuit breaker opens after threshold auth failures, blocking delivery.

        Covers: UC-004-EXT-G-07
        """
        cb = CircuitBreaker(failure_threshold=3, success_threshold=2, timeout_seconds=60)

        # Initial state: CLOSED, attempts allowed
        assert cb.state == CircuitState.CLOSED
        assert cb.can_attempt() is True

        # Simulate 3 consecutive 401 failures
        cb.record_failure()  # failure 1
        assert cb.state == CircuitState.CLOSED
        cb.record_failure()  # failure 2
        assert cb.state == CircuitState.CLOSED
        cb.record_failure()  # failure 3: threshold reached

        # Circuit should now be OPEN
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
        service = WebhookDeliveryService()

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
        service = WebhookDeliveryService()

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
        service = WebhookDeliveryService()

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
# UC-004-MAIN-02
# ---------------------------------------------------------------------------
