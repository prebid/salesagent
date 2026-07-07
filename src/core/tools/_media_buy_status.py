"""Shared persisted-status resolver for the two required read tools.

``get_media_buy_delivery`` and ``get_media_buys`` each map the persisted
``MediaBuy.status`` column onto a wire status vocabulary, refining generic
serving states against the flight window. That map + date-refinement is ONE
logical operation (CLAUDE.md DRY invariant), so it lives here once instead of
being mirrored in two modules where the copies drifted (delivery dropped
unmapped rows; list showed them).

Canonical output vocabulary — the media-buy lifecycle taxonomy plus the
delivery-only terminal ``failed``::

    pending_creatives, pending_start, active, paused, completed,
    rejected, canceled, failed

(``get-media-buy-delivery-response.json`` status enum; AdCP spec 3.1.0-beta.3.)
The two callers adapt this single result to their own surface:

- ``get_media_buy_delivery`` uses the canonical string directly and overlays
  the webhook-only ``reporting_delayed`` when the reporting circuit breaker is
  open.
- ``get_media_buys`` collapses ``failed`` to ``rejected`` (the lifecycle enum
  has no ``failed``) and converts to ``MediaBuyStatus``.

Both use the SAME map and the SAME date-refinement, so the same buy is
described identically by both tools (pinned by
``tests/unit/test_media_buy_status_consistency.py``).
"""

from __future__ import annotations

from datetime import date
from typing import Any, cast

# Generic serving state: the persisted value that means "delivering, subject to
# the flight window". These resolve to CANONICAL_SERVING and are then
# date-refined (pending_start before flight, active within, completed after).
CANONICAL_SERVING = "active"

# Terminal / explicit lifecycle decisions. These are authoritative and are
# NEVER re-derived from flight dates — a canceled buy inside its flight window
# is canceled, not active. ``paused`` is grouped here because it is likewise an
# explicit decision, not a date-derived state.
TERMINAL_STATUSES: frozenset[str] = frozenset({"paused", "completed", "rejected", "canceled", "failed"})

# Persisted ``MediaBuy.status`` -> canonical status. Written by
# media_buy_create.py, the lifecycle transitions, the status scheduler, and the
# admin blueprints. Includes the legacy aliases still resident in production
# rows so an existing buy is never dropped:
#   - "ready" (PR #375): approved & scheduled to go live at flight start.
#   - "scheduled" / "pending_activation": admin/scheduler pre-serving states.
# All three denote an approved buy whose serving is date-gated, so they resolve
# to the generic serving state and date-refine exactly like "active".
PERSISTED_STATUS_TO_CANONICAL: dict[str, str] = {
    "active": "active",
    "approved": "active",
    "ready": "active",
    "scheduled": "active",
    "pending_activation": "active",
    "paused": "paused",
    "completed": "completed",
    "rejected": "rejected",
    "canceled": "canceled",
    "failed": "failed",
    "draft": "pending_creatives",
    "pending": "pending_start",
    "pending_approval": "pending_start",
    "pending_creatives": "pending_creatives",
    "pending_start": "pending_start",
}

# The complete set of values ``resolve_canonical_status`` may return. Used by
# get_media_buy_delivery as its valid internal-filter vocabulary.
CANONICAL_STATUSES: frozenset[str] = frozenset(
    {"pending_creatives", "pending_start", "active", "paused", "completed", "rejected", "canceled", "failed"}
)


def resolve_canonical_status(buy: Any, reference_date: date, *, simulate: bool = False) -> str:
    """Resolve a media buy's canonical status from its persisted column.

    The persisted ``MediaBuy.status`` is the source of truth. A generic serving
    state ("active"/"approved" and the legacy scheduled aliases) is refined
    against the flight window; a terminal/explicit state (paused, completed,
    rejected, canceled, failed) is returned verbatim.

    An *unmapped* persisted status is treated as a generic serving state and
    date-refined — never returned verbatim and never dropped — so a buy that
    exists is always describable. (Regression: the delivery copy passed unknown
    values through, which then failed its internal-status filter and made even
    fetch-by-ID report ``MEDIA_BUY_NOT_FOUND`` for a buy that exists.)

    Args:
        buy: A media buy exposing ``status``, ``start_date``/``end_date``,
            optional ``start_time``/``end_time``, and ``is_paused``.
        reference_date: The date the status is evaluated against (the request's
            end date, or the simulated clock in time-simulation mode).
        simulate: When True (time-simulation via ``mock_time`` / ``jump_to_event``),
            any NON-terminal persisted state also follows the flight window so a
            buyer can observe the full lifecycle (pending -> active -> completed)
            and reach the "final" delivery notification. Terminal/explicit
            decisions are still preserved — simulation must not resurrect a buy
            the seller deliberately stopped.

    Returns:
        One of ``CANONICAL_STATUSES``.
    """
    persisted = (buy.status or "").lower()
    canonical = PERSISTED_STATUS_TO_CANONICAL.get(persisted, CANONICAL_SERVING)

    should_refine = canonical == CANONICAL_SERVING or (simulate and canonical not in TERMINAL_STATUSES)
    if not should_refine:
        return canonical

    # Generic serving state (or a simulated non-terminal state) — refine against
    # the flight window. Prefer the precise start_time/end_time when present.
    # Cast to date to satisfy mypy (SQLAlchemy returns Python date at runtime).
    start_time = getattr(buy, "start_time", None)
    end_time = getattr(buy, "end_time", None)
    start_compare = start_time.date() if start_time else cast(date, buy.start_date)
    end_compare = end_time.date() if end_time else cast(date, buy.end_date)

    if getattr(buy, "is_paused", False):
        return "paused"
    if reference_date < start_compare:
        return "pending_start"
    if reference_date > end_compare:
        return "completed"
    return "active"
