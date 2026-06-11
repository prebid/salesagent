"""Idempotency cache admission policy — thresholds and retry_after derivation.

The policy layer over :class:`IdempotencyAttemptRepository`: the repository
answers the two scope questions (how many inserts in the trailing window, how
many active rows — plus their oldest timestamps); this module owns the
thresholds, the ``retry_after`` math, and the decision to reject. Data access
stays in the repository; policy changes never touch SQL.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.core.database.repositories.idempotency_attempt import IdempotencyAttemptRepository

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


def enforce_insert_ceiling(
    attempts: IdempotencyAttemptRepository,
    *,
    principal_id: str,
    account_id: str | None = None,
    ceiling: int | None = None,
    rate_ceiling: int | None = None,
    now: datetime | None = None,
) -> None:
    """Raise ``RATE_LIMITED`` when the scope has no room for another cached success.

    Called by the idempotency probe on a cache MISS, before any execution —
    a fresh key would insert a new row. Two bounds, both on the spec's
    (tenant, principal, account) scope (no tool dimension):

    - **insert rate** (the spec's MUST): at most :data:`MAX_INSERTS_PER_WINDOW`
      rows created within the trailing :data:`INSERT_RATE_WINDOW`;
      ``retry_after`` is when the oldest in-window insert leaves the window.
    - **active row count** (the derived storage bound): at most
      :data:`MAX_ACTIVE_ATTEMPTS_PER_SCOPE` non-expired rows; ``retry_after``
      is when the oldest active row expires.

    Replays and conflicts are not rate-limited — they insert nothing.
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
        retry_after = (
            max(1, math.ceil(window_seconds - (current - oldest_in_window).total_seconds())) if oldest_in_window else 1
        )
        # The wait can never logically exceed the window itself; the bound
        # also absorbs DB-vs-app clock skew on created_at (server_default).
        raise AdCPRateLimitError(
            "idempotency cache insert rate exceeded for this account — retry shortly",
            retry_after=min(retry_after, window_seconds, _RETRY_AFTER_MAX),
        )

    # Storage bound: ACTIVE (non-expired) rows.
    limit = ceiling if ceiling is not None else MAX_ACTIVE_ATTEMPTS_PER_SCOPE
    active, oldest_expiry = attempts.count_active(principal_id=principal_id, account_id=account_id, now=current)
    if active < limit:
        return

    retry_after = max(1, math.ceil((oldest_expiry - current).total_seconds())) if oldest_expiry else 1
    raise AdCPRateLimitError(
        "too many active idempotency keys for this account — retry after the oldest replay window expires",
        retry_after=min(retry_after, _RETRY_AFTER_MAX),
    )
