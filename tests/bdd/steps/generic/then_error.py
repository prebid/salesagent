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


def _get_error_code(error: Exception) -> str:
    """Extract error code from a real exception."""
    from src.core.exceptions import AdCPError

    if isinstance(error, AdCPError):
        return error.error_code
    # Pydantic ValidationError → VALIDATION_ERROR
    try:
        from pydantic import ValidationError

        if isinstance(error, ValidationError):
            return "VALIDATION_ERROR"
    except ImportError:
        pass
    return type(error).__name__


def _get_error_message(error: Exception) -> str:
    """Extract human-readable message from a real exception."""
    from src.core.exceptions import AdCPError

    if isinstance(error, AdCPError):
        return error.message
    return str(error)


def _get_error_dict(error: Exception) -> dict:
    """Convert exception to dict for field-presence checks."""
    from src.core.exceptions import AdCPError

    if isinstance(error, AdCPError):
        d = error.to_dict()
        # AdCPError.to_dict() has: error_code, message, recovery, details
        # Map to the assertion vocabulary used in feature files
        d["code"] = d.get("error_code", "")
        if error.details and "suggestion" in error.details:
            d["suggestion"] = error.details["suggestion"]
        return d
    return {"code": _get_error_code(error), "message": _get_error_message(error)}


# ── Operation failure ────────────────────────────────────────────────


@then("the operation should fail")
def then_operation_fails(ctx: dict) -> None:
    """Assert the operation resulted in an error."""
    assert "error" in ctx, "Expected an error but none was recorded in ctx"


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
    """Assert error message mentions tenant context resolution failure."""
    error = ctx.get("error")
    assert error is not None, "No error recorded in ctx"
    msg = _get_error_message(error).lower()
    assert "tenant" in msg, f"Expected 'tenant' in error message: {_get_error_message(error)}"
    context_phrases = ("could not", "cannot", "not be determined", "not found", "unknown", "missing", "invalid")
    assert any(phrase in msg for phrase in context_phrases), (
        f"Expected tenant context resolution failure (could not/cannot/not found/...), got: {_get_error_message(error)}"
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
    # AdCPError: message must reference SPECIFIC parameter/field names (not just generic keywords)
    from src.core.exceptions import AdCPError

    msg = _get_error_message(error)
    msg_lower = msg.lower()
    if isinstance(error, AdCPError) and error.details:
        # If error has details, they must contain parameter-specific info
        details_str = str(error.details).lower()
        has_param_info = any(kw in details_str for kw in ("parameter", "field", "format_id", "agent_url", "id", "url"))
        assert has_param_info, f"AdCPError.details should identify which parameters are invalid: {error.details}"
    # Message must contain a specific parameter name — not just the generic word "invalid"
    specific_params = ("format_id", "agent_url", "disclosure", "position", "budget", "pricing")
    generic_indicators = ("parameter", "field")
    has_specific = any(p in msg_lower for p in specific_params)
    has_generic_with_context = any(g in msg_lower for g in generic_indicators) and (
        "invalid" in msg_lower or "missing" in msg_lower or "required" in msg_lower
    )
    assert has_specific or has_generic_with_context, (
        f"Expected error to identify specific invalid parameters (not just 'invalid'), got: {msg}"
    )


@then(parsers.parse('the error message should indicate "{value}" is not a valid disclosure position'))
def then_error_invalid_disclosure(ctx: dict, value: str) -> None:
    """Assert error message mentions the invalid disclosure position value AND disclosure-position context."""
    error = ctx.get("error")
    assert error is not None, "No error recorded in ctx"
    msg = _get_error_message(error)
    msg_lower = msg.lower()
    assert value.lower() in msg_lower, f"Expected '{value}' in error message: {msg}"
    assert "disclosure" in msg_lower or "position" in msg_lower, (
        f"Expected 'disclosure' or 'position' in error message to confirm "
        f"disclosure-position invalidity context, got: {msg}"
    )


@then("the error message should indicate at least 1 item is required")
def then_error_min_items(ctx: dict) -> None:
    """Assert error message mentions minimum items requirement."""
    error = ctx.get("error")
    assert error is not None, "No error recorded in ctx"
    msg = _get_error_message(error).lower()
    assert any(
        phrase in msg for phrase in ("at least 1", "at least one", "minimum", "min_length", "empty", "too_short")
    ), f"Expected min-items message (at least 1/minimum/empty/too_short): {_get_error_message(error)}"


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


@then('the error should include a "suggestion" field')
@then('the error should include "suggestion" field')
def then_error_has_suggestion(ctx: dict) -> None:
    """Assert error includes a suggestion field."""
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
    error = ctx.get("error")
    assert error is not None, "No error recorded in ctx"
    d = _get_error_dict(error)
    suggestion = (d.get("suggestion") or "").lower()
    assert "credential" in suggestion or "authentication" in suggestion, (
        f"Expected auth suggestion (credential/authentication): {d.get('suggestion')}"
    )


@then("the suggestion should provide valid parameter values")
def then_suggestion_valid_values(ctx: dict) -> None:
    """Assert suggestion references valid parameter values with specific examples."""
    d = _get_error_dict(ctx.get("error"))
    suggestion = d.get("suggestion", "")
    assert suggestion, "Expected non-empty suggestion"
    suggestion_lower = suggestion.lower()
    # Must reference valid values AND contain specific examples (quoted values, commas, or colons)
    has_value_keyword = any(kw in suggestion_lower for kw in ("valid", "allowed", "accepted", "supported"))
    has_specific_values = "'" in suggestion or '"' in suggestion or "," in suggestion or ":" in suggestion
    assert has_value_keyword and has_specific_values, (
        f"Expected suggestion with valid parameter values (specific examples), got: {suggestion}"
    )


@then("the suggestion should advise using valid DisclosurePosition enum values")
def then_suggestion_disclosure_enum(ctx: dict) -> None:
    """Assert suggestion mentions DisclosurePosition and lists valid enum values."""
    error = ctx.get("error")
    assert error is not None, "No error recorded in ctx"
    d = _get_error_dict(error)
    suggestion = d.get("suggestion", "")
    assert suggestion, "Expected non-empty suggestion"
    suggestion_lower = suggestion.lower()
    # Must mention DisclosurePosition specifically (mandatory, not OR'd with generic terms)
    assert "disclosure" in suggestion_lower, f"Suggestion must reference 'disclosure' concept: {suggestion}"
    # Must reference valid values
    assert "valid" in suggestion_lower or "enum" in suggestion_lower or "values" in suggestion_lower, (
        f"Suggestion must reference valid/enum/values: {suggestion}"
    )
    # Must contain specific value examples (quoted strings or comma-separated list)
    has_examples = "'" in suggestion or '"' in suggestion or "," in suggestion
    assert has_examples, (
        f"Suggestion should list specific valid DisclosurePosition values (with quotes or comma-separated), "
        f"got: {suggestion}"
    )


@then("the suggestion should advise providing at least one position or omitting the filter")
def then_suggestion_positions_or_omit(ctx: dict) -> None:
    """Assert suggestion advises providing positions AND omitting as alternatives."""
    d = _get_error_dict(ctx.get("error"))
    suggestion = (d.get("suggestion") or "").lower()
    assert "position" in suggestion, f"Expected 'position' in suggestion: {d.get('suggestion')}"
    assert "omit" in suggestion, f"Expected 'omit' alternative in suggestion: {d.get('suggestion')}"


@then("the suggestion should advise removing duplicate positions")
def then_suggestion_remove_dupes(ctx: dict) -> None:
    """Assert suggestion advises removing duplicates — both concepts required."""
    error = ctx.get("error")
    assert error is not None, "No error recorded in ctx"
    d = _get_error_dict(error)
    suggestion = (d.get("suggestion") or "").lower()
    # Step says "removing duplicate positions" — both "duplicate" AND "remove" must appear
    assert "duplicate" in suggestion, f"Expected 'duplicate' in suggestion: {d.get('suggestion')}"
    assert "remove" in suggestion, f"Expected 'remove' in suggestion: {d.get('suggestion')}"


@then("the suggestion should advise providing at least one FormatId or omitting the filter")
def then_suggestion_format_id_or_omit(ctx: dict) -> None:
    """Assert suggestion advises both alternatives: provide FormatId AND omit option."""
    error = ctx.get("error")
    assert error is not None, "No error recorded in ctx"
    d = _get_error_dict(error)
    suggestion = (d.get("suggestion") or "").lower()
    # Step says "providing FormatId or omitting" — suggestion must mention BOTH alternatives
    assert "formatid" in suggestion or "format_id" in suggestion or "format id" in suggestion, (
        f"Expected FormatId reference in suggestion: {d.get('suggestion')}"
    )
    assert "omit" in suggestion, f"Expected 'omit' alternative in suggestion: {d.get('suggestion')}"


@then("the suggestion should advise including agent_url (URI) and id fields")
def then_suggestion_agent_url_id(ctx: dict) -> None:
    """Assert suggestion advises including both agent_url AND id fields."""
    import re

    d = _get_error_dict(ctx.get("error"))
    suggestion = d.get("suggestion", "")
    assert suggestion, "Expected non-empty suggestion"
    suggestion_lower = suggestion.lower()
    assert "agent_url" in suggestion_lower or "uri" in suggestion_lower, (
        f"Expected agent_url/URI in suggestion: {suggestion}"
    )
    # Use word boundary to match "id" as a standalone field name, not as substring
    assert re.search(r"\bid\b", suggestion_lower), (
        f"Expected 'id' field reference (word boundary) in suggestion: {suggestion}"
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
