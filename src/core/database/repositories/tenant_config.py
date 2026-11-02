"""Tenant config repository -- tenant-scoped access to configuration models.

Provides access to PublisherPartner and AdapterConfig for _impl functions
that need tenant-level configuration data without calling get_db_session(),
plus the one write path admin settings handlers need (``update_tenant``) and
the atomic authorized-list mutators shared by the admin surfaces.

Core invariant: every query includes tenant_id in the WHERE clause. The tenant_id
is set at construction time and injected into all queries automatically.

"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal
from typing import cast as type_cast

from sqlalchemy import inspect as sa_inspect
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
    """Tenant-scoped access for configuration models.

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

    def update_tenant(self, **columns: Any) -> bool:
        """Apply *columns* to the tenant row and stamp ``updated_at``.

        Returns ``False`` when the tenant does not exist, so a caller can
        render its own not-found response rather than have one imposed here.
        The caller owns the transaction and commits.

        This exists because settings handlers were each hand-rolling
        load-then-mutate-then-stamp against a raw ``select(Tenant)``; two of
        them had drifted into literally identical bodies. One method means the
        ``updated_at`` stamp cannot be the thing a third copy forgets.

        Every key is validated against a mapper-derived set before the
        ``setattr`` loop runs — an unknown key raises ``ValueError`` naming
        it, rather than landing as a plain Python attribute that ``setattr``
        happily accepts and this method then reports as a successful write.
        The set is derived, never hand-maintained, so a new ``Tenant`` column
        needs no repository edit — but it is not just ``column_attrs``:
        mapped attributes with a leading underscore (e.g. ``_gemini_api_key``)
        are excluded, and ``Tenant``'s own public properties that declare a
        setter (e.g. ``gemini_api_key``, which encrypts on write over that
        private column) are included instead. A naive column-only set would
        both reject the one correct spelling and admit the private one,
        writing a secret in the clear around its own encrypting setter.
        """
        tenant = self.get_tenant()
        if tenant is None:
            return False

        writable = {attr.key for attr in sa_inspect(Tenant).mapper.column_attrs if not attr.key.startswith("_")} | {
            name
            for name in dir(Tenant)
            if isinstance(getattr(Tenant, name, None), property) and getattr(Tenant, name).fset is not None
        }
        unknown = sorted(set(columns) - writable)
        if unknown:
            raise ValueError(f"Unknown Tenant attribute(s): {', '.join(unknown)}")

        for column, value in columns.items():
            setattr(tenant, column, value)
        tenant.updated_at = datetime.now(UTC)
        return True

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
