"""Given steps for UC-002 create_media_buy request construction.

Builds ``ctx["request_kwargs"]`` incrementally — assembled into
CreateMediaBuyRequest in the When step via _dispatch_create_media_buy().

Steps use factories for DB setup and reference ctx["default_product"]
and ctx["default_pricing_option"] created by conftest's _harness_env.
"""

from __future__ import annotations

import re as _re
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from adcp.types import ErrorCode
from pytest_bdd import given, parsers

from src.core.database.models import PricingOption
from src.core.helpers import MEDIA_ASSET_TYPES
from tests.bdd.steps.generic._create_request import (
    build_create_request_kwargs,
    pricing_option_id,
)
from tests.bdd.steps.generic.given_config import attach_push_notification_config
from tests.factories import (
    CurrencyLimitFactory,
    PricingOptionFactory,
    ProductFactory,
)
from tests.factories.creative_asset import build_assets, image_spec, named_format_assets
from tests.factories.malformed import malformed
from tests.factories.mint import mint
from tests.factories.request import CreativeAssetRequestFactory
from tests.helpers.adcp_factories import valid_reporting_webhook
from tests.helpers.egress_hatches import UNDIALLED_PUBLIC_HTTPS_ORIGIN

# Gherkin date token resolver — single source for all BDD step files.
# Matches {now}, {N days from now}, {N days ago}.
_DATE_TOKEN_RE = _re.compile(r"\{(?:now|(?P<future>\d+)\s+days?\s+from\s+now|(?P<past>\d+)\s+days?\s+ago)\}")


def _resolve_date_token(value: str, clock: Any) -> str:
    """Resolve Gherkin date tokens to ISO-Z strings using a TestClock.

    Supported tokens:
        {now}
        {N days from now} / {N day from now}
        {N days ago}       / {N day ago}

    Non-token values are returned unchanged, so plain ISO strings still work.
    """
    m = _DATE_TOKEN_RE.search(value)
    if not m:
        return value
    if m.group("future") is not None:
        return clock.future_iso(int(m.group("future")))
    if m.group("past") is not None:
        return clock.past_iso(int(m.group("past")))
    # Bare {now}
    return clock.now_iso()


def _future(days: int = 1) -> datetime:
    """Return a timezone-aware datetime N days in the future."""
    return mint(datetime.now(UTC) + timedelta(days=days))


def _ensure_request_defaults(ctx: dict) -> dict[str, Any]:
    """Ensure ctx['request_kwargs'] has valid defaults for a create_media_buy request.

    The base dict comes from ``build_create_request_kwargs`` — one request literal for
    the whole suite. Three things stay HERE because they are this caller's semantics,
    not the builder's:

    * the if-absent guard. ``ctx["request_kwargs"]`` is an accumulator that every
      Given mutates, and the builder assigns unconditionally, so calling it on an
      already-populated ctx would discard earlier steps' work.
    * the product/pricing fallback. Steps run against a ctx with no seeded product,
      where the builder's direct ``ctx["default_product"]`` lookup would raise; the
      values are resolved here and passed in.
    * the idempotency key, below.
    """
    if "request_kwargs" not in ctx:
        product = ctx.get("default_product")
        pricing_option = ctx.get("default_pricing_option")
        # Whether the base dict was built against SEEDED rows or against placeholders is
        # the fact ``harness_create_request_kwargs`` needs and cannot recover afterwards
        # (the placeholder pricing id and the seeded one are the same string). Recorded
        # here, at the only place that knows it.
        ctx["request_kwargs_placeholder_ids"] = product is None or pricing_option is None
        build_create_request_kwargs(
            ctx,
            product_id=product.product_id if product else "guaranteed_display",
            pricing_option=pricing_option_id(pricing_option) if pricing_option else "cpm_usd_fixed",
        )
    # idempotency_key is REQUIRED on CreateMediaBuyRequest (AdCP 3.0.1). Default a
    # per-scenario-unique key so scenarios not exercising idempotency stay valid —
    # a reused key would replay the original response instead of creating a buy.
    #
    # This is ONE of two deliberately different minting policies and must not be
    # collapsed with the other: this key is stable for the whole scenario, so UC-002's
    # replay scenarios dispatch the same request twice and hit the idempotency cache,
    # whereas ``media_buy_create._ensure_idempotency_key`` mints a FRESH key per call
    # so ordinary dispatches create independent buys. Merging them would either make
    # every scenario replay or stop the replay scenarios replaying.
    ctx["request_kwargs"].setdefault("idempotency_key", mint(f"bdd-key-{uuid.uuid4().hex}"))

    # account is REQUIRED on CreateMediaBuyRequest too (create-media-buy-request.json
    # /required), and unlike the key it must RESOLVE: the transport boundary looks the
    # reference up, so a literal id would answer ACCOUNT_NOT_FOUND and every scenario not
    # about accounts would fail on resolution before reaching what it grades. Seeded through
    # the env, which is idempotent, so repeated Given steps reuse one row. An account-shape
    # scenario overrides this (or passes OMIT_ACCOUNT) and setdefault leaves it alone.
    if "env" in ctx:
        ctx["request_kwargs"].setdefault("account", {"account_id": ctx["env"].setup_default_account().account_id})
    return ctx["request_kwargs"]


def harness_create_request_kwargs(ctx: dict) -> dict[str, Any]:
    """Request kwargs for a create dispatched from a harness-seeded scenario.

    ``_ensure_request_defaults`` only reads ``default_product`` /
    ``default_pricing_option`` when it FIRST builds ``request_kwargs``; a
    scenario whose Given steps created ``request_kwargs`` earlier (before the
    UC-004 create branch seeded the harness data) would dispatch against the
    placeholder ids. Re-pinning the package to the harness product here is what
    both create-dispatching When steps share instead of each carrying a copy.

    The re-pin is CONDITIONAL on that build having used placeholders, and the
    condition is the whole point: written unconditionally it also overwrote package
    ids a Given had deliberately chosen — an auction option, a EUR option, a second
    product — and the scenario would then grade the default fixed option while
    reading as if it graded what it asked for. No live caller hits that today
    (measured: 18 executions across ``test_egress_ssrf_refusal`` and
    ``test_uc004_deliver_media_buy_metrics``, every one a no-op with before ==
    after), which is exactly why it would land silently.
    """
    kwargs = _ensure_request_defaults(ctx)
    if not ctx.get("request_kwargs_placeholder_ids"):
        return kwargs
    product = ctx.get("default_product")
    pricing_option = ctx.get("default_pricing_option")
    if product is None or pricing_option is None:
        # Still nothing to pin to; leave the flag set so a later call can do it.
        return kwargs
    kwargs["packages"][0]["product_id"] = product.product_id
    kwargs["packages"][0]["pricing_option_id"] = pricing_option_id(pricing_option)
    ctx["request_kwargs_placeholder_ids"] = False
    return kwargs


# ═══════════════════════════════════════════════════════════════════════
# Adapter DB config sync (E2E support)
# ═══════════════════════════════════════════════════════════════════════


def _sync_adapter_approval_to_db(ctx: dict, *, manual_approval_required: bool) -> None:
    """Write adapter approval config to DB so Docker-hosted adapter picks it up.

    In-process transports use the mock attribute directly. E2E transports need
    the config written to the shared database. This is a no-op when no tenant
    exists yet (the harness will set up defaults).
    """
    tenant = ctx.get("tenant")
    if tenant is None:
        return
    env = ctx["env"]
    from tests.factories.core import set_adapter_test_behavior

    set_adapter_test_behavior(env, tenant.tenant_id, manual_approval_required=manual_approval_required)


def _set_tenant_human_review(ctx: dict, *, required: bool) -> None:
    """Set ``human_review_required`` on the tenant row and on the identity overrides.

    The committed row is what the live (e2e) server reads; the overrides are what the
    in-process call_impl identity is built from.
    """
    env = ctx["env"]
    ctx["tenant"].human_review_required = required
    env._commit_factory_data()
    env._tenant_overrides["human_review_required"] = required


def _seed_auto_approval(ctx: dict, *, sync_adapter: bool = True) -> None:
    """Force the auto-approval create path for the scenario's tenant.

    Sets human_review_required=False on the ORM row, the identity cache, and
    the env tenant overrides. With ``sync_adapter`` also mirrors
    manual_approval_required=False into the adapter DB config so live-server
    (e2e) transports take the same auto path as in-process: the in-process
    harness zeroes manual_approval_operations, but the live tenant defaults to
    human_review_required=True and the real adapter requires approval for
    create_media_buy, which would divert e2e onto the PENDING-approval path
    with different validation semantics (PR #1430 item 3).

    No-op when the scenario has no tenant yet.
    """
    if ctx.get("tenant") is None:
        return
    _set_tenant_human_review(ctx, required=False)
    if sync_adapter:
        _sync_adapter_approval_to_db(ctx, manual_approval_required=False)


def _sync_adapter_error_to_db(
    ctx: dict,
    *,
    fail_on_create: bool = False,
    fail_on_update: bool = False,
    fail_on_upload: bool = False,
    error_message: str | None = None,
    error_details: dict | None = None,
) -> None:
    """Write adapter error injection config to DB so Docker adapter raises errors."""
    tenant = ctx.get("tenant")
    if tenant is None:
        return
    env = ctx["env"]
    from tests.factories.core import set_adapter_test_behavior

    kwargs: dict = {
        "fail_on_create": fail_on_create,
        "fail_on_update": fail_on_update,
        "fail_on_upload": fail_on_upload,
        "error_message": error_message,
    }
    if error_details is not None:
        kwargs["error_details"] = error_details
    set_adapter_test_behavior(env, tenant.tenant_id, **kwargs)


def set_update_adapter_outcome(ctx: dict, error: Exception | None) -> None:
    """Make the adapter the UPDATE path calls succeed (``error`` None) or raise ``error``.

    In process that adapter is ``env.mock['update_adapter']`` (MediaBuyDualEnv); the
    create-module ``env.mock['adapter']`` is never asked during an update, so a Given
    that configured only that one changed nothing the update could observe. Success
    restores the harness's own adapter contract rather than clearing the side effect,
    which would leave a bare MagicMock as the adapter's answer. The DB knob carries the
    same outcome to the live server's mock adapter on e2e_rest, which raises its
    default transient class (SERVICE_UNAVAILABLE) — the class given here in process.
    """
    from tests.harness._mixins import make_adapter_update_side_effect

    env = ctx["env"]
    if "update_adapter" in env.mock:
        env.mock["update_adapter"].return_value.update_media_buy.side_effect = (
            make_adapter_update_side_effect() if error is None else error
        )
    _sync_adapter_error_to_db(
        ctx,
        fail_on_update=error is not None,
        error_message=None if error is None else "Ad server returned error during update",
    )


# ═══════════════════════════════════════════════════════════════════════
# Tenant configuration
# ═══════════════════════════════════════════════════════════════════════


#: The operations a real adapter puts behind approval when its config names none
#: (``AdServerAdapter.__init__``'s ``manual_approval_operations`` default, src/adapters/base.py).
_REAL_ADAPTER_APPROVAL_OPERATIONS = frozenset({"create_media_buy", "update_media_buy", "add_creative_assets"})


def _configure_adapter_manual_approval(env: Any, *, required: bool, operations: frozenset[str] | None = None) -> None:
    """Set manual-approval config on EVERY adapter mock the env exposes.

    ``env.adapter_mocks()`` lists them: the create module's on MediaBuyCreateEnv, and the
    update module's as well on MediaBuyDualEnv. The production create/update manual-approval
    gate reads ``manual_approval_required`` + ``'<op>' in manual_approval_operations``
    from its OWN module's adapter, so each must be configured.

    ``operations`` defaults to the two media-buy operations when ``required`` and to none
    otherwise. A Given that wants the adapter to act as a real one with its flag off passes
    :data:`_REAL_ADAPTER_APPROVAL_OPERATIONS`.
    """
    if operations is None:
        operations = frozenset({"create_media_buy", "update_media_buy"}) if required else frozenset()
    for adapter_mock in env.adapter_mocks():
        adapter_mock.manual_approval_required = required
        adapter_mock.manual_approval_operations = set(operations)


@given("tenant human_review_required is false")
def given_tenant_auto_approval(ctx: dict) -> None:
    """Configure tenant for auto-approval (human_review_required=False).

    The "the tenant is configured for auto-approval" alias is owned by
    uc002_create_media_buy (canonical) to avoid a cross-module shadow now that
    this module is registered; UC-003 scenarios reach this via the
    "human_review_required is false" alias.
    """
    assert ctx.get("tenant") is not None, (
        "No tenant in ctx — step claims 'tenant is configured for auto-approval' but no tenant exists to configure"
    )
    _seed_auto_approval(ctx)


@given("tenant human_review_required is true")
def given_tenant_review_only(ctx: dict) -> None:
    """The TENANT requires review, and the adapter does not.

    The sentence names one input, so it sets one: ``tenant.human_review_required``. The
    adapter is left as a real adapter with its own flag off -- ``manual_approval_required``
    false, and the operations a real adapter gates by default -- on the in-process mocks and
    on the live server's AdapterConfig alike. Approval then follows the tenant column alone,
    so a seller that ignored it would complete the buy. The BR-RULE-080 approval outlines'
    tenant rows reach it too, which isolates the tenant arm of the create approval gate
    (``tenant.human_review_required OR adapter.manual_approval_required``, then
    ``'create_media_buy' in adapter.manual_approval_operations``). (``given_tenant_manual_approval``
    also raises the adapter's flag, which update_media_buy scenarios rely on.)
    """
    assert ctx.get("tenant") is not None, (
        "No tenant in ctx — step claims 'tenant human_review_required is true' but no tenant exists to configure"
    )
    _set_tenant_human_review(ctx, required=True)
    _configure_adapter_manual_approval(ctx["env"], required=False, operations=_REAL_ADAPTER_APPROVAL_OPERATIONS)
    _sync_adapter_approval_to_db(ctx, manual_approval_required=False)


@given("the tenant is configured for manual approval")
@given("the tenant requires manual approval")
@given(parsers.parse('the tenant has "human_review_required" set to true'))
@given("approval path is manual")
def given_tenant_manual_approval(ctx: dict) -> None:
    """Configure tenant for manual approval."""
    assert ctx.get("tenant") is not None, (
        "No tenant in ctx — step claims 'tenant is configured for manual approval' but no tenant exists to configure"
    )
    _set_tenant_human_review(ctx, required=True)
    # Production code checks: manual_approval_required AND
    # "<op>" in adapter.manual_approval_operations. The mock adapter defaults to
    # manual_approval_operations=[], so configure every adapter mock (create +
    # update) for manual approval — the update submitted path reads update_adapter.
    _configure_adapter_manual_approval(ctx["env"], required=True)
    # Also write to DB so Docker-hosted adapter reads the correct config
    _sync_adapter_approval_to_db(ctx, manual_approval_required=True)


@given("adapter manual_approval_required is false")
def given_adapter_no_manual_approval(ctx: dict) -> None:
    """Configure adapter for auto-approval (manual_approval_required=False).

    This is the default state for MediaBuyCreateEnv, but explicitly set it
    to be clear in the scenario.
    """
    env = ctx["env"]
    _configure_adapter_manual_approval(env, required=False)
    # Also write to DB so Docker-hosted adapter reads the correct config
    _sync_adapter_approval_to_db(ctx, manual_approval_required=False)


@given("adapter manual_approval_required is true")
def given_adapter_manual_approval(ctx: dict) -> None:
    """Configure adapter to require manual approval.

    Sets manual_approval_required=True and includes 'create_media_buy' in
    manual_approval_operations — both are needed for the approval gate.
    """
    env = ctx["env"]
    _configure_adapter_manual_approval(env, required=True)
    # Also write to DB so Docker-hosted adapter reads the correct config
    _sync_adapter_approval_to_db(ctx, manual_approval_required=True)


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
        given_tenant_review_only(ctx)
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
        given_tenant_review_only(ctx)
    elif config == "adapter_ma=true":
        # Tenant auto-approve, but adapter requires manual approval
        given_tenant_auto_approval(ctx)
        given_adapter_manual_approval(ctx)
    else:
        raise ValueError(f"Unknown approval boundary config: {config}")


# ───────────────────────────────────────────────────────────────────────
# Persistence timing configuration (BR-RULE-020)
# ───────────────────────────────────────────────────────────────────────


def _configure_persistence_timing(ctx: dict, approval: str, adapter_result: str) -> None:
    """Shared configuration for persistence timing scenarios.

    Args:
        approval: "auto" for auto-approval, "manual" for manual approval.
        adapter_result: "success" or "failure" (only meaningful for auto-approval).
    """
    if approval == "auto":
        given_tenant_auto_approval(ctx)
        given_adapter_no_manual_approval(ctx)
        if adapter_result == "success":
            given_adapter_success(ctx)
        else:
            given_adapter_error(ctx)
    else:
        given_tenant_manual_approval(ctx)
        given_adapter_manual_approval(ctx)


_PARTITION_MAP = {
    "auto_approve_adapter_success": ("auto", "success"),
    "manual_approval_pending": ("manual", "n/a"),
    "auto_approve_adapter_failure": ("auto", "failure"),
}

_BOUNDARY_MAP = {
    "auto-approve success": ("auto", "success"),
    "auto-approve failure": ("auto", "failure"),
    "manual approval": ("manual", "n/a"),
}


@given(parsers.parse("the persistence timing scenario is {partition}"))
def given_persistence_timing_partition(ctx: dict, partition: str) -> None:
    """Configure approval + adapter state for persistence timing partitions (BR-RULE-020)."""
    partition = partition.strip()
    if partition not in _PARTITION_MAP:
        raise ValueError(f"Unknown persistence timing partition: {partition}")
    approval, adapter_result = _PARTITION_MAP[partition]
    _configure_persistence_timing(ctx, approval, adapter_result)


@given(parsers.parse("the persistence timing scenario is: {config}"))
def given_persistence_timing_boundary(ctx: dict, config: str) -> None:
    """Configure approval + adapter state for persistence timing boundaries (BR-RULE-020)."""
    config = config.strip()
    if config not in _BOUNDARY_MAP:
        raise ValueError(f"Unknown persistence timing boundary config: {config}")
    approval, adapter_result = _BOUNDARY_MAP[config]
    _configure_persistence_timing(ctx, approval, adapter_result)


def _set_max_daily_package_spend(ctx: dict, amount: int) -> None:
    """Configure tenant max daily package spend on the CurrencyLimit (USD).

    Shared helper for both the parameterized and bare 'configured' steps.
    """
    from decimal import Decimal

    from sqlalchemy import select

    from src.core.database.models import CurrencyLimit
    from tests.bdd.steps._harness_db import db_session

    tenant = ctx.get("tenant")
    assert tenant is not None, "No tenant in ctx — Given step ordering error"
    env = ctx["env"]
    env._commit_factory_data()
    with db_session(ctx) as session:
        cl = session.scalars(select(CurrencyLimit).filter_by(tenant_id=tenant.tenant_id, currency_code="USD")).first()
        assert cl is not None, f"No CurrencyLimit(USD) for tenant {tenant.tenant_id}"
        cl.max_daily_package_spend = Decimal(str(amount))
        session.commit()


@given(parsers.parse("the tenant has max_daily_package_spend configured at {amount:d}"))
def given_tenant_max_daily_spend(ctx: dict, amount: int) -> None:
    """Configure tenant max daily package spend on the CurrencyLimit (USD)."""
    _set_max_daily_package_spend(ctx, amount)


@given("the tenant has max_daily_package_spend configured")
def given_tenant_max_daily_spend_default(ctx: dict) -> None:
    """Configure tenant max daily package spend with a default value (1000 USD).

    Used by legacy-mode scenarios where the exact cap amount is not specified
    in the Gherkin step text -- the scenario only asserts that a cap EXISTS.
    """
    _set_max_daily_package_spend(ctx, 1000)


# ═══════════════════════════════════════════════════════════════════════
# Request construction — base
# ═══════════════════════════════════════════════════════════════════════


@given(parsers.parse("a valid create_media_buy request with:"))
def given_valid_create_request_with_table(ctx: dict, datatable: list[list[str]]) -> None:
    """Set up a create_media_buy request from a Gherkin data table.

    Table format: | field | value |

    ``start_time``/``end_time`` values may be relative date tokens
    (``{30 days from now}``, ``{now}``, ``{1 day ago}``) resolved via the
    shared TestClock on ``ctx['env'].clock`` — plain ISO strings still work.
    """
    kwargs = _ensure_request_defaults(ctx)
    clock = ctx["env"].clock
    for row in datatable:
        field, value = row[0].strip(), row[1].strip()
        if field == "brand":
            # Parse: domain "acme.com"
            if value.startswith("domain "):
                domain = value.split('"')[1]
                kwargs["brand"] = {"domain": domain}
            else:
                kwargs["brand"] = {"domain": value}
        elif field == "start_time":
            kwargs["start_time"] = _resolve_date_token(value, clock)
        elif field == "end_time":
            kwargs["end_time"] = _resolve_date_token(value, clock)
        elif field == "account":
            # Parse: account_id "acc-001"
            if "account_id" in value:
                account_id = value.split('"')[1]
                from adcp.types import (
                    AccountReferenceById,
                )

                kwargs["account"] = AccountReferenceById(account_id=account_id)
            else:
                raise ValueError(f"Unrecognized account format: '{value}'. Expected format: account_id \"acc-001\"")
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
    assert kwargs.get("packages"), (
        "No packages in request — step claims 'with total budget' but cannot set budget without packages"
    )
    kwargs["packages"][0]["budget"] = float(amount)
    # Zero out remaining packages so total equals claimed amount
    for pkg in kwargs["packages"][1:]:
        pkg["budget"] = 0.0
    # Post-setup invariant: total across all packages equals the claimed total_budget
    actual_total = sum(pkg.get("budget", 0) for pkg in kwargs["packages"])
    assert actual_total == float(amount), (
        f"Step claims 'total budget {amount}' but total of all package budgets "
        f"is {actual_total} — setup did not establish the claimed total"
    )


@given(parsers.parse("the product minimum spend is {amount:d} {currency}"))
def given_product_minimum_spend(ctx: dict, amount: int, currency: str) -> None:
    """Configure the product's minimum spend threshold.

    Sets the tenant's CurrencyLimit.min_package_budget so the product
    enforces a minimum spend. Also stores the expected values in ctx for
    downstream Then-step assertions on error details.

    SPEC-PRODUCTION GAP: Production validates min_package_budget per
    CurrencyLimit, not per product. This step uses CurrencyLimit as the
    mechanism. When per-product minimums are implemented, update this step.

    FIXME: Per-product minimum spend not yet implemented.
    """
    import pytest

    pytest.xfail(
        f"SPEC-PRODUCTION GAP: Per-product minimum spend ({amount} {currency}) "
        "not yet implemented. Production uses CurrencyLimit.min_package_budget "
        "for all products in a tenant. FIXME"
    )


# ═══════════════════════════════════════════════════════════════════════
# Package construction
# ═══════════════════════════════════════════════════════════════════════


# "the request includes 2 packages with valid product_ids" is NOT defined here. It was —
# hardcoded to 2, hand-writing the package dict — and it never ran: the parameterized
# definition in uc002_create_media_buy.py shadowed it, because both modules are registered
# in pytest_plugins and pytest takes the later-registered stepdef fixture. Deleted rather
# than repaired, and deliberately not "fixed" by reordering pytest_plugins: that leaves two
# definitions of one sentence and re-creates the shadow by a different route. The surviving
# definition builds the array through ``build_request_packages`` (the DTO-bound factory), so
# there is one owner of the package shape rather than the three there were.


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
    """Configure all packages to use the specified currency.

    Creates a pricing option with the requested currency and updates every
    package's pricing_option_id to reference it. This is a Given step — it
    SETS state rather than merely asserting it.
    """
    env = ctx["env"]
    kwargs = _ensure_request_defaults(ctx)
    # GET-OR-CREATE, not create. The sentence says the packages USE an option in this
    # currency; it does not say a second one is minted. The env already seeds the product
    # a cpm/USD/fixed option, and pricing_options now carries
    # UNIQUE (tenant_id, product_id, pricing_option_id) -- so for the default currency an
    # unconditional create is an IntegrityError, and before that constraint existed it was
    # a SECOND row sharing one id, of which the reader's dict silently dropped one. Either
    # way the step was establishing a state the seller cannot hold.
    # PER PACKAGE'S OWN PRODUCT, not the default product for all of them. The sentence is a
    # CROSS-package rule (3.1.1 create_media_buy.mdx L266: "Every package's selected pricing
    # option must declare the media-buy currency"), so it has to hold for each package
    # against the product that package actually names.
    #
    # Resolving every package off ctx["default_product"] happened to work only because
    # PricingOption.default_option_id is "{model}_{currency}_{fixed|auction}"
    # (src/core/database/models.py:531-544), so two different products' cpm/USD/fixed
    # options carry the SAME id string. That is a coincidence of the default-id grammar,
    # not a design: the moment a product's option is minted with a publisher-chosen id,
    # the step names an id that product does not have. It is now correct by construction
    # instead of by that coincidence.
    by_product = {ctx["default_product"].product_id: ctx["default_product"]}
    by_product.update({product.product_id: product for product, _ in ctx.get("extra_products") or []})

    wanted = PricingOption.default_option_id("cpm", currency, True)
    for pkg in kwargs.get("packages", []):
        product = by_product.get(pkg["product_id"])
        assert product is not None, (
            f"Package names product {pkg['product_id']!r}, which no Given seeded. Seeded: {sorted(by_product)}."
        )
        existing = next(
            (po for po in getattr(product, "pricing_options", None) or [] if po.pricing_option_id == wanted),
            None,
        )
        po = existing or PricingOptionFactory(
            product=product,
            pricing_model="cpm",
            currency=currency,
            is_fixed=True,
        )
        env._commit_factory_data()
        pkg["pricing_option_id"] = pricing_option_id(po)


@given("each package has a valid pricing_option_id")
def given_packages_valid_pricing(ctx: dict) -> None:
    """Ensure each package has a valid pricing_option_id referencing a real DB record.

    Verifies the request packages actually reference pricing_option_ids that
    correspond to PricingOption records in the database, not just checking
    non-None strings. Default packages reference valid pricing options
    created by setup_media_buy_data.
    """
    from sqlalchemy import select

    from src.core.database.models import PricingOption
    from tests.bdd.steps._harness_db import db_session

    kwargs = ctx.get("request_kwargs")
    assert kwargs is not None, "No request_kwargs in ctx — step claims packages have valid pricing"
    packages = kwargs.get("packages", [])
    assert packages, "No packages in request — step claims each has a valid pricing_option_id"

    tenant = ctx.get("tenant")
    assert tenant is not None, "No tenant in ctx — cannot verify pricing option validity"
    tenant_id = getattr(tenant, "tenant_id", None)

    for i, pkg in enumerate(packages):
        po_id = pkg.get("pricing_option_id")
        assert po_id is not None, (
            f"Package {i} has no pricing_option_id — step claims 'each package has a valid pricing_option_id'"
        )
        # Verify the pricing_option_id references an actual PricingOption in the DB
        product_id = pkg.get("product_id")
        with db_session(ctx) as session:
            po = session.scalars(
                select(PricingOption).filter_by(
                    tenant_id=tenant_id,
                    product_id=product_id,
                )
            ).all()
            known_po_ids = {pricing_option_id(p) for p in po}
            assert po_id in known_po_ids, (
                f"Package {i} pricing_option_id '{po_id}' not found among product "
                f"'{product_id}' pricing options: {known_po_ids}. "
                "Step claims 'valid pricing_option_id' but the ID does not reference "
                "an existing PricingOption record."
            )


# "a valid create_media_buy request with 2 packages" is RETIRED. It was a sibling
# spelling that did the work of the canonical pair -- "a valid create_media_buy request"
# (94 scenarios) plus "the request includes {count:d} packages with valid product_ids" --
# and its body was a FOURTH hand-written copy of the package dict. Its one binding
# scenario, @T-UC-002-ext-e, now says both canonical sentences instead. One sibling
# sentence retired, one step definition deleted, one copy of the shape gone.


# ═══════════════════════════════════════════════════════════════════════
# Error injection — "But" steps that override defaults with invalid values
# ═══════════════════════════════════════════════════════════════════════


@given("all package budgets sum to 0")
def given_zero_budget(ctx: dict) -> None:
    """Override all package budgets to 0."""
    kwargs = _ensure_request_defaults(ctx)
    for pkg in kwargs.get("packages", []):
        pkg["budget"] = 0


@given(parsers.parse("a package budget is set to {value}"))
def given_package_budget_set_to(ctx: dict, value: str) -> None:
    """Set first package budget to the given value (supports float)."""
    kwargs = _ensure_request_defaults(ctx)
    assert kwargs.get("packages"), (
        "No packages in request — step claims 'a package budget is set to' but cannot set budget without packages"
    )
    kwargs["packages"][0]["budget"] = float(value)


@given(parsers.parse('a package references product_id "{product_id}" which does not exist'))
def given_nonexistent_product(ctx: dict, product_id: str) -> None:
    """Override first package to reference a nonexistent product."""
    kwargs = _ensure_request_defaults(ctx)
    assert kwargs.get("packages"), (
        "No packages in request — step claims 'a package references product_id' "
        "but cannot override product_id without packages"
    )
    kwargs["packages"][0]["product_id"] = product_id


@given("end_time is before start_time")
def given_end_before_start(ctx: dict) -> None:
    """Set end_time before start_time."""
    kwargs = _ensure_request_defaults(ctx)
    kwargs["start_time"] = _future(10).isoformat()
    kwargs["end_time"] = _future(1).isoformat()


# Single-token value only (\S+). A bare ``{value}`` parse would greedily swallow
# spaced phrasings like ``start_time is "..." (in the past)`` and shadow the
# specific step ``given_past_start_time`` (pytest-bdd resolves multiple matches
# last-registered-wins). parsers.re uses fullmatch, so \S+ refuses any value
# containing whitespace, letting the specific spaced-phrase step win.
@given(parsers.re(r"(?:the )?start_time is (?P<value>\S+)"))
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


# Single-token value only (\S+); see given_start_time_value. Refusing whitespace
# lets the specific step ``given_end_before_start`` ("end_time is before start_time")
# win instead of this generic capturing "before start_time" as a literal value.
@given(parsers.re(r"(?:the )?end_time is (?P<value>\S+)"))
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
        kwargs["packages"][0]["pricing_option_id"] = pricing_option_id(po)


@given(parsers.parse('both packages reference the same product_id "{product_id}"'))
def given_duplicate_product(ctx: dict, product_id: str) -> None:
    """Set both packages to reference the same product_id."""
    kwargs = _ensure_request_defaults(ctx)
    packages = kwargs.get("packages", [])
    assert len(packages) >= 2, (
        f"Step claims 'both packages' but only {len(packages)} package(s) exist in the request. "
        "A preceding step must set up at least 2 packages."
    )
    # Create the product if it doesn't match default
    if ctx["default_product"].product_id != product_id:
        env = ctx["env"]
        ProductFactory(
            tenant=ctx["tenant"],
            product_id=product_id,
            property_tags=["all_inventory"],
        )
        env._commit_factory_data()
    for pkg in packages:
        pkg["product_id"] = product_id


@given(parsers.parse('a package targeting_overlay contains unknown field "{field_name}"'))
def given_unknown_targeting_field(ctx: dict, field_name: str) -> None:
    """Add unknown field to package targeting_overlay."""
    kwargs = _ensure_request_defaults(ctx)
    assert kwargs.get("packages"), (
        "No packages in request — step claims 'a package targeting_overlay contains "
        f'unknown field "{field_name}"\' but no package exists to set it on'
    )
    kwargs["packages"][0].setdefault("targeting_overlay", {})[field_name] = "value"


@given("a package targeting_overlay sets a targeting dimension the pin does not declare")
def given_undeclared_targeting_dimension(ctx: dict) -> None:
    """Set a targeting dimension the pinned core/targeting.json does not declare.

    ``key_value_pairs`` was this seller's own "managed-only" field until
    salesagent-3cs7o.22 deleted it; the pin never declared it, so the payload is now
    exactly an undeclared field, refused at model construction under CLAUDE.md pattern 7.
    """
    kwargs = _ensure_request_defaults(ctx)
    assert kwargs.get("packages"), (
        "No packages in request — step claims 'a package targeting_overlay sets a "
        "targeting dimension the pin does not declare' but no package exists to set it on"
    )
    kwargs["packages"][0]["targeting_overlay"] = {"key_value_pairs": {"section": "sports"}}


@given(parsers.parse('a package targeting_overlay includes "{value}" in both geo_countries and geo_countries_exclude'))
def given_geo_overlap(ctx: dict, value: str) -> None:
    """Create geo include/exclude overlap."""
    kwargs = _ensure_request_defaults(ctx)
    assert kwargs.get("packages"), (
        f"No packages in request — step claims 'a package targeting_overlay includes "
        f'"{value}" in both geo_countries and geo_countries_exclude\' but no package exists'
    )
    kwargs["packages"][0]["targeting_overlay"] = {
        "geo_countries": [value],
        "geo_countries_exclude": [value],
    }


@given(parsers.parse("a package has budget {budget:d} over a {days:d}-day flight (daily = {daily:d})"))
def given_high_daily_spend(ctx: dict, budget: int, days: int, daily: int) -> None:
    """Set package with high daily spend exceeding cap.

    The `daily` parameter is captured from the step text for documentation purposes
    (it describes the expected daily spend = budget / days). Verify it matches the
    math to catch Gherkin typos.
    """
    kwargs = _ensure_request_defaults(ctx)
    kwargs["start_time"] = _future(1).isoformat()
    kwargs["end_time"] = _future(1 + days).isoformat()
    assert kwargs.get("packages"), (
        f"No packages in request — step claims 'a package has budget {budget} over a "
        f"{days}-day flight' but no package exists to set it on"
    )
    # Verify the daily parameter matches budget/days to catch Gherkin data errors
    expected_daily = budget // days
    assert daily == expected_daily, (
        f"Step text claims daily = {daily} but budget {budget} / {days} days = "
        f"{expected_daily} — Gherkin data table has inconsistent values"
    )
    kwargs["packages"][0]["budget"] = float(budget)


@given(parsers.parse('a package references pricing_option_id "{po_id}" not found on the product'))
def given_nonexistent_pricing_option(ctx: dict, po_id: str) -> None:
    """Override first package pricing_option_id to a non-existent value."""
    kwargs = _ensure_request_defaults(ctx)
    assert kwargs.get("packages"), (
        f"No packages in request — step claims 'a package references pricing_option_id "
        f'"{po_id}" not found on the product\' but no package exists to set it on'
    )
    kwargs["packages"][0]["pricing_option_id"] = po_id


@given("a package selects an auction pricing option but provides no bid_price")
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
    assert kwargs.get("packages"), (
        "No packages in request — step claims 'a package selects an auction pricing "
        "option but provides no bid_price' but no package exists to set it on"
    )
    kwargs["packages"][0]["pricing_option_id"] = pricing_option_id(auction_po)
    kwargs["packages"][0].pop("bid_price", None)


@given(parsers.parse("a package has bid_price {bid:g} but floor_price is {floor:g}"))
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
    assert kwargs.get("packages"), (
        "No packages in request — step claims 'a package has bid_price' but no package exists to set it on"
    )
    kwargs["packages"][0]["pricing_option_id"] = pricing_option_id(auction_po)
    kwargs["packages"][0]["bid_price"] = bid


@given(
    parsers.parse("a package budget of {budget:g} against a pricing option requiring a minimum spend of {minimum:g}")
)
def given_budget_below_minimum_spend(ctx: dict, budget: float, minimum: float) -> None:
    """Give the option the package ALREADY names a minimum spend, then underbid it.

    The existing option is modified rather than a second one added: a new fixed CPM/USD
    option on the same product would carry the same default pricing_option_id, which the
    uniqueness constraint refuses — correctly, since a duplicate id names two rows.
    """
    env = ctx["env"]
    option = ctx["default_pricing_option"]
    option.min_spend_per_package = Decimal(str(minimum))
    env._commit_factory_data()
    kwargs = _ensure_request_defaults(ctx)
    assert kwargs.get("packages"), "No packages in request — nothing to set a budget on"
    kwargs["packages"][0]["pricing_option_id"] = pricing_option_id(option)
    kwargs["packages"][0]["budget"] = budget


# ═══════════════════════════════════════════════════════════════════════
# Pricing XOR invariant — BR-RULE-006 (inv-006-1..4)
# ═══════════════════════════════════════════════════════════════════════


@given("a package pricing option has fixed_price set and floor_price null")
def given_fixed_price_only(ctx: dict) -> None:
    """Ensure the package references a fixed pricing option (default state).

    The default PricingOption from setup_media_buy_data() is already
    is_fixed=True with rate=5.00 — this maps to fixed_price=5.00, floor_price=None.
    Also verifies the request package actually references this PO.
    """
    # Assert the default PO is fixed.
    po = ctx.get("default_pricing_option")
    assert po is not None, "No default_pricing_option in ctx — Given step ordering error"
    assert po.is_fixed, "Default pricing option should be fixed"
    # Verify the request package references this fixed PO
    kwargs = _ensure_request_defaults(ctx)
    assert kwargs.get("packages"), (
        "No packages in request — step claims 'a package pricing option has fixed_price' "
        "but no package exists to verify"
    )
    expected_po_id = pricing_option_id(po)
    actual_po_id = kwargs["packages"][0].get("pricing_option_id")
    assert actual_po_id == expected_po_id, (
        f"Package pricing_option_id '{actual_po_id}' does not reference the fixed "
        f"PO '{expected_po_id}' — step claims the package uses a fixed pricing option "
        "but the package references a different PO"
    )


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
    assert kwargs.get("packages"), (
        "No packages in request — step claims 'a package pricing option' but no package exists to associate it with"
    )
    kwargs["packages"][0]["pricing_option_id"] = pricing_option_id(auction_po)


@given("the package has a bid_price above the floor")
def given_bid_above_floor(ctx: dict) -> None:
    """Set bid_price above the pricing option's floor price.

    Requires a prior step that sets up an auction pricing option with a floor.
    The default floor from given_floor_price_only is 2.0.
    """
    kwargs = _ensure_request_defaults(ctx)
    assert kwargs.get("packages"), (
        "No packages in request — step claims 'the package has a bid_price' but no package exists"
    )
    # Derive bid from the actual floor price if available, otherwise use safe default
    po = ctx.get("default_pricing_option")
    floor = 2.0  # default from given_floor_price_only
    if po is not None and not po.is_fixed:
        guidance = getattr(po, "price_guidance", None)
        if isinstance(guidance, dict) and "floor" in guidance:
            floor = float(guidance["floor"])
    kwargs["packages"][0]["bid_price"] = floor + 3.0


@given("a package pricing option has both fixed_price and floor_price set")
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


def _select_new_pricing_option(ctx: dict, **option: Any) -> None:
    """Seed a pricing option on the default product and select it on the first package.

    Every pricing/currency Given that varies the selected option goes through here. An
    option whose id would collide with the seeded ``cpm_usd_fixed`` must name its own
    ``pricing_option_id``: ``uq_pricing_options_option_id`` is unique per product.
    """
    env = ctx["env"]
    po = PricingOptionFactory(product=ctx["default_product"], **option)
    env._commit_factory_data()
    kwargs = _ensure_request_defaults(ctx)
    assert kwargs.get("packages"), "No packages in request — cannot select a pricing option"
    kwargs["packages"][0]["pricing_option_id"] = pricing_option_id(po)


def _setup_fixed_pricing(ctx: dict, rate: float = 5.00) -> None:
    """Select a fixed-price option (is_fixed=True, rate set, no floor) at ``rate``."""
    po = ctx["default_pricing_option"]
    if po.is_fixed and po.rate == Decimal(str(rate)):
        _ensure_request_defaults(ctx)["packages"][0]["pricing_option_id"] = pricing_option_id(po)
        return
    _select_new_pricing_option(
        ctx, pricing_model="cpm", currency="USD", is_fixed=True, rate=rate, pricing_option_id=f"cpm_usd_fixed_{rate}"
    )


def _setup_auction_pricing(ctx: dict, floor: float = 2.0, bid: float = 5.0) -> None:
    """Select an auction option (is_fixed=False, floor set) and bid on it."""
    _select_new_pricing_option(
        ctx, pricing_model="cpm", currency="USD", is_fixed=False, price_guidance={"floor": floor}
    )
    _ensure_request_defaults(ctx)["packages"][0]["bid_price"] = bid


def _setup_both_pricing(ctx: dict, rate: float = 5.00, floor: float = 2.0) -> None:
    """Select a fixed option whose row also carries a floor (both set on the seller row).

    The XOR is a property of the PUBLISHED option, and the seller publishes a fixed row as
    ``fixed_price`` alone (``product_conversion``), so the buyer sees a valid fixed option.
    """
    _select_new_pricing_option(
        ctx,
        pricing_model="cpm",
        currency="USD",
        is_fixed=True,
        rate=rate,
        price_guidance={"floor": floor},
        pricing_option_id="cpm_usd_fixed_with_floor",
    )


def _setup_neither_pricing(ctx: dict) -> None:
    """Select a pricing_option_id that resolves to no option on the product.

    The seller cannot hold an option with neither a fixed price nor a floor
    (check_fixed_has_rate, check_auction_has_price_guidance), so the state a buyer can
    produce is an id the product does not offer: INVALID_PRICING_OPTION.
    """
    kwargs = _ensure_request_defaults(ctx)
    assert kwargs.get("packages"), "No packages in request — cannot assign neither-set pricing option"
    kwargs["packages"][0]["pricing_option_id"] = "cpm_usd_neither"


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
        # pricing-options/cpa-option.json requires event_type, and the seller cannot
        # publish a CPA option without one (product_conversion refuses the row).
        _select_new_pricing_option(
            ctx, pricing_model="cpa", currency="USD", is_fixed=True, rate=10.00, parameters={"event_type": "purchase"}
        )

    elif config in ("both_set", "fixed+floor"):
        _setup_both_pricing(ctx)

    elif config in ("neither_set", "neither", "neither (code rejects)"):
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


def _set_packages(ctx: dict, selections: list[tuple[str, str]]) -> None:
    """Replace the request's packages: one per ``(product_id, currency)``.

    Each package selects a fixed CPM option in that currency. The default product and
    its seeded option are reused, and every other row is created ONCE: a product named
    twice is one product, and one option per (product, currency) -- a second
    ``cpm_<currency>_fixed`` row on the same product violates
    ``uq_pricing_options_option_id``.
    """
    env = ctx["env"]
    kwargs = _ensure_request_defaults(ctx)
    default_product = ctx["default_product"]
    default_option = ctx["default_pricing_option"]
    products = {default_product.product_id: default_product}
    options = {(default_product.product_id, default_option.currency): default_option}
    packages = []
    for product_id, currency in selections:
        if product_id not in products:
            products[product_id] = ProductFactory(
                tenant=ctx["tenant"], product_id=product_id, property_tags=["all_inventory"]
            )
        if (product_id, currency) not in options:
            options[(product_id, currency)] = PricingOptionFactory(
                product=products[product_id], pricing_model="cpm", currency=currency, is_fixed=True, rate=5.00
            )
            env._commit_factory_data()
        option = options[(product_id, currency)]
        packages.append({"product_id": product_id, "budget": 5000.0, "pricing_option_id": pricing_option_id(option)})
    kwargs["packages"] = packages


def _setup_multi_package_request(ctx: dict, currencies: list[str]) -> None:
    """One package per currency: the default product first, a fresh product for each other."""
    default_pid = ctx["default_product"].product_id
    _set_packages(ctx, [(default_pid if i == 0 else f"product_{c.lower()}_{i}", c) for i, c in enumerate(currencies)])


def _select_currency_option(ctx: dict, currency: str) -> None:
    """Select a fixed CPM option in ``currency`` on the default product's single package."""
    _select_new_pricing_option(ctx, pricing_model="cpm", currency=currency, is_fixed=True, rate=5.00)


def _setup_ad_server_currencies(ctx: dict, currency: str, *, secondary: list[str]) -> None:
    """A tenant that accepts ``currency`` but whose ad-server network is USD-primary.

    ``currency`` goes into the tenant's CurrencyLimit table so the tenant gate passes and
    the ad-server gate (``AdapterConfig.gam_network_currency`` plus
    ``gam_secondary_currencies``) is the one graded. ``secondary`` lists the network's
    enabled secondary currencies. Written through the AdapterConfig upsert the e2e server
    reads as well.
    """
    from tests.factories.core import set_adapter_type

    env = ctx["env"]
    CurrencyLimitFactory(tenant=ctx["tenant"], currency_code=currency)
    row = set_adapter_type(env, ctx["tenant"].tenant_id, "mock")
    row.gam_network_currency = "USD"
    row.gam_secondary_currencies = secondary
    env._commit_factory_data()
    _select_currency_option(ctx, currency)


@given(parsers.parse("the currency scenario is {partition}"))
def given_currency_scenario(ctx: dict, partition: str) -> None:
    """Set up currency configuration for partition scenarios (BR-RULE-009).

    Partition values:
    - single_package: 1 package, USD (trivially valid — only 1 currency)
    - all_same_currency: 2 packages, both USD
    - currency_in_tenant_table: 1 package, EUR (tenant has EUR in CurrencyLimit)
    - currency_in_ad_server: 1 package, JPY; tenant has JPY, network secondary has JPY
    - mixed_currencies: 2 packages, USD + EUR (cross-package mismatch)
    - currency_not_in_tenant: 1 package, XYZ (not in tenant's CurrencyLimit table)
    - currency_not_in_ad_server: 1 package, JPY; tenant has JPY, network is USD only
    """
    partition = partition.strip()

    if partition == "single_package":
        # Default request has 1 package with USD — already valid
        _ensure_request_defaults(ctx)

    elif partition == "all_same_currency":
        _setup_multi_package_request(ctx, ["USD", "USD"])

    elif partition == "currency_in_tenant_table":
        CurrencyLimitFactory(tenant=ctx["tenant"], currency_code="EUR")
        _select_currency_option(ctx, "EUR")

    elif partition == "currency_in_ad_server":
        _setup_ad_server_currencies(ctx, "JPY", secondary=["JPY"])

    elif partition == "mixed_currencies":
        _setup_multi_package_request(ctx, ["USD", "EUR"])

    elif partition == "currency_not_in_tenant":
        _select_currency_option(ctx, "XYZ")

    elif partition == "currency_not_in_ad_server":
        _setup_ad_server_currencies(ctx, "JPY", secondary=[])

    else:
        raise ValueError(f"Unknown currency partition: {partition}")


@given(parsers.parse("the currency configuration is: {config}"))
def given_currency_configuration(ctx: dict, config: str) -> None:
    """Set up currency configuration for boundary scenarios (BR-RULE-009).

    Boundary values:
    - 1 pkg USD: single package, USD
    - 2 pkg USD+USD / 2 pkg same pricing_option_id currency: two packages, same currency
    - 2 pkg USD+EUR / 2 pkg divergent pricing_option_id currency: two packages, different
      currencies. The pin carries currency on the selected pricing option only, so the two
      spellings of each pair are one request.
    - 1 pkg XYZ: single package, currency the tenant does not hold
    - 1 pkg JPY (GAM no JPY): tenant holds JPY, the ad-server network does not
    - total_budget USD, 1 pkg EUR: the buyer's total_budget names USD (equal to the
      package budget sum, as fixed mode requires) while the only package selects EUR
    """
    config = config.strip()

    if config == "1 pkg USD":
        _ensure_request_defaults(ctx)

    elif config in ("2 pkg USD+USD", "2 pkg same pricing_option_id currency"):
        _setup_multi_package_request(ctx, ["USD", "USD"])

    elif config in ("2 pkg USD+EUR", "2 pkg divergent pricing_option_id currency"):
        _setup_multi_package_request(ctx, ["USD", "EUR"])

    elif config == "1 pkg XYZ":
        _select_currency_option(ctx, "XYZ")

    elif config == "1 pkg JPY (GAM no JPY)":
        _setup_ad_server_currencies(ctx, "JPY", secondary=[])

    elif config == "total_budget USD, 1 pkg EUR":
        _select_currency_option(ctx, "EUR")
        kwargs = ctx["request_kwargs"]
        kwargs["total_budget"] = {"amount": sum(pkg["budget"] for pkg in kwargs["packages"]), "currency": "USD"}

    else:
        raise ValueError(f"Unknown currency boundary config: {config}")


# ═══════════════════════════════════════════════════════════════════════
# Product uniqueness partition/boundary — BR-RULE-010
# ═══════════════════════════════════════════════════════════════════════


def _setup_multi_product_request(ctx: dict, product_ids: list[str]) -> None:
    """One USD package per product_id. A repeated id is the uniqueness violation under test."""
    _set_packages(ctx, [(pid, "USD") for pid in product_ids])


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

    Sets the two minimums (``_set_minimums``), then adjusts the request package budget.
    """
    kwargs = _ensure_request_defaults(ctx)
    _set_minimums(ctx, product_min=product_min, tenant_min=tenant_min)
    if kwargs.get("packages"):
        kwargs["packages"][0]["budget"] = budget


@given("the seller sets no minimum package budget")
def given_no_minimum_package_budget(ctx: dict) -> None:
    """Clear the seller's minimum at both levels, so a budget is judged by no seller minimum.

    For a budget-amount boundary: the factory tenant's USD CurrencyLimit carries
    ``min_package_budget=100``, which refuses every small positive budget with the same
    BUDGET_TOO_LOW a positivity rule answers, so the row could not tell the two apart.
    """
    _set_minimums(ctx, product_min=None, tenant_min=None)


def _set_minimums(ctx: dict, *, product_min: float | None, tenant_min: float | None) -> None:
    """Set PricingOption.min_spend_per_package (product) and CurrencyLimit.min_package_budget (tenant)."""
    env = ctx["env"]

    # Product-level minimum: update the default pricing option IN PLACE -- the one row
    # the package already selects. A sibling cpm/USD/fixed row cannot be added instead:
    # ``uq_pricing_options_option_id`` refuses a second ``cpm_usd_fixed`` on the product.
    # Safe because the row is this scenario's own: ``_harness_env`` is function-scoped
    # and seeds a fresh tenant/product/option per scenario.
    po = ctx["default_pricing_option"]
    po.min_spend_per_package = Decimal(str(product_min)) if product_min is not None else None

    _set_tenant_min_package_budget(env, tenant_min)


def _set_tenant_min_package_budget(env: Any, tenant_min: float | None) -> None:
    """Write the tenant's USD ``CurrencyLimit.min_package_budget`` (None clears it).

    The tenant-level seller minimum, on its existing USD row; committed through the env,
    so on e2e_rest it is the live server's row that changes.
    """
    cl = env.get_currency_limit("USD")
    cl.min_package_budget = Decimal(str(tenant_min)) if tenant_min is not None else None
    env._commit_factory_data()


@given("the tenant sets no min_package_budget")
def given_no_tenant_min_package_budget(ctx: dict) -> None:
    """Clear the seller's tenant-level package minimum (the seeded tenant's is 100).

    For rows that grade the request's own budget rules: with the minimum in place, any
    amount under 100 is refused BUDGET_TOO_LOW by it, so a 0.01 row could not succeed and
    a 0 row passed whether or not the positivity rule ran.
    """
    _set_tenant_min_package_budget(ctx["env"], None)
    assert ctx["env"].get_currency_limit("USD").min_package_budget is None, (
        "step claims the tenant sets no min_package_budget, but its USD CurrencyLimit still has one"
    )


@given(parsers.parse("the minimum spend scenario is {partition}"))
def given_minimum_spend_scenario(ctx: dict, partition: str) -> None:
    """Set up minimum spend configuration for partition scenarios (BR-RULE-008).

    Partition values:
    - budget_meets_product_min: budget 5000 >= product min_spend 1000
    - budget_meets_tenant_min: budget 5000 >= tenant min_package_budget 100 (no product min)
    - no_minimum_configured: no min_spend at either level
    - budget_below_product_min: budget 500 < product min_spend 1000, but >= tenant
      min_package_budget 100, so only the product minimum can refuse it
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
        # Between the two minimums. At 50 the tenant minimum refused it too, with the same
        # code and field, so the row passed whether or not the product minimum was enforced.
        _set_min_spend(ctx, product_min=1000.0, tenant_min=100.0, budget=500.0)

    elif partition == "budget_below_tenant_min":
        _set_min_spend(ctx, product_min=None, tenant_min=100.0, budget=50.0)

    else:
        raise ValueError(f"Unknown minimum spend partition: {partition}")


@given(parsers.parse("the minimum spend configuration is: {config}"))
def given_minimum_spend_configuration(ctx: dict, config: str) -> None:
    """Set up minimum spend configuration for boundary scenarios (BR-RULE-008).

    Boundary values:
    - budget=100 min=100: budget exactly at product min_spend (exact match), no tenant min
    - budget=99.99 min=100: budget just below product min_spend, no tenant min
    - budget=50 tmin=50: budget exactly at tenant min_package_budget (no product min)
    - budget=49.99 tmin=50: budget just below tenant min_package_budget
    - budget=1 no-min: no minimum configured at any level

    The two product-minimum rows clear the tenant minimum. With a tenant minimum of 100 as
    well, 99.99 was refused by the tenant check with the same code and field, so the rows
    graded nothing about the product minimum.
    """
    config = config.strip()

    if config == "budget=100 min=100":
        _set_min_spend(ctx, product_min=100.0, tenant_min=None, budget=100.0)

    elif config == "budget=99.99 min=100":
        _set_min_spend(ctx, product_min=100.0, tenant_min=None, budget=99.99)

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
    env = ctx["env"]
    kwargs = _ensure_request_defaults(ctx)

    # Set flight duration via start_time / end_time
    kwargs["start_time"] = _future(1).isoformat()
    kwargs["end_time"] = _future(1 + flight_days).isoformat()

    # Set package budget
    if kwargs.get("packages"):
        kwargs["packages"][0]["budget"] = budget

    # Set cap on CurrencyLimit
    cl = env.get_currency_limit("USD")
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

    THE DAILY RATE IS WHAT THESE ROWS VARY; THE TOTAL IS NOT FREE. A cap applies to
    ``budget / flight_days``, so a row can reach any daily figure by raising the budget or by
    shortening the flight -- but the TOTAL is what every downstream limit sees, and impressions
    are derived from it (``budget / cpm * 1000``). The no-cap row reached daily=9999 the first
    way, with a 99990 budget over ten days, and at the seeded $10 CPM that asks the seller for
    9,999,000 impressions against the mock ad server's 1,000,000 goal ceiling (a real
    GAM-style limit, ``mock_ad_server.py``). The live server refused it --
    ``packages[0].impressions``, rejected_value 9999000 -- and the row failed on e2e_rest while
    passing on the three transports that mock the adapter and so never reach that check. Its
    sibling at 10000 survives by exactly one impression: 1,000,000 is not ``> 1000000``.
    Reaching the same daily figure by shortening the flight keeps the row's meaning and stops
    it asking for inventory no seller would sell.
    """
    config = config.strip()

    if config == "daily=1000 cap=1000":
        _set_daily_spend_cap(ctx, cap=1000.0, budget=10000.0)

    elif config == "daily=1001 cap=1000":
        _set_daily_spend_cap(ctx, cap=1000.0, budget=10010.0)

    elif config == "daily=9999 no-cap":
        # daily = 9999/1 = 9999, which breaches every cap this outline uses, so the row still
        # proves the check is SKIPPED rather than merely satisfied. 999,900 impressions.
        _set_daily_spend_cap(ctx, cap=None, budget=9999.0, flight_days=1)

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

    Valid partitions (8): targeting validation passes
    Invalid partitions (11): INVALID_REQUEST for a request fault, UNSUPPORTED_FEATURE for a
    dimension this seller does not declare (keyword_targeting, proximity_*)
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
        # Two pinned dimensions this seller declares in get_adcp_capabilities
        # (media_buy.execution.targeting geo_countries and geo_regions). The row used to pair
        # geo_countries with device_type_any_of, a seller-internal field no pinned
        # core/targeting.json declares, which the live ad server refuses as UNSUPPORTED_FEATURE.
        _set_targeting_overlay(
            ctx,
            overlay={
                "geo_countries": ["US"],
                "geo_regions": ["US-CA"],
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

    elif partition == "undeclared_dimension":
        # key_value_pairs: deleted from Targeting (salesagent-3cs7o.22), undeclared by the pin.
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

    elif config == "key_value_pairs":
        # Deleted from Targeting (salesagent-3cs7o.22); the pin never declared it.
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
    assert kwargs.get("packages"), "No packages in request — cannot add creative_ids without a package"
    pkg = kwargs["packages"][0]
    existing = pkg.get("creative_ids") or []
    existing.extend(creative_ids)
    pkg["creative_ids"] = existing


def _create_approved_creative(
    ctx: dict, creative_id: str, fmt: str = "display_300x250_image", asset_key: str = "banner_image"
) -> Any:
    """Create an approved Creative in the DB and return it.

    Uses CreativeFactory with the tenant/principal from harness context.

    THE FORMAT AND THE ASSET KEY MOVE TOGETHER and are not independent defaults. The
    previous pair -- ``display_300x250`` keyed ``primary`` -- named the harness's own
    format-spec STUB: the id is absent from the pinned capture, and ``primary`` was the
    stub's asset_id. With the stub gone, both halves have to come from the capture, which
    declares ``banner_image`` (image, required) and ``click_url`` (url, required) for
    ``display_300x250_image``.

    Only ``banner_image`` is seeded, which is survivable TODAY and is worth saying why:
    nothing validates a creative's assets against the resolved spec, so a missing required
    ``click_url`` costs nothing here. That gap is #767; when it lands, this default needs
    the url asset too. The pre-existing ``cr-upload-ok`` call site made the same trade.
    """
    from tests.factories.creative import CreativeFactory

    env = ctx["env"]
    creative = CreativeFactory(
        creative_id=creative_id,
        tenant=ctx["tenant"],
        principal=ctx["principal"],
        format=fmt,
        approved=True,
        data={"assets": {asset_key: {"url": "https://example.com/banner.png", "width": 300, "height": 250}}},
    )
    env._commit_factory_data()
    return creative


def _add_inline_creatives(ctx: dict, count: int = 1, fmt_id: str = "display_300x250_image") -> None:
    """Add inline creative dicts to the first package's 'creatives' field.

    Builds creative payloads matching the product's accepted format, through
    ``CreativeAssetRequestFactory`` so the item is the pinned request shape.

    The asset map goes through ``image_spec`` rather than being typed out: the
    hand-built ``{"primary": {url, width, height}}`` carried no ``asset_type``
    discriminator, and ``PackageRequest.creatives`` rejects that with
    ``assets.primary.AssetVariant Unable to extract tag using discriminator
    'asset_type' [type=union_tag_not_found]`` — so every caller here was seeding a
    package the request model refuses. Not a scenario subject anywhere (all six
    callers ask for ordinary inline creatives), so it is fixed rather than declared
    malformed. Same correction as ``uc003_update_media_buy`` and
    ``uc003_ext_error_scenarios``.
    """
    kwargs = _ensure_request_defaults(ctx)
    if kwargs.get("packages"):
        pkg = kwargs["packages"][0]
        creatives = pkg.get("creatives") or []
        creatives.extend(inline_creative_payload(i + 1, fmt_id) for i in range(count))
        pkg["creatives"] = creatives


def inline_creative_payload(number: int, fmt_id: str = "display_300x250_image") -> dict[str, Any]:
    """The *number*-th inline creative ``_add_inline_creatives`` builds: one image for *fmt_id*."""
    return CreativeAssetRequestFactory.payload(
        creative_id=f"inline-cr-{number:03d}",
        name=f"Inline Creative {number}",
        format_id={
            "agent_url": "https://creative.adcontextprotocol.org",
            "id": fmt_id,
        },
        # Every asset the CAPTURE requires for this format, keyed by its asset_id, not by
        # the deleted stub's ``primary``. See _create_approved_creative.
        assets=named_format_assets(fmt_id, media_url=f"https://example.com/banner-{number}.png"),
    )


def inline_creative_media_asset(creative: dict[str, Any]) -> dict[str, Any]:
    """The asset carrying *creative*'s content URL: its one image, video or audio asset.

    The named format requires more than one asset (``display_300x250_image``: ``banner_image``
    image plus ``click_url`` url), so a step that breaks "the" asset has to say which. The
    content asset is the media one (``MEDIA_ASSET_TYPES``); a ``url`` asset is a clickthrough
    link, not the creative's content. Refuses a creative with no media asset, or with several,
    because then the scenario has to name the asset itself.
    """
    assets = creative.get("assets") or {}
    media = [a for a in assets.values() if isinstance(a, dict) and a.get("asset_type") in MEDIA_ASSET_TYPES]
    assert len(media) == 1, f"expected exactly one media asset to break, got assets={assets}"
    return media[0]


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
        creative = _create_approved_creative(ctx, "cr-badfmt-001", fmt="video_1280x720")
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
        creative = _create_approved_creative(ctx, "cr-wrongfmt", fmt="video_1280x720")
        _add_creative_ids_to_package(ctx, [creative.creative_id])

    elif config == "weight=0":
        # Creative reference — weight=0 (paused) is valid boundary
        creative = _create_approved_creative(ctx, "cr-w0")
        _add_creative_ids_to_package(ctx, [creative.creative_id])
        # SPEC-PRODUCTION GAP: create_media_buy uses creative_ids (no weight param).
        # The actual weight is set during _sync_creatives at default=100.
        # Weight=0 (paused) cannot be set at creation time — production always
        # assigns 100. Expected value reflects production reality, not the
        # boundary intent from the scenario name.

    elif config == "weight=100":
        # Creative reference — weight=100 (max rotation) is valid boundary
        creative = _create_approved_creative(ctx, "cr-w100")
        _add_creative_ids_to_package(ctx, [creative.creative_id])
        # Store the expected weight for boundary verification.

    elif config == "101 uploads":
        # 101 inline creatives — exceeds spec limit
        _add_inline_creatives(ctx, count=101)

    else:
        raise ValueError(f"Unknown creative asset boundary config: {config}")


# ═══════════════════════════════════════════════════════════════════════
# UC-002 ext-o/ext-p/ext-g/ext-q — single creative error scenarios
# ═══════════════════════════════════════════════════════════════════════
# These wire the four named creative-error scenarios so they dispatch a real
# create_media_buy on the wire and assert the canonical creative error code:
#   ext-o: creative_id referenced but absent from the library  → CREATIVE_REJECTED
#   ext-p: creative format not in product's accepted formats    → CREATIVE_REJECTED
#   ext-g: inline creative missing its required content URL      → message contains "URL"
#   ext-q: ad server rejects the creative upload                 → SERVICE_UNAVAILABLE
# They reuse the creative-seeding helpers above; each step text is defined once.


@given(parsers.parse('a package creative_assignment references creative_id "{creative_id}"'))
def given_package_references_missing_creative(ctx: dict, creative_id: str) -> None:
    """ext-o: reference a creative_id with no matching library row.

    No Creative is seeded for ``creative_id``, so the create path's
    "Creative IDs not found" check (media_buy_create.py) raises
    AdCPCreativeRejectedError → CREATIVE_REJECTED on the wire, with the missing
    id and a sync_creatives suggestion.

    Auto-approval is seeded because the CREATIVE_REJECTED raise lives on the
    auto path; the live-server default (human_review_required=True) would take
    the PENDING path, which silently skips missing creatives.
    """
    _add_creative_ids_to_package(ctx, [creative_id])
    _seed_auto_approval(ctx)


@given("a creative's format_id does not match any of the product's supported format_ids")
def given_creative_format_mismatch(ctx: dict) -> None:
    """ext-p: seed an approved creative whose format is absent from the product.

    The default product accepts ``display_300x250_image``; this creative carries
    ``video_1280x720``. The pre-adapter creative validation in the create path rejects the
    format mismatch with CREATIVE_REJECTED on the wire.

    BOTH ids are in the pinned capture, which is what makes this partition grade the
    MISMATCH. It used to carry ``video_640x480``, absent from the capture, so the creative
    could be refused for not EXISTING rather than for not matching -- a partition passing
    for a reason other than the one it is named after. ``video_1280x720`` is present and
    still mismatches an image product, so the intent survives and the vacuity does not.

    Auto-approval is seeded because the CREATIVE_REJECTED raise lives on the
    auto path; the live-server default (human_review_required=True) would take
    the PENDING path, which emits VALIDATION_ERROR for the same condition.
    """
    creative = _create_approved_creative(ctx, "cr-fmt-mismatch", fmt="video_1280x720")
    _add_creative_ids_to_package(ctx, [creative.creative_id])
    _seed_auto_approval(ctx)


@given("a valid create_media_buy request with inline creatives")
def given_request_with_inline_creatives(ctx: dict) -> None:
    """ext-g/base: attach one valid inline creative to the request's package."""
    _ensure_request_defaults(ctx)
    _add_inline_creatives(ctx, count=1)


@given("a creative is missing the required URL in assets")
def given_inline_creative_missing_url(ctx: dict) -> None:
    """ext-g: strip the content URL from the inline creative's media asset.

    WHAT THIS ACTUALLY REACHES, measured rather than intended. The empty URL is
    refused at the REQUEST BOUNDARY, not by production's reference-creative
    validation: ``CreativeAssetRequest`` rejects ``url: ""`` as ``url_parsing``
    ("Input should be a valid URL, input is empty"), which the buyer receives at
    ``packages[0].creatives[0].assets.banner_image.url``. So T-UC-002-ext-g grades an
    INVALID_REQUEST from the boundary and never gets as far as the
    reference-creative URL check its sentence names.

    That is a real obligation and the scenario now asserts it BY CODE AND FIELD
    (salesagent-b9hi1.2). It used to pass on any error whatsoever, because its two
    Thens asked only that the operation failed and that the error carried a
    suggestion — so it reported green while saying nothing about which refusal
    arrived. It stayed green through the ``asset_type`` repair, when the request was
    dying one step earlier still for ``union_tag_not_found`` and the empty URL was
    not even the reason.

    WHAT REMAINS UNGRADED, and is not this scenario's to fix: production's
    reference-creative URL check, which no request reaching the boundary with an
    empty url can exercise. Closing that needs a payload the pinned model ACCEPTS
    and production refuses — a different scenario, filed rather than faked here.
    """
    kwargs = _ensure_request_defaults(ctx)
    pkg = kwargs["packages"][0]
    creatives = pkg.get("creatives")
    assert creatives, "No inline creatives on package — wire the inline-creatives Given first"
    declared = []
    for creative in creatives:
        # "The required URL" is the creative's CONTENT url, on its media asset. The named
        # format requires two url-bearing assets (banner_image, an image, and click_url, a
        # clickthrough), so the step names the asset by its role instead of by "the one that
        # has a url". It is found by type rather than by a hardcoded key, so the key stays
        # where the fixture puts it (the Then reads it as assets.banner_image.url).
        target = inline_creative_media_asset(creative)
        # Empty, not absent: an absent url would fail as a MISSING field, and the
        # scenario is about a URL the buyer supplied and left blank.
        target["url"] = ""
        declared.append(
            malformed(
                "empty_string",
                "url='' on the media asset is the scenario's whole subject: a URL the buyer "
                "supplied and left blank, not one they omitted. CreativeAssetRequest refuses the "
                "bytes themselves — packages[0].creatives[0].assets.banner_image.url 'Input should be a "
                "valid URL, input is empty' [type=url_parsing] — so the obligation is INVALID_REQUEST "
                "from the request boundary, not VALIDATION_ERROR from a seller rule.",
                creative,
                obligation=ErrorCode.INVALID_REQUEST,
            )
        )
    pkg["creatives"] = declared


@given("the creative format is not generative")
def given_creative_format_not_generative(ctx: dict) -> None:
    """ext-g: assert the inline creative uses a non-generative reference format.

    Generative formats (those with output_format_ids) skip URL validation, so the
    missing-URL rejection only fires for reference formats. The default inline creative uses
    ``display_300x250_image``, which satisfies this precondition on BOTH halves -- and the
    id this used to assert satisfied neither.

    MEASURED against tests/fixtures/creative_formats/reference_formats.json, 76 formats:
    ``display_300x250`` is ABSENT from the capture entirely, so calling it "a reference
    format with no output_format_ids" was false twice over -- it is not a reference format
    the agent serves, and a format that does not exist has no output_format_ids to lack.
    ``display_300x250_image`` IS in the capture and carries no ``output_format_ids`` key
    (69 of the 76 carry none), so the docstring is now true rather than aspirational.
    """
    kwargs = ctx.get("request_kwargs", {})
    for pkg in kwargs.get("packages", []):
        for creative in pkg.get("creatives") or []:
            fmt = creative.get("format_id", {})
            fmt_id = fmt.get("id") if isinstance(fmt, dict) else fmt
            assert fmt_id == "display_300x250_image", (
                f"Inline creative format '{fmt_id}' is not the expected non-generative reference format"
            )


@given("a valid create_media_buy request with inline creatives that passes all validation")
def given_request_inline_creatives_valid(ctx: dict) -> None:
    """ext-q: attach a valid, approved creative whose format/URL pass all validation.

    The creative is well-formed and approved (status='approved', no
    platform_creative_id) so the synchronous create path reaches the ad-server
    upload (adapter.add_creative_assets). Pending-review creatives are held back
    from upload during create, so an *approved* creative is required to exercise
    the upload path the scenario tests. The only failure injected (by the sibling
    'ad server rejects the creative upload' step) is the adapter upload itself.
    Tenant is set to auto-approval so the synchronous upload runs and the adapter
    rejection surfaces on the create wire.
    """
    _ensure_request_defaults(ctx)
    # A REAL reference-catalog format (not the synthetic display_300x250):
    # the live server resolves formats from the 54-format fixture under
    # ADCP_TESTING, so a synthetic id dies CREATIVE_REJECTED before the upload
    # this scenario tests. display_300x250_image's asset_id is banner_image.
    creative = _create_approved_creative(ctx, "cr-upload-ok", fmt="display_300x250_image", asset_key="banner_image")
    _add_creative_ids_to_package(ctx, [creative.creative_id])
    # The default product only accepts display_300x250 — add the real format so
    # the creative-vs-product compat check passes on the server DB too.
    product = ctx.get("default_product")
    if product is not None:
        env = ctx["env"]
        product.format_ids = [
            *(product.format_ids or []),
            {"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250_image"},
        ]
        env._commit_factory_data()
    _seed_auto_approval(ctx)


#: The click destination the canonical-clickthrough scenarios ship and then expect at the
#: ad server. Public and defined once, because the Given seeds it and the Then asserts it: a
#: Then comparing against its own literal would pass on a seller that invented a URL, and
#: two literals drift.
CANONICAL_CLICK_URL = "https://advertiser.example.com/canonical-landing"

#: How a manifest can tell the seller which url asset is the click destination, per the
#: ladder in ``core/assets/url-asset.json``. Two independent signals -- the KEY the asset is
#: stored under, and whether the asset carries ``url_type`` -- and each row offers exactly
#: ONE of them, so each ISOLATES one rung.
#:
#: That orthogonality is the point and it is not free. A row shipping ``url_type`` on an
#: asset ALSO keyed ``landing_page_url`` offers both signals at once: remove the registry
#: lookup from production and the row stays green, because the ``url_type`` read still finds
#: it. A row two rungs can satisfy grades neither.
#:
#: ``mystery`` is a deliberately non-canonical ``asset_group_id``. The registry permits one
#: ("Non-canonical IDs remain valid for platform-specific extensions"), so pairing it with
#: ``url_type`` is a legitimate manifest rather than a malformed one -- which is what makes
#: it a fair isolation of the first rung instead of a broken input.
_CLICK_SIGNALS: dict[str, tuple[str, str | None]] = {
    # url_type present, asset_group_id meaningless -> ONLY the first rung can find it.
    "url_type": ("mystery", "clickthrough"),
    # asset_group_id in the registry, no url_type -> ONLY the registry lookup can find it.
    "the canonical slot name alone": ("landing_page_url", None),
    # neither signal -> the pin forbids a guess and recommends refusing the manifest.
    "nothing the pin recognises": ("mystery", None),
}


@given(
    parsers.parse(
        'a valid create_media_buy request with a canonical creative signalling its clickthrough by "{signal}"'
    )
)
def given_request_canonical_creative_with_clickthrough(ctx: dict, signal: str) -> None:
    """Attach an approved CANONICAL creative whose manifest carries a url asset.

    Shaped after ``given_request_inline_creatives_valid`` above and differing in the two
    things under test: the creative is identified by ``canonical_kind`` rather than by a
    legacy ``format``, and its manifest carries a url asset. Everything else -- approved
    status, auto-approval, one package -- is the sibling's, because those are what make the
    synchronous create path reach ``adapter.add_creative_assets`` at all, and these
    scenarios are about what that call CARRIES rather than whether it happens.

    ``image_main`` is what ``formats/canonical/image.json`` declares as the image slot, and
    it is fixed across every row: the media asset is not what these rows vary.

    The product gets a canonical ``format_options`` declaration, because a canonical
    creative is graded against the product's closed set and the default product publishes
    only the deprecated arm -- without it the buy is refused UNSUPPORTED_FEATURE before any
    upload, and the scenario would grade the closed set instead of the clickthrough.
    """
    from tests.factories.creative import CreativeFactory
    from tests.factories.creative_asset import build_assets, image_spec, url_spec

    assert signal in _CLICK_SIGNALS, f"unknown click signal {signal!r}; known: {sorted(_CLICK_SIGNALS)}"
    asset_key, url_type = _CLICK_SIGNALS[signal]

    _ensure_request_defaults(ctx)
    env = ctx["env"]
    creative = CreativeFactory(
        creative_id="cr-canonical-click",
        tenant=ctx["tenant"],
        principal=ctx["principal"],
        canonical_kind="image",
        approved=True,
        data={
            "assets": build_assets(
                image_spec("image_main"),
                url_spec(asset_key, url=CANONICAL_CLICK_URL, url_type=url_type),
            )
        },
    )
    env._commit_factory_data()
    _add_creative_ids_to_package(ctx, [creative.creative_id])
    _publish_on_default_product(ctx, {"format_kind": "image", "params": {"width": 300, "height": 250}})


@given("a valid create_media_buy request with a canonical audio creative")
def given_request_canonical_audio_creative(ctx: dict) -> None:
    """Attach an approved canonical ``audio_hosted`` creative carrying only its audio slot.

    ``audio_main`` is what ``formats/canonical/audio_hosted.json`` declares, and the
    manifest deliberately carries NOTHING else: the kind also declares ``companion_image``,
    and including one would supply dimensions and so hide the obligation under test. An
    audio asset has no width or height, and the buy must be accepted anyway.
    """
    from tests.factories.creative import CreativeFactory
    from tests.factories.creative_asset import asset_spec, build_assets

    _ensure_request_defaults(ctx)
    env = ctx["env"]
    creative = CreativeFactory(
        creative_id="cr-canonical-audio",
        tenant=ctx["tenant"],
        principal=ctx["principal"],
        canonical_kind="audio_hosted",
        approved=True,
        data={"assets": build_assets(asset_spec("audio_main", "audio", url="https://cdn.example.com/spot.mp3"))},
    )
    env._commit_factory_data()
    _add_creative_ids_to_package(ctx, [creative.creative_id])
    _publish_on_default_product(ctx, {"format_kind": "audio_hosted", "params": {}})


#: A media slot id named by NOTHING except the product declaration under test: no slot of
#: any canonical kind, no member of MEDIA_ASSET_FALLBACK_IDS, no entry or alias of
#: core/asset-group-vocabulary.json. If the seller finds this asset, it read the product's
#: effective declaration — there is no other source that could have told it the name.
PRODUCT_RENAMED_MEDIA_SLOT = "hero_shot"


@given("a valid create_media_buy request whose product renames the canonical media slot")
def given_request_product_renames_the_media_slot(ctx: dict) -> None:
    """A product whose declaration REPLACES the kind's slot list, and a creative keyed to it.

    ``core/product-format-declaration.json`` permits this outright -- its own
    ``audio_hosted`` example declares ``script`` and ``offering_ref``, neither of which any
    kind declares -- so the kind's vocabulary is NOT a superset of what a product may name.
    A seller reading only the kind looks for ``image_main``, finds nothing, and refuses a
    buy whose manifest matches the product's own published declaration exactly.

    The creative is a canonical ``image`` keyed :data:`PRODUCT_RENAMED_MEDIA_SLOT`, with
    dimensions because an image slot is a sized type. The product publishes one declaration
    naming only that slot, which is also what makes this a control rather than a
    demonstration: every other way the seller could find a media asset is excluded by the
    name, so the scenario reddens unless the product declaration was actually consulted.
    """
    from tests.factories.creative import CreativeFactory

    _ensure_request_defaults(ctx)
    env = ctx["env"]
    creative = CreativeFactory(
        creative_id="cr-product-renamed-slot",
        tenant=ctx["tenant"],
        principal=ctx["principal"],
        canonical_kind="image",
        approved=True,
        data={"assets": build_assets(image_spec(PRODUCT_RENAMED_MEDIA_SLOT))},
    )
    env._commit_factory_data()
    _add_creative_ids_to_package(ctx, [creative.creative_id])
    _declare_renamed_media_slot(ctx)


@given("a valid create_media_buy request uploading an inline creative keyed to the slot its product renames")
def given_request_inline_creative_in_renamed_slot(ctx: dict) -> None:
    """The renamed-slot product again, with the creative sent INLINE on the package.

    create_media_buy syncs a package's inline creatives before the package exists, so the
    sync names no assignment for them -- and a sync that assigns nothing holds a canonical
    manifest to its KIND's slots, where ``image`` requires ``image_main``. These creatives are
    assigned the moment the buy exists, and their manifest is owed the product's declaration,
    which names :data:`PRODUCT_RENAMED_MEDIA_SLOT` (creative-manifests.mdx § Basic structure:
    "assets is keyed by asset_group_id from the matched declaration's params.slots[]"). So the
    sibling stored-creative scenario and this one must answer alike; update_media_buy, which
    passes its assignments to the sync, already did.
    """
    from tests.factories.request import OMIT

    _ensure_request_defaults(ctx)
    ctx["request_kwargs"]["packages"][0]["creatives"] = [
        CreativeAssetRequestFactory.payload(
            creative_id="inline-renamed-slot",
            name="Inline creative in the renamed slot",
            format_id=OMIT,
            format_kind="image",
            assets=build_assets(image_spec(PRODUCT_RENAMED_MEDIA_SLOT)),
        )
    ]
    _declare_renamed_media_slot(ctx)


def _declare_renamed_media_slot(ctx: dict) -> None:
    """Give the default product the one image declaration naming :data:`PRODUCT_RENAMED_MEDIA_SLOT`.

    Its slot list REPLACES the kind's, so ``image_main`` is not owed.
    """
    _publish_on_default_product(
        ctx,
        {
            "format_kind": "image",
            "params": {
                "width": 300,
                "height": 250,
                "slots": [{"asset_group_id": PRODUCT_RENAMED_MEDIA_SLOT, "asset_type": "image", "required": True}],
            },
        },
    )


def _publish_on_default_product(ctx: dict, declaration: dict[str, Any]) -> None:
    """Add one canonical *declaration* to the default product's ``format_options``, then auto-approve.

    The canonical creative Givens each need the product to publish the declaration their
    creative selects; auto-approval is seeded so the create takes the synchronous path the
    Thens read.
    """
    product = ctx.get("default_product")
    if product is not None:
        product.format_options = [*(product.format_options or []), declaration]
        ctx["env"]._commit_factory_data()
    _seed_auto_approval(ctx)


@given("the ad server rejects the creative upload")
def given_ad_server_rejects_creative_upload(ctx: dict) -> None:
    """ext-q: make the adapter raise on the creative-upload call.

    Configures ``add_creative_assets`` to raise so the create path's
    synchronous upload surfaces the adapter failure as SERVICE_UNAVAILABLE
    on the wire (instead of swallowing it — 'No Quiet Failures').
    """
    from src.core.exceptions import AdCPAdapterError

    message = "Ad server rejected the creative upload"

    env = ctx["env"]
    mock_adapter = env.mock["adapter"].return_value
    # Model a CONFORMANT adapter error: the buyer suggestion arrives at the top-level
    # error.json position on its own, because the code resolves it from CODE_TABLE. The
    # step used to hand-author it through a suggestion= parameter; that parameter no
    # longer exists, and the scenario asserts the code rather than the sentence.
    mock_adapter.add_creative_assets.side_effect = AdCPAdapterError()
    # E2E path: write the failure to the adapter test-behavior config so the
    # Docker-hosted adapter raises the same error on creative upload
    # (MockAdServer.add_creative_assets reads the fail_on_upload flag).
    _sync_adapter_error_to_db(
        ctx,
        fail_on_upload=True,
        error_message=message,
    )


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

    elif partition == "event_not_supported_by_product":
        # Configure product to not support event/conversion tracking
        _set_optimization_goals(ctx, [_event_goal(event_sources=_simple_event_sources())])

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

    elif config == "no metric capability":
        _set_optimization_goals(ctx, [_metric_goal("clicks")])

    elif config == "event capable":
        _set_optimization_goals(ctx, [_event_goal(event_sources=_simple_event_sources())])

    elif config == "no event capability":
        _set_optimization_goals(ctx, [_event_goal(event_sources=_simple_event_sources())])

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
    kwargs["proposal_id"] = mint(f"prop-{uuid.uuid4().hex[:8]}")
    kwargs["total_budget"] = {"amount": float(amount), "currency": "USD"}


@given("a valid create_media_buy request in proposal mode with a proposal_id and total_budget")
def given_request_proposal_mode(ctx: dict) -> None:
    """Set up a proposal-mode request with proposal_id and total_budget.

    In proposal mode the buyer supplies a proposal_id and total_budget but
    does NOT supply a manual packages array -- the seller derives packages
    from the proposal's product allocations.
    """
    kwargs = _ensure_request_defaults(ctx)
    kwargs["proposal_id"] = mint(f"prop-{uuid.uuid4().hex[:8]}")
    kwargs["total_budget"] = {"amount": 5000.0, "currency": "USD"}
    # Remove the packages array to signal proposal mode (seller derives packages)
    kwargs.pop("packages", None)


@given(parsers.parse('proposal "{proposal_id}" does not exist or has expired'))
def given_proposal_not_exists(ctx: dict, proposal_id: str) -> None:
    """Mark that the referenced proposal does not exist.

    SPEC-PRODUCTION GAP: Production has no proposal store — proposal_id is
    accepted but never validated. This step cannot establish the claimed
    precondition, so the scenario is xfailed.

    FIXME: When production implements proposal storage, this step must:
    1. Verify no proposal record exists for this ID, OR
    2. Create an expired proposal record to test the expiry path
    Then remove the xfail.
    """
    import pytest

    assert proposal_id, "proposal_id must be non-empty"
    pytest.xfail(
        "SPEC-PRODUCTION GAP: Production has no proposal store — cannot establish "
        f"'proposal \"{proposal_id}\" does not exist or has expired' precondition. "
        "FIXME"
    )


@given(parsers.parse("the proposal's total_budget_guidance.min is {amount:d}"))
def given_proposal_budget_guidance_min(ctx: dict, amount: int) -> None:
    """Set expected proposal budget guidance minimum.

    SPEC-PRODUCTION GAP: Production has no proposal budget guidance.
    This step cannot configure the claimed precondition, so the scenario is xfailed.

    FIXME: When production implements proposal budget guidance, this step must:
    1. Configure the proposal record with total_budget_guidance.min = amount
    2. Verify the proposal exists in ctx before setting guidance
    Then remove the xfail.
    """
    import pytest

    assert amount >= 0, f"Budget guidance minimum must be non-negative, got {amount}"
    pytest.xfail(
        "SPEC-PRODUCTION GAP: Production has no proposal budget guidance — cannot establish "
        f"'total_budget_guidance.min is {amount}' precondition. FIXME"
    )


# ═══════════════════════════════════════════════════════════════════════
# Adapter state
# ═══════════════════════════════════════════════════════════════════════


# "the ad server adapter is available" is owned by uc002_create_media_buy
# (canonical) — removed here to avoid a cross-module shadow now that this module
# is registered. No UC-003 scenario uses that text.


@given("the ad server adapter returns an error")
def given_adapter_error(ctx: dict) -> None:
    """Configure the mock adapter to return an error on any operation."""
    from src.core.exceptions import AdCPAdapterError

    env = ctx["env"]
    mock_adapter = env.mock["adapter"].return_value
    # recovery is not stated: it is a function of the code. This step previously injected
    # "retryable", which is not in RecoveryHint at all — the parameter's deletion made that
    # unrepresentable, and AdCPAdapterError's SERVICE_UNAVAILABLE table recovery
    # ("transient") is what a buyer should see for an ad-server outage anyway.
    # No details either, for the same reason recovery is absent: ``suggestion`` is a
    # read-only property over CODE_TABLE, so a ``details={"suggestion": ...}`` block never
    # reached it. A details block is a declared ErrorDetails subclass and there is no
    # suggestion field on one -- this fixture fabricated a shape production cannot produce.
    error = AdCPAdapterError()
    mock_adapter.create_media_buy.side_effect = error
    mock_adapter.update_media_buy.side_effect = error
    # Also write to DB so Docker adapter raises the same error.
    # Must also disable manual approval so the adapter is actually called
    # (manual approval short-circuits before calling adapter.create_media_buy).
    _sync_adapter_approval_to_db(ctx, manual_approval_required=False)
    _sync_adapter_error_to_db(
        ctx,
        fail_on_create=True,
        fail_on_update=True,
        error_message="Ad server unavailable",
        # No recovery= knob: the DOCKER-hosted MockAdServer maps the injected value to
        # an exception CLASS, and its default for an absent knob is already "transient"
        # -> AdCPAdapterError, which is what this scenario means by an ad-server outage.
        # Naming it would only re-state the default; naming "retryable" (the old value)
        # would trip that mapping's loud-raise branch and hand e2e a terminal
        # CONFIGURATION_ERROR instead.
    )
    # Ensure tenant is auto-approval so production code doesn't short-circuit
    _seed_auto_approval(ctx, sync_adapter=False)
    # Strip creative_ids so E2E doesn't fail on creative format validation
    # (the Docker creative agent doesn't know test formats like display_300x250).
    # This scenario tests adapter failure, not creative assignment.
    for pkg in ctx.get("request_kwargs", {}).get("packages", []):
        pkg.pop("creative_ids", None)


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
    # The update path's adapter, in process and (through the DB knob, which also clears
    # fail_on_create) on the live server.
    set_update_adapter_outcome(ctx, None)


@given("a create_media_buy request")
@given("a valid create_media_buy request with packages")
def given_bare_create_request(ctx: dict) -> None:
    """Set up a bare create_media_buy request with valid defaults."""
    _ensure_request_defaults(ctx)


@given("the request uses legacy mode with no per-package budgets")
def given_legacy_mode_no_packages(ctx: dict) -> None:
    """Convert request to legacy mode: total_budget only, no packages array.

    In legacy mode (v3.1 BR-RULE-012 INV-5), the buyer sends a total_budget
    without per-package budget breakdowns. The daily cap is validated against
    the total budget treated as a single daily figure.
    """
    kwargs = _ensure_request_defaults(ctx)
    # Capture the total from existing packages before removing them
    total = sum(pkg.get("budget", 0) for pkg in kwargs.get("packages", []))
    if total <= 0:
        total = 5000.0
    kwargs["total_budget"] = {"amount": total, "currency": "USD"}
    # Remove the packages array to signal legacy mode
    kwargs.pop("packages", None)


@given("the request supplies no buyer packages array")
def given_no_packages_array(ctx: dict) -> None:
    """Remove the packages array from the request.

    In proposal mode, the buyer does not supply a manual packages array --
    the seller derives packages from the proposal's product allocations.
    The product-uniqueness check (BR-RULE-010) has no applicable buyer input.
    """
    kwargs = _ensure_request_defaults(ctx)
    kwargs.pop("packages", None)


@given(parsers.parse("a valid create_media_buy request that passes all validation"))
def given_request_passes_validation(ctx: dict) -> None:
    """Set up a request that passes all validation checks.

    Includes creative_ids with matching DB records so scenarios that test
    creative assignment persistence (INV-020-1) have actual creatives.

    Through ``_create_approved_creative``, so the creative carries a format the pinned
    capture SERVES and the product accepts. It was hand-built on ``display_300x250``, an id
    absent from the capture, so the create's format resolution refused it and a request this
    sentence calls valid failed validation (``given_adapter_error`` strips the ids for
    exactly that reason).
    """
    creative = _create_approved_creative(ctx, "cr-all-validation")
    _add_creative_ids_to_package(ctx, [creative.creative_id])


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
    AdCPValidationError with recovery="correctable" and a suggestion.
    """
    _ensure_request_defaults(ctx)
    for pkg in ctx["request_kwargs"].get("packages", []):
        pkg["product_id"] = "nonexistent-correctable-product"


@given(parsers.parse('a media buy exists in "{state}" state'))
def given_existing_media_buy(ctx: dict, state: str) -> None:
    """Create a media buy in the specified state in the database.

    Uses env.seed_media_buy() which handles both modes:
    - In-process: factory with deterministic IDs (per-test DB, no collision)
    - E2E: HTTP POST to Docker → server-generated UUID (shared DB, no collision)
    """

    env = ctx["env"]
    product = ctx.get("default_product")
    assert product is not None, (
        "No default_product in ctx — Background must set up ctx['default_product'] "
        "before 'a media buy exists in ... state'"
    )
    media_buy = env.seed_media_buy(
        tenant=ctx["tenant"],
        principal=ctx["principal"],
        product=product,
        status=state,
        push_notification_config=ctx.get("push_notification_config"),
    )
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

    Production refuses a package whose catalogs repeat a type (VALIDATION_ERROR). It
    does not check catalog_id existence: the seller has no sync_catalogs, so there is
    no synced catalog to look one up in.
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

    Same production behavior as the partition step above.
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
        # Two packages, each with type=product — distinct per-package, not cross-package.
        # Two PRODUCTS as well: a repeated product_id is refused by the uniqueness rule
        # before any catalog rule is reached.
        _setup_multi_product_request(ctx, [ctx["default_product"].product_id, "product_catalog_b"])
        for package in _ensure_request_defaults(ctx)["packages"]:
            package["catalogs"] = [_make_catalog("product")]

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


def _set_format_ids(ctx: dict, format_ids: list[dict[str, Any] | str] | None) -> None:
    """Set format_ids on the first package of the request."""
    kwargs = _ensure_request_defaults(ctx)
    if format_ids is None:
        kwargs["packages"][0].pop("format_ids", None)
    else:
        kwargs["packages"][0]["format_ids"] = format_ids


#: The boundary outline's spellings of the partition outline's cases. One request per case.
_FORMAT_ID_BOUNDARY_CASES = {
    "valid FormatId": "valid_format_id",
    '"banner_300x250"': "plain_string",
    "no agent_url": "missing_agent_url",
    "bad agent_url": "unregistered_agent",
    "unknown format": "unknown_format",
}


def _format_ids_for(ctx: dict, case: str) -> list[dict[str, Any] | str]:
    """The package ``format_ids`` for one format-ID-structure case.

    Every case is derived from the seeded product's own format reference, so the valid
    case is one the product actually accepts and each invalid case differs from it in
    exactly the one dimension it names. A literal ``display_300x250`` here was refused by
    the product (which carries ``display_300x250_image``), so "valid" was never valid.
    """
    valid = dict(ctx["default_product"].format_ids[0])
    if case == "valid_format_id":
        return [valid]
    if case == "plain_string":
        return ["banner_300x250"]
    if case == "missing_agent_url":
        return [{"id": valid["id"]}]
    if case == "missing_id":
        return [{"agent_url": valid["agent_url"]}]
    if case == "unregistered_agent":
        return [{**valid, "agent_url": "https://unknown-agent.example.com"}]
    if case == "unknown_format":
        return [{**valid, "id": "nonexistent_format_999"}]
    raise ValueError(f"Unknown format ID case: {case}")


@given(parsers.parse("the format ID scenario is {partition}"))
def given_format_id_partition(ctx: dict, partition: str) -> None:
    """Set up format_ids for partition scenarios (format ID structure)."""
    _set_format_ids(ctx, _format_ids_for(ctx, partition.strip()))


@given(parsers.parse("the format ID scenario is: {config}"))
def given_format_id_boundary(ctx: dict, config: str) -> None:
    """Set up format_ids for boundary scenarios (format ID structure)."""
    config = config.strip()
    _set_format_ids(ctx, _format_ids_for(ctx, _FORMAT_ID_BOUNDARY_CASES.get(config, config)))


# ═══════════════════════════════════════════════════════════════════════
# Webhook configuration
# ═══════════════════════════════════════════════════════════════════════


@given("the buyer has configured a webhook for notifications")
def given_webhook_configured(ctx: dict) -> None:
    """Register push_notification_config so Then steps can verify webhook delivery.

    Stores both the config in ctx (Given→Then contract) and wires it into
    request_kwargs so production receives it when the media buy is created.
    """
    # Must pass ingest egress policy under every hatch posture (validate_url
    # runs inside _create_media_buy_impl): https public-unicast IP literal, no
    # DNS dependency. Never fetched by these scenarios.
    webhook_url = f"{UNDIALLED_PUBLIC_HTTPS_ORIGIN}/webhooks/adcp-notifications"
    # ``push-notification-config.json`` declares url, authentication, operation_id
    # and token -- and NOTHING else. There is no event selector on a task-notification
    # config; subscribing to specific events is what the account-level
    # ``notification-config`` (subscriber_id + event_types) is for. The
    # ``events: ["status_change"]`` this used to carry was accepted by the model and
    # dropped on the way out, so it selected nothing and nothing asserted on it.
    # ONE writer for both ctx keys. This used to set the config and request_kwargs
    # but not the push_notification_url mirror, while given_config's sentence set
    # the config and the mirror but not request_kwargs — so what a scenario ended
    # up holding depended on which sentence it used, and a Then reading the key its
    # sentence never wrote passed on a fallback instead of on the thing it names.
    # The mirror is gone; the config is the one spelling of the fact.
    attach_push_notification_config(ctx, webhook_url)


@given(parsers.parse('the request includes a reporting_webhook with url "{url}"'))
def given_reporting_webhook_url(ctx: dict, url: str) -> None:
    """Attach a reporting_webhook (valid auth credentials) targeting ``url``.

    Used by registration SSRF scenarios: credentials satisfy MinLen=32 so the
    request reaches the SSRF gate rather than failing Pydantic auth validation.
    """
    kwargs = _ensure_request_defaults(ctx)
    kwargs["reporting_webhook"] = valid_reporting_webhook(url)
