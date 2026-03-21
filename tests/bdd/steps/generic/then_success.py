"""Then steps for success assertions (response status, fields, sandbox).

These steps assert on ``ctx["response"]`` which holds a real response
object from production code (any use case).
"""

from __future__ import annotations

from pytest_bdd import parsers, then

# ── Response status ──────────────────────────────────────────────────


@then(parsers.parse('the response status should be "{status}"'))
def then_response_status(ctx: dict, status: str) -> None:
    """Assert the operation completed with expected status.

    Works across use cases:
    - UC-005 (ListCreativeFormatsResponse): no status field, presence = completed
    - UC-004 (GetMediaBuyDeliveryResponse): has explicit status field
    """
    resp = ctx.get("response")
    assert resp is not None, "Expected a response but none found"

    # If response has an explicit status field, check it directly
    if hasattr(resp, "status"):
        actual = resp.status
        assert actual == status, f"Expected status '{status}', got '{actual}'"
        return

    # UC-005 fallback: presence of response with expected fields = completed
    if status == "completed":
        return

    raise AssertionError(f"Unknown status '{status}' — response has no status field")


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
    """Assert serialized response does not contain a sandbox field.

    Checks model_dump() (what API consumers see), not the Python attribute.
    A field present with value None still serializes as ``{"sandbox": null}``
    which counts as "including a sandbox field".
    """
    resp = ctx.get("response")
    assert resp is not None, "Expected a response but none found"
    dumped = resp.model_dump()
    assert "sandbox" not in dumped, (
        f"Expected no sandbox field in serialized response, got sandbox={dumped.get('sandbox')}"
    )


# ── No real API calls assertion ──────────────────────────────────────


@then("no real ad platform API calls should have been made")
def then_no_real_api_calls(ctx: dict) -> None:
    """Assert no real ad platform API calls were made.

    Verifies the mock registry was used instead of real HTTP calls.
    The harness patches ``get_creative_agent_registry`` — if production
    code called it, it got the mock and no real API calls occurred.
    """
    env = ctx["env"]
    assert env is not None, "Expected harness env in ctx — without the harness, real API calls could occur"
    registry_mock = env.mock.get("registry")
    assert registry_mock is not None, "Registry mock not configured in harness"
    # If a response exists, production ran the impl — verify it used the mock
    if "response" in ctx:
        mock_registry = registry_mock.return_value
        formats_called = mock_registry.list_all_formats.called or mock_registry.list_all_formats_with_errors.called
        assert formats_called, (
            "Production code returned a response but did not call the mock registry — real API calls may have been made"
        )
