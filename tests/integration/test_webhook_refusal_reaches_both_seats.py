"""A non-conforming registration refuses on BOTH seats — and only one of them has an outcome.

Epic D lane C4 (GH #1802), section 10 of the change-set. Ruling #2 says
an authentication block that is not one of the two pinned schemes, or whose
credential is under the pinned ``minLength: 32``, is not a delivery we should be
making. That rule has to hold on both surfaces a stored registration can reach,
and the two surfaces are NOT symmetric:

  SEAT 1 — the ORM-row senders (``webhook_delivery_service``,
    ``protocol_webhook_service``, ``order_approval_service``,
    ``core/webhook_delivery``). They read ``authentication_type`` /
    ``authentication_token`` off a stored row and hand those primitives to
    ``deliver_webhook``, so the refusal happens AT THE SEAM and comes back as a
    ``WebhookDeliveryOutcome`` the sender consumes — that record is what re-keys
    the circuit-breaker failure and the sender's own bookkeeping. The record
    itself is graded next door, in
    ``tests/integration/test_webhook_delivery_outcome_contract.py``; what is
    graded HERE is that a real stored row reaches it and that the sender acts on
    what comes back.

  SEAT 2 — the A2A / workflow-step stash path
    (``ContextManager._send_push_notifications`` ->
    ``ValidatedWebhookRegistration.from_stash``). The refusal happens at
    REHYDRATION, before any sender is constructed, so there is NO outcome record
    on this seat and deliberately so: the outcome type has exactly one producer
    (the seam), and recording here would create a second construction site plus a
    delivery-repository write from the rehydration path — the half-migration Epic
    E would then have to undo. With no migration, no CLI and no durable record,
    this seat's LOG LINE is the only surface the decision has, which is why its
    content is asserted rather than assumed.

A grader that covered seat 1 alone would miss half the behaviour: the shapes that
stop delivering are reachable from both, and the seat-2 half is the one where
"stopped delivering" is invisible to every other observation the suite makes.

RED when authored, and for which reason (the resolver named here has since
been deleted; the seam now refuses both cases, which is what turned these green):
  * seat 1 — every case DELIVERED. The old resolver mapped an unrecognised
    scheme to an unauthenticated delivery and sent it PLAIN, and mapped a
    sub-32 credential to an ordinary bearer/signed variant, because there was
    no length gate at send time at all (``webhook_delivery_service.py``'s "No secret-strength gate"
    comment argues for exactly the tolerance ruling #2 removes).
  * seat 2 — ``from_stash`` deliberately tolerates both shapes today
    (``registration.py``'s "Rows written through the untyped A2A path carry any
    scheme spelling and any credential length"), so the delivery goes out; and
    the refusal it does make is printed through ``rich``'s ``console.print``,
    not logged, so no operator surface exists to assert on.

MUST STAY GREEN and untouched by this file: the BDD delivery scenarios in
``tests/bdd/features/local-egress-ssrf-refusal.feature`` and the UC-004 modules.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest

from tests.harness import CircuitBreakerEnv, MediaBuyPushRegistrationEnv, ProtocolWebhookEnv

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

# The pinned AdCP 3.1.1 ``AuthenticationScheme`` spellings, and the pinned
# ``credentials`` ``minLength: 32`` boundary, as constants: a regression that
# changes what production compares against must fail these cases rather than be
# quietly re-typed into them.
HMAC_SCHEME = "HMAC-SHA256"
BEARER_SCHEME = "Bearer"
CONFORMING_SECRET = "s" * 32
ONE_SHORT_OF_CONFORMING = "s" * 31

# Neither is a member of the pinned scheme enum, and BOTH are refused at the
# seam — probed directly: an unrecognised scheme returns
# ``WebhookDeliveryOutcome(kind='refused_auth', reason='scheme_not_in_spec')``.
#
# An earlier comment here claimed ruling #5 kept ``Basic`` deliverable through a
# ``Literal["Basic"]`` widening on the pinned type. No such widening exists —
# ``webhook_egress.py`` imports the library ``Authentication`` unmodified — and
# this file's own ``test_a_legacy_basic_row_refuses_at_the_seam_too`` grades the
# refusal sixty lines below. The two are kept as separate constants because they
# reach the refusal by different routes, not because one of them delivers.
UNRECOGNISED_SCHEME = "Digest"
LEGACY_SCHEME = "Basic"

# The two loggers that tell the seats apart. A refusal that reached the seam logs
# from the seam; a refusal at rehydration logs from the context manager and the
# seam is never entered — which is precisely what "no outcome record on seat 2"
# means, expressed as something observable rather than as an absence of a value
# no test can hold.
SEAM_LOGGER = "src.core.security.webhook_egress"
REHYDRATION_LOGGER = "src.core.context_manager"


def _stored_registration(*, schemes: list[str], credentials: str | None, url: str) -> dict[str, Any]:
    """The wire-shaped document every producer of this stash key writes."""
    authentication: dict[str, Any] = {"schemes": schemes}
    if credentials is not None:
        authentication["credentials"] = credentials
    return {"url": url, "authentication": authentication}


class TestSeatOneStoredRowsRefuseAtTheSeam:
    """An ORM-row sender reaches the seam, gets an outcome back, and acts on it."""

    @pytest.mark.parametrize(
        ("label", "auth_type", "auth_token"),
        [
            ("unrecognised scheme", UNRECOGNISED_SCHEME, CONFORMING_SECRET),
            ("sub-32 credential", BEARER_SCHEME, ONE_SHORT_OF_CONFORMING),
            ("sub-32 signing secret", HMAC_SCHEME, ONE_SHORT_OF_CONFORMING),
        ],
    )
    def test_the_delivery_service_refuses_and_records_the_failure(self, label, auth_type, auth_token, integration_db):
        """No dial, no success reported, and the breaker is told.

        The circuit-breaker failure is what makes this a test of the OUTCOME
        rather than of an early return: a refusal that never reaches
        ``record_failure()`` leaves an endpoint we can no longer deliver to
        looking perfectly healthy, which is the same reason a refused URL records
        one (``test_delivery_service_behavioral.py::test_refused_url_records_failures``).

        The origin answers 200 throughout. A destination that would have ACCEPTED
        the delivery is what makes zero hits mean *the sender refused* rather than
        *the request went out and failed on arrival*.
        """
        with CircuitBreakerEnv(tenant_id="t1", principal_id="p1") as env:
            env.setup_default_data()
            env.make_webhook_config(auth_type=auth_type, auth_token=auth_token)
            env.set_http_response(200)

            delivered = env.call_send(tenant_id="t1", principal_id="p1")

            assert env.delivery_attempts == 0, (
                f"{label}: {env.delivery_attempts} request(s) reached an origin answering 200 — "
                f"the row was delivered to, not refused"
            )
            assert delivered is False, f"{label}: a refused delivery reported success to its caller"

            _state, failure_count = env.get_service().get_circuit_breaker_state(env.webhook_url)
            assert failure_count == 1, (
                f"{label}: the refusal produced {failure_count} circuit-breaker failures — a "
                f"destination we can no longer deliver to must not look healthy to the breaker"
            )

    def test_a_legacy_basic_row_refuses_at_the_seam_too(self, integration_db):
        """A stored ``Basic`` row stops delivering, graded on a real ORM row.

        This case asserted the opposite until the owner's one-vocabulary ruling.
        ``Basic`` is not an AdCP scheme; it was kept because the A2A push-config
        endpoint stores a free-form string and rows like this exist in production.
        They are now MIGRATED rather than tolerated — re-registered with a scheme
        the pinned enum defines — and until that happens they do not deliver.

        Graded here, on a stored row through the sender, rather than only at the
        seam's own contract: the reversal has to be visible at the seat that
        actually holds such rows, or "we refuse non-spec schemes" would be a
        property of a helper nobody's data reaches.

        The circuit-breaker assertion is the point of the seat: a destination we
        can no longer deliver to must not look healthy to the breaker.
        """
        with CircuitBreakerEnv(tenant_id="t1", principal_id="p1") as env:
            env.setup_default_data()
            env.make_webhook_config(auth_type=LEGACY_SCHEME, auth_token=CONFORMING_SECRET)
            env.set_http_response(200)

            delivered = env.call_send(tenant_id="t1", principal_id="p1")

            assert delivered is False, (
                "a stored Basic row reported delivery success — the scheme is not in the pinned "
                "AuthenticationScheme enum, so the seam must refuse it"
            )
            assert env.delivery_attempts == 0, (
                f"the refusal dialled anyway ({env.delivery_attempts} attempt(s)) — the decision "
                f"is pre-flight, so no socket should have been opened"
            )

    async def test_the_protocol_sender_refuses_and_records_no_delivery_row(self, integration_db):
        """The protocol sender refuses the same row, and writes NO delivery-log row.

        The absence is the assertion, and it is not vacuous: this very env writes
        exactly one row for a delivery that goes out (graded at
        ``test_protocol_webhook_egress.py::TestDeliveryLogParity``). A refusal is
        pre-flight — there is no attempt, no status and no duration to record —
        so a row here would be a fabricated delivery attempt in the operator's
        own audit surface.
        """
        with ProtocolWebhookEnv() as env:
            buy = env.make_media_buy()
            env.set_http_status(200)
            config = env.make_config(
                authentication_type=UNRECOGNISED_SCHEME,
                authentication_token=CONFORMING_SECRET,
            )

            delivered = await env.send(config=config, media_buy_id=buy.media_buy_id)

            assert env.delivery_attempts == 0, (
                f"{env.delivery_attempts} request(s) reached an origin answering 200 — an "
                f"unrecognised scheme was delivered as though it had been plain"
            )
            assert delivered is False, "a refused delivery reported success to its caller"
            assert env.delivery_logs(buy.media_buy_id) == [], (
                f"a pre-flight refusal wrote {len(env.delivery_logs(buy.media_buy_id))} "
                f"webhook_delivery_log row(s) — nothing was attempted, so there is nothing to record"
            )

    async def test_a_conforming_row_still_delivers_signed(self, integration_db):
        """The over-correction guard: refusing on the SCHEME would stop every real HMAC row.

        Held in the same class as the refusals so the two cannot drift: this is
        the row that separates "refuse what the spec does not allow" from "refuse
        anything that asked to be authenticated".
        """
        with ProtocolWebhookEnv() as env:
            buy = env.make_media_buy()
            env.set_http_status(200)
            config = env.make_config(
                authentication_type=HMAC_SCHEME,
                authentication_token=CONFORMING_SECRET,
            )

            delivered = await env.send(config=config, media_buy_id=buy.media_buy_id)

            assert delivered is True
            assert env.delivery_attempts == 1


class TestSeatTwoTheStashPathRefusesWithoutAnOutcome:
    """Rehydration refuses, the status transition survives, and nothing reaches the seam."""

    def _register_and_stash(self, env: MediaBuyPushRegistrationEnv) -> Any:
        """Register a CONFORMING webhook over A2A and return its workflow step.

        Conforming on purpose: the row has to be one the ingest gate accepts, or
        the case would be grading ingest a second time instead of grading what a
        STORED row does. The stash is then rewritten into the legacy shape — the
        one only the untyped A2A path could have written — which is the document
        this seat actually meets in production.
        """
        _tenant, _principal, product, pricing_option = env.setup_media_buy_data()
        env.call_a2a(
            **env.minimal_create_kwargs(
                product,
                pricing_option,
                push_notification_config=_stored_registration(
                    schemes=[HMAC_SCHEME],
                    credentials=CONFORMING_SECRET,
                    url=env.webhook_url,
                ),
            )
        )
        return env.push_step("create_media_buy")

    @pytest.mark.parametrize(
        ("label", "schemes", "credentials", "named_scheme"),
        [
            ("unrecognised scheme", [UNRECOGNISED_SCHEME], CONFORMING_SECRET, UNRECOGNISED_SCHEME),
            ("sub-32 credential", [BEARER_SCHEME], ONE_SHORT_OF_CONFORMING, BEARER_SCHEME),
        ],
    )
    def test_a_stored_legacy_row_stops_delivering_and_says_so(
        self, label, schemes, credentials, named_scheme, integration_db, caplog
    ):
        """The webhook is skipped, the status transition is not, and the log names the row.

        Fail-closed is the existing contract of this path and must survive
        untouched: this runs inside a workflow-step status update, so a stash that
        is no longer deliverable costs the WEBHOOK, never the transition.

        The log line carries the whole decision. Ruling #3 ships no migration, no
        backfill and no operator report — an operator enumerates affected rows
        with their own query — so a refusal that does not name the scheme, the
        reason and the step leaves nobody able to act on it.
        """
        with MediaBuyPushRegistrationEnv() as env:
            step = self._register_and_stash(env)
            env.set_http_status(200)
            env.restash_authentication(step, {"schemes": schemes, "credentials": credentials})

            with caplog.at_level(logging.ERROR):
                env.complete_step(step)

            assert env.delivery_attempts == 0, (
                f"{label}: {env.delivery_attempts} request(s) reached an origin answering 200 — "
                f"the stored row was delivered to, not refused"
            )
            assert env.step_status(step) == "completed", (
                f"{label}: the step is {env.step_status(step)!r} — an undeliverable webhook must "
                f"cost the webhook, never the status transition it rides on"
            )

            refusals = [
                record
                for record in caplog.records
                if record.name == REHYDRATION_LOGGER and record.levelno >= logging.ERROR
            ]
            assert len(refusals) == 1, (
                f"{label}: the rehydration refusal produced {len(refusals)} ERROR lines from "
                f"{REHYDRATION_LOGGER} — with no migration and no durable record, this line is the "
                f"only surface this decision has"
            )
            message = refusals[0].getMessage()
            assert named_scheme in message, f"{label}: the refusal does not name the scheme: {message!r}"
            assert step.step_id in message, (
                f"{label}: the refusal does not name the step, so the operator cannot find the "
                f"registration it belongs to: {message!r}"
            )
            assert credentials not in message, f"{label}: the refusal leaked the buyer's credential: {message!r}"

    def test_the_refusal_happens_at_rehydration_so_no_outcome_is_produced(self, integration_db, caplog):
        """Seat 2 never enters the seam — which is what "no outcome record" means here.

        Stated as an observation rather than as an absence of a value: the seam
        logs its own refusal (graded in
        ``test_webhook_delivery_outcome_contract.py``), so silence from the seam
        logger while the rehydration logger refuses is the evidence that this seat
        stopped short of the one producer of ``WebhookDeliveryOutcome``. Recording
        an outcome here would be a second construction site and a delivery-repository
        write from the rehydration path — the half-migration Epic E must undo.
        """
        with MediaBuyPushRegistrationEnv() as env:
            step = self._register_and_stash(env)
            env.set_http_status(200)
            env.restash_authentication(step, {"schemes": [UNRECOGNISED_SCHEME], "credentials": CONFORMING_SECRET})

            with caplog.at_level(logging.ERROR):
                env.complete_step(step)

            assert [record.getMessage() for record in caplog.records if record.name == SEAM_LOGGER] == [], (
                "the stash path reached the delivery seam — this seat must refuse at rehydration, "
                "before a sender exists, so that the outcome record keeps exactly one producer"
            )
            assert any(
                record.name == REHYDRATION_LOGGER and record.levelno >= logging.ERROR for record in caplog.records
            ), "nothing refused at all — the stored row was neither delivered nor reported"

    def test_a_conforming_stash_still_delivers(self, integration_db):
        """The control: rehydration must not refuse the rows it is supposed to carry.

        Without it, "seat 2 refuses" would be satisfied by a rehydration that
        refuses everything, which converts every stored registration from
        delivered into never delivered at all — the failure this path's own
        docstring exists to prevent, arriving from the other end.
        """
        with MediaBuyPushRegistrationEnv() as env:
            step = self._register_and_stash(env)
            env.set_http_status(200)

            env.complete_step(step)

            assert env.delivery_attempts == 1, (
                f"a conforming stored registration produced {env.delivery_attempts} deliveries"
            )
