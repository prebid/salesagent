"""Account repository — tenant-scoped data access for accounts and agent access.

Core invariant: every query includes tenant_id in the WHERE clause. The tenant_id
is set at construction time and injected into all queries automatically.

beads: salesagent-m44
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.core.database.integrity import is_constraint_violation
from src.core.database.models import Account, AgentAccountAccess
from src.core.helpers.brand_key import brand_key_parts

#: The index that IS the natural-key invariant. ``_require_natural_key_free`` is
#: only the fast path in front of it.
NATURAL_KEY_INDEX = "uq_accounts_natural_key"


class NaturalKeyConflict(ValueError):
    """The account natural key is already occupied.

    A ``ValueError`` subclass on purpose: the admin blueprint's existing
    ``except ValueError`` keeps working unchanged, while ``sync_accounts`` — whose
    semantic is upsert-by-natural-key — can catch this specific type and resolve
    to ``existing_account_id`` instead of failing the entry.

    Raised for BOTH ways the key can be occupied (the pre-check finding a
    committed row, and losing the index race to a concurrent create), because to
    every caller they are the same condition; only the timing differs.
    """

    def __init__(self, message: str, *, existing_account_id: str | None = None) -> None:
        super().__init__(message)
        self.existing_account_id = existing_account_id


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

    #: Fields ``update_fields`` refuses. Beyond the PK and creation stamp, this
    #: includes the NATURAL-KEY components: ``get_by_natural_key`` resolves a
    #: buyer's ``sync_accounts`` entry on (tenant_id, operator, brand.domain[,
    #: brand_id], sandbox), so ``operator`` and ``sandbox`` are load-bearing
    #: identity, not settings. Mutating either RE-KEYS the account — the buyer's
    #: next sync carrying the original key stops matching and provisions a
    #: DUPLICATE, stranding the account_id they hold (salesagent-8sfr; the admin
    #: edit form wrote both). Enforced here rather than in the form so any future
    #: caller is refused too.
    #: ``brand`` is the third component (the lookups read ``brand_domain`` /
    #: ``brand_id`` out of that JSON column) and is listed even though nothing
    #: writes it today: this bug stayed latent for exactly as long as nothing
    #: wrote ``sandbox``, so protection that rests on the absence of a caller is
    #: not protection.
    _IMMUTABLE_FIELDS: frozenset[str] = frozenset(
        {"tenant_id", "account_id", "created_at", "operator", "sandbox", "brand"}
    )

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

    @staticmethod
    def build_row(
        *,
        tenant_id: str,
        account_id: str,
        name: str,
        status: str,
        brand_domain: str,
        brand_id: str | None,
        # ``str | None``, matching the NULLABLE column this writes (``models.py`` :864).
        # It was declared ``str`` and the sync path simply never passed None, so the
        # narrowing never surfaced; routing the admin blueprint — which writes
        # ``operator or None`` so an empty form field becomes SQL NULL rather than "" —
        # through the shared builder is what exposed a pre-existing type lie (#1878).
        operator: str | None,
        principal_id: str | None,
        created_fields: dict[str, object],
    ) -> Account:
        """Build the Account row a provisioning entry would create.

        Pure, non-persisting factory -- no DB access. Shared by the live create
        and the dry_run preview (``src/core/tools/accounts.py``) so the two
        cannot describe different rows -- a preview built from its own field
        list is how the two arms drift (#1721). The dry_run caller deliberately
        never adds the result to the session; it only needs an object to
        compare LATER entries in the same request against, the way the live
        arm compares them against the flushed row.

        Natural-key assembly (``build_row`` OWNS the row's identity columns --
        ``tenant_id``/``account_id``/``brand``/``operator``) lives here rather
        than in the tools layer: it is the exact shape CLAUDE.md Pattern #3
        names (raw ORM kwargs assembled in business logic), now on the
        repository the guard actually scans (#1721 M2).
        """
        return Account(
            tenant_id=tenant_id,
            account_id=account_id,
            name=name,
            status=status,
            # No domain means NO brand reference, not a reference with an empty domain:
            # ``Account.brand`` is nullable and a ``{"domain": ""}`` row is a different
            # value from ``None``. A no-op whenever a domain is present, which is every
            # sync/provisioning call; it is the admin form that can legitimately omit one
            # (#1878).
            brand=({"domain": brand_domain, **({"brand_id": brand_id} if brand_id else {})} if brand_domain else None),
            operator=operator,
            principal_id=principal_id,
            # Every settable field comes from the one walk in the caller -- naming
            # them here is what let a field be added to the re-sync arm and
            # forgotten at create.
            **created_fields,
        )

    def create(self, account: Account) -> Account:
        """Add a new account to the session.

        Raises ValueError if the account's tenant_id doesn't match, and
        :class:`NaturalKeyConflict` if its natural key is already occupied —
        whether the pre-check saw the occupant (the common case) or this create
        LOST the race to the unique index (#1721).

        The insert runs inside a SAVEPOINT so losing that race stays recoverable.
        Without it the failed statement aborts the enclosing transaction, and
        ``sync_accounts`` — which processes its whole batch in one ``AccountUoW``
        — could not continue to the next entry: every later statement would raise
        ``PendingRollbackError``. The savepoint rolls back this insert alone and
        leaves the surrounding transaction healthy enough to both re-resolve the
        winner and finish the batch.
        """
        if account.tenant_id != self._tenant_id:
            raise ValueError(
                f"Tenant mismatch: repository is scoped to '{self._tenant_id}' "
                f"but account has tenant_id='{account.tenant_id}'"
            )
        self._require_natural_key_free(account)
        try:
            with self._session.begin_nested():
                self._session.add(account)
                self._session.flush()
        except IntegrityError as exc:
            if not is_constraint_violation(exc, NATURAL_KEY_INDEX):
                raise
            # The winner committed between our pre-check and our insert. Re-running
            # the pre-check now finds it and raises the SAME conflict the caller
            # would have got a microsecond earlier — one message, one query, defined
            # in one place.
            self._require_natural_key_free(account)
            # Unreachable in practice: the only way the key is free again is the
            # winner rolling back after beating us. Re-raise rather than invent a
            # cause we cannot attribute — a retry of the create will now succeed.
            raise
        return account

    def _require_natural_key_free(self, account: Account) -> None:
        """Refuse a create whose natural key already resolves to an account.

        The other half of salesagent-8sfr. That bug closed the UPDATE path by
        making the key components immutable; a second CREATE reached the same
        buyer-visible harm from the other side. Once two rows share a key,
        ``get_by_natural_key().first()`` answers non-deterministically and
        ``list_by_natural_key`` reports the key unresolvable — and the buyer
        cannot repair either, because the natural key is the only handle their
        ``sync_accounts`` entry has and they do not own the extra row.

        Here rather than in the admin form for the same reason ``_IMMUTABLE_FIELDS``
        is here: there are two create callers (the admin blueprint and
        ``sync_accounts``), and a guard living in one form guards one form. The
        ``uq_accounts_natural_key`` index is the actual invariant — this check
        exists so the common case gets a usable message instead of an
        IntegrityError, and cannot replace the index (two concurrent creates can
        both pass it).

        Matches the index's key semantics exactly, NOT ``get_by_natural_key``'s
        caller-facing ones: a lookup that omits ``brand_id`` deliberately matches
        any brand_id, which would make this refuse two accounts that differ only
        in brand_id — legitimately distinct accounts under the AdCP key
        (brand.domain + brand.brand_id + operator + sandbox). A check that
        disagreed with its own index would refuse rows the database accepts.
        """
        brand_domain, brand_id = brand_key_parts(account.brand)

        if brand_domain is None:
            # No brand domain, no natural key: nothing resolves such a row (every
            # lookup supplies a domain), so there is no ambiguity to prevent.
            # Matches the partial `uq_accounts_natural_key` index — a check
            # stricter than its own index would refuse rows the DB accepts.
            return

        existing = self._session.scalars(
            select(Account).where(
                Account.tenant_id == self._tenant_id,
                Account.operator.is_not_distinct_from(account.operator),
                Account.brand["domain"].as_string().is_not_distinct_from(brand_domain),
                Account.brand["brand_id"].as_string().is_not_distinct_from(brand_id),
                # NULL and false are ONE key to every resolver, so they must be
                # one key here too — mirrors COALESCE(sandbox, false) in the index.
                func.coalesce(Account.sandbox, False) == bool(account.sandbox),
            )
        ).first()

        if existing is not None:
            raise NaturalKeyConflict(
                f"Natural key already in use: account '{existing.account_id}' already exists for "
                f"operator={account.operator!r}, brand.domain={brand_domain!r}, "
                f"brand.brand_id={brand_id!r}, sandbox={bool(account.sandbox)!r}. "
                "Edit that account instead — a second account on one natural key makes the "
                "buyer's sync_accounts entry unresolvable, and they cannot repair it.",
                existing_account_id=existing.account_id,
            )

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
