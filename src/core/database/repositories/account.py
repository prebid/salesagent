"""Account repository — tenant-scoped data access for accounts and agent access.

Core invariant: every query includes tenant_id in the WHERE clause. The tenant_id
is set at construction time and injected into all queries automatically.

"""

from __future__ import annotations

from typing import TYPE_CHECKING

from adcp.types import CoreGovernanceAgent  # url-only SDK agent — the DB column + response record type
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.core.database.models import Account, AgentAccountAccess
from src.core.exceptions import AdCPAccountNotFoundError

if TYPE_CHECKING:
    # Request-side agent (carries write-only authentication) — distinct from the
    # url-only SDK type above; annotation-only, so no runtime import/cycle.
    from adcp.types.generated_poc.account.sync_governance_request import (
        GovernanceAgent as SyncGovernanceRequestAgent,
    )


def _serialize_governance_agents(agents: list[SyncGovernanceRequestAgent]) -> list[CoreGovernanceAgent]:
    """Project request governance agents to the url-only ``accounts.governance_agents`` records.

    Lives beside its sole consumer, ``AccountRepository.set_governance_binding`` — the single
    write path for ``accounts.governance_agents`` — which uses it to strip credentials before
    persisting. The SDK ``CoreGovernanceAgent`` is url-only BY DESIGN: credentials are write-only
    and MUST NEVER be persisted or echoed (sync_governance.mdx; sync-governance-response.json
    ``governance_agents.items`` = url only). The request-side ``GovernanceAgent`` carries
    ``authentication`` (schemes + credentials); this reads only ``.url``, dropping it.

    The input is the request model list ONLY (``list[SyncGovernanceRequestAgent]``): the pinned
    request schema makes ``governance_agents`` required with ``minItems: 1``, so a ``None`` /
    empty arm is unreachable, and the sole production caller passes parsed request models — no
    dict arm (the earlier ``| list[dict] | None`` union advertised shapes nothing in production
    produced, #1329). Constructing ``CoreGovernanceAgent(url=...)`` normalizes the ``AnyUrl`` and
    drops the write-only ``authentication``; the ``JSONType(model=CoreGovernanceAgent)`` column
    serializes each record on write and re-coerces on read, so persisted and returned shapes agree.
    """
    return [CoreGovernanceAgent(url=agent.url) for agent in agents]


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

    # Fields the repository OWNS: they carry an invariant a generic ``setattr`` would
    # bypass, so the generic ``update_fields`` refuses them and a dedicated method is
    # the only write path. ``governance_agents`` must be projected to url-only (the
    # credential strip) — writing it raw through ``update_fields`` would persist the
    # write-only ``authentication`` blob. ``set_governance_binding`` is that path;
    # rejecting the field here makes the strip a structural guarantee, not call-site
    # discipline, and any bypass (including a new caller) goes red immediately
    # (#1329).
    _REPO_OWNED_FIELDS: frozenset[str] = frozenset({"governance_agents"})

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
        principal_id: str | None = None,
    ) -> list[Account]:
        """List accounts matching a natural key (up to limit, for ambiguity detection).

        Single query replaces the previous count_by_natural_key + get_by_natural_key
        two-round-trip pattern. Check len(result) for ambiguity.

        When ``principal_id`` is given, the result is scoped to accounts that agent
        can access (AgentAccountAccess join) so ambiguity detection never observes —
        nor later discloses the count of — accounts outside the agent's access
        (#1417).
        """
        stmt = select(Account).where(
            Account.tenant_id == self._tenant_id,
            Account.operator == operator,
            Account.brand["domain"].as_string() == brand_domain,
        )
        stmt = self._scope_natural_key(stmt, brand_id, sandbox, principal_id)
        return list(self._session.scalars(stmt.limit(limit)).all())

    def _scope_natural_key(self, stmt, brand_id, sandbox, principal_id):
        """Apply the shared natural-key filters (brand_id, sandbox, agent access).

        Centralizes the brand_id/sandbox/access predicates so list_by_natural_key
        and count_by_natural_key cannot drift — the count drifting from the
        detection scope is exactly the #1417 info leak.
        """
        if brand_id is not None:
            stmt = stmt.where(Account.brand["brand_id"].as_string() == brand_id)
        if sandbox is not None:
            stmt = stmt.where(Account.sandbox == sandbox)
        else:
            stmt = stmt.where(Account.sandbox.is_(None) | (Account.sandbox == False))  # noqa: E712
        if principal_id is not None:
            stmt = stmt.join(
                AgentAccountAccess,
                (Account.tenant_id == AgentAccountAccess.tenant_id)
                & (Account.account_id == AgentAccountAccess.account_id),
            ).where(AgentAccountAccess.principal_id == principal_id)
        return stmt

    def count_by_natural_key(
        self,
        operator: str,
        brand_domain: str,
        brand_id: str | None = None,
        sandbox: bool | None = None,
        principal_id: str | None = None,
    ) -> int:
        """Count accounts matching a natural key.

        For ambiguity *detection* prefer list_by_natural_key(limit=2) (single query).
        This exact count is for ambiguity *disclosure* on the error path only — once
        detection has confirmed >1 match, callers use it to tell the buyer how many
        accounts collide (see account_helpers._resolve_by_natural_key).

        ``principal_id`` MUST mirror the value passed to list_by_natural_key so the
        disclosed count is scoped to the agent's accessible accounts — disclosing a
        tenant-wide count to an agent who can access fewer is an info leak
        (#1417).
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
        stmt = self._scope_natural_key(stmt, brand_id, sandbox, principal_id)
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

        Raises ValueError if any immutable or repository-owned field is in kwargs.
        Repository-owned fields (e.g. ``governance_agents``) carry a write invariant
        and must go through their dedicated method (``set_governance_binding``).
        """
        bad = self._IMMUTABLE_FIELDS & set(kwargs)
        if bad:
            raise ValueError(f"Cannot update immutable fields: {bad}")
        owned = self._REPO_OWNED_FIELDS & set(kwargs)
        if owned:
            raise ValueError(
                f"Fields {owned} are repository-owned and must be written via their "
                f"dedicated method (governance_agents -> set_governance_binding), not update_fields."
            )
        return self._apply_fields(account_id, kwargs)

    def _apply_fields(self, account_id: str, fields: dict[str, object]) -> Account | None:
        """Private setter — assign fields and flush, WITHOUT the update_fields guard.

        The single place a repository-owned field is actually written. ``set_governance_binding``
        routes here so it is not self-blocked by the ``update_fields`` guard, while every
        external caller still hits the guard.
        """
        account = self.get_by_id(account_id)
        if account is None:
            return None
        for key, value in fields.items():
            setattr(account, key, value)
        self._session.flush()
        return account

    def set_governance_binding(
        self, account_id: str, agents: list[SyncGovernanceRequestAgent]
    ) -> list[CoreGovernanceAgent]:
        """Replace an account's governance-agent binding; return the persisted url-only list.

        The SINGLE write path for ``accounts.governance_agents``, and it OWNS the
        credential strip. Request agents carry write-only ``authentication`` (schemes +
        credentials) that MUST NEVER be persisted (sync_governance.mdx; the SDK
        ``CoreGovernanceAgent`` column model is url-only by construction), so this method
        projects each agent to the url-only SDK record via ``_serialize_governance_agents``
        before writing through the private ``_apply_fields`` setter. Because generic
        ``update_fields`` REFUSES ``governance_agents`` (``_REPO_OWNED_FIELDS``), this is the
        only *update* path — the strip is a repository-layer guarantee for the mutation
        surface, not scattered call-site discipline. (A raw ``create()`` of an
        Account carrying a pre-populated ``governance_agents`` blob, or a raw ``setattr``,
        would bypass this strip; no live caller does so, there is no admin surface for
        governance, and reads are fail-closed — a strip at the persistence boundary is
        tracked as a separate hardening, #1934.)

        Returns the stored records so the caller echoes exactly what was persisted (the two can
        NEVER disagree — the docstring invariant is now enforced): ``_apply_fields`` returns
        ``None`` when no row matches ``account_id``, which would otherwise return a "synced" list
        for an account that was never written. That path is unreachable in production (the caller
        resolves ``account_id`` via ``resolve_account`` in the SAME unit of work), but a computed
        failure signal must not be discarded — raise the same ``ACCOUNT_NOT_FOUND`` the tool
        already renders as the per-account ``failed`` result, so "returned ⇒ persisted" holds by
        construction (#1329). Replaces the prior binding (per-account replace semantics).
        """
        records = _serialize_governance_agents(agents)
        if self._apply_fields(account_id, {"governance_agents": records}) is None:
            raise AdCPAccountNotFoundError(f"Account {account_id!r} not found while binding governance agents.")
        return records

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
