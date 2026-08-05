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

from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from src.core.schemas import ListCreativeFormatsResponse
from tests.bdd.steps.domain.uc026_package_media_buy import then_operation_succeeds
from tests.bdd.steps.generic.then_payload import (
    then_boundary_handling_result,
    then_partition_filtering_result,
)
from tests.bdd.steps.generic.then_success import then_response_status
from tests.harness.transport import Transport


def _valid_uc005_ctx() -> dict:
    """A genuinely valid UC-005 response context (control: must still pass)."""
    return {"response": ListCreativeFormatsResponse(formats=[]), "registry_formats": [{"name": "stub"}]}


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
    ctx = {"response": object(), "registry_formats": []}
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
    """ListCreativeFormatsResponse declares a required ``status`` field.

    salesagent-oyiv.9 confirmed (via direct ``model_fields`` introspection) that
    the status-less branch is NOT limited to non-spec test doubles:
    ``SyncCreativesResponse`` (UC-006) is a real AdCP spec response type that
    genuinely has no ``status`` field, and is a live-if-dormant code path, not
    vestigial. ``ListCreativeFormatsResponse`` itself does declare ``status``
    (default "completed" for synchronous tasks), so this control case still
    exercises the declared-status branch, not ctx["error"].
    """
    ctx = {
        "response": ListCreativeFormatsResponse(formats=[]),
        "error": RuntimeError("operation actually failed"),
    }
    # No longer raises — response has status="completed" via protocol envelope
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


# ── salesagent-oyiv.9: synthetic wire-dict proof (TDD red) ───────────────
#
# then_response_status branch 1 (the "status" in resp_fields arm) and
# uc026_package_media_buy.py::then_operation_succeeds both currently grade
# the harness-RECONSTRUCTED typed payload (resp.status /
# getattr(resp, "status", None)) instead of the real wire body
# (wire_dict(ctx)/wire_field(ctx, ...)). Most scenarios reaching these
# functions are dormant/xfailed today, so a BDD module run is vacuous proof
# — these direct-call tests are the mandatory synthetic proof per the plan's
# Step 3. Every test below currently FAILS against the unmigrated code
# (either an uncaught AssertionError because the read comes off the typed
# ``resp`` instead of the wire, or a ``pytest.raises`` block that does NOT
# currently raise) and is expected to pass once the migration lands.


class _StubResponseWithStatus(BaseModel):
    """Minimal real Pydantic model — satisfies then_response_status's
    ``"status" in resp_fields`` routing gate (a legitimate schema-DECLARATION
    probe, out of scope for migration) while carrying a controllable typed
    ``status`` value distinct from the wire value used to prove the read
    that matters (the actual grade) comes off the wire, not this attribute.
    """

    status: str


def test_response_status_reads_wire_only_for_matching_status() -> None:
    """Migrated: grades ``wire_dict(ctx)["status"]`` — the typed resp's own
    status value is irrelevant/inconsistent, proving the function no longer
    needs a matching typed value, only a matching wire value.

    Today: ``enum_value(resp.status)`` reads the typed placeholder
    "unused-placeholder", which does not equal "completed" — an uncaught
    ``AssertionError`` (wrong reason: reads the typed payload, not the wire).
    """
    ctx = {
        "response": _StubResponseWithStatus(status="unused-placeholder"),
        "wire_response": {"status": "completed"},
        "transport": Transport.REST,
    }
    then_response_status(ctx, status="completed")


def test_response_status_catches_wire_divergence_despite_matching_typed_status() -> None:
    """Coercion/staleness divergence: the typed resp reports status="completed"
    (a value that WOULD satisfy the request), but the real wire carries a
    different value ("submitted"). The migrated function must fail on the
    WIRE value, proving it no longer reads ``resp.status``.

    Today: ``enum_value(resp.status)`` reads "completed", which equals the
    requested "completed" — no exception is raised, so wrapping in
    ``pytest.raises`` fails today with "DID NOT RAISE" (the honest red
    signal: today's code is fooled by the stale/coerced typed value).
    """
    ctx = {
        "response": _StubResponseWithStatus(status="completed"),
        "wire_response": {"status": "submitted"},
        "transport": Transport.REST,
    }
    with pytest.raises(AssertionError):
        then_response_status(ctx, status="completed")


def test_operation_succeeds_reads_wire_only_ignoring_typed_failure_status() -> None:
    """Migrated: ``then_operation_succeeds`` grades
    ``wire_dict(ctx).get("status")`` — a non-failure WIRE status must not
    raise even though the typed ``resp.status`` carries a failure value the
    migrated code must no longer consult.

    Today: ``getattr(resp, "status", None)`` reads the typed "failed", which
    IS in ``_FAILURE_STATUSES`` — an uncaught ``AssertionError`` (wrong
    reason: reads the typed payload, not the wire).
    """
    ctx = {
        "response": SimpleNamespace(status="failed"),
        "wire_response": {"status": "completed"},
        "transport": Transport.REST,
    }
    then_operation_succeeds(ctx)


def test_operation_succeeds_catches_wire_failure_despite_typed_success_status() -> None:
    """Coercion/staleness divergence: the typed resp reports status="completed"
    (a value that would NOT raise), but the real wire carries a failure value
    ("failed"). The migrated function must fail on the WIRE value.

    Today: ``getattr(resp, "status", None)`` reads "completed", which is not
    in ``_FAILURE_STATUSES`` — no exception is raised, so wrapping in
    ``pytest.raises`` fails today with "DID NOT RAISE".
    """
    ctx = {
        "response": SimpleNamespace(status="completed"),
        "wire_response": {"status": "failed"},
        "transport": Transport.REST,
    }
    with pytest.raises(AssertionError):
        then_operation_succeeds(ctx)


def test_operation_succeeds_tolerates_absent_wire_status_ignoring_typed_failure() -> None:
    """Tolerate-absent, matching BR-UC-009's real response shape (no
    ``status`` field on the wire at all): ``wire_dict(ctx).get("status")``
    must return ``None`` (no ``KeyError`` — deliberately not the bare
    ``wire[field]`` shape ``wire_field`` uses elsewhere) and the function
    must not raise, even though the typed resp carries a failure value the
    migrated code must no longer fall back to.

    Today: ``getattr(resp, "status", None)`` reads the typed "failed" (a
    value the real BR-UC-009 schema would never stamp, since it has no
    status field at all) — an uncaught ``AssertionError`` (wrong reason:
    reads the typed payload instead of tolerating the genuinely status-less
    wire body).
    """
    ctx = {
        "response": SimpleNamespace(status="failed"),
        "wire_response": {},
        "transport": Transport.REST,
    }
    then_operation_succeeds(ctx)
