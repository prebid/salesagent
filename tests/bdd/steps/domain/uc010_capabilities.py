"""BDD step definitions for UC-010: Discover Seller Capabilities.

Three POST-S10 scenarios are wired today — @T-UC-010-pricing (happy path),
@T-UC-010-pricing-degrade (adapter reports nothing → field absent) and
@T-UC-010-pricing-offenum (unrecognized values skipped, recognized survive);
every other UC-010 scenario xfails at the harness fixture with a reason (see
``_harness_env`` in tests/bdd/conftest.py).

Transport independence: the wired scenarios' When ("the Buyer Agent requests
capabilities") is transport-blind and the scenarios carry no @mcp/@a2a/@rest
tag, so ``pytest_generate_tests`` parametrizes them over a2a/mcp/rest
(+ e2e_rest when enabled). The When step dispatches through
``ctx["transport"]`` via ``dispatch_request`` — the pricing-model contract is
identical on every wire, and pinning one would leave the others ungraded.

Then steps read ``supported_pricing_models`` off the real serialized wire body
(``wire_dict``), never ``response.media_buy...`` or a model_dump fallback.
"""

from __future__ import annotations

from typing import Any

from pytest_bdd import given, parsers, then, when

from tests.bdd.steps._outcome_helpers import wire_dict
from tests.bdd.steps.generic._dispatch import dispatch_request

# Pinned copy of MockAdServer.get_supported_pricing_models() — the adapter the
# BDD tenant binds (ad_server="mock", src/adapters/mock_ad_server.py). It is
# DELIBERATELY duplicated here rather than derived: an oracle computed from the
# PricingModel enum grades a set's membership in itself (production filters
# against the same frozenset before emitting), and one computed by calling the
# adapter re-runs the SUT's own input source. A pinned literal reddens when the
# wire diverges from the adapter's declared surface for ANY reason —
# serialization drop, mapping regression (emitting cpc for cpm), or an adapter
# surface change that should be re-reviewed here.
_MOCK_ADAPTER_PRICING_SURFACE: frozenset[str] = frozenset({"cpm", "vcpm", "cpcv", "cpp", "cpc", "cpv", "flat_rate"})


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


@given("the tenant's adapter reports no pricing models")
def given_adapter_reports_no_pricing_models(ctx: dict) -> None:
    """Bind a degenerate adapter: the pricing surface is empty.

    Grades the degrade contract's first partition on the wire: ``minItems: 1``
    makes ``[]`` unserializable, so "reports nothing" must surface as the field
    being ABSENT, not empty — and the read must still succeed. Unrealizable
    over e2e (the live stack's adapter surface is fixed production code); the
    env method declares that via ``E2EUnsupportedSetup``.
    """
    env = ctx["env"]
    env.set_adapter_pricing_models(set())


@given(parsers.parse('the tenant\'s adapter reports pricing models "{models}"'))
def given_adapter_reports_pricing_models(ctx: dict, models: str) -> None:
    """Bind an adapter reporting an arbitrary (possibly off-enum) surface.

    Free-form strings straight from the Gherkin — mixed case and off-enum
    values included — so the scenario, not this step, owns the partition.
    """
    env = ctx["env"]
    env.set_adapter_pricing_models({tok.strip() for tok in models.split(",")})


# ═══════════════════════════════════════════════════════════════════════
# When
# ═══════════════════════════════════════════════════════════════════════


@when("the Buyer Agent requests capabilities")
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

    error = ctx.get("error")
    assert error is None, f"capabilities request failed: {error!r}"
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
    """POST-S10: the wire carries exactly the bound adapter's declared surface.

    Set equality against ``_MOCK_ADAPTER_PRICING_SURFACE`` (a pinned, enum-legal
    literal) subsumes the Gherkin sentence — every member of the pinned set is a
    wire-legal PricingModel string — while staying falsifiable: the previous
    ``⊆ {m.value for m in PricingModel}`` oracle re-derived the same frozenset
    production filters against, so it could not redden on the path it graded
    (nor on a wrong-but-enum-legal emission like cpc-for-cpm).
    """
    models = _wire_pricing_models(ctx)
    assert models, "media_buy.supported_pricing_models absent — nothing to validate"
    assert set(models) == _MOCK_ADAPTER_PRICING_SURFACE, (
        f"wire pricing models diverge from the bound mock adapter's declared surface: "
        f"wire={sorted(models)!r}, adapter={sorted(_MOCK_ADAPTER_PRICING_SURFACE)!r}"
    )
    assert len(models) == len(set(models)), f"duplicate pricing models on the wire: {models!r}"


@then("media_buy.supported_pricing_models should be absent from the wire body")
def then_pricing_models_absent(ctx: dict) -> None:
    """POST-S10 degrade: an empty pricing surface is 'unknown' — the key is omitted.

    ``minItems: 1`` makes ``[]`` unserializable, and an explicit ``null`` is
    schema-invalid for the non-nullable array — absence is the only wire-legal
    encoding. REST/A2A omit via exclude-none; MCP currently serializes ``null``
    and its param is strict-xfailed against #1710 in the conftest collection
    hook (pre-existing, repo-wide, not introduced by this PR).
    """
    error = ctx.get("error")
    assert error is None, f"degraded capabilities read must still succeed, got: {error!r}"
    media_buy = _wire_media_buy(ctx)
    assert "supported_pricing_models" not in media_buy, (
        f"supported_pricing_models must be OMITTED when the adapter reports nothing "
        f"(minItems: 1 forbids [], the schema forbids null), got: "
        f"{media_buy.get('supported_pricing_models')!r}"
    )


@then(parsers.parse('media_buy.supported_pricing_models should be exactly "{expected}"'))
def then_pricing_models_exactly(ctx: dict, expected: str) -> None:
    """POST-S10 degrade: only recognized values survive the map, nothing else.

    Equality (not membership): the off-enum partition must prove both that the
    recognized value reached the wire AND that the unrecognized one did not.
    """
    error = ctx.get("error")
    assert error is None, f"capabilities request failed: {error!r}"
    expected_set = {tok.strip() for tok in expected.split(",")}
    models = _wire_pricing_models(ctx)
    assert isinstance(models, list), (
        f"media_buy.supported_pricing_models should be an array on the wire, got {models!r}"
    )
    assert set(models) == expected_set, (
        f"wire pricing models should be exactly {sorted(expected_set)!r}, got {sorted(models)!r}"
    )
    assert len(models) == len(expected_set), f"duplicate pricing models on the wire: {models!r}"
