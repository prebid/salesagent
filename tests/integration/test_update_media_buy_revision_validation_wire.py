"""The `revision` contract, graded on the real wire of all three transports.

Both directions are protocol contracts, so both are read off the actual wire rather
than off a re-serialized model: what each transport ACCEPTS, and what each EMITS.

One rule decides what a `revision` value may be, so MCP, REST and A2A must reject
and accept exactly the same inputs. The pinned update-media-buy-request.json types
the field as {"type": "integer", "minimum": 1} -- and under draft-07 `integer`
admits any number with a zero fractional part, so 2.0 is VALID and must be accepted
on every transport (A2A carries JSON numbers as doubles).

The present-but-null case is the one that can only be decided at the boundary: the
schema gives `revision` no null arm, so an explicit null is a violation, but further
in it is indistinguishable from an omitted key. Each boundary therefore runs the
shared gate itself, and each is graded here.

Dispatch goes through MediaBuyDualEnv's RAW flat-kwargs form on purpose. Building an
UpdateMediaBuyRequest in the test process would raise inside the test for exactly the
values under test, so the rejection would never reach a wire to be graded.

Spec grounding (CLAUDE.md Spec-Grounding Gate): a `revision` value the pinned schema
does not admit is a schema-value violation -> VALIDATION_ERROR, the same class and code
as a malformed idempotency_key on this model (one model, one violation class, one code).
The pin is dist/schemas/3.1.1/media-buy/update-media-buy-request.json (revision:
{"type": "integer", "minimum": 1}). The 3.1.1 conformance storyboard does NOT grade
revision: `grep -rn "revision" dist/compliance/3.1.1 --include=*.yaml` returns only
narrative and a sample body, so the code choice is anchored to the value/range taxonomy,
not to a graded step (ungraded).
"""

import pytest

from tests.harness.media_buy_dual import MediaBuyDualEnv
from tests.harness.transport import WIRE_TRANSPORTS, Transport

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

_MEDIA_BUY_ID = "mb_revision_wire"

#: A nontrivial buyer-supplied context. The two-layer error envelope MUST echo it on
#: a CONFLICT so an async buyer can correlate the rejection back to its own request
#: (get_media_buys keys off these fields). ContextObject is free-form
#: (additionalProperties), so any populated dict exercises the echo.
_BUYER_CONTEXT = {"internal_campaign_id": "camp-xyz", "trace": "t-123"}

#: The real wire transports come from the harness (WIRE_TRANSPORTS): IMPL is excluded --
#: it has no wire, and the boundary gate under test is precisely what IMPL does not run.

#: Values the pinned schema does not admit. Each must be refused identically everywhere.
REJECTED_VALUES = [
    pytest.param("7", id="numeric-string"),
    pytest.param(True, id="bool-true"),
    pytest.param(1.5, id="fractional"),
    pytest.param(0, id="below-minimum"),
    pytest.param(-1, id="negative"),
]


def _update(env: MediaBuyDualEnv, transport: Transport, **kwargs):
    """Send a flat update body over *transport*'s real wire."""
    return env.call_via(transport, media_buy_id=_MEDIA_BUY_ID, paused=True, **kwargs)


@pytest.mark.parametrize("transport", WIRE_TRANSPORTS)
class TestRevisionValueContractOnEveryWire:
    @pytest.mark.parametrize("bad_value", REJECTED_VALUES)
    def test_rejects_a_schema_violation(self, integration_db, transport, bad_value):
        with MediaBuyDualEnv() as env:
            env.seed_existing_media_buy(_MEDIA_BUY_ID)
            result = _update(env, transport, revision=bad_value)
        assert result.is_error, (
            f"{transport} accepted revision={bad_value!r}, which the pinned schema forbids; payload={result.payload!r}"
        )
        # Pin the CODE, not merely "something went wrong": the buy exists and the
        # request is otherwise valid, so a schema-value rejection (VALIDATION_ERROR) is
        # the only reason this may fail. Any other code means the gate did not run.
        # recovery is not hand-passed: assert_wire_error defaults it to the pinned enum's
        # classification (pin-wins), so the test does not restate a pinned fact.
        result.assert_wire_error("VALIDATION_ERROR")

    def test_a_malformed_revision_echoes_buyer_context(self, integration_db, transport):
        """A malformed-revision rejection must echo the buyer's context on every wire.

        An async buyer receives the rejection out-of-band and needs its own context back to
        correlate it with the request it sent -- the same correlation the CONFLICT path
        already gives. The context is threaded from the raw payload into the
        malformed-revision VALIDATION_ERROR (``_validate_revision``) and lands at the
        two-layer envelope's top level; dropping that pass-through reddens this test.
        """
        with MediaBuyDualEnv() as env:
            env.seed_existing_media_buy(_MEDIA_BUY_ID)
            # 0 is below the pinned minimum (a schema-value violation), so the gate rejects
            # before any concurrency comparison — the malformed-value path under test.
            result = _update(env, transport, revision=0, context=dict(_BUYER_CONTEXT))
        assert result.is_error, (
            f"{transport} accepted revision=0, which the pinned schema forbids; payload={result.payload!r}"
        )
        result.assert_wire_error("VALIDATION_ERROR")
        echoed = result.wire_error_envelope.get("context")
        assert echoed == _BUYER_CONTEXT, (
            f"{transport} did not echo the buyer's context on the malformed-revision envelope: "
            f"got {echoed!r}, expected {_BUYER_CONTEXT!r}"
        )

    def test_rejects_present_but_null(self, integration_db, transport):
        """An explicit null is a schema violation, not "no token supplied".

        Reading it as absent would hand the buyer a 200 on an update whose
        concurrency check never ran -- the exact silent failure this gate exists
        to prevent. Only the boundary can still tell null from omitted.
        """
        with MediaBuyDualEnv() as env:
            env.seed_existing_media_buy(_MEDIA_BUY_ID)
            result = _update(env, transport, revision=None)
        assert result.is_error, (
            f"{transport} read an explicitly-supplied null revision as 'absent' and "
            f"processed the update anyway; payload={result.payload!r}"
        )
        result.assert_wire_error("VALIDATION_ERROR")

    def test_uses_a_whole_number_float_as_the_token(self, integration_db, transport):
        """2.0 is schema-valid under draft-07 `integer` AND must be USED as the token.

        A2A delivers integers as doubles, so refusing this form would refuse a
        conformant buyer on one transport and accept it on the others -- but merely
        accepting it is not enough: a boundary that coerced the float to None (dropping
        the token) would also produce "no error" on a matching value, so accepting a
        MATCHING float proves nothing about coercion. Seed the row at 2 and send a stale
        whole-number float 1.0: the only way this yields CONFLICT naming expected 1 /
        current 2 is if 1.0 was accepted, coerced to the int 1, and compared as the token.
        A float->None mutation drops the token, the update succeeds, and this reddens.
        """
        with MediaBuyDualEnv() as env:
            env.seed_existing_media_buy(_MEDIA_BUY_ID, revision=2)
            result = _update(env, transport, revision=1.0)
        assert result.is_error, (
            f"{transport} accepted the stale whole-number float 1.0 against a row at revision 2; "
            f"the float was not used as the concurrency token. payload={result.payload!r}"
        )
        result.assert_wire_error("CONFLICT", recovery="transient")
        envelope = result.wire_error_envelope
        for layer, payload in (("adcp_error", envelope["adcp_error"]), ("errors[0]", envelope["errors"][0])):
            details = payload.get("details")
            assert details is not None, f"{transport} {layer} carried no details"
            assert details["resource_id"] == _MEDIA_BUY_ID
            assert_wire_number(details["expected_version"], 1, transport, what=f"{layer}.expected_version")
            assert_wire_number(details["current_version"], 2, transport, what=f"{layer}.current_version")

    def test_accepts_a_plain_integer(self, integration_db, transport):
        with MediaBuyDualEnv() as env:
            env.seed_existing_media_buy(_MEDIA_BUY_ID)
            result = _update(env, transport, revision=1)
        assert not result.is_error, (
            f"{transport} rejected a matching integer token: {result.wire_error_envelope or result.error!r}"
        )

    def test_omitting_revision_is_still_accepted(self, integration_db, transport):
        """revision is optional; the gate must not turn absence into a rejection."""
        with MediaBuyDualEnv() as env:
            env.seed_existing_media_buy(_MEDIA_BUY_ID)
            result = _update(env, transport)
        assert not result.is_error, (
            f"{transport} rejected an update that supplied no revision at all: "
            f"{result.wire_error_envelope or result.error!r}"
        )


#: How each transport renders a JSON number on the wire.
#:
#: A2A carries its payload through a protobuf Struct, whose only numeric type is
#: `double` -- so it emits 2.0 where MCP and REST emit 2. BOTH ARE CONFORMANT: draft-07
#: `integer` admits any number with a zero fractional part, and the pinned
#: error-details/conflict.json types the version fields as ["number", "string"].
#:
#: This table exists so the difference is PINNED rather than invisible. `2 == 2.0` is
#: True in Python, so an equality-only assertion cannot see a transport start or stop
#: forking. Do NOT "fix" A2A to emit an int to make this table uniform -- normalising a
#: schema-valid representation is a separate decision, not a bug fix.
WIRE_NUMBER_TYPE = {Transport.MCP: int, Transport.REST: int, Transport.A2A: float}


def assert_wire_number(value, expected: int, transport: Transport, *, what: str) -> None:
    """Assert *value* is `expected` AND is rendered in *transport*'s documented form."""
    assert value == expected, f"{transport} emitted {what}={value!r}, expected {expected}"
    assert type(value) is WIRE_NUMBER_TYPE[transport], (
        f"{transport} emitted {what} as {type(value).__name__} ({value!r}); "
        f"this transport is documented to render numbers as "
        f"{WIRE_NUMBER_TYPE[transport].__name__}. If the change is deliberate, update "
        f"WIRE_NUMBER_TYPE and say why -- do not delete the type check, which is the "
        f"only thing that can see this (2 == 2.0)."
    )


@pytest.mark.parametrize("transport", WIRE_TRANSPORTS)
class TestRevisionEmittedOnEveryWire:
    """The response side is a protocol contract and is graded on the wire bytes."""

    def test_successful_update_emits_the_advanced_revision(self, integration_db, transport):
        """The buyer's next token comes off this field, so it must be the NEW revision.

        Read from the real serialized body (wire_response), not from re-serializing
        the response model -- a model that serializes correctly in-process proves
        nothing about what crossed the wire.
        """
        with MediaBuyDualEnv() as env:
            env.seed_existing_media_buy(_MEDIA_BUY_ID)
            result = _update(env, transport, revision=1)

        assert not result.is_error, result.wire_error_envelope
        wire = result.require_wire()
        assert "revision" in wire, (
            f"{transport} omitted `revision` from the success body; the buyer has no token "
            f"to send with its next update. Got keys: {sorted(wire)}"
        )
        # Seeded at 1, honoured once by the compare-and-set -> 2.
        assert_wire_number(wire["revision"], 2, transport, what="revision")

    def test_conflict_emits_both_versions_in_details(self, integration_db, transport):
        """The CONFLICT must name the pair, on every transport -- not just at the impl.

        Without both versions the buyer is told only that it lost, not what to send
        next, which is the whole point of the conflict details shape.
        """
        with MediaBuyDualEnv() as env:
            env.seed_existing_media_buy(_MEDIA_BUY_ID)
            # Move the revision to 2 so a token of 99 is unambiguously stale.
            _update(env, transport, revision=1)
            result = _update(env, transport, revision=99)

        assert result.is_error, f"{transport} accepted a stale revision token"
        result.assert_wire_error("CONFLICT", recovery="transient")

        envelope = result.wire_error_envelope
        # The two-layer envelope must carry details in BOTH layers, not just one.
        for layer, payload in (("adcp_error", envelope["adcp_error"]), ("errors[0]", envelope["errors"][0])):
            details = payload.get("details")
            assert details is not None, f"{transport} {layer} carried no details"
            assert details["resource_id"] == _MEDIA_BUY_ID
            assert_wire_number(details["expected_version"], 99, transport, what=f"{layer}.expected_version")
            assert_wire_number(details["current_version"], 2, transport, what=f"{layer}.current_version")

    def test_conflict_echoes_buyer_context(self, integration_db, transport):
        """A stale-token CONFLICT must echo the buyer's context on every wire.

        An async buyer receives the conflict out-of-band and needs its own context
        back to correlate the rejection with the request it sent. The context is
        threaded from ``req.context`` into the revision-conflict error and lands at
        the two-layer envelope's top level; reverting that pass-through drops the
        echo and reddens this test.
        """
        with MediaBuyDualEnv() as env:
            env.seed_existing_media_buy(_MEDIA_BUY_ID)
            # Advance the revision to 2 so a token of 99 is unambiguously stale.
            _update(env, transport, revision=1)
            result = _update(env, transport, revision=99, context=dict(_BUYER_CONTEXT))

        assert result.is_error, f"{transport} accepted a stale revision token"
        result.assert_wire_error("CONFLICT", recovery="transient")

        echoed = result.wire_error_envelope.get("context")
        assert echoed == _BUYER_CONTEXT, (
            f"{transport} did not echo the buyer's context on the CONFLICT envelope: "
            f"got {echoed!r}, expected {_BUYER_CONTEXT!r}"
        )
