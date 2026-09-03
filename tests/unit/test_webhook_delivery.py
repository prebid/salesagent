"""Unit tests for webhook delivery service with exponential backoff retry logic.

Delivery goes to a real local HTTP origin (``WebhookEnv``), not to a patched
``requests.post``. Nothing here asserts on the shape of a transport call, so
these tests keep grading the same behaviour once delivery moves onto the egress
seam (GH #1802) — which is the whole reason they were repointed
(GH #1802). What each test observes is what the endpoint actually
received and what production returned.
"""

from unittest.mock import patch

from tests.helpers.backoff_assertions import assert_backoff_schedule
from tests.helpers.local_http_origin import responds

# Reserved (link-local cloud metadata). Production's URL policy refuses it even
# under the harness's loopback allowance, which is what makes a refusal test
# grade production rather than the allowance.
RESERVED_METADATA_URL = "http://169.254.169.254/webhook"


class TestWebhookDelivery:
    """Test cases for webhook delivery with exponential backoff retry."""

    def test_successful_delivery_first_attempt(self):
        """Test successful delivery on first attempt (200 OK)."""
        from tests.harness.delivery_webhook_unit import WebhookEnv

        with WebhookEnv() as env:
            env.set_http_status(200)

            success, result = env.call_deliver(payload={"test": "data"}, max_retries=3, timeout=10)

            assert success is True
            assert result["status"] == "delivered"
            assert result["attempts"] == 1
            assert result["response_code"] == 200
            assert "delivery_id" in result
            assert env.delivery_attempts == 1

    def test_successful_delivery_after_retry(self):
        """Test successful delivery after 5xx error retry."""
        from tests.harness.delivery_webhook_unit import WebhookEnv

        with WebhookEnv() as env:
            env.set_http_sequence([(503, "Service temporarily unavailable"), (200, "OK")])

            success, result = env.call_deliver(payload={"test": "data"}, max_retries=3, timeout=10)

            assert success is True
            assert result["status"] == "delivered"
            assert result["attempts"] == 2
            assert result["response_code"] == 200
            assert env.delivery_attempts == 2

            # Should have backed off 1 second between attempts (2^0 = 1)
            assert env.mock["sleep"].call_count == 1
            assert_backoff_schedule([float(c.args[0]) for c in env.mock["sleep"].call_args_list], jitter=None)

    def test_retry_on_500_error(self):
        """Test that 5xx errors trigger retry."""
        from tests.harness.delivery_webhook_unit import WebhookEnv

        with WebhookEnv() as env:
            env.set_http_status(500, "Internal Server Error")

            success, result = env.call_deliver(payload={"test": "data"}, max_retries=3, timeout=10)

            assert success is False
            assert result["status"] == "failed"
            assert result["attempts"] == 3  # All 3 attempts used
            assert result["response_code"] == 500
            # The origin's body no longer reaches the caller: the seam does not echo
            # a counterparty response back to whoever supplied the URL. The status is
            # named, the body is not — assert both halves so a regression re-adding
            # the echo fails here.
            assert "3 attempts" in result["error"]
            assert "Internal Server Error" not in result["error"]
            assert env.delivery_attempts == 3

            # Exponential backoff: 1s + 2s (no sleep after the last attempt)
            assert_backoff_schedule([float(c.args[0]) for c in env.mock["sleep"].call_args_list], jitter=None)

    def test_no_retry_on_400_error(self):
        """Test that 4xx client errors do NOT trigger retry."""
        from tests.harness.delivery_webhook_unit import WebhookEnv

        with WebhookEnv() as env:
            env.set_http_status(400, "Bad Request")

            success, result = env.call_deliver(payload={"test": "data"}, max_retries=3, timeout=10)

            assert success is False
            assert result["status"] == "failed"
            assert result["attempts"] == 1  # No retries
            assert result["response_code"] == 400
            assert "Client error 400" in result["error"]
            assert "Bad Request" not in result["error"], "the origin's body must not be echoed back"
            assert env.delivery_attempts == 1  # Only 1 attempt

    def test_no_retry_on_404_error(self):
        """Test that 404 Not Found does NOT trigger retry."""
        from tests.harness.delivery_webhook_unit import WebhookEnv

        with WebhookEnv() as env:
            env.set_http_status(404, "Not Found")

            success, result = env.call_deliver(payload={"test": "data"}, max_retries=3, timeout=10)

            assert success is False
            assert result["attempts"] == 1  # No retries for client error
            assert env.delivery_attempts == 1

    def test_retry_on_timeout(self):
        """Test that timeout errors trigger retry.

        The origin stalls longer than the caller's timeout, so the timeout is
        real: every attempt provably arrived (it is counted) and the caller's
        own clock is what gives up.
        """
        from tests.harness.delivery_webhook_unit import WebhookEnv

        with WebhookEnv() as env:
            env.set_http_sequence([responds(200, delay_seconds=2.0)])

            success, result = env.call_deliver(payload={"test": "data"}, max_retries=3, timeout=1)

            assert success is False
            assert result["status"] == "failed"
            assert result["attempts"] == 3
            # The seam does not distinguish a timeout from a dropped connection.
            # What it still reports, truthfully, is that nothing came back.
            assert "no response received" in result["error"]
            assert env.delivery_attempts == 3

            # Exponential backoff: 1s + 2s (no sleep after the last attempt)
            assert_backoff_schedule([float(c.args[0]) for c in env.mock["sleep"].call_args_list], jitter=None)

    def test_retry_on_connection_error(self):
        """Test that connection errors trigger retry."""
        from tests.harness.delivery_webhook_unit import WebhookEnv

        with WebhookEnv() as env:
            env.set_http_error()

            success, result = env.call_deliver(payload={"test": "data"}, max_retries=3, timeout=10)

            assert success is False
            assert result["attempts"] == 3
            assert "no response received" in result["error"]
            assert env.delivery_attempts == 3

    def test_exponential_backoff_timing(self):
        """Test that backoff follows BR-RULE-029's 1s, 2s, 4s schedule."""
        from tests.harness.delivery_webhook_unit import WebhookEnv

        with WebhookEnv() as env:
            env.set_http_status(503, "Service Unavailable")

            env.call_deliver(payload={"test": "data"}, max_retries=3, timeout=10)

            # 1s after attempt 1, 2s after attempt 2 (no sleep after the last), each
            # plus the seam's jitter — which is why jitter=None (grade the window and
            # require randomisation) rather than an exact schedule.
            durations = [float(c.args[0]) for c in env.mock["sleep"].call_args_list]
            assert len(durations) == 2
            assert_backoff_schedule(durations, jitter=None)

    def test_max_retries_exceeded(self):
        """Test behavior when all retries are exhausted."""
        from tests.harness.delivery_webhook_unit import WebhookEnv

        with WebhookEnv() as env:
            env.set_http_status(502, "Bad Gateway")

            success, result = env.call_deliver(payload={"test": "data"}, max_retries=2, timeout=10)

            assert success is False
            assert result["attempts"] == 2
            assert env.delivery_attempts == 2

    def test_successful_delivery_with_202_accepted(self):
        """Test that 202 Accepted is treated as success."""
        from tests.harness.delivery_webhook_unit import WebhookEnv

        with WebhookEnv() as env:
            env.set_http_status(202)

            success, result = env.call_deliver(payload={"test": "data"})

            assert success is True
            assert result["response_code"] == 202

    def test_successful_delivery_with_204_no_content(self):
        """Test that 204 No Content is treated as success."""
        from tests.harness.delivery_webhook_unit import WebhookEnv

        with WebhookEnv() as env:
            env.set_http_status(204)

            success, result = env.call_deliver(payload={"test": "data"})

            assert success is True
            assert result["response_code"] == 204

    def test_hmac_signature_added(self):
        """The HMAC signature verifies against the exact bytes that crossed the socket.

        Recomputes over ``env.last_delivery.body`` (the raw wire bytes), not a
        fresh serialization of the payload dict -- a recompute from the dict
        can silently agree with a sender that signed one serialization and
        transmitted another, which is exactly the defect GH #1802
        fixed. Spec header name (X-AdCP-Signature, from
        adcp.sign_legacy_webhook) since GH #1802 -- the non-spec
        X-Webhook-Signature no longer exists.
        """
        from tests.harness.delivery_webhook_unit import WebhookEnv
        from tests.helpers import assert_signature_verifies_over_wire_body

        secret = "test-secret-key-padded-to-the-min"

        with WebhookEnv() as env:
            env.set_http_status(200)

            success, result = env.call_deliver(payload={"test": "data"}, signing_secret=secret)

            assert success is True
            assert_signature_verifies_over_wire_body(env.last_delivery, secret)

    def test_invalid_webhook_url_validation(self):
        """Test that invalid webhook URLs are rejected."""
        from tests.harness.delivery_webhook_unit import WebhookEnv

        with WebhookEnv() as env:
            success, result = env.call_deliver(
                webhook_url="javascript:alert('xss')",  # Invalid scheme
                payload={"test": "data"},
            )

            assert success is False
            assert "Invalid webhook URL" in result["error"]
            assert env.delivery_attempts == 0  # Should not attempt to call

    def test_reserved_address_webhook_url_rejected(self):
        """Test that reserved (link-local metadata) URLs are rejected for SSRF protection.

        This grades the same production branch the old localhost case did. It
        cannot be localhost here: the harness must allow loopback for the test
        origin to be reachable at all, so a localhost assertion would grade the
        harness's allowance instead of production's policy.
        """
        from tests.harness.delivery_webhook_unit import WebhookEnv

        with WebhookEnv() as env:
            success, result = env.call_deliver(webhook_url=RESERVED_METADATA_URL, payload={"test": "data"})

            assert success is False
            assert "Invalid webhook URL" in result["error"]
            assert env.delivery_attempts == 0

    @patch("src.core.webhook_delivery._create_delivery_record")
    @patch("src.core.webhook_delivery._update_delivery_record")
    def test_database_tracking_on_success(self, mock_update, mock_create):
        """Test that successful delivery is tracked in database."""
        from tests.harness.delivery_webhook_unit import WebhookEnv

        with WebhookEnv() as env:
            env.set_http_status(200)

            success, result = env.call_deliver(
                payload={"test": "data"},
                event_type="test.event",
                tenant_id="tenant_1",
                object_id="obj_123",
            )

            assert success is True

            # Should create initial record
            assert mock_create.call_count == 1
            create_args = mock_create.call_args.kwargs
            assert create_args["tenant_id"] == "tenant_1"
            assert create_args["event_type"] == "test.event"
            assert create_args["object_id"] == "obj_123"

            # Should update record with success
            assert mock_update.call_count == 1
            update_args = mock_update.call_args.kwargs
            assert update_args["status"] == "delivered"
            assert update_args["attempts"] == 1
            assert update_args["response_code"] == 200

    @patch("src.core.webhook_delivery._create_delivery_record")
    @patch("src.core.webhook_delivery._update_delivery_record")
    def test_database_tracking_on_failure(self, mock_update, mock_create):
        """Test that failed delivery is tracked in database."""
        from tests.harness.delivery_webhook_unit import WebhookEnv

        with WebhookEnv() as env:
            env.set_http_status(400, "Bad Request")

            success, result = env.call_deliver(
                payload={"test": "data"},
                event_type="test.event",
                tenant_id="tenant_1",
            )

            assert success is False

            # Should update record with failure
            assert mock_update.call_count == 1
            update_args = mock_update.call_args.kwargs
            assert update_args["status"] == "failed"
            assert update_args["response_code"] == 400
            assert "Client error 400" in update_args["last_error"]
            assert "Bad Request" not in update_args["last_error"]

    def test_custom_timeout(self):
        """Test that a custom timeout value is honoured, not merely passed along.

        The origin stalls for longer than the configured timeout, so a delivery
        that respects the value fails on the clock. Asserting the kwarg reached
        the transport would prove nothing once the transport changes.
        """
        from tests.harness.delivery_webhook_unit import WebhookEnv

        with WebhookEnv() as env:
            env.set_http_sequence([responds(200, delay_seconds=2.0)])

            success, result = env.call_deliver(payload={"test": "data"}, max_retries=1, timeout=1)

            assert success is False
            assert result["error"] == "Delivery failed after 1 attempts (no response received)"
            assert env.delivery_attempts == 1

    def test_result_contains_duration(self):
        """Test that result includes duration metric."""
        from tests.harness.delivery_webhook_unit import WebhookEnv

        with WebhookEnv() as env:
            env.set_http_status(200)

            success, result = env.call_deliver(payload={"test": "data"})

            assert "duration" in result
            assert isinstance(result["duration"], float)
            assert result["duration"] >= 0


class TestDefensiveExceptionHandlers:
    """Covers: defensive exception handlers in webhook_delivery.py.

    Lines 355, 357: _update_delivery_record absorbs DB exceptions.
    Lines 383-384: _set_auth_blocked absorbs DB exceptions.
    """

    def test_update_delivery_record_absorbs_db_exception(self):
        """_update_delivery_record logs and swallows DB exceptions."""
        from src.core.webhook_delivery import _update_delivery_record

        with patch(
            "src.core.webhook_delivery.get_db_session",
            side_effect=Exception("DB connection refused"),
        ):
            # Must not raise — the defensive handler absorbs the error
            _update_delivery_record(
                delivery_id="whd_test123",
                tenant_id="t1",
                status="delivered",
                attempts=1,
                response_code=200,
            )

    def test_set_auth_blocked_absorbs_db_exception(self):
        """_set_auth_blocked logs and swallows DB exceptions."""
        from src.core.webhook_delivery import _set_auth_blocked

        with patch(
            "src.core.webhook_delivery.get_db_session",
            side_effect=Exception("DB connection refused"),
        ):
            # Must not raise — the defensive handler absorbs the error
            _set_auth_blocked(
                tenant_id="t1",
                webhook_url="https://example.com/hook",
            )
