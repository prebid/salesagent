"""Unit tests for the shared media-buy flight-window helpers.

Covers ``resolve_flight_window_utc`` (window resolution + tz normalization) and
``lifecycle_status_for_window`` (the window→status decision the admin approve
route and creative-review path share). See #1544.
"""

from datetime import UTC, date, datetime, timedelta, timezone

from src.core.media_buy_flight import lifecycle_status_for_window, resolve_flight_window_utc


class _Buy:
    """Minimal duck-typed stand-in for a MediaBuy row."""

    def __init__(self, *, start_time=None, end_time=None, start_date=None, end_date=None):
        self.start_time = start_time
        self.end_time = end_time
        self.start_date = start_date
        self.end_date = end_date


class TestResolveFlightWindowUtc:
    def test_prefers_times_over_dates(self):
        st = datetime(2026, 1, 1, 9, 0, tzinfo=UTC)
        et = datetime(2026, 2, 1, 17, 0, tzinfo=UTC)
        start, end = resolve_flight_window_utc(
            _Buy(start_time=st, end_time=et, start_date=date(2020, 1, 1), end_date=date(2020, 1, 2))
        )
        assert (start, end) == (st, et)

    def test_falls_back_to_dates_at_day_bounds(self):
        start, end = resolve_flight_window_utc(_Buy(start_date=date(2026, 1, 1), end_date=date(2026, 12, 31)))
        assert start == datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
        assert end == datetime(2026, 12, 31, 23, 59, 59, 999999, tzinfo=UTC)

    def test_naive_datetime_assumed_utc(self):
        start, _ = resolve_flight_window_utc(_Buy(start_time=datetime(2026, 1, 1, 12, 0)))
        assert start == datetime(2026, 1, 1, 12, 0, tzinfo=UTC)

    def test_aware_non_utc_converted_to_utc(self):
        # 09:00 at UTC-5 == 14:00 UTC
        st = datetime(2026, 1, 1, 9, 0, tzinfo=timezone(timedelta(hours=-5)))
        start, _ = resolve_flight_window_utc(_Buy(start_time=st))
        assert start == datetime(2026, 1, 1, 14, 0, tzinfo=UTC)

    def test_missing_bounds_are_none(self):
        assert resolve_flight_window_utc(_Buy()) == (None, None)


class TestLifecycleStatusForWindow:
    def setup_method(self):
        self.now = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)

    def test_before_start_is_scheduled(self):
        assert (
            lifecycle_status_for_window(self.now, self.now + timedelta(days=1), self.now + timedelta(days=2))
            == "scheduled"
        )

    def test_after_end_is_completed(self):
        assert (
            lifecycle_status_for_window(self.now, self.now - timedelta(days=2), self.now - timedelta(days=1))
            == "completed"
        )

    def test_within_window_is_active(self):
        assert (
            lifecycle_status_for_window(self.now, self.now - timedelta(days=1), self.now + timedelta(days=1))
            == "active"
        )

    def test_boundaries_are_inclusive_active(self):
        assert lifecycle_status_for_window(self.now, self.now, self.now + timedelta(days=1)) == "active"
        assert lifecycle_status_for_window(self.now, self.now - timedelta(days=1), self.now) == "active"

    def test_unbounded_window_is_active(self):
        assert lifecycle_status_for_window(self.now, None, None) == "active"
        assert lifecycle_status_for_window(self.now, None, self.now + timedelta(days=1)) == "active"
        assert lifecycle_status_for_window(self.now, self.now - timedelta(days=1), None) == "active"

    def test_composes_with_resolution(self):
        # End-to-end: a past-end buy resolves to completed (the corrected
        # creative-review behavior — previously "scheduled").
        buy = _Buy(start_date=date(2026, 1, 1), end_date=date(2026, 1, 31))
        assert lifecycle_status_for_window(self.now, *resolve_flight_window_utc(buy)) == "completed"
