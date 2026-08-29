"""BDD step definitions for UC-003 storyboard scenarios wired onto AdCPTestClient.

Demonstrator: dispatches through the transport-generic
``AdCPTestClient`` (``tests/harness/client.py``) via ``dispatch_via_client``
instead of ``MediaBuyDualEnv``/``dispatch_request``. Additive only — see
``tests/bdd/conftest.py``'s ``_UC003_STORYBOARD_GENERIC_CLIENT`` branch.

"""

from __future__ import annotations

from uuid import uuid4

from pytest_bdd import given, then, when

from tests.bdd.steps._outcome_helpers import wire_error_dict
from tests.bdd.steps.generic._dispatch import dispatch_via_client
from tests.bdd.steps.generic.then_error import _wire_result
from tests.harness.transport import DERIVED_STATUS_ADCP_ERROR, DERIVED_STATUS_TRANSPORT_FAULT


@given("the buyer fabricates a media_buy_id that does not exist in the seller catalog")
def given_fabricated_nonexistent_media_buy_id(ctx: dict) -> None:
    """Stash a guaranteed-nonexistent media_buy_id — nothing to seed against."""
    ctx["fabricated_media_buy_id"] = f"mb_does_not_exist_{uuid4()}"


@when("the Buyer Agent sends update_media_buy with the unknown media_buy_id and paused true")
def when_update_media_buy_with_unknown_id(ctx: dict) -> None:
    """Dispatch update_media_buy for the fabricated id through AdCPTestClient.

    Generates and stashes a correlation_id under the generic ctx["correlation_id"]
    key (not scenario-specific) so the dormant sibling scenario
    T-UC-003-storyboard-package-not-found, which reuses the correlation_id-echo
    Then step below, can graduate later without a rewrite.
    """
    correlation_id = str(uuid4())
    ctx["correlation_id"] = correlation_id
    payload = {
        "media_buy_id": ctx["fabricated_media_buy_id"],
        "paused": True,
        "context": {"correlation_id": correlation_id},
    }
    dispatch_via_client(ctx, "update_media_buy", payload)


@when("the Buyer Agent sends update_media_buy with canceled true on the already-canceled buy")
def when_update_media_buy_recancel(ctx: dict) -> None:
    """Dispatch the second cancel of an already-canceled buy, on the wire.

    Sends the buyer's literal payload — ``canceled: true`` included — through
    ``AdCPTestClient`` so the request-field normalization seam is what decides
    whether ``canceled`` is honored or refused.
    Deliberately NOT ``dispatch_request`` on ``MediaBuyDualEnv``: that env's
    ``_flatten_update_request`` pops ``canceled``/``cancellation_reason``
    (``_WRAPPER_UNSUPPORTED_FIELDS``) before the wire, so the scenario would
    grade the harness's own accommodation of the bug instead of the seller.

    Stashes the correlation_id under the generic ``ctx["correlation_id"]`` key,
    the same contract the sibling storyboard When step uses, so the shared
    correlation-echo Then step below works unmodified.
    """
    correlation_id = str(uuid4())
    ctx["correlation_id"] = correlation_id
    media_buy = ctx["existing_media_buy"]
    assert media_buy is not None, (
        "No existing_media_buy in ctx — the re-cancel scenario needs the Background's "
        "seeded buy, mutated to canceled status by the Given step"
    )
    payload = {
        "media_buy_id": media_buy.media_buy_id,
        "canceled": True,
        "context": {"correlation_id": correlation_id},
    }
    dispatch_via_client(ctx, "update_media_buy", payload)


@then("the error recovery hint should indicate correctable")
def then_error_recovery_hint_correctable(ctx: dict) -> None:
    """Grade the recovery hint alone, through the harness's code-free grader.

    ``correctable`` is the only expectation crossing the boundary, so it is the
    value actually under test; the scenario's separate ``the error code should
    be "X"`` step pins the code on the wire. Deliberately NOT
    ``then_error_recovery``: that generic step reaches the same assertion via
    ``assert_wire_error(<code read out of the envelope>, recovery=...)``, whose
    code arm is then graded against the envelope under test and cannot fail —
    the exact pattern ``assert_wire_error``'s docstring forbids.
    """
    _wire_result(ctx).assert_wire_recovery("correctable")


@then("the response should echo the context.correlation_id unchanged")
def then_response_echoes_correlation_id_unchanged(ctx: dict) -> None:
    """Assert the wire envelope's top-level context.correlation_id matches the When step's stash.

    Reads the generic ctx["correlation_id"] key (see the When step above), not
    a scenario-specific one, so this step is reusable by any sibling scenario
    that generates and stashes a correlation_id the same way.
    """
    envelope = wire_error_dict(ctx)
    expected = ctx.get("correlation_id")
    assert expected is not None, "No correlation_id stashed on ctx — the When step must generate and stash one"
    actual = (envelope.get("context") or {}).get("correlation_id")
    assert actual == expected, f"Expected context.correlation_id={expected!r} echoed unchanged, got {actual!r}"


@then("the response should NOT be a 500 or non-AdCP error shape")
def then_response_not_500_or_non_adcp_shape(ctx: dict) -> None:
    """Assert the seller answered with a structured AdCP envelope, not a transport fault.

    Order matters, and is the whole point of this step's shape. The DERIVED
    status is read and asserted FIRST, before the envelope grader runs. Written
    the other way round the check was structurally unreachable: the guarded
    envelope read raises loudly when no wire envelope was captured, and "no wire
    envelope captured" is precisely the condition ``derive_error_status`` reports
    as ``transport_fault`` — so the fault could never be observed at the assertion
    point, on any transport or dispatch path. Reading the status first makes the
    fault observable, and makes THIS obligation (not the grader's generic
    missing-wire guard) the thing that reports it.

    The assertion is POSITIVE — ``status == adcp_error`` — not ``status !=
    transport_fault``. A negative assertion also passes when the status is
    ABSENT, which is how this half of the sentence graded nothing on mcp and a2a
    while looking like a real check. Requiring the derived value to be present
    and correct means a dispatch path that stops reporting it reddens here
    instead of going quiet.

    "Not a 500 or non-AdCP shape" is graded by the derived status rather
    than by a synthesized ``status_code``: inventing an
    integer for MCP/A2A and asserting it is != 500 would trade a silent no-op for
    a loud tautology. Each transport reports its own evidence instead — REST's
    real HTTP body, A2A's failed-Task artifact, MCP's ToolError. The REST
    ``status_code`` check is kept where it genuinely exists.

    The envelope SHAPE half delegates to ``result.assert_wire_is_adcp_envelope``,
    the harness's CODE-FREE shape grader, so a spec change to the envelope shape
    only needs updating in one place. This step has no expected code of its own
    (it is reused across scenarios with different codes), which is exactly why it
    must NOT hand ``assert_wire_error`` a code read back out of the envelope
    under test — that arm would compare the envelope against itself and could
    never fail (``assert_wire_error``'s own docstring forbids it). The code-free
    grader performs the same real checks: the two-layer invariant
    (``adcp_error.code == errors[0].code``), that the code is canonical (pinned
    ``error-code.json``), and that recovery matches the pinned classification.
    """
    result = ctx.get("result")
    assert result is not None, (
        "No TransportResult on ctx — the When step must dispatch through "
        "dispatch_request/dispatch_via_client so this step can read the transport's own evidence"
    )

    transport_envelope = getattr(result, "envelope", None) or {}
    status = transport_envelope.get("status")
    assert status == DERIVED_STATUS_ADCP_ERROR, (
        f"Expected the seller to answer with a structured AdCP error envelope "
        f"(status={DERIVED_STATUS_ADCP_ERROR!r}), got status={status!r} "
        f"(envelope={transport_envelope!r}). "
        + (
            "The transport reported a fault instead of an AdCP envelope"
            if status == DERIVED_STATUS_TRANSPORT_FAULT
            else "This dispatch path reports no derived status at all, so it grades nothing here"
        )
        + " — this is the 'not a 500 or non-AdCP error shape' obligation failing."
    )

    status_code = transport_envelope.get("status_code")
    if status_code is not None:
        assert status_code != 500, f"Expected a non-500 status, got {status_code}"

    result.assert_wire_is_adcp_envelope()
