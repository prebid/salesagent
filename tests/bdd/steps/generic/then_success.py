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
    """Assert response contains a non-empty array field.

    Scenarios using this step have Given preconditions that guarantee data
    exists (e.g., registered creative agents), so an empty list is a failure.
    """
    resp = ctx.get("response")
    assert resp is not None, "Expected a response but none found"
    value = getattr(resp, field, None)
    assert value is not None, f"Expected '{field}' in response, got attrs: {dir(resp)}"
    assert isinstance(value, list), f"Expected '{field}' to be a list, got {type(value)}"
    assert len(value) > 0, f"Expected non-empty '{field}' array — Given step guarantees data exists"


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

    Subsystem-aware: checks the appropriate mock depending on which
    harness environment is active (delivery adapter for UC-004,
    creative registry for UC-005).
    """
    env = ctx["env"]

    # Check delivery adapter mock (UC-004 delivery scenarios)
    adapter_mock = env.mock.get("adapter")
    if adapter_mock is not None:
        mock_adapter = adapter_mock.return_value
        if hasattr(mock_adapter, "get_media_buy_delivery"):
            assert mock_adapter.get_media_buy_delivery.called, (
                "Adapter mock was not called — delivery metrics may have hit a real ad server"
            )
            return

    # Check creative registry mock (UC-005 creative format scenarios)
    registry_mock = env.mock.get("registry")
    if registry_mock is not None:
        mock_registry = registry_mock.return_value
        formats_called = mock_registry.list_all_formats.called or mock_registry.list_all_formats_with_errors.called
        assert formats_called, "Mock registry was not called — real API calls may have been made"
        return

    # Neither mock found — the harness isn't configured for sandbox testing
    raise AssertionError(
        "No adapter or registry mock found in harness — cannot verify sandbox isolation. "
        f"Available mocks: {list(env.mock.keys())}"
    )
