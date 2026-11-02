"""A registration that asked for HMAC is DELIVERED signed — on every surface.

Epic D lane C2. ``tests/integration/test_webhook_sender_auth_contract.py``
already grades what a sender does with a STORED row, and
``tests/integration/test_webhook_hmac_credentials_ingest_refusal.py`` grades what
ingest does with an unusable registration. Between them sits the gap this file
grades: a registration that was accepted, and a sender that would have signed
it, still deliver UNSIGNED if the credential half is lost in the HANDOFF — the
workflow-step stash on the media-buy paths. Nothing on either side can see that,
because each end is individually correct.

Two producers reach that handoff; each gets a case, and each case gets a
reverse-TDD control that drops the credential half from the stash and shows the
delivery arrives unsigned. The control is what makes the primary case a grader
rather than a green mark: a case that cannot go red under the exact damage it
exists to detect is grading nothing, and this whole lane is a change to how
that handoff is represented.

Why the signature and not the stash's shape: the lane rewrites the intermediate
representation (raw dict / raw protobuf today, a validated value tomorrow), so
any assertion about the stash would have to be rewritten by the change it is
supposed to be guarding. What the buyer's endpoint receives is invariant across
that rewrite — and it is also the only thing a buyer can act on.

Why the registering calls are SIGNED: this seller verifies RFC 9421 signatures, and
a request that hands it webhook credentials must carry one (security.mdx @ v3.1.1
:1462-1465, enforced by ``src/core/signing/webhook_credentials.py``). That is a
precondition of getting a registration accepted at all, not the property under test —
see :func:`_register`.

Why integration and not BDD: two of the three deliveries are fired by a
workflow-step status change and the third by a task reaching a terminal state,
both after the buyer's call has returned. There is no wire envelope for a
``Then`` step to assert on. The identical rationale is recorded at
``tests/integration/test_order_approval_webhook.py`` and
``tests/bdd/features/local-egress-ssrf-refusal.feature:45-51``.

MUST STAY GREEN untouched, and deliberately not modified here:
``tests/integration/test_webhook_sender_auth_contract.py``,
``tests/integration/test_order_approval_webhook.py``.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from tests.factories.webhook import PushNotificationConfigRequestFactory
from tests.harness import MediaBuyPushRegistrationEnv, Transport
from tests.helpers import assert_delivered_unsigned, assert_signature_verifies_over_wire_body

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

# The pinned AdCP 3.1.1 ``AuthenticationScheme`` spelling every writer in
# ``src/`` persists. A constant, not a literal per case: a regression that
# changed the spelling production compares against must fail these cases rather
# than be quietly re-typed into them.
HMAC_SCHEME = "HMAC-SHA256"

# At least 32 characters because the pinned AdCP 3.1.1
# ``core/push-notification-config.json`` puts ``minLength: 32`` on
# ``authentication.credentials``, and these registrations are made through the
# real tool wire where that constraint is enforced. (The sibling sender tests
# use a short secret precisely because they write the ORM column directly and
# never cross that schema — the column carries no length requirement.)
STRONG_SECRET = "buyer-shared-secret-32-chars-or-more"


def _tool_auth_block() -> dict[str, Any]:
    """The AdCP tool shape: a ``schemes`` LIST plus ``credentials``."""
    return {"schemes": [HMAC_SCHEME], "credentials": STRONG_SECRET}


def _a2a_auth_block() -> dict[str, Any]:
    """The A2A protobuf shape: a SINGULAR free-form ``scheme``.

    Not the same object as the tool shape and deliberately not derived from it —
    ``AuthenticationInfo`` is a protobuf message with no enum behind it, and a
    helper that papered over the difference would hide the very divergence that
    makes a non-canonical spelling REFUSE at the seam rather than authenticate.
    """
    return {"scheme": HMAC_SCHEME, "credentials": STRONG_SECRET}


def _assert_delivered_signed(env: Any) -> None:
    """Exactly one delivery arrived, and its signature verifies over the wire body."""
    assert env.delivery_attempts == 1, (
        f"expected exactly one delivery, saw {env.delivery_attempts} — "
        f"a registration that never reached the sender cannot be graded for signing"
    )
    assert_signature_verifies_over_wire_body(env.last_delivery, STRONG_SECRET)


def _register(env: MediaBuyPushRegistrationEnv, what: str, **kwargs: Any) -> Any:
    """Dispatch a registering call over MCP as a buyer that HANDS THIS SELLER CREDENTIALS.

    *what* names the producer for the failure message only — the env selects the
    tool from the request's own content (``MediaBuyDualEnv._is_update_request``).

    Every call here carries an ``authentication`` block, and that is precisely the
    request security.mdx @ v3.1.1 :1462-1465 makes un-refusable while unsigned: a
    seller whose ``request_signing.supported`` is true MUST require the inbound
    request to be RFC 9421-signed when ``push_notification_config.authentication``
    is present, "regardless of ``required_for`` membership" (:1375), and this agent
    enforces it in ``src/core/signing/webhook_credentials.py`` +
    ``verifier._bucket_for``. An unsigned registration is answered
    ``request_signature_required`` and never reaches the stash these cases grade.

    So the buyer signs. Signing is a PRECONDITION of the registration, not the
    property under test: what is graded is still what the buyer's endpoint receives
    when the workflow step fires, and the reverse-TDD controls still damage only the
    stash. ``enable_request_signing()`` is idempotent and is called here rather than
    in each case, so no case can register through an unsigned dispatch by omission.

    The dispatch is asserted to have SUCCEEDED: a refused registration writes no
    stash at all, and a case that then found no delivery would report a signing
    defect for a request the seller never accepted.
    """
    env.enable_request_signing()
    result = env.call_via(Transport.MCP, signed=True, **kwargs)
    assert result.is_success, (
        f"the signed {what} registration was refused ({result.wire_error_envelope or result.error!r}); "
        f"nothing was stashed, so there is no handoff left to grade"
    )
    return result.payload


def _register_via_create(env: MediaBuyPushRegistrationEnv, *, with_push_config: bool) -> Any:
    """Run a real create_media_buy over MCP, optionally registering the webhook.

    ``with_push_config=False`` is how the update case gets a media buy to
    update without also registering anything — the update case must grade its
    OWN producer, not one create already made correct. That create hands the seller
    no credentials, so nothing escalates it and it is dispatched UNSIGNED: the
    escalation :func:`_register` describes fires on the ``authentication`` block
    alone, not on the tool.
    """
    tenant, _principal, product, pricing_option = env.setup_media_buy_data()
    kwargs = env.minimal_create_kwargs(product, pricing_option)
    if not with_push_config:
        return env.call_mcp(**kwargs)
    kwargs["push_notification_config"] = PushNotificationConfigRequestFactory.payload(
        url=env.webhook_url, authentication=_tool_auth_block()
    )
    return _register(env, "create_media_buy", **kwargs)


def _register_via_update(env: MediaBuyPushRegistrationEnv, media_buy_id: str) -> Any:
    """Run a real update_media_buy over MCP that registers the HMAC webhook.

    Written once for both update cases, for the reason :func:`_bare_update_req`
    gives: they differ only in what they do to the stash afterwards.
    """
    return _register(
        env,
        "update_media_buy",
        req=_bare_update_req(media_buy_id),
        push_notification_config={"url": env.webhook_url, "authentication": _tool_auth_block()},
    )


def _bare_update_req(media_buy_id: str) -> Any:
    """The minimal VALID update of *media_buy_id*, carrying no registration.

    Written once rather than at each call site: the two update cases below differ
    only in what they do to the stash afterwards, so a second copy of the constructor
    is the shape the DRY invariant forbids and the way the two would drift into
    updating different documents.

    ``account`` and ``idempotency_key`` are not padding. AdCP 3.1.1
    ``media-buy/update-media-buy-request.json`` /required is
    ``[idempotency_key, account, media_buy_id]``, so an update omitting them is
    refused as a malformed document and never reaches the registration these
    cases grade. The key is per-call unique because the pinned shape is a
    client-generated at-most-once token (16-255 chars); a fixed one would make
    the two cases collide on idempotency rather than each run its own update.
    """
    from src.core.schemas import UpdateMediaBuyRequest

    return UpdateMediaBuyRequest(
        media_buy_id=media_buy_id,
        account={"account_id": "acct_test"},
        idempotency_key=f"upd-{uuid.uuid4().hex}",
    )


# RETIRED: TestA2AProtocolRegistrationDeliversSigned. Its producer was the A2A protocol
# envelope's own push registration, which this agent no longer implements -- it advertises
# `push_notifications=false` and declines all four `tasks/pushNotificationConfig/*` methods,
# because AdCP 3.1.1 L3/webhooks.mdx :308 makes that a separate registration channel with a
# separate (A2A `Task`) envelope. The two producers below are the AdCP-channel ones, and
# they still grade the handoff this file exists for.


class TestCreateMediaBuyRegistrationDeliversSigned:
    """``create_media_buy`` registers; the workflow step's status change delivers signed."""

    def test_workflow_step_webhook_carries_the_registered_signature(self, integration_db):
        with MediaBuyPushRegistrationEnv() as env:
            _register_via_create(env, with_push_config=True)
            env.set_http_status(200)

            env.complete_step(env.push_step("create_media_buy"))

            _assert_delivered_signed(env)

    def test_control_the_delivery_goes_unsigned_when_the_stash_loses_the_credentials(self, integration_db):
        """Reverse-TDD: damage only the stash, and the case above must go red."""
        with MediaBuyPushRegistrationEnv() as env:
            _register_via_create(env, with_push_config=True)
            env.set_http_status(200)

            step = env.push_step("create_media_buy")
            env.drop_stashed_credential_half(step)
            env.complete_step(step)

            assert_delivered_unsigned(env)


class TestUpdateMediaBuyRegistrationDeliversSigned:
    """``update_media_buy`` registers; its workflow step delivers signed.

    The producer two solution-review passes singled out. ``update_media_buy``
    writes ``request_data["push_notification_config"]`` by model-dumping the
    request — the same key ``ContextManager._send_push_notifications`` reads —
    without going through ``create_media_buy``'s stash writer at all. A stash
    format chosen to suit the create path alone therefore resolves this
    registration to "unauthenticated" and delivers an HMAC config UNSIGNED,
    which is the state Epic D declares unconstructible. Nothing else in the
    lane's grader set touches this producer.

    The webhook row is registered separately (by an earlier
    ``create_media_buy``, in production) because ``update_media_buy`` never
    upserts one: the DB row is what makes the delivery happen at all, while the
    STASH is what decides whether it is signed. Exactly one row, so exactly one
    delivery.
    """

    def test_workflow_step_webhook_carries_the_registered_signature(self, integration_db):
        with MediaBuyPushRegistrationEnv() as env:
            created = _register_via_create(env, with_push_config=False)
            env.register_delivery_target()
            env.set_http_status(200)

            _register_via_update(env, created.media_buy_id)

            env.complete_step(env.push_step("update_media_buy"))

            _assert_delivered_signed(env)

    def test_control_the_delivery_goes_unsigned_when_the_stash_loses_the_credentials(self, integration_db):
        """Reverse-TDD: damage only the stash, and the case above must go red."""
        with MediaBuyPushRegistrationEnv() as env:
            created = _register_via_create(env, with_push_config=False)
            env.register_delivery_target()
            env.set_http_status(200)

            _register_via_update(env, created.media_buy_id)

            step = env.push_step("update_media_buy")
            env.drop_stashed_credential_half(step)
            env.complete_step(step)

            assert_delivered_unsigned(env)


class TestOneEventIsOneDelivery:
    """One logical event is delivered ONCE, whatever the principal has stored.

    AdCP 3.1.1 ``compliance/universal/webhook-emission.yaml``,
    ``expect_no_duplicate_webhook_on_replay``: "At most one logical webhook event is
    delivered for the operation."

    This is the case the suite could not have: ``_send_push_notifications`` looped over
    every active ``PushNotificationConfig`` the principal had and sent the step's OWN
    stashed registration once per row -- the loop variable was never read. Configs
    accumulate one per operation, so the duplication grew with the principal's history:
    on the storyboard tenant the run's third create received three copies of one event,
    the same payload to the same URL inside 17ms.

    Every test in this file stayed green through it, because the harness seeded exactly
    one row and had no way to seed two. At one row the buggy model and the correct one
    are indistinguishable. ``register_delivery_target(count=...)`` exists so this case
    can tell them apart, and ``delivery_attempts`` is read off the REAL origin.

    Integration and not BDD for this file's stated reason: the delivery is fired by a
    workflow-step status change after the buyer's call returned, so there is no wire
    envelope for a ``Then`` step to assert on.
    """

    def test_three_stored_configs_still_produce_one_delivery(self, integration_db):
        with MediaBuyPushRegistrationEnv() as env:
            created = _register_via_create(env, with_push_config=False)
            # Three, where every other case in this file seeds one. Under the removed
            # loop this delivered three times; the count is the whole assertion.
            env.register_delivery_target(count=3)
            env.set_http_status(200)

            _register_via_update(env, created.media_buy_id)

            env.complete_step(env.push_step("update_media_buy"))

            assert env.delivery_attempts == 1, (
                f"one status change on one mapped object delivered {env.delivery_attempts} "
                f"times -- the count followed the principal's stored config rows, which is "
                f"the fan-out AdCP caps at one delivery per logical event"
            )


class TestRefusedStashCostsTheWebhookNotTheTransition:
    """A stash the gate REFUSES must cost that webhook only.

    Grades the fail-closed OUTCOME of design revision #2: rehydration re-runs the
    ingest gate, so a stash that no longer passes it raises inside a status
    update. The obligation is that the buyer loses the notification, never the
    state transition.

    Honest scope: this asserts the OUTCOME, and the outcome is defended twice —
    by the per-webhook ``except AdCPValidationError: continue`` branch and by the
    pre-existing outer ``except Exception`` net. So it does not redden if only
    the branch is reverted; it reddens if BOTH nets go. The branch's marginal value
    over the outer net is that it refuses one webhook explicitly instead of
    unwinding out of both loops with a traceback, which is a logging and
    sibling-preservation property rather than a delivery-outcome one.
    """

    def test_a_gate_refusing_stash_delivers_nothing_and_still_completes(self, integration_db):
        with MediaBuyPushRegistrationEnv() as env:
            _register_via_create(env, with_push_config=True)
            env.set_http_status(200)

            step = env.push_step("create_media_buy")
            env.poison_stashed_registration(step)
            env.complete_step(step)

            assert env.delivery_attempts == 0, (
                f"a stash the ingest gate refuses produced {env.delivery_attempts} delivery attempts — "
                f"an unreceipted registration reached the sender"
            )
            assert env.step_status(step) == "completed", (
                "the status transition did not survive an undeliverable stash — "
                "a refused webhook must cost the notification, not the workflow state"
            )


class TestBlankUrlRegistrationIsNotPersisted:
    """A whitespace-only URL must never become a stored config row.

    The obligation is stable across two lanes; only WHO refuses has changed, and
    each change made the answer stronger:

    - Lane C2 gave ``_impl`` a write guard keyed on ``registration.url.strip()``.
      That mattered because the registration gate is a documented no-op on blank
      URLs, so ``accept_push_notification_config`` RETURNS a value for
      ``url="   "``; keying the write on the value's PRESENCE persisted a row with
      a whitespace url (measured: ``('   ', 'HMAC-SHA256')``), and C2 also deleted
      the repository ValueError that used to catch it downstream.
    - Lane C3 then made the A2A tool wrapper coerce through the pinned model, so a
      whitespace url is now REFUSED at ingest — correctably, naming
      ``push_notification_config.url`` — instead of being silently dropped. The
      buyer learns their registration did not take effect, which is the whole
      point of the epic.

    The C2 write guard is GONE, and its removal is part of the same movement: with
    ``_impl`` typed, ``PushNotificationConfig(url="   ")`` raises ``url_parsing``,
    so a blank-url config cannot be constructed and the guard became unreachable
    code. The protection did not disappear — it moved into the type, and this case
    grades it at the wrapper, which is why it asserts a REFUSAL as well as the
    absent row rather than only the absent row.

    WHICH code, and why the refusal is graded on the wire and not on an exception
    class. ``   `` fails the pinned ``core/push-notification-config.json``
    (AdCP 3.1.1) constraint on ``url`` — ``"type": "string", "format": "uri"`` —
    which is a SCHEMA violation, and ``enums/error-code.json`` splits exactly
    there: ``INVALID_REQUEST`` is "malformed, missing required fields, or violates
    schema constraints", ``VALIDATION_ERROR`` is "violates business rules beyond
    schema validation". So the code is ``INVALID_REQUEST`` with
    ``recovery: "correctable"``, and ``core/error.json``'s ``field`` — "field path
    associated with the error in JSONPath-lite format" — must name
    ``push_notification_config.url`` so the buyer knows WHAT to fix. Both halves
    are asserted, because a refusal that does not name the field degrades the
    buyer to "invalid request" with nowhere to go even though the request was
    correctly rejected.

    The assertion goes through ``TransportResult.assert_wire_error`` on the
    envelope the buyer actually received, not ``pytest.raises`` on a production
    exception class: the harness no longer reconstructs ``AdCPSalesAgentError``
    subclasses from wire bytes (``tests/harness/_base.py`` ``WireError``), so a
    class assertion here would grade a reconstruction that no longer exists.
    See ``tests/CLAUDE.md`` § Error Verification Policy.
    """

    def test_whitespace_only_url_is_refused_and_writes_no_config_row(self, integration_db):
        with MediaBuyPushRegistrationEnv() as env:
            _, _principal, product, pricing_option = env.setup_media_buy_data()
            kwargs = env.minimal_create_kwargs(product, pricing_option)
            kwargs["push_notification_config"] = PushNotificationConfigRequestFactory.payload(
                url="   ", authentication=_tool_auth_block()
            )

            result = env.call_via(Transport.A2A, **kwargs)

            assert result.is_error, f"a whitespace-only URL must be refused, but the create returned {result.payload!r}"
            result.assert_wire_error(
                "INVALID_REQUEST",
                recovery="correctable",
                field="push_notification_config.url",
            )
            rows = env.persisted_config_rows()
            assert rows == [], (
                f"a whitespace-only URL was persisted as "
                f"{[(row.url, row.authentication_type) for row in rows]} — a refused registration "
                f"must leave nothing behind"
            )
