"""Regression tests for de-vacuumized generic partition/boundary/status Then steps.

salesagent-6oq: the generic Then steps `then_partition_filtering_result`,
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


def _valid_uc005_ctx() -> dict:
    """A genuinely valid UC-005 response context (control: must still pass)."""
    return {"response": ListCreativeFormatsResponse(formats=[])}


# ── Control cases: legitimate outcomes must still pass ───────────────────


def test_valid_partition_with_known_field_still_passes() -> None:
    then_partition_filtering_result(_valid_uc005_ctx(), field="format_ids", expected="valid")


def test_invalid_partition_with_real_rejection_still_passes() -> None:
    from pydantic import ValidationError

    try:
        ListCreativeFormatsResponse(formats="not-a-list")  # type: ignore[arg-type]
    except ValidationError as exc:
        ctx = {"error": exc}
    then_partition_filtering_result(ctx, field="asset_types", expected="invalid")


# ── De-vacuumization: broken inputs that used to pass must now FAIL ──────


def test_valid_outcome_rejects_junk_response_object() -> None:
    """A non-response junk object with no error used to pass (only hasattr check)."""
    ctx = {"response": object()}
    with pytest.raises(AssertionError):
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


def test_response_status_completed_rejects_error_in_ctx() -> None:
    """status-less + error present used to pass on status=='completed'."""
    ctx = {
        "response": ListCreativeFormatsResponse(formats=[]),
        "error": RuntimeError("operation actually failed"),
    }
    with pytest.raises(AssertionError):
        then_response_status(ctx, status="completed")


def test_response_status_completed_rejects_missing_success_payload() -> None:
    """status-less response lacking its schema-required success collection."""

    class _Shell:
        """Status-less object with no formats — used to pass vacuously."""

    ctx = {"response": _Shell()}
    with pytest.raises(AssertionError):
        then_response_status(ctx, status="completed")


def test_response_status_completed_valid_still_passes() -> None:
    then_response_status(_valid_uc005_ctx(), status="completed")


def test_response_status_non_completed_against_statusless_fails() -> None:
    with pytest.raises(AssertionError):
        then_response_status(_valid_uc005_ctx(), status="working")
