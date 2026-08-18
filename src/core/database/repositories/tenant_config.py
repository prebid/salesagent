"""Tenant config repository -- tenant-scoped access for configuration models.

Provides access to PublisherPartner and AdapterConfig for _impl functions
and admin write paths that need tenant-level configuration data without
calling get_db_session().

Core invariant: every query includes tenant_id in the WHERE clause. The tenant_id
is set at construction time and injected into all queries automatically.

beads: salesagent-9y0
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.core.database.models import AdapterConfig, PublisherPartner, Tenant


class TenantConfigRepository:
    """Tenant-scoped access for configuration models.

    All queries filter by tenant_id automatically. Callers cannot bypass
    tenant isolation. Write methods add/update objects but never commit —
    TenantConfigUoW handles commit/rollback at the boundary.

    Args:
        session: SQLAlchemy session (caller manages lifecycle).
        tenant_id: Tenant scope for all queries.
    """

    def __init__(self, session: Session, tenant_id: str) -> None:
        self._session = session
        self._tenant_id = tenant_id

    @property
    def tenant_id(self) -> str:
        return self._tenant_id

    def get_tenant(self) -> Tenant | None:
        """Get the tenant record."""
        stmt = select(Tenant).filter_by(tenant_id=self._tenant_id)
        return self._session.scalars(stmt).first()

    def list_publisher_partners(self) -> list[PublisherPartner]:
        """Get all publisher partners for the tenant."""
        stmt = select(PublisherPartner).filter_by(tenant_id=self._tenant_id)
        return list(self._session.scalars(stmt).all())

    def get_publisher_partner(self, partner_id: int) -> PublisherPartner | None:
        """Get a publisher partner by id within this tenant."""
        stmt = select(PublisherPartner).filter_by(id=partner_id, tenant_id=self._tenant_id)
        return self._session.scalars(stmt).first()

    def get_publisher_partner_by_domain(self, publisher_domain: str) -> PublisherPartner | None:
        """Get a publisher partner by domain within this tenant."""
        stmt = select(PublisherPartner).filter_by(tenant_id=self._tenant_id, publisher_domain=publisher_domain)
        return self._session.scalars(stmt).first()

    def create_publisher_partner(
        self,
        *,
        publisher_domain: str,
        display_name: str,
        supported_channels: list[str] | None = None,
        sync_status: str = "pending",
        is_verified: bool = False,
        last_synced_at: datetime | None = None,
    ) -> PublisherPartner:
        """Create a publisher partner scoped to this tenant. Does not commit."""
        partner = PublisherPartner(
            tenant_id=self._tenant_id,
            publisher_domain=publisher_domain,
            display_name=display_name,
            supported_channels=supported_channels,
            sync_status=sync_status,
            is_verified=is_verified,
            last_synced_at=last_synced_at,
        )
        self._session.add(partner)
        self._session.flush()
        return partner

    def update_publisher_partner(self, partner_id: int, **fields: object) -> PublisherPartner | None:
        """Update mutable partner fields. Returns None if not found in this tenant."""
        allowed = {"display_name", "supported_channels"}
        extra = set(fields) - allowed
        if extra:
            raise ValueError(f"Cannot update fields: {extra}")
        partner = self.get_publisher_partner(partner_id)
        if partner is None:
            return None
        for key, value in fields.items():
            setattr(partner, key, value)
        self._session.flush()
        return partner

    def list_publisher_domains(self) -> list[str]:
        """Get sorted list of publisher domain strings for the tenant."""
        partners = self.list_publisher_partners()
        return sorted([p.publisher_domain for p in partners])

    def get_adapter_config(self) -> AdapterConfig | None:
        """Get the adapter configuration for the tenant, or None if not configured.

        Delegates to AdapterConfigRepository — the canonical AdapterConfig
        lookup (same absence-is-normal semantics as ``find_by_tenant``).
        """
        from src.core.database.repositories.adapter_config import AdapterConfigRepository

        return AdapterConfigRepository(self._session, self._tenant_id).find_by_tenant()
