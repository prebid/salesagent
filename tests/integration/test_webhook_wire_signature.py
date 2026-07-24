"""Wire-level HMAC verification: signatures must verify over RECEIVED bytes.

Every earlier signature test in this repo either read the payload back out of
a transport mock as a dict, or "verified" by re-running the sender's own
re-serialization \u2014 so the one property that matters (the HMAC verifies over
the raw bytes a receiver actually gets, per the AdCP legacy-HMAC rule
``{timestamp}.{raw_http_body}``) was never tested against a real transport.

These tests drive the real senders through real ``requests``/``httpx`` wire
serialization at a live localhost receiver (subclassing the shared e2e
capture handler per its own docstring) and verify the HMAC over the exact
bytes the socket delivered \u2014 the receiving half of #1441.

No DB required: the site-3 sender skips delivery records without tenant_id,
and the site-1 sender only writes logs via metadata we omit.
"""

from __future__ import annotations

import asyncio
from http.server import HTTPServer
from threading import Thread

import pytest

from tests.e2e._webhook_capture import WebhookCaptureHandler
from tests.helpers.webhook_hmac import assert_hmac_over_transmitted_bytes

pytestmark = [pytest.mark.integration]

SECRET = "wire-signature-secret-0123456789abcdef"  # 32+ chars


class RawCaptureHandler(WebhookCaptureHandler):
    """Capture the RAW request bytes and headers (not the parsed JSON).

    The shared handler stores ``json.loads(body)`` \u2014 useless for signature
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
    """The core oracle: recompute the HMAC over the bytes the socket delivered.

    Thin bind to the shared assertion so this module's three cases all grade
    the contract identically (and identically to the mock-boundary suites).
    """
    assert_hmac_over_transmitted_bytes(SECRET, body, headers)


def test_generic_retry_sender_signature_verifies_on_received_bytes(capture_server):
    """Site 3 (webhook_delivery.py / requests): sign-once-send-those-bytes.

    Uses a payload whose key order differs from sorted order and includes
    non-ASCII + a float \u2014 the exact inputs where the old sign-a-different-
    serialization scheme produced signatures that never verified.
    """
    from unittest.mock import patch

    from src.core.webhook_delivery import WebhookDelivery, deliver_webhook_with_retry
    from src.core.webhook_validator import WebhookURLValidator

    payload = {
        "zeta": "caf\u00e9",  # non-ASCII: ensure_ascii mismatch trap
        "alpha": 250.0,  # float formatting trap
        "nested": {"b": 1, "a": 2},  # insertion order != sorted order
    }
    delivery = WebhookDelivery(
        webhook_url=capture_server,
        payload=payload,
        headers={"Content-Type": "application/json"},
        signing_secret=SECRET,
    )

    # Allow the loopback receiver through SSRF validation ONLY \u2014 the subject
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

    This is the buyer-facing push-notification path \u2014 the primary live
    exposure of #1441 (HMAC-SHA256 push configs got unverifiable signatures).
    """
    from src.services.protocol_webhook_service import ProtocolWebhookService
    from tests.factories import PushNotificationConfigFactory

    # .build() (not .create()) \u2014 in-memory only, so this stays a no-DB test.
    config = PushNotificationConfigFactory.build(
        url=capture_server,
        authentication_type="HMAC-SHA256",
        authentication_token=SECRET,
    )
    payload = {"task_id": "task-wire-1", "status": "completed", "note": "na\u00efve\u2192wire"}

    service = ProtocolWebhookService()
    sent = asyncio.run(service.send_notification(config, payload, metadata={"task_type": "create_media_buy"}))
    assert sent is True

    assert len(RawCaptureHandler.received_raw) == 1
    body, headers = RawCaptureHandler.received_raw[0]
    _assert_hmac_over_received_bytes(body, headers)


def test_delivery_service_sender_signature_verifies_on_received_bytes(capture_server):
    """Site 2 (webhook_delivery_service / httpx): the third sender, on a real socket.

    The only sender on ``httpx`` (``content=body_bytes``) and the one this
    change touched hardest -- it moved from an ISO timestamp to unix seconds
    and lost its local signer. Its HMAC was graded only at a transport mock,
    so the httpx serialization leg was never exercised end to end.

    The DB config lookup is mocked rather than seeded: the subject under test
    is serialize -> sign -> httpx -> socket, and a real ``PushNotificationConfig``
    row would drag the whole DB fixture in for one attribute read.

    The payload MUST carry a non-ASCII value (routed in through ``by_package``,
    the only caller-controlled dict in this sender's payload). httpx's
    ``json=`` encoder already uses compact separators, so it differs from the
    signed body only on ``ensure_ascii`` -- with an all-ASCII payload,
    reintroducing the re-serialization bug produces byte-identical output and
    this test cannot fail. The non-ASCII value is what makes it an oracle
    rather than decoration.
    """
    from datetime import UTC, datetime
    from unittest.mock import MagicMock, patch

    from src.services.webhook_delivery_service import WebhookDeliveryService

    config = MagicMock()
    config.url = capture_server
    config.webhook_secret = SECRET  # the signer reads webhook_secret, not authentication_token
    config.authentication_type = None
    config.auth_blocked_at = None

    session = MagicMock()
    session.__enter__.return_value.scalars.return_value.all.return_value = [config]
    session.__exit__.return_value = False

    service = WebhookDeliveryService()
    with patch("src.core.database.database_session.get_db_session", return_value=session):
        sent = service.send_delivery_webhook(
            media_buy_id="mb-wire-1",
            tenant_id="t-wire",
            principal_id="p-wire",
            reporting_period_start=datetime(2026, 1, 1, tzinfo=UTC),
            reporting_period_end=datetime(2026, 1, 2, tzinfo=UTC),
            impressions=12345,
            spend=678.90,  # float formatting trap
            currency="EUR",
            by_package=[{"package_id": "pkg-m\u00fcnchen-caf\u00e9", "impressions": 12345}],
        )

    assert sent is True, "delivery webhook was not sent"
    assert len(RawCaptureHandler.received_raw) == 1
    body, headers = RawCaptureHandler.received_raw[0]
    _assert_hmac_over_received_bytes(body, headers)
