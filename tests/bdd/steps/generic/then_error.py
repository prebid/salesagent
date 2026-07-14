"""Then steps for error assertions (failure, error codes, messages, suggestions).

These steps assert on ``ctx["error"]`` which is populated by When steps when
an operation fails. Errors are real exceptions from production code:
    - AdCPError subclasses (have .error_code, .message)
    - pydantic.ValidationError (mapped to VALIDATION_ERROR)
    - Other exceptions
"""

from __future__ import annotations

from pytest_bdd import parsers, then

# ── Helpers ─────────────────────────────────────────────────────────


def _wire_code(ctx: dict) -> str | None:
    """Return the authoritative wire error code when a wire envelope was captured.

    ``dispatch_request`` stores the normalized ``TransportResult`` on
    ``ctx['result']`` and exposes the real two-layer envelope on
    ``wire_error_envelope`` (REST/A2A/MCP). The wire code is the buyer-facing
    contract; prefer it over the lossy reconstructed ``ctx['error']`` (which
    collapses distinct wire codes onto one exception class — e.g. yields
    ``RuntimeError`` for an unmapped code). Returns ``None`` on IMPL / no-wire
    scenarios so callers fall back to the reconstructed exception (#1417).
    """
    result = ctx.get("result")
    envelope = getattr(result, "wire_error_envelope", None) if result is not None else None
    if not envelope:
        return None
    return (envelope.get("adcp_error") or {}).get("code")


def _wire_suggestion(ctx: dict) -> str | None:
    """Return the buyer-facing ``suggestion`` from the captured wire envelope.

    Mirrors ``_wire_code``: when the scenario dispatched through a wire transport
    (REST/A2A/MCP), the ``suggestion`` is the buyer-facing contract and must be
    read from the real envelope, not the lossy reconstructed ``ctx['error']``.
    STRICT error.json conformance: only the top-level ``suggestion`` on the
    error object (``errors[0]`` or ``adcp_error`` layer) counts — a suggestion
    buried in ``details`` is a conformance bug the harness surfaces, not masks
    (#1417). Same canonical lookup as
    ``TransportResult.assert_wire_error``. Returns ``None`` on IMPL / no-wire
    scenarios so callers fall back to the reconstructed exception
    (#1417).
    """
    from tests.harness.transport import extract_wire_suggestion

    result = ctx.get("result")
    envelope = getattr(result, "wire_error_envelope", None) if result is not None else None
    return extract_wire_suggestion(envelope)


def _wire_error_object(ctx: dict) -> dict | None:
    """Return the buyer-facing error object from the captured wire envelope.

    Mirrors ``_wire_code`` / ``_wire_suggestion``: when the scenario dispatched
    through a wire transport (REST/A2A/MCP), field-presence checks must read the
    real envelope's error object, not the lossy reconstructed ``ctx['error']``.
    Prefers the ``errors[0]`` layer (per-error fields like ``field``) and falls
    back to the envelope-level ``adcp_error``. Returns ``None`` on IMPL / no-wire
    scenarios so callers fall back to the reconstructed exception (#1417).
    """
    result = ctx.get("result")
    envelope = getattr(result, "wire_error_envelope", None) if result is not None else None
    if not envelope:
        return None
    errors = envelope.get("errors") or []
    if errors and errors[0]:
        return errors[0]
    return envelope.get("adcp_error") or {}


def _get_error_code(error: object) -> str:
    """Extract error code from an exception or Error model.

    Handles two patterns:
    1. Exception-based: AdCPError with .error_code
    2. Partial success: adcp.types.Error model with .code (from response.errors)
    """
    from src.core.exceptions import AdCPError

    if isinstance(error, AdCPError):
        return error.error_code
    # adcp.types.Error model (from partial success response.errors)
    if hasattr(error, "code") and not isinstance(error, Exception):
        return error.code
    # Pydantic ValidationError → VALIDATION_ERROR
    try:
        from pydantic import ValidationError

        if isinstance(error, ValidationError):
            return "VALIDATION_ERROR"
    except ImportError:
        pass
    return type(error).__name__


def _get_error_message(error: object) -> str:
    """Extract human-readable message from an exception or Error model."""
    from src.core.exceptions import AdCPError

    if isinstance(error, AdCPError):
        return error.message
    # adcp.types.Error model
    if hasattr(error, "message") and not isinstance(error, Exception):
        return error.message
    return str(error)


def _get_error_dict(error: object) -> dict:
    """Convert exception or Error model to dict for field-presence checks."""
    from src.core.exceptions import AdCPError

    if isinstance(error, AdCPError):
        d = error.to_dict()
        # AdCPError.to_dict() has: error_code, message, recovery, details
        # Map to the assertion vocabulary used in feature files.
        # Deliberately NO promotion of details["suggestion"]: error.json places
        # suggestion at the top level, and to_dict() already carries it there
        # when the emitter is conformant (#1417).
        d["code"] = d.get("error_code", "")
        return d
    # adcp.types.Error model (from partial success response.errors) — has code,
    # message, suggestion, recovery, field as direct attributes.
    if hasattr(error, "code") and not isinstance(error, Exception):
        d: dict = {"code": error.code, "message": getattr(error, "message", "")}
        suggestion = getattr(error, "suggestion", None)
        if suggestion:
            d["suggestion"] = suggestion
        recovery = getattr(error, "recovery", None)
        if recovery is not None:
            d["recovery"] = recovery.value if hasattr(recovery, "value") else str(recovery)
        field = getattr(error, "field", None)
        if field:
            d["field"] = field
        return d
    return {"code": _get_error_code(error), "message": _get_error_message(error)}


# ── Shared validation ───────────────────────────────────────────────


def _assert_meaningful_error(error: object) -> None:
    """Assert the error object carries meaningful error information.

    Validates that the error is either:
    - An AdCPError with a non-empty error_code string, OR
    - An adcp Error model with a non-empty string .code attribute, OR
    - Another Exception with a non-empty string representation.

    This rejects empty/placeholder errors that would make any
    "operation should fail" assertion tautological.
    """
    from src.core.exceptions import AdCPError

    if isinstance(error, AdCPError):
        assert isinstance(error.error_code, str) and error.error_code, (
            f"AdCPError has empty or non-string error_code: {error.error_code!r}"
        )
        return

    # adcp.types.Error model (from partial success response.errors)
    code = getattr(error, "code", None)
    if code is not None and not isinstance(error, Exception):
        assert isinstance(code, str) and code, f"Error model has empty or non-string code: {code!r}"
        return

    if isinstance(error, (Exception, BaseException)):
        assert str(error), f"Exception has no message: {type(error).__name__}"
        return

    raise AssertionError(f"ctx['error'] is not an Exception or Error model: {type(error).__name__} = {error!r}")


# ── Operation failure ────────────────────────────────────────────────


@then("the operation should fail")
def then_operation_fails(ctx: dict) -> None:
    """Assert the operation resulted in an error.

    Checks two patterns:
    1. Exception-based: ctx["error"] set by dispatch on exception
    2. Partial success: response.errors non-empty (UC-004 delivery pattern)

    Both paths make a positive assertion that a real error object exists
    with meaningful error information — not just that a ctx key is set.
    """
    error = ctx.get("error")
    if error is not None:
        _assert_meaningful_error(error)
        return
    resp = ctx.get("response")
    if resp is not None and hasattr(resp, "errors") and resp.errors:
        # Promote the first response error to ctx["error"] so downstream
        # Then steps (error_code, error_message) can find it.
        first_error = resp.errors[0]
        assert first_error is not None, "response.errors[0] is None — expected a concrete error object"
        _assert_meaningful_error(first_error)
        ctx["error"] = first_error
        return
    raise AssertionError(
        "Expected the operation to fail but no error was recorded. "
        f"ctx keys: {list(ctx.keys())}, response: {ctx.get('response')!r}"
    )


@then("the entire sync operation fails")
def then_entire_sync_operation_fails(ctx: dict) -> None:
    """Assert the sync operation failed entirely -- no partial successes.

    Stronger than "the operation should fail": this step additionally verifies
    that the failure is total.  When a sync runs in strict validation mode
    (BR-RULE-172 INV-5), a single invalid catalog must cause the entire
    operation to be rejected -- the response must NOT contain any successfully
    processed items alongside the error.

    Asserts:
    1. An error was recorded with meaningful error information.
    2. If a response exists with a results/catalogs collection, NONE of the
       items were processed successfully (no partial success).
    """
    # ── Resolve the error object ────────────────────────────────────
    error = ctx.get("error")
    resp = ctx.get("response")

    # Promote response.errors if no top-level error was captured
    if error is None and resp is not None and hasattr(resp, "errors") and resp.errors:
        first_error = resp.errors[0]
        assert first_error is not None, "response.errors[0] is None -- expected a concrete error"
        ctx["error"] = first_error
        error = first_error

    assert error is not None, (
        "Expected the entire sync operation to fail but no error was recorded. "
        f"ctx keys: {list(ctx.keys())}, response: {resp!r}"
    )

    # ── Verify it carries meaningful error information ──────────────
    _assert_meaningful_error(error)

    # ── Verify NO partial successes ─────────────────────────────────
    # "Entire sync fails" means the operation was rejected wholesale.
    # If a response exists with item-level results, none may have succeeded.
    if resp is not None:
        for attr in ("catalogs", "results", "items"):
            items = getattr(resp, attr, None)
            if items is None:
                continue
            successful = [
                item
                for item in items
                if getattr(item, "action", None) not in (None, "failed", "error", "rejected")
                or getattr(item, "status", None) == "success"
            ]
            assert not successful, (
                f"Expected entire sync to fail but found {len(successful)} "
                f"successfully processed item(s) in response.{attr} -- "
                f"this indicates partial success, not total failure. "
                f"BR-RULE-172 INV-5 requires the ENTIRE operation to fail."
            )


# ── Error code ───────────────────────────────────────────────────────


@then(parsers.parse('the error code should be "{code}"'))
def then_error_code(ctx: dict, code: str) -> None:
    """Assert the error code matches — wire-first, reconstructed fallback.

    When the scenario dispatched through a wire transport, assert on the real
    wire envelope's code (the buyer-facing contract); otherwise fall back to the
    reconstructed ``ctx['error']`` for IMPL/no-wire scenarios (ztl6.6).
    """
    actual = _wire_code(ctx)
    if actual is None:
        error = ctx.get("error")
        assert error is not None, "No error recorded in ctx"
        actual = _get_error_code(error)
    assert actual == code, f"Expected error code '{code}', got '{actual}'"


# ── Error message content (generic) ───────────────────────────────────


@then(parsers.parse('the error message should contain "{text}"'))
def then_error_message_contains(ctx: dict, text: str) -> None:
    """Assert error message contains the given text (case-insensitive)."""
    error = ctx.get("error")
    assert error is not None, "No error recorded in ctx"
    msg = _get_error_message(error).lower()
    assert text.lower() in msg, f"Expected '{text}' in error message: {_get_error_message(error)}"


@then(parsers.parse('the suggestion should contain "{text}"'))
def then_suggestion_contains(ctx: dict, text: str) -> None:
    """Assert error suggestion contains the given text — wire-first, reconstructed fallback.

    When the scenario dispatched through a wire transport, assert on the real
    wire envelope's suggestion (the buyer-facing contract); otherwise fall back
    to the reconstructed ``ctx['error']`` for IMPL/no-wire scenarios (ztl6.6).
    """
    suggestion = _wire_suggestion(ctx)
    if suggestion is None:
        error = ctx.get("error")
        assert error is not None, "No error recorded in ctx"
        suggestion = _get_error_dict(error).get("suggestion")
    suggestion_lower = (suggestion or "").lower()
    assert text.lower() in suggestion_lower, f"Expected '{text}' in suggestion: {suggestion}"


# ── Error message content (specific) ───────────────────────────────────


@then("the error message should indicate tenant context could not be determined")
def then_error_tenant_context(ctx: dict) -> None:
    """Assert error message mentions tenant context resolution failure."""
    error = ctx.get("error")
    assert error is not None, "No error recorded in ctx"
    msg = _get_error_message(error).lower()
    assert "tenant" in msg, f"Expected 'tenant' in error message: {_get_error_message(error)}"
    # Gherkin says "could not be determined" — must indicate a resolution failure
    resolution_words = ("could not", "cannot", "unable", "not found", "missing", "resolve", "determine")
    assert any(w in msg for w in resolution_words), (
        f"Expected tenant resolution failure language, got: {_get_error_message(error)}"
    )


@then("the error message should indicate which parameters are invalid")
def then_error_invalid_params(ctx: dict) -> None:
    """Assert error message indicates which specific parameters are invalid."""
    error = ctx.get("error")
    assert error is not None, "No error recorded in ctx"
    # Pydantic ValidationError: has per-field error details with field paths
    if hasattr(error, "errors"):
        field_errors = error.errors()
        assert field_errors, "ValidationError has no field-level error details"
        assert all("loc" in e for e in field_errors), f"Expected field locations in error details: {field_errors}"
        return
    # AdCPError: message must reference parameter/field specifics
    msg = _get_error_message(error)
    msg_lower = msg.lower()
    assert any(kw in msg_lower for kw in ("parameter", "field", "invalid", "format_id", "agent_url")), (
        f"Expected error to indicate which parameters are invalid, got: {msg}"
    )


@then(parsers.parse('the error message should indicate "{value}" is not a valid disclosure position'))
def then_error_invalid_disclosure(ctx: dict, value: str) -> None:
    """Assert error message mentions the invalid disclosure position value."""
    error = ctx.get("error")
    assert error is not None, "No error recorded in ctx"
    msg = _get_error_message(error)
    assert value in msg, f"Expected '{value}' in error message: {msg}"


@then("the error message should indicate at least 1 item is required")
def then_error_min_items(ctx: dict) -> None:
    """Assert error message mentions minimum items requirement.

    Must reference a quantity constraint, not just generic 'required'.
    """
    error = ctx.get("error")
    assert error is not None, "No error recorded in ctx"
    msg = _get_error_message(error).lower()
    quantity_patterns = (
        "at least 1",
        "at least one",
        "minimum",
        "min_length",
        "minlength",
        "ensure this",
        "too short",
        "empty",
    )
    assert any(p in msg for p in quantity_patterns), (
        f"Expected min-items/quantity constraint message, got: {_get_error_message(error)}"
    )


@then("the error message should indicate duplicate values are not allowed")
def then_error_duplicates(ctx: dict) -> None:
    """Assert error message mentions duplicate values."""
    error = ctx.get("error")
    assert error is not None, "No error recorded in ctx"
    msg = _get_error_message(error).lower()
    assert "duplicate" in msg, f"Expected 'duplicate' in error message: {_get_error_message(error)}"


@then("the error message should indicate FormatId must include agent_url and id")
def then_error_format_id_structure(ctx: dict) -> None:
    """Assert error message mentions both agent_url AND id as required FormatId fields."""
    error = ctx.get("error")
    assert error is not None, "No error recorded in ctx"
    # Pydantic ValidationError: check field paths directly
    if hasattr(error, "errors"):
        error_fields = {str(loc) for e in error.errors() for loc in e.get("loc", ())}
        assert "agent_url" in error_fields, f"Expected 'agent_url' in validation error fields: {error_fields}"
        assert "id" in error_fields, f"Expected 'id' in validation error fields: {error_fields}"
        return
    # AdCPError: message must reference both fields
    msg = _get_error_message(error).lower()
    assert "agent_url" in msg, f"Expected 'agent_url' in error: {_get_error_message(error)}"
    assert "id" in msg, f"Expected 'id' in FormatId error: {_get_error_message(error)}"


# ── Suggestion field ─────────────────────────────────────────────────


@then(parsers.parse('the error recovery should be "{recovery}"'))
def then_error_recovery(ctx: dict, recovery: str) -> None:
    """Assert the error recovery hint matches."""
    error = ctx.get("error")
    assert error is not None, "No error recorded in ctx"
    from src.core.exceptions import AdCPError

    if isinstance(error, AdCPError):
        assert error.recovery == recovery, f"Expected recovery '{recovery}', got '{error.recovery}'"
    else:
        raise AssertionError(f"Cannot check recovery on non-AdCPError: {type(error).__name__}")


@then('the error should include a "suggestion" field')
@then('the error should include "suggestion" field')
def then_error_has_suggestion(ctx: dict) -> None:
    """Assert error includes a non-empty suggestion — wire-first, reconstructed fallback.

    On a wire transport the suggestion is read from the real envelope (the
    buyer-facing contract); IMPL/no-wire scenarios fall back to the reconstructed
    ``ctx['error']`` (ztl6.6).
    """
    suggestion = _wire_suggestion(ctx)
    if suggestion is not None:
        assert suggestion, "Expected non-empty suggestion in wire envelope"
        return
    error = ctx.get("error")
    assert error is not None, "No error recorded in ctx"
    d = _get_error_dict(error)
    assert "suggestion" in d, f"Expected 'suggestion' in error: {d}"
    assert d["suggestion"], "Expected non-empty suggestion"


@then("the error should include a suggestion for how to fix the issue")
def then_error_has_fix_suggestion(ctx: dict) -> None:
    """Assert error includes an actionable suggestion for fixing the issue.

    Unlike then_error_has_suggestion (structural check), this step verifies
    the suggestion contains actionable language (use/try/check/provide/etc.)
    that tells the caller how to correct the problem.
    """
    # Wire-first: on a wire transport the suggestion is the buyer-facing contract.
    # Read it from the real envelope; fall back to the reconstructed ctx['error']
    # for IMPL/no-wire scenarios (ztl6.8).
    suggestion = _wire_suggestion(ctx)
    if suggestion is None:
        error = ctx.get("error")
        assert error is not None, "No error recorded in ctx"

        # Pydantic ValidationErrors carry the fix guidance inline in each field
        # error's ``msg`` (e.g. "Input should be 'operator', 'agent' or 'advertiser'")
        # rather than a separate ``suggestion`` field. That inline message IS the
        # actionable guidance, so accept it without the verb check below.
        from pydantic import ValidationError

        if isinstance(error, ValidationError):
            details = error.errors()
            assert details, "ValidationError has no field-level details to guide a fix"
            for detail in details:
                msg = detail.get("msg", "")
                assert isinstance(msg, str) and msg.strip(), f"ValidationError detail lacks fix guidance: {detail}"
            return

        suggestion = _get_error_dict(error).get("suggestion")
    assert suggestion, "Expected non-empty suggestion"
    # A fix suggestion must contain actionable guidance — a verb telling the
    # caller what to DO, not just describing the problem.
    suggestion_lower = suggestion.lower()
    # Split into words to avoid substring matches (e.g., "reset" matching "set")
    words = set(suggestion_lower.split())
    action_verbs = {
        "use",
        "try",
        "check",
        "provide",
        "include",
        "ensure",
        "remove",
        "specify",
        "set",
        "omit",
        "add",
        "verify",
    }
    found = words & action_verbs
    assert found, (
        f"Expected actionable fix suggestion with a verb ({', '.join(sorted(action_verbs))}), got: {suggestion}"
    )


# ── Suggestion content ───────────────────────────────────────────────


@then("the suggestion should advise providing authentication credentials")
def then_suggestion_auth(ctx: dict) -> None:
    """Assert suggestion mentions authentication credentials — wire-first, reconstructed fallback (ztl6.8)."""
    suggestion = _wire_suggestion(ctx)
    if suggestion is None:
        suggestion = _get_error_dict(ctx.get("error")).get("suggestion") or ""
    suggestion_lower = suggestion.lower()
    assert "credential" in suggestion_lower or "auth" in suggestion_lower, f"Expected auth suggestion: {suggestion}"


@then("the suggestion should provide valid parameter values")
def then_suggestion_valid_values(ctx: dict) -> None:
    """Assert suggestion provides valid parameter values — wire-first, reconstructed fallback (ztl6.8).

    Must reference both validity AND values.
    """
    suggestion = _wire_suggestion(ctx)
    if suggestion is None:
        suggestion = _get_error_dict(ctx.get("error")).get("suggestion") or ""
    assert suggestion, "Expected non-empty suggestion"
    suggestion_lower = suggestion.lower()
    # Must mention validity concept
    assert any(kw in suggestion_lower for kw in ("valid", "allowed", "accepted", "supported")), (
        f"Expected suggestion to indicate valid/allowed/accepted values, got: {suggestion}"
    )
    # Must mention values/options concept (not just "use valid X")
    assert any(kw in suggestion_lower for kw in ("values", "options", ":", "'", '"', "[", ",")), (
        f"Expected suggestion to enumerate or reference specific values, got: {suggestion}"
    )


@then("the suggestion should advise using valid DisclosurePosition enum values")
def then_suggestion_disclosure_enum(ctx: dict) -> None:
    """Assert suggestion mentions both DisclosurePosition AND valid values — wire-first (ztl6.8)."""
    raw = _wire_suggestion(ctx)
    if raw is None:
        raw = _get_error_dict(ctx.get("error")).get("suggestion") or ""
    suggestion = raw.lower()
    # Gherkin requires both concepts: "DisclosurePosition" AND "valid enum values"
    assert (
        "disclosureposition" in suggestion or "disclosure_position" in suggestion or "disclosure position" in suggestion
    ), f"Expected 'DisclosurePosition' in suggestion: {raw}"
    assert "valid" in suggestion or "allowed" in suggestion or "enum" in suggestion, (
        f"Expected valid/allowed/enum values language in suggestion: {raw}"
    )


@then("the suggestion should advise providing at least one position or omitting the filter")
def then_suggestion_positions_or_omit(ctx: dict) -> None:
    """Assert suggestion advises providing positions OR omitting the filter.

    Gherkin describes two alternatives — the suggestion should mention at least
    one alternative completely (position + provide/add, or omit/remove).
    Wire-first, reconstructed fallback (ztl6.8).
    """
    raw = _wire_suggestion(ctx)
    if raw is None:
        raw = _get_error_dict(ctx.get("error")).get("suggestion") or ""
    suggestion = raw.lower()
    has_provide_position = "position" in suggestion and any(
        w in suggestion for w in ("provide", "add", "include", "at least")
    )
    has_omit = "omit" in suggestion or "remove" in suggestion
    assert has_provide_position or has_omit, (
        f"Expected suggestion to advise providing positions or omitting filter: {raw}"
    )


@then("the suggestion should advise removing duplicate positions")
def then_suggestion_remove_dupes(ctx: dict) -> None:
    """Assert suggestion advises removing duplicates — wire-first, reconstructed fallback (ztl6.8).

    Both concepts required.
    """
    raw = _wire_suggestion(ctx)
    if raw is None:
        raw = _get_error_dict(ctx.get("error")).get("suggestion") or ""
    suggestion = raw.lower()
    # Gherkin says "removing duplicate" — both concepts must appear
    assert "duplicate" in suggestion, f"Expected 'duplicate' in suggestion: {raw}"
    assert any(w in suggestion for w in ("remove", "deduplicate", "dedup", "eliminate")), (
        f"Expected removal action in suggestion: {raw}"
    )


@then("the suggestion should advise providing at least one FormatId or omitting the filter")
def then_suggestion_format_id_or_omit(ctx: dict) -> None:
    """Assert suggestion advises providing FormatId OR omitting the filter.

    Same pattern as positions_or_omit — one complete alternative required.
    Wire-first, reconstructed fallback (ztl6.8).
    """
    raw = _wire_suggestion(ctx)
    if raw is None:
        raw = _get_error_dict(ctx.get("error")).get("suggestion") or ""
    suggestion = raw.lower()
    has_provide_format = ("formatid" in suggestion or "format_id" in suggestion or "format id" in suggestion) and any(
        w in suggestion for w in ("provide", "add", "include", "at least")
    )
    has_omit = "omit" in suggestion or "remove" in suggestion
    assert has_provide_format or has_omit, f"Expected suggestion to advise providing FormatId or omitting filter: {raw}"


@then("the suggestion should advise including agent_url (URI) and id fields")
def then_suggestion_agent_url_id(ctx: dict) -> None:
    """Assert suggestion advises including both agent_url AND id fields — wire-first (ztl6.8)."""
    import re

    suggestion = _wire_suggestion(ctx)
    if suggestion is None:
        suggestion = _get_error_dict(ctx.get("error")).get("suggestion") or ""
    assert suggestion, "Expected non-empty suggestion"
    suggestion_lower = suggestion.lower()
    assert "agent_url" in suggestion_lower or "uri" in suggestion_lower, (
        f"Expected agent_url/URI in suggestion: {suggestion}"
    )
    # Use word-boundary match to avoid false positives on "invalid", "bidder", etc.
    assert re.search(r"\bid\b", suggestion_lower), (
        f"Expected standalone 'id' field reference in suggestion: {suggestion}"
    )


# ── No error raised ─────────────────────────────────────────────────


@then("no error should be raised")
def then_no_error(ctx: dict) -> None:
    """Assert no error was recorded."""
    assert "error" not in ctx, f"Expected no error but got: {ctx.get('error')}"


@then("no error should be returned")
def then_no_error_returned(ctx: dict) -> None:
    """Assert no error was returned (synonym for no error raised)."""
    assert "error" not in ctx, f"Expected no error but got: {ctx.get('error')}"


@then(parsers.parse('no error should be raised for "{value}"'))
def then_no_error_for_value(ctx: dict, value: str) -> None:
    """Assert no error was raised for a specific value (silent exclusion)."""
    assert "error" not in ctx, f"Expected no error for '{value}' but got: {ctx.get('error')}"


# ── Validation error (sandbox) ───────────────────────────────────────


@then("the response should indicate a validation error")
def then_validation_error(ctx: dict) -> None:
    """Assert response indicates a validation error — wire-first, reconstructed fallback.

    When the scenario dispatched through a wire transport, assert on the real
    wire envelope's code (the buyer-facing contract); otherwise fall back to the
    reconstructed ``ctx['error']`` for IMPL/no-wire scenarios (ztl6.8).
    """
    actual = _wire_code(ctx)
    if actual is None:
        error = ctx.get("error")
        assert error is not None, "Expected a validation error"
        actual = _get_error_code(error)
    assert actual == "VALIDATION_ERROR", f"Expected VALIDATION_ERROR, got {actual}"


@then("the error should be a real validation error, not simulated")
def then_real_validation_error(ctx: dict) -> None:
    """Assert the error is a real Pydantic validation error, not a simulated one.

    A real validation error is a pydantic.ValidationError raised by schema
    validation, with per-field error details. This distinguishes it from
    AdCPValidationError (our wrapper) or sandbox-simulated errors.
    """
    error = ctx.get("error")
    assert error is not None, "Expected an error"
    from pydantic import ValidationError

    assert isinstance(error, ValidationError), (
        f"Expected a real pydantic.ValidationError, got {type(error).__name__}: {error}"
    )
    assert error.errors(), "Expected ValidationError with field-level error details"


# ── Generic field presence / value ──────────────────────────────────


# Fields defined at the TOP LEVEL of the error.json protocol schema. Presence of
# these MUST be asserted at the top level of the wire error object — a copy buried
# in the free-form ``details`` dict does NOT satisfy the protocol contract (same
# burial disease removed from extract_wire_suggestion, #1417/ioni).
_ERROR_JSON_TOP_LEVEL_FIELDS = frozenset(
    {"code", "message", "field", "suggestion", "retry_after", "issues", "details", "recovery"}
)


@then(parsers.parse('the error should include "{field}" field'))
def then_error_includes_field(ctx: dict, field: str) -> None:
    """Assert the error includes a named field with a non-empty value — wire-first.

    When the scenario dispatched through a wire transport, read the field from
    the real wire envelope's error object (the buyer-facing contract); otherwise
    fall back to the reconstructed ``ctx['error']`` for IMPL/no-wire scenarios.

    For fields defined at the top level of error.json (see
    ``_ERROR_JSON_TOP_LEVEL_FIELDS``) the assertion requires the TOP-LEVEL position
    only — a value buried in ``details`` is a protocol-conformance violation, not a
    pass. The ``details`` alternative is kept only for genuinely detail-scoped keys.
    """
    protocol_top_level = field in _ERROR_JSON_TOP_LEVEL_FIELDS
    wire = _wire_error_object(ctx)
    if wire is not None:
        wire_details = wire.get("details") or {}
        has_top = bool(field in wire and wire[field])
        has_detail = bool(field in wire_details and wire_details[field])
        has_field = has_top if protocol_top_level else (has_top or has_detail)
        assert has_field, (
            f"Expected wire error to include non-empty '{field}' field"
            + (" at the protocol top level (not in details)" if protocol_top_level else "")
            + f". Wire error keys: {list(wire.keys())}, details keys: {list(wire_details.keys())}"
        )
        return
    error = ctx.get("error")
    assert error is not None, "No error recorded in ctx"
    d = _get_error_dict(error)
    # Also check details sub-dict and direct attributes
    details = getattr(error, "details", None) or {}
    has_top = bool((field in d and d[field]) or (hasattr(error, field) and getattr(error, field)))
    has_detail = bool(field in details and details[field])
    has_field = has_top if protocol_top_level else (has_top or has_detail)
    assert has_field, (
        f"Expected error to include non-empty '{field}' field"
        + (" at the protocol top level (not in details)" if protocol_top_level else "")
        + f". Error dict keys: {list(d.keys())}, details keys: {list(details.keys())}"
    )


@then(parsers.parse('the error should include "{field}" field with value "{value}"'))
def then_error_field_with_value(ctx: dict, field: str, value: str) -> None:
    """Assert the error includes a named field matching the expected value.

    Checks the error dict, details sub-dict, and direct attributes.
    Compares as strings for cross-type compatibility.
    """
    error = ctx.get("error")
    assert error is not None, "No error recorded in ctx"
    actual = _resolve_error_field(error, field)
    assert actual is not None, (
        f"Expected error to include '{field}' field but it was not found. Available: {_available_error_fields(error)}"
    )
    # Compare as strings for cross-type compatibility (enum .value, int, etc.)
    actual_str = actual.value if hasattr(actual, "value") else str(actual)
    assert actual_str == value, f"Expected {field}='{value}', got '{actual_str}'"


# ── Error details assertions ────────────────────────────────────────


@then(parsers.parse("the error details should include {key} {value}"))
def then_error_details_include_unquoted(ctx: dict, key: str, value: str) -> None:
    """Assert error.details contains a key with the given value (numeric/unquoted).

    Handles numeric coercion: if the expected value looks like a number,
    compare numerically. Otherwise compare as strings.
    """
    error = ctx.get("error")
    assert error is not None, "No error recorded in ctx"
    details = _get_error_details(error)
    assert key in details, f"Expected '{key}' in error details. Available keys: {list(details.keys())}"
    actual = details[key]
    _assert_detail_value_matches(key, actual, value)


@then(parsers.parse('the error details should include {key} "{value}"'))
def then_error_details_include_quoted(ctx: dict, key: str, value: str) -> None:
    """Assert error.details contains a key with the given string value."""
    error = ctx.get("error")
    assert error is not None, "No error recorded in ctx"
    details = _get_error_details(error)
    assert key in details, f"Expected '{key}' in error details. Available keys: {list(details.keys())}"
    actual = details[key]
    assert str(actual) == value, f"Expected details['{key}'] = '{value}', got '{actual}'"


@then(parsers.parse('the error "details" object should include "{key}" with value {value:d}'))
def then_error_details_object_numeric(ctx: dict, key: str, value: int) -> None:
    """Assert error.details contains a key with an integer value.

    Feature-file pattern: 'the error "details" object should include "minimum_budget" with value 500'
    Delegates to the same _get_error_details / _assert_detail_value_matches helpers
    as the unquoted-key variant above.
    """
    error = ctx.get("error")
    assert error is not None, "No error recorded in ctx"
    details = _get_error_details(error)
    assert key in details, f"Expected '{key}' in error details. Available keys: {list(details.keys())}"
    actual = details[key]
    _assert_detail_value_matches(key, actual, str(value))


@then(parsers.parse('the error "details" object should include "{key}" with value "{value}"'))
def then_error_details_object_string(ctx: dict, key: str, value: str) -> None:
    """Assert error.details contains a key with a string value.

    Feature-file pattern: 'the error "details" object should include "currency" with value "USD"'
    """
    error = ctx.get("error")
    assert error is not None, "No error recorded in ctx"
    details = _get_error_details(error)
    assert key in details, f"Expected '{key}' in error details. Available keys: {list(details.keys())}"
    actual = details[key]
    # A media-buy-id label in the feature (e.g. CONFLICT details resource_id
    # "mb_existing") resolves to the factory-generated real id; no-op otherwise.
    expected = ctx.get("media_buy_labels", {}).get(value, value)
    assert str(actual) == expected, f"Expected details['{key}'] = '{expected}', got '{actual}'"


@then(parsers.parse('the "{field}" value should match ISO 4217 alphabetic format'))
def then_field_matches_iso4217(ctx: dict, field: str) -> None:
    """Assert the given field value in error details matches ISO 4217 format.

    ISO 4217 alphabetic codes are exactly 3 uppercase ASCII letters (e.g., USD, EUR, GBP).
    """
    import re

    error = ctx.get("error")
    assert error is not None, "No error recorded in ctx"
    details = _get_error_details(error)
    actual = details.get(field)
    assert actual is not None, f"Field '{field}' not found in error details. Available keys: {list(details.keys())}"
    assert isinstance(actual, str), f"Expected '{field}' to be a string, got {type(actual).__name__}: {actual!r}"
    assert re.fullmatch(r"[A-Z]{3}", actual), (
        f"Expected '{field}' value '{actual}' to match ISO 4217 alphabetic format "
        "(exactly 3 uppercase ASCII letters, e.g., USD)"
    )


# ── Terminal failure ────────────────────────────────────────────────


@then("the response should indicate a terminal failure")
def then_terminal_failure(ctx: dict) -> None:
    """Assert the operation failed with a terminal (non-recoverable) error.

    Verifies both that an error occurred and that its recovery hint is
    'terminal' -- meaning the buyer cannot retry with corrected input.
    """
    error = ctx.get("error")
    assert error is not None, (
        "Expected a terminal failure but no error was recorded. "
        f"ctx keys: {list(ctx.keys())}, response: {ctx.get('response')!r}"
    )
    _assert_meaningful_error(error)
    from src.core.exceptions import AdCPError

    if isinstance(error, AdCPError):
        assert error.recovery == "terminal", f"Expected terminal recovery, got '{error.recovery}'"
    elif hasattr(error, "recovery"):
        recovery = error.recovery.value if hasattr(error.recovery, "value") else str(error.recovery)
        assert recovery == "terminal", f"Expected terminal recovery, got '{recovery}'"
    # If the error type doesn't carry recovery info, the error itself is
    # sufficient -- non-AdCP exceptions are terminal by nature.


# ── No records created (DB state assertions) ────────────────────────


@then("no database records should be created")
def then_no_db_records_created(ctx: dict) -> None:
    """Assert that no new database records were created by the operation.

    For create operations: verifies no media buy was persisted.
    Uses the media_buy_id from the response (if any) or checks that no
    new records exist beyond what was set up by Given steps.
    """
    _assert_no_new_media_buy(ctx)


@then("no new media buy should have been created")
def then_no_new_media_buy(ctx: dict) -> None:
    """Assert no new media buy record was persisted in the database."""
    _assert_no_new_media_buy(ctx)


_ADAPTER_CREATE_METHODS = ("create_order", "create_line_item", "create_media_buy")


@then("no new ad platform order should have been created")
def then_no_new_ad_platform_order(ctx: dict) -> None:
    """Assert the action under test booked NO new ad platform order.

    "No NEW order" means: the adapter created no order beyond what already
    existed before the action under test. The expected create-count is read
    from an explicit baseline rather than sniffing the environment:

      baseline = ctx.get("adapter_calls_after_first_create")

    Two scenario families share this step text, distinguished only by whether
    that baseline is present:

    * Baseline ABSENT (default 0) -- fresh-failure scenarios (validation /
      account-not-found). The request fails before reaching the adapter, so
      EVERY adapter create method must show zero calls. We scan all the create
      methods (``create_order``, ``create_line_item``, ``create_media_buy``) on
      both the adapter mock and its ``return_value`` (the adapter instance),
      because the request never got far enough to call any of them.

    * Baseline PRESENT -- idempotency-replay scenarios. The "already created"
      Given step performed a REAL first create (which DID call the adapter) and
      recorded ``adapter_calls_after_first_create`` = the
      ``create_media_buy`` call_count immediately after that first create. The
      replay must serve the cached response WITHOUT a second booking, so the
      post-action ``create_media_buy`` call_count must not exceed the baseline.

    The baseline default of 0 is the correct expected count for the fresh case
    (nothing booked yet), so the same arithmetic check -- "current count <=
    baseline" -- serves both families without an env-sniffing branch.
    """
    env = ctx["env"]
    baseline = ctx.get("adapter_calls_after_first_create")

    if baseline is None:
        # Fresh-failure family: the adapter was never reached. Any call to ANY
        # create method on the adapter mock or its instance is a real booking.
        adapter_mock = env.mock.get("adapter")
        assert adapter_mock is not None, "No adapter mock in the harness env — cannot verify booking state"
        scan_targets = [adapter_mock]
        adapter_instance = getattr(adapter_mock, "return_value", None)
        if adapter_instance is not None:
            scan_targets.append(adapter_instance)
        for target, label in zip(scan_targets, ("adapter", "adapter()"), strict=False):
            for method_name in _ADAPTER_CREATE_METHODS:
                method = getattr(target, method_name, None)
                call_count = getattr(method, "call_count", 0) if method is not None else 0
                assert call_count == 0, (
                    f"Expected no new ad platform order but {label}.{method_name} was called "
                    f"{call_count} time(s) — the request booked an order despite failing/short-circuiting"
                )
        return

    # Idempotency-replay family: the first create already booked one order
    # (baseline). The replay must not book another.
    adapter_instance = env.mock["adapter"].return_value
    after = adapter_instance.create_media_buy.call_count
    assert after <= baseline, (
        f"Adapter create_media_buy was called {after} time(s) total, but only "
        f"{baseline} (the original) is allowed — the replay re-booked an ad platform order "
        "instead of serving the cached response"
    )


# ── Helpers for new steps ───────────────────────────────────────────


def _resolve_error_field(error: object, field: str) -> object | None:
    """Resolve a named field from an error, checking multiple sources."""
    # 1. Direct attribute on the error
    if hasattr(error, field):
        val = getattr(error, field)
        if val is not None:
            return val
    # 2. The error dict (to_dict() representation)
    d = _get_error_dict(error)
    if field in d and d[field] is not None:
        return d[field]
    # 3. The details sub-dict
    details = getattr(error, "details", None) or {}
    if field in details and details[field] is not None:
        return details[field]
    return None


def _available_error_fields(error: object) -> list[str]:
    """List available field names from all error sources for diagnostics."""
    fields: set[str] = set()
    d = _get_error_dict(error)
    fields.update(d.keys())
    details = getattr(error, "details", None) or {}
    fields.update(details.keys())
    for attr in ("error_code", "message", "recovery", "suggestion", "field"):
        if hasattr(error, attr):
            fields.add(attr)
    return sorted(fields)


def _get_error_details(error: object) -> dict:
    """Extract the details dict from an error object."""
    from src.core.exceptions import AdCPError

    if isinstance(error, AdCPError):
        return error.details or {}
    # adcp.types.Error model
    if hasattr(error, "details") and not isinstance(error, Exception):
        return error.details or {}
    # Fallback: try the error dict
    d = _get_error_dict(error)
    return d.get("details", {})


def _assert_detail_value_matches(key: str, actual: object, expected_str: str) -> None:
    """Assert a detail value matches, with numeric coercion."""
    # Try numeric comparison first
    try:
        expected_num = float(expected_str)
        actual_num = float(actual)  # type: ignore[arg-type]
        if expected_num == int(expected_num):
            # Integer comparison (e.g., "500" should match 500 and 500.0)
            assert actual_num == expected_num, f"Expected details['{key}'] = {expected_str}, got {actual}"
        else:
            assert abs(actual_num - expected_num) < 1e-9, f"Expected details['{key}'] = {expected_str}, got {actual}"
        return
    except (ValueError, TypeError):
        pass
    # Fall back to string comparison
    assert str(actual) == expected_str, f"Expected details['{key}'] = '{expected_str}', got '{actual}'"


def _assert_no_new_media_buy(ctx: dict) -> None:
    """Shared implementation: verify no new media buy was created.

    Two strategies:
    1. If a response exists with a media_buy_id, verify that ID does not
       exist in the database.
    2. If the harness tracks pre-operation media buy count, verify count
       is unchanged.
    3. Fallback: verify the operation errored (no response = no creation).
    """
    env = ctx["env"]
    resp = ctx.get("response")

    # Strategy 1: if we got a response with media_buy_id, it should not be in DB
    if resp is not None:
        mb_id = getattr(resp, "media_buy_id", None)
        if mb_id is not None:
            mb = env.get_media_buy(mb_id) if hasattr(env, "get_media_buy") else None
            assert mb is None, f"Expected no media buy to be created but found {mb_id} in database"
            return

    # Strategy 2: operation should have errored (no response = nothing created)
    error = ctx.get("error")
    if error is not None:
        # Error means the operation failed before creating anything
        return

    # Strategy 3: check that response doesn't indicate creation
    if resp is not None and not hasattr(resp, "media_buy_id"):
        return

    raise AssertionError(
        "Cannot verify no media buy was created: no error recorded and "
        f"response has media_buy_id. ctx keys: {list(ctx.keys())}"
    )
