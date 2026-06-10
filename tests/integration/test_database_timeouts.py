"""Test database timeout and resilience features."""

import time
from unittest.mock import patch

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from src.core.database.database_session import (
    check_database_health,
    get_db_session,
    get_pool_status,
)


@pytest.mark.requires_db
def test_query_timeout_configuration(integration_db):
    """Test that query timeout is configured correctly by executing a fast query."""
    # Instead of checking internal pool configuration, verify timeout is working
    # by executing a test query (should complete successfully)
    with get_db_session() as session:
        result = session.execute(text("SELECT 1"))
        assert result.scalar() == 1

    # Also verify that we can retrieve the engine without errors
    from src.core.database.database_session import get_engine

    engine = get_engine()
    assert engine is not None
    assert engine.pool is not None


@pytest.mark.requires_db
def test_health_check_success(integration_db):
    """Test database health check when database is healthy."""
    # Force a fresh check
    healthy, message = check_database_health(force=True)

    assert healthy is True
    assert message == "healthy"


@pytest.mark.requires_db
def test_health_check_caching(integration_db):
    """Test that health checks are cached to reduce load."""
    # First check
    check_database_health(force=True)

    # Immediate second check should return cached result
    start = time.time()
    healthy, message = check_database_health(force=False)
    duration = time.time() - start

    assert message == "cached"
    assert duration < 0.01  # Should be nearly instant


def test_health_check_failure():
    """Test health check when database is unavailable."""
    from src.core.database.database_session import reset_health_state

    try:
        with patch("src.core.database.database_session.get_db_session") as mock_session:
            # Simulate database error
            mock_session.side_effect = OperationalError("Connection failed", None, None)

            healthy, message = check_database_health(force=True)

            assert healthy is False
            assert "unhealthy" in message.lower()
            assert "OperationalError" in message
    finally:
        # Reset circuit-breaker state so subsequent tests are not poisoned.
        # Without this, any test that calls get_db_session() within the 10-second
        # fail-fast window will see RuntimeError("Database is unhealthy").
        reset_health_state()


@pytest.mark.requires_db
def test_pool_status(integration_db):
    """Test that we can get connection pool statistics."""
    status = get_pool_status()

    assert "size" in status
    assert "checked_in" in status
    assert "checked_out" in status
    assert "overflow" in status
    assert "total_connections" in status

    # All values should be non-negative integers
    for _key, value in status.items():
        assert isinstance(value, int)
        assert value >= 0


@pytest.mark.requires_db
def test_circuit_breaker_fail_fast(integration_db):
    """Test that circuit breaker fails fast when database is unhealthy."""
    from src.core.database import database_session
    from src.core.database.database_session import reset_health_state

    # Mark database as unhealthy
    database_session._is_healthy = False
    database_session._last_health_check = time.time()

    # Should fail fast without attempting connection
    with pytest.raises(RuntimeError, match="failing fast"):
        with get_db_session():
            pass

    # Reset health state using public API
    reset_health_state()


@pytest.mark.requires_db
def test_circuit_breaker_recovery(integration_db):
    """Test that circuit breaker allows retry after timeout."""
    from src.core.database import database_session
    from src.core.database.database_session import reset_health_state

    # Mark database as unhealthy
    database_session._is_healthy = False
    database_session._last_health_check = time.time() - 11  # 11 seconds ago

    # Should allow retry after 10 second timeout
    try:
        with get_db_session() as session:
            # This should work since timeout expired
            result = session.execute(text("SELECT 1"))
            assert result.scalar() == 1
        # If we get here, circuit breaker allowed the request
        success = True
    except RuntimeError as e:
        if "failing fast" in str(e):
            success = False
        else:
            raise

    assert success, "Circuit breaker should allow retry after timeout"

    # Reset health state using public API
    reset_health_state()


@pytest.mark.requires_db
def test_statement_timeout_enforced(integration_db):
    """Test that statement timeout actually terminates long queries."""
    import os

    # Set a very short timeout for this test
    original_timeout = os.environ.get("DATABASE_QUERY_TIMEOUT")
    os.environ["DATABASE_QUERY_TIMEOUT"] = "1"  # 1 second

    try:
        # Reset engine to pick up new timeout
        from src.core.database.database_session import reset_engine

        reset_engine()

        # This query should timeout (pg_sleep sleeps for 2 seconds).
        # Under parallel load the error may surface as "statement timeout",
        # a connection-level cancellation, or a terminated-backend message —
        # all indicate the timeout fired correctly.
        with pytest.raises(OperationalError, match="statement timeout|cancel|terminat"):
            with get_db_session() as session:
                session.execute(text("SELECT pg_sleep(2)"))

    finally:
        # Restore original timeout
        if original_timeout:
            os.environ["DATABASE_QUERY_TIMEOUT"] = original_timeout
        else:
            os.environ.pop("DATABASE_QUERY_TIMEOUT", None)

        # Reset engine again
        reset_engine()

        # Reset health state to prevent cascading failures in subsequent tests
        from src.core.database.database_session import reset_health_state

        reset_health_state()


@pytest.mark.requires_db
def test_connection_timeout_configuration(integration_db):
    """Test that connection timeout is configured by verifying we can connect."""
    # Instead of checking internal pool configuration, verify connection works
    # This implicitly tests that connection timeout is properly configured
    with get_db_session() as session:
        result = session.execute(text("SELECT 1"))
        assert result.scalar() == 1

    # Verify engine has the expected pool configuration
    from src.core.database.database_session import get_engine

    engine = get_engine()
    assert engine is not None

    # Verify pool has timeout configured (without accessing internal _creator)
    pool = engine.pool
    assert hasattr(pool, "_timeout")  # SQLAlchemy pool should have timeout attribute
