"""Repository for AuditLog queries."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.core.database.models import AuditLog


class AuditLogRepository:
    """Read-only repository for audit log queries."""

    def __init__(self, session: Session, tenant_id: str) -> None:
        self.session = session
        self.tenant_id = tenant_id

    def list_by_tenant(self) -> list[AuditLog]:
        """Return all audit logs for the tenant."""
        return list(self.session.scalars(select(AuditLog).filter_by(tenant_id=self.tenant_id)).all())

    def find_by_operation(self, operation_substring: str) -> list[AuditLog]:
        """Return audit logs whose operation contains the substring."""
        return [log for log in self.list_by_tenant() if operation_substring in (log.operation or "")]

    def list_recent(self, operation: str, limit: int) -> list[AuditLog]:
        """The tenant's *limit* most recent audit logs of exactly *operation*, newest first."""
        stmt = (
            select(AuditLog)
            .filter_by(tenant_id=self.tenant_id, operation=operation)
            .order_by(AuditLog.timestamp.desc())
            .limit(limit)
        )
        return list(self.session.scalars(stmt).all())
