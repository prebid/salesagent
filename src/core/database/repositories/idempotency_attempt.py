"""IdempotencyAttempt repository — tenant-scoped access to the verbatim success cache.

AdCP 3.0.1 idempotency contract: retrying a mutating tool call with the same
idempotency_key must return the ORIGINAL success response byte-for-byte (marked
``replayed: true``), and errors are NEVER cached — a retry after an error
re-executes. This repository stores and replays those cached successes, keyed by
``(tenant_id, principal_id, account_id, idempotency_key)`` — the spec's
idempotency tuple exactly. ``tool_name`` is recorded for observability only,
never as a scope dimension: a key reused by a different tool hits the same row
and conflicts on its differing payload hash. ``MediaBuy.idempotency_key`` remains the
dup-booking backstop; this table holds the verbatim response to replay.

The default TTL is 24h (matches the value announced via
get_adcp_capabilities.adcp.idempotency.replay_ttl_seconds = 86400).
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from src.core.database.models import IdempotencyAttempt

# Matches GetAdcpCapabilitiesResponse.adcp.idempotency.replay_ttl_seconds (86400 = 24h).
DEFAULT_REPLAY_TTL = timedelta(seconds=86400)

# Storage-abuse ceiling: active (non-expired) cached successes per
# (tenant, principal, account) scope. Each keyed create stores one row for the
# replay TTL, so a buyer minting fresh keys is bounded to this many creates per
# window; the probe rejects the excess as RATE_LIMITED with retry_after set to
# when the oldest row expires. Looked up at call time so tests can patch it.
MAX_ACTIVE_ATTEMPTS_PER_SCOPE = 1000

# Insert-RATE limit per (tenant, principal, account) scope — the spec's MUST is
# a rate limit on cache inserts (the row count above is the derived storage
# bound). The window/ceiling follow the spec's SHOULD-level burst numbers
# (300 inserts per 10s). Looked up at call time so tests can patch them.
INSERT_RATE_WINDOW = timedelta(seconds=10)
MAX_INSERTS_PER_WINDOW = 300

# The spec Error model bounds retry_after to [1, 3600] seconds (clients clamp
# anyway); never emit more even when the oldest row expires further out.
_RETRY_AFTER_MAX = 3600


class IdempotencyAttemptRepository:
    """Tenant-scoped CRUD for the verbatim success cache.

    Queries are scoped by ``(tenant_id, principal_id, account_id,
    idempotency_key)`` — the same composite key the unique index enforces (with
    NULLS NOT DISTINCT, so a NULL account still enforces uniqueness) — so two
    principals, or two accounts under one principal, can use the same
    idempotency_key without collision, while two TOOLS under one scope cannot.

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
        idempotency_key: str,
        account_id: str | None = None,
        now: datetime | None = None,
    ) -> IdempotencyAttempt | None:
        """Return the cached success for this key, or None if absent or expired.

        The lookup scope is the spec's idempotency tuple — (agent, account,
        key) — with NO tool dimension: a key reused by a different tool must
        hit this same row (and conflict on its differing payload hash), never
        a separate per-tool cache. Expired entries are treated as absent —
        callers should fall through to re-execution rather than returning a
        stale answer. Cleanup of expired rows is the responsibility of
        ``expire_old``. ``account_id is None`` matches rows stored with no
        account (``IS NULL``), mirroring the NULLS NOT DISTINCT unique index.
        """
        current = now or datetime.now(UTC)
        stmt = (
            select(IdempotencyAttempt)
            .where(
                IdempotencyAttempt.tenant_id == self._tenant_id,
                IdempotencyAttempt.principal_id == principal_id,
                # SQLAlchemy renders ``== None`` as ``IS NULL`` — matches no-account rows.
                IdempotencyAttempt.account_id == account_id,
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
        payload_hash: str,
        account_id: str | None = None,
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

        The ``(tenant, principal, account, key)`` tuple has a UNIQUE index
        — callers guarantee they haven't already cached for this key (the
        ``find_by_key`` lookup is the natural gate). Catching the
        ``IntegrityError`` on a concurrent same-key race is the caller's
        responsibility (it resolves to a replay).

        ``payload_hash`` is the RFC 8785 canonical hash of the request payload
        (see ``src.core.idempotency_canonical``). It is required: it is what lets
        the replay lookup tell a true replay (same hash) from an
        ``IDEMPOTENCY_CONFLICT`` (same key, different hash) — a cached success
        without it could not be conflict-checked, which the spec mandates.
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

    def enforce_insert_ceiling(
        self,
        *,
        principal_id: str,
        account_id: str | None = None,
        ceiling: int | None = None,
        rate_ceiling: int | None = None,
        now: datetime | None = None,
    ) -> None:
        """Raise ``RATE_LIMITED`` when the scope has no room for another cached success.

        Called by the idempotency probe on a cache MISS, before any execution
        — a fresh key would insert a new row. Two bounds, both on the spec's
        (tenant, principal, account) scope (no tool dimension):

        - **insert rate** (the spec's MUST): at most
          :data:`MAX_INSERTS_PER_WINDOW` rows created within the trailing
          :data:`INSERT_RATE_WINDOW`; ``retry_after`` is when the oldest
          in-window insert leaves the window.
        - **active row count** (the derived storage bound): at most
          :data:`MAX_ACTIVE_ATTEMPTS_PER_SCOPE` non-expired rows;
          ``retry_after`` is when the oldest active row expires.

        Replays and conflicts are not rate-limited — they insert nothing.
        ``retry_after`` is clamped to the spec Error model's [1, 3600] bound.
        """
        current = now or datetime.now(UTC)
        scope = (
            IdempotencyAttempt.tenant_id == self._tenant_id,
            IdempotencyAttempt.principal_id == principal_id,
            # SQLAlchemy renders ``== None`` as ``IS NULL`` — matches no-account rows.
            IdempotencyAttempt.account_id == account_id,
        )

        from src.core.exceptions import AdCPRateLimitError

        # Insert-rate bound: rows CREATED inside the trailing window, expired or not.
        rate_limit = rate_ceiling if rate_ceiling is not None else MAX_INSERTS_PER_WINDOW
        window_start = current - INSERT_RATE_WINDOW
        in_window = (*scope, IdempotencyAttempt.created_at > window_start)
        recent = self._session.scalar(select(func.count()).select_from(IdempotencyAttempt).where(*in_window)) or 0
        if recent >= rate_limit:
            oldest_in_window = self._session.scalar(select(func.min(IdempotencyAttempt.created_at)).where(*in_window))
            window_seconds = math.ceil(INSERT_RATE_WINDOW.total_seconds())
            retry_after = (
                max(1, math.ceil(window_seconds - (current - oldest_in_window).total_seconds()))
                if oldest_in_window
                else 1
            )
            # The wait can never logically exceed the window itself; the bound
            # also absorbs DB-vs-app clock skew on created_at (server_default).
            raise AdCPRateLimitError(
                "idempotency cache insert rate exceeded for this account — retry shortly",
                retry_after=min(retry_after, window_seconds, _RETRY_AFTER_MAX),
            )

        # Storage bound: ACTIVE (non-expired) rows.
        limit = ceiling if ceiling is not None else MAX_ACTIVE_ATTEMPTS_PER_SCOPE
        active_scope = (*scope, IdempotencyAttempt.expires_at > current)
        active = self._session.scalar(select(func.count()).select_from(IdempotencyAttempt).where(*active_scope)) or 0
        if active < limit:
            return

        oldest_expiry = self._session.scalar(select(func.min(IdempotencyAttempt.expires_at)).where(*active_scope))
        retry_after = max(1, math.ceil((oldest_expiry - current).total_seconds())) if oldest_expiry else 1

        raise AdCPRateLimitError(
            "too many active idempotency keys for this account — retry after the oldest replay window expires",
            retry_after=min(retry_after, _RETRY_AFTER_MAX),
        )

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
