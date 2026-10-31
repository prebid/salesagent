"""The cooldown a pinned ``FrequencyCap`` expresses, in minutes.

Two adapters translate a buyer's cap into a vendor's minute-or-hour cooldown field --
GAM's ``frequencyCaps[].numTimeUnits``/``timeUnit`` and Kevel's ``FreqCapDuration`` -- and
both read ``FrequencyCap.suppress_minutes`` directly. That is wrong twice over against
``core/frequency-cap.json``:

* ``suppress_minutes`` is ``number | null`` and DEPRECATED. The pin's preferred spelling is
  ``suppress``, a ``core/duration.json`` object (``{interval, unit}``). An adapter reading
  only the deprecated field ignores every cap sent the preferred way.
* The schema requires at least one of ``suppress``, ``suppress_minutes`` or
  ``max_impressions``, so a cap carrying ONLY ``max_impressions`` is valid and has no
  cooldown at all. Both adapters compared ``None`` against an int for such a cap
  (``freq_cap.suppress_minutes < 60`` / ``// 60``), which is a ``TypeError`` out of the
  adapter rather than a line item.

One function so the two cannot drift, per CLAUDE.md § DRY: the duplicated conversion is
what let the same two defects exist in both copies.

CEILING: a ``suppress`` whose unit is ``campaign`` is a cooldown spanning the whole flight,
which neither vendor field can express as a finite number of minutes, so it reads as "no
minute-expressible cooldown" here and the caller omits the vendor field. Upgrade path: a
typed refusal, once a spec-grounded code is settled for a cap this seller cannot enforce
(today it would mean inventing one, which CLAUDE.md pattern #10 refuses).
"""

from __future__ import annotations

from typing import Any

#: Minutes per ``core/duration.json`` unit. ``campaign`` is deliberately absent -- it names
#: the flight rather than a span, so it has no minute value (see the module's CEILING note).
_MINUTES_PER_UNIT: dict[str, float] = {
    "seconds": 1 / 60,
    "minutes": 1.0,
    "hours": 60.0,
    "days": 1440.0,
}


def suppress_cooldown_minutes(cap: Any) -> float | None:
    """The cap's cooldown between consecutive exposures, in minutes.

    Reads the pin's preferred ``suppress`` duration first and falls back to the deprecated
    ``suppress_minutes``, so a cap sent either way converts. Returns ``None`` when the cap
    expresses no cooldown a vendor's minute field can carry: a ``max_impressions``-only cap,
    or a ``suppress`` measured in ``campaign`` units.

    ``cap`` is typed ``Any`` because the two pinned spellings of this object --
    ``core/frequency-cap.json`` under a targeting overlay and ``core/media-buy-frequency-cap
    .json`` at the request root -- generate two classes, and a persisted overlay may hand
    back the stored document rather than a model. Both are read by attribute or key, never
    serialized.
    """
    suppress = _member(cap, "suppress")
    if suppress is not None:
        unit = _member(suppress, "unit")
        interval = _member(suppress, "interval")
        # ``unit`` may arrive as the generated enum rather than a bare string; its value is
        # the schema's token either way.
        minutes = _MINUTES_PER_UNIT.get(str(getattr(unit, "value", unit)))
        if minutes is not None and interval is not None:
            return float(interval) * minutes

    suppress_minutes = _member(cap, "suppress_minutes")
    return float(suppress_minutes) if suppress_minutes is not None else None


def _member(obj: Any, name: str) -> Any:
    """``name`` off a model or off a stored document, whichever arrived."""
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)
