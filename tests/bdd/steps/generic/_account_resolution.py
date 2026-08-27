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

from collections.abc import Callable
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
    brand_id: str | None = None,
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
        if brand_id is not None:
            account_kwargs["brand"]["brand_id"] = brand_id
    if operator is not None:
        account_kwargs["operator"] = operator
    if sandbox is not None:
        account_kwargs["sandbox"] = sandbox

    account = AccountFactory(**account_kwargs)
    AgentAccountAccessFactory(tenant_id=tenant.tenant_id, principal=principal, account=account)
    return account


def seed_natural_key_matches(
    tenant: Any,
    *,
    count: int,
    brand_domain: str,
    operator: str,
    owner_for_index: Callable[[int], Any],
    account_id_prefix: str,
    status: str = "active",
) -> list[Any]:
    """Seed ``count`` accounts that ONE brand+operator reference all resolve to.

    Ambiguity is a property of the REFERENCE, not of duplicate rows. A reference
    carrying no ``brand_id`` does not filter on one — ``_scope_natural_key`` adds
    that predicate only when the caller supplies it — so every account sharing
    the domain and operator matches it, whatever its brand_id. Each account here
    therefore gets a DISTINCT brand_id: they are separate natural keys under the
    AdCP tuple (brand.domain + brand.brand_id + operator + sandbox), and one
    partial reference matches them all. That is exactly what ACCOUNT_AMBIGUOUS
    describes, and it is a state production can still reach.

    These scenarios previously seeded rows with an IDENTICAL natural key. That
    state is now unconstructible — ``uq_accounts_natural_key`` and
    ``AccountRepository.create`` both refuse it (salesagent-0njj) — so seeding it
    would have graded behavior on data no seller can hold. The obligation is
    unchanged; only the shape of the precondition is, because the old shape
    stopped being real.

    ``owner_for_index`` decides which principal is granted access to each account,
    so a caller can make only SOME matches accessible — natural-key resolution is
    access-scoped, and inaccessible matches must not drive ambiguity (#1417).
    """
    return [
        seed_account_with_access(
            tenant,
            owner_for_index(i),
            account_id=f"{account_id_prefix}-{i}",
            status=status,
            brand_domain=brand_domain,
            brand_id=f"brand_{i}",
            operator=operator,
        )
        for i in range(count)
    ]
