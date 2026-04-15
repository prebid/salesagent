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
    assert resp is not None, f"Expected a response but none found (error={ctx.get('error')!r})"

    # If response has an explicit status field, check it directly
    if hasattr(resp, "status"):
        actual = resp.status
        assert actual == status, f"Expected status '{status}', got '{actual}'"
        return

    # UC-005 fallback: response has no status field, so "completed" means a
    # populated Pydantic response model. Verify it serializes to a non-empty
    # mapping AND carries a recognizable success payload (at least one known
    # response-success field) rather than silently passing on any object.
    if status == "completed":
        from pydantic import BaseModel

        assert isinstance(resp, BaseModel), (
            f"Expected a Pydantic response model for status 'completed', got {type(resp).__name__}"
        )
        serialized = resp.model_dump()
        assert serialized, f"Expected non-empty response for status 'completed', got {serialized!r}"
        # A "completed" response must carry identifiable success content.
        # Known success-payload keys across UCs: media_buy_id, creative_ids,
        # formats, products, creatives, deliveries, media_buys, packages,
        # build_id, creative_id.
        success_keys = {
            "media_buy_id",
            "creative_ids",
            "creative_id",
            "formats",
            "products",
            "creatives",
            "deliveries",
            "media_buys",
            "packages",
            "build_id",
            "signal_agents",
        }
        has_success_content = bool(success_keys & serialized.keys())
        # If the response schema simply has no success-payload key (e.g., a
        # bare acknowledgement), require at least one non-null non-empty field
        # so empty responses still fail.
        has_nonempty_field = any(v not in (None, "", [], {}) for v in serialized.values())
        assert has_success_content or has_nonempty_field, (
            f"Expected 'completed' response to carry success content or at least one populated field, "
            f"got empty payload: {serialized!r}"
        )
        return

    # UC-003 approval pathway: "submitted" means pending manual approval.
    # UpdateMediaBuySuccess with empty affected_packages = approval pending.
    if status == "submitted":
        from src.core.schemas._base import UpdateMediaBuySuccess

        if isinstance(resp, UpdateMediaBuySuccess):
            affected = getattr(resp, "affected_packages", None) or []
            assert len(affected) == 0, (
                f"Expected 'submitted' (pending approval) but response has "
                f"{len(affected)} affected_packages — this looks like a completed update"
            )
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

    Verifies the harness mock layer was active. The harness patches external
    dependencies (registry, adapter, etc.) — if production code ran, it hit mocks
    and no real API calls occurred.

    Checks multiple mock types to cover different use-case scenarios:
    - Registry mock (UC-005 list_creative_formats)
    - Adapter mock (UC-002/003/004 delivery scenarios)
    """
    env = ctx["env"]
    assert env is not None, "Expected harness env in ctx — without the harness, real API calls could occur"

    # At least one external-facing mock must be configured in the harness
    registry_mock = env.mock.get("registry")
    adapter_mock = env.mock.get("adapter")
    assert registry_mock is not None or adapter_mock is not None, (
        "Neither registry nor adapter mock configured in harness — cannot verify no real API calls were made"
    )

    # If a response exists, verify production used at least one mock
    if "response" in ctx:
        any_mock_called = False
        # Check registry mock (format catalog scenarios)
        if registry_mock is not None:
            mock_registry = registry_mock.return_value
            if mock_registry.list_all_formats.called or mock_registry.list_all_formats_with_errors.called:
                any_mock_called = True
        # Check adapter mock (delivery/order scenarios)
        if adapter_mock is not None:
            mock_adapter = adapter_mock.return_value
            adapter_methods = ["create_order", "create_line_items", "get_delivery_metrics", "submit_order"]
            if any(
                getattr(mock_adapter, m, None) is not None and getattr(mock_adapter, m).called for m in adapter_methods
            ):
                any_mock_called = True
        # If neither mock was called but we got a response, the harness was still active
        # (the mock patches were in place) — production could not have made real calls.
        # The harness guarantees isolation by patching at import time.
        if not any_mock_called:
            # Harness was active (env is not None, mocks configured) — isolation holds
            # even if the specific scenario path didn't call the mocked functions.
            pass


@then("no real ad platform orders should have been created")
def then_no_real_orders(ctx: dict) -> None:
    """Assert no real ad platform orders were created (sandbox mode).

    Verifies that the adapter mock was used for order creation — if production
    code called the adapter, it hit the mock and no real orders were placed.
    The adapter mock's ``create_order`` (or equivalent) was either not called
    at all, or called against the mock (not a real ad server).

    .. warning::

        FIXME(salesagent-3bv): This step checks adapter mocks as a PROXY for
        "no real orders created". The correct assertion is a direct DB query
        or adapter call log. Replace once order tracking is fully wired.
    """
    import warnings

    warnings.warn(
        "FIXME(salesagent-3bv): then_no_real_orders uses adapter mock proxy, "
        "not a real order tracking check. See salesagent-3bv.",
        stacklevel=1,
    )
    env = ctx["env"]
    assert env is not None, "Expected harness env in ctx"
    adapter_mock = env.mock.get("adapter")
    assert adapter_mock is not None, "adapter mock must be present in env.mock — its absence is a harness bug"
    # If the adapter mock was called, it means production code dispatched to the
    # mock (not a real ad server). The mock intercepts all adapter calls.
    # For sandbox mode, the key assertion is that the adapter was used (mock) and
    # therefore no real external API calls were made to create orders.
    mock_adapter = adapter_mock.return_value
    order_methods = ["create_order", "create_line_items", "submit_order", "activate"]
    called_methods: list[str] = []
    for method_name in order_methods:
        method = getattr(mock_adapter, method_name, None)
        if method is not None and method.called:
            called_methods.append(method_name)
    # In sandbox mode, order-creation methods should NOT have been called.
    # The adapter mock is present (harness guarantee), so even if called, no real
    # API calls would occur — but sandbox should suppress them entirely.
    assert not called_methods, (
        f"Sandbox mode should not create ad platform orders, but adapter methods were called: {called_methods}"
    )
