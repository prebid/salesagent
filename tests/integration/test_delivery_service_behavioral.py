"""Integration behavioral tests for UC-004 delivery service (WebhookDeliveryService, CircuitBreaker).

Migrated from tests/unit/test_delivery_service_behavioral.py to use CircuitBreakerEnv
integration harness. External services (httpx.Client, time.sleep, random.uniform)
are mocked; DB operations for PushNotificationConfig queries are real.

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
                url="https://example.com/webhook",
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

            endpoint_key = "t1:https://example.com/webhook"
            state, _ = service.get_circuit_breaker_state(endpoint_key)
            assert state == CircuitState.OPEN

            # Reset mock to track new calls
            env.mock["client"].return_value.__enter__.return_value.post.reset_mock()

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
            env.mock["client"].return_value.__enter__.return_value.post.assert_not_called()


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

            endpoint_key = "t1:https://example.com/webhook"
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
                url="https://blocked.example.com/webhook",
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
            env.mock["client"].return_value.__enter__.return_value.post.assert_not_called()


# ---------------------------------------------------------------------------
# UC-004-EXT-G-06 (_send_webhook_enhanced: HMAC signing)
# ---------------------------------------------------------------------------


@pytest.mark.requires_db
class TestSendWebhookEnhancedHmacSigning:
    """HMAC-SHA256 signature is added when webhook_secret is configured.

    Covers: UC-004-EXT-G-06
    """

    def test_hmac_signature_header_present_when_secret_configured(self, integration_db):
        """When PushNotificationConfig has a strong webhook_secret (>=32 chars),
        X-ADCP-Signature header is set on the outgoing request.

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
                url="https://hmac.example.com/webhook",
                webhook_secret="a" * 32,  # Exactly 32 chars — meets minimum
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
            post_mock = env.mock["client"].return_value.__enter__.return_value.post
            post_mock.assert_called_once()
            sent_headers = post_mock.call_args.kwargs["headers"]
            assert "X-ADCP-Signature" in sent_headers
            assert len(sent_headers["X-ADCP-Signature"]) > 0

    def test_hmac_signature_valid_reproduces_from_payload(self, integration_db):
        """The HMAC signature can be reproduced using the same secret and payload.

        Covers: UC-004-EXT-G-06
        """
        import hashlib
        import hmac
        import json

        from tests.factories import (
            PrincipalFactory,
            PushNotificationConfigFactory,
            TenantFactory,
        )
        from tests.harness import CircuitBreakerEnv

        secret = "b" * 32
        payload = {"media_buy_id": "mb_001", "impressions": 5000}

        with CircuitBreakerEnv(tenant_id="t1", principal_id="p1") as env:
            tenant = TenantFactory(tenant_id="t1")
            principal = PrincipalFactory(tenant=tenant, principal_id="p1")
            PushNotificationConfigFactory(
                tenant=tenant,
                principal=principal,
                url="https://hmac-verify.example.com/webhook",
                webhook_secret=secret,
            )

            env.set_http_response(200)
            service = env.get_service()
            service._send_webhook_enhanced(
                tenant_id="t1",
                principal_id="p1",
                media_buy_id="mb_001",
                delivery_payload=payload,
            )

            post_mock = env.mock["client"].return_value.__enter__.return_value.post
            sent_headers = post_mock.call_args.kwargs["headers"]
            sent_signature = sent_headers["X-ADCP-Signature"]
            sent_timestamp = sent_headers["X-ADCP-Timestamp"]

            # Reproduce the signature
            payload_str = json.dumps(payload, sort_keys=True, separators=(",", ":"))
            message = f"{sent_timestamp}.{payload_str}"
            expected = hmac.new(
                secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256
            ).hexdigest()

            assert sent_signature == expected


# ---------------------------------------------------------------------------
# UC-004-ALT-WEBHOOK-PUSH-REPORTING-08 (_send_webhook_enhanced: bearer auth)
# ---------------------------------------------------------------------------


@pytest.mark.requires_db
class TestSendWebhookEnhancedBearerAuth:
    """Bearer token authentication is set when configured on PushNotificationConfig.

    Covers: UC-004-ALT-WEBHOOK-PUSH-REPORTING-08
    """

    def test_bearer_token_sent_in_authorization_header(self, integration_db):
        """When authentication_type='bearer' and authentication_token is set,
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
                url="https://bearer.example.com/webhook",
                authentication_type="bearer",
                authentication_token="my-secret-token-xyz",
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
            post_mock = env.mock["client"].return_value.__enter__.return_value.post
            post_mock.assert_called_once()
            sent_headers = post_mock.call_args.kwargs["headers"]
            assert sent_headers["Authorization"] == "Bearer my-secret-token-xyz"


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
                url="https://happy.example.com/webhook",
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
            post_mock = env.mock["client"].return_value.__enter__.return_value.post
            post_mock.assert_called_once()
            assert post_mock.call_args.args[0] == "https://happy.example.com/webhook"
            assert post_mock.call_args.kwargs["json"] == payload

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
            env.mock["client"].return_value.__enter__.return_value.post.assert_not_called()


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
                url="https://success.example.com/webhook",
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
            endpoint_key = "t1:https://success.example.com/webhook"
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
                url="https://failing.example.com/webhook",
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
            post_mock = env.mock["client"].return_value.__enter__.return_value.post
            assert post_mock.call_count == 3

            # sleep should have been called for backoff (attempts 1 and 2, not before attempt 0)
            assert env.mock["sleep"].call_count == 2

            # Circuit breaker should record failure
            endpoint_key = "t1:https://failing.example.com/webhook"
            state, failure_count = service.get_circuit_breaker_state(endpoint_key)
            assert failure_count == 1


# ---------------------------------------------------------------------------
# UC-004-EXT-G-01 (_deliver_with_backoff: timeout handling)
# ---------------------------------------------------------------------------


@pytest.mark.requires_db
class TestDeliverWithBackoffTimeout:
    """httpx raises TimeoutException -> retries with backoff, records failure.

    Covers: UC-004-EXT-G-01
    """

    def test_timeout_triggers_retries_and_records_failure(self, integration_db):
        """httpx raises TimeoutException on all attempts -> retries exhaust,
        circuit breaker records failure.

        Covers: UC-004-EXT-G-01
        """
        import httpx

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
                url="https://timeout.example.com/webhook",
            )

            # Make httpx.Client().post() raise TimeoutException
            env.mock["client"].return_value.__enter__.return_value.post.side_effect = (
                httpx.TimeoutException("Connection timed out")
            )

            service = env.get_service()
            result = service._send_webhook_enhanced(
                tenant_id="t1",
                principal_id="p1",
                media_buy_id="mb_001",
                delivery_payload={"impressions": 5000},
            )

            assert result is False

            # Should have retried 3 times
            post_mock = env.mock["client"].return_value.__enter__.return_value.post
            assert post_mock.call_count == 3

            # Circuit breaker should record failure
            endpoint_key = "t1:https://timeout.example.com/webhook"
            state, failure_count = service.get_circuit_breaker_state(endpoint_key)
            assert failure_count == 1
