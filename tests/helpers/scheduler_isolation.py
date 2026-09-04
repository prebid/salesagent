"""Shared helpers for scheduler isolation test oracles."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

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


def counter_value(scheduler: str, tenant_id: str, error_type: str) -> float:
    """Read ``scheduler_isolation_errors`` counter for one label triple."""
    from src.core.metrics import scheduler_isolation_errors

    return scheduler_isolation_errors.labels(
        scheduler=scheduler,
        tenant_id=tenant_id,
        error_type=error_type,
    )._value.get()


def invalidated_operational_error() -> OperationalError:
    """Connection-invalidated OperationalError used by breaker-arm oracles."""
    return OperationalError("SELECT 1", {}, Exception("gone"), connection_invalidated=True)


def invalidated_dbapi_error(exc_type: type[DBAPIError]) -> DBAPIError:
    """Build a connection-invalidated DBAPIError for escape⇒arm parametrization."""
    return exc_type("SELECT 1", {}, Exception("gone"), connection_invalidated=True)


INVALIDATED_ESCAPE_ARM_ERROR_TYPES: tuple[type[DBAPIError], ...] = (
    ProgrammingError,
    DataError,
    IntegrityError,
    InternalError,
    NotSupportedError,
)


def invalidated_interface_error() -> InterfaceError:
    """Connection-invalidated InterfaceError used by breaker-arm oracles."""
    return InterfaceError("SELECT 1", {}, Exception("gone"), connection_invalidated=True)


def bare_disconnection_error() -> DisconnectionError:
    """Bare DisconnectionError (no connection_invalidated) for escape oracles."""
    return DisconnectionError("connection closed")


async def assert_escaped_invalidation_arms_breaker(run) -> None:
    """Run ``run`` and assert a real ``get_db_session`` CM arms the DB breaker.

    Shared by scheduler integration oracles so the reset / assert / finally
    pattern is not duplicated (R0801).
    """
    import src.core.database.database_session as db_session_mod

    db_session_mod.reset_health_state()
    assert db_session_mod._is_healthy is True
    try:
        await run()
        assert db_session_mod._is_healthy is False
    finally:
        db_session_mod.reset_health_state()


def mock_savepoint_session(*, release_exc: Exception | None = None) -> MagicMock:
    """Build a ``MagicMock`` session whose ``begin_nested()`` behaves like a SAVEPOINT.

    ``release_exc`` (if given) is raised from ``__exit__`` — simulating a
    flush failure at SAVEPOINT release time, distinct from a failure inside
    the per-item body. Collapses the session/nested mock graph that was
    hand-built at every isolation-adoption call site.
    """
    session = MagicMock()
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
