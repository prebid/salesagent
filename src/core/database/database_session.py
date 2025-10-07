"""
Standardized database session management for the AdCP Sales Agent.

This module provides a consistent, thread-safe approach to database session
management across the entire application.
"""

from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import create_engine, select
from sqlalchemy.exc import DisconnectionError, OperationalError, SQLAlchemyError
from sqlalchemy.orm import Session, scoped_session, sessionmaker

from src.core.database.db_config import DatabaseConfig

# Create engine and session factory with production-ready settings
connection_string = DatabaseConfig.get_connection_string()

# Configure engine with appropriate pooling and retry settings
if "postgresql" in connection_string:
    # PostgreSQL production settings
    engine = create_engine(
        connection_string,
        pool_size=10,  # Base connections in pool
        max_overflow=20,  # Additional connections beyond pool_size
        pool_timeout=30,  # Seconds to wait for connection
        pool_recycle=3600,  # Recycle connections after 1 hour
        pool_pre_ping=True,  # Test connections before use
        echo=False,  # Set to True for SQL logging in debug
    )
else:
    # SQLite settings (development) - SQLite doesn't support all pool options
    engine = create_engine(
        connection_string,
        pool_pre_ping=True,
        echo=False,
    )

SessionLocal = sessionmaker(bind=engine)
import logging

logger = logging.getLogger(__name__)

# Thread-safe session factory
db_session = scoped_session(SessionLocal)


def get_engine():
    """Get the current database engine."""
    return engine


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
    session = db_session()
    try:
        yield session
    except (OperationalError, DisconnectionError) as e:
        logger.error(f"Database connection error: {e}")
        session.rollback()
        # Remove session from registry to force reconnection
        db_session.remove()
        raise
    except SQLAlchemyError as e:
        logger.error(f"Database error: {e}")
        session.rollback()
        raise
    finally:
        session.close()
        db_session.remove()


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
                db_session.remove()  # Clear the session registry
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
            self._session = db_session()
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
            db_session.remove()
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
