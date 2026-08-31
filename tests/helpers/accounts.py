"""Canonical account-seeding helper shared across the BDD and integration suites.

``seed_account_with_access`` is the single source of truth for seeding an account the
requesting agent can resolve (#1417): resolution is access-scoped, so a resolvable account
needs BOTH the Account row AND the AgentAccountAccess grant. Its signature carries the full
account shape (``status``/``brand_domain``/``operator``/``sandbox``) so id-only, natural-key,
and status-specific accounts all seed through one helper — no per-suite twin.

Lives in ``tests/helpers/`` (not ``tests/bdd/steps/generic/``) so the integration suite can
import it directly, rather than reaching into ``bdd/steps/generic`` from a different layer;
``tests/bdd/steps/generic/_account_resolution.py`` re-exports it for the BDD step files that
already reference it there (#1329).
"""

from __future__ import annotations

from typing import Any

from tests.factories.account import AccountFactory, AgentAccountAccessFactory


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

    Single source of truth for account seeding: an account the requesting agent can
    resolve (by explicit id or natural key) requires both the Account row AND the
    AgentAccountAccess join — resolution is access-scoped (#1417). Callers seed ONLY
    the accounts a scenario asserts are valid; unseeded ids keep erroring
    (ACCOUNT_NOT_FOUND) by construction.

    Uses factory-boy factories (no inline ``session.add``); the harness binds the
    session to the factories so the rows commit into the env's integration DB.
    """
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
    AgentAccountAccessFactory(tenant=tenant, principal=principal, account=account)
    return account
