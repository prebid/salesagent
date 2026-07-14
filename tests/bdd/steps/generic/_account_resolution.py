"""Shared account-setup helper for BDD step definitions.

Provides the tenant/principal bootstrap used by UC-002 and UC-006 When steps.

Account *resolution* itself is no longer driven here: account-resolution
scenarios now dispatch a full ``create_media_buy`` through the wire transport
(#1417), so production resolves the account at the transport boundary
and emits the outcome (success or ACCOUNT_NOT_FOUND/AMBIGUOUS/SETUP_REQUIRED/
PAYMENT_REQUIRED/SUSPENDED/VALIDATION_ERROR) on the wire. The former test-side
``AdCPValidationError`` construction and the IMPL-only resolve_account call were
removed: they bypassed the wire and reconstructed errors the harness never saw.

beads: salesagent-71q (DRY extraction), salesagent-zh85 (wire migration)
"""

from __future__ import annotations

from typing import Any


def ensure_tenant_principal(ctx: dict, env: object) -> None:
    """Create tenant + principal if not already created by a Given step."""
    if "tenant" not in ctx:
        tenant, principal = env.setup_default_data()
        ctx["tenant"] = tenant
        ctx["principal"] = principal


def seed_account_with_access(
    tenant: Any,
    principal: Any,
    *,
    account_id: str,
    status: str = "active",
    brand_domain: str | None = None,
    operator: str | None = None,
    sandbox: bool | None = None,
) -> Any:
    """Seed one Account plus an AgentAccountAccess row granting ``principal`` access.

    Single source of truth for BDD account seeding: an account the requesting
    agent can resolve (by explicit id or natural key) requires both the Account
    row AND the AgentAccountAccess join — resolution is access-scoped (#1417).
    Callers seed ONLY the accounts a scenario asserts are valid; unseeded ids
    keep erroring (ACCOUNT_NOT_FOUND) by construction.

    Uses factory-boy factories (no inline ``session.add``); the harness binds the
    session to the factories so the rows commit into the env's integration DB.
    """
    # Local import keeps the module import-light (matches the harness convention
    # of importing factories at the point of use inside step definitions).
    from tests.factories.account import AccountFactory, AgentAccountAccessFactory

    account_kwargs: dict[str, Any] = {
        "tenant": tenant,
        "account_id": account_id,
        "status": status,
    }
    if brand_domain is not None:
        account_kwargs["brand"] = {"domain": brand_domain}
    if operator is not None:
        account_kwargs["operator"] = operator
    if sandbox is not None:
        account_kwargs["sandbox"] = sandbox

    account = AccountFactory(**account_kwargs)
    AgentAccountAccessFactory(tenant_id=tenant.tenant_id, principal=principal, account=account)
    return account
