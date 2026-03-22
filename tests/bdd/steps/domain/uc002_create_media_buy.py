"""BDD step definitions for UC-002: Create Media Buy — account resolution scenarios.

Focuses on account resolution error paths (ext-r, ext-s, ext-t, BR-RULE-080)
and partition/boundary scenarios for account_ref.

Steps delegate to MediaBuyAccountEnv which calls resolve_account() with real DB.

beads: salesagent-2rq
"""

from __future__ import annotations

from pytest_bdd import given, parsers, then, when

from tests.factories.account import AccountFactory, AgentAccountAccessFactory


def _maybe_init_request_kwargs(ctx: dict) -> None:
    """Initialize request_kwargs if env is MediaBuyCreateEnv (not account-only)."""
    from tests.harness.media_buy_create import MediaBuyCreateEnv

    env = ctx.get("env")
    if isinstance(env, MediaBuyCreateEnv):
        from tests.bdd.steps.generic.given_media_buy import _ensure_request_defaults

        _ensure_request_defaults(ctx)


# ═══════════════════════════════════════════════════════════════════════
# GIVEN steps — request setup and account state
# ═══════════════════════════════════════════════════════════════════════


@given(parsers.parse('a valid create_media_buy request with account_id "{account_id}"'))
def given_request_with_account_id(ctx: dict, account_id: str) -> None:
    """Set up a create_media_buy request referencing an explicit account_id."""
    from adcp.types.generated_poc.core.account_ref import AccountReference, AccountReference1

    ctx["account_ref"] = AccountReference(root=AccountReference1(account_id=account_id))
    ctx["request_account_id"] = account_id


@given(parsers.parse('a valid create_media_buy request with account natural key brand "{brand}" operator "{operator}"'))
def given_request_with_natural_key(ctx: dict, brand: str, operator: str) -> None:
    """Set up a create_media_buy request referencing a natural key (brand + operator)."""
    from adcp.types.generated_poc.core.account_ref import AccountReference, AccountReference2
    from adcp.types.generated_poc.core.brand_ref import BrandReference

    ctx["account_ref"] = AccountReference(
        root=AccountReference2(brand=BrandReference(domain=brand), operator=operator),
    )
    ctx["request_brand"] = brand
    ctx["request_operator"] = operator


@given("a create_media_buy request without account field")
def given_request_without_account(ctx: dict) -> None:
    """Set up a create_media_buy request with no account field."""
    ctx["account_ref"] = None
    ctx["account_absent"] = True


@given("a valid create_media_buy request with creative assignments")
def given_request_with_creative_assignments(ctx: dict) -> None:
    """Set up a create_media_buy request with creative assignments (account is implicit)."""
    ctx.setdefault("account_ref", None)
    _maybe_init_request_kwargs(ctx)


@given("a valid create_media_buy request")
def given_valid_request(ctx: dict) -> None:
    """Set up a generic valid create_media_buy request (account populated separately)."""
    ctx.setdefault("account_ref", None)
    _maybe_init_request_kwargs(ctx)


@given(parsers.parse('a valid create_media_buy request with account "{account_id}"'))
def given_request_with_account(ctx: dict, account_id: str) -> None:
    """Set up a create_media_buy request with account (short form)."""
    from adcp.types.generated_poc.core.account_ref import AccountReference, AccountReference1

    ctx["account_ref"] = AccountReference(root=AccountReference1(account_id=account_id))
    ctx["request_account_id"] = account_id
    _maybe_init_request_kwargs(ctx)
    if "request_kwargs" in ctx:
        ctx["request_kwargs"]["account"] = ctx["account_ref"]


@given("the account_id does not exist in the seller's account store")
@given("no account matches the brand + operator combination")
def given_account_not_exists(ctx: dict) -> None:
    """Ensure the referenced account does not exist in DB — no-op (default state)."""


@given(parsers.parse('the account "{account_id}" exists but requires setup (billing not configured)'))
def given_account_needs_setup(ctx: dict, account_id: str) -> None:
    """Create account with pending_approval status (setup not complete)."""
    env = ctx["env"]
    if "tenant" not in ctx:
        tenant, principal = env.setup_default_data()
        ctx["tenant"] = tenant
        ctx["principal"] = principal
    tenant, principal = ctx["tenant"], ctx["principal"]
    account = AccountFactory(
        tenant=tenant,
        account_id=account_id,
        status="pending_approval",
        brand={"domain": "setup-needed.com"},
        operator="setup-needed.com",
    )
    AgentAccountAccessFactory(tenant_id=tenant.tenant_id, principal=principal, account=account)


@given(parsers.parse("the natural key matches {count:d} accounts"))
def given_multiple_matches(ctx: dict, count: int) -> None:
    """Create multiple accounts matching the same natural key."""
    env = ctx["env"]
    if "tenant" not in ctx:
        tenant, principal = env.setup_default_data()
        ctx["tenant"] = tenant
        ctx["principal"] = principal
    else:
        tenant = ctx["tenant"]
        principal = ctx["principal"]

    brand = ctx.get("request_brand", "multi-brand.com")
    operator = ctx.get("request_operator", "agency.com")

    for i in range(count):
        account = AccountFactory(
            tenant=tenant,
            account_id=f"acc-multi-{i}",
            brand={"domain": brand},
            operator=operator,
        )
        AgentAccountAccessFactory(tenant_id=tenant.tenant_id, principal=principal, account=account)


@given(parsers.parse('the account "{account_id}" exists and is active'))
def given_account_exists_active(ctx: dict, account_id: str) -> None:
    """Create an active account with agent access."""
    env = ctx["env"]
    if "tenant" not in ctx:
        tenant, principal = env.setup_default_data()
        ctx["tenant"] = tenant
        ctx["principal"] = principal
    else:
        tenant = ctx["tenant"]
        principal = ctx["principal"]

    account = AccountFactory(
        tenant=tenant,
        account_id=account_id,
        status="active",
        brand={"domain": f"{account_id}.com"},
        operator=f"{account_id}.com",
    )
    AgentAccountAccessFactory(tenant_id=tenant.tenant_id, principal=principal, account=account)


@given("the account exists and is active")
def given_account_active(ctx: dict) -> None:
    """Create an active account for the current request context."""
    env = ctx["env"]
    if "tenant" not in ctx:
        tenant, principal = env.setup_default_data()
        ctx["tenant"] = tenant
        ctx["principal"] = principal
    else:
        tenant = ctx["tenant"]
        principal = ctx["principal"]

    account_id = ctx.get("request_account_id", "acc-001")
    account = AccountFactory(
        tenant=tenant,
        account_id=account_id,
        status="active",
        brand={"domain": f"{account_id}.com"},
        operator=f"{account_id}.com",
    )
    AgentAccountAccessFactory(tenant_id=tenant.tenant_id, principal=principal, account=account)


@given(parsers.parse("a create_media_buy request with account configuration {partition}"))
def given_request_with_partition(ctx: dict, partition: str) -> None:
    """Set up request based on partition name (for Scenario Outline tables)."""
    from adcp.types.generated_poc.core.account_ref import AccountReference, AccountReference1, AccountReference2
    from adcp.types.generated_poc.core.brand_ref import BrandReference

    env = ctx["env"]
    if "tenant" not in ctx:
        tenant, principal = env.setup_default_data()
        ctx["tenant"] = tenant
        ctx["principal"] = principal
    else:
        tenant = ctx["tenant"]
        principal = ctx["principal"]

    if partition == "explicit_account_id":
        account = AccountFactory(
            tenant=tenant,
            account_id="acc-explicit",
            status="active",
            brand={"domain": "explicit.com"},
            operator="explicit.com",
        )
        AgentAccountAccessFactory(tenant_id=tenant.tenant_id, principal=principal, account=account)
        ctx["account_ref"] = AccountReference(root=AccountReference1(account_id="acc-explicit"))

    elif partition == "natural_key_unambiguous":
        account = AccountFactory(
            tenant=tenant,
            account_id="acc-natkey",
            status="active",
            brand={"domain": "natkey.com"},
            operator="natkey.com",
        )
        AgentAccountAccessFactory(tenant_id=tenant.tenant_id, principal=principal, account=account)
        ctx["account_ref"] = AccountReference(
            root=AccountReference2(brand=BrandReference(domain="natkey.com"), operator="natkey.com"),
        )

    elif partition == "missing_account":
        ctx["account_ref"] = None
        ctx["account_absent"] = True

    elif partition == "invalid_oneOf_both":
        ctx["account_ref"] = None
        ctx["account_invalid_both"] = True

    elif partition == "explicit_not_found":
        ctx["account_ref"] = AccountReference(root=AccountReference1(account_id="acc-not-found"))

    elif partition == "natural_key_not_found":
        ctx["account_ref"] = AccountReference(
            root=AccountReference2(brand=BrandReference(domain="unknown.com"), operator="unknown.com"),
        )

    elif partition == "natural_key_ambiguous":
        for i in range(3):
            AccountFactory(
                tenant=tenant,
                account_id=f"acc-amb-{i}",
                status="active",
                brand={"domain": "ambiguous.com"},
                operator="ambiguous.com",
            )
        ctx["account_ref"] = AccountReference(
            root=AccountReference2(brand=BrandReference(domain="ambiguous.com"), operator="ambiguous.com"),
        )

    elif partition == "account_setup_required":
        account = AccountFactory(
            tenant=tenant,
            account_id="acc-setup",
            status="pending_approval",
            brand={"domain": "setup.com"},
            operator="setup.com",
        )
        AgentAccountAccessFactory(tenant_id=tenant.tenant_id, principal=principal, account=account)
        ctx["account_ref"] = AccountReference(root=AccountReference1(account_id="acc-setup"))

    elif partition == "account_payment_required":
        account = AccountFactory(
            tenant=tenant,
            account_id="acc-payment",
            status="payment_required",
            brand={"domain": "payment.com"},
            operator="payment.com",
        )
        AgentAccountAccessFactory(tenant_id=tenant.tenant_id, principal=principal, account=account)
        ctx["account_ref"] = AccountReference(root=AccountReference1(account_id="acc-payment"))

    elif partition == "account_suspended":
        account = AccountFactory(
            tenant=tenant,
            account_id="acc-suspended",
            status="suspended",
            brand={"domain": "suspended.com"},
            operator="suspended.com",
        )
        AgentAccountAccessFactory(tenant_id=tenant.tenant_id, principal=principal, account=account)
        ctx["account_ref"] = AccountReference(root=AccountReference1(account_id="acc-suspended"))

    else:
        raise ValueError(f"Unknown account partition: {partition}")


@given(parsers.parse("a create_media_buy request with account: {config}"))
def given_request_with_boundary_config(ctx: dict, config: str) -> None:
    """Set up request based on boundary config string."""
    from adcp.types.generated_poc.core.account_ref import AccountReference, AccountReference1, AccountReference2
    from adcp.types.generated_poc.core.brand_ref import BrandReference

    env = ctx["env"]
    if "tenant" not in ctx:
        tenant, principal = env.setup_default_data()
        ctx["tenant"] = tenant
        ctx["principal"] = principal
    else:
        tenant = ctx["tenant"]
        principal = ctx["principal"]

    if config.startswith("acc-") and "active" in config:
        account_id = config.split()[0]
        account = AccountFactory(
            tenant=tenant,
            account_id=account_id,
            status="active",
            brand={"domain": f"{account_id}.com"},
            operator=f"{account_id}.com",
        )
        AgentAccountAccessFactory(tenant_id=tenant.tenant_id, principal=principal, account=account)
        ctx["account_ref"] = AccountReference(root=AccountReference1(account_id=account_id))

    elif config.startswith("acc-") and "not-found" in config:
        account_id = config.split()[0]
        ctx["account_ref"] = AccountReference(root=AccountReference1(account_id=account_id))

    elif config.startswith("brand+op") and "single match" in config:
        account = AccountFactory(
            tenant=tenant,
            account_id="acc-brand-single",
            status="active",
            brand={"domain": "single.com"},
            operator="single.com",
        )
        AgentAccountAccessFactory(tenant_id=tenant.tenant_id, principal=principal, account=account)
        ctx["account_ref"] = AccountReference(
            root=AccountReference2(brand=BrandReference(domain="single.com"), operator="single.com"),
        )

    elif config.startswith("brand+op") and "no match" in config:
        ctx["account_ref"] = AccountReference(
            root=AccountReference2(brand=BrandReference(domain="nomatch.com"), operator="nomatch.com"),
        )

    elif config.startswith("brand+op") and "multi match" in config:
        for i in range(2):
            AccountFactory(
                tenant=tenant,
                account_id=f"acc-multi-{i}",
                status="active",
                brand={"domain": "multi.com"},
                operator="multi.com",
            )
        ctx["account_ref"] = AccountReference(
            root=AccountReference2(brand=BrandReference(domain="multi.com"), operator="multi.com"),
        )

    elif "setup-needed" in config:
        account = AccountFactory(
            tenant=tenant,
            account_id="acc-setup",
            status="pending_approval",
            brand={"domain": "setup.com"},
            operator="setup.com",
        )
        AgentAccountAccessFactory(tenant_id=tenant.tenant_id, principal=principal, account=account)
        ctx["account_ref"] = AccountReference(root=AccountReference1(account_id="acc-setup"))

    elif "payment-due" in config:
        account = AccountFactory(
            tenant=tenant,
            account_id="acc-payment",
            status="payment_required",
            brand={"domain": "payment.com"},
            operator="payment.com",
        )
        AgentAccountAccessFactory(tenant_id=tenant.tenant_id, principal=principal, account=account)
        ctx["account_ref"] = AccountReference(root=AccountReference1(account_id="acc-payment"))

    elif "suspended" in config:
        account = AccountFactory(
            tenant=tenant,
            account_id="acc-suspended",
            status="suspended",
            brand={"domain": "suspended.com"},
            operator="suspended.com",
        )
        AgentAccountAccessFactory(tenant_id=tenant.tenant_id, principal=principal, account=account)
        ctx["account_ref"] = AccountReference(root=AccountReference1(account_id="acc-suspended"))

    elif "no account" in config:
        ctx["account_ref"] = None
        ctx["account_absent"] = True

    elif "both fields" in config:
        ctx["account_ref"] = None
        ctx["account_invalid_both"] = True

    else:
        raise ValueError(f"Unknown boundary config: {config}")


# ═══════════════════════════════════════════════════════════════════════
# WHEN steps — send request
# ═══════════════════════════════════════════════════════════════════════


@when("the Buyer Agent sends the create_media_buy request")
def when_send_create_media_buy(ctx: dict) -> None:
    """Send the create_media_buy request and capture the result or error.

    Two dispatch paths:
    - MediaBuyAccountEnv: account resolution only (resolve_account_or_error)
    - MediaBuyCreateEnv: full create_media_buy through all transports
    """
    from tests.harness.media_buy_create import MediaBuyCreateEnv

    env = ctx["env"]
    if isinstance(env, MediaBuyCreateEnv):
        _dispatch_create_media_buy(ctx)
    else:
        from tests.bdd.steps.generic._account_resolution import resolve_account_or_error

        resolve_account_or_error(ctx)


def _dispatch_create_media_buy(ctx: dict) -> None:
    """Build CreateMediaBuyRequest from ctx and dispatch through harness."""
    from tests.bdd.steps.generic._dispatch import dispatch_request

    request_kwargs = ctx.get("request_kwargs", {})

    # Build the request object
    from src.core.schemas import CreateMediaBuyRequest

    req = CreateMediaBuyRequest(**request_kwargs)

    # Check for no-auth scenario
    if ctx.get("has_auth") is False:
        dispatch_request(ctx, req=req, identity=None)
    else:
        dispatch_request(ctx, req=req)

    # Post-process: CreateMediaBuyResult wraps errors in response, not as exceptions.
    # Promote error results to ctx["error"] so generic Then steps can find them.
    resp = ctx.get("response")
    if resp is not None and hasattr(resp, "status") and hasattr(resp, "response"):
        from src.core.schemas._base import CreateMediaBuyError as CMBError

        if isinstance(resp.response, CMBError) and resp.response.errors:
            # Promote first error from the errors list — it has .code, .message, .recovery
            ctx["error"] = resp.response.errors[0]
            ctx["error_response"] = resp.response  # Keep full error response for multi-error checks
            del ctx["response"]
        elif resp.status == "failed":
            ctx["error"] = resp
            del ctx["response"]


def _ensure_tenant_principal(ctx: dict, env: object) -> None:
    """Create tenant + principal if not already created by a Given step."""
    from tests.bdd.steps.generic._account_resolution import ensure_tenant_principal

    ensure_tenant_principal(ctx, env)


# ═══════════════════════════════════════════════════════════════════════
# THEN steps — account-specific assertions
# ═══════════════════════════════════════════════════════════════════════


@then(parsers.parse('the error should include "details" with setup instructions'))
def then_error_has_setup_details(ctx: dict) -> None:
    """Assert error details include setup instructions."""
    error = ctx.get("error")
    assert error is not None, "No error recorded in ctx"
    from src.core.exceptions import AdCPError

    if isinstance(error, AdCPError):
        assert error.details, f"Expected details on error: {error}"
        details_str = str(error.details).lower()
        assert "setup" in details_str or "billing" in details_str or "configure" in details_str, (
            f"Expected setup instructions in details: {error.details}"
        )
    else:
        raise AssertionError(f"Cannot check details on non-AdCPError: {type(error).__name__}")


@then(parsers.parse('the error message should contain "{count} accounts"'))
def then_error_contains_count(ctx: dict, count: str) -> None:
    """Assert error message mentions the specific number of matching accounts."""
    error = ctx.get("error")
    assert error is not None, "No error recorded in ctx"
    msg = str(error)
    assert f"{count} account" in msg.lower() or f"{count}" in msg, f"Expected '{count} accounts' in error: {msg}"


@then(parsers.parse("the result should be {outcome}"))
def then_result_should_be(ctx: dict, outcome: str) -> None:
    """Assert outcome of a partition/boundary scenario."""
    if outcome.startswith("account resolution succeeds"):
        assert "error" not in ctx, f"Expected success but got error: {ctx.get('error')}"
        assert "resolved_account_id" in ctx, "Expected resolved_account_id in ctx"
    elif outcome.startswith("error "):
        assert "error" in ctx, f"Expected an error for outcome: {outcome}"
        from src.core.exceptions import AdCPError

        error = ctx["error"]
        # Parse expected: "error CODE recovery_hint" or "error CODE with suggestion"
        parts = outcome[6:].strip().split()
        expected_code = parts[0]
        if isinstance(error, AdCPError):
            assert error.error_code == expected_code, f"Expected error code '{expected_code}', got '{error.error_code}'"
        # Check recovery hint if specified
        if len(parts) >= 2 and parts[1] in ("terminal", "correctable", "transient"):
            if isinstance(error, AdCPError):
                assert error.recovery == parts[1], f"Expected recovery '{parts[1]}', got '{error.recovery}'"
        # Check "with suggestion" if specified
        if "with suggestion" in outcome.lower() or "with" in parts:
            if isinstance(error, AdCPError) and error.details:
                assert "suggestion" in error.details, f"Expected suggestion in details: {error.details}"
    else:
        raise ValueError(f"Unknown outcome: {outcome}")


# ═══════════════════════════════════════════════════════════════════════
# WHEN steps — seller actions (admin-side, not transport-dispatched)
# ═══════════════════════════════════════════════════════════════════════


@when(parsers.parse('the Seller rejects the media buy with reason "{reason}"'))
def when_seller_rejects_media_buy(ctx: dict, reason: str) -> None:
    """Simulate seller rejecting a pending media buy (admin action).

    Updates the media buy status to 'rejected' and stores the rejection reason
    on the associated workflow step, mirroring the production admin flow
    in operations.py:approve_media_buy(action='reject').
    """
    from datetime import UTC, datetime

    from sqlalchemy import select

    from src.core.database.database_session import get_db_session
    from src.core.database.models import MediaBuy, ObjectWorkflowMapping, WorkflowStep

    env = ctx["env"]
    env._commit_factory_data()

    media_buy = ctx["existing_media_buy"]
    media_buy_id = media_buy.media_buy_id

    with get_db_session() as session:
        # Update media buy status
        mb = session.scalars(select(MediaBuy).filter_by(media_buy_id=media_buy_id)).first()
        assert mb is not None, f"Media buy {media_buy_id} not found in DB"
        mb.status = "rejected"

        # Find or create workflow step to store rejection reason
        mapping = session.scalars(select(ObjectWorkflowMapping).filter_by(object_id=media_buy_id)).first()
        if mapping:
            step = session.scalars(select(WorkflowStep).filter_by(step_id=mapping.step_id)).first()
            if step:
                step.status = "rejected"
                step.error_message = reason
                step.updated_at = datetime.now(UTC)

        session.commit()

    # Store rejection_reason on existing_media_buy so Then steps can find it.
    # MediaBuy model lacks a rejection_reason column — we set it dynamically.
    media_buy.rejection_reason = reason  # type: ignore[attr-defined]


# ═══════════════════════════════════════════════════════════════════════
# GIVEN steps — creative validation error injection (ext-o, ext-p, ext-q)
# ═══════════════════════════════════════════════════════════════════════


@given(parsers.parse('a package creative_assignment references creative_id "{creative_id}"'))
@given(parsers.parse('But a package creative_assignment references creative_id "{creative_id}"'))
def given_package_references_creative_id(ctx: dict, creative_id: str) -> None:
    """Add creative_id to the first package's creative_ids list.

    For ext-o: the creative_id won't exist in DB, triggering CREATIVES_NOT_FOUND.
    """
    from tests.bdd.steps.generic.given_media_buy import _ensure_request_defaults

    kwargs = _ensure_request_defaults(ctx)
    if kwargs.get("packages"):
        pkg = kwargs["packages"][0]
        existing = pkg.get("creative_ids") or []
        existing.append(creative_id)
        pkg["creative_ids"] = existing


@given("a creative's format_id does not match any of the product's supported format_ids")
@given("But a creative's format_id does not match any of the product's supported format_ids")
def given_creative_format_mismatch(ctx: dict) -> None:
    """Create a creative with a format that doesn't match the product's format_ids.

    For ext-p: creates a creative with format "video_640x480" while the product
    only supports "display_300x250", triggering CREATIVE_FORMAT_MISMATCH.
    """
    from tests.bdd.steps.generic.given_media_buy import _ensure_request_defaults
    from tests.factories.creative import CreativeFactory
    from tests.helpers.adcp_factories import create_test_format

    env = ctx["env"]
    kwargs = _ensure_request_defaults(ctx)
    # Use a display format (not video) to avoid Assets/Assets5 discriminated union
    # bug in extract_media_url_and_dimensions. display_728x90 is a valid display format
    # but not in the product's accepted formats (display_300x250).
    creative = CreativeFactory(
        tenant=ctx["tenant"],
        principal=ctx["principal"],
        creative_id="cr-mismatched-format",
        format="display_728x90",  # Mismatched — product accepts display_300x250
        status="approved",
        data={
            "assets": {
                "primary": {
                    "url": "https://cdn.example.com/leaderboard.png",
                    "width": 728,
                    "height": 90,
                }
            }
        },
    )
    env._commit_factory_data()
    # Register the mismatched format spec so pre-validation recognizes it
    # (without this, _get_format_spec_sync returns None → "unknown format" error
    # instead of the specific CREATIVE_FORMAT_MISMATCH we want to test)
    env._format_specs["display_728x90"] = create_test_format(
        format_id="display_728x90",
        name="Display 728x90 Leaderboard",
        type="display",
    )
    # Add the creative_id to the first package
    if kwargs.get("packages"):
        pkg = kwargs["packages"][0]
        existing = pkg.get("creative_ids") or []
        existing.append(creative.creative_id)
        pkg["creative_ids"] = existing


@given("a valid create_media_buy request with inline creatives that passes all validation")
def given_request_with_inline_creatives(ctx: dict) -> None:
    """Set up a request with creative_ids referencing existing approved creatives.

    For ext-q: creatives exist and format-match the product, so validation passes.
    The upload step (adapter.add_creative_assets) is where failure will be injected.
    Creative data must include proper assets (matching format spec's asset_id="primary")
    with URL and dimensions so pre-validation passes.
    """
    from tests.bdd.steps.generic.given_media_buy import _ensure_request_defaults
    from tests.factories.creative import CreativeFactory

    env = ctx["env"]
    kwargs = _ensure_request_defaults(ctx)
    # Create a creative that matches the product's format_ids with proper asset data
    creative = CreativeFactory(
        tenant=ctx["tenant"],
        principal=ctx["principal"],
        creative_id="cr-upload-test",
        agent_url="https://creative.adcontextprotocol.org",
        format="display_300x250",  # Matches product format
        status="approved",
        data={
            "assets": {
                "primary": {
                    "url": "https://cdn.example.com/creative.png",
                    "width": 300,
                    "height": 250,
                }
            }
        },
    )
    env._commit_factory_data()
    if kwargs.get("packages"):
        pkg = kwargs["packages"][0]
        existing = pkg.get("creative_ids") or []
        existing.append(creative.creative_id)
        pkg["creative_ids"] = existing


@given("the ad server rejects the creative upload")
@given("But the ad server rejects the creative upload")
def given_ad_server_rejects_upload(ctx: dict) -> None:
    """Configure the mock adapter to fail on creative upload.

    For ext-q: adapter.add_creative_assets raises a non-AdCPError exception,
    which production code catches and wraps as CREATIVE_UPLOAD_FAILED.
    """
    env = ctx["env"]
    mock_adapter = env.mock["adapter"].return_value
    mock_adapter.add_creative_assets.side_effect = Exception("Ad server rejected creative: invalid asset dimensions")


# ═══════════════════════════════════════════════════════════════════════
# GIVEN steps — optimization goal error injection (ext-u, ext-u-event)
# ═══════════════════════════════════════════════════════════════════════


@given(parsers.parse('a package has optimization_goal with kind "{kind}" and metric "{metric}" not in supported set'))
@given(
    parsers.parse('But a package has optimization_goal with kind "{kind}" and metric "{metric}" not in supported set')
)
def given_unsupported_optimization_metric(ctx: dict, kind: str, metric: str) -> None:
    """Add an optimization_goal with an unsupported metric to the first package.

    SPEC-PRODUCTION GAP: optimization_goals is not in adcp v3.6.0 or production
    schemas. PackageRequest(extra='forbid') will reject this field with a generic
    Pydantic validation error, not the spec-expected UNSUPPORTED_FEATURE.
    """
    from tests.bdd.steps.generic.given_media_buy import _ensure_request_defaults

    kwargs = _ensure_request_defaults(ctx)
    if kwargs.get("packages"):
        kwargs["packages"][0]["optimization_goals"] = [{"kind": kind, "metric": metric, "priority": 1}]


@given('a package has optimization_goal with kind "event" and unregistered event_source_id')
@given('But a package has optimization_goal with kind "event" and unregistered event_source_id')
def given_unregistered_event_source(ctx: dict) -> None:
    """Add an optimization_goal with an unregistered event_source_id to the first package.

    SPEC-PRODUCTION GAP: optimization_goals is not in adcp v3.6.0 or production
    schemas. PackageRequest(extra='forbid') will reject this field with a generic
    Pydantic validation error, not the spec-expected INVALID_REQUEST.
    """
    from tests.bdd.steps.generic.given_media_buy import _ensure_request_defaults

    kwargs = _ensure_request_defaults(ctx)
    if kwargs.get("packages"):
        kwargs["packages"][0]["optimization_goals"] = [
            {"kind": "event", "event_source_id": "evt-unregistered-999", "priority": 1}
        ]


# ═══════════════════════════════════════════════════════════════════════
# GIVEN steps — catalog validation error injection (ext-v, ext-v-notfound)
# ═══════════════════════════════════════════════════════════════════════


@given(parsers.parse('a package has two catalogs both with type "{catalog_type}"'))
@given(parsers.parse('But a package has two catalogs both with type "{catalog_type}"'))
def given_duplicate_catalog_types(ctx: dict, catalog_type: str) -> None:
    """Add two catalogs with the same type to the first package.

    SPEC-PRODUCTION GAP: Production code accepts catalogs (field is in adcp
    library PackageRequest) but never validates duplicate types. The request
    succeeds silently instead of returning INVALID_REQUEST.
    """
    from tests.bdd.steps.generic.given_media_buy import _ensure_request_defaults

    kwargs = _ensure_request_defaults(ctx)
    if kwargs.get("packages"):
        kwargs["packages"][0]["catalogs"] = [
            {"type": catalog_type, "url": "https://example.com/feed-a.xml"},
            {"type": catalog_type, "url": "https://example.com/feed-b.xml"},
        ]


@given(parsers.parse('a package references catalog_id "{catalog_id}" not found in synced catalogs'))
@given(parsers.parse('But a package references catalog_id "{catalog_id}" not found in synced catalogs'))
def given_catalog_id_not_found(ctx: dict, catalog_id: str) -> None:
    """Add a catalog with a nonexistent catalog_id to the first package.

    SPEC-PRODUCTION GAP: Production code accepts catalogs (field is in adcp
    library PackageRequest) but never validates catalog_id existence against
    synced catalogs. The request succeeds silently instead of returning
    INVALID_REQUEST.
    """
    from tests.bdd.steps.generic.given_media_buy import _ensure_request_defaults

    kwargs = _ensure_request_defaults(ctx)
    if kwargs.get("packages"):
        kwargs["packages"][0]["catalogs"] = [
            {"type": "product", "catalog_id": catalog_id, "url": "https://example.com/feed.xml"},
        ]


# ═══════════════════════════════════════════════════════════════════════
# GIVEN steps — inline creatives (alt-creatives scenario)
# ═══════════════════════════════════════════════════════════════════════


@given('the request includes packages with inline "creatives" array')
def given_request_with_inline_creatives_array(ctx: dict) -> None:
    """Build inline CreativeAsset objects on the first package's creatives field.

    Creates a CreativeAsset with valid format_id, name, and assets.
    Uses display_300x250_image which matches the creative agent registry's
    mock format list (ADCP_TESTING=true).

    Also patches the creative agent registry so _sync_creatives_impl doesn't
    make real HTTP calls to the creative agent (preview_creative). This is the
    same pattern used by CreativeSyncEnv.
    """
    from unittest.mock import AsyncMock, MagicMock, patch

    from tests.bdd.steps.generic.given_media_buy import _ensure_request_defaults
    from tests.helpers.adcp_factories import create_test_format

    env = ctx["env"]
    kwargs = _ensure_request_defaults(ctx)
    # Register format spec so _get_format_spec_sync mock recognizes it.
    env._format_specs["display_300x250_image"] = create_test_format(
        format_id="display_300x250_image",
        name="Medium Rectangle",
        type="display",
    )
    # Patch creative agent registry for inline creative processing.
    # _sync_creatives_impl calls preview_creative on the external agent;
    # we mock it to avoid real HTTP calls (same pattern as CreativeSyncEnv).
    mock_registry = MagicMock()
    mock_registry.list_all_formats.return_value = []
    mock_registry.get_format = AsyncMock(return_value={"id": "display_300x250_image", "name": "Medium Rectangle"})
    mock_registry.preview_creative = AsyncMock(return_value={})
    mock_registry.build_creative = AsyncMock(return_value={})
    registry_patcher = patch(
        "src.core.creative_agent_registry.get_creative_agent_registry",
        return_value=mock_registry,
    )
    registry_patcher.start()
    env._patchers.append(registry_patcher)
    # Also patch run_async_in_sync_context used by _sync for format validation
    run_async_patcher = patch(
        "src.core.tools.creatives._sync.run_async_in_sync_context",
        side_effect=lambda coro: [],
    )
    run_async_patcher.start()
    env._patchers.append(run_async_patcher)

    # Update product format_ids to match the mock format registry's ID
    # (display_300x250_image instead of display_300x250)
    product = ctx.get("default_product")
    if product:
        product.format_ids = [
            {"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250_image"},
        ]
        env._commit_factory_data()

    if kwargs.get("packages"):
        kwargs["packages"][0]["creatives"] = [
            {
                "creative_id": "cr-inline-001",
                "name": "Inline Banner 300x250",
                "format_id": {
                    "agent_url": "https://creative.adcontextprotocol.org",
                    "id": "display_300x250_image",
                },
                "assets": {
                    "primary": {
                        "url": "https://cdn.example.com/inline-banner.png",
                        "width": 300,
                        "height": 250,
                    }
                },
            }
        ]


@given("each creative has a valid format_id, name, and assets with URL and dimensions")
def given_creatives_have_valid_fields(ctx: dict) -> None:
    """Assert the inline creatives already have required fields.

    The previous Given step builds valid CreativeAssets — this step confirms
    the precondition without modifying state.
    """
    kwargs = ctx.get("request_kwargs", {})
    packages = kwargs.get("packages", [])
    for pkg in packages:
        for creative in pkg.get("creatives", []):
            assert "format_id" in creative, "Creative missing format_id"
            assert "name" in creative, "Creative missing name"
            assert "assets" in creative, "Creative missing assets"
            for asset in creative["assets"].values():
                if isinstance(asset, dict):
                    assert "url" in asset, f"Asset missing url: {asset}"


@given("the creative agent has the referenced formats registered")
def given_creative_agent_formats_registered(ctx: dict) -> None:
    """Ensure the harness format_specs registry has entries for all referenced formats.

    The display_300x250 format is registered by the 'inline creatives array'
    Given step. This step verifies it's present.
    """
    env = ctx["env"]
    kwargs = ctx.get("request_kwargs", {})
    for pkg in kwargs.get("packages", []):
        for creative in pkg.get("creatives", []):
            fmt_id = creative.get("format_id", {})
            fid = fmt_id.get("id") if isinstance(fmt_id, dict) else None
            if fid:
                assert fid in env._format_specs, (
                    f"Format {fid} not registered in harness — available: {list(env._format_specs.keys())}"
                )


# ═══════════════════════════════════════════════════════════════════════
# THEN steps — inline creatives assertions (alt-creatives scenario)
# ═══════════════════════════════════════════════════════════════════════


@then("the system should upload the creatives to the creative library")
def then_creatives_uploaded_to_library(ctx: dict) -> None:
    """Assert inline creatives were synced to the DB creative library.

    Production calls process_and_upload_package_creatives → _sync_creatives_impl
    which persists creatives to the database. Verify by checking the DB.
    """
    from sqlalchemy import func, select

    from src.core.database.database_session import get_db_session
    from src.core.database.models import Creative as CreativeModel

    tenant = ctx["tenant"]
    with get_db_session() as session:
        count = session.scalar(select(func.count()).select_from(CreativeModel).filter_by(tenant_id=tenant.tenant_id))
        assert count is not None and count > 0, f"Expected creatives in DB for tenant {tenant.tenant_id}, found {count}"


@then("the system should assign the uploaded creatives to packages")
def then_creatives_assigned_to_packages(ctx: dict) -> None:
    """Assert creative assignments exist for the created media buy packages.

    Production creates CreativeAssignment records linking creatives to packages.
    """
    from sqlalchemy import select

    from src.core.database.database_session import get_db_session
    from src.core.database.models import CreativeAssignment

    resp = ctx.get("response")
    assert resp is not None, "Expected a response"
    media_buy_id = _get_response_field_from_resp(resp, "media_buy_id")
    assert media_buy_id, "No media_buy_id in response"

    with get_db_session() as session:
        assignments = session.scalars(select(CreativeAssignment).filter_by(media_buy_id=media_buy_id)).all()
        assert len(assignments) > 0, f"Expected creative assignments for media_buy {media_buy_id}, found none"


@then("the response should include the created media buy with creative assignments")
def then_response_has_creative_assignments(ctx: dict) -> None:
    """Assert the response includes a created media buy.

    The response should be a success with a media_buy_id. Creative assignments
    are verified by the preceding Then steps (DB check).
    """
    resp = ctx.get("response")
    assert resp is not None, "Expected a response"
    media_buy_id = _get_response_field_from_resp(resp, "media_buy_id")
    assert media_buy_id, "No media_buy_id in response — media buy not created"
    status = _get_response_field_from_resp(resp, "status")
    # Inline creative uploads trigger pending_activation (creatives synced but
    # not yet approved by creative agent) or completed (auto-approved tenant).
    assert status in ("pending_activation", "completed", "activating"), (
        f"Expected activation-related status, got '{status}'"
    )


# ═══════════════════════════════════════════════════════════════════════
# GIVEN steps — proposal-based creation (alt-proposal scenario)
# ═══════════════════════════════════════════════════════════════════════


@given(parsers.parse('proposal "{proposal_id}" exists and has not expired'))
def given_proposal_exists(ctx: dict, proposal_id: str) -> None:
    """Record that a proposal exists (spec-production gap: no proposal store).

    SPEC-PRODUCTION GAP: Production has no proposal store. proposal_id is
    accepted on CreateMediaBuyRequest (from adcp library) but never validated.
    This step records the expected proposal for Then-step assertions.
    """
    ctx["expected_proposal_id"] = proposal_id


@given(parsers.parse("the proposal has {count:d} product allocations"))
def given_proposal_allocations(ctx: dict, count: int) -> None:
    """Record expected proposal allocations (spec-production gap).

    SPEC-PRODUCTION GAP: Production has no proposal allocation mechanism.
    This step records the expected allocation count for Then-step assertions.
    """
    ctx["expected_proposal_allocations"] = count


# ═══════════════════════════════════════════════════════════════════════
# THEN steps — proposal-based creation assertions (alt-proposal scenario)
# ═══════════════════════════════════════════════════════════════════════


@then("the system should derive packages from proposal allocations")
def then_packages_derived_from_proposal(ctx: dict) -> None:
    """Assert packages were derived from proposal allocations.

    SPEC-PRODUCTION GAP: Production does not derive packages from proposals.
    proposal_id is accepted but never processed.
    """
    resp = ctx.get("response")
    assert resp is not None, "Expected a response"
    packages = _get_response_field_from_resp(resp, "packages")
    expected = ctx.get("expected_proposal_allocations", 0)
    assert packages is not None and len(packages) == expected, (
        f"Expected {expected} packages derived from proposal, got {len(packages) if packages else 0}"
    )


@then("the total_budget should be distributed per allocation percentages")
def then_budget_distributed_per_allocations(ctx: dict) -> None:
    """Assert total budget was distributed across packages.

    SPEC-PRODUCTION GAP: Production does not distribute budget per proposal
    allocations. Packages retain their individual budgets as submitted.
    """
    resp = ctx.get("response")
    assert resp is not None, "Expected a response"
    packages = _get_response_field_from_resp(resp, "packages")
    assert packages is not None and len(packages) > 0, "No packages in response"
    # Verify budget sum matches total_budget from request
    kwargs = ctx.get("request_kwargs", {})
    total_budget = kwargs.get("total_budget", {})
    if isinstance(total_budget, dict):
        expected_total = total_budget.get("amount", 0)
    else:
        expected_total = getattr(total_budget, "amount", None)
        assert expected_total is not None, f"Cannot extract amount from total_budget: {total_budget!r}"
    budget_sum = sum((p.get("budget", 0) if isinstance(p, dict) else getattr(p, "budget", 0) or 0) for p in packages)
    assert abs(budget_sum - expected_total) < 0.01, f"Expected budget sum {expected_total}, got {budget_sum}"


@then("the response should include the created media buy with derived packages")
def then_response_has_derived_packages(ctx: dict) -> None:
    """Assert response has a media buy with packages from proposal.

    SPEC-PRODUCTION GAP: Production does not derive packages from proposals.
    This asserts the response has a media_buy_id and packages array.
    """
    resp = ctx.get("response")
    assert resp is not None, "Expected a response"
    media_buy_id = _get_response_field_from_resp(resp, "media_buy_id")
    assert media_buy_id, "No media_buy_id in response"
    packages = _get_response_field_from_resp(resp, "packages")
    assert packages is not None and len(packages) > 0, "No derived packages in response"


# ═══════════════════════════════════════════════════════════════════════
# Helpers (local to this module)
# ═══════════════════════════════════════════════════════════════════════


def _get_response_field_from_resp(resp: object, field: str) -> object:
    """Extract a field from a response, handling wrapper types.

    Delegates to the shared helper in then_media_buy to avoid duplication.
    """
    from tests.bdd.steps.generic.then_media_buy import _get_response_field

    return _get_response_field(resp, field)
