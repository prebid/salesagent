"""Then steps for success assertions (response status, fields, sandbox).

These steps assert on ``ctx["response"]`` which holds a real response
object from production code (any use case).
"""

from __future__ import annotations

from pytest_bdd import parsers, then

# ── Response status ──────────────────────────────────────────────────


# Success collections that prove a status-less response is genuinely
# "completed" — at least one must be present and a list, not None.
_STATUSLESS_SUCCESS_ATTRS: tuple[str, ...] = (
    "formats",  # ListCreativeFormatsResponse
    "media_buy_deliveries",  # GetMediaBuyDeliveryResponse
    "aggregated_totals",  # GetMediaBuyDeliveryResponse
)


@then(parsers.parse('the response status should be "{status}"'))
def then_response_status(ctx: dict, status: str) -> None:
    """Assert the operation completed with expected status.

    Works across use cases:
    - UC-004 (GetMediaBuyDeliveryResponse): has explicit ``status`` field —
      assert it equals the expected value directly.
    - UC-005 (ListCreativeFormatsResponse): no ``status`` field. Such
      response types can only represent the *completed* state, so
      "completed" is proven by (a) no error recorded for the operation and
      (b) the schema-required success payload being present. Any requested
      status other than "completed" against a status-less response fails.
    """
    resp = ctx.get("response")
    assert resp is not None, "Expected a response but none found"

    # If response has an explicit status field, check it directly.
    if hasattr(resp, "status"):
        actual = resp.status
        assert actual == status, f"Expected status '{status}', got '{actual}'"
        return

    # Status-less response: only the completed/success state is representable.
    if status != "completed":
        raise AssertionError(
            f"Status '{status}' requested but response {type(resp).__name__} "
            f"has no status field — status-less responses can only be 'completed'"
        )

    # "completed" must be proven, not assumed from a non-None object.
    error = ctx.get("error")
    assert error is None, f"Status 'completed' claimed but the operation recorded an error: {error!r}"

    present = [a for a in _STATUSLESS_SUCCESS_ATTRS if hasattr(resp, a)]
    assert present, (
        f"Status-less response {type(resp).__name__} exposes none of the "
        f"expected success collections {_STATUSLESS_SUCCESS_ATTRS} — cannot "
        f"prove the operation completed successfully"
    )
    for attr in present:
        value = getattr(resp, attr)
        assert value is not None, (
            f"Status 'completed' claimed but response.{attr} is None — the schema-required success payload is missing"
        )
        if attr in ("formats", "media_buy_deliveries"):
            assert isinstance(value, list), (
                f"Status 'completed' claimed but response.{attr} is {type(value).__name__}, expected a list"
            )


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
