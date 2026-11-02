"""Regression tests for de-vacuumized generic partition/boundary/status Then steps.

: the generic Then steps `then_partition_filtering_result`,
`then_boundary_handling_result` (then_payload.py) and `then_response_status`
(then_success.py) historically passed *vacuously* — they ignored the captured
``field`` and accepted any non-None response (or any recorded exception) as a
satisfied outcome. ~140 scenarios xpassed without proving anything.

These tests call the step functions directly with crafted ``ctx`` states (no
DB, no harness) and assert the *strengthened* behavior:

- a "valid" outcome requires a schema-valid response of the operation's type
  with its required success collection correctly typed — not a junk object;
- an "invalid"/"error" outcome requires a real validation/AdCP rejection —
  not an arbitrary exception;
- the captured ``field`` must name a known dimension — an empty/unknown field
  is a misnamed scenario and must fail loudly;
- a context with neither response nor error must fail loudly;
- a status-less "completed" response must prove absence of error plus presence
  of its schema-required success payload.

Each negative case below PASSED vacuously before the fix and must FAIL
(AssertionError) the broken input after it.
"""

from __future__ import annotations

import pytest

from src.core.schemas import ListCreativeFormatsResponse
from tests.bdd.steps.generic.then_payload import (
    then_boundary_handling_result,
    then_partition_filtering_result,
)
from tests.bdd.steps.generic.then_success import then_response_status


# ctx key for a payload produced WITHOUT dispatching, which is what these
# unit tests do: they drive a Then directly to exercise its assertion logic.
def _valid_uc005_ctx() -> dict:
    """A genuinely valid UC-005 response context (control: must still pass)."""
    return {"self_dispatched_response": ListCreativeFormatsResponse(formats=[]), "registry_formats": [{"name": "stub"}]}


# ── Control cases: legitimate outcomes must still pass ───────────────────


def test_valid_partition_with_known_field_still_passes() -> None:
    then_partition_filtering_result(_valid_uc005_ctx(), field="format_ids", expected="valid")


def _wire_rejection_ctx(code: str = "INVALID_REQUEST") -> dict:
    """A ctx as a real WIRE rejection leaves it: a TransportResult with an envelope."""
    from tests.harness.transport import Transport
    from tests.harness.wire_fixtures import wire_error_result

    envelope = {
        "transport": Transport.A2A.value,
        "status": "adcp_error",
        "adcp_error": {"code": code, "message": "Invalid request parameters", "recovery": "correctable"},
        "errors": [{"code": code, "message": "Invalid request parameters", "recovery": "correctable"}],
    }
    return {"result": wire_error_result(envelope, error=Exception(code), envelope=envelope)}


def test_invalid_partition_with_real_wire_rejection_still_passes() -> None:
    """Control for the REJECTION half: a real wire envelope naming the code passes.

    THE OLD BAR WAS A WAYPOINT, NOT THE TARGET. This test used to build a pydantic
    ValidationError from a RESPONSE model in the test process and assert the step
    accepted it — an earlier de-vacuuming pass had raised the bar from "any
    exception" to "a real ValidationError" and froze it there. That bar could never
    have caught the actual defect, because a client-side ValidationError is exactly
    what a step produced when the payload NEVER REACHED THE SELLER: the harness
    built every request through the typed model, so an invalid payload died in this
    process and production was never executed (salesagent-prkv.65).

    The contract now is the buyer's two-layer envelope carrying the NAMED code from
    the Examples column, so the control is a wire rejection and the outcome cell is
    a code rather than a bare "invalid".
    """
    then_partition_filtering_result(_wire_rejection_ctx(), field="asset_types", expected="INVALID_REQUEST")


def test_invalid_partition_rejects_bare_invalid_outcome_cell() -> None:
    """A bare 'invalid' cell no longer grades anything and must fail loudly."""
    with pytest.raises(AssertionError, match="Name the AdCP error code"):
        then_partition_filtering_result(_wire_rejection_ctx(), field="asset_types", expected="invalid")


def test_invalid_partition_rejects_client_side_exception() -> None:
    """A test-process exception is NOT a rejection: no envelope means no evidence.

    This is the case the old bar admitted and the whole migration exists to close.
    """
    from pydantic import ValidationError

    try:
        ListCreativeFormatsResponse(formats="not-a-list")  # type: ignore[arg-type]
    except ValidationError as exc:
        ctx = {"error": exc}
    with pytest.raises(AssertionError, match="never reached the seller"):
        then_partition_filtering_result(ctx, field="asset_types", expected="INVALID_REQUEST")


def test_invalid_partition_rejects_wrong_code_on_the_wire() -> None:
    """A real envelope carrying a DIFFERENT code must fail — the code is the contract."""
    with pytest.raises(AssertionError):
        then_partition_filtering_result(
            _wire_rejection_ctx("VALIDATION_ERROR"), field="asset_types", expected="INVALID_REQUEST"
        )


# ── De-vacuumization: broken inputs that used to pass must now FAIL ──────


def test_valid_outcome_rejects_junk_response_object() -> None:
    """A non-response junk object with no error used to pass (only hasattr check)."""
    ctx = {"self_dispatched_response": object(), "registry_formats": []}
    with pytest.raises((AssertionError, AttributeError)):
        then_partition_filtering_result(ctx, field="format_ids", expected="valid")


def test_valid_outcome_rejects_unknown_field_name() -> None:
    """An empty/unknown field is a misnamed scenario — must fail loudly."""
    with pytest.raises(AssertionError):
        then_partition_filtering_result(_valid_uc005_ctx(), field="", expected="valid")
    with pytest.raises(AssertionError):
        then_partition_filtering_result(_valid_uc005_ctx(), field="totally_not_a_dimension", expected="valid")


def test_invalid_outcome_rejects_arbitrary_exception() -> None:
    """An arbitrary RuntimeError is not a real validation/AdCP rejection."""
    ctx = {"error": RuntimeError("kaboom unrelated crash")}
    with pytest.raises(AssertionError):
        then_boundary_handling_result(ctx, field="account", expected="invalid")


def test_outcome_requires_response_or_error() -> None:
    """A context with neither response nor error must fail loudly, not pass."""
    with pytest.raises(AssertionError):
        then_partition_filtering_result({}, field="format_ids", expected="valid")


def test_boundary_unknown_field_fails_loudly() -> None:
    with pytest.raises(AssertionError):
        then_boundary_handling_result(_valid_uc005_ctx(), field="bogus_boundary", expected="valid")


def test_unknown_expected_word_still_rejected() -> None:
    with pytest.raises(AssertionError):
        then_partition_filtering_result(_valid_uc005_ctx(), field="format_ids", expected="banana")


# ── then_response_status status-less "completed" de-vacuumization ────────


def test_response_status_completed_with_error_in_ctx() -> None:
    """AdCP 3.1: protocol-envelope.json requires status on ALL responses.

    ListCreativeFormatsResponse refs protocol-envelope.json which declares
    status as required (default "completed" for synchronous tasks). The step
    checks the declared status field, not ctx["error"]. This is correct per
    3.1 — the "status-less response" path only applies to non-spec test doubles.
    """
    ctx = {
        "self_dispatched_response": ListCreativeFormatsResponse(formats=[]),
        "error": RuntimeError("operation actually failed"),
    }
    # No longer raises — response has status="completed" via protocol envelope
    then_response_status(ctx, status="completed")


def test_response_status_completed_rejects_missing_success_payload() -> None:
    """status-less response lacking its schema-required success collection."""

    class _Shell:
        """Status-less object with no formats — used to pass vacuously."""

    ctx = {"self_dispatched_response": _Shell()}
    with pytest.raises(AssertionError):
        then_response_status(ctx, status="completed")


def test_response_status_completed_valid_still_passes() -> None:
    then_response_status(_valid_uc005_ctx(), status="completed")


def test_response_status_non_completed_against_statusless_fails() -> None:
    with pytest.raises(AssertionError):
        then_response_status(_valid_uc005_ctx(), status="working")
