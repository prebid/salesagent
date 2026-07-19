"""Dormant idempotency-store admission policy primitives.

The seller advertises ``idempotency.supported=false``, so no production tool
invokes this policy. It is retained with the dormant repository substrate and
covered directly to keep a possible future implementation migration-safe. A
future capability must be grounded and wired across every required task before
these helpers become production behavior.
"""

from __future__ import annotations

import math
import os
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.core.database.repositories.idempotency_attempt import IdempotencyAttemptRepository

# Dormant storage ceiling for active rows per (tenant, principal, account)
# scope. Env-tunable; looked up at call time so direct primitive tests can
# patch it. It is not enforced or advertised by current production paths.
MAX_ACTIVE_ATTEMPTS_PER_SCOPE = int(os.getenv("IDEMPOTENCY_MAX_ACTIVE_ATTEMPTS_PER_SCOPE") or "1000")

# Dormant insert-rate limit per (tenant, principal, account) scope. The
# window/ceiling preserve the historical substrate defaults (300 inserts per
# 10s). They are not enforced while idempotency is unsupported.
INSERT_RATE_WINDOW = timedelta(seconds=int(os.getenv("IDEMPOTENCY_INSERT_RATE_WINDOW_SECONDS") or "10"))
MAX_INSERTS_PER_WINDOW = int(os.getenv("IDEMPOTENCY_MAX_INSERTS_PER_WINDOW") or "300")

# The spec Error model bounds retry_after to [1, 3600] seconds (clients clamp
# anyway); never emit more even when the oldest row expires further out. A spec
# constant, not an operational knob — deliberately not env-tunable.
_RETRY_AFTER_MAX = 3600


def _clamp_retry_after(seconds: float) -> int:
    """Clamp a raw retry_after to the spec Error model's [1, _RETRY_AFTER_MAX] bound.

    The single home for the floor/ceiling both rejection branches share; callers
    layer any context-specific cap (e.g. the insert-rate window) on top.
    """
    return min(max(1, math.ceil(seconds)), _RETRY_AFTER_MAX)


def enforce_insert_ceiling(
    attempts: IdempotencyAttemptRepository,
    *,
    principal_id: str,
    account_id: str | None = None,
    ceiling: int | None = None,
    rate_ceiling: int | None = None,
    now: datetime | None = None,
) -> None:
    """Apply dormant row-rate and active-row ceilings to a repository primitive.

    No production probe calls this while idempotency is unsupported. The helper
    preserves two historical bounds on the
    (tenant, principal, account) scope (no tool dimension):

    - **insert rate** (the spec's MUST): at most :data:`MAX_INSERTS_PER_WINDOW`
      rows created within the trailing :data:`INSERT_RATE_WINDOW`;
      ``retry_after`` is when the oldest in-window insert leaves the window.
    - **active row count** (the derived storage bound): at most
      :data:`MAX_ACTIVE_ATTEMPTS_PER_SCOPE` non-expired rows; ``retry_after``
      is when the oldest active row expires.

    ``retry_after`` is clamped to the spec Error model's [1, 3600] bound.
    """
    from src.core.exceptions import AdCPRateLimitError

    current = now or datetime.now(UTC)

    # Insert-rate bound: rows CREATED inside the trailing window, expired or not.
    rate_limit = rate_ceiling if rate_ceiling is not None else MAX_INSERTS_PER_WINDOW
    window_start = current - INSERT_RATE_WINDOW
    recent, oldest_in_window = attempts.count_inserts_since(
        principal_id=principal_id, account_id=account_id, since=window_start
    )
    if recent >= rate_limit:
        window_seconds = math.ceil(INSERT_RATE_WINDOW.total_seconds())
        raw_wait = window_seconds - (current - oldest_in_window).total_seconds() if oldest_in_window else 1
        # The wait can never logically exceed the window itself; the bound
        # also absorbs DB-vs-app clock skew on created_at (server_default).
        raise AdCPRateLimitError(
            "idempotency cache insert rate exceeded for this account — retry shortly",
            retry_after=min(_clamp_retry_after(raw_wait), window_seconds),
        )

    # Storage bound: ACTIVE (non-expired) rows.
    limit = ceiling if ceiling is not None else MAX_ACTIVE_ATTEMPTS_PER_SCOPE
    active, oldest_expiry = attempts.count_active(principal_id=principal_id, account_id=account_id, now=current)
    if active < limit:
        return

    raw_wait = (oldest_expiry - current).total_seconds() if oldest_expiry else 1
    raise AdCPRateLimitError(
        "too many active idempotency keys for this account — retry after the oldest replay window expires",
        retry_after=_clamp_retry_after(raw_wait),
    )
