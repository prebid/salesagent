"""UTC flight-window datetime helpers.

A media buy's flight window is persisted as ``date`` columns, but everything
that compares against a clock — the time-simulation hooks, adapter reporting
calls, the status schedulers — needs timezone-aware datetimes: the simulated
clock is always UTC-aware and a naive value raises ``TypeError: can't compare
offset-naive and offset-aware datetimes``.

These two helpers are the single home for the date -> aware-datetime policy
(all reporting is UTC per the AdCP spec). Before extraction the same
one-liners were open-coded across the delivery tool, the update tool, and the
schedulers, so a timezone-policy change had to find every copy by hand.
"""

from __future__ import annotations

from datetime import UTC, date, datetime


def utc_flight_start(d: date) -> datetime:
    """The instant a flight date begins: midnight UTC of that date."""
    return datetime.combine(d, datetime.min.time(), tzinfo=UTC)


def utc_flight_end(d: date) -> datetime:
    """The last instant of a flight date: end-of-day UTC of that date."""
    return datetime.combine(d, datetime.max.time(), tzinfo=UTC)
