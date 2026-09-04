"""Unit tests for MediaBuyReadinessService state computation."""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

from src.admin.services.media_buy_readiness_service import MediaBuyReadinessService


def _media_buy(*, start_time: datetime, end_time: datetime, status: str = "active") -> MagicMock:
    mb = MagicMock()
    mb.status = status
    mb.start_time = start_time
    mb.end_time = end_time
    mb.start_date = None
    mb.end_date = None
    return mb


class TestComputeStateLiveGuard:
    def test_in_window_zero_creatives_not_live(self):
        """In-window buy with zero creatives must not report live (#1718 KM Aug3).

        Grades ``creatives_total > 0`` on the live branch: without it, draft
        (packages_total==0) satisfies ``0 == 0`` and falsely returns live
        before the draft check. With packages, has_blockers also blocks live.
        """
        now = datetime.now(UTC)
        media_buy = _media_buy(
            start_time=now - timedelta(days=1),
            end_time=now + timedelta(days=6),
        )
        state = MediaBuyReadinessService._compute_state(
            media_buy=media_buy,
            now=now,
            packages_total=0,
            packages_with_creatives=0,
            creatives_total=0,
            creatives_approved=0,
            creatives_pending=0,
            creatives_rejected=0,
            blocking_issues=[],
        )
        assert state != "live"
        assert state == "draft"
