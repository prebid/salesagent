"""Repository for tenant lookups."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.core.database.models import Tenant


class TenantRepository:
    """Data access for tenants."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_by_id(self, tenant_id: str) -> Tenant | None:
        stmt = select(Tenant).filter_by(tenant_id=tenant_id)
        return self._session.scalars(stmt).first()

    def get_active(self, tenant_id: str) -> Tenant | None:
        stmt = select(Tenant).filter_by(tenant_id=tenant_id, is_active=True)
        return self._session.scalars(stmt).first()
