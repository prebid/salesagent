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
    """Assert suggestion mentions authentication credentials."""
    d = _get_error_dict(ctx.get("error"))
    suggestion = (d.get("suggestion") or "").lower()
    assert "credential" in suggestion or "auth" in suggestion, f"Expected auth suggestion: {d.get('suggestion')}"


@then("the suggestion should provide valid parameter values")
def then_suggestion_valid_values(ctx: dict) -> None:
    """Assert suggestion provides valid parameter values — must reference both validity AND values."""
    d = _get_error_dict(ctx.get("error"))
    suggestion = d.get("suggestion", "")
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
    """Assert suggestion mentions both DisclosurePosition AND valid values."""
    d = _get_error_dict(ctx.get("error"))
    suggestion = (d.get("suggestion") or "").lower()
    # Gherkin requires both concepts: "DisclosurePosition" AND "valid enum values"
    assert (
        "disclosureposition" in suggestion or "disclosure_position" in suggestion or "disclosure position" in suggestion
    ), f"Expected 'DisclosurePosition' in suggestion: {d.get('suggestion')}"
    assert "valid" in suggestion or "allowed" in suggestion or "enum" in suggestion, (
        f"Expected valid/allowed/enum values language in suggestion: {d.get('suggestion')}"
    )


@then("the suggestion should advise providing at least one position or omitting the filter")
def then_suggestion_positions_or_omit(ctx: dict) -> None:
    """Assert suggestion advises providing positions OR omitting the filter.

    Gherkin describes two alternatives — the suggestion should mention at least
    one alternative completely (position + provide/add, or omit/remove).
    """
    d = _get_error_dict(ctx.get("error"))
    suggestion = (d.get("suggestion") or "").lower()
    has_provide_position = "position" in suggestion and any(
        w in suggestion for w in ("provide", "add", "include", "at least")
    )
    has_omit = "omit" in suggestion or "remove" in suggestion
    assert has_provide_position or has_omit, (
        f"Expected suggestion to advise providing positions or omitting filter: {d.get('suggestion')}"
    )


@then("the suggestion should advise removing duplicate positions")
def then_suggestion_remove_dupes(ctx: dict) -> None:
    """Assert suggestion advises removing duplicates — both concepts required."""
    d = _get_error_dict(ctx.get("error"))
    suggestion = (d.get("suggestion") or "").lower()
    # Gherkin says "removing duplicate" — both concepts must appear
    assert "duplicate" in suggestion, f"Expected 'duplicate' in suggestion: {d.get('suggestion')}"
    assert any(w in suggestion for w in ("remove", "deduplicate", "dedup", "eliminate")), (
        f"Expected removal action in suggestion: {d.get('suggestion')}"
    )


@then("the suggestion should advise providing at least one FormatId or omitting the filter")
def then_suggestion_format_id_or_omit(ctx: dict) -> None:
    """Assert suggestion advises providing FormatId OR omitting the filter.

    Same pattern as positions_or_omit — one complete alternative required.
    """
    d = _get_error_dict(ctx.get("error"))
    suggestion = (d.get("suggestion") or "").lower()
    has_provide_format = ("formatid" in suggestion or "format_id" in suggestion or "format id" in suggestion) and any(
        w in suggestion for w in ("provide", "add", "include", "at least")
    )
    has_omit = "omit" in suggestion or "remove" in suggestion
    assert has_provide_format or has_omit, (
        f"Expected suggestion to advise providing FormatId or omitting filter: {d.get('suggestion')}"
    )


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
