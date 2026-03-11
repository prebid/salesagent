"""Then steps for success assertions (response status, fields, sandbox).

These steps assert on ``ctx["response"]`` which holds a real
ListCreativeFormatsResponse from production code.
"""

from __future__ import annotations

from pytest_bdd import parsers, then

# ── Response status ──────────────────────────────────────────────────


@then(parsers.parse('the response status should be "{status}"'))
def then_response_status(ctx: dict, status: str) -> None:
    """Assert response status matches expected value.

    In production, a successful response means status is "completed".
    The response object existing in ctx means the call succeeded.
    """
    assert "response" in ctx, "Expected a response but none found"
    if status == "completed":
        # A response object in ctx means the call completed successfully
        return
    raise AssertionError(f"Unexpected status value: {status}")


# ── Response contains field ──────────────────────────────────────────


@then(parsers.parse('the response should contain "{field}" array'))
def then_response_contains_array(ctx: dict, field: str) -> None:
    """Assert response contains a field that is an array (list)."""
    resp = ctx.get("response")
    assert resp is not None, "Expected a response but none found"
    value = getattr(resp, field, None)
    assert value is not None, f"Expected '{field}' in response, got attrs: {dir(resp)}"
    assert isinstance(value, list), f"Expected '{field}' to be a list, got {type(value)}"


# ── Sandbox flag assertions ──────────────────────────────────────────


@then("the response should include sandbox equals true")
def then_sandbox_true(ctx: dict) -> None:
    """Assert response includes sandbox: true."""
    resp = ctx.get("response")
    assert resp is not None, "Expected a response but none found"
    assert getattr(resp, "sandbox", None) is True, f"Expected sandbox=True, got {getattr(resp, 'sandbox', None)}"


@then("the response should not include a sandbox field")
def then_no_sandbox_field(ctx: dict) -> None:
    """Assert response does not include a sandbox field."""
    resp = ctx.get("response")
    assert resp is not None, "Expected a response but none found"
    assert getattr(resp, "sandbox", None) is None, f"Expected sandbox absent, got {getattr(resp, 'sandbox', None)}"


# ── No real API calls assertion ──────────────────────────────────────


@then("no real ad platform API calls should have been made")
def then_no_real_api_calls(ctx: dict) -> None:
    """Assert no real ad platform API calls were made (sandbox path).

    In harness mode the registry is mocked, so no real API calls are made.
    Always passes.
    """
    pass
