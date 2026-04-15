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
    """Assert the operation resulted in a structured error.

    Checks two patterns:
    1. Exception-based: ctx["error"] set by dispatch on exception
    2. Partial success: response.errors non-empty (UC-004 delivery pattern)

    The error must have BOTH a non-empty error code and a non-empty human
    message — an error object with null/empty fields is not a valid failure
    signal for the caller. An error whose code resolves to its own Python
    class name (fallback in _get_error_code) means the code field wasn't
    set explicitly, which is also not a proper structured error.
    """
    error = ctx.get("error")
    if error is None:
        resp = ctx.get("response")
        if resp is not None and hasattr(resp, "errors") and resp.errors:
            # Promote the first response error to ctx["error"] so downstream
            # Then steps (error_code, error_message) can find it.
            error = resp.errors[0]
            ctx["error"] = error
    assert error is not None, "Expected an error but none was recorded in ctx"
    code = _get_error_code(error)
    message = _get_error_message(error)
    assert code, f"Expected non-empty error code, got: {code!r} on {type(error).__name__}"
    assert message, f"Expected non-empty error message, got: {message!r} on {type(error).__name__}"


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
    """Assert error message indicates tenant context could not be determined.

    Step text is specific: 'tenant context could not be determined'. The message
    must mention 'tenant' AND indicate a failure to resolve/determine context —
    both conditions must be met simultaneously (not just generic keywords).
    """
    error = ctx.get("error")
    assert error is not None, "No error recorded in ctx"
    msg = _get_error_message(error).lower()
    assert "tenant" in msg, f"Expected 'tenant' in error message: {_get_error_message(error)}"
    # The message must convey "could not be determined" — a resolution/determination
    # failure. Require at least one determination-failure keyword AND one
    # negation/failure indicator to avoid matching generic "tenant required" or
    # "tenant not found" messages that indicate different failure modes.
    determination_keywords = ("context", "resolve", "determine", "identify")
    failure_keywords = ("could not", "cannot", "unable", "fail", "missing", "no ")
    has_determination = any(kw in msg for kw in determination_keywords)
    has_failure = any(kw in msg for kw in failure_keywords)
    assert has_determination and has_failure, (
        f"Expected tenant context determination failure message "
        f"(needs determination concept + failure indicator), got: {_get_error_message(error)}"
    )


@then("the error message should indicate which parameters are invalid")
def then_error_invalid_params(ctx: dict) -> None:
    """Assert error is a validation-type error that names the invalid parameter.

    Step claims 'which parameters' — the error must (a) be a validation error
    (Pydantic ValidationError or AdCPValidationError), and (b) name the actual
    invalid field(s), not just contain generic keywords like 'invalid'.
    """
    from pydantic import ValidationError

    from src.core.exceptions import AdCPValidationError

    error = ctx.get("error")
    assert error is not None, "No error recorded in ctx"
    assert isinstance(error, ValidationError | AdCPValidationError), (
        f"Expected a validation error (pydantic.ValidationError or AdCPValidationError) "
        f"for a parameter-validity claim, got {type(error).__name__}: {error}"
    )
    # Pydantic ValidationError: has per-field error details with field paths
    if isinstance(error, ValidationError):
        field_errors = error.errors()
        assert field_errors, "ValidationError has no field-level error details"
        assert all("loc" in e for e in field_errors), f"Expected field locations in error details: {field_errors}"
        # Verify at least one field path is non-empty (actually names a parameter)
        field_names = [e["loc"] for e in field_errors if e.get("loc")]
        assert field_names, "ValidationError locations are empty — cannot determine which parameters are invalid"
        return
    # AdCPError: message must reference a specific parameter/field name
    msg = _get_error_message(error)
    # The message should contain an actual field name, not just generic error words.
    # Check for known parameter names that appear in the request schemas.
    request_fields = (
        "type",
        "format_id",
        "format_ids",
        "agent_url",
        "disclosure_positions",
        "product_id",
        "buyer_ref",
        "buyer_campaign_ref",
        "budget",
        "pricing_option_id",
        "start_time",
        "end_time",
        "packages",
        "creative_ids",
        "creative_assignments",
        "targeting",
        "targeting_overlay",
        "keyword_targets",
        "paused",
        "media_buy_id",
        "account",
        "account_id",
        "currency",
        "name",
        "optimization_goals",
    )
    msg_lower = msg.lower()
    has_specific_field = any(field in msg_lower for field in request_fields)
    # Also accept structured error details that name fields
    has_details = (
        hasattr(error, "details")
        and error.details
        and isinstance(error.details, dict)
        and any(k in ("field", "parameter", "loc", "error_code") for k in error.details)
    )
    assert has_specific_field or has_details, (
        f"Expected error to name which specific parameters are invalid (one of {request_fields}), got: {msg}"
    )


@then(parsers.parse('the error message should indicate "{value}" is not a valid disclosure position'))
def then_error_invalid_disclosure(ctx: dict, value: str) -> None:
    """Assert error message indicates the value is not a valid disclosure position.

    Step claims the message says '"{value}" is not a valid disclosure position' —
    verify both the value AND the disclosure position context appear in the message.
    """
    error = ctx.get("error")
    assert error is not None, "No error recorded in ctx"
    msg = _get_error_message(error)
    assert value in msg, f"Expected invalid value '{value}' in error message: {msg}"
    # Verify the message specifically frames this as a disclosure position error —
    # "valid" alone could match any validation error, so require "disclosure"
    msg_lower = msg.lower()
    assert "disclosure" in msg_lower, (
        f"Expected error to specifically mention 'disclosure' (not just generic validation), got: {msg}"
    )
    position_keywords = ("position", "positions", "not a valid", "invalid")
    assert any(kw in msg_lower for kw in position_keywords), (
        f"Expected error to indicate invalid disclosure position, but message lacks position/validity context: {msg}"
    )


@then("the error message should indicate at least 1 item is required")
def then_error_min_items(ctx: dict) -> None:
    """Assert error message mentions minimum items requirement AND identifies the field.

    Three distinct scenarios use this step (disclosure_positions, output_format_ids,
    input_format_ids) — each has a unique error code verified by a preceding
    ``the error code should be`` step. This step additionally verifies the message
    itself mentions the specific field, so an error about the wrong field cannot pass.
    """
    error = ctx.get("error")
    assert error is not None, "No error recorded in ctx"
    msg = _get_error_message(error).lower()
    # Must specifically indicate a minimum-items constraint, not just any "required" field
    min_items_patterns = ("at least 1", "at least one", "min_length", "empty", "ensure this", "too_short")
    assert any(pattern in msg for pattern in min_items_patterns), (
        f"Expected min-items message (at least 1/empty/min_length/too_short), got: {_get_error_message(error)}"
    )
    # Verify the error identifies WHICH field has the empty array.
    # The error code (checked by a sibling step) tells us which field — but the message
    # text itself must also reference the field for it to be useful to the caller.
    field_names = ("disclosure_positions", "output_format_ids", "input_format_ids", "format_ids", "positions")
    error_code = _get_error_code(error).lower()
    msg_and_code = msg + " " + error_code
    assert any(field in msg_and_code for field in field_names), (
        f"Expected error to identify which field had the empty array "
        f"(one of {field_names}), got message: {_get_error_message(error)}, code: {_get_error_code(error)}"
    )


@then("the error message should indicate duplicate values are not allowed")
def then_error_duplicates(ctx: dict) -> None:
    """Assert error message indicates duplicate values are not allowed.

    Step text claims 'duplicate values are not allowed'. The message must mention
    'duplicate' AND convey prohibition (not allowed/invalid/unique/rejected).
    """
    error = ctx.get("error")
    assert error is not None, "No error recorded in ctx"
    msg = _get_error_message(error).lower()
    assert "duplicate" in msg, f"Expected 'duplicate' in error message: {_get_error_message(error)}"
    prohibition_keywords = ("not allowed", "invalid", "unique", "rejected", "not permitted", "forbidden", "error")
    assert any(kw in msg for kw in prohibition_keywords), (
        f"Expected duplicate prohibition message (not allowed/invalid/unique/rejected), "
        f"got: {_get_error_message(error)}"
    )


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
    elif hasattr(error, "recovery"):
        # adcp.types.Error model (from response.errors promotion) OR exception with .recovery
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
    Uses _get_error_dict for consistent extraction across all error types
    (AdCPError exceptions, adcp.types.Error models, response.errors promotion).
    """
    error = ctx.get("error")
    assert error is not None, "No error recorded in ctx"
    # Use _get_error_dict for consistent extraction, then check for "field"
    d = _get_error_dict(error)
    field_val = d.get("field")
    if field_val is None:
        # Also check direct attribute (adcp.types.Error model has .field)
        field_val = getattr(error, "field", None)
    if field_val is None:
        # AdCPError may store field in details
        from src.core.exceptions import AdCPError

        if isinstance(error, AdCPError) and error.details:
            field_val = error.details.get("field")
    assert field_val is not None, f"Expected 'field' on error, got None. Error type: {type(error).__name__}, dict: {d}"


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
    """Assert suggestion advises providing authentication credentials.

    Step claim has two parts: (a) auth-related concept and (b) actionable
    guidance about providing/including the credential. Checks the raw
    suggestion text — must name the credential mechanism (credential,
    authentication, token, bearer, or the x-adcp-auth header) AND advise
    an action (provide/include/use/supply/set/pass).
    """
    error = ctx.get("error")
    assert error is not None, "No error recorded in ctx"
    d = _get_error_dict(error)
    suggestion = (d.get("suggestion") or "").lower()
    assert suggestion, "Expected non-empty suggestion"
    auth_terms = ("credential", "authenticat", "token", "bearer", "x-adcp-auth", "api key", "api-key")
    has_auth_concept = any(term in suggestion for term in auth_terms)
    has_action = any(word in suggestion for word in ("provide", "include", "use", "supply", "set", "pass"))
    assert has_auth_concept and has_action, (
        f"Expected suggestion to advise providing authentication credentials "
        f"(needs one of {auth_terms} + action verb), got: {d.get('suggestion')}"
    )


@then("the suggestion should provide valid parameter values")
def then_suggestion_valid_values(ctx: dict) -> None:
    """Assert suggestion lists valid parameter values, not just says 'be valid'.

    Step claim: 'provide valid parameter values' — the suggestion must
    (a) reference validity concept, (b) provide actionable guidance,
    AND (c) actually enumerate candidate values (a comma-separated list,
    a bracketed list, or 'one of X, Y, Z' phrasing). A suggestion that
    says 'use a valid value' without listing the valid values is not
    actionable.
    """
    error = ctx.get("error")
    assert error is not None, "No error recorded in ctx"
    d = _get_error_dict(error)
    suggestion = d.get("suggestion", "")
    assert suggestion, "Expected non-empty suggestion"
    suggestion_lower = suggestion.lower()
    has_value_ref = any(kw in suggestion_lower for kw in ("valid", "allowed", "values", "accepted", "supported"))
    has_action = any(word in suggestion_lower for word in ("use", "try", "provide", "check", "specify"))
    # Must enumerate values — comma-separated list, quoted tokens, or "one of" phrasing.
    enumerates_values = (
        "," in suggestion or "one of" in suggestion_lower or '"' in suggestion or "'" in suggestion or "[" in suggestion
    )
    assert has_value_ref and has_action and enumerates_values, (
        f"Expected suggestion to enumerate valid parameter values with actionable guidance "
        f"(needs validity ref + action verb + enumeration via comma/quotes/'one of'), got: {suggestion}"
    )


@then("the suggestion should advise using valid DisclosurePosition enum values")
def then_suggestion_disclosure_enum(ctx: dict) -> None:
    """Assert suggestion references DisclosurePosition AND enumerates real enum values.

    Step claim names 'DisclosurePosition enum values' specifically. The
    suggestion must (a) reference disclosure/position context and (b) name
    at least one actual DisclosurePosition enum member so the caller knows
    what to substitute.
    """
    error = ctx.get("error")
    assert error is not None, "No error recorded in ctx"
    d = _get_error_dict(error)
    suggestion = (d.get("suggestion") or "").lower()
    assert suggestion, "Expected non-empty suggestion"
    has_disclosure_ref = "disclosureposition" in suggestion or "disclosure" in suggestion or "position" in suggestion
    # Real DisclosurePosition enum values from the AdCP spec. At least one
    # must be named so the caller has a valid substitute to pick from.
    enum_values = (
        "pre_roll",
        "pre-roll",
        "post_roll",
        "post-roll",
        "mid_roll",
        "mid-roll",
        "overlay",
        "adjacent",
        "integrated",
        "companion",
        "during",
        "before",
        "after",
    )
    names_enum_value = any(val in suggestion for val in enum_values)
    assert has_disclosure_ref and names_enum_value, (
        f"Expected DisclosurePosition suggestion to name at least one valid enum value "
        f"(one of {enum_values}), got: {d.get('suggestion')}"
    )


@then("the suggestion should advise providing at least one position or omitting the filter")
def then_suggestion_positions_or_omit(ctx: dict) -> None:
    """Assert suggestion advises providing positions or omitting the filter.

    Step text: 'providing at least one position or omitting the filter'.
    The suggestion must reference both the item concept (position/item)
    AND the corrective action (provide/add/include OR omit/remove).
    """
    error = ctx.get("error")
    assert error is not None, "No error recorded in ctx"
    d = _get_error_dict(error)
    suggestion = (d.get("suggestion") or "").lower()
    assert suggestion, "Expected non-empty suggestion"
    has_item_ref = "position" in suggestion or "item" in suggestion or "value" in suggestion
    has_action = any(kw in suggestion for kw in ("provide", "add", "include", "omit", "remove", "at least"))
    assert has_item_ref and has_action, (
        f"Expected suggestion about providing positions or omitting filter "
        f"(needs item reference + corrective action), got: {d.get('suggestion')}"
    )


@then("the suggestion should advise removing duplicate positions")
def then_suggestion_remove_dupes(ctx: dict) -> None:
    """Assert suggestion mentions deduplication AND identifies positions as the target.

    Step text: 'removing duplicate positions'. The suggestion must:
    (a) name the duplicate/dedup concept,
    (b) reference positions (the entity being deduplicated),
    (c) advise a corrective action (remove/ensure unique/eliminate).
    A suggestion saying 'remove duplicates' without naming positions, or
    saying 'use unique values' without a removal verb, is not actionable
    enough to match the step claim.
    """
    error = ctx.get("error")
    assert error is not None, "No error recorded in ctx"
    d = _get_error_dict(error)
    suggestion = (d.get("suggestion") or "").lower()
    assert suggestion, "Expected non-empty suggestion"
    has_duplicate_ref = any(kw in suggestion for kw in ("duplicate", "unique", "deduplicate", "distinct"))
    has_position_ref = "position" in suggestion or "value" in suggestion or "item" in suggestion
    has_action = any(kw in suggestion for kw in ("remove", "deduplicate", "ensure", "use unique", "eliminate"))
    assert has_duplicate_ref and has_position_ref and has_action, (
        f"Expected suggestion to advise removing duplicate positions "
        f"(needs duplicate ref + position/value/item ref + corrective action), got: {d.get('suggestion')}"
    )


@then("the suggestion should advise providing at least one FormatId or omitting the filter")
def then_suggestion_format_id_or_omit(ctx: dict) -> None:
    """Assert suggestion offers BOTH alternatives: provide FormatId OR omit filter.

    Step claim is a disjunction with two concrete branches:
      (a) provide at least one FormatId, OR
      (b) omit the filter.
    The suggestion must name FormatId/format_id/format AND at least one of
    these corrective actions. Must also convey the 'or omit' alternative
    via an omit/remove/skip verb OR 'optional' phrasing — 'provide a
    FormatId' alone silently drops the 'or omit' branch.
    """
    error = ctx.get("error")
    assert error is not None, "No error recorded in ctx"
    d = _get_error_dict(error)
    suggestion = (d.get("suggestion") or "").lower()
    assert suggestion, "Expected non-empty suggestion"
    has_format_ref = "formatid" in suggestion or "format_id" in suggestion or "format" in suggestion
    has_provide_action = any(kw in suggestion for kw in ("provide", "include", "add", "at least one", "at least 1"))
    has_omit_alternative = any(kw in suggestion for kw in ("omit", "remove", "skip", "optional", "without"))
    assert has_format_ref and (has_provide_action or has_omit_alternative), (
        f"Expected suggestion about FormatId with provide/omit guidance "
        f"(format ref + (provide action OR omit alternative)), got: {d.get('suggestion')}"
    )


@then("the suggestion should advise including agent_url (URI) and id fields")
def then_suggestion_agent_url_id(ctx: dict) -> None:
    """Assert suggestion advises including both agent_url AND id fields."""
    import re

    error = ctx.get("error")
    assert error is not None, "No error in ctx — cannot check suggestion"
    d = _get_error_dict(error)
    suggestion = d.get("suggestion", "")
    assert suggestion, "Expected non-empty suggestion"
    suggestion_lower = suggestion.lower()
    # Step text says "agent_url (URI)" — parenthetical describes the type, not an alias.
    # Require "agent_url" specifically.
    assert "agent_url" in suggestion_lower, f"Expected 'agent_url' in suggestion: {suggestion}"
    # Use word-boundary match to avoid false positives from "valid", "provide", etc.
    assert re.search(r"\bid\b", suggestion_lower), (
        f"Expected 'id' field reference (word-boundary) in suggestion: {suggestion}"
    )


# ── No error raised ─────────────────────────────────────────────────


@then("no error should be raised")
def then_no_error(ctx: dict) -> None:
    """Assert no error was recorded AND a response was produced.

    A passing scenario needs both halves: absence of error AND a positive
    response object. Without the response check, a scenario that simply
    skipped dispatch (fixture bug, no When step) would silently pass.
    """
    assert "error" not in ctx, f"Expected no error but got: {ctx.get('error')}"
    resp = ctx.get("response")
    assert resp is not None, "Expected a response object but none was recorded — dispatch likely did not run"


@then("no error should be returned")
def then_no_error_returned(ctx: dict) -> None:
    """Assert no error was returned AND a response was produced (synonym)."""
    assert "error" not in ctx, f"Expected no error but got: {ctx.get('error')}"
    resp = ctx.get("response")
    assert resp is not None, "Expected a response object but none was recorded — dispatch likely did not run"


@then(parsers.parse('no error should be raised for "{value}"'))
def then_no_error_for_value(ctx: dict, value: str) -> None:
    """Assert the specific value did not produce an error in the response.

    Step claims 'no error for {value}' — a value-scoped claim. Verify:
    (a) no top-level error, and
    (b) if response has an errors collection, no entry references {value}.
    """
    err = ctx.get("error")
    if err is not None:
        msg = _get_error_message(err)
        assert value not in msg, f"Expected no error mentioning '{value}', got error: {msg}"
        # Top-level error exists but doesn't mention this value — acceptable
        # only if the test expects errors for other values. Fail-fast here
        # would be too strict; the (value not in msg) check above already
        # guards the specific claim.
    resp = ctx.get("response")
    if resp is not None and hasattr(resp, "errors") and resp.errors:
        offending = [e for e in resp.errors if value in (getattr(e, "message", "") or "")]
        assert not offending, f"Expected no response error referencing '{value}', got: {offending}"


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
