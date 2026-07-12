"""Principal repository — tenant-scoped access to principal records.

Serves the NON-identity principal loads (#1088): background workers that
resolve a principal from a DB row they already hold (approval executor,
creative push, delivery webhook scheduler) rather than from a request token.
Request-path code never uses this — the transport boundary eagerly loads
``identity.principal`` (src/core/auth_utils.get_principal_from_token).

Core invariant: every query includes tenant_id in the WHERE clause.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.core.database.models import Principal as PrincipalModel
from src.core.schemas import Principal


class PrincipalRepository:
    """Tenant-scoped data access for Principal records.

    All queries filter by tenant_id automatically. Read-only — no write
    methods; principal management lives in the admin layer.

    Args:
        session: SQLAlchemy session (caller manages lifecycle).
        tenant_id: Tenant scope for all queries.
    """

    def __init__(self, session: Session, tenant_id: str) -> None:
        self._session = session
        self._tenant_id = tenant_id

    def find_by_id(self, principal_id: str) -> Principal | None:
        """Get the schema Principal for ``principal_id``, or None if absent."""
        row = self._session.scalars(
            select(PrincipalModel).filter_by(principal_id=principal_id, tenant_id=self._tenant_id)
        ).first()
        if row is None:
            return None
        return Principal(
            principal_id=row.principal_id,
            name=row.name,
            platform_mappings=row.platform_mappings,
        )
