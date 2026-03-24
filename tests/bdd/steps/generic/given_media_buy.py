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
    CurrencyLimitFactory,
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


@given(parsers.parse("the approval scenario is {partition}"))
def given_approval_partition(ctx: dict, partition: str) -> None:
    """Configure approval flags for partition scenarios (BR-RULE-080).

    Dispatches to existing tenant/adapter approval helpers.
    """
    partition = partition.strip()

    if partition == "auto_approve":
        given_tenant_auto_approval(ctx)
        given_adapter_no_manual_approval(ctx)
    elif partition == "pending_human_review":
        given_tenant_manual_approval(ctx)
    elif partition == "pending_adapter_approval":
        # Tenant auto-approve, but adapter requires manual approval
        given_tenant_auto_approval(ctx)
        given_adapter_manual_approval(ctx)
    else:
        raise ValueError(f"Unknown approval partition: {partition}")


@given(parsers.parse("the approval configuration is: {config}"))
def given_approval_boundary(ctx: dict, config: str) -> None:
    """Configure approval flags for boundary scenarios (BR-RULE-080).

    Dispatches to existing tenant/adapter approval helpers.
    """
    config = config.strip()

    if config == "both=false":
        given_tenant_auto_approval(ctx)
        given_adapter_no_manual_approval(ctx)
    elif config == "tenant_hr=true":
        given_tenant_manual_approval(ctx)
    elif config == "adapter_ma=true":
        # Tenant auto-approve, but adapter requires manual approval
        given_tenant_auto_approval(ctx)
        given_adapter_manual_approval(ctx)
    else:
        raise ValueError(f"Unknown approval boundary config: {config}")


# ───────────────────────────────────────────────────────────────────────
# Persistence timing configuration (BR-RULE-020)
# ───────────────────────────────────────────────────────────────────────


@given(parsers.parse("the persistence timing scenario is {partition}"))
def given_persistence_timing_partition(ctx: dict, partition: str) -> None:
    """Configure approval + adapter state for persistence timing partitions (BR-RULE-020).

    Dispatches to existing tenant/adapter helpers.
    """
    partition = partition.strip()

    if partition == "auto_approve_adapter_success":
        given_tenant_auto_approval(ctx)
        given_adapter_no_manual_approval(ctx)
        given_adapter_success(ctx)
    elif partition == "manual_approval_pending":
        given_tenant_manual_approval(ctx)
        given_adapter_manual_approval(ctx)
    elif partition == "auto_approve_adapter_failure":
        given_tenant_auto_approval(ctx)
        given_adapter_no_manual_approval(ctx)
        given_adapter_error(ctx)
    else:
        raise ValueError(f"Unknown persistence timing partition: {partition}")


@given(parsers.parse("the persistence timing scenario is: {config}"))
def given_persistence_timing_boundary(ctx: dict, config: str) -> None:
    """Configure approval + adapter state for persistence timing boundaries (BR-RULE-020).

    Dispatches to existing tenant/adapter helpers.
    """
    config = config.strip()

    if config == "auto-approve success":
        given_tenant_auto_approval(ctx)
        given_adapter_no_manual_approval(ctx)
        given_adapter_success(ctx)
    elif config == "auto-approve failure":
        given_tenant_auto_approval(ctx)
        given_adapter_no_manual_approval(ctx)
        given_adapter_error(ctx)
    elif config == "manual approval":
        given_tenant_manual_approval(ctx)
        given_adapter_manual_approval(ctx)
    else:
        raise ValueError(f"Unknown persistence timing boundary config: {config}")


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


@given(parsers.parse("a package budget is set to {value}"))
def given_package_budget_set_to(ctx: dict, value: str) -> None:
    """Set first package budget to the given value (supports float)."""
    kwargs = _ensure_request_defaults(ctx)
    if kwargs.get("packages"):
        kwargs["packages"][0]["budget"] = float(value)


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


@given(parsers.parse("the start_time is {value}"))
@given(parsers.parse("start_time is {value}"))
def given_start_time_value(ctx: dict, value: str) -> None:
    """Set or remove start_time on the request (unquoted table value).

    Handles partition/boundary scenarios:
    - 'null' → remove start_time (absent)
    - 'asap' → set literal 'asap'
    - datetime string → set directly
    """
    kwargs = _ensure_request_defaults(ctx)
    value = value.strip()
    if value == "null":
        kwargs.pop("start_time", None)
    else:
        kwargs["start_time"] = value


@given(parsers.parse("the end_time is {value}"))
@given(parsers.parse("end_time is {value}"))
def given_end_time_value(ctx: dict, value: str) -> None:
    """Set or remove end_time on the request (unquoted table value).

    Handles partition/boundary scenarios:
    - 'null' → remove end_time (absent)
    - datetime string → set directly
    """
    kwargs = _ensure_request_defaults(ctx)
    value = value.strip()
    if value == "null":
        kwargs.pop("end_time", None)
    else:
        kwargs["end_time"] = value


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
    """Set a managed-only targeting dimension.

    Uses key_value_pairs which is a real managed-only dimension per
    validate_overlay_targeting() in targeting_capabilities.py.
    """
    kwargs = _ensure_request_defaults(ctx)
    if kwargs.get("packages"):
        kwargs["packages"][0]["targeting_overlay"] = {"key_value_pairs": {"section": "sports"}}


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
    _setup_both_pricing(ctx)


@given("a package pricing option has neither fixed_price nor floor_price")
@given("But a package pricing option has neither fixed_price nor floor_price")
def given_neither_fixed_nor_floor(ctx: dict) -> None:
    """Create a malformed pricing option with no fixed_price and no floor_price.

    ORM: is_fixed=True but rate=None — the pricing option exists but has no usable
    price. This violates BR-RULE-006 which requires exactly one of fixed/floor.

    Production catches this as "has is_fixed=true but no rate specified" in
    _validate_pricing_model_selection (PRICING_ERROR).
    """
    _setup_neither_pricing(ctx)


# ═══════════════════════════════════════════════════════════════════════
# Pricing option XOR partition/boundary — BR-RULE-006
# ═══════════════════════════════════════════════════════════════════════


def _setup_fixed_pricing(ctx: dict, rate: float = 5.00) -> None:
    """Configure a fixed-price pricing option (is_fixed=True, rate set, no floor)."""
    po = ctx.get("default_pricing_option")
    assert po is not None, "No default_pricing_option in ctx"
    if po.is_fixed and po.rate:
        # Default PO is already fixed — just ensure it has the right rate if overridden
        if rate != 5.00:
            env = ctx["env"]
            new_po = PricingOptionFactory(
                product=ctx["default_product"],
                pricing_model="cpm",
                currency="USD",
                is_fixed=True,
                rate=rate,
            )
            env._commit_factory_data()
            kwargs = _ensure_request_defaults(ctx)
            if kwargs.get("packages"):
                kwargs["packages"][0]["pricing_option_id"] = _pricing_option_id(new_po)
        return
    # Default PO is not fixed — create a new fixed one
    env = ctx["env"]
    new_po = PricingOptionFactory(
        product=ctx["default_product"],
        pricing_model="cpm",
        currency="USD",
        is_fixed=True,
        rate=rate,
    )
    env._commit_factory_data()
    kwargs = _ensure_request_defaults(ctx)
    if kwargs.get("packages"):
        kwargs["packages"][0]["pricing_option_id"] = _pricing_option_id(new_po)


def _setup_auction_pricing(ctx: dict, floor: float = 2.0, bid: float = 5.0) -> None:
    """Configure an auction pricing option (is_fixed=False, floor set) with bid_price."""
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


def _setup_both_pricing(ctx: dict, rate: float = 5.00, floor: float = 2.0) -> None:
    """Configure a malformed PO with both fixed_price and floor_price (XOR violation).

    SPEC-PRODUCTION GAP: Production treats this as a valid fixed option because
    _validate_pricing_model_selection only checks is_fixed + rate. The XOR invariant
    is a spec-level constraint not enforced at create_media_buy time.
    """
    env = ctx["env"]
    malformed_po = PricingOptionFactory(
        product=ctx["default_product"],
        pricing_model="cpm",
        currency="USD",
        is_fixed=True,
        rate=rate,
        price_guidance={"floor": floor},
    )
    env._commit_factory_data()
    kwargs = _ensure_request_defaults(ctx)
    if kwargs.get("packages"):
        kwargs["packages"][0]["pricing_option_id"] = _pricing_option_id(malformed_po)


def _setup_neither_pricing(ctx: dict) -> None:
    """Configure a malformed PO with neither fixed_price nor floor_price.

    Production catches this as "has is_fixed=true but no rate specified" → PRICING_ERROR.
    """
    env = ctx["env"]
    malformed_po = PricingOptionFactory(
        product=ctx["default_product"],
        pricing_model="cpm",
        currency="USD",
        is_fixed=True,
        rate=None,
        price_guidance=None,
    )
    env._commit_factory_data()
    kwargs = _ensure_request_defaults(ctx)
    if kwargs.get("packages"):
        kwargs["packages"][0]["pricing_option_id"] = _pricing_option_id(malformed_po)


@given(parsers.parse("the pricing option configuration is {config}"))
def given_pricing_option_configuration(ctx: dict, config: str) -> None:
    """Set up pricing option configuration for partition/boundary scenarios.

    Partition values: fixed_pricing, auction_pricing, cpa_model, both_set, neither_set
    Boundary values: fixed_price=N, floor_price=N, fixed+floor, neither
    """
    config = config.strip()

    if config == "fixed_pricing":
        _setup_fixed_pricing(ctx)

    elif config == "auction_pricing":
        _setup_auction_pricing(ctx)

    elif config == "cpa_model":
        # CPA pricing model — create a fixed CPA option (valid from production's view)
        env = ctx["env"]
        cpa_po = PricingOptionFactory(
            product=ctx["default_product"],
            pricing_model="cpa",
            currency="USD",
            is_fixed=True,
            rate=10.00,
        )
        env._commit_factory_data()
        kwargs = _ensure_request_defaults(ctx)
        if kwargs.get("packages"):
            kwargs["packages"][0]["pricing_option_id"] = _pricing_option_id(cpa_po)

    elif config in ("both_set", "fixed+floor"):
        _setup_both_pricing(ctx)

    elif config in ("neither_set", "neither"):
        _setup_neither_pricing(ctx)

    elif config.startswith("fixed_price="):
        rate = float(config.split("=")[1])
        _setup_fixed_pricing(ctx, rate=rate)

    elif config.startswith("floor_price="):
        floor = float(config.split("=")[1])
        _setup_auction_pricing(ctx, floor=floor, bid=floor + 1.0)

    else:
        raise ValueError(f"Unknown pricing option configuration: {config}")


# ═══════════════════════════════════════════════════════════════════════
# Currency consistency partition/boundary — BR-RULE-009
# ═══════════════════════════════════════════════════════════════════════


def _setup_multi_package_request(ctx: dict, currencies: list[str]) -> None:
    """Create a request with N packages, each using a pricing option with the given currency.

    Also ensures a CurrencyLimit row exists for each currency that should be
    in the tenant's table.
    """
    env = ctx["env"]
    kwargs = _ensure_request_defaults(ctx)
    packages = []

    for i, currency in enumerate(currencies):
        if i == 0:
            # Re-use the default product for the first package
            product = ctx["default_product"]
        else:
            product = ProductFactory(
                tenant=ctx["tenant"],
                product_id=f"product_{currency.lower()}_{i}",
                property_tags=["all_inventory"],
            )
        po = PricingOptionFactory(
            product=product,
            pricing_model="cpm",
            currency=currency,
            is_fixed=True,
            rate=5.00,
        )
        env._commit_factory_data()
        packages.append(
            {
                "product_id": product.product_id,
                "buyer_ref": f"pkg-{i + 1}",
                "budget": 5000.0,
                "pricing_option_id": _pricing_option_id(po),
            }
        )

    kwargs["packages"] = packages


@given(parsers.parse("the currency scenario is {partition}"))
def given_currency_scenario(ctx: dict, partition: str) -> None:
    """Set up currency configuration for partition scenarios (BR-RULE-009).

    Partition values:
    - single_package: 1 package, USD (trivially valid — only 1 currency)
    - all_same_currency: 2 packages, both USD
    - currency_in_tenant_table: 1 package, EUR (tenant has EUR in CurrencyLimit)
    - mixed_currencies: 2 packages, USD + EUR (cross-package mismatch)
    - currency_not_in_tenant: 1 package, XYZ (not in tenant's CurrencyLimit table)
    """
    env = ctx["env"]
    partition = partition.strip()

    if partition == "single_package":
        # Default request has 1 package with USD — already valid
        _ensure_request_defaults(ctx)

    elif partition == "all_same_currency":
        _setup_multi_package_request(ctx, ["USD", "USD"])

    elif partition == "currency_in_tenant_table":
        # Add EUR to tenant's CurrencyLimit table, then use EUR pricing option
        CurrencyLimitFactory(tenant=ctx["tenant"], currency_code="EUR")
        env._commit_factory_data()
        kwargs = _ensure_request_defaults(ctx)
        po_eur = PricingOptionFactory(
            product=ctx["default_product"],
            pricing_model="cpm",
            currency="EUR",
            is_fixed=True,
            rate=5.00,
        )
        env._commit_factory_data()
        if kwargs.get("packages"):
            kwargs["packages"][0]["pricing_option_id"] = _pricing_option_id(po_eur)

    elif partition == "mixed_currencies":
        # 2 packages with different currencies — production derives currency from
        # the first package's pricing option, so the second package's currency
        # mismatch may or may not trigger an error depending on production logic.
        _setup_multi_package_request(ctx, ["USD", "EUR"])

    elif partition == "currency_not_in_tenant":
        # Use a currency not in tenant's CurrencyLimit table
        kwargs = _ensure_request_defaults(ctx)
        po_xyz = PricingOptionFactory(
            product=ctx["default_product"],
            pricing_model="cpm",
            currency="XYZ",
            is_fixed=True,
            rate=5.00,
        )
        env._commit_factory_data()
        if kwargs.get("packages"):
            kwargs["packages"][0]["pricing_option_id"] = _pricing_option_id(po_xyz)

    else:
        raise ValueError(f"Unknown currency partition: {partition}")


@given(parsers.parse("the currency configuration is: {config}"))
def given_currency_configuration(ctx: dict, config: str) -> None:
    """Set up currency configuration for boundary scenarios (BR-RULE-009).

    Boundary values:
    - 1 pkg USD: single package, USD
    - 2 pkg USD+USD: two packages, same currency
    - 2 pkg USD+EUR: two packages, different currencies
    - 1 pkg XYZ: single package, unsupported currency
    """
    env = ctx["env"]
    config = config.strip()

    if config == "1 pkg USD":
        # Default request already has 1 package with USD
        _ensure_request_defaults(ctx)

    elif config == "2 pkg USD+USD":
        _setup_multi_package_request(ctx, ["USD", "USD"])

    elif config == "2 pkg USD+EUR":
        _setup_multi_package_request(ctx, ["USD", "EUR"])

    elif config == "1 pkg XYZ":
        kwargs = _ensure_request_defaults(ctx)
        po_xyz = PricingOptionFactory(
            product=ctx["default_product"],
            pricing_model="cpm",
            currency="XYZ",
            is_fixed=True,
            rate=5.00,
        )
        env._commit_factory_data()
        if kwargs.get("packages"):
            kwargs["packages"][0]["pricing_option_id"] = _pricing_option_id(po_xyz)

    else:
        raise ValueError(f"Unknown currency boundary config: {config}")


# ═══════════════════════════════════════════════════════════════════════
# Product uniqueness partition/boundary — BR-RULE-010
# ═══════════════════════════════════════════════════════════════════════


def _setup_multi_product_request(ctx: dict, product_ids: list[str]) -> None:
    """Create a request with N packages, each using the given product_id.

    All packages use USD/CPM/fixed pricing — currency is not the variable under test.
    Duplicate product_ids are intentional for testing the uniqueness constraint.
    """
    env = ctx["env"]
    kwargs = _ensure_request_defaults(ctx)
    packages = []

    for i, pid in enumerate(product_ids):
        default_product = ctx.get("default_product")
        if default_product is not None and pid == default_product.product_id:
            product = default_product
        else:
            # Check if product already exists in the session (dedup for same pid)
            existing = ctx.get(f"_product_{pid}")
            if existing is not None:
                product = existing
            else:
                product = ProductFactory(
                    tenant=ctx["tenant"],
                    product_id=pid,
                    property_tags=["all_inventory"],
                )
                ctx[f"_product_{pid}"] = product

        po = PricingOptionFactory(
            product=product,
            pricing_model="cpm",
            currency="USD",
            is_fixed=True,
            rate=5.00,
        )
        env._commit_factory_data()
        packages.append(
            {
                "product_id": product.product_id,
                "buyer_ref": f"pkg-{i + 1}",
                "budget": 5000.0,
                "pricing_option_id": _pricing_option_id(po),
            }
        )

    kwargs["packages"] = packages


@given(parsers.parse("the product scenario is {partition}"))
def given_product_scenario(ctx: dict, partition: str) -> None:
    """Set up product configuration for partition scenarios (BR-RULE-010).

    Partition values:
    - single_package: 1 package, 1 product (trivially unique)
    - distinct_products: 2 packages, different product_ids
    - duplicate_product: 2 packages, same product_id (uniqueness violation)
    """
    partition = partition.strip()

    default_pid = ctx["default_product"].product_id

    if partition == "single_package":
        _ensure_request_defaults(ctx)

    elif partition == "distinct_products":
        _setup_multi_product_request(ctx, [default_pid, "product_b"])

    elif partition == "duplicate_product":
        _setup_multi_product_request(ctx, [default_pid, default_pid])

    else:
        raise ValueError(f"Unknown product partition: {partition}")


@given(parsers.parse("the product configuration is: {config}"))
def given_product_configuration(ctx: dict, config: str) -> None:
    """Set up product configuration for boundary scenarios (BR-RULE-010).

    Boundary values:
    - 1 pkg prod-A: single package (trivially unique)
    - 2 pkg prod-A,B: two packages, different products
    - 2 pkg prod-A,A: two packages, same product_id (uniqueness violation)
    """
    config = config.strip()

    if config == "1 pkg prod-A":
        # Single package — reuse harness default (mirrors currency boundary "1 pkg USD")
        _ensure_request_defaults(ctx)

    elif config == "2 pkg prod-A,B":
        _setup_multi_product_request(ctx, ["prod-A", "prod-B"])

    elif config == "2 pkg prod-A,A":
        _setup_multi_product_request(ctx, ["prod-A", "prod-A"])

    else:
        raise ValueError(f"Unknown product boundary config: {config}")


# ═══════════════════════════════════════════════════════════════════════
# Minimum spend partition/boundary — BR-RULE-008
# ═══════════════════════════════════════════════════════════════════════


def _set_min_spend(ctx: dict, *, product_min: float | None, tenant_min: float | None, budget: float) -> None:
    """Configure minimum spend thresholds and package budget.

    Sets PricingOption.min_spend_per_package (product-level) and
    CurrencyLimit.min_package_budget (tenant-level), then adjusts
    the request package budget.
    """
    from decimal import Decimal

    env = ctx["env"]
    kwargs = _ensure_request_defaults(ctx)

    # Product-level minimum: update the default pricing option
    po = ctx["default_pricing_option"]
    po.min_spend_per_package = Decimal(str(product_min)) if product_min is not None else None
    env._commit_factory_data()

    # Tenant-level minimum: update the existing CurrencyLimit row
    from sqlalchemy import select

    from src.core.database.models import CurrencyLimit

    cl = env._session.scalars(
        select(CurrencyLimit).filter_by(
            tenant_id=ctx["tenant"].tenant_id,
            currency_code="USD",
        )
    ).one()
    cl.min_package_budget = Decimal(str(tenant_min)) if tenant_min is not None else None
    env._commit_factory_data()

    # Set package budget
    if kwargs.get("packages"):
        kwargs["packages"][0]["budget"] = budget


@given(parsers.parse("the minimum spend scenario is {partition}"))
def given_minimum_spend_scenario(ctx: dict, partition: str) -> None:
    """Set up minimum spend configuration for partition scenarios (BR-RULE-008).

    Partition values:
    - budget_meets_product_min: budget 5000 >= product min_spend 1000
    - budget_meets_tenant_min: budget 5000 >= tenant min_package_budget 100 (no product min)
    - no_minimum_configured: no min_spend at either level
    - budget_below_product_min: budget 50 < product min_spend 1000
    - budget_below_tenant_min: budget 50 < tenant min_package_budget 100 (no product min)
    """
    partition = partition.strip()

    if partition == "budget_meets_product_min":
        _set_min_spend(ctx, product_min=1000.0, tenant_min=100.0, budget=5000.0)

    elif partition == "budget_meets_tenant_min":
        _set_min_spend(ctx, product_min=None, tenant_min=100.0, budget=5000.0)

    elif partition == "no_minimum_configured":
        _set_min_spend(ctx, product_min=None, tenant_min=None, budget=5000.0)

    elif partition == "budget_below_product_min":
        _set_min_spend(ctx, product_min=1000.0, tenant_min=100.0, budget=50.0)

    elif partition == "budget_below_tenant_min":
        _set_min_spend(ctx, product_min=None, tenant_min=100.0, budget=50.0)

    else:
        raise ValueError(f"Unknown minimum spend partition: {partition}")


@given(parsers.parse("the minimum spend configuration is: {config}"))
def given_minimum_spend_configuration(ctx: dict, config: str) -> None:
    """Set up minimum spend configuration for boundary scenarios (BR-RULE-008).

    Boundary values:
    - budget=100 min=100: budget exactly at product min_spend (exact match)
    - budget=99.99 min=100: budget just below product min_spend
    - budget=50 tmin=50: budget exactly at tenant min_package_budget (no product min)
    - budget=49.99 tmin=50: budget just below tenant min_package_budget
    - budget=1 no-min: no minimum configured at any level
    """
    config = config.strip()

    if config == "budget=100 min=100":
        _set_min_spend(ctx, product_min=100.0, tenant_min=100.0, budget=100.0)

    elif config == "budget=99.99 min=100":
        _set_min_spend(ctx, product_min=100.0, tenant_min=100.0, budget=99.99)

    elif config == "budget=50 tmin=50":
        _set_min_spend(ctx, product_min=None, tenant_min=50.0, budget=50.0)

    elif config == "budget=49.99 tmin=50":
        _set_min_spend(ctx, product_min=None, tenant_min=50.0, budget=49.99)

    elif config == "budget=1 no-min":
        _set_min_spend(ctx, product_min=None, tenant_min=None, budget=1.0)

    else:
        raise ValueError(f"Unknown minimum spend boundary config: {config}")


# ═══════════════════════════════════════════════════════════════════════
# Daily spend cap partition/boundary — BR-RULE-012
# ═══════════════════════════════════════════════════════════════════════


def _set_daily_spend_cap(ctx: dict, *, cap: float | None, budget: float, flight_days: int = 10) -> None:
    """Configure daily spend cap and package budget/flight duration.

    Sets CurrencyLimit.max_daily_package_spend (tenant-level cap) and adjusts
    the request package budget and flight dates to achieve the target daily spend.

    Daily spend = budget / max(flight_days, 1).
    """
    from decimal import Decimal

    from sqlalchemy import select

    from src.core.database.models import CurrencyLimit

    env = ctx["env"]
    kwargs = _ensure_request_defaults(ctx)

    # Set flight duration via start_time / end_time
    kwargs["start_time"] = _future(1).isoformat()
    kwargs["end_time"] = _future(1 + flight_days).isoformat()

    # Set package budget
    if kwargs.get("packages"):
        kwargs["packages"][0]["budget"] = budget

    # Set cap on CurrencyLimit
    cl = env._session.scalars(
        select(CurrencyLimit).filter_by(
            tenant_id=ctx["tenant"].tenant_id,
            currency_code="USD",
        )
    ).one()
    cl.max_daily_package_spend = Decimal(str(cap)) if cap is not None else None
    env._commit_factory_data()


@given(parsers.parse("the daily spend cap scenario is {partition}"))
def given_daily_spend_cap_scenario(ctx: dict, partition: str) -> None:
    """Set up daily spend cap configuration for partition scenarios (BR-RULE-012).

    Partition values (daily = budget / flight_days):
    - below_cap: daily 50 < cap 100
    - cap_not_configured: no cap set (check skipped)
    - at_cap_exactly: daily 100 == cap 100
    - exceeds_cap: daily 200 > cap 100
    """
    partition = partition.strip()

    if partition == "below_cap":
        _set_daily_spend_cap(ctx, cap=100.0, budget=500.0)

    elif partition == "cap_not_configured":
        _set_daily_spend_cap(ctx, cap=None, budget=5000.0)

    elif partition == "at_cap_exactly":
        _set_daily_spend_cap(ctx, cap=100.0, budget=1000.0)

    elif partition == "exceeds_cap":
        _set_daily_spend_cap(ctx, cap=100.0, budget=2000.0)

    else:
        raise ValueError(f"Unknown daily spend cap partition: {partition}")


@given(parsers.parse("the daily spend scenario is: {config}"))
def given_daily_spend_boundary(ctx: dict, config: str) -> None:
    """Set up daily spend cap for boundary scenarios (BR-RULE-012).

    Boundary configs:
    - daily=1000 cap=1000: at limit (budget=10000, 10-day flight)
    - daily=1001 cap=1000: exceeds by 1 (budget=10010, 10-day flight)
    - daily=9999 no-cap: no cap configured (check skipped)
    - 0-day-flight: start==end, production floors to 1 day
    """
    config = config.strip()

    if config == "daily=1000 cap=1000":
        _set_daily_spend_cap(ctx, cap=1000.0, budget=10000.0)

    elif config == "daily=1001 cap=1000":
        _set_daily_spend_cap(ctx, cap=1000.0, budget=10010.0)

    elif config == "daily=9999 no-cap":
        _set_daily_spend_cap(ctx, cap=None, budget=99990.0)

    elif config == "0-day-flight":
        # 0-day flight: start==end, production floors flight_days to 1
        # daily = budget / 1 = 500, cap = 1000 → passes
        _set_daily_spend_cap(ctx, cap=1000.0, budget=500.0, flight_days=0)

    else:
        raise ValueError(f"Unknown daily spend boundary config: {config}")


# ═══════════════════════════════════════════════════════════════════════
# Targeting overlay partition/boundary — BR-RULE-014
# ═══════════════════════════════════════════════════════════════════════


def _set_targeting_overlay(ctx: dict, overlay: dict[str, Any] | None) -> None:
    """Set the targeting_overlay on the first package in the request.

    None means absent overlay (field omitted entirely).
    {} means empty overlay.
    """
    kwargs = _ensure_request_defaults(ctx)
    if kwargs.get("packages"):
        if overlay is None:
            kwargs["packages"][0].pop("targeting_overlay", None)
        else:
            kwargs["packages"][0]["targeting_overlay"] = overlay


@given(parsers.parse("the targeting overlay scenario is {partition}"))
def given_targeting_overlay_partition(ctx: dict, partition: str) -> None:
    """Set up targeting overlay for partition scenarios (BR-RULE-014).

    Valid partitions (12): targeting validation passes
    Invalid partitions (7): error INVALID_REQUEST with suggestion
    """
    partition = partition.strip()

    # ── Valid partitions ──
    if partition == "absent_overlay":
        _set_targeting_overlay(ctx, overlay=None)

    elif partition == "valid_overlay":
        _set_targeting_overlay(ctx, overlay={"geo_countries": ["US", "CA"]})

    elif partition == "empty_overlay":
        _set_targeting_overlay(ctx, overlay={})

    elif partition == "single_geo_dimension":
        _set_targeting_overlay(ctx, overlay={"geo_countries": ["US"]})

    elif partition == "multiple_dimensions":
        _set_targeting_overlay(
            ctx,
            overlay={
                "geo_countries": ["US"],
                "device_type_any_of": ["mobile", "desktop"],
            },
        )

    elif partition == "frequency_cap_suppress_only":
        _set_targeting_overlay(
            ctx,
            overlay={
                "frequency_cap": {"suppress_minutes": 60.0},
            },
        )

    elif partition == "frequency_cap_max_impressions_only":
        _set_targeting_overlay(
            ctx,
            overlay={
                "frequency_cap": {
                    "max_impressions": 3,
                    "per": "devices",
                    "window": {"interval": 24, "unit": "hours"},
                },
            },
        )

    elif partition == "frequency_cap_combined":
        _set_targeting_overlay(
            ctx,
            overlay={
                "frequency_cap": {
                    "suppress_minutes": 30.0,
                    "max_impressions": 5,
                    "per": "devices",
                    "window": {"interval": 1, "unit": "days"},
                },
            },
        )

    elif partition == "keyword_targeting":
        _set_targeting_overlay(
            ctx,
            overlay={
                "keyword_targets": [
                    {"keyword": "shoes", "match_type": "exact"},
                ],
            },
        )

    elif partition == "proximity_travel_time":
        _set_targeting_overlay(
            ctx,
            overlay={
                "geo_proximity": [
                    {
                        "lat": 40.7128,
                        "lng": -74.0060,
                        "travel_time": {"value": 30, "unit": "min"},
                        "transport_mode": "driving",
                    }
                ],
            },
        )

    elif partition == "proximity_radius":
        _set_targeting_overlay(
            ctx,
            overlay={
                "geo_proximity": [
                    {
                        "lat": 40.7128,
                        "lng": -74.0060,
                        "radius": {"value": 5, "unit": "km"},
                    }
                ],
            },
        )

    elif partition == "proximity_geometry":
        _set_targeting_overlay(
            ctx,
            overlay={
                "geo_proximity": [
                    {
                        "geometry": {
                            "type": "Polygon",
                            "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
                        },
                    }
                ],
            },
        )

    # ── Invalid partitions ──
    elif partition == "unknown_field":
        _set_targeting_overlay(ctx, overlay={"weather_targeting": "sunny"})

    elif partition == "managed_only_dimension":
        _set_targeting_overlay(ctx, overlay={"key_value_pairs": {"section": "sports"}})

    elif partition == "geo_overlap":
        _set_targeting_overlay(
            ctx,
            overlay={
                "geo_countries": ["US"],
                "geo_countries_exclude": ["US"],
            },
        )

    elif partition == "device_type_overlap":
        _set_targeting_overlay(
            ctx,
            overlay={
                "device_type_any_of": ["mobile"],
                "device_type_none_of": ["mobile"],
            },
        )

    elif partition == "proximity_method_conflict":
        _set_targeting_overlay(
            ctx,
            overlay={
                "geo_proximity": [
                    {
                        "lat": 40.7128,
                        "lng": -74.0060,
                        "travel_time": {"value": 30, "unit": "min"},
                        "transport_mode": "driving",
                        "radius": {"value": 5, "unit": "km"},
                    }
                ],
            },
        )

    elif partition == "frequency_cap_missing_fields":
        _set_targeting_overlay(
            ctx,
            overlay={
                "frequency_cap": {"max_impressions": 3},
            },
        )

    elif partition == "keyword_duplicate":
        _set_targeting_overlay(
            ctx,
            overlay={
                "keyword_targets": [
                    {"keyword": "shoes", "match_type": "exact"},
                    {"keyword": "shoes", "match_type": "exact"},
                ],
            },
        )

    else:
        raise ValueError(f"Unknown targeting overlay partition: {partition}")


@given(parsers.parse("the targeting overlay scenario is: {config}"))
def given_targeting_overlay_boundary(ctx: dict, config: str) -> None:
    """Set up targeting overlay for boundary scenarios (BR-RULE-014).

    Boundary configs map to edge-case values for each targeting dimension.
    """
    config = config.strip()

    if config == "no overlay":
        _set_targeting_overlay(ctx, overlay=None)

    elif config == "empty":
        _set_targeting_overlay(ctx, overlay={})

    elif config == "geo_countries=US":
        _set_targeting_overlay(ctx, overlay={"geo_countries": ["US"]})

    elif config == "weather=sunny":
        _set_targeting_overlay(ctx, overlay={"weather": "sunny"})

    elif config == "managed dimension":
        _set_targeting_overlay(ctx, overlay={"key_value_pairs": {"section": "sports"}})

    elif config == "US in both lists":
        _set_targeting_overlay(
            ctx,
            overlay={
                "geo_countries": ["US"],
                "geo_countries_exclude": ["US"],
            },
        )

    elif config == "mobile in both":
        _set_targeting_overlay(
            ctx,
            overlay={
                "device_type_any_of": ["mobile"],
                "device_type_none_of": ["mobile"],
            },
        )

    elif config == "travel_time=30m":
        _set_targeting_overlay(
            ctx,
            overlay={
                "geo_proximity": [
                    {
                        "lat": 40.7128,
                        "lng": -74.0060,
                        "travel_time": {"value": 30, "unit": "min"},
                        "transport_mode": "driving",
                    }
                ],
            },
        )

    elif config == "radius=5km":
        _set_targeting_overlay(
            ctx,
            overlay={
                "geo_proximity": [
                    {
                        "lat": 40.7128,
                        "lng": -74.0060,
                        "radius": {"value": 5, "unit": "km"},
                    }
                ],
            },
        )

    elif config == "geometry=polygon":
        _set_targeting_overlay(
            ctx,
            overlay={
                "geo_proximity": [
                    {
                        "geometry": {
                            "type": "Polygon",
                            "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
                        },
                    }
                ],
            },
        )

    elif config == "travel+radius":
        _set_targeting_overlay(
            ctx,
            overlay={
                "geo_proximity": [
                    {
                        "lat": 40.7128,
                        "lng": -74.0060,
                        "travel_time": {"value": 30, "unit": "min"},
                        "transport_mode": "driving",
                        "radius": {"value": 5, "unit": "km"},
                    }
                ],
            },
        )

    elif config == "suppress=24h":
        _set_targeting_overlay(
            ctx,
            overlay={
                "frequency_cap": {"suppress_minutes": 1440.0},
            },
        )

    elif config == "max=3 per=1 win=24h":
        _set_targeting_overlay(
            ctx,
            overlay={
                "frequency_cap": {
                    "max_impressions": 3,
                    "per": "devices",
                    "window": {"interval": 24, "unit": "hours"},
                },
            },
        )

    elif config == "max=3 no-per":
        _set_targeting_overlay(
            ctx,
            overlay={
                "frequency_cap": {"max_impressions": 3},
            },
        )

    elif config == "kw=shoes exact":
        _set_targeting_overlay(
            ctx,
            overlay={
                "keyword_targets": [
                    {"keyword": "shoes", "match_type": "exact"},
                ],
            },
        )

    elif config == "kw=shoes exact x2":
        _set_targeting_overlay(
            ctx,
            overlay={
                "keyword_targets": [
                    {"keyword": "shoes", "match_type": "exact"},
                    {"keyword": "shoes", "match_type": "exact"},
                ],
            },
        )

    else:
        raise ValueError(f"Unknown targeting overlay boundary config: {config}")


# ═══════════════════════════════════════════════════════════════════════
# Creative asset partition/boundary — BR-RULE-015
# ═══════════════════════════════════════════════════════════════════════


def _add_creative_ids_to_package(ctx: dict, creative_ids: list[str]) -> None:
    """Add creative_ids to the first package in the request.

    Merges with any existing creative_ids. Ensures request_kwargs exists.
    """
    kwargs = _ensure_request_defaults(ctx)
    if kwargs.get("packages"):
        pkg = kwargs["packages"][0]
        existing = pkg.get("creative_ids") or []
        existing.extend(creative_ids)
        pkg["creative_ids"] = existing


def _create_approved_creative(ctx: dict, creative_id: str, fmt: str = "display_300x250") -> Any:
    """Create an approved Creative in the DB and return it.

    Uses CreativeFactory with the tenant/principal from harness context.
    Asset key uses "primary" to match the harness format spec's asset_id.
    """
    from tests.factories.creative import CreativeFactory

    env = ctx["env"]
    creative = CreativeFactory(
        creative_id=creative_id,
        tenant=ctx["tenant"],
        principal=ctx["principal"],
        format=fmt,
        approved=True,
        data={"assets": {"primary": {"url": "https://example.com/banner.png", "width": 300, "height": 250}}},
    )
    env._commit_factory_data()
    return creative


def _add_inline_creatives(ctx: dict, count: int = 1, fmt_id: str = "display_300x250") -> None:
    """Add inline creative dicts to the first package's 'creatives' field.

    Builds minimal creative payloads matching the product's accepted format.
    """
    kwargs = _ensure_request_defaults(ctx)
    if kwargs.get("packages"):
        pkg = kwargs["packages"][0]
        creatives = pkg.get("creatives") or []
        for i in range(count):
            creatives.append(
                {
                    "creative_id": f"inline-cr-{i + 1:03d}",
                    "name": f"Inline Creative {i + 1}",
                    "format_id": {
                        "agent_url": "https://creative.adcontextprotocol.org",
                        "id": fmt_id,
                    },
                    "assets": {
                        "primary": {
                            "url": f"https://example.com/banner-{i + 1}.png",
                            "width": 300,
                            "height": 250,
                        }
                    },
                }
            )
        pkg["creatives"] = creatives


@given(parsers.parse("the creative scenario is {partition}"))
def given_creative_partition(ctx: dict, partition: str) -> None:
    """Set up creative configuration for partition scenarios (BR-RULE-015).

    Valid partitions (6): creative validation passes
    Invalid partitions (4): error CREATIVE_REJECTED or INVALID_REQUEST
    """
    partition = partition.strip()

    # ── Valid partitions ──
    if partition == "no_creatives":
        # Default request has no creative_ids or inline creatives
        _ensure_request_defaults(ctx)

    elif partition == "assignments_only":
        # Reference an existing approved creative via creative_ids
        creative = _create_approved_creative(ctx, "cr-assign-001")
        _add_creative_ids_to_package(ctx, [creative.creative_id])

    elif partition == "uploads_only":
        # Inline creative objects on the package (no library references)
        _add_inline_creatives(ctx, count=1)

    elif partition == "both_paths":
        # Both library references AND inline uploads
        creative = _create_approved_creative(ctx, "cr-both-001")
        _add_creative_ids_to_package(ctx, [creative.creative_id])
        _add_inline_creatives(ctx, count=1)

    elif partition == "assignment_with_weight_zero":
        # Approved creative referenced by ID — weight=0 is an assignment-level
        # attribute applied post-creation; validation should still pass.
        creative = _create_approved_creative(ctx, "cr-weight0-001")
        _add_creative_ids_to_package(ctx, [creative.creative_id])

    elif partition == "assignment_with_placement_targeting":
        # Approved creative referenced by ID — placement targeting is an
        # assignment-level attribute; validation should still pass.
        creative = _create_approved_creative(ctx, "cr-placement-001")
        _add_creative_ids_to_package(ctx, [creative.creative_id])

    # ── Invalid partitions ──
    elif partition == "creative_not_found":
        # Reference a creative_id that doesn't exist in the DB
        _add_creative_ids_to_package(ctx, ["cr-nonexistent-999"])

    elif partition == "format_mismatch":
        # Creative with format that doesn't match the product's supported formats
        creative = _create_approved_creative(ctx, "cr-badfmt-001", fmt="video_640x480")
        _add_creative_ids_to_package(ctx, [creative.creative_id])

    elif partition == "missing_required_assets":
        # Creative exists but has empty assets (validation should reject)
        from tests.factories.creative import CreativeFactory

        env = ctx["env"]
        creative = CreativeFactory(
            creative_id="cr-noassets-001",
            tenant=ctx["tenant"],
            principal=ctx["principal"],
            format="display_300x250",
            approved=True,
            data={},  # No assets
        )
        env._commit_factory_data()
        _add_creative_ids_to_package(ctx, [creative.creative_id])

    elif partition == "exceeds_max_creatives":
        # 101 inline creatives — exceeds the spec limit of 100
        _add_inline_creatives(ctx, count=101)

    else:
        raise ValueError(f"Unknown creative asset partition: {partition}")


@given(parsers.parse("the creative scenario is: {config}"))
def given_creative_boundary(ctx: dict, config: str) -> None:
    """Set up creative configuration for boundary scenarios (BR-RULE-015).

    Boundary configs test edge values for creative assignment and upload.
    """
    config = config.strip()

    if config == "no creatives":
        _ensure_request_defaults(ctx)

    elif config == "assignment cr-001":
        # Single library creative reference
        creative = _create_approved_creative(ctx, "cr-001")
        _add_creative_ids_to_package(ctx, [creative.creative_id])

    elif config == "upload with format":
        # Single inline creative with proper format matching product
        _add_inline_creatives(ctx, count=1)

    elif config == "assignment cr-bad":
        # Reference a creative_id that doesn't exist
        _add_creative_ids_to_package(ctx, ["cr-bad"])

    elif config == "wrong format":
        # Creative with mismatched format
        creative = _create_approved_creative(ctx, "cr-wrongfmt", fmt="video_640x480")
        _add_creative_ids_to_package(ctx, [creative.creative_id])

    elif config == "weight=0":
        # Creative reference — weight=0 (paused) is valid
        creative = _create_approved_creative(ctx, "cr-w0")
        _add_creative_ids_to_package(ctx, [creative.creative_id])

    elif config == "weight=100":
        # Creative reference — weight=100 (max rotation) is valid
        creative = _create_approved_creative(ctx, "cr-w100")
        _add_creative_ids_to_package(ctx, [creative.creative_id])

    elif config == "101 uploads":
        # 101 inline creatives — exceeds spec limit
        _add_inline_creatives(ctx, count=101)

    else:
        raise ValueError(f"Unknown creative asset boundary config: {config}")


# ═══════════════════════════════════════════════════════════════════════
# Optimization goals partition/boundary — BR-RULE-087
# ═══════════════════════════════════════════════════════════════════════


def _set_optimization_goals(ctx: dict, goals: list[dict[str, Any]] | None) -> None:
    """Set optimization_goals on the first package in the request.

    None means field omitted entirely.
    """
    kwargs = _ensure_request_defaults(ctx)
    if kwargs.get("packages"):
        if goals is None:
            kwargs["packages"][0].pop("optimization_goals", None)
        else:
            kwargs["packages"][0]["optimization_goals"] = goals


def _metric_goal(
    metric: str = "clicks",
    *,
    priority: int | None = None,
    target: dict[str, Any] | None = None,
    view_duration_seconds: float | None = None,
    reach_unit: str | None = None,
    target_frequency: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a metric-kind optimization goal dict."""
    goal: dict[str, Any] = {"kind": "metric", "metric": metric}
    if priority is not None:
        goal["priority"] = priority
    if target is not None:
        goal["target"] = target
    if view_duration_seconds is not None:
        goal["view_duration_seconds"] = view_duration_seconds
    if reach_unit is not None:
        goal["reach_unit"] = reach_unit
    if target_frequency is not None:
        goal["target_frequency"] = target_frequency
    return goal


def _event_goal(
    *,
    event_sources: list[dict[str, Any]] | None = None,
    priority: int | None = None,
    target: dict[str, Any] | None = None,
    attribution_window: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build an event-kind optimization goal dict."""
    goal: dict[str, Any] = {"kind": "event"}
    if event_sources is not None:
        goal["event_sources"] = event_sources
    if priority is not None:
        goal["priority"] = priority
    if target is not None:
        goal["target"] = target
    if attribution_window is not None:
        goal["attribution_window"] = attribution_window
    return goal


def _simple_event_sources(
    *,
    event_source_id: str = "pixel-1",
    event_type: str = "purchase",
    value_field: str | None = None,
    value_factor: float | None = None,
) -> list[dict[str, Any]]:
    """Build a minimal event_sources list."""
    src: dict[str, Any] = {"event_source_id": event_source_id, "event_type": event_type}
    if value_field is not None:
        src["value_field"] = value_field
    if value_factor is not None:
        src["value_factor"] = value_factor
    return [src]


@given(parsers.parse("the optimization goal scenario is {partition}"))
def given_optimization_goal_partition(ctx: dict, partition: str) -> None:
    """Set up optimization goals for partition scenarios (BR-RULE-087).

    SPEC-PRODUCTION GAP: optimization_goals is not in adcp v3.6.0 or production
    schemas. PackageRequest(extra='forbid') will reject the field. All scenarios
    are expected to xfail via conftest.py tag-based xfail.

    Valid partitions (11): optimization validation passes
    Invalid partitions (12): error UNSUPPORTED_FEATURE/INVALID_REQUEST with suggestion
    """
    partition = partition.strip()

    # ── Valid partitions ──
    if partition == "single_metric_goal":
        _set_optimization_goals(ctx, [_metric_goal("clicks")])

    elif partition == "single_event_goal":
        _set_optimization_goals(ctx, [_event_goal(event_sources=_simple_event_sources())])

    elif partition == "multiple_goals_unique_priorities":
        _set_optimization_goals(
            ctx,
            [
                _metric_goal("clicks", priority=1),
                _event_goal(event_sources=_simple_event_sources(), priority=2),
            ],
        )

    elif partition == "metric_completed_views_with_duration":
        _set_optimization_goals(ctx, [_metric_goal("completed_views", view_duration_seconds=15)])

    elif partition == "metric_reach_with_unit":
        _set_optimization_goals(ctx, [_metric_goal("reach", reach_unit="individuals")])

    elif partition == "event_goal_with_attribution_window":
        _set_optimization_goals(
            ctx,
            [
                _event_goal(
                    event_sources=_simple_event_sources(),
                    attribution_window={
                        "post_click": {"interval": 7, "unit": "days"},
                        "post_view": {"interval": 1, "unit": "days"},
                    },
                )
            ],
        )

    elif partition == "metric_goal_with_target":
        _set_optimization_goals(ctx, [_metric_goal("clicks", target={"kind": "cost_per", "value": 0.50})])

    elif partition == "event_goal_with_roas_target":
        _set_optimization_goals(
            ctx,
            [
                _event_goal(
                    event_sources=_simple_event_sources(value_field="value"),
                    target={"kind": "per_ad_spend", "value": 4.0},
                )
            ],
        )

    elif partition == "goals_at_max_count":
        # 10 goals with unique priorities — at the spec cap
        goals = [_metric_goal("clicks", priority=i + 1) for i in range(10)]
        _set_optimization_goals(ctx, goals)

    elif partition == "reach_with_target_frequency":
        _set_optimization_goals(
            ctx,
            [
                _metric_goal(
                    "reach",
                    reach_unit="individuals",
                    target_frequency={"min": 1, "max": 3, "window": {"interval": 7, "unit": "days"}},
                )
            ],
        )

    elif partition == "event_multi_source_dedup":
        _set_optimization_goals(
            ctx,
            [
                _event_goal(
                    event_sources=[
                        {"event_source_id": "pixel", "event_type": "purchase", "value_field": "value"},
                        {
                            "event_source_id": "api",
                            "event_type": "purchase",
                            "value_field": "order_total",
                            "value_factor": 0.01,
                        },
                    ]
                )
            ],
        )

    # ── Invalid partitions ──
    elif partition == "unsupported_metric":
        _set_optimization_goals(ctx, [_metric_goal("attention_score")])

    elif partition == "unregistered_event_source":
        _set_optimization_goals(
            ctx,
            [
                _event_goal(
                    event_sources=[{"event_source_id": "evt-unregistered-999", "event_type": "purchase"}],
                )
            ],
        )

    elif partition == "duplicate_priority":
        _set_optimization_goals(
            ctx,
            [
                _metric_goal("clicks", priority=1),
                _metric_goal("views", priority=1),
            ],
        )

    elif partition == "unsupported_view_duration":
        _set_optimization_goals(ctx, [_metric_goal("completed_views", view_duration_seconds=-1)])

    elif partition == "unsupported_reach_unit":
        _set_optimization_goals(ctx, [_metric_goal("reach", reach_unit="households")])

    elif partition == "unsupported_attribution_window":
        _set_optimization_goals(
            ctx,
            [
                _event_goal(
                    event_sources=_simple_event_sources(),
                    attribution_window={"post_click": {"interval": 365, "unit": "days"}},
                )
            ],
        )

    elif partition == "empty_array":
        _set_optimization_goals(ctx, [])

    elif partition == "exceeds_max_goals":
        goals = [_metric_goal("clicks", priority=i + 1) for i in range(11)]
        _set_optimization_goals(ctx, goals)

    elif partition == "unsupported_target_kind":
        _set_optimization_goals(ctx, [_metric_goal("clicks", target={"kind": "unsupported_kind", "value": 1.0})])

    elif partition == "value_target_without_value_field":
        _set_optimization_goals(
            ctx,
            [
                _event_goal(
                    event_sources=_simple_event_sources(),  # no value_field
                    target={"kind": "per_ad_spend", "value": 4.0},
                )
            ],
        )

    elif partition == "metric_not_supported_by_product":
        # Configure product to not support metric optimization
        _set_optimization_goals(ctx, [_metric_goal("viewability")])
        ctx["product_lacks_metric_optimization"] = True

    elif partition == "event_not_supported_by_product":
        # Configure product to not support event/conversion tracking
        _set_optimization_goals(ctx, [_event_goal(event_sources=_simple_event_sources())])
        ctx["product_lacks_conversion_tracking"] = True

    else:
        raise ValueError(f"Unknown optimization goal partition: {partition}")


@given(parsers.parse("the optimization goals scenario is: {config}"))
def given_optimization_goals_boundary(ctx: dict, config: str) -> None:
    """Set up optimization goals for boundary scenarios (BR-RULE-087).

    SPEC-PRODUCTION GAP: optimization_goals is not in adcp v3.6.0 or production
    schemas. PackageRequest(extra='forbid') will reject the field. All scenarios
    are expected to xfail via conftest.py tag-based xfail.

    Boundary configs test edge values for optimization goal validation.
    """
    config = config.strip()

    if config == "1 metric goal":
        _set_optimization_goals(ctx, [_metric_goal("clicks")])

    elif config == "empty array":
        _set_optimization_goals(ctx, [])

    elif config == "at max count":
        goals = [_metric_goal("clicks", priority=i + 1) for i in range(10)]
        _set_optimization_goals(ctx, goals)

    elif config == "above max count":
        goals = [_metric_goal("clicks", priority=i + 1) for i in range(11)]
        _set_optimization_goals(ctx, goals)

    elif config == "priority=1":
        _set_optimization_goals(
            ctx,
            [
                _metric_goal("clicks", priority=1),
                _metric_goal("views", priority=2),
            ],
        )

    elif config == "priority=0":
        _set_optimization_goals(ctx, [_metric_goal("clicks", priority=0)])

    elif config == "vds=0.001":
        _set_optimization_goals(ctx, [_metric_goal("completed_views", view_duration_seconds=0.001)])

    elif config == "vds=0":
        _set_optimization_goals(ctx, [_metric_goal("completed_views", view_duration_seconds=0)])

    elif config == "target=0.001":
        _set_optimization_goals(ctx, [_metric_goal("clicks", target={"kind": "cost_per", "value": 0.001})])

    elif config == "target=0":
        _set_optimization_goals(ctx, [_metric_goal("clicks", target={"kind": "cost_per", "value": 0})])

    elif config == "metric kind valid":
        _set_optimization_goals(ctx, [_metric_goal("clicks")])

    elif config == "event kind valid":
        _set_optimization_goals(ctx, [_event_goal(event_sources=_simple_event_sources())])

    elif config == "metric capable":
        _set_optimization_goals(ctx, [_metric_goal("clicks")])
        ctx["product_has_metric_optimization"] = True

    elif config == "no metric capability":
        _set_optimization_goals(ctx, [_metric_goal("clicks")])
        ctx["product_lacks_metric_optimization"] = True

    elif config == "event capable":
        _set_optimization_goals(ctx, [_event_goal(event_sources=_simple_event_sources())])
        ctx["product_has_conversion_tracking"] = True

    elif config == "no event capability":
        _set_optimization_goals(ctx, [_event_goal(event_sources=_simple_event_sources())])
        ctx["product_lacks_conversion_tracking"] = True

    elif config == "freq min=1 max=3":
        _set_optimization_goals(
            ctx,
            [
                _metric_goal(
                    "reach",
                    reach_unit="individuals",
                    target_frequency={"min": 1, "max": 3, "window": {"interval": 7, "unit": "days"}},
                )
            ],
        )

    elif config == "freq min=5 max=3":
        _set_optimization_goals(
            ctx,
            [
                _metric_goal(
                    "reach",
                    reach_unit="individuals",
                    target_frequency={"min": 5, "max": 3, "window": {"interval": 7, "unit": "days"}},
                )
            ],
        )

    elif config == "roas with value_field":
        _set_optimization_goals(
            ctx,
            [
                _event_goal(
                    event_sources=_simple_event_sources(value_field="value"),
                    target={"kind": "per_ad_spend", "value": 4.0},
                )
            ],
        )

    elif config == "roas no value_field":
        _set_optimization_goals(
            ctx,
            [
                _event_goal(
                    event_sources=_simple_event_sources(),  # no value_field
                    target={"kind": "per_ad_spend", "value": 4.0},
                )
            ],
        )

    else:
        raise ValueError(f"Unknown optimization goals boundary config: {config}")


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


# ═══════════════════════════════════════════════════════════════════════
# Catalog distinct type — partition + boundary
# ═══════════════════════════════════════════════════════════════════════

# All 13 AdCP catalog types
_CATALOG_TYPES = [
    "offering",
    "product",
    "inventory",
    "store",
    "promotion",
    "hotel",
    "flight",
    "job",
    "vehicle",
    "real_estate",
    "education",
    "destination",
    "app",
]


def _set_catalogs(ctx: dict, catalogs: list[dict[str, Any]] | None) -> None:
    """Set catalogs on the first package of the request."""
    kwargs = _ensure_request_defaults(ctx)
    if catalogs is None:
        # Remove catalogs key entirely
        kwargs["packages"][0].pop("catalogs", None)
    else:
        kwargs["packages"][0]["catalogs"] = catalogs


def _make_catalog(catalog_type: str, suffix: str = "") -> dict[str, Any]:
    """Build a catalog dict with the given type."""
    return {"type": catalog_type, "url": f"https://example.com/{catalog_type}{suffix}-feed.xml"}


@given(parsers.parse("the catalog scenario is {partition}"))
def given_catalog_partition(ctx: dict, partition: str) -> None:
    """Set up catalogs for partition scenarios (catalog distinct type).

    SPEC-PRODUCTION GAP: Production code accepts catalogs (field is in adcp
    library PackageRequest) but never validates duplicate types or catalog_id
    existence. Valid partitions succeed silently; invalid partitions should fail
    but succeed instead.
    """
    partition = partition.strip()

    # ── Valid partitions ──
    if partition == "no_catalogs":
        _set_catalogs(ctx, None)

    elif partition == "single_catalog":
        _set_catalogs(ctx, [_make_catalog("product")])

    elif partition == "distinct_types":
        _set_catalogs(ctx, [_make_catalog("product"), _make_catalog("store")])

    elif partition == "max_distinct_types":
        _set_catalogs(ctx, [_make_catalog(t) for t in _CATALOG_TYPES])

    # ── Invalid partitions ──
    elif partition == "duplicate_catalog_type":
        _set_catalogs(ctx, [_make_catalog("product", "-a"), _make_catalog("product", "-b")])

    elif partition == "multiple_duplicates":
        _set_catalogs(
            ctx,
            [
                _make_catalog("product", "-a"),
                _make_catalog("product", "-b"),
                _make_catalog("store", "-a"),
                _make_catalog("store", "-b"),
            ],
        )

    elif partition == "catalog_not_found":
        _set_catalogs(
            ctx, [{"type": "product", "catalog_id": "cat-nonexistent-999", "url": "https://example.com/feed.xml"}]
        )

    else:
        raise ValueError(f"Unknown catalog partition: {partition}")


@given(parsers.parse("the catalog configuration is: {config}"))
def given_catalog_boundary(ctx: dict, config: str) -> None:
    """Set up catalogs for boundary scenarios (catalog distinct type).

    SPEC-PRODUCTION GAP: same as partition — production never validates catalog
    uniqueness or catalog_id existence.
    """
    config = config.strip()

    if config == "absent":
        _set_catalogs(ctx, None)

    elif config == "empty array":
        _set_catalogs(ctx, [])

    elif config == "1 product":
        _set_catalogs(ctx, [_make_catalog("product")])

    elif config == "product+store":
        _set_catalogs(ctx, [_make_catalog("product"), _make_catalog("store")])

    elif config == "product+product":
        _set_catalogs(ctx, [_make_catalog("product", "-a"), _make_catalog("product", "-b")])

    elif config == "2prod+store":
        _set_catalogs(
            ctx,
            [
                _make_catalog("product", "-a"),
                _make_catalog("product", "-b"),
                _make_catalog("store"),
            ],
        )

    elif config == "all 13 types":
        _set_catalogs(ctx, [_make_catalog(t) for t in _CATALOG_TYPES])

    elif config == "cross-pkg product":
        # Two packages, each with type=product — distinct per-package, not cross-package
        kwargs = _ensure_request_defaults(ctx)
        kwargs["packages"][0]["catalogs"] = [_make_catalog("product")]
        # Duplicate the first package for a second one
        if len(kwargs["packages"]) < 2:
            import copy

            pkg2 = copy.deepcopy(kwargs["packages"][0])
            pkg2["buyer_ref"] = "pkg-2"
            kwargs["packages"].append(pkg2)
        kwargs["packages"][1]["catalogs"] = [_make_catalog("product")]

    elif config == "valid catalog_id":
        _set_catalogs(ctx, [{"type": "product", "catalog_id": "cat-synced-001", "url": "https://example.com/feed.xml"}])

    elif config == "bad catalog_id":
        _set_catalogs(
            ctx, [{"type": "product", "catalog_id": "cat-nonexistent-999", "url": "https://example.com/feed.xml"}]
        )

    else:
        raise ValueError(f"Unknown catalog boundary config: {config}")


# ═══════════════════════════════════════════════════════════════════════
# Format ID structure partition / boundary
# ═══════════════════════════════════════════════════════════════════════

_VALID_FORMAT_ID = {"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"}


def _set_format_ids(ctx: dict, format_ids: list[dict[str, Any] | str] | None) -> None:
    """Set format_ids on the first package of the request."""
    kwargs = _ensure_request_defaults(ctx)
    if format_ids is None:
        kwargs["packages"][0].pop("format_ids", None)
    else:
        kwargs["packages"][0]["format_ids"] = format_ids


@given(parsers.parse("the format ID scenario is {partition}"))
def given_format_id_partition(ctx: dict, partition: str) -> None:
    """Set up format_ids for partition scenarios (format ID structure).

    SPEC-PRODUCTION GAP: Production validates format_id structure via Pydantic
    (FormatId model), so plain strings and missing fields raise ValidationError.
    However, unregistered agent and unknown format pass Pydantic validation and
    are only caught later (or not at all) by format compatibility checks.
    """
    partition = partition.strip()

    if partition == "valid_format_id":
        _set_format_ids(ctx, [_VALID_FORMAT_ID])

    elif partition == "plain_string":
        _set_format_ids(ctx, ["banner_300x250"])

    elif partition == "missing_agent_url":
        _set_format_ids(ctx, [{"id": "display_300x250"}])

    elif partition == "missing_id":
        _set_format_ids(ctx, [{"agent_url": "https://creative.adcontextprotocol.org"}])

    elif partition == "unregistered_agent":
        _set_format_ids(ctx, [{"agent_url": "https://unknown-agent.example.com", "id": "display_300x250"}])

    elif partition == "unknown_format":
        _set_format_ids(ctx, [{"agent_url": "https://creative.adcontextprotocol.org", "id": "nonexistent_format_999"}])

    else:
        raise ValueError(f"Unknown format ID partition: {partition}")


@given(parsers.parse("the format ID scenario is: {config}"))
def given_format_id_boundary(ctx: dict, config: str) -> None:
    """Set up format_ids for boundary scenarios (format ID structure).

    SPEC-PRODUCTION GAP: same as partition — Pydantic catches structural issues,
    but agent registration and format existence are not fully validated.
    """
    config = config.strip()

    if config == "valid FormatId":
        _set_format_ids(ctx, [_VALID_FORMAT_ID])

    elif config == '"banner_300x250"':
        _set_format_ids(ctx, ["banner_300x250"])

    elif config == "no agent_url":
        _set_format_ids(ctx, [{"id": "display_300x250"}])

    elif config == "bad agent_url":
        _set_format_ids(ctx, [{"agent_url": "https://unknown-agent.example.com", "id": "display_300x250"}])

    elif config == "unknown format":
        _set_format_ids(ctx, [{"agent_url": "https://creative.adcontextprotocol.org", "id": "nonexistent_format_999"}])

    else:
        raise ValueError(f"Unknown format ID boundary config: {config}")
