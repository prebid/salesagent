"""Meta-tests for WebhookEnv (unit variant) — verifies the harness contract.

These tests ensure the unit harness itself works correctly. They run in ``make quality``
but have no ``Covers:`` tags — they test infrastructure, not obligations.
"""

from __future__ import annotations

from tests.harness.delivery_webhook_unit import WebhookEnv
from tests.helpers.local_http_origin import responds


class TestWebhookEnvContract:
    """Contract tests for WebhookEnv (unit variant)."""

    def test_default_200_succeeds(self):
        """Default env delivers successfully with 200 OK."""
        with WebhookEnv() as env:
            success, result = env.call_deliver()

            assert success is True
            assert result["status"] == "delivered"
            assert env.delivery_attempts == 1

    def test_503_fails(self):
        """A 503 response causes delivery failure after retries."""
        with WebhookEnv() as env:
            env.set_http_status(503, "Service Unavailable")

            success, result = env.call_deliver(max_retries=2)

            assert success is False
            assert result["status"] == "failed"
            assert result["response_code"] == 503
            assert env.delivery_attempts == 2

    def test_http_sequence_for_retry(self):
        """set_http_sequence controls per-attempt responses for retry testing."""
        with WebhookEnv() as env:
            env.set_http_sequence(
                [
                    (503, "Unavailable"),
                    (200, "OK"),
                ]
            )

            success, result = env.call_deliver(max_retries=3)

            assert success is True
            assert result["attempts"] == 2
            assert env.delivery_attempts == 2

    def test_refused_url_short_circuits(self):
        """A URL production's own policy refuses fails before any request leaves."""
        with WebhookEnv() as env:
            success, result = env.call_deliver(webhook_url="http://169.254.169.254/latest/meta-data/")

            assert success is False
            assert "Invalid webhook URL" in result["error"]
            assert result["attempts"] == 0
            assert env.delivery_attempts == 0

    def test_mock_sleep_accessible(self):
        """env.mock['sleep'] captures backoff calls for assertion."""
        with WebhookEnv() as env:
            env.set_http_status(503, "Unavailable")
            env.call_deliver(max_retries=4)

            # Exponential backoff: 1s, 2s, 4s between 4 attempts
            assert env.mock["sleep"].call_count == 3
            assert env.delivery_attempts == 4

    def test_http_error_is_a_real_transport_failure(self):
        """set_http_error drops the connection, so delivery reports a transport failure.

        The seam collapses a dropped connection and a timeout into one class on
        purpose — a refusal must not become a side channel — so what production can
        still say truthfully is that the endpoint never answered. That is what this
        asserts now, rather than the requests-era "Connection error" wording.
        """
        with WebhookEnv() as env:
            env.set_http_error()

            success, result = env.call_deliver(max_retries=1)

            assert success is False
            assert "no response received" in result["error"]
            assert env.delivery_attempts == 1

    def test_stalled_response_trips_the_callers_timeout(self):
        """A response slower than the caller's timeout produces a real timeout."""
        with WebhookEnv() as env:
            env.set_http_sequence([responds(200, delay_seconds=1.5), (200, "OK")])

            success, result = env.call_deliver(max_retries=2, timeout=1)

            assert success is True
            assert result["attempts"] == 2
            assert env.delivery_attempts == 2

    def test_empty_payload_is_not_replaced_with_default(self):
        """call_deliver(payload={}) should use empty dict, not the default payload."""
        with WebhookEnv() as env:
            success, result = env.call_deliver(payload={})

            assert success is True
            # Verify the bytes that crossed the socket were the empty dict
            assert env.last_delivery.json() == {}

    def test_signing_secret_flows_through(self):
        """Signing secret parameter reaches the delivery function."""
        with WebhookEnv() as env:
            success, result = env.call_deliver(signing_secret="test-secret-padded-to-thirty-two-ch")

            assert success is True
            # Verify the endpoint received signature headers. Spec header name
            # (X-AdCP-Signature, from adcp.sign_legacy_webhook) since
            # GH #1802 -- the non-spec X-Webhook-Signature no longer exists.
            assert "X-AdCP-Signature" in env.last_delivery.headers
