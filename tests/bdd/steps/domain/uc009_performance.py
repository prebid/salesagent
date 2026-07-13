"""Steps for UC-009 update_performance_index — salesagent-8wf2/cmjm.

The five @T-UC-009-main-* scenarios run a real update_performance_index
through the parametrized wire transport (a2a/mcp/rest) on PerformanceEnv.
Only the ad-server adapter and the audit logger are mocks (external
boundaries); ownership verification, principal resolution, request
validation, and the transport wrappers are production code.

Scenario texts are transport-flavored ("MCP tool", "A2A skill") because the
storyboard narrates one transport per scenario, but the harness parametrizes
every scenario across all wire transports — both When texts dispatch through
ctx["transport"] identically.
"""

from __future__ import annotations

import json
from typing import Any

from pytest_bdd import given, parsers, then, when

from tests.bdd.steps._datatable import datatable_to_dicts
from tests.bdd.steps._outcome_helpers import wire_field
from tests.bdd.steps.generic._dispatch import dispatch_request

# ── Given steps ─────────────────────────────────────────────────────


@given(parsers.parse('the Buyer owns media buy "{media_buy_id}"'))
def given_buyer_owns_media_buy(ctx: dict, media_buy_id: str) -> None:
    """Create a media buy owned by the authenticated principal."""
    from tests.factories import MediaBuyFactory

    env = ctx["env"]
    tenant, principal = env.setup_default_data()
    ctx.setdefault("tenant", tenant)
    ctx.setdefault("principal", principal)
    ctx["media_buy"] = MediaBuyFactory(tenant=tenant, principal=principal, media_buy_id=media_buy_id)


@given(parsers.parse("the media buy has products {product_ids}"))
def given_media_buy_products(ctx: dict, product_ids: str) -> None:
    """Record the product ids the feedback will reference.

    The impl maps each ProductPerformance.product_id straight onto a
    PackagePerformance.package_id (performance.py) without a catalog read, so
    the products exist as the expected forwarding targets, not as DB rows.
    """
    ctx["expected_package_ids"] = json.loads(product_ids)


# ── When steps ──────────────────────────────────────────────────────


def _dispatch_performance_feedback(ctx: dict, datatable: list) -> None:
    row = datatable_to_dicts(datatable)[0]
    dispatch_request(
        ctx,
        media_buy_id=row["media_buy_id"],
        performance_data=json.loads(row["performance_data"]),
    )


@when("the Buyer Agent calls update_performance_index MCP tool with:")
def when_update_performance_mcp(ctx: dict, datatable: list) -> None:
    """MCP-flavored storyboard scenario: @mcp tag suppresses parametrization,
    so the step pins the transport itself (setdefault — a parametrized
    transport would win)."""
    from tests.harness.transport import Transport

    ctx.setdefault("transport", Transport.MCP)
    _dispatch_performance_feedback(ctx, datatable)


@when("the Buyer Agent sends update_performance_index A2A skill request with:")
def when_update_performance_a2a(ctx: dict, datatable: list) -> None:
    """A2A-flavored storyboard scenario (tagged @rest @a2a upstream; the When
    text names the A2A skill — dispatch matches the text)."""
    from tests.harness.transport import Transport

    ctx.setdefault("transport", Transport.A2A)
    _dispatch_performance_feedback(ctx, datatable)


# ── Then steps ──────────────────────────────────────────────────────


@then("the response success field should be true")
def then_response_success_true(ctx: dict) -> None:
    """v3.1 Success branch: the wire response reports the success status.

    The response schema carries ``status`` ("success"/"failed"), not a boolean
    ``success`` field — the scenario text predates the v3.1 response shape;
    the graded contract is the status value on the real wire body.
    """
    assert ctx.get("error") is None, f"Request failed: {ctx.get('error')}"
    assert wire_field(ctx, "status") == "success"


@then("the adapter should receive PackagePerformance entries for:")
def then_adapter_receives_package_performance(ctx: dict, datatable: list) -> None:
    """Assert the adapter was forwarded exactly the mapped PackagePerformance list."""
    env = ctx["env"]
    calls = env.adapter_update_calls
    assert calls, "adapter.update_media_buy_performance_index was never called"
    (media_buy_id, package_performance), _kwargs = calls[-1]
    assert media_buy_id == ctx["media_buy"].media_buy_id
    actual = {(p.package_id, p.performance_index) for p in package_performance}
    expected = {(row["package_id"], float(row["performance_index"])) for row in datatable_to_dicts(datatable)}
    assert actual == expected, f"adapter received {actual}, expected {expected}"


@then("the audit log should contain an entry with:")
def then_audit_log_entry(ctx: dict, datatable: list) -> None:
    """Assert the audit logger recorded the operation with the exact details."""
    env = ctx["env"]
    expected = {row["field"]: row["value"] for row in datatable_to_dicts(datatable)}
    log_calls = env.mock["audit"].return_value.log_operation.call_args_list
    operations = [c.kwargs.get("operation") for c in log_calls]
    assert "update_performance_index" in operations, f"no update_performance_index audit entry; got: {operations}"
    last = len(operations) - 1 - operations[::-1].index("update_performance_index")
    details: dict[str, Any] = log_calls[last].kwargs.get("details") or {}
    assert details.get("media_buy_id") == expected["media_buy_id"]
    assert details.get("product_count") == int(expected["product_count"])
    assert abs(details.get("avg_performance_index", 0.0) - float(expected["avg_performance"])) < 1e-9, (
        f"avg_performance_index={details.get('avg_performance_index')}, expected {expected['avg_performance']}"
    )
