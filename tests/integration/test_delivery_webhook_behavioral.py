"""Integration behavioral tests for UC-004 webhook delivery (deliver_webhook_with_retry).

Migrated from tests/unit/test_delivery_webhook_behavioral.py to use WebhookEnv
integration harness. External services (requests.post, URL validator, time.sleep)
are mocked; DB operations for delivery record tracking are real.

Each test targets exactly one obligation ID and follows the 6 hard rules.
"""

from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# UC-004-ALT-WEBHOOK-PUSH-REPORTING-01
# ---------------------------------------------------------------------------


@pytest.mark.requires_db
class TestWebhookDeliveryHappyPath:
    """Scheduled webhook delivery happy path — POST with signed payload.

    Covers: UC-004-ALT-WEBHOOK-PUSH-REPORTING-01
    """

    def test_webhook_sends_signed_payload(self, integration_db):
        """Webhook delivery sends POST to configured URL with HMAC-signed payload.

        Covers: UC-004-ALT-WEBHOOK-PUSH-REPORTING-01
        """
        from tests.harness import WebhookEnv

        with WebhookEnv() as env:
            env.set_http_status(200)

            success, result = env.call_deliver(
                webhook_url="https://buyer.example.com/webhook",
                payload={
                    "media_buy_id": "mb_001",
                    "impressions": 5000,
                    "spend": 250.0,
                    "notification_type": "scheduled",
                },
                signing_secret="test-secret-key",
                max_retries=1,
            )

            assert success is True
            assert result["status"] == "delivered"

            # Verify POST was called with correct URL
            call_args = env.mock["post"].call_args
            assert call_args.args[0] == "https://buyer.example.com/webhook"

            # Verify HMAC signature headers were added
            sent_headers = call_args.kwargs["headers"]
            assert "X-Webhook-Signature" in sent_headers
            assert "X-Webhook-Timestamp" in sent_headers

            # Verify payload was sent
            sent_payload = call_args.kwargs["json"]
            assert sent_payload["media_buy_id"] == "mb_001"
            assert sent_payload["notification_type"] == "scheduled"


# ---------------------------------------------------------------------------
# UC-004-ALT-WEBHOOK-PUSH-REPORTING-07
# ---------------------------------------------------------------------------


class TestWebhookHmacSha256Signing:
    """Webhook payload signed with HMAC-SHA256.

    Covers: UC-004-ALT-WEBHOOK-PUSH-REPORTING-07
    """

    def test_sign_payload_produces_hmac_headers(self):
        """WebhookAuthenticator.sign_payload produces HMAC-SHA256 signature headers.

        Covers: UC-004-ALT-WEBHOOK-PUSH-REPORTING-07
        """
        from src.core.webhook_authenticator import WebhookAuthenticator

        payload = {"media_buy_id": "mb_001", "impressions": 5000}
        secret = "test-signing-secret"

        headers = WebhookAuthenticator.sign_payload(payload, secret)

        assert "X-Webhook-Signature" in headers
        assert headers["X-Webhook-Signature"].startswith("sha256=")
        assert len(headers["X-Webhook-Signature"]) > len("sha256=")
        assert "X-Webhook-Timestamp" in headers
        assert headers["X-Webhook-Timestamp"].isdigit()


# ---------------------------------------------------------------------------
# UC-004-ALT-WEBHOOK-PUSH-REPORTING-08
# ---------------------------------------------------------------------------


@pytest.mark.requires_db
class TestWebhookBearerTokenAuth:
    """Webhook delivery with Bearer token authentication.

    Covers: UC-004-ALT-WEBHOOK-PUSH-REPORTING-08
    """

    def test_bearer_token_sent_in_authorization_header(self, integration_db):
        """Bearer token is forwarded in Authorization header when set by caller.

        Covers: UC-004-ALT-WEBHOOK-PUSH-REPORTING-08
        """
        from tests.harness import WebhookEnv

        with WebhookEnv() as env:
            env.set_http_status(200)

            success, result = env.call_deliver(
                webhook_url="https://buyer.example.com/webhook",
                payload={"media_buy_id": "mb_001"},
                headers={
                    "Content-Type": "application/json",
                    "Authorization": "Bearer test-bearer-token-xyz",
                },
                max_retries=1,
            )

            assert success is True
            assert result["status"] == "delivered"

            call_args = env.mock["post"].call_args
            sent_headers = call_args.kwargs["headers"]
            assert sent_headers["Authorization"] == "Bearer test-bearer-token-xyz"


# ---------------------------------------------------------------------------
# UC-004-ALT-WEBHOOK-PUSH-REPORTING-11
# ---------------------------------------------------------------------------


@pytest.mark.requires_db
class TestWebhookOnlyActiveMediaBuys:
    """Only active media buys trigger webhook delivery.

    Covers: UC-004-ALT-WEBHOOK-PUSH-REPORTING-11
    """

    def test_paused_media_buy_webhook_rejected(self, integration_db):
        """Webhook delivery should be rejected for paused media buys.

        Covers: UC-004-ALT-WEBHOOK-PUSH-REPORTING-11
        """
        from tests.harness import WebhookEnv

        with WebhookEnv() as env:
            env.set_http_status(200)

            success, result = env.call_deliver(
                webhook_url="https://buyer.example.com/webhook",
                payload={"media_buy_id": "mb_paused", "status": "paused"},
                max_retries=1,
            )

            assert success is False, "Webhook should not be delivered for paused media buy"


# ---------------------------------------------------------------------------
# UC-004-ALT-WEBHOOK-PUSH-REPORTING-12
# ---------------------------------------------------------------------------


@pytest.mark.requires_db
class TestWebhookEndpoint2xxAcknowledgment:
    """Endpoint acknowledges with 2xx — successful delivery recorded.

    Covers: UC-004-ALT-WEBHOOK-PUSH-REPORTING-12
    """

    def test_2xx_response_records_successful_delivery(self, integration_db):
        """200 OK from buyer endpoint records delivery as successful.

        Covers: UC-004-ALT-WEBHOOK-PUSH-REPORTING-12
        """
        from tests.harness import WebhookEnv

        with WebhookEnv() as env:
            env.set_http_status(200)

            success, result = env.call_deliver(
                webhook_url="https://buyer.example.com/webhook",
                payload={"media_buy_id": "mb_001", "impressions": 5000},
                max_retries=1,
            )

            assert success is True
            assert result["status"] == "delivered"
            assert result["response_code"] == 200
            assert result["attempts"] == 1


# ---------------------------------------------------------------------------
# UC-004-EXT-G-01
# ---------------------------------------------------------------------------


@pytest.mark.requires_db
class TestWebhook503RetryBackoff:
    """Tests that a 503 webhook endpoint triggers retries with exponential backoff.

    Covers: UC-004-EXT-G-01
    """

    def test_503_triggers_retries_with_exponential_backoff(self, integration_db):
        """When a webhook returns 503, the system retries with exponential backoff.

        Covers: UC-004-EXT-G-01
        """
        from unittest.mock import call

        from tests.harness import WebhookEnv

        with WebhookEnv() as env:
            env.set_http_status(503, "Service Unavailable")

            success, result = env.call_deliver(max_retries=4, timeout=10)

            assert success is False
            assert result["status"] == "failed"
            assert result["attempts"] == 4
            assert result["response_code"] == 503
            assert env.mock["post"].call_count == 4
            assert env.mock["sleep"].call_count == 3
            env.mock["sleep"].assert_has_calls([call(1), call(2), call(4)])

    def test_503_no_backoff_after_final_attempt(self, integration_db):
        """No sleep occurs after the last attempt — only between attempts.

        Covers: UC-004-EXT-G-01
        """
        from tests.harness import WebhookEnv

        with WebhookEnv() as env:
            env.set_http_status(503, "Service Unavailable")

            env.call_deliver(max_retries=4)

            assert env.mock["sleep"].call_count == 3
            assert env.mock["post"].call_count == 4

    def test_503_then_success_stops_retrying(self, integration_db):
        """If a retry succeeds, no further retries or backoff occur.

        Covers: UC-004-EXT-G-01
        """
        from tests.harness import WebhookEnv

        with WebhookEnv() as env:
            env.set_http_sequence([(503, "Service Unavailable"), (200, "OK")])

            success, result = env.call_deliver(max_retries=4)

            assert success is True
            assert result["status"] == "delivered"
            assert result["attempts"] == 2
            assert env.mock["post"].call_count == 2
            assert env.mock["sleep"].call_count == 1
            env.mock["sleep"].assert_called_once_with(1)

    @pytest.mark.xfail(
        reason="Production code does not add jitter to exponential backoff. "
        "BR-RULE-029 specifies '1s, 2s, 4s + jitter' but deliver_webhook_with_retry "
        "uses exact 2**attempt with no randomization.",
        strict=True,
    )
    def test_backoff_includes_jitter(self, integration_db):
        """Backoff delays should include jitter to prevent thundering herd.

        Covers: UC-004-EXT-G-01
        """
        from tests.harness import WebhookEnv

        with WebhookEnv() as env:
            env.set_http_status(503, "Service Unavailable")

            env.call_deliver(max_retries=4)

            sleep_values = [c.args[0] for c in env.mock["sleep"].call_args_list]
            exact_powers = [1, 2, 4]

            has_jitter = any(actual != expected for actual, expected in zip(sleep_values, exact_powers, strict=True))
            assert has_jitter, f"Sleep values {sleep_values} are exact powers of 2 — no jitter detected"


# ---------------------------------------------------------------------------
# UC-004-EXT-G-02
# ---------------------------------------------------------------------------


@pytest.mark.requires_db
class TestWebhookRetrySucceedsOnSecondAttempt:
    """Webhook endpoint fails first, succeeds on retry -> delivery recorded.

    Covers: UC-004-EXT-G-02
    """

    def test_transient_failure_then_success_records_delivered(self, integration_db):
        """Given a webhook that 503s then 200s, the delivery result is 'delivered'.

        Covers: UC-004-EXT-G-02
        """
        from tests.harness import WebhookEnv

        with WebhookEnv() as env:
            env.set_http_sequence([(503, "Service Unavailable"), (200, "OK")])

            success, result = env.call_deliver(
                webhook_url="https://buyer.example.com/webhook",
                payload={"media_buy_id": "mb_001", "event": "delivery.update"},
                max_retries=3,
                timeout=10,
                event_type="delivery.update",
                tenant_id="test_tenant",
                object_id="mb_001",
            )

            assert success is True
            assert result["status"] == "delivered"
            assert result["attempts"] == 2
            assert result["response_code"] == 200
            assert env.mock["sleep"].call_count == 1


# ---------------------------------------------------------------------------
# UC-004-EXT-G-05
# ---------------------------------------------------------------------------


@pytest.mark.requires_db
class TestWebhook401ForbiddenNoRetry:
    """Tests that 401 authentication errors are not retried.

    Covers: UC-004-EXT-G-05
    """

    def test_401_response_is_not_retried_and_marked_failed(self, integration_db):
        """A 401 Forbidden response must cause immediate failure with no retries.

        Covers: UC-004-EXT-G-05
        """
        from tests.harness import WebhookEnv

        with WebhookEnv() as env:
            env.set_http_status(401, "Unauthorized - invalid credentials")

            success, result = env.call_deliver(
                webhook_url="https://buyer.example.com/webhook",
                payload={"media_buy_id": "mb_001", "event": "delivery.update"},
                max_retries=3,
                timeout=10,
                event_type="delivery.update",
                tenant_id="test_tenant",
                object_id="mb_001",
            )

            assert success is False
            assert result["status"] == "failed"
            assert result["response_code"] == 401
            assert env.mock["post"].call_count == 1
            assert result["attempts"] == 1
            assert "401" in result["error"]

    def test_401_vs_500_retry_behavior_contrast(self, integration_db):
        """Verify 401 does NOT retry while 500 DOES retry.

        Covers: UC-004-EXT-G-05
        """
        from tests.harness import WebhookEnv

        # --- 401 case: should stop immediately ---
        with WebhookEnv() as env:
            env.set_http_status(401, "Unauthorized")
            success_401, result_401 = env.call_deliver(max_retries=3)

            assert success_401 is False
            assert result_401["attempts"] == 1
            assert env.mock["post"].call_count == 1

        # --- 500 case: should retry all attempts ---
        with WebhookEnv() as env:
            env.set_http_status(500, "Internal Server Error")
            success_500, result_500 = env.call_deliver(max_retries=3)

            assert success_500 is False
            assert result_500["attempts"] == 3
            assert env.mock["post"].call_count == 3


# ---------------------------------------------------------------------------
# UC-004-EXT-G-06
# ---------------------------------------------------------------------------


@pytest.mark.requires_db
class TestEXT_G_06_HmacAuthRejection:
    """HMAC auth rejection: 401/403 logs rejection, no retry, marks failed.

    Covers: UC-004-EXT-G-06
    """

    @pytest.mark.parametrize("status_code", [401, 403])
    def test_auth_rejection_no_retry_marks_failed(self, integration_db, status_code):
        """401/403 from endpoint => single attempt, no retry, status=failed.

        Covers: UC-004-EXT-G-06
        """
        from tests.harness import WebhookEnv

        with WebhookEnv() as env:
            env.set_http_status(status_code, "HMAC signature mismatch")

            success, result = env.call_deliver(
                webhook_url="https://buyer.example.com/webhook",
                payload={"media_buy_id": "mb_001", "impressions": 5000},
                signing_secret="super-secret-key-for-hmac-signing",
                max_retries=3,
                event_type="delivery.report",
                tenant_id="test_tenant",
                object_id="mb_001",
            )

            assert success is False
            assert result["status"] == "failed"
            assert result["response_code"] == status_code
            assert result["attempts"] == 1
            assert env.mock["post"].call_count == 1
            assert f"Client error {status_code}" in result["error"]

    def test_hmac_headers_sent_before_rejection(self, integration_db):
        """When signing_secret is provided, HMAC signature headers are added.

        Covers: UC-004-EXT-G-06
        """
        from tests.harness import WebhookEnv

        with WebhookEnv() as env:
            env.set_http_status(401, "Invalid signature")

            env.call_deliver(
                webhook_url="https://buyer.example.com/webhook",
                payload={"media_buy_id": "mb_001", "event": "delivery.report"},
                signing_secret="my-webhook-secret-key",
                event_type="delivery.report",
                tenant_id="test_tenant",
                object_id="mb_001",
            )

            sent_headers = env.mock["post"].call_args[1]["headers"]
            assert "X-Webhook-Signature" in sent_headers
            assert sent_headers["X-Webhook-Signature"].startswith("sha256=")
            assert "X-Webhook-Timestamp" in sent_headers

    def test_auth_rejection_vs_server_error_retry_behavior(self, integration_db):
        """Contrast: 401 does NOT retry, but 500 DOES retry.

        Covers: UC-004-EXT-G-06
        """
        from tests.harness import WebhookEnv

        # 401 case
        with WebhookEnv() as env:
            env.set_http_status(401, "Unauthorized")
            success_401, result_401 = env.call_deliver(max_retries=3, event_type="delivery.report", tenant_id="t1")
            assert success_401 is False
            assert result_401["attempts"] == 1
            assert env.mock["post"].call_count == 1

        # 500 case
        with WebhookEnv() as env:
            env.set_http_status(500, "Internal Server Error")
            success_500, result_500 = env.call_deliver(max_retries=3, event_type="delivery.report", tenant_id="t1")
            assert success_500 is False
            assert result_500["attempts"] == 3
            assert env.mock["post"].call_count == 3
