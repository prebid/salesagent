"""Wire-level HMAC verification: signatures must verify over RECEIVED bytes.

Every earlier signature test in this repo either read the payload back out of
a transport mock as a dict, or "verified" by re-running the sender's own
re-serialization — so the one property that matters (the HMAC verifies over
the raw bytes a receiver actually gets, per the AdCP legacy-HMAC rule
``{timestamp}.{raw_http_body}``) was never tested against a real transport.

These tests drive the real senders through real ``requests``/``httpx`` wire
serialization at a live localhost receiver (subclassing the shared e2e
capture handler per its own docstring) and verify the HMAC over the exact
bytes the socket delivered — the receiving half of #1441.

No DB required: the site-3 sender skips delivery records without tenant_id,
and the site-1 sender only writes logs via metadata we omit.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
from http.server import HTTPServer
from threading import Thread

import pytest

from src.core.webhook_authenticator import WebhookAuthenticator
from src.services.webhook_verification import verify_adcp_webhook
from tests.e2e._webhook_capture import WebhookCaptureHandler

pytestmark = [pytest.mark.integration]

SECRET = "wire-signature-secret-0123456789abcdef"  # 32+ chars


class RawCaptureHandler(WebhookCaptureHandler):
    """Capture the RAW request bytes and headers (not the parsed JSON).

    The shared handler stores ``json.loads(body)`` — useless for signature
    verification, which must see the exact bytes. Subclass per the module's
    own guidance, with our own class-level store.
    """

    received_raw: list[tuple[bytes, dict[str, str]]] = []

    def do_POST(self):  # noqa: N802 - BaseHTTPRequestHandler API
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)
        RawCaptureHandler.received_raw.append((body, dict(self.headers.items())))
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"status": "received"}')


@pytest.fixture
def capture_server():
    """Loopback HTTP server recording raw bodies + headers."""
    import socket

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    RawCaptureHandler.received_raw = []
    server = HTTPServer(("127.0.0.1", port), RawCaptureHandler)
    Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{port}/webhook"
    finally:
        server.shutdown()
        server.server_close()


def _assert_hmac_over_received_bytes(body: bytes, headers: dict[str, str]) -> None:
    """The core oracle: recompute the HMAC over the bytes the socket delivered."""
    headers_lower = {k.lower(): v for k, v in headers.items()}
    signature = headers_lower["x-adcp-signature"].removeprefix("sha256=")
    timestamp = headers_lower["x-adcp-timestamp"]
    assert timestamp.isdigit(), f"spec timestamp is unix seconds, got {timestamp!r}"

    expected = hmac.new(SECRET.encode("utf-8"), timestamp.encode("utf-8") + b"." + body, hashlib.sha256).hexdigest()
    assert signature == expected, "HMAC does not verify over the received raw bytes"

    # And both in-repo receiver references agree, fed the RAW body:
    assert WebhookAuthenticator.verify_signature(
        body.decode("utf-8"), headers_lower["x-adcp-signature"], timestamp, SECRET
    )
    assert verify_adcp_webhook(SECRET, body, headers)


def test_generic_retry_sender_signature_verifies_on_received_bytes(capture_server):
    """Site 3 (webhook_delivery.py / requests): sign-once-send-those-bytes.

    Uses a payload whose key order differs from sorted order and includes
    non-ASCII + a float — the exact inputs where the old sign-a-different-
    serialization scheme produced signatures that never verified.
    """
    from unittest.mock import patch

    from src.core.webhook_delivery import WebhookDelivery, deliver_webhook_with_retry
    from src.core.webhook_validator import WebhookURLValidator

    payload = {
        "zeta": "café",  # non-ASCII: ensure_ascii mismatch trap
        "alpha": 250.0,  # float formatting trap
        "nested": {"b": 1, "a": 2},  # insertion order != sorted order
    }
    delivery = WebhookDelivery(
        webhook_url=capture_server,
        payload=payload,
        headers={"Content-Type": "application/json"},
        signing_secret=SECRET,
    )

    # Allow the loopback receiver through SSRF validation ONLY — the subject
    # under test is serialization+signing+transport, and the validator (which
    # correctly blocks 127.0.0.0/8 in production) has its own test suite.
    with patch.object(WebhookURLValidator, "validate_webhook_url", return_value=(True, "")):
        success, result = deliver_webhook_with_retry(delivery)
    assert success is True, f"delivery failed: {result}"

    assert len(RawCaptureHandler.received_raw) == 1
    body, headers = RawCaptureHandler.received_raw[0]
    _assert_hmac_over_received_bytes(body, headers)


def test_protocol_push_notification_signature_verifies_on_received_bytes(capture_server):
    """Site 1 (protocol_webhook_service / requests Session): same contract.

    This is the buyer-facing push-notification path — the primary live
    exposure of #1441 (HMAC-SHA256 push configs got unverifiable signatures).
    """
    from src.core.database.models import PushNotificationConfig
    from src.services.protocol_webhook_service import ProtocolWebhookService

    config = PushNotificationConfig(
        id="pnc-wire-sig",
        tenant_id="t-wire",
        principal_id="p-wire",
        url=capture_server,
        authentication_type="HMAC-SHA256",
        authentication_token=SECRET,
    )
    payload = {"task_id": "task-wire-1", "status": "completed", "note": "naïve→wire"}

    service = ProtocolWebhookService()
    sent = asyncio.run(service.send_notification(config, payload, metadata={"task_type": "create_media_buy"}))
    assert sent is True

    assert len(RawCaptureHandler.received_raw) == 1
    body, headers = RawCaptureHandler.received_raw[0]
    _assert_hmac_over_received_bytes(body, headers)
