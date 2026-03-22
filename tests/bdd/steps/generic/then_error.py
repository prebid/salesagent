"""Then steps for error assertions (failure, error codes, messages, suggestions).

These steps assert on ``ctx["error"]`` which is populated by When steps when
an operation fails. Errors are real exceptions from production code:
    - AdCPError subclasses (have .error_code, .message)
    - pydantic.ValidationError (mapped to VALIDATION_ERROR)
    - Other exceptions
"""

from __future__ import annotations

from typing import Any

from pytest_bdd import parsers, then

# ── Helpers ─────────────────────────────────────────────────────────


def _get_error_code(error: object) -> str:
    """Extract error code from an exception or Error model.

    Handles two patterns:
    1. Exception-based: AdCPError with .error_code
    2. Partial success: adcp.types.Error model with .code (from response.errors)
    """
    from src.core.exceptions import AdCPError

    if isinstance(error, AdCPError):
        # Production code often stores the specific error code in details["error_code"]
        # (e.g., CREATIVES_NOT_FOUND, CREATIVE_FORMAT_MISMATCH) while the exception
        # class has a generic code (NOT_FOUND, VALIDATION_ERROR). Prefer the specific code.
        if error.details and "error_code" in error.details:
            return error.details["error_code"]
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
        # Map to the assertion vocabulary used in feature files
        # Prefer specific code from details["error_code"] over generic class code
        if error.details and "error_code" in error.details:
            d["code"] = error.details["error_code"]
        else:
            d["code"] = d.get("error_code", "")
        if error.details and "suggestion" in error.details:
            d["suggestion"] = error.details["suggestion"]
        return d
    # adcp.types.Error model (from response.errors promotion in When steps)
    if hasattr(error, "code") and hasattr(error, "message") and not isinstance(error, Exception):
        d: dict[str, Any] = {"code": error.code, "message": error.message}
        if getattr(error, "suggestion", None):
            d["suggestion"] = error.suggestion
        if getattr(error, "recovery", None):
            d["recovery"] = error.recovery
        return d
    return {"code": _get_error_code(error), "message": _get_error_message(error)}


# ── Operation failure ────────────────────────────────────────────────


@then("the operation should fail")
def then_operation_fails(ctx: dict) -> None:
    """Assert the operation resulted in an error.

    Checks two patterns:
    1. Exception-based: ctx["error"] set by dispatch on exception
    2. Partial success: response.errors non-empty (UC-004 delivery pattern)
    """
    if "error" in ctx:
        return  # Exception-based error — OK
    resp = ctx.get("response")
    if resp is not None and hasattr(resp, "errors") and resp.errors:
        # Promote the first response error to ctx["error"] so downstream
        # Then steps (error_code, error_message) can find it.
        ctx["error"] = resp.errors[0]
        return
    raise AssertionError("Expected an error but none was recorded in ctx")


# ── Error code ───────────────────────────────────────────────────────


@then(parsers.parse('the error code should be "{code}"'))
def then_error_code(ctx: dict, code: str) -> None:
    """Assert the error code matches."""
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
    """Assert error suggestion contains the given text (case-insensitive)."""
    error = ctx.get("error")
    assert error is not None, "No error recorded in ctx"
    d = _get_error_dict(error)
    suggestion = (d.get("suggestion") or "").lower()
    assert text.lower() in suggestion, f"Expected '{text}' in suggestion: {d.get('suggestion')}"


# ── Error message content (specific) ───────────────────────────────────


@then("the error message should indicate tenant context could not be determined")
def then_error_tenant_context(ctx: dict) -> None:
    """Assert error message mentions tenant context."""
    error = ctx.get("error")
    assert error is not None, "No error recorded in ctx"
    msg = _get_error_message(error).lower()
    assert "tenant" in msg, f"Expected 'tenant' in error message: {_get_error_message(error)}"


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
    """Assert error message mentions minimum items requirement."""
    error = ctx.get("error")
    assert error is not None, "No error recorded in ctx"
    msg = _get_error_message(error).lower()
    assert "at least 1" in msg or "required" in msg or "ensure this" in msg, (
        f"Expected min-items message: {_get_error_message(error)}"
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
    elif hasattr(error, "recovery") and not isinstance(error, Exception):
        # adcp.types.Error model (from response.errors promotion)
        # recovery may be a Recovery enum — compare by .value
        actual = error.recovery.value if hasattr(error.recovery, "value") else str(error.recovery)
        assert actual == recovery, f"Expected recovery '{recovery}', got '{actual}'"
    else:
        raise AssertionError(f"Cannot check recovery on {type(error).__name__}: no recovery attribute")


@then('the error should include a "suggestion" field')
@then('the error should include "suggestion" field')
def then_error_has_suggestion(ctx: dict) -> None:
    """Assert error includes a suggestion field."""
    error = ctx.get("error")
    assert error is not None, "No error recorded in ctx"
    d = _get_error_dict(error)
    assert "suggestion" in d, f"Expected 'suggestion' in error: {d}"
    assert d["suggestion"], "Expected non-empty suggestion"


@then('the error should include "field" field')
def then_error_has_field(ctx: dict) -> None:
    """Assert error includes a non-None field path.

    The adcp Error model has ``field: str | None`` indicating which request
    field caused the error (e.g. 'packages[0].product_id').
    """
    error = ctx.get("error")
    assert error is not None, "No error recorded in ctx"
    # adcp.types.Error model (from response.errors promotion)
    field_val = getattr(error, "field", None)
    if field_val is None:
        # AdCPError may store field in details
        from src.core.exceptions import AdCPError

        if isinstance(error, AdCPError) and error.details:
            field_val = error.details.get("field")
    assert field_val is not None, f"Expected 'field' on error, got None. Error: {error}"


@then("the error should include a suggestion for how to fix the issue")
def then_error_has_fix_suggestion(ctx: dict) -> None:
    """Assert error includes an actionable suggestion for fixing the issue.

    Unlike then_error_has_suggestion (structural check), this step verifies
    the suggestion contains actionable language (use/try/check/provide/etc.)
    that tells the caller how to correct the problem.
    """
    error = ctx.get("error")
    assert error is not None, "No error recorded in ctx"
    d = _get_error_dict(error)
    assert "suggestion" in d, f"Expected 'suggestion' in error: {d}"
    suggestion = d["suggestion"]
    assert suggestion, "Expected non-empty suggestion"
    # A fix suggestion must contain actionable guidance
    suggestion_lower = suggestion.lower()
    action_words = ("use", "try", "check", "provide", "include", "ensure", "remove", "specify", "set", "omit")
    assert any(word in suggestion_lower for word in action_words), (
        f"Expected actionable fix suggestion (use/try/check/provide/...), got: {suggestion}"
    )


# ── Suggestion content ───────────────────────────────────────────────


@then("the suggestion should advise providing authentication credentials")
def then_suggestion_auth(ctx: dict) -> None:
    """Assert suggestion mentions authentication credentials."""
    d = _get_error_dict(ctx.get("error"))
    suggestion = (d.get("suggestion") or "").lower()
    assert "credential" in suggestion or "auth" in suggestion, f"Expected auth suggestion: {d.get('suggestion')}"


@then("the suggestion should provide valid parameter values")
def then_suggestion_valid_values(ctx: dict) -> None:
    """Assert suggestion references valid parameter values, not just generic text."""
    d = _get_error_dict(ctx.get("error"))
    suggestion = d.get("suggestion", "")
    assert suggestion, "Expected non-empty suggestion"
    suggestion_lower = suggestion.lower()
    assert any(kw in suggestion_lower for kw in ("valid", "allowed", "values", "accepted", "supported")), (
        f"Expected suggestion to reference valid parameter values, got: {suggestion}"
    )


@then("the suggestion should advise using valid DisclosurePosition enum values")
def then_suggestion_disclosure_enum(ctx: dict) -> None:
    """Assert suggestion mentions valid DisclosurePosition values."""
    d = _get_error_dict(ctx.get("error"))
    suggestion = (d.get("suggestion") or "").lower()
    assert "disclosureposition" in suggestion or "enum" in suggestion or "valid" in suggestion, (
        f"Expected DisclosurePosition suggestion: {d.get('suggestion')}"
    )


@then("the suggestion should advise providing at least one position or omitting the filter")
def then_suggestion_positions_or_omit(ctx: dict) -> None:
    """Assert suggestion advises providing positions or omitting."""
    d = _get_error_dict(ctx.get("error"))
    suggestion = (d.get("suggestion") or "").lower()
    assert "position" in suggestion or "omit" in suggestion, f"Expected position/omit suggestion: {d.get('suggestion')}"


@then("the suggestion should advise removing duplicate positions")
def then_suggestion_remove_dupes(ctx: dict) -> None:
    """Assert suggestion advises removing duplicates."""
    d = _get_error_dict(ctx.get("error"))
    suggestion = (d.get("suggestion") or "").lower()
    assert "duplicate" in suggestion or "remove" in suggestion, (
        f"Expected duplicate removal suggestion: {d.get('suggestion')}"
    )


@then("the suggestion should advise providing at least one FormatId or omitting the filter")
def then_suggestion_format_id_or_omit(ctx: dict) -> None:
    """Assert suggestion advises providing FormatId or omitting."""
    d = _get_error_dict(ctx.get("error"))
    suggestion = (d.get("suggestion") or "").lower()
    assert "formatid" in suggestion or "omit" in suggestion, f"Expected FormatId/omit suggestion: {d.get('suggestion')}"


@then("the suggestion should advise including agent_url (URI) and id fields")
def then_suggestion_agent_url_id(ctx: dict) -> None:
    """Assert suggestion advises including both agent_url AND id fields."""
    d = _get_error_dict(ctx.get("error"))
    suggestion = d.get("suggestion", "")
    assert suggestion, "Expected non-empty suggestion"
    suggestion_lower = suggestion.lower()
    assert "agent_url" in suggestion_lower or "uri" in suggestion_lower, (
        f"Expected agent_url/URI in suggestion: {suggestion}"
    )
    assert "id" in suggestion_lower, f"Expected 'id' field reference in suggestion: {suggestion}"


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
    """Assert response indicates a validation error."""
    error = ctx.get("error")
    assert error is not None, "Expected a validation error"
    assert _get_error_code(error) == "VALIDATION_ERROR", f"Expected VALIDATION_ERROR, got {_get_error_code(error)}"


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
