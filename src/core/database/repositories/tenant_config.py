"""Tenant config repository -- tenant-scoped access for configuration models.

Provides access to PublisherPartner and AdapterConfig for _impl functions
that need tenant-level configuration data without calling get_db_session(),
plus the atomic authorized-list mutators shared by the admin surfaces.

Core invariant: every query includes tenant_id in the WHERE clause. The tenant_id
is set at construction time and injected into all queries automatically.

beads: salesagent-9y0
"""

from __future__ import annotations

from typing import Any, Literal
from typing import cast as type_cast

from sqlalchemy import literal, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session
from sqlalchemy.sql import func

from src.core.database.jsonb_append import jsonb_list
from src.core.database.models import AdapterConfig, PublisherPartner, Tenant

AuthorizedListColumn = Literal["authorized_domains", "authorized_emails"]
AddOutcome = Literal["added", "duplicate", "missing_tenant"]
RemoveOutcome = Literal["removed", "absent", "missing_tenant"]


class TenantConfigRepository:
    """Tenant-scoped read access for configuration models.

    All queries filter by tenant_id automatically. Callers cannot bypass
    tenant isolation.

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

    def list_publisher_domains(self) -> list[str]:
        """Get sorted list of publisher domain strings for the tenant."""
        partners = self.list_publisher_partners()
        return sorted([p.publisher_domain for p in partners])

    # ------------------------------------------------------------------
    # Authorized-list mutation (atomic)
    # ------------------------------------------------------------------
    #
    # The whole point of these two methods is that check and write are ONE
    # statement: a Python read-modify-write of the JSON list loses concurrent
    # edits (last writer wins the whole list) and its membership check reads a
    # stale snapshot. A single UPDATE with the membership test in the WHERE
    # clause serializes on the row lock and re-evaluates on the fresh row, so
    # concurrent adds/removes cannot erase each other and the loser of a
    # duplicate race gets the same answer the pre-check would have given.
    # Values are stored lowercased by every caller, so exact-match jsonb
    # operators (@>, -) are the membership semantics.

    def _authorized_list_col(self, column: AuthorizedListColumn):
        if column not in ("authorized_domains", "authorized_emails"):
            raise ValueError(f"Not an authorized-list column: {column}")
        return jsonb_list(getattr(Tenant, column))

    def add_to_authorized_list(self, column: AuthorizedListColumn, value: str) -> AddOutcome:
        """Atomically append ``value`` to the tenant's list if not present."""
        col_j = self._authorized_list_col(column)
        elem = func.jsonb_build_array(literal(value))
        stmt = (
            update(Tenant)
            .where(Tenant.tenant_id == self._tenant_id)
            .where(~col_j.op("@>", is_comparison=True)(elem))
            .values({column: col_j.op("||")(elem)})
            .execution_options(synchronize_session=False)
        )
        rowcount = type_cast("CursorResult[Any]", self._session.execute(stmt)).rowcount
        if rowcount:
            return "added"
        return "duplicate" if self.get_tenant() else "missing_tenant"

    def remove_from_authorized_list(self, column: AuthorizedListColumn, value: str) -> RemoveOutcome:
        """Atomically remove every occurrence of ``value`` from the tenant's list."""
        col_j = self._authorized_list_col(column)
        stmt = (
            update(Tenant)
            .where(Tenant.tenant_id == self._tenant_id)
            .where(col_j.op("@>", is_comparison=True)(func.jsonb_build_array(literal(value))))
            .values({column: col_j.op("-")(literal(value))})
            .execution_options(synchronize_session=False)
        )
        rowcount = type_cast("CursorResult[Any]", self._session.execute(stmt)).rowcount
        if rowcount:
            return "removed"
        return "absent" if self.get_tenant() else "missing_tenant"

    def get_adapter_config(self) -> AdapterConfig | None:
        """Get the adapter configuration for the tenant, or None if not configured.

        Delegates to AdapterConfigRepository — the canonical AdapterConfig
        lookup (same absence-is-normal semantics as ``find_by_tenant``).
        """
        from src.core.database.repositories.adapter_config import AdapterConfigRepository

        return AdapterConfigRepository(self._session, self._tenant_id).find_by_tenant()
