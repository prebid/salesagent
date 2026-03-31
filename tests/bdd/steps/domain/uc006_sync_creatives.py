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

from tests.bdd.steps.generic._dispatch import dispatch_request
from tests.factories.account import AccountFactory, AgentAccountAccessFactory

# ═══════════════════════════════════════════════════════════════════════
# GIVEN steps — request setup and account state
# ═══════════════════════════════════════════════════════════════════════


@given("a creative with a known format_id")
def given_creative_with_format(ctx: dict) -> None:
    """Set up a creative with a known format — no-op for account resolution tests."""
    ctx.setdefault("creative_format_id", "display_300x250")


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
    status_map = {
        "acc_acme_001": "active",
        "acc_new_unconfigured": "pending_approval",
        "acc_overdue": "payment_required",
        "acc_suspended": "suspended",
    }
    status = status_map.get(account_id)
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

    Pre-resolution validation (missing/invalid account_ref) is handled via
    the shared validate_account_ref() helper.
    """
    from tests.bdd.steps.generic._account_resolution import validate_account_ref

    account_ref = validate_account_ref(ctx)
    if account_ref is None:
        return  # ctx["error"] already set

    dispatch_request(ctx, account=account_ref, creatives=[])


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

    from src.core.database.database_session import get_db_session
    from src.core.database.models import Account

    tenant_id = ctx["tenant"].tenant_id
    with get_db_session() as session:
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
