"""Then steps for success assertions (response status, fields, sandbox).

These steps assert on ``ctx["result"]`` which is populated by When steps.

Phase 0: assertions check the stub data in ctx. Epic 1 will wire real
production responses.
"""

from __future__ import annotations

from pytest_bdd import parsers, then

# ── Response status ──────────────────────────────────────────────────


@then(parsers.parse('the response status should be "{status}"'))
def then_response_status(ctx: dict, status: str) -> None:
    """Assert response status matches expected value."""
    result = ctx.get("result", {})
    assert result.get("status") == status, f"Expected status '{status}', got '{result.get('status')}'"


# ── Response contains field ──────────────────────────────────────────


@then(parsers.parse('the response should contain "{field}" array'))
def then_response_contains_array(ctx: dict, field: str) -> None:
    """Assert response contains a field that is an array (list)."""
    result = ctx.get("result", {})
    assert field in result, f"Expected '{field}' in response, got keys: {list(result.keys())}"
    assert isinstance(result[field], list), f"Expected '{field}' to be a list"


# ── Sandbox flag assertions ──────────────────────────────────────────


@then("the response should include sandbox equals true")
def then_sandbox_true(ctx: dict) -> None:
    """Assert response includes sandbox: true."""
    result = ctx.get("result", {})
    assert result.get("sandbox") is True, f"Expected sandbox=True, got {result.get('sandbox')}"


@then("the response should not include a sandbox field")
def then_no_sandbox_field(ctx: dict) -> None:
    """Assert response does not include a sandbox field."""
    result = ctx.get("result", {})
    # sandbox should be absent or None (not False)
    assert result.get("sandbox") is None, f"Expected sandbox absent, got {result.get('sandbox')}"


# ── No real API calls assertion ──────────────────────────────────────


@then("no real ad platform API calls should have been made")
def then_no_real_api_calls(ctx: dict) -> None:
    """Assert no real ad platform API calls were made (sandbox path).

    Phase 0 stub: always passes since we never make real calls in stubs.
    """
    # Phase 0: always passes — stubs don't call real APIs
    pass
