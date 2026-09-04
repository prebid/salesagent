"""Unit tests: status scheduler per-buy SAVEPOINT isolation and breaker escape.

Durable-commit and sibling-survival guarantees are graded by the real-Postgres
twin ``test_raising_buy_does_not_abort_remaining_status_flips``
(``tests/integration/test_media_buy_status_scheduler.py``) — these unit tests
pin only what that twin cannot cheaply express without a real DB: the
re-raise/escape control flow routing to the outer log, and the
``scheduler="media_buy_status"`` metric label.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.exc import OperationalError

import src.services.media_buy_status_scheduler as status_mod
from src.core.metrics import scheduler_isolation_errors
from src.services.media_buy_status_scheduler import STATUS_BATCH_SUMMARY_PREFIX, MediaBuyStatusScheduler
from tests.helpers.scheduler_isolation import (
    counter_value,
    mock_get_db_session_cm,
    mock_media_buy,
    mock_savepoint_session,
    summary_lines,
)


@pytest.mark.asyncio
async def test_savepoint_release_failure_counts_as_isolated_not_flipped():
    """R10-1.4 unit twin: release-time DataError is ISOLATED, not FLIPPED."""
    from sqlalchemy.exc import DataError

    buys = [mock_media_buy(media_buy_id=f"mb_{i}", tenant_id=f"t{i}") for i in range(3)]
    release_exc = DataError("UPDATE media_buys", {}, Exception("value too long"))
    session = mock_savepoint_session(release_exc=release_exc)
    cm = mock_get_db_session_cm(session)
    scheduler = MediaBuyStatusScheduler()

    with (
        patch("src.services.media_buy_status_scheduler.get_db_session", return_value=cm),
        patch(
            "src.services.media_buy_status_scheduler.MediaBuyRepository.get_all_by_statuses",
            return_value=buys,
        ),
        patch.object(scheduler, "_compute_new_status", return_value="completed"),
        patch.object(status_mod.logger, "warning") as mock_warning,
        patch.object(status_mod.logger, "info") as mock_info,
    ):
        await scheduler._update_statuses()

    warn_lines = summary_lines(mock_warning, STATUS_BATCH_SUMMARY_PREFIX)
    assert warn_lines == [f"{STATUS_BATCH_SUMMARY_PREFIX}: 0 updated, 3 errors"]
    assert summary_lines(mock_info, STATUS_BATCH_SUMMARY_PREFIX) == []
    assert summary_lines(mock_info, STATUS_BATCH_SUMMARY_PREFIX, needle="Updated media buy ") == []
    session.commit.assert_not_called()


@pytest.mark.asyncio
async def test_status_scheduler_connection_invalidated_reraises():
    """Adoption oracle: invalidated OperationalError re-raises out of the batch.

    Breaker arming against the real ``get_db_session`` CM is graded by the
    integration twin; this unit test pins the re-raise half via the
    branch-distinguishing outer log (not the per-item isolate log).
    """
    buy = mock_media_buy(media_buy_id="mb1", tenant_id="t-breaker")
    session = mock_savepoint_session()
    cm = mock_get_db_session_cm(session)

    scheduler = MediaBuyStatusScheduler()

    def _raise_invalidated(_media_buy, _now, _session):
        raise OperationalError("SELECT 1", {}, Exception("gone"), connection_invalidated=True)

    scheduler_isolation_errors.clear()
    metric_before = counter_value("media_buy_status", "t-breaker", "db_error")

    with (
        patch("src.services.media_buy_status_scheduler.get_db_session", return_value=cm),
        patch(
            "src.services.media_buy_status_scheduler.MediaBuyRepository.get_all_by_statuses",
            return_value=[buy],
        ),
        patch.object(scheduler, "_compute_new_status", side_effect=_raise_invalidated),
        patch.object(status_mod.logger, "error") as mock_error,
    ):
        await scheduler._update_statuses()

    error_msgs = [str(c.args[0]) for c in mock_error.call_args_list if c.args]
    assert any("Failed to update media buy statuses" in msg for msg in error_msgs)
    assert not any("Error updating media buy status" in msg for msg in error_msgs)
    assert counter_value("media_buy_status", "t-breaker", "db_error") == metric_before


@pytest.mark.asyncio
async def test_status_isolates_one_failure_and_meters_once():
    """Pins the ``scheduler="media_buy_status"`` metric label and per-tenant
    non-metering of siblings — not durable commit (mocked session/objects
    can't grade that; the real-DB twin does)."""
    buys = [
        mock_media_buy(media_buy_id="mb_a", tenant_id="tenant-ok-a", principal_id="p-mb_a"),
        mock_media_buy(media_buy_id="mb_fail", tenant_id="tenant-fail", principal_id="p-mb_fail"),
        mock_media_buy(media_buy_id="mb_b", tenant_id="tenant-ok-b", principal_id="p-mb_b"),
    ]
    session = mock_savepoint_session()
    cm = mock_get_db_session_cm(session)

    scheduler = MediaBuyStatusScheduler()

    def _compute(media_buy, _now, _session):
        if media_buy.media_buy_id == "mb_fail":
            raise OperationalError("SELECT 1", {}, Exception("timeout"))
        return "completed"

    scheduler_isolation_errors.clear()
    fail_before = counter_value("media_buy_status", "tenant-fail", "db_error")

    with (
        patch("src.services.media_buy_status_scheduler.get_db_session", return_value=cm),
        patch(
            "src.services.media_buy_status_scheduler.MediaBuyRepository.get_all_by_statuses",
            return_value=buys,
        ),
        patch.object(scheduler, "_compute_new_status", side_effect=_compute),
        patch.object(status_mod.logger, "error") as mock_error,
    ):
        await scheduler._update_statuses()

    assert mock_error.call_count == 1
    assert mock_error.call_args.kwargs.get("exc_info") is True
    assert "tenant_id=tenant-fail" in mock_error.call_args.args[0]

    assert counter_value("media_buy_status", "tenant-fail", "db_error") == fail_before + 1
    # Siblings must not be metered.
    assert counter_value("media_buy_status", "tenant-ok-a", "db_error") == 0


@pytest.mark.asyncio
async def test_batch_summary_warns_when_every_visited_buy_fails():
    """A1: ``errors == seen`` must log WARNING (not INFO)."""
    buys = [mock_media_buy(media_buy_id=f"mb_{i}", tenant_id=f"t{i}") for i in range(3)]
    session = mock_savepoint_session()
    cm = mock_get_db_session_cm(session)
    scheduler = MediaBuyStatusScheduler()

    def _always_fail(_media_buy, _now, _session):
        raise OperationalError("SELECT 1", {}, Exception("timeout"))

    with (
        patch("src.services.media_buy_status_scheduler.get_db_session", return_value=cm),
        patch(
            "src.services.media_buy_status_scheduler.MediaBuyRepository.get_all_by_statuses",
            return_value=buys,
        ),
        patch.object(scheduler, "_compute_new_status", side_effect=_always_fail),
        patch.object(status_mod.logger, "warning") as mock_warning,
        patch.object(status_mod.logger, "info") as mock_info,
    ):
        await scheduler._update_statuses()

    warn_lines = summary_lines(mock_warning, STATUS_BATCH_SUMMARY_PREFIX)
    assert warn_lines == [f"{STATUS_BATCH_SUMMARY_PREFIX}: 0 updated, 3 errors"]
    assert summary_lines(mock_info, STATUS_BATCH_SUMMARY_PREFIX) == []


@pytest.mark.asyncio
async def test_batch_summary_info_when_noop_plus_one_raiser():
    """R10-2: mid-flight NOOPs + one ISOLATED must INFO, not WARNING.

    Discriminates ``errors == seen`` from the pre-seen rule
    ``errors and not updated_count`` (which would false-page every 60s).
    """
    buys = [mock_media_buy(media_buy_id=f"mb_{i}", tenant_id=f"t{i}") for i in range(5)]
    session = mock_savepoint_session()
    cm = mock_get_db_session_cm(session)
    scheduler = MediaBuyStatusScheduler()

    def _noop_or_raise(media_buy, _now, _session):
        if media_buy.media_buy_id == "mb_4":
            raise OperationalError("SELECT 1", {}, Exception("timeout"))

    with (
        patch("src.services.media_buy_status_scheduler.get_db_session", return_value=cm),
        patch(
            "src.services.media_buy_status_scheduler.MediaBuyRepository.get_all_by_statuses",
            return_value=buys,
        ),
        patch.object(scheduler, "_compute_new_status", side_effect=_noop_or_raise),
        patch.object(status_mod.logger, "warning") as mock_warning,
        patch.object(status_mod.logger, "info") as mock_info,
    ):
        await scheduler._update_statuses()

    assert summary_lines(mock_info, STATUS_BATCH_SUMMARY_PREFIX) == [
        f"{STATUS_BATCH_SUMMARY_PREFIX}: 0 updated, 1 errors"
    ]
    assert summary_lines(mock_warning, STATUS_BATCH_SUMMARY_PREFIX) == []


@pytest.mark.asyncio
async def test_lazy_id_failure_isolates_and_siblings_still_flip():
    """R10-3a: id reads inside the per-buy try keep a lazy-id failure contained."""

    class _LazyIdBuy:
        media_buy_id = "mb_lazy"
        principal_id = "p-lazy"
        status = "active"

        @property
        def tenant_id(self) -> str:
            raise RuntimeError("lazy tenant_id")

    buys = [
        _LazyIdBuy(),
        mock_media_buy(media_buy_id="mb_ok_a", tenant_id="t-ok-a"),
        mock_media_buy(media_buy_id="mb_ok_b", tenant_id="t-ok-b"),
    ]
    session = mock_savepoint_session()
    cm = mock_get_db_session_cm(session)
    scheduler = MediaBuyStatusScheduler()

    def _compute(media_buy, _now, _session):
        if getattr(media_buy, "media_buy_id", None) == "mb_lazy":
            raise AssertionError("compute must not run after lazy-id failure")
        return "completed"

    with (
        patch("src.services.media_buy_status_scheduler.get_db_session", return_value=cm),
        patch(
            "src.services.media_buy_status_scheduler.MediaBuyRepository.get_all_by_statuses",
            return_value=buys,
        ),
        patch.object(scheduler, "_compute_new_status", side_effect=_compute),
        patch.object(status_mod.logger, "error") as mock_error,
        patch.object(status_mod.logger, "info") as mock_info,
    ):
        await scheduler._update_statuses()

    err_msgs = [str(c.args[0]) for c in mock_error.call_args_list if c.args]
    assert any("Error updating media buy status" in msg for msg in err_msgs)
    assert not any("Failed to update media buy statuses" in msg for msg in err_msgs)
    session.commit.assert_called_once_with()
    flip_lines = summary_lines(mock_info, STATUS_BATCH_SUMMARY_PREFIX, needle="Updated media buy ")
    assert len(flip_lines) == 2


@pytest.mark.asyncio
async def test_nested_handler_failure_stays_isolated():
    """R10-3b: handler boom must not escape the per-buy iteration."""
    buys = [
        mock_media_buy(media_buy_id="mb_fail", tenant_id="t-fail"),
        mock_media_buy(media_buy_id="mb_ok", tenant_id="t-ok"),
    ]
    session = mock_savepoint_session()
    cm = mock_get_db_session_cm(session)
    scheduler = MediaBuyStatusScheduler()

    def _compute(media_buy, _now, _session):
        if media_buy.media_buy_id == "mb_fail":
            raise OperationalError("SELECT 1", {}, Exception("timeout"))
        return "completed"

    with (
        patch("src.services.media_buy_status_scheduler.get_db_session", return_value=cm),
        patch(
            "src.services.media_buy_status_scheduler.MediaBuyRepository.get_all_by_statuses",
            return_value=buys,
        ),
        patch.object(scheduler, "_compute_new_status", side_effect=_compute),
        patch(
            "src.services.media_buy_status_scheduler.record_scheduler_isolation_error",
            side_effect=RuntimeError("handler boom"),
        ),
        patch.object(status_mod.logger, "error") as mock_error,
        patch.object(status_mod.logger, "info") as mock_info,
    ):
        await scheduler._update_statuses()

    err_msgs = [str(c.args[0]) for c in mock_error.call_args_list if c.args]
    assert any("Status isolation error handler failed" in msg for msg in err_msgs)
    assert not any("Failed to update media buy statuses" in msg for msg in err_msgs)
    session.commit.assert_called_once_with()
    flip_lines = summary_lines(mock_info, STATUS_BATCH_SUMMARY_PREFIX, needle="Updated media buy ")
    assert len(flip_lines) == 1


@pytest.mark.asyncio
async def test_batch_summary_warns_on_escape_with_only_prior_errors():
    """R10-5: escape after isolations (zero flips) must WARNING, not INFO."""
    buys = [
        mock_media_buy(media_buy_id="mb_a", tenant_id="t-a"),
        mock_media_buy(media_buy_id="mb_b", tenant_id="t-b"),
        mock_media_buy(media_buy_id="mb_c", tenant_id="t-c"),
        mock_media_buy(media_buy_id="mb_dead", tenant_id="t-dead"),
    ]
    session = mock_savepoint_session()
    cm = mock_get_db_session_cm(session)
    scheduler = MediaBuyStatusScheduler()

    def _compute(media_buy, _now, _session):
        if media_buy.media_buy_id == "mb_dead":
            raise OperationalError("SELECT 1", {}, Exception("gone"), connection_invalidated=True)
        raise OperationalError("SELECT 1", {}, Exception("timeout"))

    with (
        patch("src.services.media_buy_status_scheduler.get_db_session", return_value=cm),
        patch(
            "src.services.media_buy_status_scheduler.MediaBuyRepository.get_all_by_statuses",
            return_value=buys,
        ),
        patch.object(scheduler, "_compute_new_status", side_effect=_compute),
        patch.object(status_mod.logger, "warning") as mock_warning,
        patch.object(status_mod.logger, "info") as mock_info,
    ):
        await scheduler._update_statuses()

    warn_lines = summary_lines(mock_warning, STATUS_BATCH_SUMMARY_PREFIX)
    assert warn_lines == [f"{STATUS_BATCH_SUMMARY_PREFIX}: 0 updated, 3 errors"]
    assert summary_lines(mock_info, STATUS_BATCH_SUMMARY_PREFIX) == []


@pytest.mark.asyncio
async def test_batch_summary_suppresses_all_quiet_tick():
    """A5: no flips and no errors → no summary line."""
    buys = [mock_media_buy(media_buy_id="mb_noop", tenant_id="t1")]
    session = mock_savepoint_session()
    cm = mock_get_db_session_cm(session)
    scheduler = MediaBuyStatusScheduler()

    with (
        patch("src.services.media_buy_status_scheduler.get_db_session", return_value=cm),
        patch(
            "src.services.media_buy_status_scheduler.MediaBuyRepository.get_all_by_statuses",
            return_value=buys,
        ),
        patch.object(scheduler, "_compute_new_status", return_value=None),
        patch.object(status_mod.logger, "warning") as mock_warning,
        patch.object(status_mod.logger, "info") as mock_info,
    ):
        await scheduler._update_statuses()

    assert summary_lines(mock_warning, STATUS_BATCH_SUMMARY_PREFIX) == []
    assert summary_lines(mock_info, STATUS_BATCH_SUMMARY_PREFIX) == []


@pytest.mark.asyncio
async def test_batch_summary_reports_pending_flips_when_commit_fails():
    """A2: failed terminal commit must not claim N updated."""
    buys = [mock_media_buy(media_buy_id="mb_a", tenant_id="t1")]
    session = mock_savepoint_session()
    session.commit = MagicMock(side_effect=RuntimeError("commit boom"))
    cm = mock_get_db_session_cm(session)
    scheduler = MediaBuyStatusScheduler()

    with (
        patch("src.services.media_buy_status_scheduler.get_db_session", return_value=cm),
        patch(
            "src.services.media_buy_status_scheduler.MediaBuyRepository.get_all_by_statuses",
            return_value=buys,
        ),
        patch.object(scheduler, "_compute_new_status", return_value="completed"),
        patch.object(status_mod.logger, "warning") as mock_warning,
        patch.object(status_mod.logger, "info") as mock_info,
    ):
        await scheduler._update_statuses()

    warn_lines = summary_lines(mock_warning, STATUS_BATCH_SUMMARY_PREFIX)
    assert len(warn_lines) == 1
    assert "0 persisted" in warn_lines[0]
    assert "1 pending flips lost" in warn_lines[0]
    assert summary_lines(mock_info, STATUS_BATCH_SUMMARY_PREFIX) == []


@pytest.mark.asyncio
async def test_batch_summary_survives_dead_connection_escape():
    """A3: escape mid-loop still emits summary for prior pending flips."""
    buys = [
        mock_media_buy(media_buy_id="mb_ok", tenant_id="t-ok"),
        mock_media_buy(media_buy_id="mb_dead", tenant_id="t-dead"),
    ]
    session = mock_savepoint_session()
    cm = mock_get_db_session_cm(session)
    scheduler = MediaBuyStatusScheduler()

    def _compute(media_buy, _now, _session):
        if media_buy.media_buy_id == "mb_dead":
            raise OperationalError("SELECT 1", {}, Exception("gone"), connection_invalidated=True)
        return "completed"

    with (
        patch("src.services.media_buy_status_scheduler.get_db_session", return_value=cm),
        patch(
            "src.services.media_buy_status_scheduler.MediaBuyRepository.get_all_by_statuses",
            return_value=buys,
        ),
        patch.object(scheduler, "_compute_new_status", side_effect=_compute),
        patch.object(status_mod.logger, "warning") as mock_warning,
        patch.object(status_mod.logger, "info") as mock_info,
    ):
        await scheduler._update_statuses()

    warn_lines = summary_lines(mock_warning, STATUS_BATCH_SUMMARY_PREFIX)
    assert len(warn_lines) == 1
    assert "0 persisted" in warn_lines[0]
    assert "1 pending flips lost" in warn_lines[0]
    assert summary_lines(mock_info, STATUS_BATCH_SUMMARY_PREFIX) == []
