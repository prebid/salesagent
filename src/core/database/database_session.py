"""
Standardized database session management for the AdCP Sales Agent.

This module provides a consistent, thread-safe approach to database session
management across the entire application.
"""

import logging
import os
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import create_engine, select
from sqlalchemy.exc import DisconnectionError, OperationalError, SQLAlchemyError
from sqlalchemy.orm import Session, scoped_session, sessionmaker

from src.core.database.db_config import DatabaseConfig

logger = logging.getLogger(__name__)

# Module-level globals for lazy initialization
_engine = None
_session_factory = None
_scoped_session = None


def get_engine():
    """Get or create the database engine (lazy initialization)."""
    global _engine, _session_factory, _scoped_session

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

        # Create engine with production-ready settings
        _engine = create_engine(
            connection_string,
            pool_size=10,  # Base connections in pool
            max_overflow=20,  # Additional connections beyond pool_size
            pool_timeout=30,  # Seconds to wait for connection
            pool_recycle=3600,  # Recycle connections after 1 hour
            pool_pre_ping=True,  # Test connections before use
            echo=False,  # Set to True for SQL logging in debug
            connect_args={"connect_timeout": 5} if os.environ.get("ADCP_TESTING") else {},  # Fast timeout for tests
        )

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


def get_scoped_session():
    """Get the scoped session factory (lazy initialization)."""
    # Calling get_engine() ensures all globals are initialized
    get_engine()
    return _scoped_session


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
    """
    scoped = get_scoped_session()
    session = scoped()
    try:
        yield session
    except (OperationalError, DisconnectionError) as e:
        logger.error(f"Database connection error: {e}")
        session.rollback()
        # Remove session from registry to force reconnection
        scoped.remove()
        raise
    except SQLAlchemyError as e:
        logger.error(f"Database error: {e}")
        session.rollback()
        raise
    finally:
        session.close()
        scoped.remove()


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
                scoped = get_scoped_session()
                scoped.remove()  # Clear the session registry
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


def get_or_create(session: Session, model, defaults: dict = None, **kwargs):
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
