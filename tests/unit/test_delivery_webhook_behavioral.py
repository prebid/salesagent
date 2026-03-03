"""Behavioral tests for UC-004 webhook delivery (deliver_webhook_with_retry).

Tests the webhook retry logic, HMAC signing, URL validation, and HTTP error handling
against per-obligation scenarios.

Split from test_delivery_behavioral.py — see also:
- test_delivery_poll_behavioral.py (_get_media_buy_delivery_impl)
- test_delivery_service_behavioral.py (WebhookDeliveryService, CircuitBreaker)

Each test targets exactly one obligation ID and follows the 6 hard rules:
1. MUST import from src.
2. MUST call production function
3. MUST assert production output
4. MUST have Covers: tag
5. MUST use factories where applicable (helpers here — no ORM factories for unit)
6. MUST NOT be mock-echo only
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from unittest.mock import MagicMock, Mock, call, patch

import pytest

from src.core.resolved_identity import ResolvedIdentity
from src.core.testing_hooks import AdCPTestContext
from src.core.webhook_authenticator import WebhookAuthenticator
from src.core.webhook_delivery import WebhookDelivery, deliver_webhook_with_retry

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
# UC-004-ALT-WEBHOOK-PUSH-REPORTING-01
# ---------------------------------------------------------------------------


class TestWebhookDeliveryHappyPath:
    """Scheduled webhook delivery happy path — POST with signed payload.

    Covers: UC-004-ALT-WEBHOOK-PUSH-REPORTING-01
    """

    @patch("src.core.webhook_delivery.get_db_session")
    @patch("src.core.webhook_delivery.WebhookURLValidator.validate_webhook_url", return_value=(True, None))
    @patch("src.core.webhook_delivery.requests.post")
    def test_webhook_sends_signed_payload(self, mock_post, mock_validate, mock_db_session):
        """Webhook delivery sends POST to configured URL with HMAC-signed payload.

        Covers: UC-004-ALT-WEBHOOK-PUSH-REPORTING-01
        """
        # Arrange
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_post.return_value = mock_response
        mock_db_session.return_value.__enter__ = MagicMock()
        mock_db_session.return_value.__exit__ = MagicMock(return_value=False)

        delivery = WebhookDelivery(
            webhook_url="https://buyer.example.com/webhook",
            payload={
                "media_buy_id": "mb_001",
                "impressions": 5000,
                "spend": 250.0,
                "notification_type": "scheduled",
            },
            headers={"Content-Type": "application/json"},
            signing_secret="test-secret-key",
            max_retries=1,
        )

        # Act
        success, result = deliver_webhook_with_retry(delivery)

        # Assert — delivery succeeded with signed payload
        assert success is True
        assert result["status"] == "delivered"

        # Verify POST was called with correct URL
        call_args = mock_post.call_args
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
# UC-004-ALT-WEBHOOK-PUSH-REPORTING-02
# ---------------------------------------------------------------------------


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
        payload = {"media_buy_id": "mb_001", "impressions": 5000}
        secret = "test-signing-secret"

        # Act
        headers = WebhookAuthenticator.sign_payload(payload, secret)

        # Assert — HMAC-SHA256 signature header present with correct format
        assert "X-Webhook-Signature" in headers
        assert headers["X-Webhook-Signature"].startswith("sha256=")
        assert len(headers["X-Webhook-Signature"]) > len("sha256=")

        # Assert — timestamp header present for replay protection
        assert "X-Webhook-Timestamp" in headers
        assert headers["X-Webhook-Timestamp"].isdigit()


# ---------------------------------------------------------------------------
# UC-004-ALT-WEBHOOK-PUSH-REPORTING-08
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# UC-004-ALT-WEBHOOK-PUSH-REPORTING-08
# ---------------------------------------------------------------------------


class TestWebhookBearerTokenAuth:
    """Webhook delivery with Bearer token authentication.

    Covers: UC-004-ALT-WEBHOOK-PUSH-REPORTING-08
    """

    @patch("src.core.webhook_delivery.get_db_session")
    @patch("src.core.webhook_delivery.WebhookURLValidator.validate_webhook_url", return_value=(True, None))
    @patch("src.core.webhook_delivery.requests.post")
    def test_bearer_token_sent_in_authorization_header(self, mock_post, mock_validate, mock_db_session):
        """Bearer token is forwarded in Authorization header when set by caller.

        Covers: UC-004-ALT-WEBHOOK-PUSH-REPORTING-08
        """
        # Arrange
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_post.return_value = mock_response
        mock_db_session.return_value.__enter__ = MagicMock()
        mock_db_session.return_value.__exit__ = MagicMock(return_value=False)

        delivery = WebhookDelivery(
            webhook_url="https://buyer.example.com/webhook",
            payload={"media_buy_id": "mb_001"},
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer test-bearer-token-xyz",
            },
            max_retries=1,
        )

        # Act
        success, result = deliver_webhook_with_retry(delivery)

        # Assert — delivery succeeded
        assert success is True
        assert result["status"] == "delivered"

        # Assert — Bearer token was sent in the request headers
        call_args = mock_post.call_args
        sent_headers = call_args.kwargs["headers"]
        assert sent_headers["Authorization"] == "Bearer test-bearer-token-xyz"


# ---------------------------------------------------------------------------
# UC-004-ALT-WEBHOOK-PUSH-REPORTING-09
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# UC-004-ALT-WEBHOOK-PUSH-REPORTING-11
# ---------------------------------------------------------------------------


class TestWebhookOnlyActiveMediaBuys:
    """Only active media buys trigger webhook delivery.

    Covers: UC-004-ALT-WEBHOOK-PUSH-REPORTING-11
    """

    @pytest.mark.xfail(
        reason="deliver_webhook_with_retry does not check media buy status. "
        "It sends whatever payload is given regardless of the media buy's state. "
        "Webhook trigger scheduler with status filtering not yet implemented."
    )
    @patch("src.core.webhook_delivery.get_db_session")
    @patch("src.core.webhook_delivery.WebhookURLValidator.validate_webhook_url", return_value=(True, None))
    @patch("src.core.webhook_delivery.requests.post")
    def test_paused_media_buy_webhook_rejected(self, mock_post, mock_validate, mock_db_session):
        """Webhook delivery should be rejected for paused media buys.

        Covers: UC-004-ALT-WEBHOOK-PUSH-REPORTING-11
        """
        # Arrange — webhook for a paused media buy
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_post.return_value = mock_response
        mock_db_session.return_value.__enter__ = MagicMock()
        mock_db_session.return_value.__exit__ = MagicMock(return_value=False)

        delivery = WebhookDelivery(
            webhook_url="https://buyer.example.com/webhook",
            payload={"media_buy_id": "mb_paused", "status": "paused"},
            headers={"Content-Type": "application/json"},
            max_retries=1,
        )

        # Act
        success, result = deliver_webhook_with_retry(delivery)

        # Assert — should NOT deliver webhook for paused media buy
        assert success is False, "Webhook should not be delivered for paused media buy"


# ---------------------------------------------------------------------------
# UC-004-ALT-WEBHOOK-PUSH-REPORTING-12
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# UC-004-ALT-WEBHOOK-PUSH-REPORTING-12
# ---------------------------------------------------------------------------


class TestWebhookEndpoint2xxAcknowledgment:
    """Endpoint acknowledges with 2xx — successful delivery recorded.

    Covers: UC-004-ALT-WEBHOOK-PUSH-REPORTING-12
    """

    @patch("src.core.webhook_delivery.get_db_session")
    @patch("src.core.webhook_delivery.WebhookURLValidator.validate_webhook_url", return_value=(True, None))
    @patch("src.core.webhook_delivery.requests.post")
    def test_2xx_response_records_successful_delivery(self, mock_post, mock_validate, mock_db_session):
        """200 OK from buyer endpoint records delivery as successful.

        Covers: UC-004-ALT-WEBHOOK-PUSH-REPORTING-12
        """
        # Arrange
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_post.return_value = mock_response
        mock_db_session.return_value.__enter__ = MagicMock()
        mock_db_session.return_value.__exit__ = MagicMock(return_value=False)

        delivery = WebhookDelivery(
            webhook_url="https://buyer.example.com/webhook",
            payload={"media_buy_id": "mb_001", "impressions": 5000},
            headers={"Content-Type": "application/json"},
            max_retries=1,
        )

        # Act
        success, result = deliver_webhook_with_retry(delivery)

        # Assert — delivery recorded as successful
        assert success is True
        assert result["status"] == "delivered"
        assert result["response_code"] == 200
        assert result["attempts"] == 1


# ---------------------------------------------------------------------------
# UC-004-EXT-A-02
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# UC-004-EXT-G-01
# ---------------------------------------------------------------------------


class TestWebhook503RetryBackoff:
    """Tests that a 503 webhook endpoint triggers retries with exponential backoff.

    Covers: UC-004-EXT-G-01
    """

    @patch("src.core.webhook_delivery.WebhookURLValidator.validate_webhook_url", return_value=(True, None))
    @patch("src.core.webhook_delivery.time.sleep")
    @patch("src.core.webhook_delivery.requests.post")
    def test_503_triggers_retries_with_exponential_backoff(self, mock_post, mock_sleep, mock_validate):
        """When a webhook returns 503, the system retries with exponential backoff.

        Covers: UC-004-EXT-G-01

        Verifies:
        - All attempts are made (max_retries controls total attempts)
        - Backoff delays follow 2^attempt pattern (1s, 2s, 4s)
        - Final result is failure with correct attempt count
        """
        mock_response = MagicMock()
        mock_response.status_code = 503
        mock_response.text = "Service Unavailable"
        mock_post.return_value = mock_response

        delivery = WebhookDelivery(
            webhook_url="https://example.com/webhook",
            payload={"event": "delivery.update", "media_buy_id": "mb_001"},
            headers={"Content-Type": "application/json"},
            max_retries=4,  # 1 initial + 3 retries = 4 total attempts
            timeout=10,
            tenant_id=None,
            event_type=None,
        )

        success, result = deliver_webhook_with_retry(delivery)

        assert success is False
        assert result["status"] == "failed"
        assert result["attempts"] == 4
        assert result["response_code"] == 503
        assert mock_post.call_count == 4
        assert mock_sleep.call_count == 3
        mock_sleep.assert_has_calls(
            [
                call(1),  # 2^0 = 1s after attempt 0
                call(2),  # 2^1 = 2s after attempt 1
                call(4),  # 2^2 = 4s after attempt 2
            ]
        )

    @patch("src.core.webhook_delivery.WebhookURLValidator.validate_webhook_url", return_value=(True, None))
    @patch("src.core.webhook_delivery.time.sleep")
    @patch("src.core.webhook_delivery.requests.post")
    def test_503_no_backoff_after_final_attempt(self, mock_post, mock_sleep, mock_validate):
        """No sleep occurs after the last attempt — only between attempts.

        Covers: UC-004-EXT-G-01

        With max_retries=4, there should be 3 sleeps (not 4).
        """
        mock_response = MagicMock()
        mock_response.status_code = 503
        mock_response.text = "Service Unavailable"
        mock_post.return_value = mock_response

        delivery = WebhookDelivery(
            webhook_url="https://example.com/webhook",
            payload={"event": "test"},
            headers={},
            max_retries=4,
            tenant_id=None,
            event_type=None,
        )

        success, result = deliver_webhook_with_retry(delivery)

        assert mock_sleep.call_count == 3
        assert mock_post.call_count == 4

    @patch("src.core.webhook_delivery.WebhookURLValidator.validate_webhook_url", return_value=(True, None))
    @patch("src.core.webhook_delivery.time.sleep")
    @patch("src.core.webhook_delivery.requests.post")
    def test_503_then_success_stops_retrying(self, mock_post, mock_sleep, mock_validate):
        """If a retry succeeds, no further retries or backoff occur.

        Covers: UC-004-EXT-G-01

        First attempt returns 503, second attempt returns 200.
        """
        fail_response = MagicMock()
        fail_response.status_code = 503
        fail_response.text = "Service Unavailable"

        ok_response = MagicMock()
        ok_response.status_code = 200
        ok_response.text = "OK"

        mock_post.side_effect = [fail_response, ok_response]

        delivery = WebhookDelivery(
            webhook_url="https://example.com/webhook",
            payload={"event": "delivery.update"},
            headers={},
            max_retries=4,
            tenant_id=None,
            event_type=None,
        )

        success, result = deliver_webhook_with_retry(delivery)

        assert success is True
        assert result["status"] == "delivered"
        assert result["attempts"] == 2
        assert mock_post.call_count == 2
        assert mock_sleep.call_count == 1
        mock_sleep.assert_called_once_with(1)

    @pytest.mark.xfail(
        reason="Production code does not add jitter to exponential backoff. "
        "BR-RULE-029 specifies '1s, 2s, 4s + jitter' but deliver_webhook_with_retry "
        "(src/core/webhook_delivery.py:226-228) uses exact 2**attempt with no randomization. "
        "Jitter would be added at line 226 with e.g. backoff_time = 2**attempt + random.uniform(0, 0.5).",
        strict=True,
    )
    @patch("src.core.webhook_delivery.WebhookURLValidator.validate_webhook_url", return_value=(True, None))
    @patch("src.core.webhook_delivery.time.sleep")
    @patch("src.core.webhook_delivery.requests.post")
    def test_backoff_includes_jitter(self, mock_post, mock_sleep, mock_validate):
        """Backoff delays should include jitter to prevent thundering herd.

        Covers: UC-004-EXT-G-01

        BR-RULE-029 specifies exponential backoff with jitter.
        Current implementation uses exact powers of 2 without randomization.
        """
        mock_response = MagicMock()
        mock_response.status_code = 503
        mock_response.text = "Service Unavailable"
        mock_post.return_value = mock_response

        delivery = WebhookDelivery(
            webhook_url="https://example.com/webhook",
            payload={"event": "test"},
            headers={},
            max_retries=4,
            tenant_id=None,
            event_type=None,
        )

        deliver_webhook_with_retry(delivery)

        sleep_values = [c.args[0] for c in mock_sleep.call_args_list]
        exact_powers = [1, 2, 4]

        has_jitter = any(actual != expected for actual, expected in zip(sleep_values, exact_powers, strict=True))
        assert has_jitter, f"Sleep values {sleep_values} are exact powers of 2 — no jitter detected"


# ---------------------------------------------------------------------------
# UC-004-EXT-G-02
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# UC-004-EXT-G-02
# ---------------------------------------------------------------------------


class TestWebhookRetrySucceedsOnSecondAttempt:
    """Webhook endpoint fails first, succeeds on retry -> delivery recorded as successful.

    Covers: UC-004-EXT-G-02
    """

    @patch("src.core.webhook_delivery.time.sleep")
    @patch("src.core.webhook_delivery._update_delivery_record")
    @patch("src.core.webhook_delivery._create_delivery_record")
    @patch("src.core.webhook_delivery.WebhookURLValidator.validate_webhook_url", return_value=(True, ""))
    @patch("requests.post")
    def test_transient_failure_then_success_records_delivered(
        self, mock_post, mock_validator, mock_create, mock_update, mock_sleep
    ):
        """Given a webhook that 503s then 200s, the delivery result is 'delivered'.

        Covers: UC-004-EXT-G-02
        """
        resp_503 = Mock()
        resp_503.status_code = 503
        resp_503.text = "Service Unavailable"

        resp_200 = Mock()
        resp_200.status_code = 200

        mock_post.side_effect = [resp_503, resp_200]

        delivery = WebhookDelivery(
            webhook_url="https://buyer.example.com/webhook",
            payload={"media_buy_id": "mb_001", "event": "delivery.update"},
            headers={"Content-Type": "application/json"},
            max_retries=3,
            timeout=10,
            event_type="delivery.update",
            tenant_id="test_tenant",
            object_id="mb_001",
        )

        success, result = deliver_webhook_with_retry(delivery)

        assert success is True
        assert result["status"] == "delivered"
        assert result["attempts"] == 2
        assert result["response_code"] == 200

        assert mock_update.call_count == 1
        update_kwargs = mock_update.call_args.kwargs
        assert update_kwargs["status"] == "delivered"
        assert update_kwargs["attempts"] == 2
        assert update_kwargs["response_code"] == 200
        assert update_kwargs["delivered_at"] is not None

        mock_sleep.assert_called_once_with(1)

        assert mock_create.call_count == 1
        create_kwargs = mock_create.call_args.kwargs
        assert create_kwargs["tenant_id"] == "test_tenant"
        assert create_kwargs["event_type"] == "delivery.update"


# ---------------------------------------------------------------------------
# UC-004-EXT-G-03
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# UC-004-EXT-G-05
# ---------------------------------------------------------------------------


class TestWebhook401ForbiddenNoRetry:
    """Tests that 401 authentication errors are not retried.

    Covers: UC-004-EXT-G-05
    """

    @patch("src.core.webhook_delivery._update_delivery_record")
    @patch("src.core.webhook_delivery._create_delivery_record")
    @patch("src.core.webhook_delivery.WebhookURLValidator.validate_webhook_url")
    @patch("src.core.webhook_delivery.requests.post")
    def test_401_response_is_not_retried_and_marked_failed(
        self, mock_post, mock_validate, mock_create_record, mock_update_record
    ):
        """A 401 Forbidden response must cause immediate failure with no retries.

        Covers: UC-004-EXT-G-05
        """
        # Arrange: URL validation passes
        mock_validate.return_value = (True, None)

        # Arrange: endpoint returns 401 Forbidden
        mock_response = Mock()
        mock_response.status_code = 401
        mock_response.text = "Unauthorized - invalid credentials"
        mock_post.return_value = mock_response

        delivery = WebhookDelivery(
            webhook_url="https://buyer.example.com/webhook",
            payload={"media_buy_id": "mb_001", "event": "delivery.update"},
            headers={"Content-Type": "application/json"},
            max_retries=3,
            timeout=10,
            event_type="delivery.update",
            tenant_id="test_tenant",
            object_id="mb_001",
        )

        # Act
        success, result = deliver_webhook_with_retry(delivery)

        # Assert: delivery failed
        assert success is False
        assert result["status"] == "failed"
        assert result["response_code"] == 401

        # Assert: exactly 1 attempt -- NO retries for 4xx
        assert mock_post.call_count == 1
        assert result["attempts"] == 1

        # Assert: error message contains the status code
        assert "401" in result["error"]

    @patch("src.core.webhook_delivery._update_delivery_record")
    @patch("src.core.webhook_delivery._create_delivery_record")
    @patch("src.core.webhook_delivery.WebhookURLValidator.validate_webhook_url")
    @patch("src.core.webhook_delivery.requests.post")
    def test_401_vs_500_retry_behavior_contrast(self, mock_post, mock_validate, mock_create_record, mock_update_record):
        """Verify 401 does NOT retry while 500 DOES retry -- proves the branch matters.

        Covers: UC-004-EXT-G-05
        """
        mock_validate.return_value = (True, None)

        # --- 401 case: should stop immediately ---
        mock_response_401 = Mock()
        mock_response_401.status_code = 401
        mock_response_401.text = "Unauthorized"
        mock_post.return_value = mock_response_401

        delivery_401 = WebhookDelivery(
            webhook_url="https://buyer.example.com/webhook",
            payload={"event": "delivery"},
            headers={},
            max_retries=3,
        )
        success_401, result_401 = deliver_webhook_with_retry(delivery_401)

        assert success_401 is False
        assert result_401["attempts"] == 1
        calls_for_401 = mock_post.call_count

        # Reset mock
        mock_post.reset_mock()

        # --- 500 case: should retry all 3 attempts ---
        mock_response_500 = Mock()
        mock_response_500.status_code = 500
        mock_response_500.text = "Internal Server Error"
        mock_post.return_value = mock_response_500

        delivery_500 = WebhookDelivery(
            webhook_url="https://buyer.example.com/webhook",
            payload={"event": "delivery"},
            headers={},
            max_retries=3,
        )
        success_500, result_500 = deliver_webhook_with_retry(delivery_500)

        assert success_500 is False
        assert result_500["attempts"] == 3
        calls_for_500 = mock_post.call_count

        # The key contrast: 401 = 1 call, 500 = 3 calls
        assert calls_for_401 == 1
        assert calls_for_500 == 3


# ---------------------------------------------------------------------------
# UC-004-EXT-G-06
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# UC-004-EXT-G-06
# ---------------------------------------------------------------------------


class TestEXT_G_06_HmacAuthRejection:
    """HMAC auth rejection: 401/403 logs rejection, no retry, marks failed.

    Covers: UC-004-EXT-G-06
    """

    @pytest.mark.parametrize("status_code", [401, 403])
    @patch("src.core.webhook_delivery._update_delivery_record")
    @patch("src.core.webhook_delivery._create_delivery_record")
    @patch("src.core.webhook_delivery.requests.post")
    @patch("src.core.webhook_delivery.WebhookURLValidator.validate_webhook_url")
    def test_auth_rejection_no_retry_marks_failed(
        self,
        mock_validate,
        mock_post,
        mock_create_record,
        mock_update_record,
        status_code,
    ):
        """401/403 from endpoint => single attempt, no retry, status=failed."""
        mock_validate.return_value = (True, None)

        mock_response = Mock()
        mock_response.status_code = status_code
        mock_response.text = "HMAC signature mismatch"
        mock_post.return_value = mock_response

        delivery = WebhookDelivery(
            webhook_url="https://buyer.example.com/webhook",
            payload={"media_buy_id": "mb_001", "impressions": 5000},
            headers={"Content-Type": "application/json"},
            max_retries=3,
            signing_secret="super-secret-key-for-hmac-signing",
            event_type="delivery.report",
            tenant_id="test_tenant",
            object_id="mb_001",
        )

        success, result = deliver_webhook_with_retry(delivery)

        assert success is False
        assert result["status"] == "failed"
        assert result["response_code"] == status_code
        assert result["attempts"] == 1
        assert mock_post.call_count == 1
        assert f"Client error {status_code}" in result["error"]

        mock_update_record.assert_called_once()
        update_kwargs = mock_update_record.call_args
        assert update_kwargs[1]["status"] == "failed"
        assert update_kwargs[1]["response_code"] == status_code

    @patch("src.core.webhook_delivery._update_delivery_record")
    @patch("src.core.webhook_delivery._create_delivery_record")
    @patch("src.core.webhook_delivery.requests.post")
    @patch("src.core.webhook_delivery.WebhookURLValidator.validate_webhook_url")
    def test_hmac_headers_sent_before_rejection(
        self,
        mock_validate,
        mock_post,
        mock_create_record,
        mock_update_record,
    ):
        """When signing_secret is provided, HMAC signature headers are added to request."""
        mock_validate.return_value = (True, None)

        mock_response = Mock()
        mock_response.status_code = 401
        mock_response.text = "Invalid signature"
        mock_post.return_value = mock_response

        payload = {"media_buy_id": "mb_001", "event": "delivery.report"}
        delivery = WebhookDelivery(
            webhook_url="https://buyer.example.com/webhook",
            payload=payload,
            headers={"Content-Type": "application/json"},
            signing_secret="my-webhook-secret-key",
            event_type="delivery.report",
            tenant_id="test_tenant",
            object_id="mb_001",
        )

        deliver_webhook_with_retry(delivery)

        sent_headers = mock_post.call_args[1]["headers"]
        assert "X-Webhook-Signature" in sent_headers
        assert sent_headers["X-Webhook-Signature"].startswith("sha256=")
        assert "X-Webhook-Timestamp" in sent_headers

    @patch("src.core.webhook_delivery._update_delivery_record")
    @patch("src.core.webhook_delivery._create_delivery_record")
    @patch("src.core.webhook_delivery.requests.post")
    @patch("src.core.webhook_delivery.WebhookURLValidator.validate_webhook_url")
    def test_auth_rejection_vs_server_error_retry_behavior(
        self,
        mock_validate,
        mock_post,
        mock_create_record,
        mock_update_record,
    ):
        """Contrast: 401 does NOT retry, but 500 DOES retry -- proves branching."""
        mock_validate.return_value = (True, None)

        mock_response_401 = Mock()
        mock_response_401.status_code = 401
        mock_response_401.text = "Unauthorized"
        mock_post.return_value = mock_response_401

        delivery_auth_fail = WebhookDelivery(
            webhook_url="https://buyer.example.com/webhook",
            payload={"test": True},
            headers={},
            max_retries=3,
            event_type="delivery.report",
            tenant_id="t1",
        )

        success_401, result_401 = deliver_webhook_with_retry(delivery_auth_fail)
        assert success_401 is False
        assert result_401["attempts"] == 1
        assert mock_post.call_count == 1

        mock_post.reset_mock()
        mock_response_500 = Mock()
        mock_response_500.status_code = 500
        mock_response_500.text = "Internal Server Error"
        mock_post.return_value = mock_response_500

        delivery_server_fail = WebhookDelivery(
            webhook_url="https://buyer.example.com/webhook",
            payload={"test": True},
            headers={},
            max_retries=3,
            event_type="delivery.report",
            tenant_id="t1",
        )

        with patch("src.core.webhook_delivery.time.sleep"):
            success_500, result_500 = deliver_webhook_with_retry(delivery_server_fail)

        assert success_500 is False
        assert result_500["attempts"] == 3
        assert mock_post.call_count == 3


# ---------------------------------------------------------------------------
# UC-004-EXT-G-07
# ---------------------------------------------------------------------------
