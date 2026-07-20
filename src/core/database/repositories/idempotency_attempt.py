"""Dormant tenant-scoped idempotency storage primitives.

FIXME(#1683): dormant while create-replay is descoped. The seller advertises
AdCP 3.1.1 ``idempotency.supported=false``; production transports and tools do
not consult or write this repository, and supplied keys are operationally inert
after boundary shape validation. The repository is deliberately retained (not
deleted with the rest of the descoped machinery) for two reasons: it is the
schema seam over the ``idempotency_attempts`` table, and the supported=false
contract tests (test_idempotency_wire_matrix / test_idempotency_replay) use its
full method surface as their negative oracle — seeding a historical cache row
and proving no method is invoked and no row changes across repeated creates.
The probe-first replay rebuild tracked by #1683 rewires it into production and
removes this marker.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from pydantic import BaseModel
from sqlalchemy import ColumnElement, delete, func, select
from sqlalchemy.orm import InstrumentedAttribute, Session

from src.core.database.models import IdempotencyAttempt

# Dormant primitive default. It is not announced while idempotency is unsupported.
DEFAULT_REPLAY_TTL = timedelta(seconds=86400)


class IdempotencyAttemptRepository:
    """Tenant-scoped CRUD for the dormant verbatim-success store.

    Queries are scoped by ``(tenant_id, principal_id, account_id,
    idempotency_key)`` — the same composite key the unique index enforces (with
    NULLS NOT DISTINCT, so a NULL account still enforces uniqueness) — so two
    principals, or two accounts under one principal, can use the same
        idempotency_key without collision, while two tools under one scope cannot.

    No production caller uses these methods while the advertised capability is
    unsupported.

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

    def _scope_prefix(self, principal_id: str, account_id: str | None) -> tuple[ColumnElement[bool], ...]:
        """The (tenant, principal, account) isolation terms shared by EVERY scoped query.

        One home for the isolation prefix so a tenant/principal/account scoping
        fix lands once, not once per query — missing a copy would silently break
        isolation for that path, the exact failure mode the DRY-as-correctness
        rule guards against. ``_scope_filter`` appends the key for keyed lookups;
        the rate-limit aggregates use the prefix directly. ``account_id is None``
        renders ``IS NULL`` — matches no-account rows, mirroring the NULLS NOT
        DISTINCT unique index.
        """
        return (
            IdempotencyAttempt.tenant_id == self._tenant_id,
            IdempotencyAttempt.principal_id == principal_id,
            # SQLAlchemy renders ``== None`` as ``IS NULL`` — matches no-account rows.
            IdempotencyAttempt.account_id == account_id,
        )

    def _scope_filter(
        self, principal_id: str, account_id: str | None, idempotency_key: str
    ) -> tuple[ColumnElement[bool], ...]:
        """The (tenant, principal, account, key) WHERE terms the keyed lookups share.

        One home so the two dormant keyed lookup primitives cannot scope the
        same key differently. Builds on ``_scope_prefix`` and appends the key.
        """
        return (
            *self._scope_prefix(principal_id, account_id),
            IdempotencyAttempt.idempotency_key == idempotency_key,
        )

    def _count_and_oldest(
        self,
        scope: tuple[ColumnElement[bool], ...],
        min_column: InstrumentedAttribute[datetime],
    ) -> tuple[int, datetime | None]:
        """(COUNT(*), MIN(min_column)) over rows matching ``scope``.

        The two rate-limit aggregates are the same (count, oldest) scope query —
        differing only in their windowing predicate and the MIN column — so the
        query shape has one home here.
        """
        count = self._session.scalar(select(func.count()).select_from(IdempotencyAttempt).where(*scope)) or 0
        oldest = self._session.scalar(select(func.min(min_column)).where(*scope))
        return count, oldest

    def find_by_key(
        self,
        *,
        principal_id: str,
        idempotency_key: str,
        account_id: str | None = None,
        now: datetime | None = None,
    ) -> IdempotencyAttempt | None:
        """Return a non-expired stored row for this scope and key, if present.

        ``tool_name`` is deliberately not part of this dormant storage lookup.
        Expired entries are treated as absent; ``expire_old`` can reclaim them.
        ``account_id is None`` matches rows stored with no account (``IS NULL``),
        mirroring the NULLS NOT DISTINCT unique index.
        """
        current = now or datetime.now(UTC)
        stmt = (
            select(IdempotencyAttempt)
            .where(
                *self._scope_filter(principal_id, account_id, idempotency_key),
                IdempotencyAttempt.expires_at > current,
            )
            .limit(1)
        )
        return self._session.scalars(stmt).first()

    def find_including_expired(
        self,
        *,
        principal_id: str,
        idempotency_key: str,
        account_id: str | None = None,
    ) -> IdempotencyAttempt | None:
        """Return the stored row for this scope and key, including expired rows.

        The composite tuple is unique, so at most one dormant substrate row is
        returned. This primitive makes no production replay or conflict claim.
        """
        stmt = select(IdempotencyAttempt).where(*self._scope_filter(principal_id, account_id, idempotency_key)).limit(1)
        return self._session.scalars(stmt).first()

    def record_success(
        self,
        *,
        principal_id: str,
        tool_name: str,
        idempotency_key: str,
        response_model: BaseModel,
        protocol_status: str,
        payload_hash: str,
        account_id: str | None = None,
        ttl: timedelta = DEFAULT_REPLAY_TTL,
        now: datetime | None = None,
    ) -> IdempotencyAttempt:
        """Store a successful response envelope in the dormant substrate.

        The stored envelope is ``{"status": <protocol task status>, "response":
        <model dump>}`` — the protocol status is held alongside the domain
        response so a future implementation could reconstruct the wrapper (a pending
        buy's ``submitted`` status is not a valid domain status, so it cannot
        ride inside the response payload). A wire ``replayed`` marker is not
        stored. The model is serialized here, not
        by the caller, so ``_impl`` functions never call ``.model_dump()``
        (enforced by the no-model-dump-in-impl structural guard).

        The ``(tenant, principal, account, key)`` tuple has a UNIQUE index
        — callers of this primitive must handle a duplicate insert themselves.

        ``payload_hash`` is the RFC 8785 canonical hash of the request payload
        (the canonicalizer seam was deleted with the replay descope; the rebuild
        restores it — #1683). It is retained so a future implementation can
        compare the stored request without changing the schema. The currently
        advertised unsupported behavior never reads it.
        """
        current = now or datetime.now(UTC)
        attempt = IdempotencyAttempt(
            tenant_id=self._tenant_id,
            principal_id=principal_id,
            account_id=account_id,
            tool_name=tool_name,
            idempotency_key=idempotency_key,
            response_envelope={"status": protocol_status, "response": response_model.model_dump(mode="json")},
            payload_hash=payload_hash,
            expires_at=current + ttl,
        )
        self._session.add(attempt)
        self._session.flush()
        return attempt

    def count_inserts_since(
        self,
        *,
        principal_id: str,
        account_id: str | None,
        since: datetime,
    ) -> tuple[int, datetime | None]:
        """COUNT and MIN(created_at) of rows created after ``since`` in scope.

        Pure scope query (expired rows included — the insert-rate question is
        about row creation, not liveness). Thresholds and the rejection
        decision belong to the policy layer, deleted with the replay descope
        and restored by the rebuild (#1683).
        """
        scope = (*self._scope_prefix(principal_id, account_id), IdempotencyAttempt.created_at > since)
        return self._count_and_oldest(scope, IdempotencyAttempt.created_at)

    def count_active(
        self,
        *,
        principal_id: str,
        account_id: str | None,
        now: datetime,
    ) -> tuple[int, datetime | None]:
        """COUNT and MIN(expires_at) of non-expired rows in scope.

        Pure scope query; the storage ceiling and ``retry_after`` derivation
        belong to the policy layer, deleted with the replay descope and
        restored by the rebuild (#1683).
        """
        scope = (*self._scope_prefix(principal_id, account_id), IdempotencyAttempt.expires_at > now)
        return self._count_and_oldest(scope, IdempotencyAttempt.expires_at)

    def expire_old(self, *, now: datetime | None = None) -> int:
        """Delete all expired attempts for this tenant. Returns the deleted count.

        This dormant maintenance primitive is scoped to ``tenant_id`` so
        cross-tenant cleanup is impossible from a single repository.
        """
        current = now or datetime.now(UTC)
        stmt = delete(IdempotencyAttempt).where(
            IdempotencyAttempt.tenant_id == self._tenant_id,
            IdempotencyAttempt.expires_at <= current,
        )
        result = self._session.execute(stmt)
        # Result.rowcount is provided by DBAPI cursors but typed loosely in SQLAlchemy's
        # base Result protocol; the concrete CursorResult always carries it.
        return int(getattr(result, "rowcount", 0) or 0)
