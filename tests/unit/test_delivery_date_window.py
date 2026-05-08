"""Tests for delivery-tool date window handling.

Covers tescoboy issues #149 and #170:

- #149 ``_normalize_reporting_window``: same-day ``start_date == end_date``
  was overwritten with ``now()`` and rejected as ``invalid_date_range``.
  AdCP defines ``start_date``/``end_date`` as inclusive date-only inputs,
  so same-day means the full 24-hour UTC day.

- #170 ``_clamp_target_date_to_now``: the freshness validator rejected any
  window whose ``end_date`` extended past "now". Buyer queries with
  future-dated ``end_date`` are legitimate; GAM cannot have data through
  a date that hasn't happened, so the call site clamps
  ``target_date = min(end, now)`` before validation.
"""

from datetime import UTC, datetime, timedelta

from src.adapters.google_ad_manager import _clamp_target_date_to_now
from src.core.tools.media_buy_delivery import _normalize_reporting_window


class TestNormalizeReportingWindow:
    """`_normalize_reporting_window` honors AdCP date semantics."""

    def test_same_day_returns_full_utc_day(self):
        start, end, valid = _normalize_reporting_window("2026-03-15", "2026-03-15")
        assert valid is True
        assert start == datetime(2026, 3, 15, 0, 0, 0, tzinfo=UTC)
        assert end == datetime(2026, 3, 15, 23, 59, 59, 999999, tzinfo=UTC)

    def test_multi_day_window(self):
        start, end, valid = _normalize_reporting_window("2026-03-10", "2026-03-15")
        assert valid is True
        assert start == datetime(2026, 3, 10, 0, 0, 0, tzinfo=UTC)
        assert end == datetime(2026, 3, 15, 23, 59, 59, 999999, tzinfo=UTC)

    def test_start_after_end_marked_invalid_but_echoes_buyer_input(self):
        """Per #149: error response must not lie about the queried range."""
        start, end, valid = _normalize_reporting_window("2026-03-20", "2026-03-10")
        assert valid is False
        # Buyer input echoed back rather than substituted with now()
        assert start == datetime(2026, 3, 20, 0, 0, 0, tzinfo=UTC)
        assert end == datetime(2026, 3, 10, 23, 59, 59, 999999, tzinfo=UTC)

    def test_default_window_when_both_dates_omitted(self):
        start, end, valid = _normalize_reporting_window(None, None)
        assert valid is True
        delta = end - start
        assert timedelta(days=29, hours=23) < delta < timedelta(days=30, hours=1)
        assert (datetime.now(UTC) - end) < timedelta(seconds=60)

    def test_only_start_supplied_falls_through_to_default(self):
        # Treat single-bound input as "missing range" — both must be present
        # to honor a buyer-defined window.
        start, end, valid = _normalize_reporting_window("2026-03-10", None)
        assert valid is True
        # 30-day default — buyer's start_date alone does not pin the window.
        assert (datetime.now(UTC) - end) < timedelta(seconds=60)

    def test_only_end_supplied_falls_through_to_default(self):
        start, end, valid = _normalize_reporting_window(None, "2026-03-15")
        assert valid is True
        assert (datetime.now(UTC) - end) < timedelta(seconds=60)


class TestClampTargetDateToNow:
    """`_clamp_target_date_to_now` clamps future end-dates back to now."""

    def test_future_end_date_clamps_to_now(self):
        future = datetime.now(UTC) + timedelta(days=1)
        clamped = _clamp_target_date_to_now(future)
        assert clamped < future
        assert (datetime.now(UTC) - clamped) < timedelta(seconds=60)

    def test_past_end_date_unchanged(self):
        past = datetime(2020, 1, 1, tzinfo=UTC)
        assert _clamp_target_date_to_now(past) == past

    def test_naive_future_clamps_to_naive_now(self):
        future = (datetime.now(UTC) + timedelta(days=1)).replace(tzinfo=None)
        clamped = _clamp_target_date_to_now(future)
        assert clamped.tzinfo is None
        assert clamped < future
