"""IdempotencyAttempt repository — tenant-scoped access to the verbatim success cache.

AdCP 3.0.1 idempotency contract: retrying a mutating tool call with the same
idempotency_key must return the ORIGINAL success response byte-for-byte (marked
``replayed: true``), and errors are NEVER cached — a retry after an error
re-executes. This repository stores and replays those cached successes, keyed by
``(tenant_id, principal_id, account_id, tool_name, idempotency_key)`` (the AdCP
scope is agent + account + key). ``MediaBuy.idempotency_key`` remains the
dup-booking backstop; this table holds the verbatim response to replay.

The default TTL is 24h (matches the value announced via
get_adcp_capabilities.adcp.idempotency.replay_ttl_seconds = 86400).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from src.core.database.models import IdempotencyAttempt

# Matches GetAdcpCapabilitiesResponse.adcp.idempotency.replay_ttl_seconds (86400 = 24h).
DEFAULT_REPLAY_TTL = timedelta(seconds=86400)


class IdempotencyAttemptRepository:
    """Tenant-scoped CRUD for the verbatim success cache.

    Queries are scoped by ``(tenant_id, principal_id, account_id, tool_name,
    idempotency_key)`` — the same composite key the unique index enforces (with
    NULLS NOT DISTINCT, so a NULL account still enforces uniqueness) — so two
    principals, or two accounts under one principal, can use the same
    idempotency_key without collision.

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

    def find_by_key(
        self,
        *,
        principal_id: str,
        tool_name: str,
        idempotency_key: str,
        account_id: str | None = None,
        now: datetime | None = None,
    ) -> IdempotencyAttempt | None:
        """Return the cached success for this key, or None if absent or expired.

        Expired entries are treated as absent — callers should fall through to
        re-execution rather than returning a stale answer. Cleanup of expired
        rows is the responsibility of ``expire_old``. ``account_id is None``
        matches rows stored with no account (``IS NULL``), mirroring the
        NULLS NOT DISTINCT unique index.
        """
        current = now or datetime.now(UTC)
        stmt = (
            select(IdempotencyAttempt)
            .where(
                IdempotencyAttempt.tenant_id == self._tenant_id,
                IdempotencyAttempt.principal_id == principal_id,
                # SQLAlchemy renders ``== None`` as ``IS NULL`` — matches no-account rows.
                IdempotencyAttempt.account_id == account_id,
                IdempotencyAttempt.tool_name == tool_name,
                IdempotencyAttempt.idempotency_key == idempotency_key,
                IdempotencyAttempt.expires_at > current,
            )
            .limit(1)
        )
        return self._session.scalars(stmt).first()

    def record_success(
        self,
        *,
        principal_id: str,
        tool_name: str,
        idempotency_key: str,
        response_model: BaseModel,
        protocol_status: str,
        account_id: str | None = None,
        payload_hash: str | None = None,
        ttl: timedelta = DEFAULT_REPLAY_TTL,
        now: datetime | None = None,
    ) -> IdempotencyAttempt:
        """Cache a successful response so future retries with the same key replay it verbatim.

        The stored envelope is ``{"status": <protocol task status>, "response":
        <model dump>}`` — the protocol status is held alongside the domain
        response so a replay reconstructs the exact original wrapper (a pending
        buy's ``submitted`` status is not a valid domain status, so it cannot
        ride inside the response payload). The wire ``replayed`` marker is
        injected at replay time, never stored. The model is serialized HERE, not
        by the caller, so ``_impl`` functions never call ``.model_dump()``
        (enforced by the no-model-dump-in-impl structural guard).

        The ``(tenant, principal, account, tool, key)`` tuple has a UNIQUE index
        — callers guarantee they haven't already cached for this key (the
        ``find_by_key`` lookup is the natural gate). Catching the
        ``IntegrityError`` on a concurrent same-key race is the caller's
        responsibility (it resolves to a replay).

        ``payload_hash`` is the RFC 8785 canonical hash of the request payload
        (see ``src.core.idempotency_canonical``). It lets the replay lookup tell
        a true replay (same hash) from an ``IDEMPOTENCY_CONFLICT`` (same key,
        different hash); ``None`` when the caller does not compute it.
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

    def expire_old(self, *, now: datetime | None = None) -> int:
        """Delete all expired attempts for this tenant. Returns the deleted count.

        Designed to be called by a periodic cleanup job. Scoped to ``tenant_id``
        so cross-tenant cleanup is impossible from a single repository.

        TTL on stored rows still applies at the read path (``find_by_key``
        filters on ``expires_at``), so replay correctness holds regardless of
        when this runs; only storage growth is the concern it addresses.
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
