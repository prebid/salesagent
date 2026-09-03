"""Both senders conclude through ONE recorder, and a success is written down too.

Lane GH #1802 ("One recorder for both senders, success rows included").
The absence this module grades is an OPERATOR-OBSERVABILITY absence, which is
why it is an integration module and not a BDD feature: "did this webhook
conclude, and how" is a question asked of the ``webhook_delivery_log`` table by
an operator, through no protocol surface, so no AdCP scenario reaches it. AdCP
3.1.1 is silent on delivery-log persistence (the lane's spec-grounding note:
ungraded by any storyboard step) — the obligation here is the triage's R4 root,
that a fact production writes down means the same thing whichever sender wrote
it.

Two senders exist and they do not agree today:

  ``webhook_delivery_service`` (the delivery-report sender) writes NO
    ``webhook_delivery_log`` row at all — not on success, not on a refusal.
    ``_deliver_with_backoff`` returns a bare ``bool`` and the only thing it
    records is a circuit-breaker tick, so every delivery this sender makes is
    invisible in the operator's own audit surface.

  ``protocol_webhook_service`` writes rows through a private 14-parameter
    method (``_write_delivery_log``, reached via ``_record_delivery_failure``)
    that nothing else can call, and it spells a REFUSAL as ``status="failed"``
    — the same word it uses for a delivery the buyer's endpoint actually
    rejected.

After the lane both senders conclude through
``DeliveryRepository.record_outcome``, which owns the kind->row mapping once:
``delivered -> "success"``, ``refused_destination -> "refused"``,
``client_error``/``exhausted -> "failed"``, ``refused_auth -> no row``.

RED, and for which reason:

  (a) ``test_the_delivery_report_sender_records_a_success_row`` — RED: that
      sender writes nothing, so the read comes back empty. It is the grader for
      "success rows included" on the sender that had none.
  (b) ``test_the_protocol_sender_records_the_same_success_encoding`` — GREEN
      today and the parity ANCHOR: it pins the shape the delivery sender must
      grow into, so a lane that made (a) pass by inventing a different encoding
      still fails. It must redden if ``record_outcome`` is broken (the
      mutation check the lane's verify atom owns).
  (c) ``test_both_senders_encode_a_refused_destination_identically`` — RED on
      BOTH senders: the delivery sender writes no row, and the protocol sender
      writes ``status="failed"`` where the mapping says ``"refused"``.

Rows are read back through ``DeliveryRepository`` (via
``env.recorded_outcomes``), never a raw ``select()`` — a grader that wrote its
own query would only prove the grader and the writer agree about columns.

KNOWN CONFLICT, for the implement atom (not fixed here — this atom writes no
production code and weakens no assertion):
``tests/integration/test_protocol_webhook_egress.py::TestDeliveryLogParity::
test_refused_destination_records_zero_attempts_and_the_task_identity`` asserts
``row.status == "failed"`` for a refused destination. That is today's spelling.
The designed mapping makes it ``"refused"``, so that assertion must be MIGRATED
to the new spelling when ``record_outcome`` lands — the case keeps every other
value it asserts.
"""

from __future__ import annotations

import pytest

from tests.harness import CircuitBreakerEnv, ProtocolWebhookEnv
from tests.harness.protocol_webhook import DELIVERY_METADATA_TASK_TYPE

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

# The internal ``task_type`` label each sender stamps on its rows. They are
# DIFFERENT by design — ``WebhookTaskContext.records_delivery_log`` admits both
# — and both are named here because every read below must filter on one of
# them: ``src/core/tools/media_buy_delivery.py`` persists ``delivery_poll``
# counter rows that are ALSO ``status="success"`` on the same ``media_buy_id``,
# so an unfiltered read would let grader (a) pass on a row no sender wrote.
_DELIVERY_REPORT_TASK_TYPE = "delivery_report"
_PROTOCOL_TASK_TYPE = DELIVERY_METADATA_TASK_TYPE

# How each outcome kind is written down, as ``(status, attempt_count,
# http_status_code)``. Both senders are graded against these same two tuples:
# that is what "encode it identically" means, and comparing each sender to a
# constant is stronger than comparing the two senders to each other (which two
# equally-wrong senders would satisfy).
_DELIVERED_ENCODING = ("success", 1, 200)
_REFUSED_ENCODING = ("refused", 0, None)

# A destination the egress seam refuses before opening a connection. RFC 6761
# §6.4 reserves ``.invalid`` as a name guaranteed never to resolve, so the
# refusal comes from the seam's ADDRESS policy rather than from what is or is
# not listening anywhere — and it still arrives with the private-range hatch
# ``LocalOriginMixin`` opens left open, which a loopback URL could not manage.
# Deliberately ``https``: an ``http`` URL would be refused by the scheme rule
# first, grading the scheme instead of the destination.
_UNRESOLVABLE_WEBHOOK_URL = "https://webhook-sink.invalid/webhook"

# Long enough that a duration measured in MILLISECONDS is unmistakably distinct
# from the same span measured in seconds (``int(0.25) == 0``).
# ``response_time_ms`` is a parameter of the designed ``record_outcome``, so a
# sender that concluded through the recorder without measuring anything would
# otherwise pass on a ``None`` nobody looked at.
_MEASURABLE_DELAY_SECONDS = 0.25

# Non-default identity values for the protocol sender's payload. The env's
# default ``result`` carries neither, so ``notification_type`` would be ``None``
# and ``sequence_number`` would take production's ``isinstance(..., int)``
# fallback of 1 — asserting those defaults would grade nothing.
_PROTOCOL_NOTIFICATION_TYPE = "scheduled"
_PROTOCOL_SEQUENCE_NUMBER = 7

# What the delivery-report sender derives for its FIRST notification about a
# media buy: ``send_delivery_webhook`` increments a per-media-buy counter from
# zero, and neither ``is_final`` nor ``is_adjusted`` is set.
_DELIVERY_SEQUENCE_NUMBER = 1
_DELIVERY_NOTIFICATION_TYPE = "scheduled"


def _encoding(row: object) -> tuple[str, int, int | None]:
    """The three fields that say WHAT BECAME of a delivery, as one value."""
    return (row.status, row.attempt_count, row.http_status_code)  # type: ignore[attr-defined]


def _recorded(rows: list) -> object:
    """What a sender wrote down, as ONE comparable value — row count included.

    Returns the encoding plus the destination when there is exactly one row, and
    otherwise says how many rows there were. Folding the count in is what lets
    both senders be compared in a SINGLE assertion: a per-sender
    ``len(rows) == 1`` would stop at the first sender and hide whether the other
    one is wrong too, which for a parity property is half the finding.
    """
    if len(rows) != 1:
        return f"{len(rows)} row(s) — expected exactly one"
    return (*_encoding(rows[0]), rows[0].webhook_url)


class TestASuccessIsWrittenDownByBothSenders:
    """A delivery that succeeded leaves the same record whichever sender made it."""

    def test_the_delivery_report_sender_records_a_success_row(self, integration_db):
        """RED: this sender writes no ``webhook_delivery_log`` row at all.

        The read is filtered to ``task_type="delivery_report"`` deliberately: it
        must not be satisfiable by a ``delivery_poll`` counter row, which is
        also ``status="success"`` against the same media buy and is written by
        ``media_buy_delivery.py``, not by a sender.
        """
        with CircuitBreakerEnv(tenant_id="t1", principal_id="p1") as env:
            buy = env.make_media_buy()
            env.make_webhook_config()
            env.set_http_response(200)
            env.origin.delay(_MEASURABLE_DELAY_SECONDS)

            delivered = env.call_send(media_buy_id=buy.media_buy_id, tenant_id="t1", principal_id="p1")

            assert delivered is True
            assert env.delivery_attempts == 1

            rows = env.recorded_outcomes(buy.media_buy_id, task_type=_DELIVERY_REPORT_TASK_TYPE)
            assert len(rows) == 1, (
                f"the delivery-report sender wrote {len(rows)} delivery-log row(s) for a webhook it "
                "delivered — a delivery that succeeded is as much a fact an operator needs written "
                "down as one that failed, and this sender records neither"
            )
            row = rows[0]
            assert _encoding(row) == _DELIVERED_ENCODING, (
                f"the delivered webhook was written down as {_encoding(row)}, not "
                f"{_DELIVERED_ENCODING} — the row does not say the delivery succeeded on its first "
                "attempt with the status the endpoint returned"
            )
            assert row.webhook_url == env.webhook_url
            assert row.principal_id == "p1"
            assert row.media_buy_id == buy.media_buy_id
            assert row.sequence_number == _DELIVERY_SEQUENCE_NUMBER
            assert row.notification_type == _DELIVERY_NOTIFICATION_TYPE
            assert row.payload_size_bytes == len(env.last_delivery.body), (
                f"the row records {row.payload_size_bytes} payload bytes for a body of "
                f"{len(env.last_delivery.body)} — the column no longer means the bytes that went "
                "on the wire"
            )
            assert row.response_time_ms >= _MEASURABLE_DELAY_SECONDS * 1000, (
                f"response_time_ms is {row.response_time_ms} for a delivery that provably took at "
                f"least {_MEASURABLE_DELAY_SECONDS}s — the metric is no longer milliseconds of this "
                "delivery"
            )
            assert row.completed_at is not None
            assert row.error_message is None

    async def test_the_protocol_sender_records_the_same_success_encoding(self, integration_db):
        """The parity anchor: the shape the other sender must grow into.

        GREEN today. It is here so that greening grader (a) with a DIFFERENT
        encoding — a second vocabulary for "it worked" — still fails the lane,
        and so the lane's mutation check has a case to redden when
        ``record_outcome`` is broken.
        """
        with ProtocolWebhookEnv() as env:
            buy = env.make_media_buy()
            env.set_http_status(200)
            env.origin.delay(_MEASURABLE_DELAY_SECONDS)

            delivered = await env.send(
                payload=env.make_payload(
                    result={
                        "media_buy_id": buy.media_buy_id,
                        "notification_type": _PROTOCOL_NOTIFICATION_TYPE,
                        "sequence_number": _PROTOCOL_SEQUENCE_NUMBER,
                    }
                ),
                media_buy_id=buy.media_buy_id,
                # STATED, not scraped off the payload. The sender used to rebuild
                # its task context from the payload's result, so declaring these
                # in the payload alone was enough to reach the row. It reaches the
                # row now because the CALLER says so, which is the only way a
                # caller with no such values in its payload can get them right.
                notification_type=_PROTOCOL_NOTIFICATION_TYPE,
                sequence_number=_PROTOCOL_SEQUENCE_NUMBER,
            )

            assert delivered is True
            assert env.delivery_attempts == 1

            rows = env.recorded_outcomes(buy.media_buy_id, task_type=_PROTOCOL_TASK_TYPE)
            assert len(rows) == 1, (
                f"the protocol sender wrote {len(rows)} delivery-log row(s) for one delivered "
                "webhook — one conclusion is one row"
            )
            row = rows[0]
            assert _encoding(row) == _DELIVERED_ENCODING, (
                f"the delivered webhook was written down as {_encoding(row)}, not {_DELIVERED_ENCODING}"
            )
            assert row.webhook_url == env.webhook_url
            assert row.media_buy_id == buy.media_buy_id
            assert row.sequence_number == _PROTOCOL_SEQUENCE_NUMBER
            assert row.notification_type == _PROTOCOL_NOTIFICATION_TYPE
            assert row.payload_size_bytes == len(env.last_delivery.body)
            assert row.response_time_ms >= _MEASURABLE_DELAY_SECONDS * 1000, (
                f"response_time_ms is {row.response_time_ms} for a delivery that provably took at "
                f"least {_MEASURABLE_DELAY_SECONDS}s"
            )
            assert row.completed_at is not None


class TestARefusalIsWrittenDownIdenticallyByBothSenders:
    """A destination the egress seam refused reads the same from either sender.

    This is the case where a single vocabulary matters most. A refusal is
    pre-flight: nothing was attempted, no status was observed, and the buyer's
    endpoint never saw a request. Spelling that ``"failed"`` — which is what the
    protocol sender does today — makes a misconfigured destination
    indistinguishable from one that answered 500 three times, and the delivery
    sender does not write it down at all.
    """

    async def test_both_senders_encode_a_refused_destination_identically(self, integration_db):
        """RED on both halves.

        Delivery sender: no row exists at all. Protocol sender: the row exists
        but says ``"failed"`` — the same word it uses for a delivery the buyer's
        endpoint actually rejected.

        The two senders are driven in sequence and BOTH results reduced to one
        comparable value before a single assertion, so the failure message names
        what each sender did rather than stopping at whichever one is checked
        first. Identity fields legitimately differ between the senders (each
        stamps its own ``task_type``); what must NOT differ is what the row says
        became of the delivery, and where it was going.
        """
        expected = (*_REFUSED_ENCODING, _UNRESOLVABLE_WEBHOOK_URL)

        with CircuitBreakerEnv(tenant_id="t1", principal_id="p1") as env:
            buy = env.make_media_buy()
            env.make_webhook_config(url=_UNRESOLVABLE_WEBHOOK_URL)

            delivered = env.call_send(media_buy_id=buy.media_buy_id, tenant_id="t1", principal_id="p1")

            assert delivered is False
            assert env.delivery_attempts == 0, (
                f"{env.delivery_attempts} request(s) reached the local origin for a webhook aimed "
                "at an unresolvable host — the refusal did not happen before the connection"
            )
            delivery_sender = _recorded(env.recorded_outcomes(buy.media_buy_id, task_type=_DELIVERY_REPORT_TASK_TYPE))

        with ProtocolWebhookEnv() as env:
            buy = env.make_media_buy()
            config = env.make_config(url=_UNRESOLVABLE_WEBHOOK_URL)

            delivered = await env.send(
                config=config,
                payload=env.make_payload(
                    result={
                        "media_buy_id": buy.media_buy_id,
                        "notification_type": _PROTOCOL_NOTIFICATION_TYPE,
                        "sequence_number": _PROTOCOL_SEQUENCE_NUMBER,
                    }
                ),
                media_buy_id=buy.media_buy_id,
                # STATED, not scraped off the payload. The sender used to rebuild
                # its task context from the payload's result, so declaring these
                # in the payload alone was enough to reach the row. It reaches the
                # row now because the CALLER says so, which is the only way a
                # caller with no such values in its payload can get them right.
                notification_type=_PROTOCOL_NOTIFICATION_TYPE,
                sequence_number=_PROTOCOL_SEQUENCE_NUMBER,
            )

            assert delivered is False
            assert env.delivery_attempts == 0
            protocol_sender = _recorded(env.recorded_outcomes(buy.media_buy_id, task_type=_PROTOCOL_TASK_TYPE))

        assert {"delivery-report sender": delivery_sender, "protocol sender": protocol_sender} == {
            "delivery-report sender": expected,
            "protocol sender": expected,
        }, (
            f"the two senders do not write the same refusal down the same way. Delivery-report "
            f"sender: {delivery_sender}. Protocol sender: {protocol_sender}. Both must be "
            f"{expected} — a destination refused before any connection was opened is not a delivery "
            "that failed on the wire, and a refusal that leaves no row at all is indistinguishable "
            "from an endpoint nobody configured"
        )
