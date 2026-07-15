"""Backstop-agnostic verbatim-replay primitives for the AdCP idempotency cache.

The read/write half of idempotency that is identical across tools — probe the
verbatim success cache, conflict-check the stored payload hash, best-effort
record a fresh success, evict expired rows. Extracted from ``media_buy_create``
so ``sync_accounts`` (and future tools) share ONE implementation rather than
copy-pasting the cache plumbing (DRY invariant).

What lives HERE (tool-agnostic):
    - ``raise_on_payload_conflict`` — same key + different canonical payload → CONFLICT
    - ``lookup_cached_replay``      — probe + conflict-check + decode, with optional ceiling
    - ``record_replayable_success`` — best-effort cache write (race-loser is a no-op)
    - ``maybe_evict_expired``       — probabilistic housekeeping in its own txn

What STAYS in the tool module (backstop-specific): the dup-booking unique-index
collision handling and the degraded post-race fallback. Those depend on the
tool's own backstop table (``media_buys.idempotency_key``) and its response
shape, so they cannot be generalized — they call INTO the primitives here.

Each caller supplies:
    - ``uow_factory``: a callable ``(tenant_id) -> ContextManager`` yielding an
      object with an ``idempotency_attempts`` :class:`IdempotencyAttemptRepository`
      (``MediaBuyUoW`` for create_media_buy, ``IdempotencyUoW`` for sync_accounts).
      Passed by the caller (not imported here) so a test that patches the caller's
      UoW symbol still swaps the object these primitives use.
    - ``decode``: a callable ``(response_envelope: dict) -> T | None`` that
      reconstructs the tool's domain replay result (``None`` == unusable → miss).
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from sqlalchemy.exc import IntegrityError

from src.core.database.repositories.idempotency_attempt import COMPLETED, IN_FLIGHT, RESERVED

if TYPE_CHECKING:
    from collections.abc import Callable
    from contextlib import AbstractContextManager
    from datetime import timedelta

    from pydantic import BaseModel

    from src.core.database.repositories.idempotency_attempt import IdempotencyAttemptRepository

logger = logging.getLogger(__name__)

# Fraction of successful keyed writes that run storage reclamation. Eviction is
# pure housekeeping (read-path TTL filtering guarantees replay correctness), so
# the hot path almost never carries the DELETE; patchable in tests.
DEFAULT_EVICTION_PROBABILITY = 0.01


class _IdempotencyUoWLike(Protocol):
    """The slice of a UoW these primitives touch: an idempotency-attempts repo."""

    idempotency_attempts: IdempotencyAttemptRepository | None


def raise_on_payload_conflict(
    stored_hash: str | None,
    request_hash: str | None,
    *,
    details: dict[str, Any] | None = None,
) -> None:
    """Raise IDEMPOTENCY_CONFLICT when the same key carries a different canonical payload.

    Applied at every lookup point — the probe and any post-race recovery — so a
    conflicting duplicate can never be resolved to someone else's response.
    Production writes always store a hash (``record_success`` requires it); a row
    without one carries no conflict signal, so it never conflicts (legacy tolerance).

    ``details`` is echoed into the error envelope (e.g. the current resource
    version for an ETag-style conflict); ``None`` leaves the envelope's details
    unset, preserving the original create-path behaviour.
    """
    if stored_hash is not None and stored_hash != request_hash:
        from src.core.exceptions import AdCPIdempotencyConflictError

        raise AdCPIdempotencyConflictError(
            "idempotency_key was reused with a different request payload",
            details=details,
        )


def lookup_cached_replay[T](
    uow_factory: Callable[[str], AbstractContextManager[_IdempotencyUoWLike]],
    tenant_id: str,
    *,
    principal_id: str,
    account_id: str | None,
    idempotency_key: str,
    request_hash: str | None,
    decode: Callable[[dict[str, Any]], T | None],
    enforce_ceiling: bool = False,
    conflict_details: dict[str, Any] | None = None,
) -> T | None:
    """Probe the verbatim success cache: conflict-check the stored hash, then decode.

    Shared read path for the front probe and any post-race recovery. The same
    key carrying a different canonical payload raises ``IDEMPOTENCY_CONFLICT``
    (checked BEFORE any decode); a hit whose stored envelope no longer validates
    returns ``None`` exactly like a miss, so callers fall through to fresh
    execution (probe) or the degraded fallback (post-race).

    ``enforce_ceiling=True`` (the front probe) additionally rate-limits a MISS:
    a fresh key would insert a new cache row, and the per-scope insert rate and
    row count are bounded — see :mod:`src.services.idempotency_policy`. The
    post-race path never enforces it (the loser inserts nothing).
    """
    with uow_factory(tenant_id) as uow:
        assert uow.idempotency_attempts is not None
        cached = uow.idempotency_attempts.find_by_key(
            principal_id=principal_id,
            account_id=account_id,
            idempotency_key=idempotency_key,
        )
        if cached is None:
            if enforce_ceiling:
                from src.services.idempotency_policy import enforce_insert_ceiling

                enforce_insert_ceiling(
                    uow.idempotency_attempts,
                    principal_id=principal_id,
                    account_id=account_id,
                )
            return None
        raise_on_payload_conflict(cached.payload_hash, request_hash, details=conflict_details)
        # An in-flight reservation carries a NULL envelope and is not replayable —
        # treat it as a miss (the caller falls through to fresh execution or the
        # degraded fallback). Completed rows always carry an envelope.
        if cached.response_envelope is None:
            return None
        return decode(cached.response_envelope)


def record_replayable_success(
    uow_factory: Callable[[str], AbstractContextManager[_IdempotencyUoWLike]],
    tenant_id: str,
    *,
    principal_id: str,
    account_id: str | None,
    tool_name: str,
    idempotency_key: str,
    response_model: BaseModel,
    protocol_status: str,
    payload_hash: str,
    eviction_probability: float = DEFAULT_EVICTION_PROBABILITY,
) -> None:
    """Best-effort store of a fresh success into the verbatim cache, then evict.

    The write is best-effort — a concurrent same-key winner raises
    ``IntegrityError`` on the unique index and is harmless (the buyer's retry
    replays the winner). Any other failure is logged and swallowed: the buyer's
    response is already computed; a cache-write miss only costs a re-execution on
    the (rare) retry. Callers guard the preconditions (genuine success carrying a
    key) BEFORE calling — this primitive assumes the write is wanted.

    Eviction runs AFTER the write commits, in its own transaction, so a
    tenant-wide DELETE deadlock can never roll back the just-cached success.
    """
    try:
        with uow_factory(tenant_id) as uow:
            assert uow.idempotency_attempts is not None
            uow.idempotency_attempts.record_success(
                principal_id=principal_id,
                account_id=account_id,
                tool_name=tool_name,
                idempotency_key=idempotency_key,
                response_model=response_model,
                protocol_status=protocol_status,
                payload_hash=payload_hash,
            )
    except IntegrityError:
        logger.info(
            "Idempotency cache race for key %s (tenant %s, principal %s) — winner already stored",
            idempotency_key,
            tenant_id,
            principal_id,
        )
    except Exception:
        logger.warning(
            "Best-effort idempotency cache write failed for key %s (tenant %s, principal %s)",
            idempotency_key,
            tenant_id,
            principal_id,
            exc_info=True,
        )
    maybe_evict_expired(uow_factory, tenant_id, probability=eviction_probability)


def maybe_evict_expired(
    uow_factory: Callable[[str], AbstractContextManager[_IdempotencyUoWLike]],
    tenant_id: str,
    *,
    probability: float = DEFAULT_EVICTION_PROBABILITY,
) -> None:
    """Probabilistically reclaim expired cache rows in a separate short transaction.

    Runs OUTSIDE the cache-write transaction so a tenant-wide DELETE deadlock
    can never roll back a just-cached success, and only on ``probability`` of
    keyed successes so writes almost never pay for housekeeping. Best-effort by
    design — a failure here affects nothing the buyer sees.
    """
    if random.random() >= probability:
        return
    try:
        with uow_factory(tenant_id) as uow:
            assert uow.idempotency_attempts is not None
            uow.idempotency_attempts.expire_old()
    except Exception:
        logger.warning("Best-effort idempotency cache eviction failed for tenant %s", tenant_id, exc_info=True)


# ---------------------------------------------------------------------------
# Durable two-phase reservation façade (reserve -> work -> complete/release)
# ---------------------------------------------------------------------------
# The reservation model replaces the best-effort probe/record split for tools
# that need first-insert-wins durability (sync_accounts has no dup-booking
# backstop, so a concurrent same-key pair must be arbitrated by the reservation
# row itself, not by an idempotent side effect). create_media_buy keeps the
# best-effort lookup/record primitives above — its media_buys.idempotency_key
# partial unique index is the real dup-booking enforcer.


@dataclass(frozen=True)
class ReservationResult[T]:
    """Outcome of :func:`reserve_idempotent`.

    Exactly one field is set:

    - ``replay`` — the decoded verbatim success of a prior completed attempt;
      the caller returns it and does NO work.
    - ``attempt_id`` — this caller won the reservation; it must do the work and
      then :func:`complete_idempotent` (success) or :func:`release_idempotent`
      (failure) the row.

    A CONFLICT or IN_FLIGHT outcome does not reach here — it is raised as a typed
    ``AdCPIdempotencyConflictError`` / ``AdCPIdempotencyInFlightError``.
    """

    replay: T | None = None
    attempt_id: str | None = None


def reserve_idempotent[T](
    uow_factory: Callable[[str], AbstractContextManager[_IdempotencyUoWLike]],
    tenant_id: str,
    *,
    principal_id: str,
    account_id: str | None,
    tool_name: str,
    idempotency_key: str,
    request_hash: str,
    lease: timedelta,
    decode: Callable[[dict[str, Any]], T | None],
    conflict_details: dict[str, Any] | None = None,
) -> ReservationResult[T]:
    """Reserve the idempotency key in its OWN committed transaction, or replay/raise.

    Runs :meth:`IdempotencyAttemptRepository.reserve` and commits the surrounding
    UoW so a RESERVED in_flight row is DURABLE before the caller performs any side
    effect — the durability the best-effort ``record_replayable_success`` path
    cannot give. Outcomes:

    - RESERVED → returns ``ReservationResult(attempt_id=...)`` (row committed).
    - COMPLETED, same payload hash → decodes and returns
      ``ReservationResult(replay=...)``.
    - COMPLETED, different payload hash → raises ``AdCPIdempotencyConflictError``.
    - IN_FLIGHT (a live same-key reservation) → raises
      ``AdCPIdempotencyInFlightError`` with the lease-derived ``retry_after``.
    """
    with uow_factory(tenant_id) as uow:
        assert uow.idempotency_attempts is not None
        outcome = uow.idempotency_attempts.reserve(
            principal_id=principal_id,
            account_id=account_id,
            tool_name=tool_name,
            idempotency_key=idempotency_key,
            payload_hash=request_hash,
            lease=lease,
        )
        if outcome.kind == IN_FLIGHT:
            from src.core.exceptions import AdCPIdempotencyInFlightError

            raise AdCPIdempotencyInFlightError(
                "A prior request with the same idempotency_key is still being processed",
                retry_after=outcome.retry_after or 1,
            )
        if outcome.kind == COMPLETED:
            # Same key, different canonical payload → CONFLICT (checked before decode).
            raise_on_payload_conflict(outcome.stored_hash, request_hash, details=conflict_details)
            decoded = decode(outcome.response_envelope) if outcome.response_envelope is not None else None
            return ReservationResult(replay=decoded, attempt_id=None)
        # RESERVED (fresh INSERT or stolen expired lease). Fall through so the UoW
        # commits the in_flight row (and any steal UPDATE) on clean exit — the
        # reservation is durable only after this commit.
        assert outcome.kind == RESERVED
        reserved_attempt_id = outcome.attempt_id
    return ReservationResult(replay=None, attempt_id=reserved_attempt_id)


def complete_idempotent(
    uow: _IdempotencyUoWLike,
    *,
    attempt_id: str,
    response_model: BaseModel,
    protocol_status: str,
) -> None:
    """Flip the reserved row to completed on the SHARED work UoW (strict, atomic).

    Called INSIDE the caller's work transaction (e.g. ``SyncAccountsUoW``) so the
    cached success and the guarded side effect commit as one unit — unlike the
    best-effort ``record_replayable_success``, a completion failure rolls the
    whole atomic unit back. The model is serialized inside the repository
    (no-model-dump-in-impl).
    """
    assert uow.idempotency_attempts is not None
    uow.idempotency_attempts.complete(
        attempt_id,
        response_model=response_model,
        protocol_status=protocol_status,
    )


def release_idempotent(
    uow_factory: Callable[[str], AbstractContextManager[_IdempotencyUoWLike]],
    tenant_id: str,
    *,
    attempt_id: str,
) -> None:
    """Delete the reserved in_flight row in its OWN transaction (handler failed).

    Runs after the work transaction has rolled back, so it needs a fresh session.
    Best-effort: a release failure only leaves an in_flight row that expires and
    becomes stealable after its lease — it never caches an error. Errors are
    never cached, so a retry after release re-executes.
    """
    try:
        with uow_factory(tenant_id) as uow:
            assert uow.idempotency_attempts is not None
            uow.idempotency_attempts.release(attempt_id)
    except Exception:
        logger.warning(
            "Best-effort idempotency reservation release failed for attempt %s (tenant %s) — "
            "the in_flight row will expire and become stealable",
            attempt_id,
            tenant_id,
            exc_info=True,
        )
