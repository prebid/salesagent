"""Then steps for create_media_buy response assertions.

THIS MODULE IS NOT REGISTERED in tests/bdd/conftest.py's ``pytest_plugins``, so none of
these definitions is reachable: pytest-bdd resolves a step to a FIXTURE, and a module that
is neither a registered plugin nor imported by a test module contributes none. Registering
it was measured and rejected -- it steals 127 already-bound step instances across 5 modules
(salesagent-uokuq). Its remaining steps are candidates for PROMOTION into a registered
module when a scenario needs them, one at a time, which is how the two UC-002 package Thens
left here (see salesagent-9p7oe.4). The ``"errors" array`` pair moved to ``then_error.py``
and ``the package budget should be persisted as`` to ``uc003_update_media_buy.py`` the
same way, when UC-003 scenarios needed them; the UC-002 persistence and success-shape
Thens moved to ``uc002_invariants.py`` when the UC-002 scenarios were wired.

Eleven patterns across seven functions have been removed rather than left: five functions
whose six patterns were SHADOWED by a registered module (so they were dead twice over --
unregistered and outranked), plus the two UC-002 package Thens that moved. A shadowed copy
is worse than an absent one: it reads as the definition when you grep, and the two bodies
drift apart silently -- 'the response should include a "{field}"' had already diverged from
its live twin.

Asserts on the dispatch payload (CreateMediaBuyResult or CreateMediaBuySuccess)
and ``ctx["error"]`` (AdCPSalesAgentError or CreateMediaBuyError).
"""

from __future__ import annotations

from pytest_bdd import parsers, then

from tests.bdd.steps._harness_db import db_session as _db_session

# RE-EXPORTED, therefore LIVE despite this module being unregistered. then_success.py
# imports this function and re-declares it under its own @then, and `from ... import name`
# moves the FUNCTION, never the fixture -- so the registration that counts is the one over
# there. Its own @then decorators are removed here: two registrations of one sentence, one
# of them unreachable, is the shadowing this file was just cleaned of.


def then_no_media_buy_persisted(ctx: dict) -> None:
    """Assert no new media buy was created in the database."""
    from sqlalchemy import func, select

    from src.core.database.models import MediaBuy

    tenant = ctx.get("tenant")
    assert tenant is not None, "No tenant in ctx"
    with _db_session(ctx) as session:
        count = session.scalar(select(func.count()).select_from(MediaBuy).filter_by(tenant_id=tenant.tenant_id))
        # Allow existing media buys created by Given steps
        existing_count = 1 if ctx.get("existing_media_buy") else 0
        assert count == existing_count, f"Expected {existing_count} media buy(s) in DB, found {count}"


# PROMOTED to tests/bdd/steps/domain/uc002_invariants.py (registered), each graded on the wire, the
# seller's record through the repositories, or the ad server's receipt: the pricing/date
# validation passes, the approval-path and adapter-execution steps, the media-buy/package/
# creative-assignment persistence steps, the ASAP start_time steps, and the BR-RULE-018
# success/error shape steps. The 'the response should have / contain an "errors" array' pair
# is defined once, in tests/bdd/steps/generic/then_error.py.

# ═══════════════════════════════════════════════════════════════════════
# Response success assertions
# ═══════════════════════════════════════════════════════════════════════


@then(parsers.parse('the response should include "{field}" matching "{value}"'))
def then_response_field_matches(ctx: dict, field: str, value: str) -> None:
    """Assert response field matches the expected value."""
    resp = require_payload(ctx)
    actual = _get_response_field(resp, field)
    assert str(actual) == value, f"Expected {field}='{value}', got '{actual}'"


# "the response should include packages with allocations" and "each package should include
# product_id, budget, and pricing details" are NOT defined here any more. They moved to
# tests/bdd/steps/domain/uc002_create_media_buy.py, which is REGISTERED in pytest_plugins --
# this module is not, so both definitions were unreachable and the only scenario declaring
# either sentence never graded it. Both were also existence checks on result.payload; the
# replacements compare values on the wire against the request. Registering this module
# instead was measured and rejected: it steals 127 already-bound step instances across 5
# modules (salesagent-uokuq).


# ═══════════════════════════════════════════════════════════════════════
# Status and workflow assertions
# ═══════════════════════════════════════════════════════════════════════


# ═══════════════════════════════════════════════════════════════════════
# Notification assertions
# ═══════════════════════════════════════════════════════════════════════


# ═══════════════════════════════════════════════════════════════════════
# Persistence assertions
# ═══════════════════════════════════════════════════════════════════════


# ═══════════════════════════════════════════════════════════════════════
# Response field rejection
# ═══════════════════════════════════════════════════════════════════════


@then(parsers.parse('the response should include "rejection_reason" containing "{text}"'))
def then_rejection_reason_contains(ctx: dict, text: str) -> None:
    """Assert rejection_reason field contains expected text."""
    resp = payload_or_none(ctx)
    if resp is None:
        resp = ctx.get("existing_media_buy")
    assert resp is not None, "No response or media buy to check"
    reason = _get_response_field(resp, "rejection_reason") or ""
    assert text.lower() in reason.lower(), f"Expected '{text}' in rejection_reason: {reason}"


# ═══════════════════════════════════════════════════════════════════════
# Atomic response shape assertions (BR-RULE-018)
# ═══════════════════════════════════════════════════════════════════════


def _retry_after_from_error_object(error_object: dict) -> tuple[object, str] | None:
    """Read ``retry_after`` off one wire error object, spec slot first.

    ``core/error.json`` @3.1.1 declares ``retry_after`` as a TOP-LEVEL member of
    the error object (``"type": "number"``, ``minimum: 1``, ``maximum: 3600``),
    and ``error-details/rate-limited.json`` enumerates limit / remaining /
    window_seconds / scope with NO ``retry_after`` member — so ``details`` is not
    the modelled home for the value.

    The ``details`` read is kept as a LEGACY fallback rather than deleted: at
    least one pinned test-kit (``dist/compliance/3.1.1/test-kits/
    rate-limit-trip-runner.yaml``) still reads ``error.details.retry_after``, and
    call sites that have not yet moved to the spec slot must fail this step on
    the VALUE, not on where they happened to put it.
    """
    if error_object.get("retry_after") is not None:
        return error_object["retry_after"], "top-level (core/error.json)"
    details = error_object.get("details") or {}
    if details.get("retry_after") is not None:
        return details["retry_after"], "details (legacy slot)"
    return None


def _retry_after_from_wire(result: object) -> tuple[object, str] | None:
    """Read ``retry_after`` off the wire error object of *result*.

    Takes the ``TransportResult``, not a bare envelope dict, and resolves the
    error object through ``TransportResult.wire_error_object()``. Two reasons,
    and the first is the one that makes this a correctness fix rather than a
    style change:

    * The reader is wire-only by construction. ``wire_error_object`` reads
      ``wire_error_envelope``, the field reserved for real wire bytes, so this
      extractor cannot be handed a harness-side reconstruction that happens to
      have the right keys. Accepting an untyped ``dict`` made "is this what the
      buyer received?" the CALLER's question, asked afresh at every call site.
    * WHERE the spec puts the per-error fields is the harness's business. The
      former body re-derived it — ``(envelope.get("errors") or [{}])[0]`` with an
      ``adcp_error`` fallback behind it — which is a second answer to the
      question ``locate_envelope_error`` already answers once, and the shape
      ``test_architecture_bdd_wire_discipline`` forbids in a step module.

    ``errors[0]`` only, dropping the former ``adcp_error`` mirror leg, for the
    reason ``then_error.py``'s ``_wire_error_object`` gives for the same drop:
    ``core/error.json`` @3.1.1 defines the per-error members on the payload-layer
    object, so the envelope-level mirror is the wrong region to grade them in.
    Nothing is lost — ``assert_envelope_shape`` pins ``retry_after`` on BOTH
    layers when a caller passes it, so a value that reaches only the mirror is an
    interop bug this step should not be papering over.

    The read WITHIN that object stays at the top level (with the legacy
    ``details`` slot behind it): see :func:`_retry_after_from_error_object`, which
    owns that choice and its citation.
    """
    error_object = result.wire_error_object() if result is not None else None
    if error_object is None:
        return None
    found = _retry_after_from_error_object(error_object)
    if found is None:
        return None
    value, slot = found
    return value, f"errors[0] {slot}"


def _retry_after_from_exception(error: object) -> tuple[object, str] | None:
    """Read ``retry_after`` off a reconstructed exception — the IMPL / no-wire path.

    ``AdCPSalesAgentError`` has a first-class ``retry_after`` attribute that serializes to
    the envelope's top level, so that attribute is read first here for the same
    reason the wire top level is read first above.
    """
    value = getattr(error, "retry_after", None)
    if value is not None:
        return value, "error.retry_after attribute"
    details = getattr(error, "details", None) or {}
    if isinstance(details, dict) and details.get("retry_after") is not None:
        return details["retry_after"], "error.details (legacy slot)"
    return None


@then('the error should include "retry_after" field')
def then_error_has_retry_after(ctx: dict) -> None:
    """Assert the WIRE envelope carries a positive retry_after hint.

    **Authority: the WIRE envelope.** Per ``tests/CLAUDE.md`` § "Error Verification
    Policy" the buyer-facing contract is the envelope, not a reconstructed
    exception, and per ``core/error.json`` @3.1.1 the field's home in that envelope
    is the error object's TOP LEVEL — ``retry_after`` is a member of the Error
    object itself, bounded [1, 3600]. ``details["retry_after"]`` is read only as a
    legacy fallback for call sites that still emit into the slot the spec does not
    model.

    The wire branch first asserts that an error code was actually captured, so a
    green pass cannot come from a dispatch that never reached a transport. The
    former implementation instead inspected a reconstructed ``AdCPSalesAgentError``
    and fell back to ``getattr(error, "retry_after", None)`` on anything else — a
    read that silently yields ``None`` for an object that is not the expected
    shape, which is indistinguishable from a wire that genuinely omitted the hint
    (salesagent-3dawm.18). The reconstructed exception survives ONLY as the IMPL /
    no-wire branch, which has no envelope by definition, and absence there still
    fails loudly.

    **DORMANT — this body executes nowhere today.** ``then_media_buy`` is not in
    ``tests/bdd/conftest.py``'s ``pytest_plugins``, so pytest-bdd never registers
    this step; the ten feature usages either xfail at the harness gate (BR-UC-002)
    or have no binding test module at all (BR-UC-016/020/022). Wiring the module
    is #2132's scope. Stated here so a reader does not take the wire-authority
    reasoning above as evidence that anything grades it.
    """
    from tests.bdd.steps._outcome_helpers import error_envelope_or_none

    envelope = error_envelope_or_none(ctx)
    if envelope is not None:
        code = ctx["result"].wire_error_code()
        assert code is not None, (
            "expected a wire rejection carrying retry_after, but the captured envelope has no "
            "errors[0].code — the operation either succeeded or errored before reaching a "
            f"transport: {envelope!r}"
        )
        found = _retry_after_from_wire(ctx["result"])
        assert found is not None, (
            f"expected a retry_after hint on the wire envelope for {code}, but it is absent from "
            f"both errors[0].retry_after and the legacy errors[0].details slot: {envelope!r}"
        )
    else:
        # No envelope: an MCP dispatch can fail with a ToolError that is genuinely not an
        # AdCP envelope, and then the raised error IS the product.
        error = ctx.get("error")
        assert error is not None, (
            "No error recorded in ctx (checked the result's error envelope and 'error') — "
            "step claims error should include retry_after but no error was captured"
        )
        found = _retry_after_from_exception(error)
        assert found is not None, (
            "Expected retry_after on the error, but it is absent from every slot. "
            f"No wire error envelope was captured. Reconstructed error: {error!r}"
        )

    retry_after, slot = found
    # retry_after should be a positive number (seconds to wait before retrying)
    assert isinstance(retry_after, (int, float)) and not isinstance(retry_after, bool), (
        f"Expected retry_after to be a number (seconds), got {type(retry_after).__name__}: "
        f"{retry_after!r} (read from {slot})"
    )
    assert retry_after > 0, f"Expected positive retry_after value, got {retry_after} (read from {slot})"


# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════

# Single source of truth lives in _outcome_helpers; re-exported for backward compat.
from tests.bdd.steps._outcome_helpers import _get_response_field as _get_response_field  # noqa: F811, PLC0414
from tests.bdd.steps._outcome_helpers import payload_or_none, require_payload
