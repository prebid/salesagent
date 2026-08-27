"""Order-approval notifications must be authenticated the way the buyer registered.

A buyer registers ONE ``PushNotificationConfig`` row (tenant + principal + url +
``authentication_type``). Three outbound senders read that row —
``src/services/protocol_webhook_service.py:186`` signs when the type is
``HMAC-SHA256`` (via ``adcp.get_adcp_signed_headers_for_webhook``), and
``src/services/webhook_delivery_service.py:308`` has its own HMAC path. The
fourth sender, ``src/services/order_approval_service.py:_send_approval_webhook``,
reads the same row and handles only ``bearer``/``basic`` — so the
``order_approval_update`` notification arrives with no signature at all, while
the identical registration produces signed deliveries elsewhere (salesagent-98t2).

The two tests below grade two distinct defects through the same outbound
request:

1. ``test_hmac_receiver_gets_a_signed_approval_notification`` — the
   authentication gap. The registered ``HMAC-SHA256`` selector must produce the
   signature headers the sibling senders emit
   (``X-AdCP-Signature: sha256=<hex>`` + ``X-AdCP-Timestamp``, the pair
   ``adcp.webhooks._compute_legacy_signature`` returns).

2. ``test_non_ascii_approval_notification_is_signed_over_the_bytes_sent`` — the
   re-serialization. ``_send_approval_webhook`` POSTs ``json=payload``
   (``order_approval_service.py:403``) rather than the bytes the signature was
   computed over. Measured fact about the pinned httpx (0.28.1): its
   ``encode_json`` already emits ``separators=(",", ":")`` with
   ``ensure_ascii=False``, so for an ASCII payload ``json=`` and ``content=``
   produce IDENTICAL bytes and an ASCII test proves nothing. The signing core
   (``adcp.webhooks._compute_legacy_signature``) serializes with the default
   ``ensure_ascii=True``, so only a payload carrying a NON-ASCII value
   distinguishes "signed the dict" from "sent the signed bytes". Hence the
   accented/CJK advertiser name below — it is load-bearing, not decoration.
   (The success payload has no free-text field: ``message`` is the constant
   "Order approved successfully". The ad server's own rejection text is the one
   place a buyer-visible non-ASCII string enters this payload, which is why
   test 2 drives the FAILURE path.)

Test 2 currently fails one leg earlier than its signature assertion, on a THIRD
defect this reproduction uncovered: ``_mark_approval_failed``
(``order_approval_service.py:308-331``) commits inside ``with get_db_session()``
— which EXPIRES the ``SyncJob`` — then reads ``approval_job.progress`` at :326
after the session has closed. That raises ``DetachedInstanceError``, the bare
``except`` at :330 swallows it, and the buyer is never told the order failed at
all. Deterministic, and independent of any signing work.

Both tests assert on the real outbound request, captured by the shared
``tests.helpers.webhook_wire.capture_outbound_webhooks`` (one capture for every
AdCP sender, so C1's suite and this one cannot drift into two): the network is
the only thing stubbed, so the headers and ``.content`` are exactly what the
client would have put on the socket. GAM is mocked at the adapter boundary — it
is a true external.

NOTE (plumbing gap — the stated prerequisite is now MET, the migration is not):
this used to record that the shared e2e receiver discarded headers and raw body
bytes, so it could not grade either defect. Two receivers now can:
``tests/e2e/_webhook_capture.py``'s ``record_raw`` (#1291 C1) and the
TLS-fronted ``tests/e2e/webhook_capture_service.py`` (salesagent-mp53.9), which
stores ``{path, headers, body_b64}`` per delivery.

So the blocker on moving this coverage onto a real socket is gone; the move
itself is tracked as salesagent-og9k.12 and deliberately NOT smuggled in here.
Leaving the old claim in place would have invited the next author to re-derive a
bespoke capture that already exists twice over.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from collections.abc import Iterator
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from tests.harness._base import BareIntegrationEnv
from tests.helpers.webhook_wire import CapturedWebhook, capture_outbound_webhooks

_TENANT_ID = "tenant_approval_signing"
_PRINCIPAL_ID = "principal_approval_signing"
_MEDIA_BUY_ID = "mb_approval_signing"
_WEBHOOK_URL = "https://buyer.example.com/adcp/order-approval"

#: 44 chars — clears the 32-char floor ``webhook_delivery_service`` enforces, so
#: no sender can decline to sign on strength grounds.
_SECRET = "order-approval-shared-secret-0123456789abcd"

#: The advertiser name GAM quotes back in its rejection. Non-ASCII on purpose:
#: it is what gives the byte-identity assertion its power (see module docstring).
_ADVERTISER = "Grüße Tōkyō Medien"


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------


def _register_hmac_receiver() -> None:
    """Seed the buyer that registered HMAC-SHA256 for this webhook URL.

    The GAM adapter row is what lets the approval poller reach its ad-server
    branch at all (``order_approval_service.py:146`` refuses to poll without a
    network code); the push-notification row is the registration under test.
    """
    from tests.factories import (
        AdapterConfigFactory,
        PrincipalFactory,
        PushNotificationConfigFactory,
        TenantFactory,
    )

    tenant = TenantFactory(tenant_id=_TENANT_ID)
    principal = PrincipalFactory(tenant=tenant, principal_id=_PRINCIPAL_ID)
    AdapterConfigFactory(
        tenant=tenant,
        adapter_type="google_ad_manager",
        gam_network_code="123456789",
        gam_refresh_token="refresh-token-for-approval-signing",
    )
    PushNotificationConfigFactory(
        tenant=tenant,
        principal=principal,
        url=_WEBHOOK_URL,
        authentication_type="HMAC-SHA256",
        authentication_token=_SECRET,
    )


# ---------------------------------------------------------------------------
# Driving the notification
# ---------------------------------------------------------------------------


@contextmanager
def _gam_order_approval(*, approve_raises: Exception | None) -> Iterator[None]:
    """Stub GAM (a true external) at the adapter boundary.

    ``approve_raises=None`` is the happy path; an exception whose text carries
    neither ``NO_FORECAST_YET`` nor ``ForecastingError`` is the non-retryable
    branch, which reports the ad server's own message to the buyer.
    """
    orders_manager = MagicMock()
    if approve_raises is None:
        orders_manager.approve_order.return_value = True
    else:
        orders_manager.approve_order.side_effect = approve_raises

    with (
        patch("src.adapters.gam.client.GAMClientManager"),
        patch("src.adapters.gam.managers.orders.GAMOrdersManager", return_value=orders_manager),
    ):
        yield


def _deliver_order_approval_notification(
    *,
    order_id: str,
    approve_raises: Exception | None = None,
) -> CapturedWebhook:
    """Run the background approval to completion; return its outbound request."""
    from src.services.order_approval_service import start_order_approval_background

    with capture_outbound_webhooks() as captured, _gam_order_approval(approve_raises=approve_raises):
        start_order_approval_background(
            order_id=order_id,
            media_buy_id=_MEDIA_BUY_ID,
            tenant_id=_TENANT_ID,
            principal_id=_PRINCIPAL_ID,
            webhook_url=_WEBHOOK_URL,
            max_attempts=1,
            poll_interval_seconds=0,
        )

        deadline = time.monotonic() + 10.0
        while not captured and time.monotonic() < deadline:
            time.sleep(0.05)

    assert len(captured) == 1, (
        f"expected exactly 1 order_approval_update notification to {_WEBHOOK_URL}, got {len(captured)} — "
        "a registered buyer was not told the outcome at all"
    )
    return captured[0]


# ---------------------------------------------------------------------------
# Assertions
# ---------------------------------------------------------------------------


def _assert_signature_covers_the_bytes_sent(request: CapturedWebhook) -> None:
    """The receiver must be able to verify the signature over what it received.

    This is deliberately canonicalization-agnostic: it recomputes the HMAC over
    ``request.content`` itself, so it holds for any serialization the sender
    picks — and fails whenever the bytes POSTed are a re-serialization of the
    bytes signed.
    """
    header_names = sorted(request.headers.keys())
    assert "x-adcp-signature" in request.headers, (
        f"order_approval_update was delivered UNSIGNED to a receiver registered as "
        f"authentication_type=HMAC-SHA256; headers were {header_names}"
    )
    assert "x-adcp-timestamp" in request.headers, (
        f"signature header present but no X-AdCP-Timestamp to bind it against replay; headers were {header_names}"
    )

    signed_message = f"{request.headers['x-adcp-timestamp']}.{request.content.decode('utf-8')}"
    expected = "sha256=" + hmac.new(_SECRET.encode("utf-8"), signed_message.encode("utf-8"), hashlib.sha256).hexdigest()

    assert request.headers["x-adcp-signature"] == expected, (
        "X-AdCP-Signature does not verify over the bytes actually POSTed — the receiver would reject this "
        f"notification. body={request.content!r}"
    )


@pytest.mark.requires_db
class TestOrderApprovalWebhookAuthentication:
    """salesagent-98t2 — the fourth sender ignores the registered auth type."""

    def test_hmac_receiver_gets_a_signed_approval_notification(self, integration_db):
        """A buyer registered as HMAC-SHA256 gets a signed order_approval_update.

        Same row, same tenant/principal/url that produces signed deliveries on
        ``protocol_webhook_service`` — so the selector must be honoured here too.

        The verification is over ``request.content``, and this payload's keys are
        emitted in construction order (``event``, ``media_buy_id``, ``status``,
        ``message``, ...) — NOT sorted. So this ASCII case still fails a fix that
        signs with ``sort_keys=True`` (``webhook_delivery_service.py:320``) while
        POSTing httpx's insertion-order encoding. What it CANNOT catch is the
        ``ensure_ascii`` divergence; that is test 2's job.
        """
        with BareIntegrationEnv(tenant_id=_TENANT_ID, principal_id=_PRINCIPAL_ID) as env:
            _register_hmac_receiver()
            env.get_session()

            request = _deliver_order_approval_notification(order_id="gam_order_approved_98t2")

        payload = json.loads(request.content)
        assert payload["event"] == "order_approval_update"
        assert payload["status"] == "approved"

        _assert_signature_covers_the_bytes_sent(request)

    def test_non_ascii_approval_notification_is_signed_over_the_bytes_sent(self, integration_db):
        """The POSTed bytes are the signed bytes, for a payload with non-ASCII text.

        The ad server rejects the order quoting the advertiser's own name; that
        name reaches the buyer in ``message``. With a non-ASCII value present,
        "sign the dict, POST ``json=``" and "POST the signed bytes" diverge —
        which is precisely what the receiver-side verification detects.

        Today this fails at ``len(captured) == 1``: the failure notification is
        never sent, because ``_mark_approval_failed`` reads an expired, detached
        ``SyncJob`` after its session closed (module docstring). Fixing that
        alone leaves this test red on the signature/byte assertions.
        """
        gam_rejection = RuntimeError(f"Advertiser {_ADVERTISER} is not approved for programmatic guaranteed")

        with BareIntegrationEnv(tenant_id=_TENANT_ID, principal_id=_PRINCIPAL_ID) as env:
            _register_hmac_receiver()
            env.get_session()

            request = _deliver_order_approval_notification(
                order_id="gam_order_rejected_98t2",
                approve_raises=gam_rejection,
            )

        payload = json.loads(request.content)
        assert payload["status"] == "failed"
        # Fidelity: the advertiser name survives the round trip intact...
        assert _ADVERTISER in payload["message"], f"advertiser name mangled in transit: {payload['message']!r}"
        # ...and non-vacuity: the payload really does carry non-ASCII, which is
        # what makes the byte-identity assertion below able to fail.
        assert any(ord(char) > 127 for char in payload["message"])

        _assert_signature_covers_the_bytes_sent(request)
