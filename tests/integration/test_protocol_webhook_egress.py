"""Protocol webhook delivery, against a real origin and the real outbound transport.

``ProtocolWebhookService`` is the last outbound call site in this epic that owns
a LONG-LIVED transport: a module-lived ``requests.Session`` built in
``__init__`` and closed from a shutdown callback. It cannot survive the egress
seam. ``build_ip_pinned_transport`` resolves its destination once, at
construction, and both of its backends RAISE on reuse for another hostname
(``adcp/signing/ip_pinned_transport.py``: "The transport is single-host-scoped…
Build one transport per hostname you need to reach"), so a shared pinned client
is not a weaker pin — it is a non-functional one. The session removal is forced,
not preferred.

Everything here is graded off ``ProtocolWebhookEnv``'s local origin and the real
``webhook_delivery_log`` rows, so the assertions mean the same thing before and
after the migration — which is the point: the transport symbol changes, what the
buyer receives and what the operator can read back must not.

What is deliberately NOT graded here: the retry *spacing*, address policy, TLS
policy, redirect policy and the response-size cap. Those become the seam's
decisions and are graded once, in ``tests/integration/test_outbound_http.py``.
Re-asserting them per call site is exactly the duplication the seam exists to
delete.

Five cases are RED before the migration, and each names a LIVE bug rather than a
migration artefact:

``TestSignedBodyIntegrity`` (both cases)
    A buyer that verifies the HMAC over the bytes it actually received must
    accept the delivery. It does not today: ``_compute_legacy_signature`` signs
    ``json.dumps(payload, separators=(",", ":"))`` and returns exactly those
    bytes, while production signs with them and then transmits
    ``requests(json=)``'s spaced ``{"a": 1}`` form. The signature has therefore
    never verified — every HMAC-configured buyer endpoint that checks it has
    been rejecting these deliveries. A local origin that does not check
    signatures cannot see that, which is why it survived. The payload is
    non-ASCII on purpose: an ASCII payload would also be accepted by an
    ``httpx(json=)`` implementation (compact separators, but
    ``ensure_ascii=False``), so only a non-ASCII body grades the "transmit
    exactly the signed bytes" contract rather than coincidental agreement.

``TestDeliveryLogParity.test_terminal_status_row_records_one_attempt_and_the_status``
``TestDeliveryLogParity.test_exhausted_row_counts_every_attempt_that_was_made``
    Both fail on the same live bug, at
    ``protocol_webhook_service.py`` in the ``requests.HTTPError`` arm:
    ``status_code = e.response.status_code if e.response else None``.
    ``requests.Response.__bool__`` returns ``self.ok``, which is False for
    EVERY response that raised ``HTTPError`` — so ``status_code`` is
    unconditionally ``None`` there. Two consequences, both observed in the run:
    ``http_status_code`` is ``None`` on every failed delivery-log row (an
    operator cannot tell a 404 from a 503), and the ``400 <= status_code < 500``
    terminal branch is dead code, so a 404 is retried three times. Through the
    seam the status comes off ``OutboundDeliveryFailed.http_status`` and 404 is
    terminal, so both cases turn green. The 404-costs-one-request change is
    buyer-visible and belongs in the PR body beside the 429 and redirect ones.

``TestNoClientSurvivesConstruction``
    The regression guard against reintroducing a shared pooled client under any
    name. RED today because ``self._session`` exists.

``test_constructing_the_webhook_service_registers_no_shutdown_callback`` (in
``tests/integration/test_app_lifespan_shutdown.py``, where the lifespan fixture
already lives)
    Constructing the service must leave the shutdown registry untouched, and the
    real ASGI lifespan must still start and stop cleanly. RED today because
    ``get_protocol_webhook_service()`` self-registers ``close``.

Every other case below is GREEN today and must STAY green. That is the parity
requirement: the delivery-log metrics that are correct today, the audit trail,
and the ability to reach two destinations from one service instance are what the
migration must not cost.
"""

from __future__ import annotations

import logging
import time
from urllib.parse import urlparse

import httpx
import pytest
import requests
from adcp.webhook_receiver import LegacyWebhookHmacError, LegacyWebhookHmacOptions, verify_webhook_hmac

from src.services.protocol_webhook_service import ProtocolWebhookService
from tests.harness.protocol_webhook import AUDIT_LOGGER_NAME, DELIVERY_METADATA_TASK_TYPE, ProtocolWebhookEnv
from tests.helpers import assert_signature_verifies_over_wire_body
from tests.helpers.local_http_origin import run_local_origin
from tests.helpers.test_tls_material import load_gen_test_tls, server_ssl_context

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

# The attempt budget ``_send_with_retry_and_logging`` gives one notification:
# three tries total, not three retries after the first. Written once because two
# cases below assert against it and they must not drift apart.
MAX_ATTEMPTS = 3

# A stall long enough that a metric measured in MILLISECONDS is unmistakably
# distinct from the same span measured in SECONDS. ``response_time_ms`` is the
# only delivery-log field whose unit a migration could silently change (the seam
# exposes ``OutboundResult.duration_seconds``, a float over a different span),
# and int(0.25) == 0 — so a swap is only observable against a delivery that
# provably took real time.
_MEASURABLE_DELAY_SECONDS = 0.25

# A destination the egress seam refuses before opening a connection. RFC 6761
# §6.4 reserves ``.invalid`` as a name guaranteed never to resolve, so the
# refusal comes from the seam's ADDRESS policy and arrives without depending on
# what is or is not listening anywhere — and it still arrives with the
# private-range hatch ``LocalOriginMixin`` opens left open, which a loopback URL
# could not manage. Deliberately ``https``: an ``http`` URL would be refused by
# the seam's scheme rule first, grading the scheme instead of the destination.
_UNRESOLVABLE_WEBHOOK_URL = "https://webhook-sink.invalid/webhook"

# The refusal wording an operator reads, in the delivery-log row AND in the audit
# line. It is the EGRESS SEAM's — src/core/security/webhook_egress.py — not this
# sender's: before lane GH #1802 the sender's refusal arm overrode the
# seam's detail with its own "refused by egress policy" string, which is one more
# place the vocabulary could drift. Now the outcome carries the wording and the
# recorder writes it down, so there is exactly one.
_REFUSAL_DETAIL = "the destination was refused before any connection was made"

# The identity values the refused delivery must carry onto its row. They are read
# off the PAYLOAD's ``result`` (``extract_webhook_result_data``), NOT off the
# metadata dict, and they are non-default on purpose: the env's default result
# carries neither, so ``notification_type`` would be ``None`` and
# ``sequence_number`` would take production's ``isinstance(..., int)`` fallback
# of 1. Asserting those defaults would grade nothing — a sender that hardcoded
# ``sequence_number=1`` or dropped ``notification_type`` entirely would still
# pass.
_REFUSAL_NOTIFICATION_TYPE = "delivery_report"
_REFUSAL_SEQUENCE_NUMBER = 7


def _audit_messages(caplog: pytest.LogCaptureFixture, level: int) -> list[str]:
    """Every audit-trail message emitted at exactly ``level``.

    Audit entries are not returned to any caller — the audit log IS the record —
    so they can only be graded where production writes them.
    """
    return [
        record.getMessage() for record in caplog.records if record.name == AUDIT_LOGGER_NAME and record.levelno == level
    ]


class TestSignedBodyIntegrity:
    """A buyer verifying the HMAC over the received bytes must accept the delivery."""

    async def test_receiver_verifies_the_signature_over_the_body_it_received(self, integration_db):
        """The signed bytes and the transmitted bytes must be the same bytes.

        Verification runs through the SDK's own receiver-side
        ``verify_webhook_hmac``, over ``request.body`` — the raw bytes off the
        socket — because that is literally what a buyer's endpoint does. Any
        assertion that re-serialized the payload dict test-side would recompute
        the sender's mistake and pass.

        The payload carries non-ASCII text so the case grades the contract
        ("transmit exactly the bytes that were signed") rather than an encoding
        that happens to agree: compact-but-``ensure_ascii=False`` serialization
        matches the signature for ASCII input and diverges here.
        """
        secret = "shared-webhook-secret-thirty-two-plus"

        with ProtocolWebhookEnv() as env:
            buy = env.make_media_buy()
            config = env.make_config(
                authentication_type="HMAC-SHA256",
                authentication_token=secret,
            )
            env.set_http_status(200)

            delivered = await env.send(
                config=config,
                payload=env.make_payload(result={"media_buy_id": buy.media_buy_id, "note": "café ✓ 日本語"}),
                media_buy_id=buy.media_buy_id,
            )

            assert delivered is True
            assert env.delivery_attempts == 1
            request = env.last_delivery

            try:
                verify_webhook_hmac(
                    headers=dict(request.headers.items()),
                    body=request.body,
                    options=LegacyWebhookHmacOptions(
                        secret=secret.encode("utf-8"),
                        sender_identity="salesagent",
                        now=time.time(),
                    ),
                )
            except LegacyWebhookHmacError as exc:
                pytest.fail(
                    "The buyer's endpoint could not verify the delivered webhook: "
                    f"{exc}. X-AdCP-Signature was computed over bytes other than the "
                    f"{len(request.body)} that crossed the socket "
                    f"({request.body[:120]!r}...)."
                )

    async def test_the_signature_covers_the_timestamp_that_was_sent(self, integration_db):
        """``X-AdCP-Timestamp`` must be the timestamp the signature was computed with.

        Verified independently of the SDK verifier so the two halves of the
        header pair are graded separately: a sender that signed with one
        timestamp and advertised another would leave the replay window
        unenforceable even if the body bytes were right.
        """
        secret = "shared-webhook-secret-thirty-two-plus"

        with ProtocolWebhookEnv() as env:
            env.make_media_buy()
            config = env.make_config(authentication_type="HMAC-SHA256", authentication_token=secret)
            env.set_http_status(200)

            assert await env.send(config=config) is True

            assert_signature_verifies_over_wire_body(env.last_delivery, secret)


class TestDeliveryLogParity:
    """The four ``webhook_delivery_log`` metrics keep their meaning across the migration.

    ``attempt_count`` is total attempts, ``http_status_code`` is the last status
    observed, ``payload_size_bytes`` is the size of the body that was sent, and
    ``response_time_ms`` is the elapsed delivery time in milliseconds. Nothing
    downstream re-derives any of them — the row is the only record an operator
    has of what a buyer's endpoint did.
    """

    async def test_success_row_carries_the_four_metrics(self, integration_db):
        """A delivered notification records one attempt, the status, the size and the time.

        ``payload_size_bytes`` is asserted against the bytes the origin actually
        received rather than against a constant: that is what "the size of the
        payload" means, and it is the form that survives the wire-encoding
        change the HMAC fix requires (the number moves; the meaning does not).
        """
        with ProtocolWebhookEnv() as env:
            buy = env.make_media_buy()
            env.set_http_status(200)
            env.origin.delay(_MEASURABLE_DELAY_SECONDS)

            delivered = await env.send(media_buy_id=buy.media_buy_id)

            assert delivered is True
            assert env.delivery_attempts == 1

            rows = env.delivery_logs(buy.media_buy_id)
            assert len(rows) == 1
            row = rows[0]
            assert row.status == "success"
            assert row.attempt_count == 1
            assert row.http_status_code == 200
            assert row.payload_size_bytes == len(env.last_delivery.body)
            assert row.response_time_ms >= _MEASURABLE_DELAY_SECONDS * 1000, (
                f"response_time_ms is {row.response_time_ms} for a delivery that provably took at "
                f"least {_MEASURABLE_DELAY_SECONDS}s — the metric is no longer milliseconds of "
                "this delivery"
            )
            assert row.completed_at is not None

    async def test_terminal_status_row_records_one_attempt_and_the_status(self, integration_db):
        """A 404 is terminal: one attempt, the status on the row, failure recorded.

        RED today on both halves — see the module docstring. ``e.response`` is
        falsy for every error response, so production's terminal-4xx branch
        never fires (three attempts) and the status never reaches the row.
        """
        with ProtocolWebhookEnv() as env:
            buy = env.make_media_buy()
            env.set_http_status(404, "no such endpoint")

            delivered = await env.send(media_buy_id=buy.media_buy_id)

            assert delivered is False
            assert env.delivery_attempts == 1

            rows = env.delivery_logs(buy.media_buy_id)
            assert len(rows) == 1
            row = rows[0]
            assert row.status == "failed"
            assert row.attempt_count == 1
            assert row.http_status_code == 404
            assert row.payload_size_bytes == len(env.last_delivery.body)
            assert row.error_message is not None

    async def test_exhausted_row_counts_every_attempt_that_was_made(self, integration_db):
        """A 5xx that never clears records the FULL attempt count, matching the hits.

        Asserting the row against ``delivery_attempts`` rather than against the
        literal 3 is what makes this a parity test: whatever the retry policy
        does, the number the operator reads back has to be the number of
        requests the buyer's endpoint actually served. That half is green today.

        ``http_status_code`` is RED today for the reason in the module
        docstring: the column is ``None`` on every HTTP failure, so the row
        cannot distinguish a server error from a client error or from never
        having reached the endpoint at all — which is exactly what the case
        below, ``test_transport_failure_row_records_no_status``, needs the
        ``None`` to MEAN.
        """
        with ProtocolWebhookEnv() as env:
            buy = env.make_media_buy()
            env.set_http_status(500, "boom")

            delivered = await env.send(media_buy_id=buy.media_buy_id)

            assert delivered is False
            assert env.delivery_attempts == MAX_ATTEMPTS

            rows = env.delivery_logs(buy.media_buy_id)
            assert len(rows) == 1
            row = rows[0]
            assert row.status == "failed"
            assert row.attempt_count == env.delivery_attempts
            assert row.http_status_code == 500

    async def test_transport_failure_row_records_no_status(self, integration_db):
        """A delivery that never got a response records the attempts and NO status.

        ``http_status_code`` is nullable precisely for this case, and "the
        endpoint answered 500 three times" versus "the endpoint never answered"
        are different operator-facing facts. A migration that filled the column
        with a placeholder would erase the distinction.
        """
        with ProtocolWebhookEnv() as env:
            buy = env.make_media_buy()
            env.set_http_error()

            delivered = await env.send(media_buy_id=buy.media_buy_id)

            assert delivered is False
            assert env.delivery_attempts == MAX_ATTEMPTS

            rows = env.delivery_logs(buy.media_buy_id)
            assert len(rows) == 1
            row = rows[0]
            assert row.status == "failed"
            assert row.attempt_count == env.delivery_attempts
            assert row.http_status_code is None
            assert row.error_message is not None


class TestRefusedDestinationRow:
    """A destination refused by egress policy still leaves both operator records.

    This is the arm production justifies at the ``OutboundRequestBlocked``
    handler — "a misconfigured destination that leaves no trace is
    indistinguishable from one nobody configured" — and it is the only failure
    arm whose row and audit entry were graded nowhere: the four
    ``TestDeliveryLogParity`` cases all land in the ``OutboundDeliveryFailed``
    arm or on the success path, and the two cases that do reach a refusal
    (``tests/unit/test_protocol_webhook_ssrf.py``) run without a database and so
    can only see that nothing was sent.

    ``attempt_count == 0`` is the load-bearing distinction from every other
    failed row: nothing was attempted, so a count of 1 would report a delivery
    that was tried and failed on the wire. The two identity fields are asserted
    at exact non-default values because they travel the longest distance in this
    file — plucked out of the payload's result at the top of
    ``_send_with_retry_and_logging`` and consumed several frames later by the
    recorder — which is precisely the threading a refactor of that path can drop
    without any other case noticing.
    """

    async def test_refused_destination_records_zero_attempts_and_the_task_identity(self, integration_db, caplog):
        with ProtocolWebhookEnv() as env:
            buy = env.make_media_buy()
            config = env.make_config(url=_UNRESOLVABLE_WEBHOOK_URL)

            with caplog.at_level(logging.INFO, logger=AUDIT_LOGGER_NAME):
                delivered = await env.send(
                    config=config,
                    payload=env.make_payload(
                        result={
                            "media_buy_id": buy.media_buy_id,
                            "notification_type": _REFUSAL_NOTIFICATION_TYPE,
                            "sequence_number": _REFUSAL_SEQUENCE_NUMBER,
                        }
                    ),
                    media_buy_id=buy.media_buy_id,
                    # Stated on the context as well as in the payload result. The
                    # sender used to rebuild its context FROM the payload, so the
                    # result alone reached the row; the typed context is the source
                    # now, and a caller that knows these values says them.
                    notification_type=_REFUSAL_NOTIFICATION_TYPE,
                    sequence_number=_REFUSAL_SEQUENCE_NUMBER,
                )

            assert delivered is False
            assert env.delivery_attempts == 0, (
                "the local origin served a request for a notification aimed at an unresolvable "
                "host — the refusal did not happen before the connection"
            )

            rows = env.delivery_logs(buy.media_buy_id)
            assert len(rows) == 1, (
                f"a refused delivery wrote {len(rows)} delivery-log rows — a misconfigured "
                "destination that leaves no trace is indistinguishable from one nobody configured"
            )
            row = rows[0]
            assert row.status == "refused", (
                f"a destination refused before any connection was opened was written down as "
                f"{row.status!r} — the same word the recorder uses for a delivery the buyer's "
                "endpoint actually rejected. Lane GH #1802 gave the refusal its own "
                "status value precisely so an operator can tell a misconfigured URL from a "
                "flaky endpoint"
            )
            assert row.attempt_count == 0, (
                f"the row claims {row.attempt_count} attempts for a delivery refused before any "
                "connection was opened — an operator cannot tell it apart from one the buyer's "
                "endpoint actually rejected"
            )
            assert row.http_status_code is None
            assert row.error_message == _REFUSAL_DETAIL
            assert row.webhook_url == _UNRESOLVABLE_WEBHOOK_URL
            assert row.task_type == DELIVERY_METADATA_TASK_TYPE
            assert row.notification_type == _REFUSAL_NOTIFICATION_TYPE, (
                f"the row recorded notification_type {row.notification_type!r} for a payload whose "
                f"result carried {_REFUSAL_NOTIFICATION_TYPE!r} — the value was dropped between the "
                "payload and the recorder"
            )
            assert row.sequence_number == _REFUSAL_SEQUENCE_NUMBER, (
                f"the row recorded sequence #{row.sequence_number} for notification "
                f"#{_REFUSAL_SEQUENCE_NUMBER} — the operator's view of which report in the series "
                "failed no longer matches the notification that was refused"
            )

            # The audit entry is the refusal's second record, and the only one for a
            # notification whose task_type writes no row at all. Asserted on the
            # message TAIL so the whole sender-composed line — task type, task id and
            # the failure wording — is pinned exactly, while the severity decoration
            # the audit logger prepends stays the audit logger's business.
            warnings = _audit_messages(caplog, logging.WARNING)
            assert len(warnings) == 1, warnings
            assert warnings[0].endswith(
                f"{DELIVERY_METADATA_TASK_TYPE} webhook failed for task task_001: {_REFUSAL_DETAIL}"
            ), warnings


class TestAuditTrail:
    """Start / success / failure audit entries survive the migration.

    The audit log is the only place a delivery attempt is recorded for
    notifications that write NO delivery-log row (every ``task_type`` outside
    the delivery-report pair), so losing an entry loses the event entirely.
    """

    async def test_start_and_success_entries_are_emitted(self, integration_db, caplog):
        with ProtocolWebhookEnv() as env:
            buy = env.make_media_buy()
            env.set_http_status(200)

            with caplog.at_level(logging.INFO, logger=AUDIT_LOGGER_NAME):
                assert await env.send(media_buy_id=buy.media_buy_id) is True

            messages = _audit_messages(caplog, logging.INFO)
            assert any("Sending media_buy_delivery webhook for task task_001" in m for m in messages), messages
            assert any("media_buy_delivery webhook delivered successfully" in m for m in messages), messages

    async def test_start_and_failure_entries_are_emitted(self, integration_db, caplog):
        with ProtocolWebhookEnv() as env:
            buy = env.make_media_buy()
            env.set_http_status(404, "no such endpoint")

            with caplog.at_level(logging.INFO, logger=AUDIT_LOGGER_NAME):
                assert await env.send(media_buy_id=buy.media_buy_id) is False

            assert any(
                "Sending media_buy_delivery webhook for task task_001" in m
                for m in _audit_messages(caplog, logging.INFO)
            )
            warnings = _audit_messages(caplog, logging.WARNING)
            assert any("media_buy_delivery webhook failed" in m for m in warnings), warnings

    async def test_a_transport_failure_still_leaves_an_audit_trail(self, integration_db, caplog):
        """A delivery that never got a response must not vanish silently.

        This is the arm with no HTTP status to record at all: the origin accepts
        the connection, counts the hit, and closes without answering. An
        endpoint that fails this way and leaves no audit entry is
        indistinguishable from one that was never scheduled.
        """
        with ProtocolWebhookEnv() as env:
            buy = env.make_media_buy()
            env.set_http_error()

            with caplog.at_level(logging.INFO, logger=AUDIT_LOGGER_NAME):
                assert await env.send(media_buy_id=buy.media_buy_id) is False

            assert any(
                "Sending media_buy_delivery webhook for task task_001" in m
                for m in _audit_messages(caplog, logging.INFO)
            )
            assert _audit_messages(caplog, logging.WARNING), "a failed delivery emitted no failure audit entry"


class TestNoClientSurvivesConstruction:
    """Constructing the service must not leave a reusable HTTP client behind.

    This is the structural guard against someone reintroducing a shared pooled
    client and silently defeating the per-destination pin. It asserts on the
    ABSENCE of any client-shaped attribute rather than on a named one, so a
    reintroduction under a different name cannot slip past.
    """

    def test_the_service_holds_no_session_or_client_attribute(self):
        service = ProtocolWebhookService()

        leaked = {
            name: type(value).__name__
            for name, value in vars(service).items()
            if isinstance(value, (requests.Session, httpx.Client, httpx.AsyncClient))
        }

        assert leaked == {}, (
            f"ProtocolWebhookService kept a long-lived HTTP client after construction: {leaked}. "
            "build_ip_pinned_transport resolves its destination at construction and refuses to "
            "connect to any other host, so a client that outlives one delivery either bypasses "
            "the pin or breaks the next destination."
        )


class TestTwoDestinationsOneService:
    """One service instance must be able to deliver to two different hostnames.

    This is the property a shared pinned client makes impossible — the pinned
    transport's backend RAISES ``RuntimeError`` when connecting to a host other
    than the one it resolved — and it is the reason the long-lived session had
    to go rather than merely being wrapped.

    The two destinations differ by HOSTNAME, not by port. ``run_local_origin``
    binds loopback, so two origins on ``127.0.0.1`` would differ only in port,
    and the pin compares the host: a shared pinned client would serve both and
    this test would pass against the exact mutation it exists to catch. The
    second origin therefore binds ``127.0.0.2`` — still loopback, still covered
    by the private-range escape hatch ``LocalOriginMixin`` opens, and now also
    served over real TLS (the generated CA's SAN covers 127.0.0.2 specifically
    for this test — GH #1802, since the seam requires https
    unconditionally now), and a different pin. (``localhost`` would also work
    now that URLs are delivered verbatim, but ``127.0.0.2`` keeps the two pins
    unambiguously distinct without depending on resolver family ordering.)
    """

    async def test_one_service_delivers_to_two_hostnames(self, integration_db):
        with ProtocolWebhookEnv() as env:
            buy = env.make_media_buy()
            env.set_http_status(200)

            gen_test_tls = load_gen_test_tls()
            gen_test_tls.ensure_test_tls()
            with run_local_origin(
                listen_host="127.0.0.2", ssl_context=server_ssl_context(gen_test_tls)
            ) as second_origin:
                second_origin.respond_with(200)
                first = env.make_config(url=env.webhook_url)
                second = env.make_config(url=f"{second_origin.base_url}/webhook")

                assert urlparse(first.url).hostname != urlparse(second.url).hostname, (
                    "the two destinations must differ by hostname, not only by port — "
                    "the IP pin is per-host and a port-only difference grades nothing"
                )

                delivered_first = await env.send(config=first, media_buy_id=buy.media_buy_id)
                delivered_second = await env.send(config=second, media_buy_id=buy.media_buy_id)

                assert delivered_first is True, "the first destination did not receive the notification"
                assert delivered_second is True, (
                    "the SAME service instance could not deliver to a second hostname — a client "
                    "or transport built for the first destination outlived the delivery"
                )
                assert env.delivery_attempts == 1
                assert second_origin.hits == 1


class TestBuyerUrlIsDeliveredVerbatim:
    """The URL the buyer registered is the URL the delivery connects to — byte for byte.

    Core Invariant (CLAUDE.md pattern #9): no code outside
    ``src/core/security/outbound_http.py`` may alter, map, or otherwise decide
    an outbound destination, so the URL ingest validated is the URL the seam
    validates, pins, and connects to.

    Regression pinned: ``protocol_webhook_service`` used to rewrite a
    ``localhost`` hostname to ``host.docker.internal`` BEFORE the URL reached
    ``asend`` (removed in #1589 follow-up). Under that rewrite the delivery
    leaves for a host that is not the buyer's endpoint and the origin below
    records zero requests — which is exactly how this test fails if a rewrite
    ever returns.

    The proof is read at the origin, not in production state: the ``Host``
    header on the request the endpoint actually served is the netloc the client
    dialled, so ``localhost:PORT`` recorded there is byte-for-byte evidence
    that nothing rewrote the destination in front of the seam.

    Determinism note: the origin binds whichever address family ``localhost``
    resolves to FIRST in this process (``serve_in_thread`` mirrors the seam's
    pin, which takes the first ``getaddrinfo`` answer), so the case grades the
    same whether the platform's resolver orders ``::1`` or ``127.0.0.1`` first.
    """

    async def test_a_localhost_url_is_delivered_to_localhost_verbatim(self, integration_db):
        with ProtocolWebhookEnv() as env:
            buy = env.make_media_buy()
            gen_test_tls = load_gen_test_tls()
            gen_test_tls.ensure_test_tls()
            with run_local_origin(listen_host="localhost", ssl_context=server_ssl_context(gen_test_tls)) as origin:
                origin.respond_with(200)
                config = env.make_config(url=f"{origin.base_url}/webhook")
                assert urlparse(config.url).hostname == "localhost", (
                    "the registered URL must carry the literal hostname 'localhost' — "
                    "that is the exact string the deleted rewrite used to match on"
                )

                delivered = await env.send(config=config, media_buy_id=buy.media_buy_id)

                assert origin.hits == 1, (
                    f"the buyer's endpoint at {config.url} served {origin.hits} requests for one "
                    "notification — the delivery went to a destination other than the URL the "
                    "buyer registered (a netloc rewrite sits in front of the egress seam)"
                )
                request = origin.last_request
                assert request.headers["Host"] == f"localhost:{origin.port}", (
                    f"the delivery arrived addressed to Host {request.headers['Host']!r}, not "
                    f"'localhost:{origin.port}' — the netloc was rewritten between the stored "
                    "config and the egress seam"
                )
                assert request.path == "/webhook"
                assert delivered is True

                rows = env.delivery_logs(buy.media_buy_id)
                assert len(rows) == 1
                assert rows[0].webhook_url == config.url, (
                    f"the delivery log recorded {rows[0].webhook_url!r} — the operator-readable "
                    "record must name the URL the buyer registered, not a rewritten destination"
                )


class TestTaskIdentityReachesThePersistedRow:
    """The delivery row carries the sequence number and notification type the SENDER used.

    Not a style finding: both values were CORRUPTED on the way to the database.
    ``notify`` built a typed ``WebhookTaskContext``, ``send_notification`` flattened
    it to a four-key dict (``as_metadata`` emitted ``task_type``, ``tenant_id``,
    ``principal_id``, ``media_buy_id`` and nothing else), and
    ``_send_with_retry_and_logging`` rebuilt a context from that dict plus the
    PAYLOAD -- recovering ``sequence_number`` and ``notification_type`` from the
    payload's result, or falling back to ``1`` and ``None`` when it did not carry
    them. Those fallbacks were what got persisted.

    So a buyer reading ``webhook_delivery_log`` saw a webhook claiming to be the
    first in its sequence and carrying no notification type, when the server had
    sent the seventh and marked it final.

    Graded against the PERSISTED ROW, not against the encoder. The corruption
    happened on the persistence path, so a unit test of ``as_metadata`` would have
    asserted that it correctly emits four keys -- which it did -- and proved
    nothing about the two it dropped.
    """

    async def test_the_row_carries_the_sequence_number_and_notification_type_sent(self, integration_db):
        """Send the seventh, final notification; read back seven and "final"."""
        with ProtocolWebhookEnv() as env:
            buy = env.make_media_buy()
            env.set_http_status(200)

            delivered = await env.send(
                media_buy_id=buy.media_buy_id,
                sequence_number=7,
                notification_type="final",
            )

            assert delivered is True

            rows = env.delivery_logs(buy.media_buy_id)
            assert len(rows) == 1, f"expected exactly one delivery row, got {len(rows)}"
            row = rows[0]

            assert row.sequence_number == 7, (
                f"the sender sent sequence #7; the persisted row says #{row.sequence_number}. "
                "A buyer reconciling the log against what it received cannot tell a resend "
                "from a first delivery."
            )
            assert row.notification_type == "final", (
                f"the sender marked this delivery {'final'!r}; the persisted row says "
                f"{row.notification_type!r}. A buyer cannot tell the series ended."
            )
