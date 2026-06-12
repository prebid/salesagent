"""Regression tests for stale background inventory sync detection."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

from src.services.background_sync_service import _is_stale_running_sync


def _sync_job(*, started_at: datetime, progress: dict | None = None):
    job = MagicMock()
    job.started_at = started_at
    job.progress = progress
    return job


def test_stale_running_sync_with_progress_is_stale():
    """A stale phase payload does not prove the background thread is alive."""
    now = datetime(2026, 6, 9, 0, 0, tzinfo=UTC)
    job = _sync_job(
        started_at=now - timedelta(hours=2),
        progress={"phase": "Discovering Ad Units", "phase_num": 2},
    )

    assert _is_stale_running_sync(job, now=now) is True


def test_recent_running_sync_with_progress_is_not_stale():
    now = datetime(2026, 6, 9, 0, 0, tzinfo=UTC)
    job = _sync_job(
        started_at=now - timedelta(minutes=30),
        progress={"phase": "Discovering Ad Units", "phase_num": 2},
    )

    assert _is_stale_running_sync(job, now=now) is False


def test_naive_started_at_is_treated_as_utc():
    now = datetime(2026, 6, 9, 0, 0, tzinfo=UTC)
    job = _sync_job(started_at=datetime(2026, 6, 8, 22, 0))

    assert _is_stale_running_sync(job, now=now) is True
