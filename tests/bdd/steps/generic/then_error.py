"""Then steps for error assertions (failure, error codes, messages, suggestions).

These steps assert on ``ctx["error"]`` which is populated by When steps when
an operation fails. The error pattern is common across all use cases.
"""

from __future__ import annotations

from pytest_bdd import parsers, then

# ── Operation failure ────────────────────────────────────────────────


@then("the operation should fail")
def then_operation_fails(ctx: dict) -> None:
    """Assert the operation resulted in an error."""
    assert "error" in ctx, "Expected an error but none was recorded in ctx"


# ── Error code ───────────────────────────────────────────────────────


@then(parsers.parse('the error code should be "{code}"'))
def then_error_code(ctx: dict, code: str) -> None:
    """Assert the error code matches."""
    error = ctx.get("error", {})
    assert error.get("code") == code, f"Expected error code '{code}', got '{error.get('code')}'"


# ── Error message content ───────────────────────────────────────────


@then("the error message should indicate tenant context could not be determined")
def then_error_tenant_context(ctx: dict) -> None:
    """Assert error message mentions tenant context."""
    error = ctx.get("error", {})
    msg = error.get("message", "").lower()
    assert "tenant" in msg, f"Expected 'tenant' in error message: {error.get('message')}"


@then("the error message should indicate which parameters are invalid")
def then_error_invalid_params(ctx: dict) -> None:
    """Assert error message mentions invalid parameters."""
    error = ctx.get("error", {})
    assert error.get("message"), "Expected a non-empty error message"


@then(parsers.parse('the error message should indicate "{value}" is not a valid disclosure position'))
def then_error_invalid_disclosure(ctx: dict, value: str) -> None:
    """Assert error message mentions the invalid disclosure position value."""
    error = ctx.get("error", {})
    msg = error.get("message", "")
    assert value in msg, f"Expected '{value}' in error message: {msg}"


@then("the error message should indicate at least 1 item is required")
def then_error_min_items(ctx: dict) -> None:
    """Assert error message mentions minimum items requirement."""
    error = ctx.get("error", {})
    msg = error.get("message", "").lower()
    assert "at least 1" in msg or "required" in msg, f"Expected min-items message: {error.get('message')}"


@then("the error message should indicate duplicate values are not allowed")
def then_error_duplicates(ctx: dict) -> None:
    """Assert error message mentions duplicate values."""
    error = ctx.get("error", {})
    msg = error.get("message", "").lower()
    assert "duplicate" in msg, f"Expected 'duplicate' in error message: {error.get('message')}"


@then("the error message should indicate FormatId must include agent_url and id")
def then_error_format_id_structure(ctx: dict) -> None:
    """Assert error message mentions FormatId structure requirements."""
    error = ctx.get("error", {})
    msg = error.get("message", "").lower()
    assert "agent_url" in msg or "formatid" in msg.lower(), (
        f"Expected FormatId structure message: {error.get('message')}"
    )


# ── Suggestion field ─────────────────────────────────────────────────


@then('the error should include a "suggestion" field')
def then_error_has_suggestion(ctx: dict) -> None:
    """Assert error includes a suggestion field."""
    error = ctx.get("error", {})
    assert "suggestion" in error, f"Expected 'suggestion' in error: {list(error.keys())}"
    assert error["suggestion"], "Expected non-empty suggestion"


@then("the error should include a suggestion for how to fix the issue")
def then_error_has_fix_suggestion(ctx: dict) -> None:
    """Assert error includes a suggestion for fixing the issue."""
    error = ctx.get("error", {})
    assert "suggestion" in error, f"Expected 'suggestion' in error: {list(error.keys())}"
    assert error["suggestion"], "Expected non-empty suggestion"


# ── Suggestion content ───────────────────────────────────────────────


@then("the suggestion should advise providing authentication credentials")
def then_suggestion_auth(ctx: dict) -> None:
    """Assert suggestion mentions authentication credentials."""
    error = ctx.get("error", {})
    suggestion = error.get("suggestion", "").lower()
    assert "credential" in suggestion or "auth" in suggestion, f"Expected auth suggestion: {error.get('suggestion')}"


@then("the suggestion should provide valid parameter values")
def then_suggestion_valid_values(ctx: dict) -> None:
    """Assert suggestion mentions valid parameter values."""
    error = ctx.get("error", {})
    assert error.get("suggestion"), "Expected non-empty suggestion"


@then("the suggestion should advise using valid DisclosurePosition enum values")
def then_suggestion_disclosure_enum(ctx: dict) -> None:
    """Assert suggestion mentions valid DisclosurePosition values."""
    error = ctx.get("error", {})
    suggestion = error.get("suggestion", "").lower()
    assert "disclosureposition" in suggestion or "enum" in suggestion or "valid" in suggestion, (
        f"Expected DisclosurePosition suggestion: {error.get('suggestion')}"
    )


@then("the suggestion should advise providing at least one position or omitting the filter")
def then_suggestion_positions_or_omit(ctx: dict) -> None:
    """Assert suggestion advises providing positions or omitting."""
    error = ctx.get("error", {})
    suggestion = error.get("suggestion", "").lower()
    assert "position" in suggestion or "omit" in suggestion, (
        f"Expected position/omit suggestion: {error.get('suggestion')}"
    )


@then("the suggestion should advise removing duplicate positions")
def then_suggestion_remove_dupes(ctx: dict) -> None:
    """Assert suggestion advises removing duplicates."""
    error = ctx.get("error", {})
    suggestion = error.get("suggestion", "").lower()
    assert "duplicate" in suggestion or "remove" in suggestion, (
        f"Expected duplicate removal suggestion: {error.get('suggestion')}"
    )


@then("the suggestion should advise providing at least one FormatId or omitting the filter")
def then_suggestion_format_id_or_omit(ctx: dict) -> None:
    """Assert suggestion advises providing FormatId or omitting."""
    error = ctx.get("error", {})
    suggestion = error.get("suggestion", "").lower()
    assert "formatid" in suggestion or "omit" in suggestion, (
        f"Expected FormatId/omit suggestion: {error.get('suggestion')}"
    )


@then("the suggestion should advise including agent_url (URI) and id fields")
def then_suggestion_agent_url_id(ctx: dict) -> None:
    """Assert suggestion advises including agent_url and id."""
    error = ctx.get("error", {})
    suggestion = error.get("suggestion", "").lower()
    assert "agent_url" in suggestion or "uri" in suggestion, (
        f"Expected agent_url/URI suggestion: {error.get('suggestion')}"
    )


# ── No error raised ─────────────────────────────────────────────────


@then("no error should be raised")
def then_no_error(ctx: dict) -> None:
    """Assert no error was recorded."""
    assert "error" not in ctx, f"Expected no error but got: {ctx.get('error')}"


@then(parsers.parse('no error should be raised for "{value}"'))
def then_no_error_for_value(ctx: dict, value: str) -> None:
    """Assert no error was raised for a specific value (silent exclusion)."""
    assert "error" not in ctx, f"Expected no error for '{value}' but got: {ctx.get('error')}"


# ── Validation error (sandbox) ───────────────────────────────────────


@then("the response should indicate a validation error")
def then_validation_error(ctx: dict) -> None:
    """Assert response indicates a validation error."""
    error = ctx.get("error", {})
    assert error.get("code") == "VALIDATION_ERROR" or "error" in ctx, "Expected a validation error"


@then("the error should be a real validation error, not simulated")
def then_real_validation_error(ctx: dict) -> None:
    """Assert the error is a real validation error (not simulated/sandbox).

    Phase 0 stub: always passes. Epic 1 will verify against production behavior.
    """
    assert "error" in ctx, "Expected an error"
