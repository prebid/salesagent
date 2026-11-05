"""Shared helpers for scheduler isolation test oracles."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

from sqlalchemy.exc import (
    DataError,
    DBAPIError,
    DisconnectionError,
    IntegrityError,
    InterfaceError,
    InternalError,
    NotSupportedError,
    OperationalError,
    ProgrammingError,
)


def summary_lines(mock_logger: MagicMock, prefix: str, *, needle: str | None = None) -> list[str]:
    """Extract log lines whose message contains ``needle``.

    Default ``needle`` is ``f"{prefix}:"`` — the production batch-summary
    format (``f"{prefix}: {processed} …"``). Pass an explicit substring when
    matching a different production log (e.g. ``"Updated media buy "``).
    Prefer the production prefix constant (``STATUS_BATCH_SUMMARY_PREFIX``).
    """
    match = needle if needle is not None else f"{prefix}:"
    return [call.args[0] for call in mock_logger.call_args_list if call.args and match in str(call.args[0])]


class _Answered(Exception):
    """DB-API error carrying a SQLSTATE, the shape of a server that answered."""

    def __init__(self, message: str, sqlstate: str) -> None:
        super().__init__(message)
        self.pgcode = sqlstate
        self.sqlstate = sqlstate


def statement_failure() -> OperationalError:
    """Statement-timeout OperationalError (SQLSTATE 57014).

    The server answered on a live connection, so the status scheduler isolates
    this failure. An OperationalError with no SQLSTATE is a connect the server
    never answered and must propagate.
    """
    return OperationalError(
        "SELECT 1",
        {},
        _Answered("canceling statement due to statement timeout", "57014"),
    )


def server_never_answered_error() -> OperationalError:
    """OperationalError with no SQLSTATE and a connection the pool still holds."""
    return OperationalError("SELECT 1", {}, Exception("timeout"))


def invalidated_dbapi_error(exc_type: type[DBAPIError]) -> DBAPIError:
    """Build a connection-invalidated DBAPIError for propagate-and-stop oracles."""
    return exc_type("SELECT 1", {}, Exception("gone"), connection_invalidated=True)


INVALIDATED_ESCAPE_ERROR_TYPES: tuple[type[DBAPIError], ...] = (
    ProgrammingError,
    DataError,
    IntegrityError,
    InternalError,
    NotSupportedError,
    OperationalError,
    InterfaceError,
)


def bare_disconnection_error() -> DisconnectionError:
    """Bare DisconnectionError (no connection_invalidated) for escape oracles."""
    return DisconnectionError("connection closed")


async def assert_propagated_session_error(run, *, trips_fail_fast: bool) -> None:
    """Run ``run`` through the real ``get_db_session`` and check the fail-fast.

    The oracle is the next session open, through ``check_database_health``.
    Only a connect the server never answered makes that call report the
    fail-fast. A dropped connection propagates and the next session still opens.
    """
    from src.core.database.database_session import reset_health_state

    reset_health_state()
    try:
        await run()
        assert_next_session(trips_fail_fast=trips_fail_fast)
    finally:
        reset_health_state()


def assert_next_session(*, trips_fail_fast: bool) -> None:
    """The next session open, through ``check_database_health``.

    A test must not call ``get_db_session`` itself. Only a connect the server
    never answered makes this report the fail-fast.
    """
    from src.core.database.database_session import check_database_health

    healthy, message = check_database_health(force=True)
    if trips_fail_fast:
        assert not healthy, message
        assert "failing fast" in message
    else:
        assert healthy, message


async def assert_propagated_failure_stops_batch(
    scheduler,
    exc: BaseException,
    *,
    tenant_id: str,
    trips_fail_fast: bool,
    buy_count: int,
) -> None:
    """A propagated DB error stops the tick and reaches ``get_db_session``.

    ``buy_count`` is how many eligible buys the caller seeded, and every one of
    them must still be eligible. The tick visits exactly one. ``trips_fail_fast``
    is true only for a connect the server never answered.
    """
    assert buy_count >= 2, "seed at least two buys so a continued batch is visible"
    from tests.helpers.metrics import counter_value

    metric_before = counter_value("media_buy_status", tenant_id, "db_error")
    processed: list[str] = []

    def _raise(media_buy, _now, _session):
        processed.append(media_buy.media_buy_id)
        raise exc

    async def _run() -> None:
        with patch.object(scheduler, "_compute_new_status", side_effect=_raise):
            await scheduler._update_statuses()

    await assert_propagated_session_error(_run, trips_fail_fast=trips_fail_fast)
    assert len(processed) == 1
    assert counter_value("media_buy_status", tenant_id, "db_error") == metric_before


def mock_savepoint_session(*, release_exc: Exception | None = None) -> MagicMock:
    """Build a ``MagicMock`` session whose ``begin_nested()`` behaves like a SAVEPOINT.

    ``release_exc`` (if given) is raised from ``__exit__`` — simulating a
    flush failure at SAVEPOINT release time, distinct from a failure inside
    the per-item body. Collapses the session/nested mock graph that was
    hand-built at every isolation-adoption call site.
    """
    session = MagicMock()
    session.info = {}
    nested = MagicMock()
    session.begin_nested.return_value = nested
    nested.__enter__ = MagicMock(return_value=nested)
    if release_exc is not None:
        nested.__exit__ = MagicMock(side_effect=release_exc)
    else:
        nested.__exit__ = MagicMock(return_value=False)
    return session


def mock_get_db_session_cm(session: MagicMock) -> MagicMock:
    """Build the ``with get_db_session() as session:`` context manager mock."""
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=session)
    cm.__exit__ = MagicMock(return_value=False)
    return cm


def mock_media_buy(
    *,
    media_buy_id: str,
    tenant_id: str,
    principal_id: str = "p1",
    status: str = "active",
    start_time: datetime | None = None,
    end_time: datetime | None = None,
) -> MagicMock:
    """Build a ``MagicMock`` media buy with the id/flight-date fields the
    status scheduler reads, collapsing the copy-pasted construction at every
    isolation-adoption unit test call site."""
    buy = MagicMock()
    buy.tenant_id = tenant_id
    buy.principal_id = principal_id
    buy.media_buy_id = media_buy_id
    buy.status = status
    buy.start_time = start_time or datetime(2020, 1, 1, tzinfo=UTC)
    buy.end_time = end_time or datetime(2020, 1, 2, tzinfo=UTC)
    buy.start_date = None
    buy.end_date = None
    return buy


def seed_active_expired_buys(
    create_media_buy: Callable[..., str],
    *,
    tenant_id: str,
    principal_id: str,
    buy_ids: Sequence[str],
) -> tuple[datetime, datetime]:
    """Seed active buys whose flight window is already past (ready to complete).

    Real-DB peer of :func:`mock_media_buy` — collapses the
    ``now/past_start/past_end`` + ``_create_media_buy(..., status="active", …)``
    preamble shared by the isolation integration oracles.
    """
    now = datetime.now(UTC)
    past_start = now - timedelta(days=7)
    past_end = now - timedelta(hours=1)
    for mid in buy_ids:
        create_media_buy(
            tenant_id=tenant_id,
            principal_id=principal_id,
            media_buy_id=mid,
            status="active",
            start_time=past_start,
            end_time=past_end,
        )
    return past_start, past_end
