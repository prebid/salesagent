"""The protocol sender's unexpected-error arm authors its own outcome detail.

Covers GH #1802, Move 2 -- the SECOND of the two out-of-module
``WebhookDeliveryOutcome`` construction sites.

``WebhookDeliveryOutcome.detail``'s own docstring says it is "PRE-SANITIZED at
construction: never a URL, never a credential". Two sites outside the owning
module (``src/core/security/webhook_egress.py``) construct the outcome directly
and pass ``detail=str(e)`` for an arbitrary caught exception:

1. ``src/services/webhook_delivery_service.py:606`` -- graded by
   ``tests/unit/test_delivery_service_behavioral.py::TestDeliverWithBackoffGenericException``,
   which was updated in this same change to stop pinning ``detail=str(e)``.
   Not re-graded here: the setup is the queue/poller shape that lives in that
   module, and copying it would duplicate a block for no extra coverage.
2. ``src/services/protocol_webhook_service.py:360`` -- THIS module. It had zero
   coverage of any kind before this file existed.

Why ``outcome.detail`` at the arm is the right choke point rather than a
downstream proxy: both durable surfaces read that one field VERBATIM.
``src/core/database/repositories/delivery.py:327`` writes it to
``webhook_delivery_log.error_message``, and ``protocol_webhook_service.py:280``
emits it as an audit warning. So this is disclosure into storage and into an
operator record, not just into a log line.

The stimulus is the case the arm's OWN comment names -- "the pinned transport's
own wrong-host guard raises a bare RuntimeError". That message is, verbatim from
``adcp/signing/ip_pinned_transport.py:150-154``, an interpolation of TWO
hostnames into a field contracted never to carry a URL.
"""

from __future__ import annotations

from unittest.mock import patch

from src.core.webhooks.delivery import WebhookDeliveryOutcome
from src.services.protocol_webhook_service import ProtocolWebhookService
from tests.factories import WebhookTaskContextFactory

# Verbatim shape of adcp.signing.IpPinnedTransport's wrong-host guard message.
PINNED_HOST = "a.example"
ATTEMPTED_HOST = "b.example"
PINNED_TRANSPORT_ERROR = RuntimeError(
    f"IpPinnedTransport is pinned to {PINNED_HOST!r}; "
    f"refusing connect to {ATTEMPTED_HOST!r} -- build a new transport per host "
    f"(see build_ip_pinned_transport)"
)

# No ``tenant_id``: ``WebhookTaskContext.records_delivery_log`` is then False and
# no audit logger is built, so this test needs neither a database nor an audit
# backend to reach the arm. The arm is what is under test, not the epilogue.
TASK = WebhookTaskContextFactory()
PAYLOAD = {"task_id": "t1", "status": "completed"}


async def _outcome_from_the_unexpected_arm() -> WebhookDeliveryOutcome:
    """Drive the arm and hand back the outcome it built.

    ``_conclude`` returns a bool, so the outcome is captured as it is passed in
    -- the recorder delegates to the real ``_conclude`` so the epilogue still
    runs exactly as in production. This observes a VALUE, not a call: every
    assertion below is on the outcome's fields.
    """
    captured: list[WebhookDeliveryOutcome] = []
    real_conclude = ProtocolWebhookService._conclude

    def _capture(self, **kwargs):
        captured.append(kwargs["outcome"])
        return real_conclude(self, **kwargs)

    async def _raise_pinned_transport_error(*args, **kwargs):
        raise PINNED_TRANSPORT_ERROR

    service = ProtocolWebhookService()
    with (
        patch("src.services.protocol_webhook_service.adeliver_webhook", _raise_pinned_transport_error),
        patch.object(ProtocolWebhookService, "_conclude", _capture),
    ):
        await service._send_with_retry_and_logging(
            "https://buyer.example.com/hook",
            PAYLOAD,
            {"Content-Type": "application/json"},
            TASK,
        )

    assert len(captured) == 1, f"the arm concluded {len(captured)} times, expected exactly 1"
    return captured[0]


async def test_the_unexpected_arm_carries_no_foreign_exception_text():
    """A caught exception's own message never reaches ``outcome.detail``.

    The negative is asserted FIRST and on the FOREIGN text specifically: that
    is the disclosure, and it is what must redden on the unfixed tree.
    """
    detail = (await _outcome_from_the_unexpected_arm()).detail or ""

    assert PINNED_HOST not in detail, f"outcome.detail carries the pinned hostname: {detail!r}"
    assert ATTEMPTED_HOST not in detail, f"outcome.detail carries the attempted hostname: {detail!r}"
    assert "pinned to" not in detail, f"outcome.detail carries the foreign exception's phrasing: {detail!r}"


async def test_the_unexpected_arm_builds_the_named_outcome():
    """The arm builds exactly ``WebhookDeliveryOutcome.unexpected(<type name>)``.

    Whole-object equality, so ``kind``, ``attempts``, ``http_status``,
    ``payload_size_bytes``, ``reason`` and ``scheme`` stay pinned as well: the
    arm's honest report is "exhausted with zero attempts", and a named
    constructor must not quietly change any of that while fixing ``detail``.
    """
    outcome = await _outcome_from_the_unexpected_arm()

    assert outcome == WebhookDeliveryOutcome.unexpected("RuntimeError"), (
        f"the arm did not build the named outcome: {outcome!r}"
    )
