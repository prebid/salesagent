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
from typing import TYPE_CHECKING, Any, Protocol

from sqlalchemy.exc import IntegrityError

if TYPE_CHECKING:
    from collections.abc import Callable
    from contextlib import AbstractContextManager

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
