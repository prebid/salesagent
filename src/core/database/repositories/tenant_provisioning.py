"""The rows a new tenant starts with, written once when it is provisioned.

Scoped to the tenant being created, which does not exist yet: the "is this key taken"
pre-checks that must run first are cross-tenant and live on ``TenantLookupRepository``.
The provisioning service (``src/services/tenant_provisioning.py``) builds the rows and
holds both repositories in one ``TenantProvisioningUoW``, which owns the transaction.
"""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy.orm import Session

from src.core.database.models import AdapterConfig, CurrencyLimit, Tenant, User


class TenantProvisioningRepository:
    """Adds a new tenant's own rows. Does not commit; the unit of work does.

    Args:
        session: SQLAlchemy session (the unit of work manages its lifecycle).
        tenant_id: The tenant being provisioned. Every row added must belong to it.
    """

    def __init__(self, session: Session, tenant_id: str) -> None:
        self._session = session
        self._tenant_id = tenant_id

    def add_tenant(
        self,
        tenant: Tenant,
        adapter_config: AdapterConfig,
        currency_limits: Iterable[CurrencyLimit],
        admin_user: User | None = None,
    ) -> None:
        """Add *tenant* with its adapter config, currency limits and optional first admin, then flush.

        Flushes so the rows that reference the tenant (principals, publishers, products) can
        be added in the same transaction. Raises ``ValueError`` for a row of another tenant.
        """
        rows: list[Tenant | AdapterConfig | CurrencyLimit | User] = [tenant, adapter_config, *currency_limits]
        if admin_user is not None:
            rows.append(admin_user)
        foreign = [type(row).__name__ for row in rows if row.tenant_id != self._tenant_id]
        if foreign:
            raise ValueError(f"rows for another tenant than {self._tenant_id!r}: {foreign}")
        self._session.add(tenant)
        self._session.flush()
        self._session.add_all(rows[1:])
        self._session.flush()
