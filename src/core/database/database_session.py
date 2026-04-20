"""
Standardized database session management for the Prebid Sales Agent.

This module provides a consistent, thread-safe approach to database session
management across the entire application.
"""

import logging
import os
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any
from urllib.parse import urlparse

import pydantic_core
from sqlalchemy import create_engine, event, select
from sqlalchemy.exc import DisconnectionError, OperationalError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from src.core.database.db_config import DatabaseConfig

logger = logging.getLogger(__name__)

# Module-level globals for lazy initialization
_engine = None
_session_factory = None

# Track database health
_last_health_check: float = 0.0
_health_check_interval = 60  # Check health every 60 seconds
_is_healthy = True


def _pydantic_json_serializer(obj: Any) -> str:
    """JSON serializer that handles Pydantic types (Enum, datetime, AnyUrl, etc.) natively.

    Registered on create_engine() so all JSONB columns automatically get safe serialization
    without needing mode='json' at every model_dump() call site.
    """
    return pydantic_core.to_json(obj, fallback=str).decode()


def _rewrite_sslmode_for_driver(connection_string: str) -> str:
    """Rewrite `sslmode=` to `ssl=` ONLY when the URL targets an async driver.

    Context (L5+ forward-prep, Agent F pre-L0 hardening):
        - psycopg2 (sync, L0-L4, scheme `postgresql://` or `postgresql+psycopg2://`):
          libpq parses `sslmode=require|disable|prefer|allow|verify-ca|verify-full`.
          Rewriting to `ssl=` here would BREAK the sync engine at L0.
        - asyncpg (async, L5+, scheme `postgresql+asyncpg://`): does NOT understand
          `sslmode=` — treats it as an unknown query param and raises TypeError at
          engine construction. asyncpg expects `ssl=true|false|...`.

    Strategy: leave sync psycopg2 URLs untouched. Only when we detect an async
    driver scheme do we rewrite `sslmode=<value>` to the asyncpg-equivalent
    `ssl=<value>`. This function is the single choke-point for that translation
    so it can be covered by a unit test at L5b without re-reasoning about it
    at every engine construction site.

    Mappings applied when the driver is asyncpg:
        sslmode=require    -> ssl=true
        sslmode=disable    -> ssl=false
        sslmode=prefer     -> ssl=prefer   (asyncpg accepts the string form)
        (any other value stays as sslmode= — asyncpg will surface an error,
        which is the correct failure mode for misconfigured URLs)

    Returns the URL unchanged if `sslmode=` is not present OR the URL does not
    target an async driver.
    """
    if "sslmode=" not in connection_string:
        return connection_string
    # Only rewrite for async drivers. The sync path (psycopg2) keeps `sslmode=`
    # so the libpq parser is happy at L0-L4. Layer 5+ flips engines to asyncpg.
    if "+asyncpg" not in connection_string:
        return connection_string

    replacements = {
        "sslmode=require": "ssl=true",
        "sslmode=disable": "ssl=false",
        "sslmode=prefer": "ssl=prefer",
    }
    for before, after in replacements.items():
        connection_string = connection_string.replace(before, after)
    return connection_string


def _is_pgbouncer_connection(connection_string: str) -> bool:
    """Check if connection string uses PgBouncer (port 6543) or USE_PGBOUNCER is set.

    Uses URL parsing for robust port detection instead of string matching,
    which avoids false positives from passwords containing ":6543".

    Args:
        connection_string: Database connection URL

    Returns:
        True if PgBouncer is detected, False otherwise
    """
    # Check environment variable first (explicit override)
    if os.environ.get("USE_PGBOUNCER", "false").lower() == "true":
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
    global _engine, _session_factory

    if _engine is None:
        # In test mode without DATABASE_URL, we should NOT create a real connection
        # Unit tests should mock database access, not use real connections
        if os.environ.get("ADCP_TESTING") and not os.environ.get("DATABASE_URL"):
            raise RuntimeError(
                "Unit tests should not create real database connections. "
                "Either mock get_db_session() or set DATABASE_URL for integration tests. "
                "Use @pytest.mark.requires_db for integration tests."
            )

        # Get connection string from config
        connection_string = DatabaseConfig.get_connection_string()

        if "postgresql" not in connection_string:
            raise ValueError("Only PostgreSQL is supported. Use DATABASE_URL=postgresql://...")

        # Forward-prep for L5+ async engine: psycopg2 uses `sslmode=`, asyncpg uses `ssl=`.
        # No-op for sync psycopg2 URLs at L0-L4.
        connection_string = _rewrite_sslmode_for_driver(connection_string)

        # Get timeout configuration from environment
        query_timeout = int(os.environ.get("DATABASE_QUERY_TIMEOUT", "30"))  # 30s default
        connect_timeout = int(os.environ.get("DATABASE_CONNECT_TIMEOUT", "10"))  # 10s default
        pool_timeout = int(os.environ.get("DATABASE_POOL_TIMEOUT", "30"))  # 30s default

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
            # Direct PostgreSQL settings (no PgBouncer)
            _engine = create_engine(
                connection_string,
                pool_size=10,  # Base connections in pool
                max_overflow=20,  # Additional connections beyond pool_size
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

        # Create bare session factory — NO scoped_session registry (Decision D2).
        # Each `with get_db_session()` block constructs a fresh Session from this
        # factory so that FastAPI's AnyIO threadpool thread reuse cannot leak
        # session state between requests. See CLAUDE.md Critical Invariant #4.
        _session_factory = sessionmaker(bind=_engine)

    return _engine


def reset_engine():
    """Reset engine for testing - closes existing connections and clears global state."""
    global _engine, _session_factory

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

    Each `with get_db_session()` call constructs a new Session instance from
    the bare sessionmaker (NO scoped_session registry — Decision D2). This is
    safe under FastAPI's AnyIO threadpool thread reuse because there is no
    thread-local state to leak between requests. The `session.close()` on
    block exit returns the underlying connection to the QueuePool; the
    Session object itself is garbage-collected.

    The session will automatically rollback on exception and always be
    properly closed. Connection errors are logged with more detail.

    Note: Query timeout is enforced at the database level via statement_timeout.
    Queries exceeding DATABASE_QUERY_TIMEOUT will raise OperationalError.

    Per CLAUDE.md Critical Invariant #4 and Decision D2 (2026-04-16).
    """
    import time

    global _is_healthy, _last_health_check

    # Check if we should fail fast due to repeated database failures
    if not _is_healthy:
        time_since_check = time.time() - _last_health_check
        if time_since_check < 10:  # Fail fast for 10 seconds after unhealthy check
            raise RuntimeError("Database is unhealthy - failing fast to prevent cascading failures")

    # Ensure engine + session factory are initialized (lazy).
    get_engine()
    assert _session_factory is not None  # type narrowing — set by get_engine()
    session = _session_factory()
    try:
        yield session
    except (OperationalError, DisconnectionError) as e:
        logger.error(f"Database connection error: {e}")
        session.rollback()
        # Mark as unhealthy for circuit breaker
        _is_healthy = False
        _last_health_check = time.time()
        raise
    except SQLAlchemyError as e:
        logger.error(f"Database error: {e}")
        session.rollback()
        raise
    finally:
        # Close returns the underlying connection to the QueuePool;
        # the Session object itself is garbage-collected.
        session.close()


def execute_with_retry(func, max_retries: int = 3, retry_on: tuple = (OperationalError, DisconnectionError)) -> Any:
    """
    Execute a database operation with retry logic for connection issues.

    Args:
        func: Function that takes a session as its first argument
        max_retries: Maximum number of retry attempts
        retry_on: Tuple of exception types to retry on (defaults to connection errors)

    Returns:
        The result of the function
    """
    import time

    last_exception = None

    for attempt in range(max_retries):
        try:
            with get_db_session() as session:
                result = func(session)
                session.commit()
                return result
        except retry_on as e:
            last_exception = e
            logger.warning(f"Database connection attempt {attempt + 1}/{max_retries} failed: {e}")
            if attempt < max_retries - 1:
                # Exponential backoff: 0.5s, 1s, 2s
                wait_time = 0.5 * (2**attempt)
                logger.info(f"Waiting {wait_time}s before retry...")
                time.sleep(wait_time)
                # No registry to clear under bare sessionmaker (Decision D2);
                # the failed session was closed in get_db_session()'s finally.
                continue
            raise
        except SQLAlchemyError as e:
            # Don't retry non-connection errors
            logger.error(f"Non-retryable database error: {e}")
            raise

    if last_exception:
        raise last_exception


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
        """Get or create a session.

        Constructs a fresh Session from the bare sessionmaker (no
        scoped_session registry per Decision D2). The manager holds this
        instance on `self._session` until close() is called.
        """
        if self._session is None:
            # Ensure engine + session factory are initialized (lazy).
            get_engine()
            assert _session_factory is not None  # type narrowing — set by get_engine()
            self._session = _session_factory()
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
        """Close and cleanup the session.

        Under bare sessionmaker (Decision D2), `Session.close()` returns
        the underlying connection to the QueuePool; the Session object
        itself is garbage-collected. No registry to clear.
        """
        if self._session:
            self._session.close()
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
