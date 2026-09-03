"""What typing ``push_notification_config`` must NOT break.

Epic D lane C3 (GH #1802). The lane types the config parameter all the
way into ``_impl`` and stops ``schemes[0]`` from swallowing a scheme. Both of
its two RED scenarios live in ``tests/bdd/features/local-egress-ssrf-refusal.feature``
(a >1 ``schemes`` array and a too-short ``credentials``, refused at ingest on
every wired transport). This file grades the other half of the change: the two
behaviors that PASS today, that nothing in the RED set would notice breaking,
and that the obvious implementation of the lane silently reverses.

1. THE LEGACY-TOLERANCE GUARD. A ``schemes`` array with two entries is
   schema-invalid against the pin (``core/push-notification-config.json``,
   ``maxItems: 1``), so the lane refuses it AT INGEST. Rows carrying one
   nevertheless exist: the untyped A2A tool path forwards the buyer's raw dict
   and ``schemes[0]`` accepted it silently. Making ``from_stash`` strict for
   symmetry would convert those rows from *delivered* into *never delivered* —
   a refusal at rehydration surfaces to nobody (the delivery path fails closed
   and continues), and the buyer is no longer there to correct anything. Strict
   at ingest, tolerant at rehydration is the split; this is its grader.

2. THE ROW-IDENTITY GUARD. ``push_notification_config.id`` is not a value field
   — it names the ROW to upsert. It reaches ``_impl`` today only because the A2A
   path forwards a raw dict; the AdCP model has no ``id`` field and is
   ``extra="ignore"``, so coercing the parameter DROPS it silently and every
   re-registration inserts a fresh row instead of updating the buyer's. Nothing
   type-checks that away and no refusal test can see it — the only observable is
   how many rows exist afterwards.

Why integration and not BDD: (1) is fired by a workflow-step status change after
the buyer's call has returned, so there is no wire envelope for a ``Then`` step
to assert on; (2) asserts on stored rows, not on a response. The identical
rationale is recorded at
``tests/integration/test_webhook_registration_reaches_delivery_signed.py`` and
``tests/bdd/features/local-egress-ssrf-refusal.feature``.

Both cases carry a reverse-TDD control, because both are GREEN today: a case
that cannot go red under the exact damage it exists to detect is grading
nothing.

MUST STAY GREEN untouched, and deliberately not modified here:
``tests/integration/test_webhook_registration_reaches_delivery_signed.py``,
``tests/integration/test_webhook_sender_auth_contract.py``.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.core.exceptions import AdCPValidationError
from src.core.webhooks.registration import ValidatedWebhookRegistration, accept_push_notification_config
from tests.harness import MediaBuyPushRegistrationEnv
from tests.helpers import assert_signature_verifies_over_wire_body

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

# The pinned AdCP 3.1.1 ``AuthenticationScheme`` spellings. Constants rather than
# literals per case: a regression that changed the spelling production compares
# against must fail these cases rather than be quietly re-typed into them.
HMAC_SCHEME = "HMAC-SHA256"
BEARER_SCHEME = "Bearer"

# At least 32 characters, because these registrations are made through the real
# tool wire where the pinned ``credentials`` ``minLength: 32`` applies.
STRONG_SECRET = "buyer-shared-secret-32-chars-or-more"

# The row id the buyer names on the A2A path. Spelled like production's own
# generated ids (``pnc_<hex>``) so the case cannot pass because something
# special-cased a test-shaped string.
BUYER_ROW_ID = "pnc_lanec3rowidentity"


def _registration(*, row_id: str | None = None) -> dict[str, Any]:
    """A valid single-scheme HMAC registration, optionally naming its row."""
    config: dict[str, Any] = {
        "url": "https://buyer.example.com/hook",
        "authentication": {"schemes": [HMAC_SCHEME], "credentials": STRONG_SECRET},
    }
    if row_id is not None:
        config["id"] = row_id
    return config


def _seed(env: MediaBuyPushRegistrationEnv) -> tuple[Any, Any]:
    """Seed the create dependency chain ONCE and return what a request names.

    Once per env, not once per registration: the row-identity cases register
    twice against the same tenant, and re-seeding between them would collide on
    the product primary key — and, more to the point, a second tenant would make
    "one row exists" true for a reason that has nothing to do with the upsert.
    """
    _tenant, _principal, product, pricing_option = env.setup_media_buy_data()
    return product, pricing_option


def _register_over_a2a(env: MediaBuyPushRegistrationEnv, seeded: tuple[Any, Any], config: dict[str, Any]) -> Any:
    """Run a real create_media_buy over A2A, registering *config*.

    A2A and not MCP, and that is load-bearing for both cases here: the A2A skill
    handler pops ``push_notification_config`` and forwards the buyer's RAW DICT,
    which is the only path on which an ``id`` survives to ``_impl`` at all. An
    MCP-driven version of the row-identity case would assert nothing — the typed
    tool parameter has no ``id`` field to carry.
    """
    product, pricing_option = seeded
    return env.call_a2a(**env.minimal_create_kwargs(product, pricing_option, push_notification_config=config))


def _webhook_url_of(env: MediaBuyPushRegistrationEnv, config: dict[str, Any]) -> dict[str, Any]:
    """The registration with its URL pointed at this env's live origin."""
    return {**config, "url": env.webhook_url}


class TestLegacyMultiSchemeStashNowRefuses:
    """REVERSED BY OWNER DECISION — a multi-scheme stored row no longer delivers.

    This class previously asserted the opposite: that a legacy ``["HMAC-SHA256",
    "Bearer"]`` row keeps delivering, narrowed positionally to ``schemes[0]``,
    because converting "delivered" into "never delivered at all" for a row whose
    owner cannot be asked to fix it is the failure this epic exists to remove.

    Owner ruling for Epic D lane C4: "Refuse — spec or nothing." The pinned schema
    gives ``authentication.schemes`` ``maxItems: 1`` and states that a seller "MUST
    NOT sign the same webhook both ways", so a multi-entry array is schema-INVALID,
    not merely unusual. Such a row is not a delivery we should be making; its owner
    re-registers with one scheme.

    Its third method — a reverse-TDD control asserting that STRICT rehydration stops
    the row delivering — was RETIRED rather than rewritten, because strictness is now
    production: the control had no subject left, and its harness hook
    ``rehydration_refuses_multi_scheme`` would have become a silent no-op that still
    reported green. What replaces it is the pair below: the refusal itself, and the
    conforming control proving the refusal is specific rather than blanket.
    """

    def test_a_two_scheme_stash_stops_delivering(self, integration_db):
        with MediaBuyPushRegistrationEnv() as env:
            _register_over_a2a(env, _seed(env), _webhook_url_of(env, _registration()))
            env.set_http_status(200)

            step = env.push_step("create_media_buy")
            env.widen_stashed_schemes(step, [HMAC_SCHEME, BEARER_SCHEME])
            env.complete_step(step)

            assert env.delivery_attempts == 0, (
                f"a multi-scheme row produced {env.delivery_attempts} deliveries — the pinned "
                f"schema allows exactly one scheme and the owner ruled that a non-conforming "
                f"stored block refuses rather than being narrowed to its first entry"
            )

    def test_a_bearer_first_two_scheme_stash_also_stops_delivering(self, integration_db):
        """The other order, so the refusal cannot be read as "we refuse HMAC-first rows".

        The previous version of this method asserted positional narrowing — that a
        Bearer-first row delivers as Bearer. Under the ruling, order is irrelevant:
        the array length is what makes the block invalid.
        """
        with MediaBuyPushRegistrationEnv() as env:
            _register_over_a2a(env, _seed(env), _webhook_url_of(env, _registration()))
            env.set_http_status(200)

            step = env.push_step("create_media_buy")
            env.widen_stashed_schemes(step, [BEARER_SCHEME, HMAC_SCHEME])
            env.complete_step(step)

            assert env.delivery_attempts == 0, (
                f"a Bearer-first multi-scheme row produced {env.delivery_attempts} deliveries — "
                f"the refusal must key on the array being multi-entry, not on which scheme is first"
            )

    def test_control_a_conforming_single_scheme_row_still_delivers(self, integration_db):
        """The refusal is SPECIFIC: one scheme still delivers, signed.

        Without this, a gate that refused every stored registration would pass both
        cases above. This is what makes them a reversal rather than a blanket break.
        """
        with MediaBuyPushRegistrationEnv() as env:
            _register_over_a2a(env, _seed(env), _webhook_url_of(env, _registration()))
            env.set_http_status(200)

            step = env.push_step("create_media_buy")
            env.complete_step(step)

            assert env.delivery_attempts == 1, (
                f"a conforming single-scheme row produced {env.delivery_attempts} deliveries — "
                f"the multi-scheme refusal is over-broad"
            )
            assert_signature_verifies_over_wire_body(env.last_delivery, STRONG_SECRET)


class TestA2AReRegistrationUpsertsTheRowTheBuyerNamed:
    """``push_notification_config.id`` keys the upsert, so re-registration updates one row."""

    def test_re_registering_the_same_id_updates_the_same_row(self, integration_db):
        with MediaBuyPushRegistrationEnv() as env:
            config = _webhook_url_of(env, _registration(row_id=BUYER_ROW_ID))
            seeded = _seed(env)

            _register_over_a2a(env, seeded, config)
            _register_over_a2a(env, seeded, config)

            rows = env.persisted_config_rows()
            assert [row.id for row in rows] == [BUYER_ROW_ID], (
                f"re-registering the row the buyer named left {[row.id for row in rows]} — "
                f"the id did not reach the upsert, so every re-registration inserts a new row "
                f"and the buyer can no longer address the one they created"
            )

    def test_control_losing_the_row_identity_inserts_a_second_row(self, integration_db):
        """Reverse-TDD: drop the id between wrapper and ``_impl``; the case above must go red."""
        with MediaBuyPushRegistrationEnv() as env:
            config = _webhook_url_of(env, _registration(row_id=BUYER_ROW_ID))
            seeded = _seed(env)

            with env.wrapper_loses_the_row_identity():
                _register_over_a2a(env, seeded, config)
                _register_over_a2a(env, seeded, config)

            rows = env.persisted_config_rows()
            assert len(rows) == 2, (
                f"the row identity was dropped and only {len(rows)} row(s) exist — the case above "
                f"would stay green with the id gone, so it is not grading the upsert key"
            )
            assert BUYER_ROW_ID not in [row.id for row in rows], (
                f"the buyer's id survived a mutation that removes it: {[row.id for row in rows]}"
            )


class TestMultiSchemeIsRefusedAtIngestAndAtRehydration:
    """The strict/tolerant split, graded from BOTH sides.

    Pinned AdCP 3.1.1 gives ``authentication.schemes`` ``{"minItems": 1,
    "maxItems": 1}`` and states "**Precedence is a switch, not a fallback** ... A
    seller MUST NOT sign the same webhook both ways", so a multi-entry array is
    schema-INVALID. Taking ``schemes[0]`` would drop the buyer's stated intent
    silently — a buyer sending ``["Bearer", "HMAC-SHA256"]`` with no credentials
    passed the credential precondition because only ``Bearer`` was inspected, and
    was then never delivered to. That is the swallow this lane is named for.

    On the transports the model refuses the document first (``maxItems``), so
    these cases exercise the gate DIRECTLY — which is the surface that still
    accepts a dict, for ``from_stash`` and for legacy shapes. Without them the
    refusal is reachable but ungraded, and a future edit could quietly restore
    ``schemes[0]`` with a green suite.
    """

    def test_ingest_refuses_a_multi_scheme_registration_by_name(self):
        registration = {
            "url": "https://buyer.example.com/hook",
            "authentication": {"schemes": ["Bearer", "HMAC-SHA256"], "credentials": "s" * 32},
        }

        with pytest.raises(AdCPValidationError) as refusal:
            accept_push_notification_config(registration)

        assert refusal.value.field == "push_notification_config.authentication.schemes", (
            f"a multi-scheme registration must be refused BY NAME so the buyer can pick one; "
            f"got field={refusal.value.field!r}"
        )
        assert refusal.value.recovery == "correctable", (
            "the buyer can fix this by choosing a single scheme, so it is correctable"
        )

    def test_rehydration_refuses_the_same_document(self):
        """REVERSED BY OWNER DECISION — the split this class was named for is gone.

        It previously asserted that ingest refuses a multi-scheme registration while
        rehydration tolerates it, narrowing to schemes[0] so a stored row keeps
        delivering. Owner ruling for lane C4 — "Refuse — spec or nothing" — abolishes
        that asymmetry for THIS shape: the pinned schema allows exactly one scheme,
        so the block is invalid wherever it is read.

        The strict/tolerant distinction still exists, but its remaining subject is
        schema-invalidity OUTSIDE the authentication block (``token`` minLength 16 is
        the live example) — not inside it.
        """
        stashed = {
            "url": "https://buyer.example.com/hook",
            "authentication": {"schemes": ["Bearer", "HMAC-SHA256"], "credentials": "s" * 32},
        }

        with pytest.raises(AdCPValidationError) as refusal:
            ValidatedWebhookRegistration.from_stash(stashed)

        assert refusal.value.field.startswith("push_notification_config.authentication"), (
            f"the refusal must name the authentication block (or a field inside it); got {refusal.value.field!r}"
        )


class TestStoredRegistrationsKeepDelivering:
    """What a STORED registration does at rehydration, after owner ruling #2.

    This class originally asserted that every legacy shape keeps delivering, because
    converting "delivered" into "never delivered at all" for a row whose owner
    cannot be asked to fix it is the failure this epic exists to remove.

    THREE of its rows were REVERSED BY DECISION, not lost. The owner ruled: "Refuse
    — spec or nothing", and separately "3.1.1 does not allow any basic
    authentication ... Bearer is enough". A stored authentication block that does not
    satisfy the pinned schema is not a delivery we should be making, so it refuses
    and its owner re-registers. No migration is supplied; the refusal IS the
    behaviour. The rows are rewritten to assert that refusal, with the reason inline,
    so the record shows an obligation reversed deliberately rather than quietly
    dropped.

    TWO MORE were reversed the same way when the owner collapsed the vocabulary to
    one — the pinned ``AuthenticationScheme``, case-sensitive, and nothing else:
      - "lowercase scheme" (``hmac-sha256``): previously folded to the pinned member
        before the enum saw it, on the reasoning that it IS the same scheme spelled
        differently (RFC 7235 §2.1). Folding is gone; the row refuses.
      - "unrecognised scheme" (``Basic``): previously survived by an explicit
        widening kept so existing buyers kept working. The widening is gone.
    Both are now MIGRATED rather than tolerated: the operator re-registers with a
    scheme the spec defines, and until then the row does not deliver. The tolerance
    argument is self-perpetuating — it can never expire, because rows always exist —
    and what it produced was three spellings of one fact across three senders.

    ONE row still delivers, and the reason is worth stating because it looks like an
    exception to the rule above:
      - "short token field": IS schema-invalid ("abc" against ``token`` minLength 16),
        and survives because the field sits OUTSIDE the authentication block, which is
        where the refusal rule is scoped. Without this sentence the next reader sees
        "we refuse schema-invalid blocks, except this one".
    """

    @pytest.mark.parametrize(
        ("label", "authentication", "extra", "expected_scheme"),
        [
            ("short token field", {"schemes": ["Bearer"], "credentials": "s" * 32}, {"token": "abc"}, "Bearer"),
        ],
    )
    def test_a_conforming_legacy_row_still_rehydrates(self, label, authentication, extra, expected_scheme):
        stashed = {"url": "https://buyer.example.com/hook", "authentication": authentication, **extra}

        rehydrated = ValidatedWebhookRegistration.from_stash(stashed)

        assert rehydrated.authentication_type == expected_scheme, (
            f"{label}: a stored registration that delivered as {expected_scheme} now resolves to "
            f"{rehydrated.authentication_type!r} — this row stops being delivered to, with no "
            f"buyer left to fix it"
        )

    @pytest.mark.parametrize(
        ("label", "authentication"),
        [
            ("sub-32 credential", {"schemes": ["Bearer"], "credentials": "short"}),
            ("empty schemes list", {"schemes": [], "credentials": "x"}),
            ("scheme without credential", {"schemes": ["Bearer"]}),
            # Reversed by the one-vocabulary ruling: no case folding, no widening.
            ("lowercase scheme", {"schemes": ["hmac-sha256"], "credentials": "s" * 32}),
            ("unrecognised scheme", {"schemes": ["Basic"], "credentials": "s" * 32}),
        ],
    )
    def test_a_non_conforming_legacy_row_now_refuses(self, label, authentication):
        """REVERSED BY OWNER DECISION — see the class docstring.

        Each of these delivered before Epic D lane C4. They refuse now because the
        stored authentication block does not satisfy the pinned schema, and the owner
        ruled that such a row "is not a delivery we should be making". The refusal is
        fail-closed and names the scheme, which is the only surface the decision has:
        there is no migration and no durable outcome record on this path.
        """
        stashed = {"url": "https://buyer.example.com/hook", "authentication": authentication}

        with pytest.raises(AdCPValidationError) as refusal:
            ValidatedWebhookRegistration.from_stash(stashed)

        assert refusal.value.field.startswith("push_notification_config.authentication"), (
            f"{label}: the refusal must name the authentication block (or a field inside it); "
            f"got {refusal.value.field!r}"
        )

    def test_the_one_shape_that_never_delivered_still_refuses(self):
        """HMAC with no secret is the exception, and must stay one.

        It resolved to HmacSecretMissing before this package existed, i.e. it never
        delivered. Tolerating it would mean delivering unsigned to a receiver that
        will reject every unsigned request — the opposite failure.
        """
        stashed = {
            "url": "https://buyer.example.com/hook",
            "authentication": {"schemes": ["HMAC-SHA256"]},
        }

        with pytest.raises(AdCPValidationError) as refusal:
            ValidatedWebhookRegistration.from_stash(stashed)

        assert refusal.value.field == "push_notification_config.authentication.credentials", (
            f"the refusal must name the missing secret; got {refusal.value.field!r}"
        )
