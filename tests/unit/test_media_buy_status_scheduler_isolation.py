"""Unit tests: status scheduler per-buy savepoint isolation and fail-fast escape.

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

from src.core.metrics import scheduler_isolation_errors
from tests.helpers.metrics import assert_counter_delta
from tests.helpers.scheduler_isolation import (
    assert_isolated_buy_logged,
    assert_no_tick_summary,
    assert_tick_stopped,
    assert_tick_summary,
    bare_disconnection_error,
    flip_lines,
    invalidated_dbapi_error,
    logged,
    mock_media_buy,
    mock_savepoint_session,
    run_mocked_tick,
    server_never_answered_error,
    statement_failure,
)


@pytest.mark.asyncio
async def test_savepoint_release_failure_counts_as_isolated_not_flipped():
    """A release-time DataError is isolated, not counted as a flip."""
    from sqlalchemy.exc import DataError

    buys = [mock_media_buy(media_buy_id=f"mb_{i}", tenant_id=f"t{i}") for i in range(3)]
    release_exc = DataError("UPDATE media_buys", {}, Exception("value too long"))
    session = mock_savepoint_session(release_exc=release_exc)

    records = await run_mocked_tick(buys, "completed", session=session)

    assert_tick_summary(records, "WARNING", "0 updated, 3 errors")
    assert flip_lines(records) == []
    session.commit.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "escaped",
    [
        invalidated_dbapi_error(OperationalError),
        server_never_answered_error(),
        bare_disconnection_error(),
    ],
    ids=["connection_invalidated", "server_never_answered", "disconnection"],
)
async def test_status_scheduler_connection_failure_reraises(escaped):
    """A dropped connection or an unanswered connect re-raises out of the batch.

    The real ``get_db_session`` fail-fast is graded by the integration twin.
    This unit test pins the re-raise half via the outer log, not the per-item
    isolate log.
    """
    buy = mock_media_buy(media_buy_id="mb1", tenant_id="t-fail-fast")

    def _raise_invalidated(_media_buy, _now, _session):
        raise escaped

    scheduler_isolation_errors.clear()
    with assert_counter_delta(
        "scheduler_isolation_errors_total",
        0,
        scheduler="media_buy_status",
        tenant_id="t-fail-fast",
        error_type="db_error",
    ):
        records = await run_mocked_tick([buy], _raise_invalidated)

    assert_tick_stopped(records)


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

    def _compute(media_buy, _now, _session):
        if media_buy.media_buy_id == "mb_fail":
            raise statement_failure()
        return "completed"

    scheduler_isolation_errors.clear()
    with (
        assert_counter_delta(
            "scheduler_isolation_errors_total",
            1,
            scheduler="media_buy_status",
            tenant_id="tenant-fail",
            error_type="db_error",
        ),
        # Siblings must not be metered.
        assert_counter_delta(
            "scheduler_isolation_errors_total", 0, scheduler="media_buy_status", tenant_id="tenant-ok-a"
        ),
        assert_counter_delta(
            "scheduler_isolation_errors_total", 0, scheduler="media_buy_status", tenant_id="tenant-ok-b"
        ),
    ):
        records = await run_mocked_tick(buys, _compute)

    assert_isolated_buy_logged(records, tenant_id="tenant-fail", principal_id="p-mb_fail", media_buy_id="mb_fail")


@pytest.mark.asyncio
async def test_batch_summary_warns_when_every_visited_buy_fails():
    """Every visited buy failing logs WARNING, not INFO."""
    buys = [mock_media_buy(media_buy_id=f"mb_{i}", tenant_id=f"t{i}") for i in range(3)]

    def _always_fail(_media_buy, _now, _session):
        raise statement_failure()

    records = await run_mocked_tick(buys, _always_fail)

    assert_tick_summary(records, "WARNING", "0 updated, 3 errors")


@pytest.mark.asyncio
async def test_batch_summary_info_when_noop_plus_one_raiser():
    """Mid-flight no-ops plus one isolated buy log INFO, not WARNING.

    The warning fires when every visited buy failed. One failure among
    no-ops must not page on the 60-second tick.
    """
    buys = [mock_media_buy(media_buy_id=f"mb_{i}", tenant_id=f"t{i}") for i in range(5)]

    def _noop_or_raise(media_buy, _now, _session):
        if media_buy.media_buy_id == "mb_4":
            raise statement_failure()

    records = await run_mocked_tick(buys, _noop_or_raise)

    assert_tick_summary(records, "INFO", "0 updated, 1 errors")


@pytest.mark.asyncio
async def test_id_read_failure_stops_the_tick():
    """A failure reading a buy's id is outside the savepoint, so the tick stops."""

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

    def _compute(media_buy, _now, _session):
        if getattr(media_buy, "media_buy_id", None) == "mb_lazy":
            raise AssertionError("compute must not run after lazy-id failure")
        return "completed"

    records = await run_mocked_tick(buys, _compute, session=session)

    assert_tick_stopped(records)
    session.commit.assert_not_called()
    assert flip_lines(records) == []


@pytest.mark.asyncio
async def test_nested_handler_failure_stays_isolated():
    """A failure inside the isolation handler stays on that buy."""
    buys = [
        mock_media_buy(media_buy_id="mb_fail", tenant_id="t-fail"),
        mock_media_buy(media_buy_id="mb_ok", tenant_id="t-ok"),
    ]
    session = mock_savepoint_session()

    def _compute(media_buy, _now, _session):
        if media_buy.media_buy_id == "mb_fail":
            raise statement_failure()
        return "completed"

    with patch(
        "src.services.media_buy_status_scheduler.record_scheduler_isolation_error",
        side_effect=RuntimeError("handler boom"),
    ):
        records = await run_mocked_tick(buys, _compute, session=session)

    err_msgs = logged(records, "ERROR", "")
    assert any("Status isolation error handler failed" in msg for msg in err_msgs)
    assert not any("Failed to update media buy statuses" in msg for msg in err_msgs)
    session.commit.assert_called_once_with()
    assert len(flip_lines(records)) == 1


@pytest.mark.asyncio
async def test_batch_summary_warns_on_escape_with_only_prior_errors():
    """An escape after isolated buys, with no flips, logs WARNING."""
    buys = [
        mock_media_buy(media_buy_id="mb_a", tenant_id="t-a"),
        mock_media_buy(media_buy_id="mb_b", tenant_id="t-b"),
        mock_media_buy(media_buy_id="mb_c", tenant_id="t-c"),
        mock_media_buy(media_buy_id="mb_dead", tenant_id="t-dead"),
    ]

    def _compute(media_buy, _now, _session):
        if media_buy.media_buy_id == "mb_dead":
            raise invalidated_dbapi_error(OperationalError)
        raise statement_failure()

    records = await run_mocked_tick(buys, _compute)

    assert_tick_summary(records, "WARNING", "0 updated, 3 errors")


@pytest.mark.asyncio
async def test_batch_summary_suppresses_all_quiet_tick():
    """A tick with no flips and no errors logs no summary."""
    buys = [mock_media_buy(media_buy_id="mb_noop", tenant_id="t1")]

    records = await run_mocked_tick(buys, None)

    assert_no_tick_summary(records)


@pytest.mark.asyncio
async def test_batch_summary_reports_pending_flips_when_commit_fails():
    """A failed commit does not log the flips it did not persist."""
    buys = [mock_media_buy(media_buy_id="mb_a", tenant_id="t1")]
    session = mock_savepoint_session()
    session.commit = MagicMock(side_effect=RuntimeError("commit boom"))

    records = await run_mocked_tick(buys, "completed", session=session)

    assert_tick_summary(records, "WARNING", "0 persisted (1 pending flips lost), 0 errors")
    assert flip_lines(records) == []


@pytest.mark.asyncio
async def test_batch_summary_survives_dead_connection_escape():
    """An escape mid-loop still emits the summary, and does not log the uncommitted flip."""
    buys = [
        mock_media_buy(media_buy_id="mb_ok", tenant_id="t-ok"),
        mock_media_buy(media_buy_id="mb_dead", tenant_id="t-dead"),
    ]

    def _compute(media_buy, _now, _session):
        if media_buy.media_buy_id == "mb_dead":
            raise invalidated_dbapi_error(OperationalError)
        return "completed"

    records = await run_mocked_tick(buys, _compute)

    assert_tick_summary(records, "WARNING", "0 persisted (1 pending flips lost), 0 errors")
    assert flip_lines(records) == []
