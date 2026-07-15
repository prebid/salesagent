"""Shared delivery-webhook test fixtures — data that must not drift across layers.

Two facts about a serving buy with a reporting webhook were being re-encoded
independently in the integration and BDD scheduler fixtures: the daily webhook
config and the flight-phase → date-window taxonomy. Hoisting both here makes the
phase→window contract live in ONE place, so ``"live"``/``"completed"`` grade the
same flight phase in every layer — the same derive-once discipline this feature
applies to production status sets. Move a window here and both suites move together;
there is no way for them to silently diverge under the same phase name.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

# The daily reporting_webhook config used by every delivery-webhook test. Callers
# that PERSIST it copy it (``dict(DAILY_REPORTING_WEBHOOK)``) to avoid aliasing the
# shared module-level dict into ORM state.
DAILY_REPORTING_WEBHOOK = {"url": "https://example.com/webhook", "frequency": "daily"}

# Flight phase → (start, end) day-offsets from ``today``. Single source of truth for
# the phase→window contract shared by the integration and BDD scheduler fixtures.
_FLIGHT_OFFSETS: dict[str, tuple[int, int]] = {
    "live": (-30, 30),  # spans today → resolves "active" → notification_type "scheduled"
    "completed": (-60, -30),  # ended before today → date-refines "completed" → "final"
}


def flight_window(phase: str, *, today: date | None = None) -> tuple[date, date]:
    """Return ``(start_date, end_date)`` for a named flight phase.

    ``live`` spans today (in-flight); ``completed`` ended before today. Callers that
    need ISO strings call ``.isoformat()`` on the returned dates.
    """
    if today is None:
        today = datetime.now(UTC).date()
    try:
        start_off, end_off = _FLIGHT_OFFSETS[phase]
    except KeyError:
        raise ValueError(f"unknown flight phase {phase!r}; expected one of {sorted(_FLIGHT_OFFSETS)}") from None
    return today + timedelta(days=start_off), today + timedelta(days=end_off)
