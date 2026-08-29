"""Meta-tests for WebhookEnv (unit variant) — verifies the harness contract.

These tests ensure the unit harness itself works correctly. They run in ``make quality``
but have no ``Covers:`` tags — they test infrastructure, not obligations.
"""

from __future__ import annotations

import requests

from tests.harness._mixins import raw_body_from_kwargs
from tests.harness.delivery_webhook_unit import WebhookEnv


class TestWebhookEnvContract:
    """Contract tests for WebhookEnv (unit variant)."""

    def test_default_200_succeeds(self):
        """Default env delivers successfully with 200 OK."""
        with WebhookEnv() as env:
            success, result = env.call_deliver()

            assert success is True
            assert result["status"] == "delivered"

    def test_503_fails(self):
        """A 503 response causes delivery failure after retries."""
        with WebhookEnv() as env:
            env.set_http_status(503, "Service Unavailable")

            success, result = env.call_deliver(max_retries=2)

            assert success is False
            assert result["status"] == "failed"
            assert result["response_code"] == 503

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

    def test_invalid_url_short_circuits(self):
        """set_url_invalid causes immediate failure without HTTP calls."""
        with WebhookEnv() as env:
            env.set_url_invalid("Private IP not allowed")

            success, result = env.call_deliver()

            assert success is False
            assert "Invalid webhook URL" in result["error"]
            assert result["attempts"] == 0
            env.mock["post"].assert_not_called()

    def test_mock_sleep_accessible(self):
        """env.mock['sleep'] captures backoff calls for assertion."""
        with WebhookEnv() as env:
            env.set_http_status(503, "Unavailable")
            env.call_deliver(max_retries=4)

            # Exponential backoff: 1s, 2s, 4s between 4 attempts
            assert env.mock["sleep"].call_count == 3

    def test_http_error_raises(self):
        """set_http_error makes requests.post raise an exception."""
        with WebhookEnv() as env:
            env.set_http_error(requests.exceptions.ConnectionError("Connection refused"))

            success, result = env.call_deliver(max_retries=1)

            assert success is False
            assert "Connection" in result.get("error", "")

    def test_empty_payload_is_not_replaced_with_default(self):
        """call_deliver(payload={}) should use empty dict, not the default payload."""
        with WebhookEnv() as env:
            success, result = env.call_deliver(payload={})

            assert success is True
            # Verify POST was called with the empty dict, not the default
            import json as json_mod

            call_kwargs = env.mock["post"].call_args.kwargs
            assert json_mod.loads(call_kwargs["data"]) == {}

    def test_signing_secret_flows_through(self):
        """Signing secret parameter reaches the delivery function."""
        with WebhookEnv() as env:
            success, result = env.call_deliver(signing_secret="test-secret")

            assert success is True
            # Verify POST was called with spec-format signature headers, graded
            # through the one shared helper (this site was presence-only: no
            # sha256=-prefix, no unix-seconds).
            from tests.helpers.webhook_hmac import assert_signature_headers_present

            call_kwargs = env.mock["post"].call_args.kwargs
            assert_signature_headers_present(call_kwargs["headers"])


class TestWebhookWireSeam:
    """``raw_body_from_kwargs`` / ``last_webhook_wire`` — the transmitted-request read."""

    def test_raw_body_selects_on_presence_not_truthiness(self):
        """An empty ``data=`` body wins over ``content=``; it is present, not absent.

        The two kwargs are alternative spellings of the SAME body, so a request
        that carries ``data=`` must never be read through ``content=``. An
        ``or`` chain would fall through whenever the body is falsy — and an
        empty body is exactly the case a signature still has to cover.
        """
        assert raw_body_from_kwargs({"data": b"", "content": b"other"}) == b""
        assert raw_body_from_kwargs({"data": b'{"a":1}'}) == b'{"a":1}'
        assert raw_body_from_kwargs({"content": b'{"a":1}'}) == b'{"a":1}'
        assert raw_body_from_kwargs({}) is None

    def test_last_webhook_wire_returns_transmitted_body_and_headers(self):
        """The seam returns the bytes and headers of the POST the sender made."""
        with WebhookEnv() as env:
            success, _ = env.call_deliver(payload={"event": "delivery.update"}, signing_secret="test-secret")
            assert success is True

            body, headers = env.last_webhook_wire()

            call_kwargs = env.mock["post"].call_args.kwargs
            assert body == call_kwargs["data"]
            assert headers == call_kwargs["headers"]
