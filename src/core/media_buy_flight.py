"""Shared flight-window resolution and status mapping for media buys.

Two business-logic primitives live here so no caller re-expresses them:

1. :func:`resolve_flight_window_utc` — resolve a media buy's effective flight
   window to UTC-aware datetimes, preferring the precise ``start_time``/
   ``end_time`` over the date-only ``start_date``/``end_date``.
2. :func:`lifecycle_status_for_window` — map ``(now, start, end)`` to the
   lifecycle status: ``scheduled`` before the window, ``completed`` after it,
   else ``active``. This is the window→status decision the admin approve route
   and the creative-review path both make; keeping it here means the Flask
   blueprints only orchestrate (resolve → decide → persist) instead of encoding
   the rule in the UI layer.

The flight-date scheduler is deliberately NOT expressed via
``lifecycle_status_for_window``: it is a different operation — an idempotent
state-machine step that returns "no change" (``None``) before start and gates
activation on creative approval — not a pure window→status mapping.

Three divergent copies of the resolution (or the mapping) is a latent bug (one
gets a tz/boundary fix, the others don't), so each lives here once. See #1544.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from src.core.utils import utc_flight_end, utc_flight_start


def _as_utc(value: datetime) -> datetime:
    """Normalize a naive-or-aware datetime to UTC-aware (naive is assumed UTC)."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def resolve_flight_window_utc(media_buy: Any) -> tuple[datetime | None, datetime | None]:
    """Resolve ``(start, end)`` flight bounds as UTC-aware datetimes.

    Prefers ``start_time``/``end_time``; falls back to the date-only
    ``start_date``/``end_date`` via the canonical day-bound helpers
    (:func:`src.core.utils.utc_flight_start` / :func:`utc_flight_end`), so there
    is a single definition of "start/end of a flight date". A bound is ``None``
    when neither its ``*_time`` nor ``*_date`` source is set.
    """
    start: datetime | None = None
    if media_buy.start_time:
        start = _as_utc(media_buy.start_time)
    elif media_buy.start_date:
        start = utc_flight_start(media_buy.start_date)

    end: datetime | None = None
    if media_buy.end_time:
        end = _as_utc(media_buy.end_time)
    elif media_buy.end_date:
        end = utc_flight_end(media_buy.end_date)

    return start, end


def lifecycle_status_for_window(now: datetime, start: datetime | None, end: datetime | None) -> str:
    """Map a resolved flight window to a lifecycle status.

    - ``scheduled`` while ``now`` is before ``start``;
    - ``completed`` once ``now`` is past ``end``;
    - ``active`` within the window (inclusive of both bounds).

    When the window is not fully bounded (either bound ``None`` — a buy with
    neither ``*_time`` nor ``*_date``), the buy is treated as ``active``: there
    is no start to wait for and no end to have passed. Callers pair this with
    :func:`resolve_flight_window_utc`; it is the single window→status decision
    the admin approve route and creative-review path share.
    """
    if start is None or end is None:
        return "active"
    if now < start:
        return "scheduled"
    if now > end:
        return "completed"
    return "active"
