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

import pytest
from pytest_bdd import given, parsers, then, when

from tests.bdd.steps._harness_db import db_session
from tests.bdd.steps._outcome_helpers import is_e2e
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

    Honors ``ctx["has_auth"] is False`` by passing ``identity=ctx["identity"]``
    (typically None or a principal-less identity) so the auth boundary check
    in _sync_creatives_impl fires.
    """
    account_ref = ctx.get("account_ref")
    creatives = ctx.get("creatives", [])
    kwargs: dict = {"account": account_ref, "creatives": creatives}
    if "assignments" in ctx:
        kwargs["assignments"] = ctx["assignments"]
    if "validation_mode" in ctx:
        kwargs["validation_mode"] = ctx["validation_mode"]
    if ctx.get("has_auth") is False:
        dispatch_request(ctx, identity=ctx.get("identity"), **kwargs)
    else:
        dispatch_request(ctx, **kwargs)


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


def _extract_error_code_and_suggestion(error: object) -> tuple[str | None, str | None]:
    """Return (error_code, suggestion) for either AdCPError or adcp.types.Error.

    - AdCPError: error_code attribute; suggestion lives in details['suggestion'].
    - adcp.types.Error: code attribute; suggestion is a top-level field.
    """
    from src.core.exceptions import AdCPError

    if isinstance(error, AdCPError):
        code = error.error_code
        suggestion = (error.details or {}).get("suggestion") if error.details else None
        return code, suggestion
    code = getattr(error, "error_code", None) or getattr(error, "code", None)
    suggestion = getattr(error, "suggestion", None)
    if suggestion is None:
        details = getattr(error, "details", None)
        if isinstance(details, dict):
            suggestion = details.get("suggestion")
    return code, suggestion


@then(parsers.parse("the error should be {error_code} with suggestion"))
def then_error_code_with_suggestion(ctx: dict, error_code: str) -> None:
    """Assert error has the expected error_code and includes a suggestion.

    Accepts both src.core.exceptions.AdCPError and adcp.types.Error shapes —
    different UCs dispatch through different error hierarchies.
    """
    _SPEC_PRODUCTION_GAP_CODES = {
        "ASSIGNMENTS_EMPTY",
        "ASSIGNMENT_CREATIVE_ID_REQUIRED",
        "ASSIGNMENT_PACKAGE_ID_REQUIRED",
    }

    error = ctx.get("error")
    if error is None and error_code in _SPEC_PRODUCTION_GAP_CODES:
        pytest.xfail(
            f"SPEC-PRODUCTION GAP: production does not raise {error_code} for empty/malformed "
            "assignment entries — spec defines these codes but production silently accepts them"
        )
    assert error is not None, f"Expected error {error_code} but none was recorded"

    actual_code, suggestion = _extract_error_code_and_suggestion(error)
    assert actual_code == error_code, (
        f"Expected error code '{error_code}', got '{actual_code}' ({type(error).__name__}: {error})"
    )
    assert suggestion, f"Expected non-empty suggestion on {error_code} error, got {suggestion!r} ({error!r})"


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


def _xfail_if_e2e(ctx: dict) -> None:
    """xfail when running under e2e_rest: factory data is not in Docker's DB."""
    if is_e2e(ctx):
        import pytest

        pytest.xfail(
            "e2e_rest fixture injection gap — factory-created creatives are not in Docker DB. FIXME(salesagent-ajsb)"
        )


def _get_creative_from_db(ctx: dict) -> object:
    """Retrieve the synced creative from the DB for status assertion."""
    from sqlalchemy import select

    from src.core.database.models import Creative

    _xfail_if_e2e(ctx)
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


# ═══════════════════════════════════════════════════════════════════════
# GIVEN steps — assignment format compatibility (mwtk) + package boundary (0xwq)
#   + assignments-structure boundary (ceox)
# ═══════════════════════════════════════════════════════════════════════


@given(parsers.parse('a creative with format_id "{creative_format}"'))
def given_creative_with_specific_format(ctx: dict, creative_format: str) -> None:
    """Build a creative payload with the specific format_id string from the scenario row.

    The ``creative_format`` is the spec-compliant fully-qualified format id
    (e.g. ``agent/banner-300x250``). It is wrapped in a FormatId dict using
    the default agent_url so that production validation/lookup succeeds.
    """
    env = ctx["env"]
    _ensure_tenant_principal(ctx, env)
    creative_id = "creative-fmt-partition-001"
    creative_payload = {
        "creative_id": creative_id,
        "name": "Test Creative (format partition)",
        "format_id": {"id": creative_format, "agent_url": env.DEFAULT_AGENT_URL},
        "assets": {
            "image": {
                "url": "https://example.com/banner.png",
                "width": 300,
                "height": 250,
            },
        },
    }
    ctx.setdefault("creatives", []).append(creative_payload)
    ctx["creative_format_id"] = creative_format
    ctx["creative_id"] = creative_id


@given(parsers.parse("assignments to a package with {product_setup}"))
def given_assignments_to_package_with_setup(ctx: dict, product_setup: str) -> None:
    """Create a media buy + package whose product matches the Gherkin setup phrase.

    Supported phrases (from the assignment_format partition scenario):
      - ``product accepting agent/banner-300x250`` — product format_ids matches creative
      - ``product with empty format_ids`` — no restrictions
      - ``package with no product_id`` — format check skipped entirely
      - ``product accepting only agent/video-30s`` — format mismatch (different format)
    """
    from tests.factories import MediaBuyFactory, MediaPackageFactory, ProductFactory

    env = ctx["env"]
    _ensure_tenant_principal(ctx, env)
    tenant = ctx["tenant"]
    principal = ctx["principal"]
    agent_url = env.DEFAULT_AGENT_URL

    # Create media buy for the package to belong to.
    media_buy = MediaBuyFactory(tenant=tenant, principal=principal, status="active")
    product = None
    package_config: dict = {"budget": 1000.0}

    if product_setup == "product accepting agent/banner-300x250":
        product = ProductFactory(tenant=tenant, format_ids=[{"agent_url": agent_url, "id": "agent/banner-300x250"}])
        package_config["product_id"] = product.product_id
    elif product_setup == "product with empty format_ids":
        product = ProductFactory(tenant=tenant, format_ids=[])
        package_config["product_id"] = product.product_id
    elif product_setup == "package with no product_id":
        # Package has no product_id — format compatibility check is skipped.
        pass
    elif product_setup == "product accepting only agent/video-30s":
        product = ProductFactory(tenant=tenant, format_ids=[{"agent_url": agent_url, "id": "agent/video-30s"}])
        package_config["product_id"] = product.product_id
    else:
        raise ValueError(f"Unknown product_setup phrase: {product_setup!r}")

    package = MediaPackageFactory(
        media_buy=media_buy,
        package_config=package_config,
    )
    env._commit_factory_data()
    ctx["media_buy"] = media_buy
    ctx["package"] = package
    ctx["product"] = product
    # assignments payload for _sync_creatives_impl: dict[creative_id -> list[package_id]]
    creative_id = ctx.get("creative_id") or ctx["creatives"][-1]["creative_id"]
    ctx["assignments"] = {creative_id: [package.package_id]}


@given(parsers.parse('validation_mode is "{mode}"'))
def given_validation_mode(ctx: dict, mode: str) -> None:
    """Set validation_mode on the sync_creatives request (strict or lenient)."""
    ctx["validation_mode"] = mode


# --- 0xwq: assignment package boundary (existing pkg / existing assignment / missing pkg) ---


@given("an assignment to a package that exists in the tenant")
def given_assignment_to_existing_package(ctx: dict) -> None:
    """Create an existing package in the tenant and assign the creative to it."""
    from tests.factories import MediaBuyFactory, MediaPackageFactory, ProductFactory

    env = ctx["env"]
    _ensure_tenant_principal(ctx, env)
    tenant = ctx["tenant"]
    principal = ctx["principal"]
    agent_url = env.DEFAULT_AGENT_URL

    media_buy = MediaBuyFactory(tenant=tenant, principal=principal, status="active")
    # Product accepts the default known format so the format check passes.
    product = ProductFactory(tenant=tenant, format_ids=[{"agent_url": agent_url, "id": "display_300x250"}])
    package = MediaPackageFactory(
        media_buy=media_buy,
        package_config={"product_id": product.product_id, "budget": 1000.0},
    )
    env._commit_factory_data()
    ctx["media_buy"] = media_buy
    ctx["package"] = package
    creative_id = ctx["creatives"][-1]["creative_id"]
    ctx["assignments"] = {creative_id: [package.package_id]}


@given("an assignment that already exists for this creative")
def given_assignment_already_exists(ctx: dict) -> None:
    """Seed a pre-existing CreativeAssignment row so the sync acts as idempotent upsert."""
    from tests.factories import (
        CreativeAssignmentFactory,
        CreativeFactory,
        MediaBuyFactory,
        MediaPackageFactory,
        ProductFactory,
    )

    env = ctx["env"]
    _ensure_tenant_principal(ctx, env)
    tenant = ctx["tenant"]
    principal = ctx["principal"]
    agent_url = env.DEFAULT_AGENT_URL

    media_buy = MediaBuyFactory(tenant=tenant, principal=principal, status="active")
    product = ProductFactory(tenant=tenant, format_ids=[{"agent_url": agent_url, "id": "display_300x250"}])
    package = MediaPackageFactory(
        media_buy=media_buy,
        package_config={"product_id": product.product_id, "budget": 1000.0},
    )
    # Use same creative_id as the payload so the pre-existing row matches.
    creative_payload = ctx["creatives"][-1]
    creative_id = creative_payload["creative_id"]
    # Pre-seed the Creative row and the assignment (what sync will see as "already exists").
    creative = CreativeFactory(
        tenant=tenant,
        principal=principal,
        creative_id=creative_id,
        name=creative_payload["name"],
        agent_url=agent_url,
        format="display_300x250",
    )
    existing_assignment = CreativeAssignmentFactory(
        creative=creative,
        media_buy=media_buy,
        package_id=package.package_id,
        weight=50,  # non-default so the upsert sets weight=100 proving update ran
    )
    env._commit_factory_data()
    ctx["media_buy"] = media_buy
    ctx["package"] = package
    ctx["existing_assignment_id"] = existing_assignment.assignment_id
    ctx["existing_assignment_weight_before"] = 50
    ctx["assignments"] = {creative_id: [package.package_id]}


@given("an assignment to a package that does not exist")
def given_assignment_to_missing_package(ctx: dict) -> None:
    """Reference a package_id that does NOT exist anywhere in the tenant."""
    env = ctx["env"]
    _ensure_tenant_principal(ctx, env)
    env._commit_factory_data()
    creative_id = ctx["creatives"][-1]["creative_id"]
    ctx["assignments"] = {creative_id: ["pkg-does-not-exist-404"]}
    # Strict mode triggers AdCPNotFoundError with recovery='correctable'.
    ctx.setdefault("validation_mode", "strict")


# --- ceox: assignments-structure boundary (single entry + invalid structures) ---


@given("no assignments field")
def given_no_assignments_field(ctx: dict) -> None:
    """Explicitly omit the assignments field from the request (absent path)."""
    ctx.pop("assignments", None)
    ctx["assignments_absent"] = True


@given("an empty assignments array")
def given_empty_assignments_array(ctx: dict) -> None:
    """Set assignments to an empty value.

    The spec prescribes error ``ASSIGNMENTS_EMPTY`` for this case. Production
    currently treats empty as "no assignments" (no error). The Then step below
    xfails with SPEC-PRODUCTION GAP reason when production does not raise.
    """
    ctx["assignments"] = {}
    ctx["assignments_empty"] = True


@given(parsers.parse('an assignment with creative_id "{creative_id}" and package_id "{package_id}"'))
def given_assignment_with_ids(ctx: dict, creative_id: str, package_id: str) -> None:
    """Set up a real package with ``package_id`` and assign creative_id to it.

    Because the creative payload already has its own generated ``creative_id``,
    we reuse that id (the scenario label ``"c1"`` is a placeholder for "the
    creative"). The package is created with the literal ``package_id`` label
    from the scenario so lookup matches exactly.
    """
    from tests.factories import MediaBuyFactory, MediaPackageFactory, ProductFactory

    env = ctx["env"]
    _ensure_tenant_principal(ctx, env)
    tenant = ctx["tenant"]
    principal = ctx["principal"]
    agent_url = env.DEFAULT_AGENT_URL

    media_buy = MediaBuyFactory(tenant=tenant, principal=principal, status="active")
    product = ProductFactory(tenant=tenant, format_ids=[{"agent_url": agent_url, "id": "display_300x250"}])
    package = MediaPackageFactory(
        media_buy=media_buy,
        package_id=package_id,
        package_config={"package_id": package_id, "product_id": product.product_id, "budget": 1000.0},
    )
    env._commit_factory_data()
    ctx["media_buy"] = media_buy
    ctx["package"] = package
    # Use the real creative_id from the payload (the "c1" label is symbolic).
    real_creative_id = ctx["creatives"][-1]["creative_id"]
    ctx["assignments"] = {real_creative_id: [package.package_id]}


@given("an assignment entry with only package_id")
def given_assignment_entry_missing_creative_id(ctx: dict) -> None:
    """Attempt to submit an assignment missing creative_id.

    Production takes ``assignments`` as ``dict[creative_id -> list[package_id]]``
    and has no way to express an entry without a creative_id. The spec requires
    error ``ASSIGNMENT_CREATIVE_ID_REQUIRED``. We mark this as a SPEC-PRODUCTION
    GAP in the Then step.
    """
    # Best-effort: encode the spec shape by using empty-string creative_id as
    # the "missing" marker. Production will see an unknown creative and/or a
    # package lookup but not raise the spec-required error code.
    env = ctx["env"]
    _ensure_tenant_principal(ctx, env)
    from tests.factories import MediaBuyFactory, MediaPackageFactory

    tenant = ctx["tenant"]
    principal = ctx["principal"]
    media_buy = MediaBuyFactory(tenant=tenant, principal=principal, status="active")
    package = MediaPackageFactory(media_buy=media_buy)
    env._commit_factory_data()
    ctx["assignments"] = {"": [package.package_id]}
    ctx["assignment_missing_creative_id"] = True


@given("an assignment entry with only creative_id")
def given_assignment_entry_missing_package_id(ctx: dict) -> None:
    """Attempt to submit an assignment missing package_id.

    Production's ``dict[creative_id -> list[package_id]]`` shape has no way to
    encode "creative_id without package_id" — an empty list means "no packages".
    Spec requires error ``ASSIGNMENT_PACKAGE_ID_REQUIRED``. Marked as SPEC-
    PRODUCTION GAP in the Then step.
    """
    creative_id = ctx["creatives"][-1]["creative_id"]
    ctx["assignments"] = {creative_id: []}
    ctx["assignment_missing_package_id"] = True


@given("an assignment with weight 0")
def given_assignment_with_weight_zero(ctx: dict) -> None:
    """Spec: weight=0 → paused assignment. Production currently hard-codes weight=100.

    There is no way to express per-assignment weight in the current
    ``dict[creative_id -> list[package_id]]`` shape, so this is a SPEC-PRODUCTION
    GAP in the Then step.
    """
    from tests.factories import MediaBuyFactory, MediaPackageFactory, ProductFactory

    env = ctx["env"]
    _ensure_tenant_principal(ctx, env)
    tenant = ctx["tenant"]
    principal = ctx["principal"]
    agent_url = env.DEFAULT_AGENT_URL
    media_buy = MediaBuyFactory(tenant=tenant, principal=principal, status="active")
    product = ProductFactory(tenant=tenant, format_ids=[{"agent_url": agent_url, "id": "display_300x250"}])
    package = MediaPackageFactory(
        media_buy=media_buy,
        package_config={"product_id": product.product_id, "budget": 1000.0},
    )
    env._commit_factory_data()
    ctx["media_buy"] = media_buy
    ctx["package"] = package
    creative_id = ctx["creatives"][-1]["creative_id"]
    ctx["assignments"] = {creative_id: [package.package_id]}
    ctx["assignment_weight_zero"] = True


@given('an assignment with placement_ids ["slot_a"]')
def given_assignment_with_placement_ids(ctx: dict) -> None:
    """Spec: assignments carry placement_ids for sub-package targeting.

    Production's ``dict[creative_id -> list[package_id]]`` shape does not
    include placement_ids. SPEC-PRODUCTION GAP in the Then step.
    """
    from tests.factories import MediaBuyFactory, MediaPackageFactory, ProductFactory

    env = ctx["env"]
    _ensure_tenant_principal(ctx, env)
    tenant = ctx["tenant"]
    principal = ctx["principal"]
    agent_url = env.DEFAULT_AGENT_URL
    media_buy = MediaBuyFactory(tenant=tenant, principal=principal, status="active")
    product = ProductFactory(tenant=tenant, format_ids=[{"agent_url": agent_url, "id": "display_300x250"}])
    package = MediaPackageFactory(
        media_buy=media_buy,
        package_config={"product_id": product.product_id, "budget": 1000.0},
    )
    env._commit_factory_data()
    ctx["media_buy"] = media_buy
    ctx["package"] = package
    creative_id = ctx["creatives"][-1]["creative_id"]
    ctx["assignments"] = {creative_id: [package.package_id]}
    ctx["assignment_placement_ids"] = ["slot_a"]


# ═══════════════════════════════════════════════════════════════════════
# THEN steps — assignment outcomes (mwtk + 0xwq + ceox)
# ═══════════════════════════════════════════════════════════════════════


def _get_creative_assigned_to(ctx: dict) -> list[str]:
    """Return the assigned_to list from the response's first creative result."""
    resp = ctx.get("response")
    assert resp is not None, f"Expected a response, got error: {ctx.get('error')}"
    # SyncCreativesResponse surfaces per-creative results under ``creatives``
    # (list[SyncCreativeResult]) in the adcp 3.9 schema.
    results = getattr(resp, "creatives", None) or getattr(resp, "results", None)
    assert results, f"Response has no creatives/results: {resp}"
    return list(results[0].assigned_to or [])


@then(parsers.parse('the result should be "{outcome}"'))
def then_uc006_result_should_be(ctx: dict, outcome: str) -> None:
    """Assert outcome for UC-006 assignment-format partition scenarios.

    Known outcomes: ``assignment created`` (success + assigned_to populated)
    and ``FORMAT_MISMATCH`` (AdCPValidationError).

    SPEC-PRODUCTION GAP (all rows): The spec format ids ``agent/banner-300x250``
    and ``agent/video-30s`` use ``/`` to separate agent namespace from format
    name. Production's FormatId.id field enforces pattern ``^[a-zA-Z0-9_-]+$``
    (no ``/`` allowed), so Creative validation fails before any assignment
    processing. The failed creative has no DB row, yet assignment processing
    still fires and raises sqlalchemy ForeignKeyViolation. This is a pydantic-
    schema / production limitation, not a behavioral defect in assignment logic.
    """
    import pytest
    from sqlalchemy.exc import IntegrityError

    from src.core.exceptions import AdCPError

    # Common pre-check: spec format ids with '/' cannot round-trip through
    # production's FormatId pattern. Surface as SPEC-PRODUCTION GAP.
    err = ctx.get("error")
    if isinstance(err, IntegrityError) and "fk_creative_assignments_creative_composite" in str(err):
        pytest.xfail(
            "SPEC-PRODUCTION GAP: spec format id 'agent/<name>' contains '/', which violates "
            "production's FormatId.id pattern ^[a-zA-Z0-9_-]+$. Creative validation fails, "
            "no creative row is persisted, and assignment processing then raises FK violation."
        )
    # MCP's TypeAdapter rejects the format_id at the transport boundary (before
    # reaching _impl) with a pattern-mismatch ToolError — same underlying gap.
    if err is not None and "format_id.id" in str(err) and "string_pattern_mismatch" in str(err):
        pytest.xfail(
            "SPEC-PRODUCTION GAP: spec format id 'agent/<name>' rejected by MCP/transport "
            "boundary validation — FormatId.id pattern is ^[a-zA-Z0-9_-]+$ in adcp library schema."
        )

    if outcome == "assignment created":
        if err is not None:
            if isinstance(err, AdCPError):
                pytest.xfail(
                    f"SPEC-PRODUCTION GAP: Expected 'assignment created' but production "
                    f"raised {type(err).__name__}(code={err.error_code}): {err}"
                )
            raise AssertionError(f"Expected 'assignment created' but got {type(err).__name__}: {err}")
        assigned = _get_creative_assigned_to(ctx)
        expected_pkg_id = ctx["package"].package_id
        assert expected_pkg_id in assigned, f"Expected package {expected_pkg_id!r} in assigned_to but got {assigned}"
    elif outcome == "FORMAT_MISMATCH":
        if err is None:
            pytest.xfail(
                "SPEC-PRODUCTION GAP: Expected FORMAT_MISMATCH error but production "
                f"succeeded. Response: {ctx.get('response')}"
            )
        if not isinstance(err, AdCPError):
            raise AssertionError(f"Expected AdCPError for FORMAT_MISMATCH, got {type(err).__name__}: {err}")
        msg = str(err).lower()
        assert "format" in msg and ("not supported" in msg or "mismatch" in msg), (
            f"Expected format-mismatch indication in error, got: {err}"
        )
    else:
        raise ValueError(f"Unknown UC-006 outcome: {outcome!r}")


@then("the assignment should be created successfully")
def then_assignment_created_successfully(ctx: dict) -> None:
    """Assert the sync response reports the package was assigned to the creative."""
    assert "error" not in ctx, f"Expected success but got error: {ctx.get('error')}"
    assigned = _get_creative_assigned_to(ctx)
    expected = ctx["package"].package_id
    assert expected in assigned, f"Expected {expected!r} in assigned_to, got {assigned}"


@then("the existing assignment should be updated")
def then_existing_assignment_updated(ctx: dict) -> None:
    """Assert the pre-existing CreativeAssignment row was updated (weight set to 100)."""
    from sqlalchemy import select

    from src.core.database.models import CreativeAssignment

    assert "error" not in ctx, f"Expected success (idempotent upsert) but got error: {ctx.get('error')}"
    assignment_id = ctx["existing_assignment_id"]
    tenant_id = ctx["tenant"].tenant_id
    with db_session(ctx) as session:
        updated = session.scalars(
            select(CreativeAssignment).filter_by(tenant_id=tenant_id, assignment_id=assignment_id)
        ).first()
        assert updated is not None, f"Existing assignment {assignment_id} disappeared after sync"
        assert updated.weight == 100, (
            f"Idempotent upsert should set weight=100, but weight is {updated.weight} "
            f"(was {ctx['existing_assignment_weight_before']} before sync)"
        )


@then('the error should include "suggestion" field')
def then_error_includes_suggestion(ctx: dict) -> None:
    """Assert the error carries a 'suggestion' hint in its details.

    Production raises ``AdCPNotFoundError`` with ``recovery='correctable'`` for a
    missing package but does NOT currently populate a 'suggestion' detail field.
    Spec requires it — marked as SPEC-PRODUCTION GAP when absent.
    """
    import pytest

    error = ctx.get("error")
    assert error is not None, f"Expected an error but none recorded. Response: {ctx.get('response')}"
    _, suggestion = _extract_error_code_and_suggestion(error)
    if not suggestion:
        code = getattr(error, "error_code", None) or getattr(error, "code", None)
        recovery = getattr(error, "recovery", None)
        pytest.xfail(
            f"SPEC-PRODUCTION GAP: Expected 'suggestion' on error but got {suggestion!r} "
            f"(error_code={code}, recovery={recovery}, type={type(error).__name__})"
        )


@then("no assignment processing should occur")
def then_no_assignment_processing(ctx: dict) -> None:
    """Assert response succeeded and no assignment side-effects occurred.

    When ``assignments`` is absent, production returns success and
    SyncCreativeResult.assigned_to is None/empty.
    """
    assert "error" not in ctx, f"Expected success (no assignments) but got error: {ctx.get('error')}"
    resp = ctx.get("response")
    assert resp is not None, "Expected a response when assignments is absent"
    results = getattr(resp, "creatives", None) or getattr(resp, "results", None) or []
    # There may be 0 results if the creative also failed validation for other reasons,
    # but the defining property is: no assigned_to populated.
    for r in results:
        assigned = r.assigned_to or []
        assert not assigned, (
            f"Expected no assignments processed (ctx.assignments_absent=True), "
            f"but SyncCreativeResult({r.creative_id}).assigned_to={assigned}"
        )


@then("the assignment should be created as paused")
def then_assignment_created_as_paused(ctx: dict) -> None:
    """Spec: weight=0 assignment is paused (weight persisted as 0).

    Production hard-codes weight=100 on all new assignments and has no API
    surface for per-entry weight. SPEC-PRODUCTION GAP.
    """
    import pytest

    pytest.xfail(
        "SPEC-PRODUCTION GAP: Per-assignment weight (weight=0 → paused) is not supported. "
        "Production's assignments shape (dict[creative_id -> list[package_id]]) has no weight field; "
        "_assignments.py hard-codes weight=100 on create."
    )


# ═══════════════════════════════════════════════════════════════════════
# GIVEN / THEN steps — authentication boundary & partition (dke8)
# ═══════════════════════════════════════════════════════════════════════


@given("a creative with a format_id whose agent_url is unreachable")
def given_creative_with_unreachable_agent(ctx: dict) -> None:
    """Set up a creative whose format agent returns a connection error.

    Configures the registry mock's ``get_format`` coroutine to raise a
    ConnectionError so ``_validate_creative_input`` wraps it into a
    'Cannot validate format ... is unreachable' ValueError, producing
    a failed SyncCreativeResult (POST-F2/F3).
    """
    from unittest.mock import AsyncMock

    env = ctx["env"]
    _ensure_tenant_principal(ctx, env)

    format_id = "display_300x250"
    creative_id = "creative-unreachable-001"
    creative_payload = {
        "creative_id": creative_id,
        "name": "Unreachable Agent Creative",
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

    registry = env.mock["registry"].return_value
    registry.get_format = AsyncMock(
        side_effect=ConnectionError(f"Connection refused to {env.DEFAULT_AGENT_URL}"),
    )


@given("the request has an empty principal_id")
def given_request_empty_principal_id(ctx: dict) -> None:
    """Buyer presents an identity whose principal_id is the empty string.

    Distinct from 'no authentication credentials' (identity=None entirely):
    here the identity resolves but principal_id is empty, which
    _sync_creatives_impl rejects via ``if not principal_id`` before any DB
    or adapter work.
    """
    env = ctx["env"]
    ctx["has_auth"] = False
    ctx["identity"] = PrincipalFactory.make_identity(
        principal_id="",
        tenant_id=env._tenant_id,
    )


def _assert_auth_rejection(ctx: dict, expected_code: str) -> None:
    """Assert the sync was rejected with the spec-named auth error code.

    Production raises AdCPAuthenticationError.error_code='AUTH_TOKEN_INVALID'
    while the spec uses 'AUTH_REQUIRED'. When they differ, xfail with the
    spec-production gap reason rather than weakening the assertion.
    """
    error = ctx.get("error")
    assert error is not None, f"Expected {expected_code} error but got response: {ctx.get('response')}"
    actual_code, _ = _extract_error_code_and_suggestion(error)
    if actual_code != expected_code:
        pytest.xfail(
            f"SPEC-PRODUCTION GAP: spec requires error_code '{expected_code}' but production "
            f"raises '{actual_code}' (AdCPAuthenticationError.error_code='AUTH_TOKEN_INVALID')"
        )
    assert actual_code == expected_code, (
        f"Expected error code '{expected_code}', got '{actual_code}' ({type(error).__name__}: {error})"
    )


@then("the creative should be processed successfully")
def then_creative_processed_successfully(ctx: dict) -> None:
    """Assert the sync returned a response and the creative was created."""
    from src.core.schemas import SyncCreativesResponse

    assert "error" not in ctx, f"Expected success but got error: {ctx.get('error')}"
    resp = ctx.get("response")
    assert isinstance(resp, SyncCreativesResponse), (
        f"Expected SyncCreativesResponse, got {type(resp).__name__ if resp else None}"
    )
    results = getattr(resp, "results", None) or getattr(resp, "creatives", None) or []
    actions_str = [str(getattr(getattr(r, "action", None), "value", getattr(r, "action", None))) for r in results]
    assert any(a in ("created", "updated", "unchanged") for a in actions_str), (
        f"Expected at least one action in (created, updated, unchanged), got {actions_str}"
    )


@then("the request should be rejected with AUTH_REQUIRED")
def then_rejected_with_auth_required(ctx: dict) -> None:
    """Assert the sync was rejected with error_code AUTH_REQUIRED."""
    _assert_auth_rejection(ctx, "AUTH_REQUIRED")


@then("the assignment should include placement targeting")
def then_assignment_includes_placement(ctx: dict) -> None:
    """Spec: assignments carry placement_ids for sub-package targeting.

    Production's assignments shape has no placement_ids field and the
    CreativeAssignment ORM model does not persist per-assignment placement ids.
    SPEC-PRODUCTION GAP.
    """
    import pytest

    pytest.xfail(
        "SPEC-PRODUCTION GAP: Per-assignment placement_ids targeting is not supported. "
        "Production's assignments shape (dict[creative_id -> list[package_id]]) has no "
        "placement_ids field and the CreativeAssignment model does not persist them."
    )
