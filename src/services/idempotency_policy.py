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

from src.core.config import get_settings

if TYPE_CHECKING:
    from src.core.database.repositories.idempotency_attempt import IdempotencyAttemptRepository

# The two bounds, read off the settings inside enforce_insert_ceiling on each call rather
# than snapshotted at import, so the value a test loads is the value the policy applies:
#
# - Storage-abuse ceiling (LimitSettings.idempotency_max_active_attempts_per_scope):
#   active (non-expired) cached successes per (tenant, principal, account) scope. Each
#   keyed create stores one row for the replay TTL, so a buyer minting fresh keys is
#   bounded to this many creates per window; the probe rejects the excess as
#   RATE_LIMITED with retry_after set to when the oldest row expires.
# - Insert-RATE limit (idempotency_insert_rate_window_seconds and
#   idempotency_max_inserts_per_window) per the same scope: the spec's MUST is a rate
#   limit on cache inserts (the row count above is the derived storage bound). The
#   defaults follow the spec's SHOULD-level burst numbers (300 inserts per 10s).


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

    - **insert rate** (the spec's MUST): at most ``idempotency_max_inserts_per_window``
      rows created within the trailing ``idempotency_insert_rate_window_seconds``;
      ``retry_after`` is when the oldest in-window insert leaves the window.
    - **active row count** (the derived storage bound): at most
      ``idempotency_max_active_attempts_per_scope`` non-expired rows; ``retry_after``
      is when the oldest active row expires.

    Replays and conflicts are not rate-limited — they insert nothing.
    ``retry_after`` is clamped to the spec Error model's [1, 3600] bound.
    """
    from src.core.exceptions import AdCPRateLimitError, clamp_retry_after

    settings = get_settings()
    limits = settings.limits
    current = now or datetime.now(UTC)

    # Insert-rate bound: rows CREATED inside the trailing window, expired or not.
    rate_limit = rate_ceiling if rate_ceiling is not None else limits.idempotency_max_inserts_per_window
    window = timedelta(seconds=limits.idempotency_insert_rate_window_seconds)
    window_start = current - window
    recent, oldest_in_window = attempts.count_inserts_since(
        principal_id=principal_id, account_id=account_id, since=window_start
    )
    if recent >= rate_limit:
        window_seconds = math.ceil(window.total_seconds())
        raw_wait = window_seconds - (current - oldest_in_window).total_seconds() if oldest_in_window else 1
        # The wait can never logically exceed the window itself; the bound
        # also absorbs DB-vs-app clock skew on created_at (server_default).
        # No message argument: buyer-facing text for RATE_LIMITED comes from
        # CODE_TABLE via the read-only ``message`` property, so no raise site
        # authors it. The clamp helper lives in exceptions beside
        # ``retry_after`` and is shared with the egress seam.
        raise AdCPRateLimitError(
            retry_after=min(clamp_retry_after(raw_wait), window_seconds),
        )

    # Storage bound: ACTIVE (non-expired) rows.
    limit = ceiling if ceiling is not None else limits.idempotency_max_active_attempts_per_scope
    active, oldest_expiry = attempts.count_active(principal_id=principal_id, account_id=account_id, now=current)
    if active < limit:
        return

    raw_wait = (oldest_expiry - current).total_seconds() if oldest_expiry else 1
    raise AdCPRateLimitError(
        retry_after=clamp_retry_after(raw_wait),
    )
