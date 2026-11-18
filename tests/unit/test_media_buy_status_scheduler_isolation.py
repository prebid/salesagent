"""Unit tests: the status tick's failures that no real database fault reaches.

Everything a real fault can produce is graded once, on PostgreSQL: the
``@T-SWEEP-*`` scenarios and ``tests/integration/test_media_buy_status_scheduler.py``.
What stays here needs a fabricated failure: an id read that raises outside the
savepoint, an isolation handler that itself fails, and an escape after
isolated buys with no flips.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from sqlalchemy.exc import OperationalError

from src.services.media_buy_status_scheduler import STATUS_HANDLER_FAILED_PREFIX
from tests.helpers.scheduler_isolation import (
    assert_tick_not_stopped,
    assert_tick_stopped,
    assert_tick_summary,
    fail_one,
    flip_lines,
    invalidated_dbapi_error,
    lines_with_prefix,
    mock_media_buy,
    mock_savepoint_session,
    run_mocked_tick,
    statement_failure,
)


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

    records = await run_mocked_tick(
        buys, fail_one("mb_lazy", AssertionError("compute must not run after lazy-id failure")), session=session
    )

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

    with patch(
        "src.services.media_buy_status_scheduler.record_scheduler_isolation_error",
        side_effect=RuntimeError("handler boom"),
    ):
        records = await run_mocked_tick(buys, fail_one("mb_fail", statement_failure()), session=session)

    assert [level for level, _ in lines_with_prefix(records, STATUS_HANDLER_FAILED_PREFIX)] == ["ERROR"]
    assert_tick_not_stopped(records)
    assert_tick_summary(records, "INFO", "1 updated, 1 errors")
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

    compute = fail_one("mb_dead", invalidated_dbapi_error(OperationalError), otherwise=statement_failure())
    records = await run_mocked_tick(buys, compute)

    assert_tick_summary(records, "WARNING", "0 updated, 3 errors")
