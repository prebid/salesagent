"""Given steps for UC-002 create_media_buy request construction.

Builds ``ctx["request_kwargs"]`` incrementally — assembled into
CreateMediaBuyRequest in the When step via _dispatch_create_media_buy().

Steps use factories for DB setup and reference ctx["default_product"]
and ctx["default_pricing_option"] created by conftest's _harness_env.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from pytest_bdd import given, parsers

from tests.factories import (
    PricingOptionFactory,
    ProductFactory,
)


def _pricing_option_id(po: Any) -> str:
    """Build the synthetic pricing_option_id string from a PricingOption ORM model."""
    fixed_str = "fixed" if po.is_fixed else "auction"
    return f"{po.pricing_model}_{po.currency.lower()}_{fixed_str}"


def _future(days: int = 1) -> datetime:
    """Return a timezone-aware datetime N days in the future."""
    return datetime.now(UTC) + timedelta(days=days)


def _ensure_request_defaults(ctx: dict) -> dict[str, Any]:
    """Ensure ctx['request_kwargs'] has valid defaults for a create_media_buy request."""
    if "request_kwargs" not in ctx:
        product = ctx.get("default_product")
        pricing_option = ctx.get("default_pricing_option")
        product_id = product.product_id if product else "guaranteed_display"
        pricing_option_id = _pricing_option_id(pricing_option) if pricing_option else "cpm_usd_fixed"
        ctx["request_kwargs"] = {
            "buyer_ref": f"test-buyer-{uuid.uuid4().hex[:8]}",
            "brand": {"domain": "testbrand.com"},
            "start_time": _future(1).isoformat(),
            "end_time": _future(30).isoformat(),
            "packages": [
                {
                    "product_id": product_id,
                    "buyer_ref": "pkg-1",
                    "budget": 5000.0,
                    "pricing_option_id": pricing_option_id,
                }
            ],
        }
    return ctx["request_kwargs"]


# ═══════════════════════════════════════════════════════════════════════
# Tenant configuration
# ═══════════════════════════════════════════════════════════════════════


@given("the tenant is configured for auto-approval")
@given("tenant human_review_required is false")
def given_tenant_auto_approval(ctx: dict) -> None:
    """Configure tenant for auto-approval (human_review_required=False)."""
    tenant = ctx.get("tenant")
    if tenant:
        tenant.human_review_required = False
        env = ctx["env"]
        env._commit_factory_data()
        # Also update identity's tenant dict (pre-built, not re-read from DB)
        env._identity_cache.clear()
        env._tenant_overrides["human_review_required"] = False


@given(parsers.parse('the tenant has "human_review_required" set to true'))
@given("tenant human_review_required is true")
@given("approval path is manual")
def given_tenant_manual_approval(ctx: dict) -> None:
    """Configure tenant for manual approval."""
    tenant = ctx.get("tenant")
    if tenant:
        tenant.human_review_required = True
        env = ctx["env"]
        env._commit_factory_data()
        env._identity_cache.clear()
        env._tenant_overrides["human_review_required"] = True
        # Production code checks: manual_approval_required AND
        # "create_media_buy" in adapter.manual_approval_operations.
        # The mock adapter defaults to manual_approval_operations=[],
        # so we must also configure the adapter mock for manual approval.
        adapter_mock = env.mock["adapter"].return_value
        adapter_mock.manual_approval_required = True
        adapter_mock.manual_approval_operations = {"create_media_buy", "update_media_buy"}


@given("adapter manual_approval_required is false")
def given_adapter_no_manual_approval(ctx: dict) -> None:
    """Configure adapter for auto-approval (manual_approval_required=False).

    This is the default state for MediaBuyCreateEnv, but explicitly set it
    to be clear in the scenario.
    """
    env = ctx["env"]
    adapter_mock = env.mock["adapter"].return_value
    adapter_mock.manual_approval_required = False
    adapter_mock.manual_approval_operations = []


@given("adapter manual_approval_required is true")
def given_adapter_manual_approval(ctx: dict) -> None:
    """Configure adapter to require manual approval.

    Sets manual_approval_required=True and includes 'create_media_buy' in
    manual_approval_operations — both are needed for the approval gate.
    """
    env = ctx["env"]
    adapter_mock = env.mock["adapter"].return_value
    adapter_mock.manual_approval_required = True
    adapter_mock.manual_approval_operations = {"create_media_buy", "update_media_buy"}


@given(parsers.parse("the tenant has max_daily_package_spend configured at {amount:d}"))
def given_tenant_max_daily_spend(ctx: dict, amount: int) -> None:
    """Configure tenant max daily package spend on the CurrencyLimit (USD)."""
    from decimal import Decimal

    from sqlalchemy import select

    from src.core.database.database_session import get_db_session
    from src.core.database.models import CurrencyLimit

    tenant = ctx.get("tenant")
    assert tenant is not None, "No tenant in ctx — Given step ordering error"
    env = ctx["env"]
    env._commit_factory_data()
    with get_db_session() as session:
        cl = session.scalars(select(CurrencyLimit).filter_by(tenant_id=tenant.tenant_id, currency_code="USD")).first()
        assert cl is not None, f"No CurrencyLimit(USD) for tenant {tenant.tenant_id}"
        cl.max_daily_package_spend = Decimal(str(amount))
        session.commit()


# ═══════════════════════════════════════════════════════════════════════
# Request construction — base
# ═══════════════════════════════════════════════════════════════════════


@given(parsers.parse("a valid create_media_buy request with:"))
def given_valid_create_request_with_table(ctx: dict, datatable: list[list[str]]) -> None:
    """Set up a create_media_buy request from a Gherkin data table.

    Table format: | field | value |
    """
    kwargs = _ensure_request_defaults(ctx)
    for row in datatable:
        field, value = row[0].strip(), row[1].strip()
        if field == "buyer_ref":
            kwargs["buyer_ref"] = value
        elif field == "brand":
            # Parse: domain "acme.com"
            if value.startswith("domain "):
                domain = value.split('"')[1]
                kwargs["brand"] = {"domain": domain}
            else:
                kwargs["brand"] = {"domain": value}
        elif field == "start_time":
            kwargs["start_time"] = value
        elif field == "end_time":
            kwargs["end_time"] = value
        elif field == "account":
            # Parse: account_id "acc-001"
            if "account_id" in value:
                account_id = value.split('"')[1]
                from adcp.types.generated_poc.core.account_ref import (
                    AccountReference,
                    AccountReference1,
                )

                kwargs["account"] = AccountReference(root=AccountReference1(account_id=account_id))
        elif field == "proposal_id":
            kwargs["proposal_id"] = value
        elif field == "total_budget":
            # Parse: amount 5000, currency "USD"
            if "amount" in value:
                parts = value.split(",")
                amount_part = parts[0].strip()
                amount = float(amount_part.split()[-1])
                kwargs["total_budget"] = {"amount": amount, "currency": "USD"}
                if len(parts) > 1 and "currency" in parts[1]:
                    currency = parts[1].strip().split('"')[1]
                    kwargs["total_budget"]["currency"] = currency


@given(parsers.parse('a valid create_media_buy request with start_time "{value}"'))
def given_request_with_start_time(ctx: dict, value: str) -> None:
    """Set up request with specific start_time."""
    kwargs = _ensure_request_defaults(ctx)
    kwargs["start_time"] = value


@given(parsers.parse("a valid create_media_buy request with total budget {amount:d}"))
def given_request_with_total_budget(ctx: dict, amount: int) -> None:
    """Set up request with a specific total budget amount on the first package."""
    kwargs = _ensure_request_defaults(ctx)
    if kwargs.get("packages"):
        kwargs["packages"][0]["budget"] = float(amount)


# ═══════════════════════════════════════════════════════════════════════
# Package construction
# ═══════════════════════════════════════════════════════════════════════


@given("the request includes 2 packages with valid product_ids")
def given_request_2_packages(ctx: dict) -> None:
    """Add 2 packages with valid product_ids to the request."""
    kwargs = _ensure_request_defaults(ctx)
    env = ctx["env"]
    product2 = ProductFactory(
        tenant=ctx["tenant"],
        product_id="standard_video",
        property_tags=["all_inventory"],
    )
    po2 = PricingOptionFactory(
        product=product2,
        pricing_model="cpm",
        currency="USD",
        is_fixed=True,
    )
    env._commit_factory_data()
    kwargs["packages"] = [
        {
            "product_id": ctx["default_product"].product_id,
            "buyer_ref": "pkg-1",
            "budget": 5000.0,
            "pricing_option_id": _pricing_option_id(ctx["default_pricing_option"]),
        },
        {
            "product_id": product2.product_id,
            "buyer_ref": "pkg-2",
            "budget": 3000.0,
            "pricing_option_id": _pricing_option_id(po2),
        },
    ]


@given("each package has a positive budget meeting minimum spend")
def given_packages_positive_budget(ctx: dict) -> None:
    """Verify/ensure packages have positive budgets meeting minimum spend.

    Default packages already have budgets above CurrencyLimit.min_package_budget (100).
    """
    kwargs = _ensure_request_defaults(ctx)
    for pkg in kwargs.get("packages", []):
        if pkg.get("budget", 0) < 100:
            pkg["budget"] = 5000.0


@given(parsers.parse('all packages use the same currency "{currency}"'))
def given_packages_same_currency(ctx: dict, currency: str) -> None:
    """Ensure all packages use the specified currency via their pricing options."""
    # Currency comes from the pricing option, not the package directly.
    # Default pricing options are already the specified currency.
    ctx.setdefault("expected_currency", currency)


@given("each package has a valid pricing_option_id")
def given_packages_valid_pricing(ctx: dict) -> None:
    """Ensure each package has a valid pricing_option_id."""
    # Default packages already reference valid pricing options created by setup_media_buy_data.
    ctx.setdefault("pricing_validated", True)


@given("a valid create_media_buy request with 2 packages")
def given_request_2_packages_simple(ctx: dict) -> None:
    """Set up request with 2 packages (for duplicate product testing)."""
    kwargs = _ensure_request_defaults(ctx)
    env = ctx["env"]
    product2 = ProductFactory(
        tenant=ctx["tenant"],
        product_id="standard_video",
        property_tags=["all_inventory"],
    )
    po2 = PricingOptionFactory(
        product=product2,
        pricing_model="cpm",
        currency="USD",
        is_fixed=True,
    )
    env._commit_factory_data()
    kwargs["packages"] = [
        {
            "product_id": ctx["default_product"].product_id,
            "buyer_ref": "pkg-1",
            "budget": 5000.0,
            "pricing_option_id": _pricing_option_id(ctx["default_pricing_option"]),
        },
        {
            "product_id": product2.product_id,
            "buyer_ref": "pkg-2",
            "budget": 3000.0,
            "pricing_option_id": _pricing_option_id(po2),
        },
    ]


# ═══════════════════════════════════════════════════════════════════════
# Error injection — "But" steps that override defaults with invalid values
# ═══════════════════════════════════════════════════════════════════════


@given("all package budgets sum to 0")
@given("But all package budgets sum to 0")
def given_zero_budget(ctx: dict) -> None:
    """Override all package budgets to 0."""
    kwargs = _ensure_request_defaults(ctx)
    for pkg in kwargs.get("packages", []):
        pkg["budget"] = 0


@given(parsers.parse('a package references product_id "{product_id}" which does not exist'))
@given(parsers.parse('But a package references product_id "{product_id}" which does not exist'))
def given_nonexistent_product(ctx: dict, product_id: str) -> None:
    """Override first package to reference a nonexistent product."""
    kwargs = _ensure_request_defaults(ctx)
    if kwargs.get("packages"):
        kwargs["packages"][0]["product_id"] = product_id


@given(parsers.parse('start_time is "{value}" (in the past)'))
@given(parsers.parse('But start_time is "{value}" (in the past)'))
def given_past_start_time(ctx: dict, value: str) -> None:
    """Set start_time to a past datetime."""
    kwargs = _ensure_request_defaults(ctx)
    kwargs["start_time"] = value


@given("end_time is before start_time")
@given("But end_time is before start_time")
def given_end_before_start(ctx: dict) -> None:
    """Set end_time before start_time."""
    kwargs = _ensure_request_defaults(ctx)
    kwargs["start_time"] = _future(10).isoformat()
    kwargs["end_time"] = _future(1).isoformat()


@given(parsers.parse('the packages use currency "{currency}" which is not in the tenant\'s CurrencyLimit table'))
@given(parsers.parse('But the packages use currency "{currency}" which is not in the tenant\'s CurrencyLimit table'))
def given_unsupported_currency(ctx: dict, currency: str) -> None:
    """Create a pricing option with unsupported currency."""
    env = ctx["env"]
    kwargs = _ensure_request_defaults(ctx)
    # Create a pricing option with the unsupported currency
    po = PricingOptionFactory(
        product=ctx["default_product"],
        pricing_model="cpm",
        currency=currency,
        is_fixed=True,
    )
    env._commit_factory_data()
    if kwargs.get("packages"):
        kwargs["packages"][0]["pricing_option_id"] = _pricing_option_id(po)


@given(parsers.parse('both packages reference the same product_id "{product_id}"'))
@given(parsers.parse('But both packages reference the same product_id "{product_id}"'))
def given_duplicate_product(ctx: dict, product_id: str) -> None:
    """Set both packages to reference the same product_id."""
    kwargs = _ensure_request_defaults(ctx)
    # Create the product if it doesn't match default
    if ctx["default_product"].product_id != product_id:
        env = ctx["env"]
        ProductFactory(
            tenant=ctx["tenant"],
            product_id=product_id,
            property_tags=["all_inventory"],
        )
        env._commit_factory_data()
    for pkg in kwargs.get("packages", []):
        pkg["product_id"] = product_id


@given(parsers.parse('a package targeting_overlay contains unknown field "{field_name}"'))
@given(parsers.parse('But a package targeting_overlay contains unknown field "{field_name}"'))
def given_unknown_targeting_field(ctx: dict, field_name: str) -> None:
    """Add unknown field to package targeting_overlay."""
    kwargs = _ensure_request_defaults(ctx)
    if kwargs.get("packages"):
        kwargs["packages"][0].setdefault("targeting_overlay", {})[field_name] = "value"


@given("a package targeting_overlay sets a managed-only dimension")
@given("But a package targeting_overlay sets a managed-only dimension")
def given_managed_targeting_dimension(ctx: dict) -> None:
    """Set a managed-only targeting dimension."""
    kwargs = _ensure_request_defaults(ctx)
    if kwargs.get("packages"):
        kwargs["packages"][0]["targeting_overlay"] = {"content_labels": ["DL-MA"]}


@given(parsers.parse('a package targeting_overlay includes "{value}" in both geo_countries and geo_countries_exclude'))
@given(
    parsers.parse('But a package targeting_overlay includes "{value}" in both geo_countries and geo_countries_exclude')
)
def given_geo_overlap(ctx: dict, value: str) -> None:
    """Create geo include/exclude overlap."""
    kwargs = _ensure_request_defaults(ctx)
    if kwargs.get("packages"):
        kwargs["packages"][0]["targeting_overlay"] = {
            "geo_countries": [value],
            "geo_countries_exclude": [value],
        }


@given(parsers.parse("a package has budget {budget:d} over a {days:d}-day flight (daily = {daily:d})"))
@given(parsers.parse("But a package has budget {budget:d} over a {days:d}-day flight (daily = {daily:d})"))
def given_high_daily_spend(ctx: dict, budget: int, days: int, daily: int) -> None:
    """Set package with high daily spend exceeding cap."""
    kwargs = _ensure_request_defaults(ctx)
    kwargs["start_time"] = _future(1).isoformat()
    kwargs["end_time"] = _future(1 + days).isoformat()
    if kwargs.get("packages"):
        kwargs["packages"][0]["budget"] = float(budget)


@given(parsers.parse('a package references pricing_option_id "{po_id}" not found on the product'))
@given(parsers.parse('But a package references pricing_option_id "{po_id}" not found on the product'))
def given_nonexistent_pricing_option(ctx: dict, po_id: str) -> None:
    """Override first package pricing_option_id to a non-existent value."""
    kwargs = _ensure_request_defaults(ctx)
    if kwargs.get("packages"):
        kwargs["packages"][0]["pricing_option_id"] = po_id


@given("a package selects an auction pricing option but provides no bid_price")
@given("But a package selects an auction pricing option but provides no bid_price")
def given_auction_no_bid_price(ctx: dict) -> None:
    """Create an auction pricing option on the product and omit bid_price."""
    env = ctx["env"]
    auction_po = PricingOptionFactory(
        product=ctx["default_product"],
        pricing_model="cpm",
        currency="USD",
        is_fixed=False,
        price_guidance={"floor": 1.0},
    )
    env._commit_factory_data()
    kwargs = _ensure_request_defaults(ctx)
    if kwargs.get("packages"):
        kwargs["packages"][0]["pricing_option_id"] = _pricing_option_id(auction_po)
        kwargs["packages"][0].pop("bid_price", None)


@given(parsers.parse("a package has bid_price {bid:g} but floor_price is {floor:g}"))
@given(parsers.parse("But a package has bid_price {bid:g} but floor_price is {floor:g}"))
def given_bid_below_floor(ctx: dict, bid: float, floor: float) -> None:
    """Create an auction pricing option with floor and set bid below it."""
    env = ctx["env"]
    auction_po = PricingOptionFactory(
        product=ctx["default_product"],
        pricing_model="cpm",
        currency="USD",
        is_fixed=False,
        price_guidance={"floor": floor},
    )
    env._commit_factory_data()
    kwargs = _ensure_request_defaults(ctx)
    if kwargs.get("packages"):
        kwargs["packages"][0]["pricing_option_id"] = _pricing_option_id(auction_po)
        kwargs["packages"][0]["bid_price"] = bid


# ═══════════════════════════════════════════════════════════════════════
# Pricing XOR invariant — BR-RULE-006 (inv-006-1..4)
# ═══════════════════════════════════════════════════════════════════════


@given("a package pricing option has fixed_price set and floor_price null")
def given_fixed_price_only(ctx: dict) -> None:
    """Ensure the package references a fixed pricing option (default state).

    The default PricingOption from setup_media_buy_data() is already
    is_fixed=True with rate=5.00 — this maps to fixed_price=5.00, floor_price=None.
    """
    # Default state — no mutation needed. Assert the default PO is fixed.
    po = ctx.get("default_pricing_option")
    assert po is not None, "No default_pricing_option in ctx — Given step ordering error"
    assert po.is_fixed, "Default pricing option should be fixed"


@given("a package pricing option has floor_price set and fixed_price null")
def given_floor_price_only(ctx: dict) -> None:
    """Create an auction pricing option with floor_price (no fixed_price)."""
    env = ctx["env"]
    auction_po = PricingOptionFactory(
        product=ctx["default_product"],
        pricing_model="cpm",
        currency="USD",
        is_fixed=False,
        price_guidance={"floor": 2.0},
    )
    env._commit_factory_data()
    kwargs = _ensure_request_defaults(ctx)
    if kwargs.get("packages"):
        kwargs["packages"][0]["pricing_option_id"] = _pricing_option_id(auction_po)


@given("the package has a bid_price above the floor")
def given_bid_above_floor(ctx: dict) -> None:
    """Set bid_price above the pricing option's floor price."""
    kwargs = _ensure_request_defaults(ctx)
    if kwargs.get("packages"):
        kwargs["packages"][0]["bid_price"] = 5.0  # Above default floor of 2.0


@given("a package pricing option has both fixed_price and floor_price set")
@given("But a package pricing option has both fixed_price and floor_price set")
def given_both_fixed_and_floor(ctx: dict) -> None:
    """Create a malformed pricing option with both fixed and auction characteristics.

    ORM: is_fixed=True (→ fixed_price from rate) AND price_guidance with floor
    (→ floor_price). This violates BR-RULE-006 XOR invariant.

    SPEC-PRODUCTION GAP: Production's _validate_pricing_model_selection works at
    the ORM level (is_fixed + rate + price_guidance) and does not enforce the
    schema-level XOR invariant during create_media_buy. The operation may succeed.
    """
    env = ctx["env"]
    malformed_po = PricingOptionFactory(
        product=ctx["default_product"],
        pricing_model="cpm",
        currency="USD",
        is_fixed=True,
        rate=5.00,
        price_guidance={"floor": 2.0},  # Both rate AND floor — violates XOR
    )
    env._commit_factory_data()
    kwargs = _ensure_request_defaults(ctx)
    if kwargs.get("packages"):
        kwargs["packages"][0]["pricing_option_id"] = _pricing_option_id(malformed_po)


@given("a package pricing option has neither fixed_price nor floor_price")
@given("But a package pricing option has neither fixed_price nor floor_price")
def given_neither_fixed_nor_floor(ctx: dict) -> None:
    """Create a malformed pricing option with no fixed_price and no floor_price.

    ORM: is_fixed=True but rate=None — the pricing option exists but has no usable
    price. This violates BR-RULE-006 which requires exactly one of fixed/floor.

    Production catches this as "has is_fixed=true but no rate specified" in
    _validate_pricing_model_selection (PRICING_ERROR).
    """
    env = ctx["env"]
    malformed_po = PricingOptionFactory(
        product=ctx["default_product"],
        pricing_model="cpm",
        currency="USD",
        is_fixed=True,
        rate=None,  # No rate → no fixed_price
        price_guidance=None,  # No floor → no floor_price
    )
    env._commit_factory_data()
    kwargs = _ensure_request_defaults(ctx)
    if kwargs.get("packages"):
        kwargs["packages"][0]["pricing_option_id"] = _pricing_option_id(malformed_po)


# ═══════════════════════════════════════════════════════════════════════
# Proposal-related request construction
# ═══════════════════════════════════════════════════════════════════════


@given(parsers.parse('a valid create_media_buy request with proposal_id "{proposal_id}"'))
def given_request_with_proposal_id(ctx: dict, proposal_id: str) -> None:
    """Set up a create_media_buy request referencing a proposal_id."""
    kwargs = _ensure_request_defaults(ctx)
    kwargs["proposal_id"] = proposal_id


@given(parsers.parse("a valid create_media_buy request with proposal_id and total_budget amount {amount:d}"))
def given_request_with_proposal_and_budget(ctx: dict, amount: int) -> None:
    """Set up a create_media_buy request with proposal_id and total_budget."""
    kwargs = _ensure_request_defaults(ctx)
    kwargs["proposal_id"] = f"prop-{uuid.uuid4().hex[:8]}"
    kwargs["total_budget"] = {"amount": float(amount), "currency": "USD"}


@given(parsers.parse('proposal "{proposal_id}" does not exist or has expired'))
@given(parsers.parse('But proposal "{proposal_id}" does not exist or has expired'))
def given_proposal_not_exists(ctx: dict, proposal_id: str) -> None:
    """Mark that the referenced proposal does not exist.

    SPEC-PRODUCTION GAP: Production has no proposal store — proposal_id is
    accepted but never validated. This step is a no-op; the scenario will
    xfail at the Then assertion because production won't raise PROPOSAL_EXPIRED.
    """
    ctx["expected_proposal_missing"] = proposal_id


@given(parsers.parse("the proposal's total_budget_guidance.min is {amount:d}"))
@given(parsers.parse("But the proposal's total_budget_guidance.min is {amount:d}"))
def given_proposal_budget_guidance_min(ctx: dict, amount: int) -> None:
    """Set expected proposal budget guidance minimum.

    SPEC-PRODUCTION GAP: Production has no proposal budget guidance.
    This step records the expected minimum but production won't validate against it.
    """
    ctx["expected_budget_guidance_min"] = amount


# ═══════════════════════════════════════════════════════════════════════
# Adapter state
# ═══════════════════════════════════════════════════════════════════════


@given("the ad server adapter is available")
def given_adapter_available(ctx: dict) -> None:
    """Ensure the mock adapter is configured for success (default state)."""
    # MediaBuyCreateEnv._configure_mocks() already sets up happy-path adapter
    ctx.setdefault("adapter_available", True)


@given("the ad server adapter returns an error")
@given("But the ad server adapter returns an error")
def given_adapter_error(ctx: dict) -> None:
    """Configure the mock adapter to return an error."""
    env = ctx["env"]
    mock_adapter = env.mock["adapter"].return_value
    mock_adapter.create_media_buy.side_effect = Exception("Ad server unavailable")


@given("the ad server adapter returns success")
def given_adapter_success(ctx: dict) -> None:
    """Ensure adapter returns success (reset any error injection).

    Restores the original side_effect callback from harness configuration.
    The harness stores the original callback as ``_original_create_side_effect``
    on the mock adapter so it can be restored after error injection.
    """
    env = ctx["env"]
    mock_adapter = env.mock["adapter"].return_value
    original = getattr(mock_adapter, "_original_create_side_effect", None)
    if original is not None:
        mock_adapter.create_media_buy.side_effect = original
    else:
        # Fallback: just clear error injection; return_value may already be set
        mock_adapter.create_media_buy.side_effect = None


@given("a create_media_buy request")
def given_bare_create_request(ctx: dict) -> None:
    """Set up a bare create_media_buy request with valid defaults."""
    _ensure_request_defaults(ctx)


@given(parsers.parse("a valid create_media_buy request that passes all validation"))
def given_request_passes_validation(ctx: dict) -> None:
    """Set up a request that passes all validation checks."""
    _ensure_request_defaults(ctx)


@given("a create_media_buy request that fails validation")
def given_request_fails_validation(ctx: dict) -> None:
    """Set up a request that will fail validation (nonexistent product)."""
    _ensure_request_defaults(ctx)
    # Override with a product_id that doesn't exist in the tenant — triggers PRODUCT_NOT_FOUND
    for pkg in ctx["request_kwargs"].get("packages", []):
        pkg["product_id"] = "nonexistent-product-id"


@given("a create_media_buy request that fails with a correctable error")
def given_request_correctable_error(ctx: dict) -> None:
    """Set up a request that triggers a correctable validation error.

    Uses a nonexistent product_id to trigger PRODUCT_NOT_FOUND, which is a
    _StructuredValidationError with recovery="correctable" and a suggestion.
    """
    _ensure_request_defaults(ctx)
    for pkg in ctx["request_kwargs"].get("packages", []):
        pkg["product_id"] = "nonexistent-correctable-product"


@given(parsers.parse('a media buy exists in "{state}" state'))
def given_existing_media_buy(ctx: dict, state: str) -> None:
    """Create a media buy in the specified state in the database."""
    from tests.factories import MediaBuyFactory

    env = ctx["env"]
    media_buy = MediaBuyFactory(
        tenant=ctx["tenant"],
        principal=ctx["principal"],
        status=state,
    )
    env._commit_factory_data()
    ctx["existing_media_buy"] = media_buy
    ctx["existing_media_buy_id"] = media_buy.media_buy_id
