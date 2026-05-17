"""IdempotencyAttempt repository — tenant-scoped access to cached rejection envelopes.

AdCP spec contract item 7 (issue #1303): retrying a tool call with the same
idempotency_key must return the original answer. Successful media buys handle
this via media_buys.idempotency_key. This repository handles the rejection
path — when the original request was rejected, the buyer retrying with the
same key must get the same rejection envelope, not a fresh evaluation.

The default TTL is 24h (matches the value announced via
get_adcp_capabilities.adcp.idempotency.replay_ttl_seconds = 86400).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from src.core.database.models import IdempotencyAttempt

# Matches GetAdcpCapabilitiesResponse.adcp.idempotency.replay_ttl_seconds (86400 = 24h).
DEFAULT_REPLAY_TTL = timedelta(seconds=86400)


class IdempotencyAttemptRepository:
    """Tenant-scoped CRUD for cached rejection envelopes.

    Queries are scoped by (tenant_id, principal_id, tool_name, idempotency_key)
    — the same composite key the unique index enforces — so two principals can
    use the same idempotency_key without collision.

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
        now: datetime | None = None,
    ) -> IdempotencyAttempt | None:
        """Return the cached rejection for this key, or None if absent or expired.

        Expired entries are treated as absent — callers should fall through to
        re-evaluation rather than returning a stale answer. Cleanup of expired
        rows is the responsibility of `expire_old`.
        """
        current = now or datetime.now(UTC)
        stmt = (
            select(IdempotencyAttempt)
            .where(
                IdempotencyAttempt.tenant_id == self._tenant_id,
                IdempotencyAttempt.principal_id == principal_id,
                IdempotencyAttempt.tool_name == tool_name,
                IdempotencyAttempt.idempotency_key == idempotency_key,
                IdempotencyAttempt.expires_at > current,
            )
            .limit(1)
        )
        return self._session.scalars(stmt).first()

    def record_rejection(
        self,
        *,
        principal_id: str,
        tool_name: str,
        idempotency_key: str,
        response_envelope: dict[str, Any],
        ttl: timedelta = DEFAULT_REPLAY_TTL,
        now: datetime | None = None,
    ) -> IdempotencyAttempt:
        """Cache a rejection envelope so future retries with the same key replay it.

        The (tenant, principal, tool, key) tuple has a UNIQUE index — callers
        must guarantee they haven't already cached for this key (the
        ``find_by_key`` lookup is the natural gate). Catching the
        ``IntegrityError`` on race is the caller's responsibility.
        """
        current = now or datetime.now(UTC)
        attempt = IdempotencyAttempt(
            tenant_id=self._tenant_id,
            principal_id=principal_id,
            tool_name=tool_name,
            idempotency_key=idempotency_key,
            response_envelope=response_envelope,
            expires_at=current + ttl,
        )
        self._session.add(attempt)
        self._session.flush()
        return attempt

    def expire_old(self, *, now: datetime | None = None) -> int:
        """Delete all expired attempts for this tenant. Returns the deleted count.

        Designed to be called by a periodic cleanup job. Scoped to ``tenant_id``
        so cross-tenant cleanup is impossible from a single repository.
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
