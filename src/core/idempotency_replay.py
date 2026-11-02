"""The shared at-most-once replay path: probe, conflict, cache, evict.

Extracted from ``media_buy_create`` so a second tool can honour the guarantee without a
second cache. The ticket that motivated it (prkv.31) is explicit about that: sync_creatives
took an ``idempotency_key``, validated its shape, and then ignored it, so a buyer whose sync
timed out and retried executed the sync twice. Taking the key while ignoring it is worse
than not taking it, because the spec attaches the at-most-once promise to the field.

Everything here is tool-agnostic. The ONE tool-specific step is turning the stored envelope
back into a typed response, which each caller supplies as ``deserialize``.
"""

import logging
import random
from collections.abc import Callable
from typing import Any

from sqlalchemy.exc import IntegrityError

from src.core.exceptions import AdCPIdempotencyConflictError

logger = logging.getLogger(__name__)

#: Fraction of successful keyed writes that run storage reclamation. Eviction is pure
#: housekeeping (read-path TTL filtering guarantees replay correctness), so the hot path
#: almost never carries the DELETE; patchable in tests.
EVICTION_PROBABILITY = 0.01


def raise_on_payload_conflict(stored_hash: str | None, request_hash: str | None) -> None:
    """Raise IDEMPOTENCY_CONFLICT when the same key carries a different canonical payload.

    Applied at every lookup point so a conflicting duplicate can never be resolved to
    someone else's response. Production writes always store a hash (``record_success``
    requires one); a row without one carries no conflict signal, so it never conflicts.
    """
    if stored_hash is not None and stored_hash != request_hash:
        raise AdCPIdempotencyConflictError()


def lookup_cached_replay(
    *,
    tenant_id: str,
    principal_id: str,
    account_id: str | None,
    idempotency_key: str,
    request_hash: str | None,
    deserialize: Callable[[dict[str, Any]], Any | None],
    enforce_ceiling: bool = True,
) -> Any | None:
    """Return the replayed response for ``idempotency_key``, or None for a miss.

    The same key carrying a different canonical payload raises IDEMPOTENCY_CONFLICT,
    checked BEFORE any replay. A hit whose stored envelope no longer validates returns
    None exactly like a miss, so callers fall through to fresh execution rather than
    erroring on drift between the writing and the replaying deploy inside the TTL window.

    ``enforce_ceiling`` rate-limits a MISS: a fresh key would insert a new cache row, and
    the per-scope insert rate and row count are bounded. A post-race recovery path passes
    False, because the loser inserts nothing.
    """
    # Lazy: tests patch src.core.database.repositories.MediaBuyUoW; the call-time import
    # binds the patched object.
    from src.core.database.repositories import MediaBuyUoW

    with MediaBuyUoW(tenant_id) as uow:
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
        raise_on_payload_conflict(cached.payload_hash, request_hash)
        return deserialize(cached.response_envelope)


def cache_success(
    *,
    tenant_id: str,
    principal_id: str,
    account_id: str | None,
    tool_name: str,
    idempotency_key: str,
    response_model: Any,
    protocol_status: str,
    payload_hash: str,
) -> None:
    """Store a successful response for verbatim replay. Best effort, never raises.

    Errors are NEVER cached (AdCP security.mdx#idempotency rule 3); the enforcement of that
    lives in the callers' error paths, which return before reaching here.

    A concurrent winner's INSERT makes this an IntegrityError, which is the expected loser
    outcome and is logged rather than raised. Any other failure is logged too: a cache write
    that fails costs a re-execution on retry, which is strictly better than failing a
    request that already succeeded.
    """
    from src.core.database.repositories import MediaBuyUoW

    try:
        with MediaBuyUoW(tenant_id) as uow:
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
    except (
        IntegrityError
    ):  # structural-guard: integrity-narrowing - best-effort cache write; logs and continues, claims no cause
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


def maybe_evict_expired(tenant_id: str) -> None:
    """Probabilistically reclaim expired cache rows in a separate short transaction.

    Runs OUTSIDE the cache-write transaction so a tenant-wide DELETE deadlock can never roll
    back a just-cached success, and only on ``EVICTION_PROBABILITY`` of keyed successes so
    the hot path almost never pays for housekeeping. Best-effort by design.

    There used to be a ``probability`` parameter, so a tool with its own patchable constant
    could pass that instead of this module's. No tool has one: the boundary is the single
    caller, and ``EVICTION_PROBABILITY`` here is the single patch point.
    """
    if random.random() >= EVICTION_PROBABILITY:
        return

    from src.core.database.repositories import MediaBuyUoW

    try:
        with MediaBuyUoW(tenant_id) as uow:
            assert uow.idempotency_attempts is not None
            uow.idempotency_attempts.expire_old()
    except Exception:
        logger.warning("Idempotency cache eviction failed for tenant %s", tenant_id, exc_info=True)
