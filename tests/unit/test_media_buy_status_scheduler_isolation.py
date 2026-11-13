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
from src.services.media_buy_status_scheduler import STATUS_BATCH_SUMMARY_PREFIX
from tests.helpers.metrics import counter_value
from tests.helpers.scheduler_isolation import (
    bare_disconnection_error,
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

    assert logged(records, "WARNING", STATUS_BATCH_SUMMARY_PREFIX) == [
        f"{STATUS_BATCH_SUMMARY_PREFIX}: 0 updated, 3 errors"
    ]
    assert logged(records, "INFO", STATUS_BATCH_SUMMARY_PREFIX) == []
    assert logged(records, "INFO", "Updated media buy ") == []
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
    metric_before = counter_value("media_buy_status", "t-fail-fast", "db_error")

    records = await run_mocked_tick([buy], _raise_invalidated)

    error_msgs = logged(records, "ERROR", "")
    assert any("Failed to update media buy statuses" in msg for msg in error_msgs)
    assert not any("Error updating media buy status" in msg for msg in error_msgs)
    assert counter_value("media_buy_status", "t-fail-fast", "db_error") == metric_before


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
    fail_before = counter_value("media_buy_status", "tenant-fail", "db_error")

    records = await run_mocked_tick(buys, _compute)

    errors = [record for record in records if record.levelname == "ERROR"]
    assert len(errors) == 1
    assert errors[0].exc_info
    assert "tenant_id=tenant-fail" in errors[0].getMessage()

    assert counter_value("media_buy_status", "tenant-fail", "db_error") == fail_before + 1
    # Siblings must not be metered.
    assert counter_value("media_buy_status", "tenant-ok-a", "db_error") == 0


@pytest.mark.asyncio
async def test_batch_summary_warns_when_every_visited_buy_fails():
    """Every visited buy failing logs WARNING, not INFO."""
    buys = [mock_media_buy(media_buy_id=f"mb_{i}", tenant_id=f"t{i}") for i in range(3)]

    def _always_fail(_media_buy, _now, _session):
        raise statement_failure()

    records = await run_mocked_tick(buys, _always_fail)

    assert logged(records, "WARNING", STATUS_BATCH_SUMMARY_PREFIX) == [
        f"{STATUS_BATCH_SUMMARY_PREFIX}: 0 updated, 3 errors"
    ]
    assert logged(records, "INFO", STATUS_BATCH_SUMMARY_PREFIX) == []


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

    assert logged(records, "INFO", STATUS_BATCH_SUMMARY_PREFIX) == [
        f"{STATUS_BATCH_SUMMARY_PREFIX}: 0 updated, 1 errors"
    ]
    assert logged(records, "WARNING", STATUS_BATCH_SUMMARY_PREFIX) == []


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

    err_msgs = logged(records, "ERROR", "")
    assert any("Failed to update media buy statuses" in msg for msg in err_msgs)
    assert not any("Error updating media buy status" in msg for msg in err_msgs)
    session.commit.assert_not_called()
    assert logged(records, "INFO", "Updated media buy ") == []


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
    assert len(logged(records, "INFO", "Updated media buy ")) == 1


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

    assert logged(records, "WARNING", STATUS_BATCH_SUMMARY_PREFIX) == [
        f"{STATUS_BATCH_SUMMARY_PREFIX}: 0 updated, 3 errors"
    ]
    assert logged(records, "INFO", STATUS_BATCH_SUMMARY_PREFIX) == []


@pytest.mark.asyncio
async def test_batch_summary_suppresses_all_quiet_tick():
    """A tick with no flips and no errors logs no summary."""
    buys = [mock_media_buy(media_buy_id="mb_noop", tenant_id="t1")]

    records = await run_mocked_tick(buys, None)

    assert logged(records, "WARNING", STATUS_BATCH_SUMMARY_PREFIX) == []
    assert logged(records, "INFO", STATUS_BATCH_SUMMARY_PREFIX) == []


@pytest.mark.asyncio
async def test_batch_summary_reports_pending_flips_when_commit_fails():
    """A failed commit does not log the flips it did not persist."""
    buys = [mock_media_buy(media_buy_id="mb_a", tenant_id="t1")]
    session = mock_savepoint_session()
    session.commit = MagicMock(side_effect=RuntimeError("commit boom"))

    records = await run_mocked_tick(buys, "completed", session=session)

    warn_lines = logged(records, "WARNING", STATUS_BATCH_SUMMARY_PREFIX)
    assert len(warn_lines) == 1
    assert "0 persisted" in warn_lines[0]
    assert "1 pending flips lost" in warn_lines[0]
    assert logged(records, "INFO", STATUS_BATCH_SUMMARY_PREFIX) == []
    assert logged(records, "INFO", "Updated media buy ") == []


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

    warn_lines = logged(records, "WARNING", STATUS_BATCH_SUMMARY_PREFIX)
    assert len(warn_lines) == 1
    assert "0 persisted" in warn_lines[0]
    assert "1 pending flips lost" in warn_lines[0]
    assert logged(records, "INFO", STATUS_BATCH_SUMMARY_PREFIX) == []
    assert logged(records, "INFO", "Updated media buy ") == []
