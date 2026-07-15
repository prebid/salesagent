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
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel
from sqlalchemy import ColumnElement, delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import InstrumentedAttribute, Session

from src.core.database.models import IdempotencyAttempt

# Matches GetAdcpCapabilitiesResponse.adcp.idempotency.replay_ttl_seconds (86400 = 24h).
DEFAULT_REPLAY_TTL = timedelta(seconds=86400)

# Maximum lifetime of an in-flight reservation before it is treated as failed and
# stealable (L1/security.mdx rule 9). Advertised via
# get_adcp_capabilities.adcp.idempotency.in_flight_max_seconds. MUST be
# <= DEFAULT_REPLAY_TTL (a bound past the replay window is vacuous) and > the
# slowest handler (else a live attempt is stolen). 300s comfortably clears
# sync_accounts/create_media_buy latency and is far below the 24h replay TTL.
DEFAULT_IN_FLIGHT_LEASE = timedelta(seconds=300)
assert DEFAULT_IN_FLIGHT_LEASE <= DEFAULT_REPLAY_TTL, "in-flight lease must not exceed the replay TTL"

# Reservation lifecycle states (IdempotencyAttempt.status).
_STATUS_IN_FLIGHT = "in_flight"
_STATUS_COMPLETED = "completed"

# ReservationOutcome.kind values.
RESERVED = "reserved"  # this caller won the reservation; proceed and complete()
COMPLETED = "completed"  # a completed row already exists (replay or conflict — caller conflict-checks the hash)
IN_FLIGHT = "in_flight"  # a live in-flight reservation holds the key; caller returns IDEMPOTENCY_IN_FLIGHT


@dataclass(frozen=True)
class ReservationOutcome:
    """Result of a :meth:`IdempotencyAttemptRepository.reserve` attempt.

    ``kind`` is one of :data:`RESERVED`, :data:`COMPLETED`, :data:`IN_FLIGHT`:

    - ``RESERVED`` — the unique index accepted our in_flight INSERT (or we stole
      an expired reservation). ``attempt_id`` identifies the row to
      :meth:`complete` (on success) or :meth:`release` (on failure).
    - ``COMPLETED`` — a completed row already holds the key. ``response_envelope``
      + ``stored_hash`` let the caller distinguish a verbatim REPLAY (same hash)
      from an ``IDEMPOTENCY_CONFLICT`` (different hash).
    - ``IN_FLIGHT`` — a live (non-expired) in-flight reservation holds the key.
      ``retry_after`` is the whole-second wait hint until the lease expires.
    """

    kind: str
    attempt_id: str | None = None
    response_envelope: dict | None = None
    stored_hash: str | None = None
    retry_after: int | None = None


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

        One home so the cache lookup and the degraded post-race path cannot
        scope the same key differently. Builds on ``_scope_prefix`` and appends
        the key.
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
                *self._scope_filter(principal_id, account_id, idempotency_key),
                # Only COMPLETED reservations replay — an in_flight row carries a
                # NULL envelope and is a live attempt, never a cached success.
                IdempotencyAttempt.status == _STATUS_COMPLETED,
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
        """Return the cached row for this scope+key even if its replay window has expired.

        The (tenant, principal, account, key) tuple is unique, so this is the
        one row for the scope, expired or not. The degraded post-race path uses
        it to anchor the expiry decision on the STORED ``expires_at`` — the same
        replay-window authority ``find_by_key`` filters on — rather than
        recomputing the boundary from a different row's ``created_at``. Returns
        None only when no row was ever written for the scope (a true in-flight
        race, or post-eviction reclamation).
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
            # Best-effort single-phase write (create_media_buy path): the row is
            # inserted already COMPLETED. The two-phase reserve()/complete() path
            # (sync_accounts) inserts in_flight first, then flips to completed.
            status=_STATUS_COMPLETED,
            response_envelope={"status": protocol_status, "response": response_model.model_dump(mode="json")},
            payload_hash=payload_hash,
            expires_at=current + ttl,
        )
        self._session.add(attempt)
        self._session.flush()
        return attempt

    # ------------------------------------------------------------------
    # Two-phase reservation (durable, first-insert-wins)
    # ------------------------------------------------------------------

    def reserve(
        self,
        *,
        principal_id: str,
        account_id: str | None,
        tool_name: str,
        idempotency_key: str,
        payload_hash: str,
        lease: timedelta,
        now: datetime | None = None,
    ) -> ReservationOutcome:
        """Attempt to reserve the idempotency key by INSERTing an in_flight row.

        The unique index on ``(tenant, principal, account, key)`` is the
        first-insert-wins enforcer: the racer whose INSERT lands gets
        :data:`RESERVED`; every other racer collides (``IntegrityError``) and is
        classified against the row that won:

        - completed row → :data:`COMPLETED` (caller conflict-checks ``stored_hash``
          vs ``payload_hash`` to split a verbatim REPLAY from an
          ``IDEMPOTENCY_CONFLICT``);
        - live in_flight row → :data:`IN_FLIGHT` with a ``retry_after`` hint;
        - EXPIRED in_flight row → a conditional UPDATE *steals* the lease
          (``WHERE status='in_flight' AND expires_at<=now``); exactly one racer's
          UPDATE matches and gets :data:`RESERVED`, the loser re-reads and
          re-classifies.

        The INSERT is wrapped in a SAVEPOINT so a collision rolls back only the
        failed INSERT, leaving the session usable for the re-read. The CALLER is
        responsible for committing the surrounding transaction so a
        :data:`RESERVED` row is durable BEFORE any side effect (see
        :func:`src.services.idempotency_replay.reserve_idempotent`).
        """
        current = now or datetime.now(UTC)
        attempt = IdempotencyAttempt(
            tenant_id=self._tenant_id,
            principal_id=principal_id,
            account_id=account_id,
            tool_name=tool_name,
            idempotency_key=idempotency_key,
            status=_STATUS_IN_FLIGHT,
            response_envelope=None,
            payload_hash=payload_hash,
            expires_at=current + lease,
        )
        try:
            with self._session.begin_nested():
                self._session.add(attempt)
                self._session.flush()
        except IntegrityError:
            return self._classify_collision(
                principal_id=principal_id,
                account_id=account_id,
                idempotency_key=idempotency_key,
                payload_hash=payload_hash,
                lease=lease,
                now=current,
            )
        return ReservationOutcome(kind=RESERVED, attempt_id=attempt.attempt_id)

    def _classify_collision(
        self,
        *,
        principal_id: str,
        account_id: str | None,
        idempotency_key: str,
        payload_hash: str,
        lease: timedelta,
        now: datetime,
    ) -> ReservationOutcome:
        """Classify a losing reserve() INSERT against the row that already holds the key."""
        existing = self.find_including_expired(
            principal_id=principal_id,
            account_id=account_id,
            idempotency_key=idempotency_key,
        )
        if existing is None:
            # The winner's row vanished between our failed INSERT and this read
            # (evicted / released). Retry the reservation once — the key is free.
            return self.reserve(
                principal_id=principal_id,
                account_id=account_id,
                tool_name=existing.tool_name if existing else idempotency_key,
                idempotency_key=idempotency_key,
                payload_hash=payload_hash,
                lease=lease,
                now=now,
            )
        if existing.status == _STATUS_COMPLETED:
            return ReservationOutcome(
                kind=COMPLETED,
                attempt_id=existing.attempt_id,
                response_envelope=existing.response_envelope,
                stored_hash=existing.payload_hash,
            )
        # in_flight row: live → IN_FLIGHT; expired → attempt to steal the lease.
        if existing.expires_at > now:
            retry_after = max(1, math.ceil((existing.expires_at - now).total_seconds()))
            return ReservationOutcome(kind=IN_FLIGHT, attempt_id=existing.attempt_id, retry_after=retry_after)
        return self._steal_expired(existing.attempt_id, payload_hash=payload_hash, lease=lease, now=now)

    def _steal_expired(
        self,
        attempt_id: str,
        *,
        payload_hash: str,
        lease: timedelta,
        now: datetime,
    ) -> ReservationOutcome:
        """Conditionally take over an EXPIRED in_flight reservation. Only one racer wins."""
        stmt = (
            update(IdempotencyAttempt)
            .where(
                IdempotencyAttempt.attempt_id == attempt_id,
                IdempotencyAttempt.status == _STATUS_IN_FLIGHT,
                IdempotencyAttempt.expires_at <= now,
            )
            .values(
                status=_STATUS_IN_FLIGHT,
                payload_hash=payload_hash,
                response_envelope=None,
                expires_at=now + lease,
            )
        )
        result = self._session.execute(stmt)
        if (getattr(result, "rowcount", 0) or 0) == 1:
            # We stole the expired lease — reuse its row id for completion.
            return ReservationOutcome(kind=RESERVED, attempt_id=attempt_id)
        # Another racer stole it first (or it completed). Re-read + re-classify;
        # the row is now a fresh in_flight owned by the winner (→ IN_FLIGHT) or
        # completed (→ COMPLETED). It is no longer expired, so no infinite steal.
        self._session.expire_all()
        refreshed = self._session.get(IdempotencyAttempt, attempt_id)
        if refreshed is None:
            return ReservationOutcome(
                kind=IN_FLIGHT, attempt_id=attempt_id, retry_after=max(1, math.ceil(lease.total_seconds()))
            )
        if refreshed.status == _STATUS_COMPLETED:
            return ReservationOutcome(
                kind=COMPLETED,
                attempt_id=refreshed.attempt_id,
                response_envelope=refreshed.response_envelope,
                stored_hash=refreshed.payload_hash,
            )
        retry_after = max(1, math.ceil((refreshed.expires_at - now).total_seconds()))
        return ReservationOutcome(kind=IN_FLIGHT, attempt_id=refreshed.attempt_id, retry_after=retry_after)

    def complete(
        self,
        attempt_id: str,
        *,
        response_model: BaseModel,
        protocol_status: str,
        replay_ttl: timedelta = DEFAULT_REPLAY_TTL,
        now: datetime | None = None,
    ) -> None:
        """Flip an owned in_flight reservation to completed, caching the verbatim response.

        Serializes the model HERE (never in ``_impl`` — the no-model-dump-in-impl
        guard). The UPDATE is gated ``status='in_flight'`` so a released/stolen
        reservation is never resurrected. The CALLER runs this inside the SAME
        transaction as the guarded side effect (e.g. the account write) so the
        cached success and the side effect commit atomically.
        """
        current = now or datetime.now(UTC)
        envelope = {"status": protocol_status, "response": response_model.model_dump(mode="json")}
        stmt = (
            update(IdempotencyAttempt)
            .where(
                IdempotencyAttempt.attempt_id == attempt_id,
                IdempotencyAttempt.status == _STATUS_IN_FLIGHT,
            )
            .values(status=_STATUS_COMPLETED, response_envelope=envelope, expires_at=current + replay_ttl)
        )
        self._session.execute(stmt)

    def release(self, attempt_id: str, *, now: datetime | None = None) -> None:
        """Delete an owned in_flight reservation (handler failed — errors are NEVER cached).

        Gated ``status='in_flight'`` so a row that raced to completion is never
        deleted. A retry after release re-executes from scratch (correct: the
        failed attempt produced no cached success).
        """
        stmt = delete(IdempotencyAttempt).where(
            IdempotencyAttempt.attempt_id == attempt_id,
            IdempotencyAttempt.status == _STATUS_IN_FLIGHT,
        )
        self._session.execute(stmt)

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
        decision live in :mod:`src.services.idempotency_policy`.
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
        live in :mod:`src.services.idempotency_policy`.
        """
        scope = (*self._scope_prefix(principal_id, account_id), IdempotencyAttempt.expires_at > now)
        return self._count_and_oldest(scope, IdempotencyAttempt.expires_at)

    def expire_old(self, *, now: datetime | None = None) -> int:
        """Delete all expired attempts for this tenant. Returns the deleted count.

        Called probabilistically off the create hot path (in its own
        transaction — see ``_maybe_evict_expired``) and suitable for a
        periodic cleanup job. Scoped to ``tenant_id``
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
