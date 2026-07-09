"""UTC flight-window datetime helpers.

A media buy's flight window is persisted as ``date`` columns, but everything
that compares against a clock — the time-simulation hooks, adapter reporting
calls, the status schedulers — needs timezone-aware datetimes: the simulated
clock is always UTC-aware and a naive value raises ``TypeError: can't compare
offset-naive and offset-aware datetimes``.

These two helpers are the shared home for the date -> aware-datetime policy
(all reporting is UTC per the AdCP spec) across the core tools and schedulers:
the delivery tool, the update tool, and the status/webhook schedulers all use
them. Several admin/GAM call sites still open-code the same one-liner
(admin/blueprints/creatives.py, admin/blueprints/operations.py,
admin/services/media_buy_readiness_service.py, services/gam_orders_service.py) —
converting those is tracked separately (out of scope for the delivery-wire work).
"""

from __future__ import annotations

from datetime import UTC, date, datetime


def utc_flight_start(d: date) -> datetime:
    """The instant a flight date begins: midnight UTC of that date."""
    return datetime.combine(d, datetime.min.time(), tzinfo=UTC)


def utc_flight_end(d: date) -> datetime:
    """The last instant of a flight date: end-of-day UTC of that date."""
    return datetime.combine(d, datetime.max.time(), tzinfo=UTC)
