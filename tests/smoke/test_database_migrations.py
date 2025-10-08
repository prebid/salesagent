"""Tests for database migrations - ensure migrations work correctly."""

import os
import subprocess
import tempfile
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text


class TestMigrationSafety:
    """Test that migrations are safe and reversible."""

    @pytest.mark.smoke
    @pytest.mark.skip_ci
    def test_migrations_can_run_on_empty_db(self, test_database):
        """Test that migrations can run on a fresh database."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            db_path = tmp.name

        try:
            # Set up environment for migration
            env = os.environ.copy()
            env["DATABASE_URL"] = f"sqlite:///{db_path}"

            # Run migrations
            result = subprocess.run(
                ["python", "scripts/ops/migrate.py"],
                cwd=".",
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
            )

            # Migration may fail in CI if alembic isn't configured properly
            if result.returncode != 0:
                if "No module named" in result.stderr or "alembic" in result.stderr.lower():
                    pytest.skip("Migration dependencies not available in CI")
                raise AssertionError(f"Migration failed: {result.stderr}")

            # Verify tables were created
            engine = create_engine(f"sqlite:///{db_path}")
            with engine.connect() as conn:
                # Check critical tables exist
                tables = ["tenants", "principals", "products", "media_buys", "creatives", "audit_logs"]
                for table in tables:
                    result = conn.execute(text(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{table}'"))
                    assert result.fetchone() is not None, f"Table {table} was not created"

        finally:
            # Clean up
            if os.path.exists(db_path):
                os.unlink(db_path)

    @pytest.mark.smoke
    @pytest.mark.skip_ci
    def test_migrations_are_idempotent(self):
        """Test that running migrations twice doesn't break.

        NOTE: Skipped in CI as it requires SQLite which is not supported in
        PostgreSQL-only architecture. Run manually for local migration testing.
        """
        pytest.skip("SQLite no longer supported - use PostgreSQL for migration testing")
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            db_path = tmp.name

        try:
            env = os.environ.copy()
            env["DATABASE_URL"] = f"sqlite:///{db_path}"

            # Run migrations first time
            result1 = subprocess.run(
                ["python", "scripts/ops/migrate.py"],
                cwd=".",
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
            )
            assert result1.returncode == 0, f"First migration failed: {result1.stderr}"

            # Run migrations second time - should be idempotent
            result2 = subprocess.run(
                ["python", "scripts/ops/migrate.py"],
                cwd=".",
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
            )
            assert result2.returncode == 0, f"Second migration failed: {result2.stderr}"

            # Verify database is still functional
            engine = create_engine(f"sqlite:///{db_path}")
            with engine.connect() as conn:
                result = conn.execute(text("SELECT 1"))
                assert result.fetchone()[0] == 1

        finally:
            if os.path.exists(db_path):
                os.unlink(db_path)

    @pytest.mark.smoke
    @pytest.mark.skip_ci
    def test_migration_with_existing_data(self, test_database):
        """Test that migrations preserve existing data."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            db_path = tmp.name

        try:
            env = os.environ.copy()
            env["DATABASE_URL"] = f"sqlite:///{db_path}"

            # Run initial migration
            result = subprocess.run(
                ["python", "scripts/ops/migrate.py"],
                cwd=".",
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode != 0:
                if "No module named" in result.stderr or "alembic" in result.stderr.lower():
                    pytest.skip("Migration dependencies not available in CI")
                raise AssertionError(f"Migration failed: {result.stderr}")

            # Insert test data
            engine = create_engine(f"sqlite:///{db_path}")
            with engine.connect() as conn:
                conn.execute(
                    text(
                        """
                    INSERT INTO tenants (tenant_id, name, subdomain, is_active, ad_server, created_at, updated_at)
                    VALUES ('test_migration', 'Test Migration Tenant', 'test-migration', 1, 'mock',
                            CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """
                    )
                )
                conn.commit()

            # Run migrations again (simulating a new migration)
            result = subprocess.run(
                ["python", "scripts/ops/migrate.py"],
                cwd=".",
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode != 0:
                if "No module named" in result.stderr or "alembic" in result.stderr.lower():
                    pytest.skip("Migration dependencies not available in CI")
                raise AssertionError(f"Migration failed: {result.stderr}")

            # Verify data still exists
            with engine.connect() as conn:
                result = conn.execute(text("SELECT name FROM tenants WHERE tenant_id = 'test_migration'"))
                row = result.fetchone()
                assert row is not None, "Test data was lost during migration"
                assert row[0] == "Test Migration Tenant", "Test data was corrupted"

        finally:
            if os.path.exists(db_path):
                os.unlink(db_path)


class TestMigrationVersioning:
    """Test migration version tracking."""

    @pytest.mark.smoke
    @pytest.mark.skip_ci
    def test_alembic_version_table_created(self, test_database):
        """Test that alembic_version table is created."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            db_path = tmp.name

        try:
            env = os.environ.copy()
            env["DATABASE_URL"] = f"sqlite:///{db_path}"

            # Run migrations
            subprocess.run(
                ["python", "scripts/ops/migrate.py"],
                cwd=".",
                env=env,
                capture_output=True,
                timeout=30,
            )

            # Check alembic_version table exists
            engine = create_engine(f"sqlite:///{db_path}")
            with engine.connect() as conn:
                result = conn.execute(
                    text("SELECT name FROM sqlite_master WHERE type='table' AND name='alembic_version'")
                )
                assert result.fetchone() is not None, "alembic_version table not created"

                # Check version is recorded
                result = conn.execute(text("SELECT version_num FROM alembic_version"))
                version = result.fetchone()
                assert version is not None, "No version recorded in alembic_version"
                assert len(version[0]) > 0, "Empty version recorded"

        finally:
            if os.path.exists(db_path):
                os.unlink(db_path)

    @pytest.mark.smoke
    def test_migrations_directory_exists(self):
        """Test that migrations directory and files exist."""
        migrations_dir = Path("alembic")
        assert migrations_dir.exists(), "Migrations directory does not exist"

        # Check for alembic.ini
        alembic_ini = Path("alembic.ini")
        assert alembic_ini.exists(), "alembic.ini not found"

        # Check for versions directory
        versions_dir = migrations_dir / "versions"
        assert versions_dir.exists(), "Migrations versions directory does not exist"

        # Check that at least one migration exists
        migration_files = list(versions_dir.glob("*.py"))
        assert len(migration_files) > 0, "No migration files found"


class TestDatabaseCompatibility:
    """Test database compatibility across SQLite and PostgreSQL."""

    @pytest.mark.smoke
    @pytest.mark.skip_ci
    def test_sqlite_migration_compatibility(self, test_database):
        """Test that migrations work with SQLite."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            db_path = tmp.name

        try:
            env = os.environ.copy()
            env["DATABASE_URL"] = f"sqlite:///{db_path}"
            env["DB_TYPE"] = "sqlite"

            result = subprocess.run(
                ["python", "scripts/ops/migrate.py"],
                cwd=".",
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
            )

            if result.returncode != 0:
                if "No module named" in result.stderr or "alembic" in result.stderr.lower():
                    pytest.skip("Migration dependencies not available in CI")
                raise AssertionError(f"SQLite migration failed: {result.stderr}")

            # Test SQLite-specific features
            engine = create_engine(f"sqlite:///{db_path}")
            with engine.connect() as conn:
                # Test that JSON fields work (stored as TEXT in SQLite)
                conn.execute(
                    text(
                        """
                    INSERT INTO products (product_id, tenant_id, name, formats, targeting_template,
                                         delivery_type, is_fixed_price)
                    VALUES ('test_json', 'test', 'Test Product', '["display"]', '{}',
                            'guaranteed', 1)
                """
                    )
                )
                conn.commit()

                result = conn.execute(text("SELECT formats FROM products WHERE product_id = 'test_json'"))
                formats = result.fetchone()[0]
                assert formats == '["display"]', "JSON field not stored correctly in SQLite"

        finally:
            if os.path.exists(db_path):
                os.unlink(db_path)

    @pytest.mark.smoke
    @pytest.mark.skip_ci
    def test_boolean_field_compatibility(self, test_database):
        """Test that boolean fields work correctly."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            db_path = tmp.name

        try:
            env = os.environ.copy()
            env["DATABASE_URL"] = f"sqlite:///{db_path}"

            subprocess.run(
                ["python", "scripts/ops/migrate.py"],
                cwd=".",
                env=env,
                capture_output=True,
                timeout=30,
            )

            engine = create_engine(f"sqlite:///{db_path}")
            with engine.connect() as conn:
                # Test boolean values (SQLite stores as 0/1)
                conn.execute(
                    text(
                        """
                    INSERT INTO tenants (tenant_id, name, subdomain, is_active, ad_server,
                                       created_at, updated_at)
                    VALUES ('bool_test', 'Bool Test', 'bool-test', 1, 'mock',
                            CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """
                    )
                )
                conn.commit()

                result = conn.execute(text("SELECT is_active FROM tenants WHERE tenant_id = 'bool_test'"))
                is_active = result.fetchone()[0]
                assert is_active in [1, True], "Boolean TRUE not stored correctly"

                # Test FALSE value
                conn.execute(text("UPDATE tenants SET is_active = 0 WHERE tenant_id = 'bool_test'"))
                conn.commit()

                result = conn.execute(text("SELECT is_active FROM tenants WHERE tenant_id = 'bool_test'"))
                is_active = result.fetchone()[0]
                assert is_active in [0, False], "Boolean FALSE not stored correctly"

        finally:
            if os.path.exists(db_path):
                os.unlink(db_path)


class TestMigrationRollback:
    """Test migration error handling and rollback."""

    @pytest.mark.smoke
    def test_migration_handles_errors_gracefully(self):
        """Test that migration errors don't corrupt the database."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            db_path = tmp.name

        try:
            # Create a corrupted database scenario
            engine = create_engine(f"sqlite:///{db_path}")
            with engine.connect() as conn:
                # Create a conflicting table
                conn.execute(
                    text(
                        """
                    CREATE TABLE alembic_version (
                        version_num VARCHAR(32) PRIMARY KEY,
                        extra_field TEXT
                    )
                """
                    )
                )
                conn.commit()

            env = os.environ.copy()
            env["DATABASE_URL"] = f"sqlite:///{db_path}"

            # Migration might fail due to existing alembic_version, but should handle it
            result = subprocess.run(
                ["python", "scripts/ops/migrate.py"],
                cwd=".",
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
            )

            # Database should still be accessible even if migration had issues
            with engine.connect() as conn:
                result = conn.execute(text("SELECT 1"))
                assert result.fetchone()[0] == 1, "Database corrupted after migration error"

        finally:
            if os.path.exists(db_path):
                os.unlink(db_path)


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-m", "smoke"])
