"""Regression tests for ProtocolWebhookService signing byte-equality (#1546 Fix 2).

The transmitted webhook body must be byte-for-byte identical to the bytes the
signature covers. Before the fix the service signed a compact/spaced JSON but
transmitted a re-serialized ``json=payload`` body, so a receiver recomputing the
HMAC over the body it actually received could compute a different digest.

These tests reconstruct the signature the receiver would verify over the ACTUAL
transmitted bytes and assert it matches the header the service sent.
"""

import asyncio
import hashlib
import hmac
from types import SimpleNamespace
from unittest.mock import MagicMock

from adcp import create_mcp_webhook_payload

from src.services.protocol_webhook_service import ProtocolWebhookService


def _capture_service():
    """A service whose pooled session is replaced by a capturing mock."""
    service = ProtocolWebhookService()
    mock_session = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.close.return_value = None
    mock_session.post.return_value = mock_response
    service._session = mock_session
    return service, mock_session


def _payload():
    return create_mcp_webhook_payload(
        task_id="task_123",
        status="completed",
        task_type="create_media_buy",
        result={"media_buy_id": "mb_1", "nested": {"a": 1, "b": "x"}},
    )


def _send(service, config):
    return asyncio.run(
        service.send_notification(
            push_notification_config=config,
            payload=_payload(),
            metadata={},
        )
    )


class TestLegacyHmacBytePinning:
    """Legacy HMAC-SHA256 profile: signature covers the exact transmitted bytes."""

    def test_signature_verifies_over_transmitted_body_bytes(self):
        secret = "shared-webhook-secret"
        config = SimpleNamespace(
            url="https://buyer.example.com/webhook",
            authentication_type="HMAC-SHA256",
            authentication_token=secret,
        )
        service, mock_session = _capture_service()

        assert _send(service, config) is True

        kwargs = mock_session.post.call_args.kwargs
        # MUST transmit raw bytes, NOT json= (which would re-serialize to spaced JSON).
        assert "json" not in kwargs, "must not use json= (re-serializes to different bytes)"
        body = kwargs["data"]
        assert isinstance(body, bytes)

        headers = kwargs["headers"]
        # Signature applies -> the POST is never unsigned.
        assert "X-AdCP-Signature" in headers
        assert "X-AdCP-Timestamp" in headers

        # Reconstruct exactly what a receiver verifies: HMAC(secret, "{ts}.{body}")
        # over the bytes ACTUALLY transmitted.
        ts = headers["X-AdCP-Timestamp"]
        signed_message = f"{ts}.{body.decode('utf-8')}".encode()
        expected = "sha256=" + hmac.new(secret.encode("utf-8"), signed_message, hashlib.sha256).hexdigest()
        assert headers["X-AdCP-Signature"] == expected

    def test_transmitted_body_is_compact_canonical(self):
        config = SimpleNamespace(
            url="https://buyer.example.com/webhook",
            authentication_type="HMAC-SHA256",
            authentication_token="s",
        )
        service, mock_session = _capture_service()
        _send(service, config)

        body = mock_session.post.call_args.kwargs["data"]
        # Canonical compact form has no spaced separators.
        assert b", " not in body
        assert b'": ' not in body


class TestBearerAndUnauthenticatedBytes:
    """Non-HMAC profiles still transmit canonical bytes via data= (never json=)."""

    def test_bearer_transmits_bytes_and_sets_authorization(self):
        config = SimpleNamespace(
            url="https://buyer.example.com/webhook",
            authentication_type="Bearer",
            authentication_token="tok",
        )
        service, mock_session = _capture_service()
        _send(service, config)

        kwargs = mock_session.post.call_args.kwargs
        assert "json" not in kwargs
        assert isinstance(kwargs["data"], bytes)
        assert kwargs["headers"]["Authorization"] == "Bearer tok"
        # Bearer is not a signature profile.
        assert "X-AdCP-Signature" not in kwargs["headers"]

    def test_unauthenticated_transmits_canonical_bytes(self):
        config = SimpleNamespace(
            url="https://buyer.example.com/webhook",
            authentication_type=None,
            authentication_token=None,
        )
        service, mock_session = _capture_service()
        _send(service, config)

        kwargs = mock_session.post.call_args.kwargs
        assert "json" not in kwargs
        assert isinstance(kwargs["data"], bytes)
        assert b", " not in kwargs["data"]
