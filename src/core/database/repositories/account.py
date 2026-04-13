"""Account repository — tenant-scoped data access for accounts and agent access.

Core invariant: every query includes tenant_id in the WHERE clause. The tenant_id
is set at construction time and injected into all queries automatically.

beads: salesagent-m44
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.core.database.models import Account, AgentAccountAccess


class AccountRepository:
    """Tenant-scoped data access for Account and AgentAccountAccess.

    All queries filter by tenant_id automatically. Callers cannot bypass
    tenant isolation.

    Write methods add objects to the session but never commit — the Unit of Work
    (AccountUoW) handles commit/rollback at the boundary.

    Args:
        session: SQLAlchemy session (caller manages lifecycle).
        tenant_id: Tenant scope for all queries.
    """

    _IMMUTABLE_FIELDS: frozenset[str] = frozenset({"tenant_id", "account_id", "created_at"})

    def __init__(self, session: Session, tenant_id: str) -> None:
        self._session = session
        self._tenant_id = tenant_id

    @property
    def tenant_id(self) -> str:
        return self._tenant_id

    # ------------------------------------------------------------------
    # Single Account lookups
    # ------------------------------------------------------------------

    def get_by_id(self, account_id: str) -> Account | None:
        """Get an account by its ID within the tenant."""
        return self._session.scalars(
            select(Account).where(
                Account.tenant_id == self._tenant_id,
                Account.account_id == account_id,
            )
        ).first()

    def get_by_natural_key(
        self,
        operator: str,
        brand_domain: str,
        brand_id: str | None = None,
        sandbox: bool | None = None,
    ) -> Account | None:
        """Get an account by its natural key (operator + brand + sandbox).

        The brand field is JSONType containing {"domain": ..., "brand_id": ...}.
        """
        stmt = select(Account).where(
            Account.tenant_id == self._tenant_id,
            Account.operator == operator,
            Account.brand["domain"].as_string() == brand_domain,
        )
        if brand_id is not None:
            stmt = stmt.where(Account.brand["brand_id"].as_string() == brand_id)
        if sandbox is not None:
            stmt = stmt.where(Account.sandbox == sandbox)
        else:
            stmt = stmt.where(Account.sandbox.is_(None) | (Account.sandbox == False))  # noqa: E712
        return self._session.scalars(stmt).first()

    def list_by_natural_key(
        self,
        operator: str,
        brand_domain: str,
        brand_id: str | None = None,
        sandbox: bool | None = None,
        limit: int = 2,
    ) -> list[Account]:
        """List accounts matching a natural key (up to limit, for ambiguity detection).

        Single query replaces the previous count_by_natural_key + get_by_natural_key
        two-round-trip pattern. Check len(result) for ambiguity.
        """
        stmt = select(Account).where(
            Account.tenant_id == self._tenant_id,
            Account.operator == operator,
            Account.brand["domain"].as_string() == brand_domain,
        )
        if brand_id is not None:
            stmt = stmt.where(Account.brand["brand_id"].as_string() == brand_id)
        if sandbox is not None:
            stmt = stmt.where(Account.sandbox == sandbox)
        else:
            stmt = stmt.where(Account.sandbox.is_(None) | (Account.sandbox == False))  # noqa: E712
        return list(self._session.scalars(stmt.limit(limit)).all())

    def count_by_natural_key(
        self,
        operator: str,
        brand_domain: str,
        brand_id: str | None = None,
        sandbox: bool | None = None,
    ) -> int:
        """Count accounts matching a natural key (for ambiguity detection).

        Deprecated: prefer list_by_natural_key(limit=2) to avoid double query.
        """
        from sqlalchemy import func

        stmt = (
            select(func.count())
            .select_from(Account)
            .where(
                Account.tenant_id == self._tenant_id,
                Account.operator == operator,
                Account.brand["domain"].as_string() == brand_domain,
            )
        )
        if brand_id is not None:
            stmt = stmt.where(Account.brand["brand_id"].as_string() == brand_id)
        if sandbox is not None:
            stmt = stmt.where(Account.sandbox == sandbox)
        else:
            stmt = stmt.where(Account.sandbox.is_(None) | (Account.sandbox == False))  # noqa: E712
        return self._session.scalar(stmt) or 0

    # ------------------------------------------------------------------
    # List queries
    # ------------------------------------------------------------------

    def list_all(self, *, status: str | None = None) -> list[Account]:
        """List all accounts for the tenant, optionally filtered by status."""
        stmt = select(Account).where(Account.tenant_id == self._tenant_id)
        if status is not None:
            stmt = stmt.where(Account.status == status)
        return list(self._session.scalars(stmt).all())

    def list_for_agent(self, principal_id: str) -> list[Account]:
        """List accounts accessible to a specific agent (via AgentAccountAccess)."""
        return list(
            self._session.scalars(
                select(Account)
                .join(
                    AgentAccountAccess,
                    (Account.tenant_id == AgentAccountAccess.tenant_id)
                    & (Account.account_id == AgentAccountAccess.account_id),
                )
                .where(
                    Account.tenant_id == self._tenant_id,
                    AgentAccountAccess.principal_id == principal_id,
                )
            ).all()
        )

    def list_by_principal(self, principal_id: str, *, status: str | None = None) -> list[Account]:
        """List accounts created by a specific agent (for delete_missing scoping).

        Queries by Account.principal_id (the creating agent), not AgentAccountAccess.
        Optionally filter by status to exclude already-closed accounts.
        """
        stmt = select(Account).where(
            Account.tenant_id == self._tenant_id,
            Account.principal_id == principal_id,
        )
        if status is not None:
            stmt = stmt.where(Account.status == status)
        else:
            # Exclude already-closed accounts by default
            stmt = stmt.where(Account.status != "closed")
        return list(self._session.scalars(stmt).all())

    # ------------------------------------------------------------------
    # Write methods (flush, never commit)
    # ------------------------------------------------------------------

    def create(self, account: Account) -> Account:
        """Add a new account to the session.

        Raises ValueError if the account's tenant_id doesn't match.
        """
        if account.tenant_id != self._tenant_id:
            raise ValueError(
                f"Tenant mismatch: repository is scoped to '{self._tenant_id}' "
                f"but account has tenant_id='{account.tenant_id}'"
            )
        self._session.add(account)
        self._session.flush()
        return account

    def update_status(self, account_id: str, status: str) -> Account | None:
        """Update an account's status. Returns None if not found."""
        account = self.get_by_id(account_id)
        if account is None:
            return None
        account.status = status
        self._session.flush()
        return account

    def update_fields(self, account_id: str, **kwargs: object) -> Account | None:
        """Update mutable fields on an account. Returns None if not found.

        Raises ValueError if any immutable field is in kwargs.
        """
        bad = self._IMMUTABLE_FIELDS & set(kwargs)
        if bad:
            raise ValueError(f"Cannot update immutable fields: {bad}")
        account = self.get_by_id(account_id)
        if account is None:
            return None
        for key, value in kwargs.items():
            setattr(account, key, value)
        self._session.flush()
        return account

    # ------------------------------------------------------------------
    # AgentAccountAccess methods
    # ------------------------------------------------------------------

    def grant_access(self, principal_id: str, account_id: str) -> AgentAccountAccess:
        """Grant an agent access to an account."""
        access = AgentAccountAccess(
            tenant_id=self._tenant_id,
            principal_id=principal_id,
            account_id=account_id,
        )
        self._session.add(access)
        self._session.flush()
        return access

    def revoke_access(self, principal_id: str, account_id: str) -> bool:
        """Revoke an agent's access to an account. Returns True if deleted."""
        access = self._session.scalars(
            select(AgentAccountAccess).where(
                AgentAccountAccess.tenant_id == self._tenant_id,
                AgentAccountAccess.principal_id == principal_id,
                AgentAccountAccess.account_id == account_id,
            )
        ).first()
        if access is None:
            return False
        self._session.delete(access)
        self._session.flush()
        return True

    def has_access(self, principal_id: str, account_id: str) -> bool:
        """Check if an agent has access to an account."""
        return (
            self._session.scalars(
                select(AgentAccountAccess).where(
                    AgentAccountAccess.tenant_id == self._tenant_id,
                    AgentAccountAccess.principal_id == principal_id,
                    AgentAccountAccess.account_id == account_id,
                )
            ).first()
            is not None
        )

    def list_accessible_account_ids(self, principal_id: str) -> list[str]:
        """List account IDs accessible to an agent."""
        rows = self._session.scalars(
            select(AgentAccountAccess.account_id).where(
                AgentAccountAccess.tenant_id == self._tenant_id,
                AgentAccountAccess.principal_id == principal_id,
            )
        ).all()
        return list(rows)
