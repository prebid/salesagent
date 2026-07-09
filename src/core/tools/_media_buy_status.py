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

Both use the SAME map and the SAME date-refinement, so a buy evaluated against
the SAME reference date gets the SAME status from either tool (pinned by
``tests/unit/test_media_buy_status_consistency.py``, which feeds both a shared
reference date). The two tools do NOT always pass the same reference date in
production, though: ``get_media_buys`` refines against *today*
(``media_buy_list.py``), while ``get_media_buy_delivery`` refines against the
request's *end_date* (``media_buy_delivery.py``) — current-state vs
period-scoped. So for a serving buy near its flight boundary the two may
legitimately report different date-refined statuses; the mapping is identical,
the reference date is the buyer-visible difference. Under time simulation
(``mock_time`` / ``jump_to_event``) only ``get_media_buy_delivery`` advances the
clock, a further legitimate divergence.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

logger = logging.getLogger(__name__)

# NOTE: ``buy`` is typed ``Any`` rather than a structural Protocol because the
# ORM ``MediaBuy`` annotates its date columns ``Mapped[Date]`` (the SQLAlchemy
# type, not Python ``date``), so no Protocol matches it without first correcting
# the model annotations (out of scope here).

# Generic serving state: the persisted value that means "delivering, subject to
# the flight window". These resolve to CANONICAL_SERVING and are then
# date-refined (pending_start before flight, active within, completed after).
CANONICAL_SERVING = "active"

# Terminal / explicit lifecycle decisions. These are authoritative and are
# NEVER re-derived from flight dates — a canceled buy inside its flight window
# is canceled, not active. ``paused`` is grouped here because it is likewise an
# explicit decision, not a date-derived state.
TERMINAL_STATUSES: frozenset[str] = frozenset({"paused", "completed", "rejected", "canceled", "failed"})

# Statuses after which no further delivery data will ever arrive. A buy in one
# of these states gets its LAST notification: ``notification_type = "final"``
# and no ``next_expected_at`` (the spec pins next_expected_at as "only present
# ... when notification_type is not 'final'" — get-media-buy-delivery-response.json
# @ v3.1-04f59d2d5 — and promises "one final notification when the campaign
# completes" — optimization-reporting.mdx §Publisher Commitment). Derived from
# TERMINAL_STATUSES minus ``paused``: pausing is an explicit decision but the
# buy may resume and report again, so a next scheduled report is still a
# truthful promise for it.
NO_MORE_DATA_STATUSES: frozenset[str] = TERMINAL_STATUSES - {"paused"}

# Persisted ``MediaBuy.status`` -> canonical status. Written by
# media_buy_create.py, the lifecycle transitions, the status scheduler, and the
# admin blueprints. Includes the legacy aliases still resident in production
# rows so an existing buy is never dropped:
#   - "ready" / "scheduled": approved buys whose serving is purely date-gated —
#     they resolve to the generic serving state and date-refine exactly like
#     "active".
#   - "pending_activation": held un-promoted by the status scheduler until
#     creatives are approved (media_buy_status_scheduler.py), exactly like
#     "pending_start" — so it maps to "pending_start", NOT the serving state.
#     Date-refining it to "active" made a past-start buy with unapproved
#     creatives read as serving.
#   - "pending_approval" -> "pending_start" (INTERPRETATION, graded but not
#     spec-mandated): AdCP 3.1 has no "awaiting seller approval" wire status. Of
#     the two pre-serving states, pending_start ("ready to serve, waiting for its
#     flight date") is the closest — a buy the seller has yet to accept is not
#     "serving" and has no creatives gap to report (that is pending_creatives).
#     The literal reading of pending_start ("ready") slightly overstates an
#     awaiting-approval buy; the spec offers no better pre-serving bucket. If a
#     future spec adds an approval-queue status, revisit this row.
PERSISTED_STATUS_TO_CANONICAL: dict[str, str] = {
    "active": "active",
    "approved": "active",
    "ready": "active",
    "scheduled": "active",
    "pending_activation": "pending_start",
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

# The complete set of values ``resolve_canonical_status`` may return, derived
# from the map so the two can never drift. Used by get_media_buy_delivery as its
# valid internal-filter vocabulary. Its equivalence to the pinned SDK
# ``MediaBuyStatus`` enum (plus the delivery-only ``failed``) is pinned by
# ``tests/unit/test_media_buy_status_consistency.py`` so an SDK bump that widens
# the lifecycle enum fails loudly instead of silently making a new status
# unfilterable.
CANONICAL_STATUSES: frozenset[str] = frozenset(PERSISTED_STATUS_TO_CANONICAL.values())


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
    if persisted and persisted not in PERSISTED_STATUS_TO_CANONICAL:
        # Not a failure (the buy is still described, date-refined as serving), but
        # a writer has introduced a persisted value this map doesn't know about —
        # surface it so the map can be updated rather than silently guessing.
        logger.warning("Unmapped persisted media-buy status %r; treating as serving state", persisted)
    canonical = PERSISTED_STATUS_TO_CANONICAL.get(persisted, CANONICAL_SERVING)

    should_refine = canonical == CANONICAL_SERVING or (simulate and canonical not in TERMINAL_STATUSES)
    if not should_refine:
        return canonical

    # Generic serving state (or a simulated non-terminal state) — refine against
    # the flight window. Prefer the precise start_time/end_time when present.
    start_time = getattr(buy, "start_time", None)
    end_time = getattr(buy, "end_time", None)
    start_compare = start_time.date() if start_time else buy.start_date
    end_compare = end_time.date() if end_time else buy.end_date

    if getattr(buy, "is_paused", False):
        return "paused"
    if reference_date < start_compare:
        return "pending_start"
    if reference_date > end_compare:
        return "completed"
    return "active"
