"""Integration behavioral tests for UC-004 delivery service (WebhookDeliveryService, CircuitBreaker).

Delivery runs against a REAL local HTTP origin (``CircuitBreakerEnv``): webhook
configs point at ``env.webhook_url``, and the assertions read what the endpoint
actually received. Only ``time.sleep`` and ``random.uniform`` are mocked, so the
backoff schedule stays observable and deterministic; the outbound transport is
not patched, which is what keeps these tests indifferent to whether delivery is
implemented with ``httpx`` directly or through the egress seam. DB operations
for PushNotificationConfig queries are real.

Pure CircuitBreaker state machine tests remain in the unit file.

Each test targets exactly one obligation ID and follows the 6 hard rules.
"""

from __future__ import annotations

import pytest

from src.services.webhook_delivery_service import (
    CircuitState,
)

# ---------------------------------------------------------------------------
# UC-004-EXT-G-03
# ---------------------------------------------------------------------------


@pytest.mark.requires_db
class TestCircuitBreakerServiceIntegration:
    """Service-level circuit breaker integration with real DB.

    Covers: UC-004-EXT-G-03
    """

    def test_service_skips_delivery_when_circuit_open(self, integration_db):
        """WebhookDeliveryService skips webhook send when circuit breaker is OPEN.

        Covers: UC-004-EXT-G-03
        """
        from datetime import UTC, datetime

        from tests.factories import (
            PrincipalFactory,
            PushNotificationConfigFactory,
            TenantFactory,
        )
        from tests.harness import CircuitBreakerEnv

        with CircuitBreakerEnv() as env:
            tenant = TenantFactory(tenant_id="t1")
            principal = PrincipalFactory(tenant=tenant, principal_id="p1")
            PushNotificationConfigFactory(
                tenant=tenant,
                principal=principal,
                url=env.webhook_url,
            )

            # Make HTTP fail to trip the circuit breaker
            env.set_http_response(500)
            service = env.get_service()

            start_time = datetime(2025, 6, 1, tzinfo=UTC)
            for i in range(5):
                service.send_delivery_webhook(
                    media_buy_id=f"mb_{i}",
                    tenant_id="t1",
                    principal_id="p1",
                    reporting_period_start=start_time,
                    reporting_period_end=start_time,
                    impressions=1000,
                    spend=100.0,
                )

            endpoint_key = env.endpoint_key("t1")
            state, _ = service.get_circuit_breaker_state(endpoint_key)
            assert state == CircuitState.OPEN

            # Everything after this point must leave the endpoint untouched.
            attempts_before_suppression = env.delivery_attempts

            result = service.send_delivery_webhook(
                media_buy_id="mb_suppressed",
                tenant_id="t1",
                principal_id="p1",
                reporting_period_start=start_time,
                reporting_period_end=start_time,
                impressions=1000,
                spend=100.0,
            )

            assert result is False
            assert env.delivery_attempts == attempts_before_suppression


# ---------------------------------------------------------------------------
# UC-004-EXT-G-04
# ---------------------------------------------------------------------------


@pytest.mark.requires_db
class TestCircuitBreakerHalfOpenProbeService:
    """Service-level circuit breaker half-open probe with real DB.

    Covers: UC-004-EXT-G-04
    """

    def test_service_allows_probe_after_circuit_breaker_timeout(self, integration_db):
        """WebhookDeliveryService uses circuit breaker can_attempt() to allow
        half-open probe after timeout expires.

        Covers: UC-004-EXT-G-04
        """
        from datetime import UTC, datetime, timedelta

        from src.services.webhook_delivery_service import CircuitBreaker
        from tests.harness import CircuitBreakerEnv

        with CircuitBreakerEnv() as env:
            service = env.get_service()

            endpoint_key = env.endpoint_key("t1")
            cb = CircuitBreaker(failure_threshold=3, success_threshold=2, timeout_seconds=60)
            cb.state = CircuitState.OPEN
            cb.last_failure_time = datetime.now(UTC) - timedelta(seconds=120)

            service._circuit_breakers[endpoint_key] = cb

            assert cb.can_attempt() is True
            assert cb.state == CircuitState.HALF_OPEN


# ---------------------------------------------------------------------------
# UC-004-EXT-G-08
# ---------------------------------------------------------------------------


@pytest.mark.requires_db
class TestWebhookFailureNoSyncError:
    """Webhook failure does not produce synchronous error to buyer.

    Covers: UC-004-EXT-G-08
    """

    def test_webhook_failure_does_not_affect_poll_response(self, integration_db):
        """Poll endpoint and webhook delivery are separate code paths.
        A webhook failure cannot propagate to the poll response.

        Covers: UC-004-EXT-G-08
        """
        from datetime import UTC, datetime
        from unittest.mock import patch

        from src.services.webhook_delivery_service import WebhookDeliveryService
        from tests.factories import MediaBuyFactory, PrincipalFactory, TenantFactory
        from tests.harness import DeliveryPollEnv

        # First: simulate webhook failure
        service = WebhookDeliveryService()
        with patch.object(service, "_send_webhook_enhanced", side_effect=Exception("timeout")):
            webhook_result = service.send_delivery_webhook(
                media_buy_id="mb_001",
                tenant_id="t1",
                principal_id="p1",
                reporting_period_start=datetime(2025, 1, 1, tzinfo=UTC),
                reporting_period_end=datetime(2025, 6, 30, tzinfo=UTC),
                impressions=5000,
                spend=250.0,
            )

        assert webhook_result is False

        # Then: poll should still work fine
        with DeliveryPollEnv(tenant_id="t1", principal_id="p1") as env:
            tenant = TenantFactory(tenant_id="t1")
            principal = PrincipalFactory(tenant=tenant, principal_id="p1")
            buy = MediaBuyFactory(tenant=tenant, principal=principal)
            env.set_adapter_response(buy.media_buy_id, impressions=5000, spend=250.0)

            response = env.call_impl(media_buy_ids=[buy.media_buy_id])

        assert len(response.media_buy_deliveries) == 1
        assert response.media_buy_deliveries[0].totals.impressions == 5000.0
        assert response.errors is None


# ---------------------------------------------------------------------------
# UC-004-EXT-G-07 (_send_webhook_enhanced: auth-blocked skip)
# ---------------------------------------------------------------------------


@pytest.mark.requires_db
class TestSendWebhookEnhancedAuthBlockedSkip:
    """Auth-blocked PushNotificationConfig is skipped by _send_webhook_enhanced.

    Covers: UC-004-EXT-G-07
    """

    def test_auth_blocked_config_skipped_no_http_request(self, integration_db):
        """When PushNotificationConfig has auth_blocked_at set, _send_webhook_enhanced
        skips it entirely and makes no HTTP request.

        Covers: UC-004-EXT-G-07
        """
        from datetime import UTC, datetime

        from tests.factories import (
            PrincipalFactory,
            PushNotificationConfigFactory,
            TenantFactory,
        )
        from tests.harness import CircuitBreakerEnv

        with CircuitBreakerEnv(tenant_id="t1", principal_id="p1") as env:
            tenant = TenantFactory(tenant_id="t1")
            principal = PrincipalFactory(tenant=tenant, principal_id="p1")
            PushNotificationConfigFactory(
                tenant=tenant,
                principal=principal,
                url=env.webhook_url,
                auth_blocked_at=datetime(2025, 6, 1, tzinfo=UTC),
            )

            env.set_http_response(200)
            service = env.get_service()
            result = service._send_webhook_enhanced(
                tenant_id="t1",
                principal_id="p1",
                media_buy_id="mb_001",
                delivery_payload={"test": "data"},
            )

            assert result is False
            assert env.delivery_attempts == 0


# ---------------------------------------------------------------------------
# UC-004-EXT-G-06 (_send_webhook_enhanced: HMAC signing)
# ---------------------------------------------------------------------------


@pytest.mark.requires_db
class TestSendWebhookEnhancedHmacSigning:
    """HMAC-SHA256 signature is added when the row ASKS for HMAC-SHA256.

    Both cases used to configure ``webhook_secret`` with no
    ``authentication_type`` at all, so they graded signing driven by "is a
    credential present" -- defect 2 of GH #1894, encoded as an expectation. Since
    GH #1802 the scheme gates it, and the secret comes from
    ``authentication_token``: the pair every writer in ``src/`` persists. The
    obligation is unchanged; the row is now one a buyer can create.

    Covers: UC-004-EXT-G-06
    """

    def test_hmac_signature_header_present_when_secret_configured(self, integration_db):
        """An HMAC-SHA256 row sets X-ADCP-Signature on the outgoing request.

        Covers: UC-004-EXT-G-06
        """
        from tests.factories import (
            PrincipalFactory,
            PushNotificationConfigFactory,
            TenantFactory,
        )
        from tests.harness import CircuitBreakerEnv

        with CircuitBreakerEnv(tenant_id="t1", principal_id="p1") as env:
            tenant = TenantFactory(tenant_id="t1")
            principal = PrincipalFactory(tenant=tenant, principal_id="p1")
            PushNotificationConfigFactory(
                tenant=tenant,
                principal=principal,
                url=env.webhook_url,
                authentication_type="HMAC-SHA256",
                authentication_token="a" * 32,
            )

            env.set_http_response(200)
            service = env.get_service()
            result = service._send_webhook_enhanced(
                tenant_id="t1",
                principal_id="p1",
                media_buy_id="mb_001",
                delivery_payload={"impressions": 5000, "spend": 250.0},
            )

            assert result is True
            assert env.delivery_attempts == 1
            sent_headers = env.last_delivery.headers
            assert "X-ADCP-Signature" in sent_headers
            assert len(sent_headers["X-ADCP-Signature"]) > 0

    def test_hmac_signature_valid_reproduces_from_payload(self, integration_db):
        """The HMAC signature can be reproduced over the raw bytes that crossed the socket.

        Recomputes over ``env.last_delivery.body`` — the bytes the origin
        actually received — rather than a fresh ``json.dumps`` of the payload
        dict. A recompute from the dict would use whatever serialization
        formula the test happens to pick, which can silently agree with a
        sender that signs one serialization and transmits another (the bug
        GH #1802 fixed); recomputing from the received bytes is the
        only form that can catch that divergence.

        Covers: UC-004-EXT-G-06
        """
        from tests.factories import (
            PrincipalFactory,
            PushNotificationConfigFactory,
            TenantFactory,
        )
        from tests.harness import CircuitBreakerEnv
        from tests.helpers import assert_signature_verifies_over_wire_body

        secret = "b" * 32
        payload = {"media_buy_id": "mb_001", "impressions": 5000}

        with CircuitBreakerEnv(tenant_id="t1", principal_id="p1") as env:
            tenant = TenantFactory(tenant_id="t1")
            principal = PrincipalFactory(tenant=tenant, principal_id="p1")
            PushNotificationConfigFactory(
                tenant=tenant,
                principal=principal,
                url=env.webhook_url,
                authentication_type="HMAC-SHA256",
                authentication_token=secret,
            )

            env.set_http_response(200)
            service = env.get_service()
            service._send_webhook_enhanced(
                tenant_id="t1",
                principal_id="p1",
                media_buy_id="mb_001",
                delivery_payload=payload,
            )

            assert_signature_verifies_over_wire_body(env.last_delivery, secret)


# ---------------------------------------------------------------------------
# UC-004-ALT-WEBHOOK-PUSH-REPORTING-08 (_send_webhook_enhanced: bearer auth)
# ---------------------------------------------------------------------------


@pytest.mark.requires_db
class TestSendWebhookEnhancedBearerAuth:
    """Bearer token authentication is set when configured on PushNotificationConfig.

    Covers: UC-004-ALT-WEBHOOK-PUSH-REPORTING-08
    """

    def test_bearer_token_sent_in_authorization_header(self, integration_db):
        """When authentication_type='Bearer' and authentication_token is set,
        Authorization header is sent with 'Bearer <token>'.

        Covers: UC-004-ALT-WEBHOOK-PUSH-REPORTING-08
        """
        from tests.factories import (
            PrincipalFactory,
            PushNotificationConfigFactory,
            TenantFactory,
        )
        from tests.harness import CircuitBreakerEnv

        with CircuitBreakerEnv(tenant_id="t1", principal_id="p1") as env:
            tenant = TenantFactory(tenant_id="t1")
            principal = PrincipalFactory(tenant=tenant, principal_id="p1")
            PushNotificationConfigFactory(
                tenant=tenant,
                principal=principal,
                url=env.webhook_url,
                # The PINNED spelling. This case grades that a configured Bearer
                # registration sends the header — not that casing is tolerated;
                # a lowercase row refuses, graded in test_order_approval_webhook.
                authentication_type="Bearer",
                authentication_token="my-secret-token-xyz-padded-to-32ch",
            )

            env.set_http_response(200)
            service = env.get_service()
            result = service._send_webhook_enhanced(
                tenant_id="t1",
                principal_id="p1",
                media_buy_id="mb_001",
                delivery_payload={"impressions": 5000},
            )

            assert result is True
            assert env.delivery_attempts == 1
            sent_headers = env.last_delivery.headers
            assert sent_headers["Authorization"] == "Bearer my-secret-token-xyz-padded-to-32ch"


# ---------------------------------------------------------------------------
# UC-004-ALT-WEBHOOK-PUSH-REPORTING-01 (_send_webhook_enhanced: happy path)
# ---------------------------------------------------------------------------


@pytest.mark.requires_db
class TestSendWebhookEnhancedHappyPath:
    """Happy path: _send_webhook_enhanced delivers to configured endpoint.

    Covers: UC-004-ALT-WEBHOOK-PUSH-REPORTING-01
    """

    def test_happy_path_delivers_payload_to_configured_endpoint(self, integration_db):
        """With a working endpoint and valid config, _send_webhook_enhanced returns True
        and sends the payload to the configured URL.

        Covers: UC-004-ALT-WEBHOOK-PUSH-REPORTING-01
        """
        from tests.factories import (
            PrincipalFactory,
            PushNotificationConfigFactory,
            TenantFactory,
        )
        from tests.harness import CircuitBreakerEnv

        with CircuitBreakerEnv(tenant_id="t1", principal_id="p1") as env:
            tenant = TenantFactory(tenant_id="t1")
            principal = PrincipalFactory(tenant=tenant, principal_id="p1")
            PushNotificationConfigFactory(
                tenant=tenant,
                principal=principal,
                url=env.webhook_url,
            )

            env.set_http_response(200)
            service = env.get_service()
            payload = {"adcp_version": "2.3", "impressions": 5000, "spend": 250.0}
            result = service._send_webhook_enhanced(
                tenant_id="t1",
                principal_id="p1",
                media_buy_id="mb_001",
                delivery_payload=payload,
            )

            assert result is True
            assert env.delivery_attempts == 1
            assert env.last_delivery.path == "/webhook"
            assert env.last_delivery.json() == payload

    def test_no_configs_returns_false(self, integration_db):
        """When no PushNotificationConfig exists, _send_webhook_enhanced returns False.

        Covers: UC-004-ALT-WEBHOOK-PUSH-REPORTING-01
        """
        from tests.factories import (
            PrincipalFactory,
            TenantFactory,
        )
        from tests.harness import CircuitBreakerEnv

        with CircuitBreakerEnv(tenant_id="t1", principal_id="p1") as env:
            TenantFactory(tenant_id="t1")
            PrincipalFactory(tenant_id="t1", principal_id="p1")

            service = env.get_service()
            result = service._send_webhook_enhanced(
                tenant_id="t1",
                principal_id="p1",
                media_buy_id="mb_001",
                delivery_payload={"test": "data"},
            )

            assert result is False
            assert env.delivery_attempts == 0


# ---------------------------------------------------------------------------
# UC-004-EXT-G-01 (_deliver_with_backoff: successful delivery)
# ---------------------------------------------------------------------------


@pytest.mark.requires_db
class TestDeliverWithBackoffSuccess:
    """Successful httpx delivery records success on circuit breaker.

    Covers: UC-004-EXT-G-01
    """

    def test_successful_delivery_returns_true_records_success(self, integration_db):
        """httpx returns 200 -> _deliver_with_backoff returns True and
        circuit breaker records success.

        Covers: UC-004-EXT-G-01
        """
        from tests.factories import (
            PrincipalFactory,
            PushNotificationConfigFactory,
            TenantFactory,
        )
        from tests.harness import CircuitBreakerEnv

        with CircuitBreakerEnv(tenant_id="t1", principal_id="p1") as env:
            tenant = TenantFactory(tenant_id="t1")
            principal = PrincipalFactory(tenant=tenant, principal_id="p1")
            PushNotificationConfigFactory(
                tenant=tenant,
                principal=principal,
                url=env.webhook_url,
            )

            env.set_http_response(200)
            service = env.get_service()
            result = service._send_webhook_enhanced(
                tenant_id="t1",
                principal_id="p1",
                media_buy_id="mb_001",
                delivery_payload={"impressions": 5000},
            )

            assert result is True

            # Circuit breaker should remain CLOSED (success recorded)
            endpoint_key = env.endpoint_key("t1")
            state, failure_count = service.get_circuit_breaker_state(endpoint_key)
            assert state == CircuitState.CLOSED
            assert failure_count == 0


# ---------------------------------------------------------------------------
# UC-004-EXT-G-01 (_deliver_with_backoff: retry on 500)
# ---------------------------------------------------------------------------


@pytest.mark.requires_db
class TestDeliverWithBackoffRetry:
    """httpx returns 500 -> retries with backoff, circuit breaker records failure.

    Covers: UC-004-EXT-G-01
    """

    def test_500_triggers_retries_and_records_failure(self, integration_db):
        """httpx returns 500 on all attempts -> _deliver_with_backoff retries
        max_retries times, then circuit breaker records failure.

        Covers: UC-004-EXT-G-01
        """
        from tests.factories import (
            PrincipalFactory,
            PushNotificationConfigFactory,
            TenantFactory,
        )
        from tests.harness import CircuitBreakerEnv

        with CircuitBreakerEnv(tenant_id="t1", principal_id="p1") as env:
            tenant = TenantFactory(tenant_id="t1")
            principal = PrincipalFactory(tenant=tenant, principal_id="p1")
            PushNotificationConfigFactory(
                tenant=tenant,
                principal=principal,
                url=env.webhook_url,
            )

            env.set_http_response(500)
            service = env.get_service()
            result = service._send_webhook_enhanced(
                tenant_id="t1",
                principal_id="p1",
                media_buy_id="mb_001",
                delivery_payload={"impressions": 5000},
            )

            assert result is False

            # httpx.Client.post should have been called 3 times (max_retries=3)
            assert env.delivery_attempts == 3

            # sleep should have been called for backoff (attempts 1 and 2, not before attempt 0)
            assert env.mock["sleep"].call_count == 2

            # Circuit breaker should record failure
            endpoint_key = env.endpoint_key("t1")
            state, failure_count = service.get_circuit_breaker_state(endpoint_key)
            assert failure_count == 1


# ---------------------------------------------------------------------------
# UC-004-EXT-G-01 (_deliver_with_backoff: timeout handling)
# ---------------------------------------------------------------------------


@pytest.mark.requires_db
class TestDeliverWithBackoffTransportFailure:
    """A transport-level failure -> retries with backoff, records failure.

    Two distinct transport failures are graded here, because the seam collapses
    them into one class and the consequence must be identical for both: a dropped
    connection, and a genuine stall the caller's own clock gives up on.

    The stall case is real, not simulated. It became affordable when delivery moved
    onto the seam (GH #1802) and its per-attempt timeout became
    configurable (GH #1802): the test shortens the timeout instead of
    waiting three ten-second attempts, and no transport is patched to fake it.

    Covers: UC-004-EXT-G-01
    """

    def test_dropped_connection_triggers_retries_and_records_failure(self, integration_db):
        """Every attempt is dropped mid-request -> retries exhaust,
        circuit breaker records failure.

        Covers: UC-004-EXT-G-01
        """
        from tests.factories import (
            PrincipalFactory,
            PushNotificationConfigFactory,
            TenantFactory,
        )
        from tests.harness import CircuitBreakerEnv

        with CircuitBreakerEnv(tenant_id="t1", principal_id="p1") as env:
            tenant = TenantFactory(tenant_id="t1")
            principal = PrincipalFactory(tenant=tenant, principal_id="p1")
            PushNotificationConfigFactory(
                tenant=tenant,
                principal=principal,
                url=env.webhook_url,
            )

            env.set_http_error()

            service = env.get_service()
            result = service._send_webhook_enhanced(
                tenant_id="t1",
                principal_id="p1",
                media_buy_id="mb_001",
                delivery_payload={"impressions": 5000},
            )

            assert result is False

            # Should have retried 3 times
            assert env.delivery_attempts == 3

            # Circuit breaker should record failure
            endpoint_key = env.endpoint_key("t1")
            state, failure_count = service.get_circuit_breaker_state(endpoint_key)
            assert failure_count == 1

    def test_a_stalled_endpoint_times_out_retries_and_records_failure(self, integration_db, monkeypatch):
        """Every attempt stalls past the timeout -> retries exhaust, one failure recorded.

        The origin ACCEPTS each request and then holds it, so the failure is the
        caller's clock rather than a refused or dropped connection — the case
        GH #1802 exists to put back. The endpoint provably received all three
        attempts, which a mocked clock could not show.

        Covers: UC-004-EXT-G-01
        """
        from tests.factories import (
            PrincipalFactory,
            PushNotificationConfigFactory,
            TenantFactory,
        )
        from tests.harness import CircuitBreakerEnv

        monkeypatch.setenv("ADCP_WEBHOOK_DELIVERY_TIMEOUT_SECONDS", "0.3")

        with CircuitBreakerEnv(tenant_id="t1", principal_id="p1") as env:
            tenant = TenantFactory(tenant_id="t1")
            principal = PrincipalFactory(tenant=tenant, principal_id="p1")
            PushNotificationConfigFactory(tenant=tenant, principal=principal, url=env.webhook_url)

            env.origin.delay(2.0)

            service = env.get_service()
            result = service._send_webhook_enhanced(
                tenant_id="t1",
                principal_id="p1",
                media_buy_id="mb_001",
                delivery_payload={"impressions": 5000},
            )

            assert result is False
            assert env.delivery_attempts == 3, "each attempt must reach the endpoint before the clock gives up"

            endpoint_key = env.endpoint_key("t1")
            _state, failure_count = service.get_circuit_breaker_state(endpoint_key)
            assert failure_count == 1


# ---------------------------------------------------------------------------
# UC-004-EXT-G-03 (_deliver_with_backoff: a URL egress policy refuses)
# ---------------------------------------------------------------------------


@pytest.mark.requires_db
class TestDeliverWithBackoffRefusedUrl:
    """A URL the egress policy refuses is as visible to the breaker as a dead one.

    The refusal is the failure mode nothing else in this suite produces: the
    other cases all reach the origin and fail there. A refusal never reaches the
    wire at all, so it arrives at the call site as a DIFFERENT exception class,
    and a handler that catches only "the destination answered badly" would let a
    permanently unreachable endpoint fail silently forever — every delivery
    dropped, the circuit never opening, no operator signal.

    ``no-such-host.invalid`` is the unresolvable host the seam's own suite uses
    (``tests/integration/test_outbound_http.py``); it is refused by address
    policy, and the escape hatches this env opens for the loopback origin do not
    reach it.

    Covers: UC-004-EXT-G-03
    """

    REFUSED_URL = "https://no-such-host.invalid/webhook"

    def test_refused_url_records_failures_and_opens_the_circuit(self, integration_db):
        """Every refusal records one circuit-breaker failure, and the circuit opens.

        Covers: UC-004-EXT-G-03
        """
        from tests.factories import (
            PrincipalFactory,
            PushNotificationConfigFactory,
            TenantFactory,
        )
        from tests.harness import CircuitBreakerEnv

        with CircuitBreakerEnv(tenant_id="t1", principal_id="p1") as env:
            tenant = TenantFactory(tenant_id="t1")
            principal = PrincipalFactory(tenant=tenant, principal_id="p1")
            PushNotificationConfigFactory(
                tenant=tenant,
                principal=principal,
                url=self.REFUSED_URL,
            )

            service = env.get_service()
            # Read the threshold off a breaker rather than restating 5: the test
            # is about "enough failures to open it", not about the number.
            threshold = env.get_breaker().failure_threshold

            for _ in range(threshold):
                result = service._send_webhook_enhanced(
                    tenant_id="t1",
                    principal_id="p1",
                    media_buy_id="mb_001",
                    delivery_payload={"impressions": 5000},
                )
                assert result is False

            state, failure_count = service.get_circuit_breaker_state(self.REFUSED_URL)
            assert failure_count == threshold, (
                f"a refused URL produced {failure_count} circuit-breaker failures across "
                f"{threshold} deliveries — a refusal that does not reach record_failure() "
                "makes a bad endpoint invisible to the breaker"
            )
            assert state == CircuitState.OPEN

            # A refusal is decided before any connection is attempted, so there
            # is nothing to retry and nothing to back off from.
            assert env.mock["sleep"].call_count == 0, (
                f"a refused URL was retried with backoff ({env.mock['sleep'].call_count} sleeps)"
            )


# ---------------------------------------------------------------------------
# UC-004-EXT-G-01 (_deliver_with_backoff: a rate-limited endpoint)
# ---------------------------------------------------------------------------


@pytest.mark.requires_db
class TestDeliverWithBackoffRateLimited:
    """A 429 is retried, and is never logged as a failure that will not be retried.

    429 is the one 4xx the seam retries, which makes it the case where a call
    site that classifies by status range contradicts what actually happened: it
    logs "client error 429, will not retry" about a delivery that was attempted
    three times. Nothing outside this test grades that sentence, and an operator
    reading it would go looking for a rejected request instead of a rate limit.

    Covers: UC-004-EXT-G-01
    """

    def test_429_is_retried_and_not_logged_as_a_no_retry_client_error(self, integration_db):
        """The origin answers 429 to every attempt: retried, then one failure recorded.

        Covers: UC-004-EXT-G-01
        """
        from tests.factories import (
            PrincipalFactory,
            PushNotificationConfigFactory,
            TenantFactory,
        )
        from tests.harness import CircuitBreakerEnv

        with CircuitBreakerEnv(tenant_id="t1", principal_id="p1") as env:
            tenant = TenantFactory(tenant_id="t1")
            principal = PrincipalFactory(tenant=tenant, principal_id="p1")
            PushNotificationConfigFactory(
                tenant=tenant,
                principal=principal,
                url=env.webhook_url,
            )

            env.set_http_response(429)
            service = env.get_service()
            result = service._send_webhook_enhanced(
                tenant_id="t1",
                principal_id="p1",
                media_buy_id="mb_001",
                delivery_payload={"impressions": 5000},
            )

            assert result is False

            # The endpoint really was tried again — this is what makes the
            # "will not retry" wording below a false statement rather than a
            # stylistic quibble.
            assert env.delivery_attempts == 3
            assert env.mock["sleep"].call_count == 2

            no_retry_claims = [record for record in env.captured_logs if "will not retry" in record]
            assert no_retry_claims == [], (
                f"a 429 that was retried {env.delivery_attempts} times was logged as not retryable: {no_retry_claims}"
            )
            assert any("429" in record for record in env.captured_logs), (
                f"the rate limit was never named in an operator log: {env.captured_logs}"
            )

            endpoint_key = env.endpoint_key("t1")
            _, failure_count = service.get_circuit_breaker_state(endpoint_key)
            assert failure_count == 1


# ---------------------------------------------------------------------------
# UC-004-EXT-G-01 (_deliver_with_backoff: a rejected request)
# ---------------------------------------------------------------------------


@pytest.mark.requires_db
class TestDeliverWithBackoffClientError:
    """A rejected request is terminal, and this module logs the status itself.

    The status has to be named by THIS module's logger at WARNING: the capture
    handler in ``CircuitBreakerEnv`` attaches to
    ``src.services.webhook_delivery_service`` only and at WARNING only, so
    membership in ``captured_logs`` grades both the logger and the level. The
    egress seam logs under its own name and says nothing at all about a
    non-retryable 4xx, so if this site stops logging it, the only operator signal
    that an endpoint is rejecting deliveries disappears.

    Covers: UC-004-EXT-G-01
    """

    def test_404_is_not_retried_and_the_status_is_logged(self, integration_db):
        """The origin answers 404: one attempt, one recorded failure, one warning.

        Covers: UC-004-EXT-G-01
        """
        from tests.factories import (
            PrincipalFactory,
            PushNotificationConfigFactory,
            TenantFactory,
        )
        from tests.harness import CircuitBreakerEnv

        with CircuitBreakerEnv(tenant_id="t1", principal_id="p1") as env:
            tenant = TenantFactory(tenant_id="t1")
            principal = PrincipalFactory(tenant=tenant, principal_id="p1")
            PushNotificationConfigFactory(
                tenant=tenant,
                principal=principal,
                url=env.webhook_url,
            )

            env.set_http_response(404)
            service = env.get_service()
            result = service._send_webhook_enhanced(
                tenant_id="t1",
                principal_id="p1",
                media_buy_id="mb_001",
                delivery_payload={"impressions": 5000},
            )

            assert result is False
            assert env.delivery_attempts == 1
            assert env.mock["sleep"].call_count == 0

            assert any("404" in record for record in env.captured_logs), (
                f"the rejected status was never named in an operator log: {env.captured_logs}"
            )

            endpoint_key = env.endpoint_key("t1")
            _, failure_count = service.get_circuit_breaker_state(endpoint_key)
            assert failure_count == 1


# ---------------------------------------------------------------------------
# Coverage: is_adjusted notification type (line 239)
# ---------------------------------------------------------------------------


@pytest.mark.requires_db
class TestIsAdjustedNotificationType:
    """``is_adjusted`` decides the notification_type, and both arms are graded.

    An adjusted report REPLACES figures the buyer already booked; a scheduled one
    adds to them. The buyer tells the two apart by these two fields and nothing
    else, so the False arm is not a mirror of the True arm — a build that marked
    every report adjusted would satisfy the True arm alone and quietly ask buyers
    to overwrite good data on every delivery.

    Covers: line 239 of webhook_delivery_service.py,
            UC-004-ALT-WEBHOOK-PUSH-REPORTING-03
    """

    def test_is_adjusted_sets_notification_type_adjusted(self, integration_db):
        """When is_adjusted=True, the payload notification_type is 'adjusted'.

        Covers: webhook_delivery_service.py line 239
        """
        from datetime import UTC, datetime

        from tests.factories import (
            PrincipalFactory,
            PushNotificationConfigFactory,
            TenantFactory,
        )
        from tests.harness import CircuitBreakerEnv

        with CircuitBreakerEnv(tenant_id="t1", principal_id="p1") as env:
            tenant = TenantFactory(tenant_id="t1")
            principal = PrincipalFactory(tenant=tenant, principal_id="p1")
            PushNotificationConfigFactory(
                tenant=tenant,
                principal=principal,
                url=env.webhook_url,
            )

            env.set_http_response(200)
            service = env.get_service()
            result = service.send_delivery_webhook(
                media_buy_id="mb_adj",
                tenant_id="t1",
                principal_id="p1",
                reporting_period_start=datetime(2025, 6, 1, tzinfo=UTC),
                reporting_period_end=datetime(2025, 6, 30, tzinfo=UTC),
                impressions=1000,
                spend=50.0,
                is_adjusted=True,
            )

            assert result is True
            sent_payload = env.last_delivery.json()
            assert sent_payload["notification_type"] == "adjusted"
            assert sent_payload["is_adjusted"] is True

    def test_scheduled_report_is_not_marked_adjusted(self, integration_db):
        """A periodic report the caller did not flag: 'scheduled', and is_adjusted False.

        ``is False`` rather than falsy: the field is a boolean on the wire, and a
        buyer branching on it must not have to treat ``null`` or a missing key as
        "not adjusted".

        Covers: UC-004-ALT-WEBHOOK-PUSH-REPORTING-03
        """
        from datetime import UTC, datetime

        from tests.factories import (
            PrincipalFactory,
            PushNotificationConfigFactory,
            TenantFactory,
        )
        from tests.harness import CircuitBreakerEnv

        with CircuitBreakerEnv(tenant_id="t1", principal_id="p1") as env:
            tenant = TenantFactory(tenant_id="t1")
            principal = PrincipalFactory(tenant=tenant, principal_id="p1")
            PushNotificationConfigFactory(
                tenant=tenant,
                principal=principal,
                url=env.webhook_url,
            )

            env.set_http_response(200)
            service = env.get_service()
            result = service.send_delivery_webhook(
                media_buy_id="mb_sched",
                tenant_id="t1",
                principal_id="p1",
                reporting_period_start=datetime(2025, 6, 1, tzinfo=UTC),
                reporting_period_end=datetime(2025, 6, 30, tzinfo=UTC),
                impressions=1000,
                spend=50.0,
            )

            assert result is True
            sent_payload = env.last_delivery.json()
            assert sent_payload["notification_type"] == "scheduled"
            assert sent_payload["is_adjusted"] is False


# ---------------------------------------------------------------------------
# UC-004-ALT-WEBHOOK-PUSH-REPORTING-01 (send_delivery_webhook: adcp_version echo)
# ---------------------------------------------------------------------------


@pytest.mark.requires_db
class TestDeliveredPayloadAdcpVersion:
    """The delivered payload names the AdCP version this build speaks.

    A buyer receiving a push report has no handshake to negotiate against — the
    payload's own ``adcp_version`` is how it decides which schema to parse it
    with, so a stale value is a misparse on their side, not a cosmetic drift.

    Compared against ``get_adcp_spec_version()`` rather than a literal: a literal
    would keep passing across a spec bump while the wire told buyers the old
    version, which is the exact failure the assertion exists to catch. This is
    the only place the field is graded on THIS surface — the assertion in
    ``tests/e2e/test_a2a_endpoints_working.py`` is on the A2A envelope, a
    different payload assembled by different code.

    Covers: UC-004-ALT-WEBHOOK-PUSH-REPORTING-01
    """

    def test_delivered_payload_echoes_the_pinned_adcp_spec_version(self, integration_db):
        """The bytes that reached the endpoint carry the SDK's spec version.

        Covers: UC-004-ALT-WEBHOOK-PUSH-REPORTING-01
        """
        from datetime import UTC, datetime

        from adcp import get_adcp_spec_version

        from tests.factories import (
            PrincipalFactory,
            PushNotificationConfigFactory,
            TenantFactory,
        )
        from tests.harness import CircuitBreakerEnv

        with CircuitBreakerEnv(tenant_id="t1", principal_id="p1") as env:
            tenant = TenantFactory(tenant_id="t1")
            principal = PrincipalFactory(tenant=tenant, principal_id="p1")
            PushNotificationConfigFactory(
                tenant=tenant,
                principal=principal,
                url=env.webhook_url,
            )

            env.set_http_response(200)
            service = env.get_service()
            result = service.send_delivery_webhook(
                media_buy_id="mb_version",
                tenant_id="t1",
                principal_id="p1",
                reporting_period_start=datetime(2025, 6, 1, tzinfo=UTC),
                reporting_period_end=datetime(2025, 6, 30, tzinfo=UTC),
                impressions=1000,
                spend=50.0,
            )

            assert result is True
            sent_payload = env.last_delivery.json()
            assert sent_payload["adcp_version"] == get_adcp_spec_version()


# ---------------------------------------------------------------------------
# UC-004-ALT-WEBHOOK-PUSH-REPORTING-05 (send_delivery_webhook: sequence under
# concurrency — the lock around the per-media-buy counter)
# ---------------------------------------------------------------------------


@pytest.mark.requires_db
class TestSequenceNumberUnderConcurrency:
    """Two reports for one media buy never carry the same sequence_number.

    BR-RULE-029 INV-1 makes ``sequence_number`` strictly increasing per media
    buy, and it is the only handle a buyer has for ordering reports and dropping
    replays. Two payloads sharing a number is therefore silent data loss on their
    side: one of the two reports is indistinguishable from a duplicate of the
    other and gets discarded.

    Production holds that invariant with ``self._lock`` around a read-modify-write
    that is not atomic on its own — ``d[k] = d.get(k, 0) + 1`` and then a separate
    read-back of ``d[k]``. Serial deliveries cannot grade the lock; they pass
    identically with it and without it, which is why the three-report monotonicity
    scenarios elsewhere leave it untested.

    So the threads here contend for ONE media buy, and the window between the read
    and the write is made observable by stalling the read: the mapping production
    increments is replaced with one whose ``get`` sleeps. That does not invent a
    failure mode — an unsynchronized read-modify-write may legally interleave at
    exactly that point on any thread switch, GC pause or page fault. Stalling
    picks that interleaving every run instead of waiting for the scheduler to
    produce it once in a million. Everything else is real: ten concurrent
    deliveries over real HTTP to the local origin, and the assertion reads the
    sequence numbers off the bytes that arrived.

    Confirmed by mutation: with ``service._lock`` replaced by a no-op context
    manager, all ten reports arrive numbered 1 and the assertion fails.

    Covers: UC-004-ALT-WEBHOOK-PUSH-REPORTING-05
    """

    THREADS = 10

    # Long enough that every thread is inside the read before the first one
    # leaves it; short enough that ten serialized deliveries stay well under a
    # second when the lock is doing its job.
    READ_STALL_SECONDS = 0.05

    def test_concurrent_reports_for_one_media_buy_get_distinct_sequence_numbers(self, integration_db):
        """Ten threads, one media buy: the endpoint receives sequence numbers 1..10.

        Covers: UC-004-ALT-WEBHOOK-PUSH-REPORTING-05
        """
        import threading
        from datetime import UTC, datetime

        # Bound BEFORE the env starts: the env patches the seam's ``time.sleep``,
        # and because ``import time`` yields one shared module object that patch
        # lands on ``time.sleep`` process-wide. Looking the name up later would
        # get the MagicMock, and a stall that does not stall widens nothing.
        from time import sleep as unpatched_sleep

        from tests.factories import (
            PrincipalFactory,
            PushNotificationConfigFactory,
            TenantFactory,
        )
        from tests.harness import CircuitBreakerEnv

        stall_seconds = self.READ_STALL_SECONDS

        class StalledReadMapping(dict):
            """The per-media-buy counter, with its read-modify-write window held open."""

            def get(self, key, default=None):  # type: ignore[override]
                value = super().get(key, default)
                unpatched_sleep(stall_seconds)
                return value

        with CircuitBreakerEnv(tenant_id="t1", principal_id="p1") as env:
            tenant = TenantFactory(tenant_id="t1")
            principal = PrincipalFactory(tenant=tenant, principal_id="p1")
            PushNotificationConfigFactory(
                tenant=tenant,
                principal=principal,
                url=env.webhook_url,
            )

            env.set_http_response(200)
            service = env.get_service()
            service._sequence_numbers = StalledReadMapping()

            start = threading.Barrier(self.THREADS)
            sent_results: list[bool] = []
            results_lock = threading.Lock()

            def deliver_one_report() -> None:
                start.wait()
                sent = service.send_delivery_webhook(
                    media_buy_id="mb_concurrent",
                    tenant_id="t1",
                    principal_id="p1",
                    reporting_period_start=datetime(2025, 6, 1, tzinfo=UTC),
                    reporting_period_end=datetime(2025, 6, 30, tzinfo=UTC),
                    impressions=1000,
                    spend=50.0,
                )
                with results_lock:
                    sent_results.append(sent)

            threads = [threading.Thread(target=deliver_one_report) for _ in range(self.THREADS)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=60)

            assert not any(thread.is_alive() for thread in threads), (
                "a delivery thread never finished — the sequence lock is deadlocked, not merely unsynchronized"
            )
            assert sent_results == [True] * self.THREADS
            assert env.delivery_attempts == self.THREADS

            delivered_sequence_numbers = sorted(request.json()["sequence_number"] for request in env.delivered_requests)
            assert delivered_sequence_numbers == list(range(1, self.THREADS + 1)), (
                f"{self.THREADS} concurrent reports for one media buy were numbered "
                f"{delivered_sequence_numbers} — a repeated sequence_number makes one "
                "report indistinguishable from a replay of another, and the buyer drops it"
            )


# ---------------------------------------------------------------------------
# Coverage: queue full drops webhook (lines 408-409)
# ---------------------------------------------------------------------------


@pytest.mark.requires_db
class TestQueueFullDropsWebhook:
    """When webhook queue is full, _send_webhook_enhanced drops the webhook.

    Covers: lines 408-409 of webhook_delivery_service.py
    """

    def test_queue_full_skips_delivery(self, integration_db):
        """When the per-endpoint queue is at max capacity, enqueue fails
        and delivery is skipped for that endpoint.

        Covers: webhook_delivery_service.py lines 408-409
        """
        from src.services.webhook_delivery_service import WebhookQueue
        from tests.factories import (
            PrincipalFactory,
            PushNotificationConfigFactory,
            TenantFactory,
        )
        from tests.harness import CircuitBreakerEnv

        with CircuitBreakerEnv(tenant_id="t1", principal_id="p1") as env:
            tenant = TenantFactory(tenant_id="t1")
            principal = PrincipalFactory(tenant=tenant, principal_id="p1")
            PushNotificationConfigFactory(
                tenant=tenant,
                principal=principal,
                url=env.webhook_url,
            )

            env.set_http_response(200)
            service = env.get_service()

            # Pre-populate the queue to capacity (use small max_size)
            endpoint_key = env.endpoint_key("t1")
            small_queue = WebhookQueue(max_size=1)
            small_queue.enqueue({"dummy": "data"})  # Fill it
            service._queues[endpoint_key] = small_queue

            result = service._send_webhook_enhanced(
                tenant_id="t1",
                principal_id="p1",
                media_buy_id="mb_full",
                delivery_payload={"test": "data"},
            )

            assert result is False


# ---------------------------------------------------------------------------
# A short credential signs -- it is not silently downgraded (GH #1894)
# ---------------------------------------------------------------------------


@pytest.mark.requires_db
class TestShortSecretRefusesRatherThanSigning:
    """A sub-32 HMAC credential REFUSES. Reversed twice; the history matters.

    v1 asserted such a secret was silently DISCARDED and the delivery went out
    unsigned at WARNING level — a buyer who configured signing received unsigned
    webhooks and no error (GH #1894, defect 1).

    v2 (GH #1802) reversed that to "signed with, not discarded", and
    explicitly recorded that refusing had been "considered and rejected" because it
    would take buyers from "delivered" to "not delivered at all", adding: "AdCP
    3.1.1 mandates none." THAT CLAIM WAS FALSE. The pinned schema
    (core/push-notification-config.json) puts ``minLength: 32`` on
    ``authentication.credentials``; the same false sentence lived in
    webhook_delivery_service.py and has been corrected there too.

    v3 — this one — refuses, by owner ruling for Epic D lane C4: "Refuse — spec or
    nothing." A stored block that does not satisfy the pinned schema is not a
    delivery we should be making. The delivered-to-never-delivered objection is
    ANSWERED rather than waived: such a row is not a delivery we should have been
    making, its owner re-registers, and no migration is supplied because a short
    secret cannot be lengthened without changing what the receiver verifies against.

    The reachability note from v2 still stands and is why this case exists at all:
    create_media_buy's pydantic boundary enforces the minimum, but the A2A
    setTaskPushNotificationConfig handler reads the credential off a free-form
    protobuf string, so an A2A-registered row can carry a short secret today.
    """

    def test_a_short_secret_refuses_instead_of_signing(self, integration_db):
        """A credential under the pinned minimum stops the delivery, and says so."""
        from tests.factories import (
            PrincipalFactory,
            PushNotificationConfigFactory,
            TenantFactory,
        )
        from tests.harness import CircuitBreakerEnv

        secret = "tooshort"  # 8 chars, against the pinned minLength of 32

        with CircuitBreakerEnv(tenant_id="t1", principal_id="p1") as env:
            tenant = TenantFactory(tenant_id="t1")
            principal = PrincipalFactory(tenant=tenant, principal_id="p1")
            PushNotificationConfigFactory(
                tenant=tenant,
                principal=principal,
                url=env.webhook_url,
                authentication_type="HMAC-SHA256",
                authentication_token=secret,
            )

            env.set_http_response(200)
            service = env.get_service()
            result = service._send_webhook_enhanced(
                tenant_id="t1",
                principal_id="p1",
                media_buy_id="mb_weak",
                delivery_payload={"test": "data"},
            )

            assert result is False, "a non-conforming credential must not report a successful delivery"
            assert env.delivery_attempts == 0, (
                f"the seam dialled {env.delivery_attempts} time(s) for a credential the pinned "
                f"schema forbids — a refusal must not reach the wire at all"
            )


# ---------------------------------------------------------------------------
# Coverage: empty dequeue returns False (line 447)
# ---------------------------------------------------------------------------


@pytest.mark.requires_db
class TestEmptyDequeueIsNotAnOutcome:
    """An empty queue yields NO outcome — nothing was attempted.

    Distinct from every other return: ``None`` is not a delivery that failed. It
    is what stops the caller's conclusion from running at all, so an empty queue
    cannot write a delivery-log row or tick the circuit breaker for a webhook
    that was never sent. When this function concluded in ``bool`` the two were
    the same ``False``.
    """

    def test_deliver_with_backoff_empty_queue(self, integration_db):
        from src.services.webhook_delivery_service import CircuitBreaker, WebhookQueue
        from tests.harness import CircuitBreakerEnv

        with CircuitBreakerEnv() as env:
            service = env.get_service()
            cb = CircuitBreaker()
            empty_queue = WebhookQueue()

            result = service._deliver_with_backoff(env.endpoint_key("t1"), empty_queue)

            assert result is None, (
                f"an empty queue produced {result!r} — anything other than None is an outcome, "
                "and an outcome would be recorded as a delivery that never happened"
            )
            assert cb.failure_count == 0, (
                f"the breaker recorded {cb.failure_count} failure(s) for a webhook that was never "
                "dequeued — an endpoint cannot look unhealthy because nothing was queued for it"
            )
