"""Tool-agnostic verbatim idempotency replay/cache orchestration (AdCP 3.0.1).

A retry of a mutating tool call with the same ``idempotency_key`` must return the
ORIGINAL success response byte-for-byte (marked ``replayed: true``); a same key
with a different canonical payload is an ``IDEMPOTENCY_CONFLICT``; errors are
never cached, so a retry after an error re-executes. This module is the shared
ENGINE that ``create_media_buy`` and ``sync_accounts`` both drive: probe the
verbatim success cache, replay a hit, cache a fresh success, and resolve an
idempotency-race loser to the winner's cached response or fail closed.

The repository (:class:`IdempotencyAttemptRepository`) is already tool-agnostic —
``tool_name`` is observability-only, never a scope dimension (the scope is
``(tenant, principal, account, key)``). This module generalizes the
ORCHESTRATION that ``media_buy_create.py`` originally owned, so the second
consumer is a policy, not a copy.

Cross-tool isolation — a key reused by two DIFFERENT tools at one scope lands on a
single cache row, since ``tool_name`` is not part of the lookup — does NOT rest on
the payload-hash check alone. It rests on two invariants every consumer must keep:

1. The two tools' request schemas differ in at least one non-excluded REQUIRED
   field, so their canonical payload hashes cannot collide for valid requests — a
   cross-tool reuse conflicts (different hash) rather than replaying the wrong tool.
2. Each tool's ``replay_from_envelope`` rejects a foreign tool's stored envelope
   (response schema ``extra="forbid"`` + disjoint required fields), so even an
   (infeasible) hash collision returns ``None`` — a miss, re-executed under the
   caller's own tool — never a wrong-typed body. A future tool that breaks either
   invariant loses cross-tool isolation; the hash check is the first gate, not the
   only one.

Each tool supplies an :class:`IdempotencyReplayPolicy` with the four
tool-specific seams:

- ``make_uow`` — a Unit of Work exposing ``.idempotency_attempts`` (and, for the
  degraded path's backstop, whatever resource repo ``find_backstop_anchor``
  reads). A consumer whose UoW class is source-patched in unit tests must import
  it at call time so the patch binds (create's ``MediaBuyUoW``); a consumer tested
  against a real DB may close over a module-scope import (sync's ``AccountUoW``).
- ``replay_from_envelope`` — reconstruct the tool's return value from a stored
  ``{"status", "response"}`` envelope, marked ``replayed=True``; ``None`` when the
  stored shape no longer validates (treated as a cache miss).
- ``to_cacheable`` — extract ``(response_model, protocol_status)`` to persist, or
  ``None`` when the result is not a cacheable genuine success.
- ``find_backstop_anchor`` — the degraded path's dup-booking anchor. For a tool
  whose backstop is a separate resource with a unique index (create's
  ``MediaBuy.idempotency_key``) this returns that row's
  ``created_at``/``payload_hash``; for a tool whose only backstop is the cache's
  own unique index (sync), it is ``None``.
"""

from __future__ import annotations

import logging
import random
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Generic, NoReturn, TypeVar

from pydantic import BaseModel

from src.core.database.repositories.idempotency_attempt import DEFAULT_REPLAY_TTL
from src.core.exceptions import (
    AdCPIdempotencyConflictError,
    AdCPIdempotencyExpiredError,
    AdCPIdempotencyInFlightError,
    AdCPValidationError,
)
from src.core.log_safety import loggable

logger = logging.getLogger(__name__)

TResult = TypeVar("TResult")


@dataclass(frozen=True)
class BackstopAnchor:
    """A dup-booking backstop record's fields used by the degraded replay path.

    ``created_at`` anchors the replay-window expiry when no cache row survives;
    ``payload_hash`` lets the degraded path conflict-check exactly as the probe
    does. Produced by a policy's ``find_backstop_anchor`` from the tool's
    backstop resource (e.g. the persisted ``MediaBuy``).
    """

    created_at: datetime
    payload_hash: str | None


@dataclass(frozen=True)
class IdempotencyReplayPolicy(Generic[TResult]):
    """The tool-specific seams the shared replay engine binds against.

    See the module docstring for the role of each field. ``make_uow``'s import
    timing depends on the consumer's test strategy: import the UoW at call time
    when unit tests source-patch it (create's ``MediaBuyUoW``); a module-scope
    import is fine when the consumer is exercised against a real DB (sync's
    ``AccountUoW``).
    """

    tool_name: str
    make_uow: Callable[[str], AbstractContextManager[Any]]
    replay_from_envelope: Callable[[dict[str, Any]], TResult | None]
    to_cacheable: Callable[[TResult], tuple[BaseModel, str] | None]
    eviction_probability: Callable[[], float]
    find_backstop_anchor: Callable[[Any, str, str, str | None], BackstopAnchor | None] | None = None


def raise_on_payload_conflict(stored_hash: str | None, request_hash: str | None) -> None:
    """Raise IDEMPOTENCY_CONFLICT when the same key carries a different canonical payload.

    Applied at both lookup points — the probe and the post-race recovery — so a
    conflicting duplicate can never be resolved to someone else's response.
    Production writes always store a hash (``record_success`` requires it); a row
    without one carries no conflict signal, so it never conflicts (legacy tolerance).
    """
    if stored_hash is not None and stored_hash != request_hash:
        raise AdCPIdempotencyConflictError("idempotency_key was reused with a different request payload")


def lookup_cached_replay(
    policy: IdempotencyReplayPolicy[TResult],
    tenant_id: str,
    *,
    principal_id: str,
    account_id: str | None,
    idempotency_key: str,
    request_hash: str | None,
    enforce_ceiling: bool = False,
) -> TResult | None:
    """Probe the verbatim success cache: conflict-check the stored hash, then replay.

    Shared read path for the front probe and the post-race recovery. The same
    key carrying a different canonical payload raises ``IDEMPOTENCY_CONFLICT``
    (checked BEFORE any replay); a hit whose stored envelope no longer validates
    returns ``None`` exactly like a miss, so callers fall through to fresh
    execution (probe) or the degraded fallback (post-race).

    ``enforce_ceiling=True`` (the front probe) additionally rate-limits a MISS:
    a fresh key would insert a new cache row, and the per-scope insert rate and
    row count are bounded — see :mod:`src.services.idempotency_policy`. The
    post-race path never enforces it (the loser inserts nothing).
    """
    with policy.make_uow(tenant_id) as uow:
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
        return policy.replay_from_envelope(cached.response_envelope)


def _maybe_evict_expired(policy: IdempotencyReplayPolicy[Any], tenant_id: str) -> None:
    """Probabilistically reclaim expired cache rows in a separate short transaction.

    Runs OUTSIDE the cache-write transaction so a tenant-wide DELETE deadlock can
    never roll back a just-cached success, and only on the policy's eviction
    probability so the hot path almost never pays for housekeeping. Best-effort by
    design — a failure here affects nothing the caller sees.
    """
    if random.random() >= policy.eviction_probability():
        return
    try:
        with policy.make_uow(tenant_id) as uow:
            assert uow.idempotency_attempts is not None
            uow.idempotency_attempts.expire_old()
    except Exception:
        logger.warning(
            "Best-effort idempotency cache eviction failed for tenant %s", loggable(tenant_id), exc_info=True
        )


def cache_and_return(
    policy: IdempotencyReplayPolicy[TResult],
    result: TResult,
    *,
    tenant_id: str | None,
    principal_id: str | None,
    account_id: str | None,
    idempotency_key: str | None,
    request_hash: str | None,
    on_race: Callable[[], TResult] | None = None,
) -> TResult:
    """Best-effort store of a fresh successful result into the verbatim cache, then return it.

    Only a genuine success carrying an idempotency_key is cached (``to_cacheable``
    returns ``None`` for errors, dry-runs, and non-success variants).

    ``on_race`` selects the dup-booking backstop strategy for a cache-write
    ``IntegrityError``. Leave it ``None`` when the tool resolves its race at a
    SEPARATE resource write (create's ``MediaBuy`` unique index) — a cache-write
    race then just means a concurrent winner already stored, which is harmless
    (swallow, return this result). Pass a callable when the cache's OWN unique
    index is the tool's only backstop (sync): the loser resolves to the winner's
    verbatim replay (typically :func:`replay_after_race`) and that result is returned.
    """
    from sqlalchemy.exc import IntegrityError

    if request_hash is None or not idempotency_key or tenant_id is None or principal_id is None:
        return result
    cacheable = policy.to_cacheable(result)
    if cacheable is None:
        return result
    response_model, protocol_status = cacheable

    try:
        with policy.make_uow(tenant_id) as uow:
            assert uow.idempotency_attempts is not None
            uow.idempotency_attempts.record_success(
                principal_id=principal_id,
                account_id=account_id,
                tool_name=policy.tool_name,
                idempotency_key=idempotency_key,
                response_model=response_model,
                protocol_status=protocol_status,
                payload_hash=request_hash,
            )
    except IntegrityError:
        if on_race is not None:
            return on_race()
        logger.info(
            "Idempotency cache race for key %s (tenant %s, principal %s) — winner already stored",
            loggable(idempotency_key),
            loggable(tenant_id),
            loggable(principal_id),
        )
    except Exception:
        logger.warning(
            "Best-effort idempotency cache write failed for key %s (tenant %s, principal %s)",
            loggable(idempotency_key),
            loggable(tenant_id),
            loggable(principal_id),
            exc_info=True,
        )
    # Eviction runs AFTER the cache write commits, in its own transaction —
    # a DELETE deadlock can never roll back the just-cached success.
    _maybe_evict_expired(policy, tenant_id)
    return result


def raise_degraded_replay_outcome(
    policy: IdempotencyReplayPolicy[Any],
    tenant_id: str,
    idempotency_key: str,
    principal_id: str,
    *,
    account_id: str | None = None,
    request_hash: str | None = None,
) -> NoReturn:
    """Fail closed when the backstop fired but no verbatim cache row is usable.

    Reached only when a same-key request already won (the dup-booking backstop
    fired) but the verbatim success cache has no usable row — the race winner has
    not committed its cache write yet, the row expired past the replay TTL, or the
    stored envelope no longer validates. The lookup is account-scoped (the spec
    idempotency scope is agent + account + key).

    Per the spec, verbatim replay is byte-for-byte or nothing: a reconstructed
    body the buyer cannot distinguish from a faithful replay is the named failure
    mode, so this path never fabricates a response. Outcomes, in order:

    - resource backstop fired but its record is gone: terminal validation error
      (impossible-state guard — only for tools with ``find_backstop_anchor``),
    - replay window expired (stored ``expires_at``, or the backstop record's
      ``created_at`` when no cache row survives): ``IDEMPOTENCY_EXPIRED``
      (rule 6 fail-closed),
    - canonical payload differs from the stored hash: ``IDEMPOTENCY_CONFLICT``
      (rule 5 — exactly as at the probe),
    - otherwise: transient ``IDEMPOTENCY_IN_FLIGHT`` with a short ``retry_after``
      (rule 9 reject-and-redirect) — the winner's cache write is in flight; the
      buyer retries the SAME key and replays the verbatim envelope once it lands.
    """
    with policy.make_uow(tenant_id) as uow:
        assert uow.idempotency_attempts is not None

        anchor: BackstopAnchor | None = None
        if policy.find_backstop_anchor is not None:
            anchor = policy.find_backstop_anchor(uow, idempotency_key, principal_id, account_id)
            if anchor is None:
                # The resource backstop's unique index fired, so its row MUST
                # exist; its absence is an impossible state, not a replayable one.
                raise AdCPValidationError(
                    f"Idempotency key {idempotency_key} not found after race resolution",
                    recovery="terminal",
                )

        # Rule 6 (security.mdx#idempotency): a key the seller has seen whose
        # replay window has expired rejects rather than silently re-deriving —
        # the buyer cannot tell a faithful replay from a reconstruction this old.
        # Anchor on the cache row's STORED expires_at — the single replay-window
        # authority the probe path filters on. Fall back to the backstop record's
        # creation time only when no cache row survives (evicted after expiry, or
        # the race winner's cache write still in flight).
        cached = uow.idempotency_attempts.find_including_expired(
            principal_id=principal_id, idempotency_key=idempotency_key, account_id=account_id
        )
        now = datetime.now(UTC)
        if cached is not None:
            window_expired = cached.expires_at <= now
            conflict_hash = anchor.payload_hash if anchor is not None else cached.payload_hash
        elif anchor is not None:
            window_expired = now - anchor.created_at > DEFAULT_REPLAY_TTL
            conflict_hash = anchor.payload_hash
        else:
            # No cache row and no resource backstop (cache-only tool): the winner's
            # cache write is in flight — fall through to the transient outcome.
            window_expired = False
            conflict_hash = None

        if window_expired:
            raise AdCPIdempotencyExpiredError(
                "idempotency_key was seen before, but its replay window "
                f"({int(DEFAULT_REPLAY_TTL.total_seconds())}s) has expired",
                suggestion=(
                    "Perform a natural-key existence check to determine whether the "
                    "original request succeeded, then accept that result or mint a fresh "
                    "idempotency_key for a new attempt."
                ),
            )

        # Rule 5: same key + different canonical payload conflicts even on the
        # degraded path — never resolve a request to a buy it does not describe.
        # Legacy rows without a stored hash carry no conflict signal.
        raise_on_payload_conflict(conflict_hash, request_hash)

    raise AdCPIdempotencyInFlightError(
        "a concurrent request with this idempotency_key is still in flight — "
        "the original response is still being committed; retry with the same key shortly",
        retry_after=1,
        suggestion=(
            "wait retry_after seconds and retry with the SAME idempotency_key; do not mint "
            "a fresh key — that turns a safe retry into a double-execution race"
        ),
    )


def replay_after_race(
    policy: IdempotencyReplayPolicy[TResult],
    tenant_id: str,
    *,
    idempotency_key: str,
    principal_id: str,
    account_id: str | None,
    request_hash: str | None,
) -> TResult:
    """Resolve an idempotency-race loser to the winner's verbatim cached success.

    On the unique-index ``IntegrityError`` the winner has committed and then
    best-effort cached its response. The loser's payload must still match — the
    same key with a different canonical payload is an ``IDEMPOTENCY_CONFLICT`` here
    exactly as at the probe, never a replay of someone else's response. If the
    cache row is visible (and validates), replay it verbatim; otherwise fail
    closed (see :func:`raise_degraded_replay_outcome`) — never a fabricated body.
    """
    replay = lookup_cached_replay(
        policy,
        tenant_id,
        principal_id=principal_id,
        account_id=account_id,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
    )
    if replay is not None:
        return replay
    raise_degraded_replay_outcome(
        policy,
        tenant_id,
        idempotency_key,
        principal_id,
        account_id=account_id,
        request_hash=request_hash,
    )
