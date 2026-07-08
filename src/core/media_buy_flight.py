"""Shared flight-window resolution for media buys.

The lifecycle-status DECISION differs by caller and deliberately stays in each
caller's module: the flight-date scheduler gates activation on creative approval
and reports "no change" (None); the admin approve route maps the window to
scheduled/active/completed; the creative-review path maps it to active/scheduled.
Those are genuinely different operations.

What they all shared — and what used to be copy-pasted three-plus ways — is
resolving a media buy's effective flight window to UTC-aware datetimes,
preferring the precise ``start_time``/``end_time`` over the date-only
``start_date``/``end_date``. Three divergent copies of that resolution is a
latent bug (one gets a tz fix, the others don't), so it lives here once. See
#1544.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


def _as_utc(value: datetime) -> datetime:
    """Normalize a naive-or-aware datetime to UTC-aware (naive is assumed UTC)."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def resolve_flight_window_utc(media_buy: Any) -> tuple[datetime | None, datetime | None]:
    """Resolve ``(start, end)`` flight bounds as UTC-aware datetimes.

    Prefers ``start_time``/``end_time``; falls back to ``start_date`` at the
    start of the day (00:00:00) and ``end_date`` at the end of the day
    (23:59:59.999999). A bound is ``None`` when neither its ``*_time`` nor
    ``*_date`` source is set.
    """
    start: datetime | None = None
    if media_buy.start_time:
        start = _as_utc(media_buy.start_time)
    elif media_buy.start_date:
        start = datetime.combine(media_buy.start_date, datetime.min.time(), tzinfo=UTC)

    end: datetime | None = None
    if media_buy.end_time:
        end = _as_utc(media_buy.end_time)
    elif media_buy.end_date:
        end = datetime.combine(media_buy.end_date, datetime.max.time(), tzinfo=UTC)

    return start, end
