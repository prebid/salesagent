"""Shared account fixtures for account-resolution tests."""

from __future__ import annotations

from typing import Any

from tests.factories import AccountFactory, AgentAccountAccessFactory


def create_accessible_delivery_account(
    *,
    tenant: Any,
    principal: Any,
    account_id: str = "acc_acme_001",
    brand_domain: str = "acme-corp.com",
    operator: str = "acme-corp.com",
    status: str = "active",
) -> Any:
    """Create an account fixture that the supplied principal can access."""
    account = AccountFactory(
        tenant=tenant,
        account_id=account_id,
        status=status,
        brand={"domain": brand_domain},
        operator=operator,
    )
    AgentAccountAccessFactory(tenant_id=tenant.tenant_id, principal=principal, account=account)
    return account
