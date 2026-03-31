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
    from tests.factories.principal import PrincipalFactory

    status_map = {
        "acc_acme_001": "active",
        "acc_new_unconfigured": "pending_approval",
        "acc_overdue": "payment_required",
        "acc_suspended": "suspended",
        "acc_other_agent": "active",  # Exists but accessible to a different agent
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
    if account_id == "acc_other_agent":
        # Grant access to a DIFFERENT principal — tests authorization boundary
        other_principal = PrincipalFactory(tenant=tenant)
        AgentAccountAccessFactory(tenant_id=tenant.tenant_id, principal=other_principal, account=account)
    else:
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
    elif brand_domain == "other-agent.com":
        # Access denied: account exists but belongs to a different agent
        from tests.factories.principal import PrincipalFactory

        account = AccountFactory(
            tenant=tenant,
            account_id="acc-other-agent",
            status="active",
            brand={"domain": brand_domain},
            operator=operator,
        )
        other_principal = PrincipalFactory(tenant=tenant)
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
    """Assert account resolution succeeded — sync_creatives returned a response.

    When dispatched through transport wrappers, successful account resolution
    means the wrapper called enrich_identity_with_account() without error
    and the _impl returned a SyncCreativesResponse.
    """
    from src.core.schemas import SyncCreativesResponse

    assert "error" not in ctx, f"Expected success but got error: {ctx.get('error')}"
    resp = ctx.get("response")
    assert resp is not None, "Expected a response (SyncCreativesResponse)"
    assert isinstance(resp, SyncCreativesResponse), f"Expected SyncCreativesResponse, got {type(resp).__name__}"


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
