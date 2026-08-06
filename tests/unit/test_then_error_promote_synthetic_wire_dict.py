"""Synthetic wire-dict proof for the error-promotion branch in generic/then_error.py.

Two @then steps promote "the first response error" into ``ctx["error"]`` so downstream
error-code/message steps can find it:

  1. ``then_operation_fails``            (@then("the operation should fail"))  — LIVE
  2. ``then_entire_sync_operation_fails`` (@then("the entire sync operation fails")) — dormant

Both currently resolve that error off the harness-reconstructed TYPED payload
(``ctx["response"].errors[0]``) and never look at the real wire body. Per
salesagent-oyiv.12 they must share one helper that GRADES THE WIRE
(``wire_dict(ctx)["errors"]`` — presence plus a non-empty ``code``) while still handing
the TYPED model forward on ``ctx["error"]``, because every downstream accessor in
then_error.py (``_assert_meaningful_error``, ``_get_error_code``, ``_get_error_message``,
``_get_error_dict``) duck-types on ``.code``/``.message``/``.suggestion``/``.recovery``.

Each test hand-builds ``ctx["wire_response"]`` plus a real-wire ``ctx["transport"]``
(``Transport.REST``, satisfying ``wire_dict``'s loud "wire_response missing" guard) and
diverges it from the typed payload, so a pass can only be explained by the function
reading the wire. Same synthetic-ctx pattern as
``test_bdd_wave_ab_synthetic_wire_dict.py`` / ``test_uc002_nfr_sla_synthetic_wire_dict.py``.

Deliberately NOT covered: the item-level ``wire_list(ctx, "catalogs")`` walk at the tail
of ``then_entire_sync_operation_fails``. That walk is unreachable by construction (no
sync_catalogs tool exists, no collected scenario binds BR-UC-023) and has no ground truth
to test against beyond the pinned SDK schema; the one test below that touches that
function supplies no catalogs on either side, so the walk is a no-op and stays ungraded.
"""

from __future__ import annotations

import pytest
from adcp.types import Error

from tests.bdd.steps.generic.then_error import (
    then_entire_sync_operation_fails,
    then_operation_fails,
)
from tests.harness.transport import Transport


class _StubErrorsResponse:
    """Success-shaped response carrying a typed ``errors`` list (the partial-success shape
    ``_dispatch.py`` puts on ``ctx["response"]``), and nothing else — no catalogs/results/items,
    so the item-level walk stays a no-op."""

    def __init__(self, errors: list[Error]) -> None:
        self.errors = errors


def _ctx(typed_errors: list[Error], wire_errors: list[dict]) -> dict:
    return {
        "response": _StubErrorsResponse(typed_errors),
        "wire_response": {"errors": wire_errors},
        "transport": Transport.REST,
    }


# ═══════════════════════════════════════════════════════════════════════
# then_operation_fails — the LIVE call site
# ═══════════════════════════════════════════════════════════════════════


def test_operation_fails_grades_wire_error_not_typed_error() -> None:
    """Divergence: the typed payload carries a perfectly good error, but the real wire's
    errors[0] carries an EMPTY code — a non-conformant envelope the buyer would receive.
    Grading the wire must fail here.

    Today: ``_assert_meaningful_error(resp.errors[0])`` inspects the typed model's valid
    "VALIDATION_ERROR" code and passes, oblivious to the malformed wire. pytest.raises
    therefore fails today (no exception raised) — the honest TDD-red signal.
    """
    ctx = _ctx(
        typed_errors=[Error(code="VALIDATION_ERROR", message="typed reconstruction looks fine")],
        wire_errors=[{"code": "", "message": "wire error with no code"}],
    )
    with pytest.raises(AssertionError):
        then_operation_fails(ctx)


def test_operation_fails_when_wire_carries_no_errors_despite_typed_errors() -> None:
    """Divergence: the typed payload carries an error but the real wire's errors[] is empty —
    the buyer received a body claiming nothing went wrong. "The operation should fail" must
    not pass on that.

    Today: the typed ``resp.errors`` is non-empty, so the step promotes it and returns
    successfully. pytest.raises fails today (no exception raised) — the honest TDD-red signal.
    """
    ctx = _ctx(
        typed_errors=[Error(code="VALIDATION_ERROR", message="only on the typed side")],
        wire_errors=[],
    )
    with pytest.raises(AssertionError):
        then_operation_fails(ctx)


def test_operation_fails_reports_wire_typed_divergence_when_reconstruction_is_empty() -> None:
    """Inverse divergence: the wire carries a well-formed error but the typed reconstruction
    lost it. That is a harness/serialization bug, and the failure message must say so rather
    than claim no error was recorded anywhere.

    Today: the step never reads the wire, sees an empty ``resp.errors``, and raises the generic
    "Expected the operation to fail but no error was recorded" — so matching on the divergence
    wording fails today. That message mismatch is the TDD-red signal.
    """
    ctx = _ctx(
        typed_errors=[],
        wire_errors=[{"code": "VALIDATION_ERROR", "message": "present on the wire only"}],
    )
    with pytest.raises(AssertionError, match="diverge"):
        then_operation_fails(ctx)


def test_operation_fails_promotes_a_typed_error_object_not_a_wire_dict() -> None:
    """Regression guard for the handoff contract: ``ctx["error"]`` is a slot downstream Then
    steps read through attribute-based accessors, so the promoted value must stay the TYPED
    model even though the GRADING now reads the wire. Promoting the wire dict instead would
    break ``_assert_meaningful_error`` / ``_get_error_code`` / ``_get_error_message`` /
    ``_get_error_dict``, which all duck-type on ``.code``/``.message``.

    Passes today (the step promotes the typed model) and must keep passing after the wire-
    grading migration — this is the guard, not the red.
    """
    typed_error = Error(code="VALIDATION_ERROR", message="budget must be positive")
    ctx = _ctx(
        typed_errors=[typed_error],
        wire_errors=[{"code": "VALIDATION_ERROR", "message": "budget must be positive"}],
    )

    then_operation_fails(ctx)

    promoted = ctx["error"]
    assert promoted is typed_error, f"expected the typed Error model to be promoted, got {promoted!r}"
    assert not isinstance(promoted, dict), f"ctx['error'] must not be a wire dict: {promoted!r}"
    assert promoted.code == "VALIDATION_ERROR"
    assert promoted.message == "budget must be positive"


# ═══════════════════════════════════════════════════════════════════════
# then_entire_sync_operation_fails — the second call site of the same helper
# ═══════════════════════════════════════════════════════════════════════


def test_entire_sync_operation_fails_grades_wire_error_not_typed_error() -> None:
    """The shared helper must apply at BOTH call sites: the same empty-code wire envelope that
    fails "the operation should fail" must fail "the entire sync operation fails" too.
    Splitting the wire grading across only one caller is the duplication this extraction exists
    to remove.

    Today: the sibling promotes ``resp.errors[0]`` off the typed payload and passes on the
    valid typed code. pytest.raises fails today (no exception raised) — the TDD-red signal.
    No catalogs are supplied on either side, so the item-level walk is a no-op here.
    """
    ctx = _ctx(
        typed_errors=[Error(code="VALIDATION_ERROR", message="typed reconstruction looks fine")],
        wire_errors=[{"code": "", "message": "wire error with no code"}],
    )
    with pytest.raises(AssertionError):
        then_entire_sync_operation_fails(ctx)
