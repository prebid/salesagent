"""Repository for the audit trail."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.core.database.models import AuditLog


class AuditLogRepository:
    """The tenant's audit records: reads, and the one write, a new record."""

    def __init__(self, session: Session, tenant_id: str) -> None:
        self.session = session
        self.tenant_id = tenant_id

    def record(
        self,
        *,
        operation: str,
        principal_id: str,
        success: bool,
        details: dict[str, Any],
        principal_name: str | None = None,
        adapter_id: str | None = None,
        error_message: str | None = None,
    ) -> None:
        """Add one audit record for this tenant, timestamped now; it commits with the unit that holds it."""
        self.session.add(
            AuditLog(
                tenant_id=self.tenant_id,
                timestamp=datetime.now(UTC),
                operation=operation,
                principal_name=principal_name,
                principal_id=principal_id,
                adapter_id=adapter_id,
                success=success,
                error_message=error_message,
                details=details,
            )
        )

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
