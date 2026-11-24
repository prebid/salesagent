"""Tenant config repository -- tenant-scoped access to configuration models.

Provides access to the Tenant row and AdapterConfig for _impl functions
that need tenant-level configuration data without calling get_db_session(),
plus the one write path admin settings handlers need (``update_tenant``) and
the atomic authorized-list mutators shared by the admin surfaces, and the tenant's
users' role and active flag for the admin Users page (``set_user_role``,
``toggle_user_active``), which refuse to leave the tenant without an active admin.

Core invariant: every query includes tenant_id in the WHERE clause. The tenant_id
is set at construction time and injected into all queries automatically.

"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal, NamedTuple
from typing import cast as type_cast

from sqlalchemy import inspect as sa_inspect
from sqlalchemy import literal, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session
from sqlalchemy.sql import func

from src.core.database.jsonb_append import jsonb_list
from src.core.database.models import AdapterConfig, Tenant, User, UserRole
from src.core.database.repositories.base import Repository
from src.core.domain_config import get_super_admin_domain
from src.core.http_utils import authorized_domain
from src.core.json_validators import checked_authorized_domain
from src.core.operator_errors import SuperAdminDomainRefused, TenantNotFound

AuthorizedListColumn = Literal["authorized_domains", "authorized_emails"]
AddOutcome = Literal["added", "duplicate", "missing_tenant"]
RemoveOutcome = Literal["removed", "absent", "missing_tenant"]
UserChangeOutcome = Literal["changed", "missing_user", "last_admin"]


class UserChange(NamedTuple):
    """What ``set_user_role`` / ``toggle_user_active`` did, read before the transaction ends.

    ``email`` and ``is_active`` are empty unless the user exists; ``is_active`` is the
    value after the call.
    """

    outcome: UserChangeOutcome
    email: str = ""
    is_active: bool = False


class TenantConfigRepository(Repository):
    """Tenant-scoped access for configuration models.

    All queries filter by tenant_id automatically. Callers cannot bypass
    tenant isolation.

    Args:
        session: SQLAlchemy session (caller manages lifecycle).
        tenant_id: Tenant scope for all queries.
    """

    NOT_FOUND = TenantNotFound

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
        """Atomically append ``value`` to the tenant's list if not present.

        An authorized domain must be a domain name (``checked_authorized_domain``): this UPDATE
        bypasses the ORM validator that checks a whole-list assignment. It must also not be the
        deployment's ``SUPER_ADMIN_DOMAIN``, compared in the form it would be stored in. Every
        page that lets a tenant add a domain appends through here; a whole-list assignment is
        the operator's own (tenant creation, the management API, the single-tenant bootstrap,
        which seeds ``SUPER_ADMIN_DOMAINS``), so this refusal is not on the column.
        """
        col_j = self._authorized_list_col(column)
        if column == "authorized_domains":
            value = checked_authorized_domain(value)
            if value == authorized_domain(get_super_admin_domain()):
                raise SuperAdminDomainRefused(f"Tenant {self._tenant_id} authorizing the super-admin domain {value}")
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

    # ------------------------------------------------------------------
    # Tenant users
    # ------------------------------------------------------------------

    def set_user_role(self, user_id: str, role: UserRole) -> UserChange:
        """Give the tenant's user *user_id* *role*, unless that demotes the last active admin."""
        user = self._get_user(user_id)
        if user is None:
            return UserChange("missing_user")
        if role != UserRole.ADMIN and self._is_last_active_admin(user):
            return UserChange("last_admin", user.email, user.is_active)
        user.role = role
        return UserChange("changed", user.email, user.is_active)

    def toggle_user_active(self, user_id: str) -> UserChange:
        """Flip whether the tenant's user *user_id* is active, unless that deactivates the last active admin."""
        user = self._get_user(user_id)
        if user is None:
            return UserChange("missing_user")
        if self._is_last_active_admin(user):
            return UserChange("last_admin", user.email, user.is_active)
        user.is_active = not user.is_active
        return UserChange("changed", user.email, user.is_active)

    def get_active_user_by_email(self, email: str) -> User | None:
        """The tenant's active user with *email* (compared lower-cased), or None."""
        stmt = select(User).filter_by(tenant_id=self._tenant_id, email=email.lower(), is_active=True)
        return self._session.scalars(stmt).first()

    def _get_user(self, user_id: str) -> User | None:
        stmt = select(User).filter_by(tenant_id=self._tenant_id, user_id=user_id)
        return self._session.scalars(stmt).first()

    def _is_last_active_admin(self, user: User) -> bool:
        """Whether *user* is the tenant's only active admin.

        Locks the tenant's active admin rows (``FOR UPDATE``, in id order) until the
        transaction ends, so two admins demoting or deactivating each other at the same
        time cannot both pass: the second waits for the first to commit, and PostgreSQL
        re-checks the locked rows against the ``WHERE`` before returning them. This holds
        because the callers above check and write in the same transaction.

        The answer comes from the locked query alone, never from *user*'s own (unlocked)
        ``role`` / ``is_active``: a target read as a viewer may have been promoted since,
        and deciding from that read would take no lock at all.
        """
        stmt = (
            select(User.user_id)
            .filter_by(tenant_id=self._tenant_id, role=UserRole.ADMIN, is_active=True)
            .order_by(User.user_id)
            .with_for_update()
        )
        return list(self._session.scalars(stmt).all()) == [user.user_id]
