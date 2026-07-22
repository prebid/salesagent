"""BDD step definitions for UC-010: Discover Seller Capabilities.

Only the @T-UC-010-pricing scenario is wired today (POST-S10); every other
UC-010 scenario xfails at the harness fixture with a reason (see
``_harness_env`` in tests/bdd/conftest.py).

Transport independence: the Gherkin When text names the MCP tool, but the
scenario carries no @mcp/@a2a/@rest tag, so ``pytest_generate_tests``
parametrizes it over a2a/mcp/rest (+ e2e_rest when enabled). The When step
therefore dispatches through ``ctx["transport"]`` via ``dispatch_request``
rather than pinning MCP — the pricing-model contract is identical on every
wire, and pinning one would leave the other three ungraded.

Then steps read ``supported_pricing_models`` off the real serialized wire body
(``wire_dict``), never ``response.media_buy...`` or a model_dump fallback.
"""

from __future__ import annotations

from typing import Any

from pytest_bdd import given, then, when

from tests.bdd.steps._outcome_helpers import wire_dict
from tests.bdd.steps.generic._dispatch import dispatch_request


def _wire_media_buy(ctx: dict) -> dict[str, Any]:
    """Return the ``media_buy`` object as it appears on the buyer's wire."""
    wire = wire_dict(ctx)
    media_buy = wire.get("media_buy")
    assert media_buy is not None, f"capabilities response carried no media_buy section: {sorted(wire)}"
    return media_buy


def _wire_pricing_models(ctx: dict) -> Any:
    """Return ``media_buy.supported_pricing_models`` from the wire body."""
    return _wire_media_buy(ctx).get("supported_pricing_models")


# ═══════════════════════════════════════════════════════════════════════
# Given
# ═══════════════════════════════════════════════════════════════════════


@given("the tenant has full capabilities configured")
def given_tenant_full_capabilities(ctx: dict) -> None:
    """The seeded tenant is bound to the full-capability (mock) ad server.

    The harness fixture seeds tenant + principal; this step pins the precondition
    the scenario depends on — the bound adapter declares the whole pricing-model
    surface. Asserting it (rather than passing) keeps the Then non-vacuous: if the
    default adapter ever changed to one reporting nothing,
    ``supported_pricing_models`` would legitimately be absent and the scenario
    would silently stop testing anything.
    """
    tenant = ctx["tenant"]
    assert tenant.ad_server == "mock", (
        f"UC-010 expects the full-capability mock adapter, got ad_server={tenant.ad_server!r}"
    )
    ctx["full_capabilities"] = True


# ═══════════════════════════════════════════════════════════════════════
# When
# ═══════════════════════════════════════════════════════════════════════


@when("the Buyer Agent calls get_adcp_capabilities MCP tool")
def when_buyer_requests_capabilities(ctx: dict) -> None:
    """Request capabilities over the scenario's parametrized wire transport.

    ``req=None`` is explicit rather than defaulted: capabilities discovery
    carries no buyer payload at all (the REST route is a bodyless GET), so the
    whole request is auth plus transport.
    """
    dispatch_request(ctx, req=None)


# ═══════════════════════════════════════════════════════════════════════
# Then
# ═══════════════════════════════════════════════════════════════════════


@then("media_buy.supported_pricing_models should be a non-empty array")
def then_pricing_models_non_empty(ctx: dict) -> None:
    """POST-S10: the buyer receives the bound adapter's pricing models on the wire."""
    from adcp.types.generated_poc.enums.pricing_model import PricingModel

    dispatch_error = ctx.get("error")
    assert dispatch_error is None, f"capabilities request failed: {dispatch_error!r}"
    models = _wire_pricing_models(ctx)
    assert isinstance(models, list), (
        f"media_buy.supported_pricing_models should be an array on the wire, got {models!r}"
    )
    # Element-level, not a count: every ad server sells CPM (the base adapter
    # default is literally {"cpm"}), so naming it is the concrete non-emptiness
    # oracle — a count check would pass on a list of the wrong thing.
    assert PricingModel.cpm.value in models, (
        f"media_buy.supported_pricing_models omits the universal 'cpm' — "
        f"buyers cannot pre-filter products. Wire: {models!r}"
    )


@then("each pricing model should be a valid pricing-model enum value")
def then_pricing_models_are_enum_values(ctx: dict) -> None:
    """POST-S10: every emitted value is a wire-legal PricingModel string."""
    from adcp.types.generated_poc.enums.pricing_model import PricingModel

    allowed = {m.value for m in PricingModel}
    models = _wire_pricing_models(ctx)
    assert models, "media_buy.supported_pricing_models absent — nothing to validate"
    unknown = [m for m in models if m not in allowed]
    assert not unknown, f"off-enum pricing models on the wire: {unknown!r} (allowed: {sorted(allowed)})"
