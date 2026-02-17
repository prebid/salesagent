"""Integration test for the TIMESTAMPTZ migration (3a16c5fc27ce).

Verifies that the Alembic migration correctly converts naive TIMESTAMP columns
to TIMESTAMPTZ on upgrade, and reverts cleanly on downgrade, against a real
PostgreSQL instance.
"""

import os
import re
import uuid
from datetime import datetime

import psycopg2
import pytest
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
from sqlalchemy import create_engine, text

# The migration under test
MIGRATION_REV = "3a16c5fc27ce"
PRE_MIGRATION_REV = "b0bde1dcb049"

# Representative tables/columns to verify (subset of the full 73)
SPOT_CHECK_COLUMNS = [
    ("tenants", "created_at"),
    ("tenants", "updated_at"),
    ("media_buys", "start_time"),
    ("media_buys", "end_time"),
    ("media_buys", "created_at"),
    ("principals", "created_at"),
    ("audit_logs", "timestamp"),
    ("products", "expires_at"),
    ("creatives", "created_at"),
    ("sync_jobs", "started_at"),
]


def _parse_postgres_url():
    """Parse DATABASE_URL into connection components."""
    postgres_url = os.environ.get("DATABASE_URL", "")
    match = re.match(r"postgresql://([^:]+):([^@]+)@([^:]+):(\d+)/(.+)", postgres_url)
    if not match:
        return None
    user, password, host, port_str, _ = match.groups()
    return user, password, host, int(port_str)


def _get_column_type(engine, table_name, column_name):
    """Query information_schema for the actual PostgreSQL column type."""
    with engine.connect() as conn:
        result = conn.execute(
            text(
                "SELECT data_type FROM information_schema.columns WHERE table_name = :table AND column_name = :column"
            ),
            {"table": table_name, "column": column_name},
        )
        row = result.fetchone()
        return row[0] if row else None


@pytest.fixture(scope="module")
def migration_db():
    """Create an isolated PostgreSQL database for migration testing.

    Uses Alembic to manage schema state — does NOT use Base.metadata.create_all().
    """
    parsed = _parse_postgres_url()
    if not parsed:
        pytest.skip("Requires PostgreSQL DATABASE_URL")

    user, password, host, port = parsed
    db_name = f"test_migration_{uuid.uuid4().hex[:8]}"

    conn_params = {
        "host": host,
        "port": port,
        "user": user,
        "password": password,
        "database": "postgres",
    }

    # Create the test database
    conn = psycopg2.connect(**conn_params)
    conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    cur = conn.cursor()
    cur.execute(f'CREATE DATABASE "{db_name}"')
    cur.close()
    conn.close()

    db_url = f"postgresql://{user}:{password}@{host}:{port}/{db_name}"
    engine = create_engine(db_url, echo=False)

    yield engine, db_url

    # Cleanup
    engine.dispose()
    try:
        conn = psycopg2.connect(**conn_params)
        conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        cur = conn.cursor()
        cur.execute(
            f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            f"WHERE datname = '{db_name}' AND pid <> pg_backend_pid()"
        )
        cur.execute(f'DROP DATABASE IF EXISTS "{db_name}"')
        cur.close()
        conn.close()
    except Exception:
        pass


def _run_alembic(db_url, target_revision):
    """Run Alembic upgrade to a specific revision.

    Sets DATABASE_URL env var because alembic/env.py reads from
    DatabaseConfig.get_connection_string() which uses the env var.
    """
    from alembic.config import Config

    from alembic import command

    old_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = db_url
    try:
        alembic_cfg = Config("alembic.ini")
        command.upgrade(alembic_cfg, target_revision)
    finally:
        if old_url:
            os.environ["DATABASE_URL"] = old_url
        elif "DATABASE_URL" in os.environ:
            del os.environ["DATABASE_URL"]


def _run_alembic_downgrade(db_url, target_revision):
    """Run Alembic downgrade to a specific revision."""
    from alembic.config import Config

    from alembic import command

    old_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = db_url
    try:
        alembic_cfg = Config("alembic.ini")
        command.downgrade(alembic_cfg, target_revision)
    finally:
        if old_url:
            os.environ["DATABASE_URL"] = old_url
        elif "DATABASE_URL" in os.environ:
            del os.environ["DATABASE_URL"]


def _insert_test_tenant(engine, test_time):
    """Insert a minimal tenant row for data preservation testing."""
    with engine.connect() as conn:
        conn.execute(
            text(
                "INSERT INTO tenants (tenant_id, name, subdomain, "
                "created_at, updated_at) "
                "VALUES (:tid, :name, :sub, :ts, :ts)"
            ),
            {
                "tid": "test_tz_tenant",
                "name": "TZ Test",
                "sub": "tz-test",
                "ts": test_time,
            },
        )
        conn.commit()


@pytest.mark.requires_db
class TestTimestamptzMigration:
    """Test the TIMESTAMPTZ migration upgrade and downgrade."""

    def test_upgrade_converts_timestamp_to_timestamptz(self, migration_db):
        """Upgrade should convert all TIMESTAMP columns to TIMESTAMPTZ."""
        engine, db_url = migration_db

        # Step 1: Migrate to the revision BEFORE our migration
        _run_alembic(db_url, PRE_MIGRATION_REV)

        # Verify columns are naive TIMESTAMP before our migration
        for table, column in SPOT_CHECK_COLUMNS:
            col_type = _get_column_type(engine, table, column)
            assert col_type == "timestamp without time zone", (
                f"{table}.{column} should be TIMESTAMP before migration, got: {col_type}"
            )

        # Step 2: Insert test data with a naive timestamp
        test_time = datetime(2025, 6, 15, 12, 30, 0)
        _insert_test_tenant(engine, test_time)

        # Step 3: Run our migration (upgrade)
        _run_alembic(db_url, MIGRATION_REV)

        # Step 4: Verify columns are now TIMESTAMPTZ
        for table, column in SPOT_CHECK_COLUMNS:
            col_type = _get_column_type(engine, table, column)
            assert col_type == "timestamp with time zone", (
                f"{table}.{column} should be TIMESTAMPTZ after upgrade, got: {col_type}"
            )

        # Step 5: Verify data is preserved and correctly converted
        with engine.connect() as conn:
            result = conn.execute(text("SELECT created_at FROM tenants WHERE tenant_id = 'test_tz_tenant'"))
            row = result.fetchone()
            assert row is not None, "Test data should survive migration"
            # The naive 2025-06-15 12:30:00 was cast via AT TIME ZONE 'UTC'
            # so it becomes 2025-06-15 12:30:00+00
            created_at = row[0]
            assert created_at.year == 2025
            assert created_at.month == 6
            assert created_at.day == 15
            assert created_at.hour == 12
            assert created_at.minute == 30

    def test_downgrade_reverts_timestamptz_to_timestamp(self, migration_db):
        """Downgrade should revert TIMESTAMPTZ columns back to TIMESTAMP."""
        engine, db_url = migration_db

        # The database is already at MIGRATION_REV from the previous test.
        # Verify it's TIMESTAMPTZ first.
        col_type = _get_column_type(engine, "tenants", "created_at")
        assert col_type == "timestamp with time zone", f"Expected TIMESTAMPTZ before downgrade, got: {col_type}"

        # Step 1: Downgrade
        _run_alembic_downgrade(db_url, PRE_MIGRATION_REV)

        # Step 2: Verify columns are back to TIMESTAMP
        for table, column in SPOT_CHECK_COLUMNS:
            col_type = _get_column_type(engine, table, column)
            assert col_type == "timestamp without time zone", (
                f"{table}.{column} should be TIMESTAMP after downgrade, got: {col_type}"
            )

        # Step 3: Verify data survives the roundtrip
        with engine.connect() as conn:
            result = conn.execute(text("SELECT created_at FROM tenants WHERE tenant_id = 'test_tz_tenant'"))
            row = result.fetchone()
            assert row is not None, "Test data should survive downgrade"
            created_at = row[0]
            assert created_at.year == 2025
            assert created_at.month == 6
            assert created_at.day == 15
            assert created_at.hour == 12
            assert created_at.minute == 30
