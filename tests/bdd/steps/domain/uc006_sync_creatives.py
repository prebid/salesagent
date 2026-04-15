"""BDD step definitions for UC-006: Sync Creatives — account resolution scenarios.

Focuses on account partition/boundary scenarios that test resolve_account()
in the sync_creatives context. The account resolution logic is shared with
UC-002 (create_media_buy) — same resolve_account(), same exceptions.

Steps dispatch through CreativeSyncEnv which exercises sync_creatives wrappers
(MCP/A2A/REST) that call enrich_identity_with_account() → resolve_account().

beads: salesagent-71q, salesagent-99w
"""

from __future__ import annotations

import json

from pytest_bdd import given, parsers, then, when

from tests.bdd.steps._harness_db import db_session
from tests.bdd.steps.generic._dispatch import dispatch_request
from tests.factories.account import AccountFactory, AgentAccountAccessFactory
from tests.factories.principal import PrincipalFactory

# ═══════════════════════════════════════════════════════════════════════
# GIVEN steps — request setup and account state
# ═══════════════════════════════════════════════════════════════════════


@given("a creative with a known format_id")
def given_creative_with_format(ctx: dict) -> None:
    """Set up a creative payload with a known format_id for sync_creatives dispatch.

    Ensures tenant/principal exist, then builds a creative payload dict matching
    the shape that _sync_creatives_impl expects (CreativeAsset-compatible dict).
    Stores the payload in ctx["creatives"] for the When step to consume.
    """
    env = ctx["env"]
    _ensure_tenant_principal(ctx, env)

    format_id = "display_300x250"
    creative_id = "creative-known-fmt-001"
    creative_payload = {
        "creative_id": creative_id,
        "name": "Test Creative with Known Format",
        "format_id": {"id": format_id, "agent_url": env.DEFAULT_AGENT_URL},
        "assets": {
            "image": {
                "url": "https://example.com/banner.png",
                "width": 300,
                "height": 250,
            },
        },
    }
    ctx.setdefault("creatives", []).append(creative_payload)
    ctx["creative_format_id"] = format_id


@given(parsers.parse("account is {account_setup}"))
def given_account_is(ctx: dict, account_setup: str) -> None:
    """Set up account state from the scenario table's JSON or sentinel value.

    Parses account_setup as JSON to build an AccountReference, or handles
    sentinel values like "not provided".
    """
    from adcp.types.generated_poc.core.account_ref import AccountReference, AccountReference1, AccountReference2
    from adcp.types.generated_poc.core.brand_ref import BrandReference

    env = ctx["env"]
    _ensure_tenant_principal(ctx, env)
    tenant, principal = ctx["tenant"], ctx["principal"]

    if account_setup == "not provided":
        ctx["account_ref"] = None
        ctx["account_absent"] = True
        return

    # Parse JSON account setup
    config = json.loads(account_setup)

    # Check for invalid oneOf: both account_id and brand present
    if "account_id" in config and "brand" in config:
        ctx["account_ref"] = None
        ctx["account_invalid_both"] = True
        return

    if "account_id" in config:
        account_id = config["account_id"]
        ctx["account_ref"] = AccountReference(root=AccountReference1(account_id=account_id))
        ctx["request_account_id"] = account_id

        # Create DB state based on known account IDs from the spec
        _setup_account_by_id(account_id, tenant, principal)

    elif "brand" in config:
        brand_domain = config["brand"]["domain"]
        operator = config["operator"]
        ctx["account_ref"] = AccountReference(
            root=AccountReference2(brand=BrandReference(domain=brand_domain), operator=operator),
        )
        ctx["request_brand"] = brand_domain
        ctx["request_operator"] = operator

        # Create DB state based on known domain patterns
        _setup_account_by_natural_key(brand_domain, operator, tenant, principal)


def _setup_account_by_id(account_id: str, tenant: object, principal: object) -> None:
    """Create DB state for account_id-based scenarios."""
    # Accounts that exist but belong to a different principal (AUTHORIZATION_ERROR)
    access_denied_ids = {"acc_other_agent"}

    status_map = {
        "acc_acme_001": "active",
        "acc_new_unconfigured": "pending_approval",
        "acc_overdue": "payment_required",
        "acc_suspended": "suspended",
    }
    status = status_map.get(account_id)

    if account_id in access_denied_ids:
        # Account exists but the test principal has no access — triggers AUTHORIZATION_ERROR
        domain = account_id.replace("_", "-") + ".com"
        other_principal = PrincipalFactory(tenant=tenant)
        account = AccountFactory(
            tenant=tenant,
            account_id=account_id,
            status="active",
            brand={"domain": domain},
            operator=domain,
        )
        AgentAccountAccessFactory(tenant_id=tenant.tenant_id, principal=other_principal, account=account)
        return

    if status is None:
        # Unknown account_id — don't create (tests not-found path)
        return

    # BrandReference domain must match ^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]...)$
    # Replace underscores with hyphens for valid domains
    domain = account_id.replace("_", "-") + ".com"
    account = AccountFactory(
        tenant=tenant,
        account_id=account_id,
        status=status,
        brand={"domain": domain},
        operator=domain,
    )
    AgentAccountAccessFactory(tenant_id=tenant.tenant_id, principal=principal, account=account)


def _setup_account_by_natural_key(brand_domain: str, operator: str, tenant: object, principal: object) -> None:
    """Create DB state for natural-key-based scenarios."""
    # Domains where the account exists but belongs to a different principal (AUTHORIZATION_ERROR)
    access_denied_domains = {"other-agent.com"}

    if brand_domain == "multi.com":
        # Ambiguous: create 3 accounts with same natural key
        for i in range(3):
            AccountFactory(
                tenant=tenant,
                account_id=f"acc-multi-{i}",
                status="active",
                brand={"domain": brand_domain},
                operator=operator,
            )
    elif brand_domain in ("unknown.com",):
        # Not found — don't create anything
        pass
    elif brand_domain in access_denied_domains:
        # Account exists but the test principal has no access — triggers AUTHORIZATION_ERROR
        other_principal = PrincipalFactory(tenant=tenant)
        account = AccountFactory(
            tenant=tenant,
            account_id=f"acc-{brand_domain.replace('.', '-')}",
            status="active",
            brand={"domain": brand_domain},
            operator=operator,
        )
        AgentAccountAccessFactory(tenant_id=tenant.tenant_id, principal=other_principal, account=account)
    else:
        # Single match — create one active account
        account = AccountFactory(
            tenant=tenant,
            account_id=f"acc-{brand_domain.replace('.', '-')}",
            status="active",
            brand={"domain": brand_domain},
            operator=operator,
        )
        AgentAccountAccessFactory(tenant_id=tenant.tenant_id, principal=principal, account=account)


# ═══════════════════════════════════════════════════════════════════════
# WHEN steps — send request
# ═══════════════════════════════════════════════════════════════════════


@when("the Buyer Agent syncs the creative")
@when("the Buyer Agent syncs the creative via the REST/A2A endpoint")
@when("the Buyer Agent syncs the creative via the MCP tool")
def when_sync_creative(ctx: dict) -> None:
    """Send sync_creatives request with account reference through transport dispatch.

    The wrappers call enrich_identity_with_account() → resolve_account(),
    exercising the full account resolution chain across all transports.

    Always dispatches — even when account_ref is None or invalid — because
    the step text says "syncs the creative". Error handling is the production
    code's responsibility, not the step's.
    """
    account_ref = ctx.get("account_ref")
    creatives = ctx.get("creatives", [])
    dispatch_request(ctx, account=account_ref, creatives=creatives)


def _ensure_tenant_principal(ctx: dict, env: object) -> None:
    """Create tenant + principal if not already created by a Given step."""
    from tests.bdd.steps.generic._account_resolution import ensure_tenant_principal

    ensure_tenant_principal(ctx, env)


# ═══════════════════════════════════════════════════════════════════════
# THEN steps — account-specific assertions
# ═══════════════════════════════════════════════════════════════════════


@then("the request should proceed with resolved account")
def then_proceed_with_resolved_account(ctx: dict) -> None:
    """Assert account resolution succeeded and the resolved account matches Given state.

    Verifies three things:
    1. The transport dispatch succeeded (no error, correct response type).
    2. The Given step provided an account reference (request_account_id or
       request_brand/request_operator exists in ctx).
    3. The account that was set up in the DB is active — confirming the
       production resolve_account() path found and validated it during
       enrich_identity_with_account().
    """
    from src.core.schemas import SyncCreativesResponse

    # 1. Response succeeded with correct type
    assert "error" not in ctx, f"Expected success but got error: {ctx.get('error')}"
    resp = ctx.get("response")
    assert resp is not None, "Expected a response (SyncCreativesResponse)"
    assert isinstance(resp, SyncCreativesResponse), f"Expected SyncCreativesResponse, got {type(resp).__name__}"

    # 2. Verify an account reference was provided by the Given step
    has_account_id = "request_account_id" in ctx
    has_natural_key = "request_brand" in ctx and "request_operator" in ctx
    assert has_account_id or has_natural_key, (
        "Then step claims 'proceed with resolved account' but no account reference "
        "was set up by a Given step (missing request_account_id and request_brand/request_operator)"
    )

    # 3. Verify the account exists and is active in the DB — proving
    #    resolve_account() found a valid, active account during dispatch
    from sqlalchemy import select

    from src.core.database.models import Account

    tenant_id = ctx["tenant"].tenant_id
    with db_session(ctx) as session:
        if has_account_id:
            account = session.scalars(
                select(Account).filter_by(tenant_id=tenant_id, account_id=ctx["request_account_id"])
            ).first()
            assert account is not None, (
                f"Account {ctx['request_account_id']} not found in DB — "
                "resolve_account() should have matched this account"
            )
            assert account.status == "active", (
                f"Account {ctx['request_account_id']} has status '{account.status}', "
                "expected 'active' for successful resolution"
            )
        else:
            # Natural key lookup — verify at least one active account with matching brand
            brand_domain = ctx["request_brand"]
            accounts = session.scalars(select(Account).filter_by(tenant_id=tenant_id)).all()
            matching = [a for a in accounts if a.brand and a.brand.domain == brand_domain and a.status == "active"]
            assert len(matching) == 1, (
                f"Expected exactly 1 active account with brand domain '{brand_domain}', "
                f"found {len(matching)} — resolve_account() requires unambiguous match"
            )


@then(parsers.parse("the error should be {error_code} with suggestion"))
def then_error_code_with_suggestion(ctx: dict, error_code: str) -> None:
    """Assert error has the expected error_code and includes a suggestion."""
    from src.core.exceptions import AdCPError

    error = ctx.get("error")
    assert error is not None, f"Expected error {error_code} but none was recorded"

    if isinstance(error, AdCPError):
        assert error.error_code == error_code, f"Expected error code '{error_code}', got '{error.error_code}'"
        assert error.details, f"Expected details with suggestion on {error_code} error"
        assert "suggestion" in error.details, f"Expected 'suggestion' in error details: {error.details}"
    else:
        raise AssertionError(f"Expected AdCPError with code {error_code}, got {type(error).__name__}: {error}")


# ═══════════════════════════════════════════════════════════════════════
# GIVEN steps — approval mode scenarios (BR-RULE-037)
# ═══════════════════════════════════════════════════════════════════════


@given(parsers.parse('a creative with name "{name}" and a known format_id'))
def given_creative_with_name_and_format(ctx: dict, name: str) -> None:
    """Set up a creative payload with a specific name and a known format_id."""
    env = ctx["env"]
    _ensure_tenant_principal(ctx, env)

    format_id = "display_300x250"
    creative_id = f"creative-{name.lower().replace(' ', '-')}-001"
    creative_payload = {
        "creative_id": creative_id,
        "name": name,
        "format_id": {"id": format_id, "agent_url": env.DEFAULT_AGENT_URL},
        "assets": {
            "image": {
                "url": "https://example.com/banner.png",
                "width": 300,
                "height": 250,
            },
        },
    }
    ctx.setdefault("creatives", []).append(creative_payload)
    ctx["creative_format_id"] = format_id


@given(parsers.parse('the tenant has approval_mode set to "{mode}"'))
def given_tenant_has_approval_mode_set_to(ctx: dict, mode: str) -> None:
    """Set approval_mode on the tenant (REST main-flow scenario)."""
    _set_tenant_approval_mode(ctx, mode)


@given(parsers.parse('the tenant has approval_mode "{mode}"'))
def given_tenant_has_approval_mode(ctx: dict, mode: str) -> None:
    """Set approval_mode on the tenant (partition scenario)."""
    _set_tenant_approval_mode(ctx, mode)


@given('the tenant has approval_mode ""')
def given_tenant_has_empty_approval_mode(ctx: dict) -> None:
    """Handle the partition 'not_set' row where mode is empty string."""
    env = ctx["env"]
    _ensure_tenant_principal(ctx, env)
    ctx["tenant"].approval_mode = "require-human"
    env._commit_factory_data()


@given(parsers.re(r"the tenant approval mode is (?P<approval_mode>.+)"))
def given_tenant_approval_mode_creative(ctx: dict, approval_mode: str) -> None:
    """Set tenant approval_mode for creative sync boundary scenarios.

    Handles creative approval modes: not configured, "auto-approve",
    "require-human", "ai-powered". Also delegates to the UC-003 step function
    for media buy modes (auto-approval, manual).
    """
    stripped = approval_mode.strip().strip('"')
    env = ctx["env"]
    _ensure_tenant_principal(ctx, env)
    tenant = ctx["tenant"]

    if stripped in ("not configured", "not set"):
        tenant.approval_mode = "require-human"
        env._commit_factory_data()
    elif stripped in ("auto-approve", "require-human", "ai-powered"):
        tenant.approval_mode = stripped
        env._commit_factory_data()
    else:
        from tests.bdd.steps.domain.uc003_update_media_buy import given_tenant_approval_mode

        given_tenant_approval_mode(ctx, approval_mode)
        return

    ctx["approval_mode_expected"] = stripped if stripped not in ("not configured", "not set") else "require-human"


def _set_tenant_approval_mode(ctx: dict, mode: str) -> None:
    """Shared helper to set approval_mode on the tenant ORM instance."""
    env = ctx["env"]
    _ensure_tenant_principal(ctx, env)
    tenant = ctx["tenant"]

    if mode in ("auto-approve", "require-human", "ai-powered"):
        tenant.approval_mode = mode
    else:
        raise ValueError(f"Unknown approval mode: {mode}")

    if mode == "require-human":
        tenant.slack_webhook_url = "https://hooks.slack.test/approval"

    env._commit_factory_data()


# ═══════════════════════════════════════════════════════════════════════
# THEN steps — approval mode assertions (BR-RULE-037)
# ═══════════════════════════════════════════════════════════════════════


def _get_creative_from_db(ctx: dict) -> object:
    """Retrieve the synced creative from the DB for status assertion."""
    from sqlalchemy import select

    from src.core.database.models import Creative

    tenant = ctx["tenant"]
    principal = ctx["principal"]
    with db_session(ctx) as session:
        creative = session.scalars(
            select(Creative).filter_by(
                tenant_id=tenant.tenant_id,
                principal_id=principal.principal_id,
            )
        ).first()
        assert creative is not None, (
            f"No creative found in DB for tenant={tenant.tenant_id}, principal={principal.principal_id}"
        )
        return creative


def _assert_workflow_steps(env: object, *, expect_present: bool) -> list:
    """Assert workflow steps exist or not, returning the steps list."""
    steps = env.get_workflow_steps()
    if expect_present:
        assert len(steps) > 0, "Expected workflow steps but none were created"
        for step in steps:
            assert step.step_type == "creative_approval", (
                f"Expected step_type 'creative_approval', got '{step.step_type}'"
            )
            assert step.owner == "publisher", f"Expected owner 'publisher', got '{step.owner}'"
            assert step.status == "requires_approval", f"Expected status 'requires_approval', got '{step.status}'"
    else:
        assert len(steps) == 0, (
            f"Expected no workflow steps, but found {len(steps)}: {[(s.step_type, s.status) for s in steps]}"
        )
    return steps


def _assert_success_response(ctx: dict) -> None:
    """Assert dispatch succeeded with no error."""
    assert "error" not in ctx, f"Expected success but got error: {ctx.get('error')}"
    assert ctx.get("response") is not None, "Expected a response but got None"


@then(parsers.parse('the creative status should be "{status}"'))
def then_creative_status_should_be(ctx: dict, status: str) -> None:
    """Assert the creative's DB status matches the expected value."""
    _assert_success_response(ctx)
    creative = _get_creative_from_db(ctx)
    assert creative.status == status, f"Expected creative status '{status}', got '{creative.status}'"


@then(parsers.parse('workflow steps created should be "{workflow}"'))
def then_workflow_steps_created(ctx: dict, workflow: str) -> None:
    """Assert whether workflow steps were created."""
    env = ctx["env"]
    if workflow == "none":
        _assert_workflow_steps(env, expect_present=False)
    elif workflow == "yes":
        _assert_workflow_steps(env, expect_present=True)
    else:
        raise ValueError(f"Unknown workflow expectation: {workflow}")


@then("the creative should use require-human as default")
def then_creative_use_require_human_default(ctx: dict) -> None:
    """Assert that when approval_mode is not configured, require-human is the default (INV-1)."""
    _assert_success_response(ctx)
    creative = _get_creative_from_db(ctx)
    assert creative.status == "pending_review", (
        f"INV-1: Default approval mode should produce 'pending_review' status, got '{creative.status}'"
    )
    _assert_workflow_steps(ctx["env"], expect_present=True)


@then("the creative status should be set to approved immediately")
def then_creative_approved_immediately(ctx: dict) -> None:
    """Assert auto-approve sets status to approved with no workflow (INV-2)."""
    _assert_success_response(ctx)
    creative = _get_creative_from_db(ctx)
    assert creative.status == "approved", (
        f"INV-2: auto-approve should set status to 'approved', got '{creative.status}'"
    )
    _assert_workflow_steps(ctx["env"], expect_present=False)


@then("a review workflow should be created with Slack notification")
def then_review_workflow_with_slack(ctx: dict) -> None:
    """Assert require-human creates workflow + sends Slack notification (INV-3)."""
    _assert_success_response(ctx)
    creative = _get_creative_from_db(ctx)
    assert creative.status == "pending_review", (
        f"INV-3: require-human should set status to 'pending_review', got '{creative.status}'"
    )
    _assert_workflow_steps(ctx["env"], expect_present=True)
    mock_notify = ctx["env"].mock.get("send_notifications")
    if mock_notify is not None:
        mock_notify.assert_called_once()


@then("a review workflow should be created with AI review")
def then_review_workflow_with_ai(ctx: dict) -> None:
    """Assert ai-powered creates workflow + submits AI review (INV-4)."""
    _assert_success_response(ctx)
    creative = _get_creative_from_db(ctx)
    assert creative.status == "pending_review", (
        f"INV-4: ai-powered should set status to 'pending_review', got '{creative.status}'"
    )
    _assert_workflow_steps(ctx["env"], expect_present=True)


@then("a workflow step should be created for the Seller")
def then_workflow_step_for_seller(ctx: dict) -> None:
    """Assert a workflow step was created with owner=publisher (the Seller)."""
    _assert_success_response(ctx)
    _assert_workflow_steps(ctx["env"], expect_present=True)
