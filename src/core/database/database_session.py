"""
Standardized database session management for the Prebid Sales Agent.

This module provides a consistent, thread-safe approach to database session
management across the entire application.
"""

import logging
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any
from urllib.parse import urlparse

import pydantic_core
from sqlalchemy import create_engine, event, select
from sqlalchemy.exc import DBAPIError, DisconnectionError, OperationalError, SQLAlchemyError
from sqlalchemy.orm import Session, scoped_session, sessionmaker

from src.core.config import get_settings
from src.core.database.db_config import DatabaseConfig

logger = logging.getLogger(__name__)

# Module-level globals for lazy initialization
_engine = None
_session_factory = None
_scoped_session = None

# Track database health
_last_health_check: float = 0.0
_health_check_interval = 60  # Check health every 60 seconds
_is_healthy = True
#: Seconds get_db_session refuses every session after a connect the server never answered.
FAIL_FAST_WINDOW_S = 10


def _pydantic_json_serializer(obj: Any) -> str:
    """JSON serializer that handles Pydantic types (Enum, datetime, AnyUrl, etc.) natively.

    Registered on create_engine() so all JSONB columns automatically get safe serialization
    without needing mode='json' at every model_dump() call site.
    """
    return pydantic_core.to_json(obj, fallback=str).decode()


def _is_pgbouncer_connection(connection_string: str) -> bool:
    """Check if connection string uses PgBouncer (port 6543) or USE_PGBOUNCER is set.

    Uses URL parsing for robust port detection instead of string matching,
    which avoids false positives from passwords containing ":6543".

    Args:
        connection_string: Database connection URL

    Returns:
        True if PgBouncer is detected, False otherwise
    """
    # The explicit override first
    if get_settings().database.use_pgbouncer:
        return True

    # Parse connection string to check port
    try:
        parsed = urlparse(connection_string)
        return parsed.port == 6543
    except Exception:
        # Fallback to string matching if URL parsing fails
        # This handles edge cases with non-standard URLs
        return ":6543" in connection_string


def get_engine():
    """Get or create the database engine (lazy initialization)."""
    global _engine, _session_factory, _scoped_session

    if _engine is None:
        # Under test with no database configured, opening an engine is a test bug, not a
        # fallback: unit tests mock database access.
        if get_settings().unit_tests_may_not_open_a_database:
            raise RuntimeError(
                "Unit tests should not create real database connections. "
                "Either mock get_db_session() or set DATABASE_URL for integration tests. "
                "Use @pytest.mark.requires_db for integration tests."
            )

        # Get connection string from config
        connection_string = DatabaseConfig.get_connection_string()

        if "postgresql" not in connection_string:
            raise ValueError("Only PostgreSQL is supported. Use DATABASE_URL=postgresql://...")

        db = get_settings().database
        query_timeout = db.database_query_timeout
        connect_timeout = db.database_connect_timeout
        pool_timeout = db.database_pool_timeout

        # Detect PgBouncer usage (typically port 6543)
        # PgBouncer requires different pooling strategy since it manages connections
        is_pgbouncer = _is_pgbouncer_connection(connection_string)

        if is_pgbouncer:
            logger.info("PgBouncer detected - using optimized connection pool settings")
            # PgBouncer-optimized settings:
            # - Smaller pool_size (PgBouncer handles pooling)
            # - No pool_pre_ping (can cause issues with transaction pooling)
            # - Shorter pool_recycle (PgBouncer recycles for us)
            # - statement_timeout set via event listener (PgBouncer doesn't support startup parameters)
            _engine = create_engine(
                connection_string,
                pool_size=2,  # Small pool - PgBouncer does the pooling
                max_overflow=5,  # Limited overflow
                pool_timeout=pool_timeout,
                pool_recycle=300,  # 5 minutes - shorter since PgBouncer manages connections
                pool_pre_ping=False,  # Disable pre-ping with PgBouncer transaction pooling
                echo=False,
                json_serializer=_pydantic_json_serializer,
                connect_args={
                    "connect_timeout": connect_timeout,
                },
            )
        else:
            logger.info("Direct PostgreSQL connection - using standard connection pool settings")
            # Direct PostgreSQL settings (no PgBouncer).
            # DB_POOL_SIZE / DB_MAX_OVERFLOW let CI tune the pool down (e.g. 4 / 8)
            # without code changes, so a GHA runner does not exhaust max_connections (D40).
            _engine = create_engine(
                connection_string,
                pool_size=db.db_pool_size,
                max_overflow=db.db_max_overflow,
                pool_timeout=pool_timeout,  # Seconds to wait for connection from pool
                pool_recycle=3600,  # Recycle connections after 1 hour
                pool_pre_ping=True,  # Test connections before use
                echo=False,  # Set to True for SQL logging in debug
                json_serializer=_pydantic_json_serializer,
                connect_args={
                    "connect_timeout": connect_timeout,  # Connection timeout in seconds
                },
            )

        # Set statement_timeout after connection is established
        # This works with both direct PostgreSQL and PgBouncer
        # PgBouncer doesn't support startup parameters, so we must use SET command
        @event.listens_for(_engine, "connect")
        def set_statement_timeout(dbapi_conn, connection_record):
            """Set statement_timeout on new connections."""
            cursor = dbapi_conn.cursor()
            cursor.execute(f"SET statement_timeout = '{query_timeout * 1000}'")
            cursor.close()

        # Create session factory
        _session_factory = sessionmaker(bind=_engine)
        _scoped_session = scoped_session(_session_factory)

    return _engine


def reset_engine():
    """Reset engine for testing - closes existing connections and clears global state."""
    global _engine, _session_factory, _scoped_session

    if _scoped_session is not None:
        _scoped_session.remove()
        _scoped_session = None

    if _engine is not None:
        _engine.dispose()
        _engine = None

    _session_factory = None


def reset_health_state():
    """Reset circuit breaker health state for testing.

    Use this to clear the health state after intentionally triggering database
    errors in tests to prevent cascading failures in subsequent tests.

    Example:
        try:
            # Test that triggers database error
            with get_db_session() as session:
                session.execute(text("SELECT pg_sleep(999)"))  # Timeout
        finally:
            reset_health_state()  # Prevent cascading failures
    """
    global _is_healthy, _last_health_check
    _is_healthy = True
    _last_health_check = 0


def get_scoped_session():
    """Get the scoped session factory (lazy initialization)."""
    # Calling get_engine() ensures all globals are initialized
    get_engine()
    return _scoped_session


def _sqlstate(e: SQLAlchemyError) -> str | None:
    """The SQLSTATE the server answered *e* with, or ``None`` when it answered nothing.

    Read under both drivers' names: psycopg2 calls it ``pgcode``, psycopg 3 ``sqlstate``.
    Reading only one would make every error under the other driver look unanswered.
    """
    orig = getattr(e, "orig", None)
    return getattr(orig, "pgcode", None) or getattr(orig, "sqlstate", None)


def _connection_lost(e: SQLAlchemyError) -> bool:
    """Whether *e* is a connection that dropped (an admin terminate, ``idle_session_timeout``,
    PgBouncer closing a client).

    SQLAlchemy flags it ``connection_invalidated`` and has already discarded the connection,
    so the next checkout reconnects. A ``DisconnectionError`` is the pool's own signal of
    the same thing.
    """
    return isinstance(e, DisconnectionError) or (isinstance(e, DBAPIError) and e.connection_invalidated)


def _server_never_answered(e: SQLAlchemyError) -> bool:
    """Whether *e* is a connect the server never answered, the one failure that trips the fail-fast.

    The fail-fast below refuses EVERY session in the process for ``FAIL_FAST_WINDOW_S``
    seconds. It exists for a database that cannot be reached, where each new connect would
    otherwise hold a worker for the connect timeout. Nothing else trips it:

    - A deadlock victim, a statement_timeout cancel or a lock timeout is the server answering
      on a live connection; the error carries the server's SQLSTATE.
    - A connection that dropped is one dead connection, not an outage (``_connection_lost``).
    """
    return isinstance(e, OperationalError) and not _connection_lost(e) and _sqlstate(e) is None


def _worth_retrying(e: SQLAlchemyError) -> bool:
    """Whether ``execute_with_retry`` re-runs the transaction *e* ended, read off the same signal.

    - A dropped connection: the retry runs on a new one.
    - SQLSTATE class 40 (40P01 deadlock victim, 40001 serialization failure): the server
      rolled the transaction back so that it can be run again.

    Nothing else. A statement the server cancelled (57014: a statement_timeout, or an
    operator's ``pg_cancel_backend``) would take as long again, and the timeout usually means
    the server is short of capacity, so re-running it adds load when there is least to spare.
    A connect the server never answered trips the fail-fast, which refuses the retry anyway.
    """
    return _connection_lost(e) or (_sqlstate(e) or "").startswith("40")


@contextmanager
def get_db_session() -> Generator[Session, None, None]:
    """
    Context manager for database sessions with automatic cleanup and retry logic.

    Usage:
        with get_db_session() as session:
            stmt = select(Model).filter_by(...)
            result = session.scalars(stmt).first()
            session.add(new_object)
            session.commit()  # Explicit commit needed

    The session will automatically rollback on exception and
    always be properly closed. Connection errors are logged with more detail.

    Note: Query timeout is enforced at the database level via statement_timeout.
    Queries exceeding DATABASE_QUERY_TIMEOUT will raise OperationalError.
    """
    import time

    global _is_healthy, _last_health_check

    # Check if we should fail fast due to repeated database failures
    if not _is_healthy:
        time_since_check = time.time() - _last_health_check
        if time_since_check < FAIL_FAST_WINDOW_S:
            raise RuntimeError("Database is unhealthy - failing fast to prevent cascading failures")

    scoped = get_scoped_session()
    session = scoped()
    try:
        yield session
    except SQLAlchemyError as e:
        logger.error(f"Database error: {e}")
        session.rollback()
        if _server_never_answered(e):
            _is_healthy = False
            _last_health_check = time.time()
        raise
    finally:
        session.close()
        scoped.remove()


def execute_with_retry(func, max_retries: int = 3) -> Any:
    """
    Run *func* in its own session and commit, re-running it when the failure is worth retrying.

    Args:
        func: Function that takes a session as its first argument
        max_retries: Maximum number of attempts

    Returns:
        The result of the function

    Which failures are re-run is ``_worth_retrying``: a dropped connection and a
    transaction the server rolled back to be run again (SQLSTATE class 40). Everything else
    is raised from the first attempt.
    """
    import time

    for attempt in range(max_retries):
        try:
            with get_db_session() as session:
                result = func(session)
                session.commit()
                return result
        except SQLAlchemyError as e:
            if attempt == max_retries - 1 or not _worth_retrying(e):
                raise
            # Exponential backoff: 0.5s, 1s, 2s
            wait_time = 0.5 * (2**attempt)
            logger.warning(f"Database attempt {attempt + 1}/{max_retries} failed, retrying in {wait_time}s: {e}")
            time.sleep(wait_time)


class DatabaseManager:
    """
    Manager class for database operations with session management.

    This class can be used as a base for services that need
    consistent database access patterns.
    """

    def __init__(self):
        self._session: Session | None = None

    @property
    def session(self) -> Session:
        """Get or create a session."""
        if self._session is None:
            scoped = get_scoped_session()
            self._session = scoped()
        return self._session

    def commit(self):
        """Commit the current transaction."""
        if self._session:
            try:
                self._session.commit()
            except SQLAlchemyError:
                self.rollback()
                raise

    def rollback(self):
        """Rollback the current transaction."""
        if self._session:
            self._session.rollback()

    def close(self):
        """Close and cleanup the session."""
        if self._session:
            self._session.close()
            scoped = get_scoped_session()
            scoped.remove()
            self._session = None

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit with automatic cleanup."""
        if exc_type:
            self.rollback()
        else:
            self.commit()
        self.close()


# Convenience functions for common patterns
def get_or_404(session: Session, model, **kwargs):
    """
    Get a model instance or raise 404-like exception.

    Args:
        session: Database session
        model: SQLAlchemy model class
        **kwargs: Filter criteria

    Returns:
        Model instance

    Raises:
        ValueError: If not found
    """
    stmt = select(model).filter_by(**kwargs)
    instance = session.scalars(stmt).first()
    if not instance:
        raise ValueError(f"{model.__name__} not found with criteria: {kwargs}")
    return instance


def get_or_create(session: Session, model, defaults: dict | None = None, **kwargs):
    """
    Get an existing instance or create a new one.

    Args:
        session: Database session
        model: SQLAlchemy model class
        defaults: Default values for creation
        **kwargs: Filter criteria (also used for creation)

    Returns:
        Tuple of (instance, created) where created is a boolean
    """
    stmt = select(model).filter_by(**kwargs)
    instance = session.scalars(stmt).first()
    if instance:
        return instance, False

    params = dict(kwargs)
    if defaults:
        params.update(defaults)

    instance = model(**params)
    session.add(instance)
    return instance, True


def check_database_health(force: bool = False) -> tuple[bool, str]:
    """
    Check database health with query timeout protection.

    Args:
        force: Force immediate check, ignoring cache interval

    Returns:
        Tuple of (is_healthy, message)
    """
    import time

    from sqlalchemy import text

    global _last_health_check, _is_healthy

    # Return cached result if recent (unless forced)
    if not force and time.time() - _last_health_check < _health_check_interval:
        return _is_healthy, "cached"

    try:
        # Use a very short timeout for health check
        with get_db_session() as session:
            # Simple query that should always work
            result = session.execute(text("SELECT 1"))
            result.scalar()

        _is_healthy = True
        _last_health_check = time.time()
        return True, "healthy"

    except Exception as e:
        _is_healthy = False
        _last_health_check = time.time()
        error_msg = f"Database unhealthy: {type(e).__name__}: {str(e)[:100]}"
        logger.error(error_msg)
        return False, error_msg


def get_pool_status() -> dict:
    """
    Get current connection pool status for monitoring.

    Returns:
        Dict with pool statistics (all non-negative integers)

    Note:
        overflow() can return negative values before pool is fully initialized.
        We normalize these to 0 for monitoring purposes.
    """
    engine = get_engine()
    pool = engine.pool

    # Get raw pool stats
    size = pool.size()
    checked_in = pool.checkedin()
    checked_out = pool.checkedout()
    overflow = pool.overflow()

    # Normalize overflow to non-negative (can be negative before pool initialization)
    # SQLAlchemy's pool.overflow() returns negative values in two cases:
    # 1. Before the pool is fully initialized (returns -pool_size, e.g., -2 when pool_size=2)
    # 2. When connections are closed before being used (internal pool accounting)
    # For monitoring purposes, we treat these as 0 (no overflow connections active)
    # since negative values don't represent actual resource usage.
    overflow_normalized = max(0, overflow)

    return {
        "size": size,
        "checked_in": checked_in,
        "checked_out": checked_out,
        "overflow": overflow_normalized,
        "total_connections": size + overflow_normalized,
    }
