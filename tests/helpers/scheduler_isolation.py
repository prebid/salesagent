"""Shared helpers for scheduler isolation test oracles."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager, nullcontext
from datetime import UTC, date, datetime, timedelta
from enum import Enum
from typing import TYPE_CHECKING, Literal
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

from src.core.database.models import MediaBuy, PersistedMediaBuyStatus
from src.services.media_buy_status_scheduler import (
    STATUS_BATCH_SUMMARY_PREFIX,
    STATUS_FLIP_PREFIX,
    STATUS_ISOLATED_BUY_PREFIX,
    STATUS_TICK_FAILED_PREFIX,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from src.core.database.models import Principal, Tenant

#: The shape of ``MediaBuyStatusScheduler._compute_new_status``, which every stand-in mimics.
type StatusCompute = Callable[[MediaBuy, datetime, Session], PersistedMediaBuyStatus | str | None]


def records_with_prefix(records: Sequence[logging.LogRecord], prefix: str) -> list[logging.LogRecord]:
    """The records whose rendered message starts with *prefix*, at ANY level.

    The one matching rule for the tick's log lines. Selecting at any level means a
    negative cannot pass vacuously because a line moved level; a reader that cares
    about the level asserts it on the result.
    """
    return [record for record in records if record.getMessage().startswith(prefix)]


def lines_with_prefix(records: Sequence[logging.LogRecord], prefix: str) -> list[tuple[str, str]]:
    """:func:`records_with_prefix` as ``(level name, rendered message)`` pairs."""
    return [(record.levelname, record.getMessage()) for record in records_with_prefix(records, prefix)]


def tick_summaries(records: Sequence[logging.LogRecord]) -> list[tuple[str, str]]:
    """The tick's summary lines."""
    return lines_with_prefix(records, STATUS_BATCH_SUMMARY_PREFIX)


def assert_tick_summary(records: Sequence[logging.LogRecord], severity: str, text: str) -> None:
    """The tick logged exactly one summary, at *severity*, reading *text* after the prefix."""
    assert tick_summaries(records) == [(severity, f"{STATUS_BATCH_SUMMARY_PREFIX}: {text}")]


def assert_no_tick_summary(records: Sequence[logging.LogRecord]) -> None:
    """A quiet tick: no summary line at any level."""
    assert tick_summaries(records) == []


def assert_isolated_buy_logged(
    records: Sequence[logging.LogRecord],
    *,
    tenant_id: str,
    principal_id: str,
    media_buy_id: str,
) -> None:
    """One isolated-buy line, at ERROR, naming the buy's three ids, with the traceback attached.

    It is also the tick's only ERROR record.
    """
    isolated = records_with_prefix(records, STATUS_ISOLATED_BUY_PREFIX)
    errors = [record for record in records if record.levelname == "ERROR"]
    assert len(isolated) == 1 and errors == isolated, [r.getMessage() for r in [*isolated, *errors]]
    message = isolated[0].getMessage()
    assert f"tenant_id={tenant_id}" in message
    assert f"principal_id={principal_id}" in message
    assert f"media_buy_id={media_buy_id}" in message
    assert isolated[0].exc_info, "the isolated buy's ERROR record must carry exc_info"


def flip_lines(records: Sequence[logging.LogRecord]) -> list[str]:
    """The per-buy flip lines the tick logged after its commit, at any level."""
    return [message for _level, message in lines_with_prefix(records, f"{STATUS_FLIP_PREFIX} ")]


def tick_failures(records: Sequence[logging.LogRecord]) -> list[tuple[str, str]]:
    """The outer handler's tick-failure lines."""
    return lines_with_prefix(records, STATUS_TICK_FAILED_PREFIX)


def assert_tick_stopped(records: Sequence[logging.LogRecord]) -> None:
    """The tick stopped on the outer handler, at ERROR, and no buy took the isolate path."""
    failures = tick_failures(records)
    assert failures and all(level == "ERROR" for level, _ in failures), failures
    assert lines_with_prefix(records, STATUS_ISOLATED_BUY_PREFIX) == []


def assert_tick_not_stopped(records: Sequence[logging.LogRecord]) -> None:
    """The outer handler did not run.

    The INFO summary does not prove this on its own: ``reached_end`` is set inside
    the session block, so a failure on the way out of it logs here and still leaves
    the summary at INFO.
    """
    assert tick_failures(records) == []


class _Real(Enum):
    """The typed sentinel for "run the real ``_compute_new_status``"."""

    COMPUTE = "real"


_REAL_COMPUTE: Literal[_Real.COMPUTE] = _Real.COMPUTE


async def run_status_tick(
    scheduler=None,
    *,
    compute: StatusCompute | PersistedMediaBuyStatus | str | None | Literal[_Real.COMPUTE] = _REAL_COMPUTE,
    visited: list[str] | None = None,
) -> list[logging.LogRecord]:
    """One status-sweep tick and the log records it produced.

    ``compute`` replaces ``_compute_new_status``: a callable is a side effect,
    anything else is the return value. Left out, the real method runs.
    ``visited`` collects the ``media_buy_id`` of every buy the tick computed, in
    order, so a test can tell a tick that continued past a failure from one that
    stopped on it.
    """
    from tests.helpers.log_capture import capture_logs

    if scheduler is None:
        from src.services.media_buy_status_scheduler import MediaBuyStatusScheduler

        scheduler = MediaBuyStatusScheduler()
    if compute is _REAL_COMPUTE:
        inner = scheduler._compute_new_status
    elif callable(compute):
        inner = compute
    else:

        def inner(_media_buy, _now, _session):
            return compute

    def _stand_in(media_buy, now, session):
        if visited is not None:
            visited.append(media_buy.media_buy_id)
        return inner(media_buy, now, session)

    replaced = compute is not _REAL_COMPUTE or visited is not None
    logger_name = "src.services.media_buy_status_scheduler"
    with (
        patch.object(scheduler, "_compute_new_status", side_effect=_stand_in) if replaced else nullcontext(),
        capture_logs(logger_name, level=logging.INFO) as captured,
    ):
        await scheduler._update_statuses()
    return list(captured.log_records)


def fail_one(
    media_buy_id: str,
    fault: BaseException | Callable[[Session], object],
    otherwise: str | BaseException | StatusCompute | None = "completed",
) -> StatusCompute:
    """``_compute_new_status`` stand-in: *fault* for one buy, *otherwise* for every other.

    *fault* is raised, or, when callable, called with the tick's session (a statement the
    database refuses). *otherwise* is a status to return (``None`` makes the others no-ops),
    an exception to raise, or a ``_compute_new_status``-shaped callable to delegate to.
    """

    def _compute(media_buy: MediaBuy, now: datetime, session: Session) -> PersistedMediaBuyStatus | str | None:
        if media_buy.media_buy_id == media_buy_id:
            if isinstance(fault, BaseException):
                raise fault
            fault(session)
        if isinstance(otherwise, BaseException):
            raise otherwise
        return otherwise(media_buy, now, session) if callable(otherwise) else otherwise

    return _compute


def fail_all(exc: BaseException) -> StatusCompute:
    """``_compute_new_status`` stand-in: raise *exc* for every buy."""

    def _compute(_media_buy: MediaBuy, _now: datetime, _session: Session) -> PersistedMediaBuyStatus | None:
        raise exc

    return _compute


async def run_mocked_tick(buys, compute, *, session: MagicMock | None = None) -> list[logging.LogRecord]:
    """Run one tick against mocked buys and return its log records.

    ``compute`` is the ``_compute_new_status`` stand-in: a callable is a side
    effect, anything else is the return value.
    """
    session = mock_savepoint_session() if session is None else session
    cm = mock_get_db_session_cm(session)
    from src.services.media_buy_status_scheduler import MediaBuyStatusScheduler

    with (
        patch("src.services.media_buy_status_scheduler.get_db_session", return_value=cm),
        patch(
            "src.services.media_buy_status_scheduler.MediaBuyRepository.get_all_by_statuses",
            return_value=buys,
        ),
    ):
        return await run_status_tick(MediaBuyStatusScheduler(), compute=compute)


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


@contextmanager
def assert_propagated_session_error(*, trips_fail_fast: bool) -> Iterator[None]:
    """Run the block through the real ``get_db_session`` and check the fail-fast after it.

    The oracle is the next session open, through ``check_database_health``.
    Only a connect the server never answered makes that call report the
    fail-fast. A dropped connection propagates and the next session still opens.
    """
    from src.core.database.database_session import reset_health_state

    reset_health_state()
    try:
        yield
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
    from tests.helpers.metrics import assert_isolation_delta

    processed: list[str] = []
    with (
        assert_isolation_delta(0, tenant_id=tenant_id, error_type="db_error"),
        assert_propagated_session_error(trips_fail_fast=trips_fail_fast),
    ):
        records = await run_status_tick(scheduler, compute=fail_all(exc), visited=processed)
    assert len(processed) == 1
    assert_tick_stopped(records)


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


@contextmanager
def status_party(tenant_id: str) -> Iterator[tuple[Tenant, Principal]]:
    """A tenant and its principal, through the factories, for the sweep's integration tests.

    Rows a test creates inside the block commit as they are made, so the tick's
    own session sees them after the block exits.
    """
    from tests.harness._base import BareIntegrationEnv

    with BareIntegrationEnv(tenant_id=tenant_id) as env:
        yield env.setup_default_data()


def seed_status_buys(
    tenant: Tenant,
    principal: Principal,
    buy_ids: Sequence[str],
    *,
    status: str,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    **columns: object,
) -> list[MediaBuy]:
    """Insert media buys in *status* with this flight window, through ``MediaBuyFactory``.

    The one row builder for the status sweep's real-database tests: the BDD Givens
    and the integration tests both seed through it. A date not given is the date
    of its datetime. *columns* go to the factory as they are (``confirmed_at=None``
    for a test that grades the repository's own confirmation stamp).
    """
    from tests.factories import MediaBuyFactory

    return [
        MediaBuyFactory(
            tenant=tenant,
            principal=principal,
            media_buy_id=media_buy_id,
            status=status,
            start_time=start_time,
            end_time=end_time,
            start_date=start_date or start_time.date(),
            end_date=end_date or end_time.date(),
            **columns,
        )
        for media_buy_id in buy_ids
    ]


def seed_active_expired_buys(tenant_id: str, buy_ids: Sequence[str]) -> str:
    """Seed a tenant, its principal, and active buys whose flight has ended (ready to complete).

    Returns the principal id.
    """
    now = datetime.now(UTC)
    with status_party(tenant_id) as (tenant, principal):
        seed_status_buys(
            tenant,
            principal,
            buy_ids,
            status="active",
            start_time=now - timedelta(days=7),
            end_time=now - timedelta(hours=1),
        )
        return principal.principal_id
