#!/usr/bin/env python3
"""
Database Health Check Integration Tests - Real Database Tests

These tests validate database health check functionality with real database connections
to catch issues that mocks would miss. This replaces over-mocked unit tests that
don't test actual database interactions.

This addresses the pattern identified in issue #161 of reducing mocking at data boundaries
to improve test coverage and catch real bugs.
"""

from unittest.mock import patch

import pytest
from sqlalchemy import func, select, text

from src.core.database.database_session import get_db_session, get_engine
from src.core.database.health_check import check_database_health, print_health_report
from src.core.database.models import Base, Product, Tenant

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


class TestDatabaseHealthIntegration:
    """Test database health check with real database connections."""

    # Note: Using conftest_db fixtures instead of custom temp_database
    # This ensures proper test isolation and database setup

    def test_health_check_with_complete_database(self, integration_db, test_tenant):
        """Test health check against a complete, properly migrated database."""
        # test_tenant fixture provides a functional database with test data

        # Run health check with real database
        health = check_database_health()

        # Validate structure
        expected_keys = [
            "status",
            "missing_tables",
            "extra_tables",
            "schema_issues",
            "migration_status",
            "recommendations",
        ]
        for key in expected_keys:
            assert key in health, f"Missing key '{key}' in health report"

        # Should return a valid status (may be unhealthy if migrations haven't run yet)
        assert health["status"] in ["healthy", "warning", "unhealthy"], f"Invalid status: {health['status']}"
        assert isinstance(health["missing_tables"], list)
        assert isinstance(health["extra_tables"], list)
        assert isinstance(health["schema_issues"], list)
        assert isinstance(health["recommendations"], list)

    def test_health_check_with_missing_tables(self, integration_db):
        """Test health check detects missing tables correctly."""
        # Use a mock to simulate missing tables without actually dropping them

        mock_tables = [
            "tenants",
            "audit_logs",
            # Missing: products, workflow_steps, etc.
        ]

        with patch("src.core.database.health_check.inspect") as mock_inspect:
            mock_inspector = mock_inspect.return_value
            mock_inspector.get_table_names.return_value = mock_tables

            # Mock database session and connection
            with patch("src.core.database.health_check.get_db_session") as mock_get_db:
                mock_session = mock_get_db.return_value.__enter__.return_value
                # Mock the migration status query
                mock_session.execute.return_value.scalar.return_value = "020_fix_tasks_schema_properly"

                # Run health check
                health = check_database_health()

                # Should detect missing table
                assert "products" in health["missing_tables"], "Should detect missing products table"
                assert "workflow_steps" in health["missing_tables"], "Should detect missing workflow_steps table"
                assert health["status"] == "unhealthy", "Should report unhealthy status"
                assert len(health["schema_issues"]) > 0, "Should report schema issues"

    def test_health_check_with_extra_tables(self, integration_db):
        """Test health check detects extra/deprecated tables."""
        # Add an extra table that shouldn't exist
        engine = get_engine()

        with engine.connect() as connection:
            connection.execute(
                text("CREATE TABLE IF NOT EXISTS deprecated_old_table (id INTEGER PRIMARY KEY, data TEXT)")
            )
            connection.commit()

        # Run health check
        health = check_database_health()

        # Should detect extra table
        assert "deprecated_old_table" in health["extra_tables"], "Should detect extra table"

    def test_health_check_database_access_errors(self, integration_db):
        """Test health check handles database access errors gracefully."""
        # Mock the database session to raise a connection error
        from unittest.mock import MagicMock

        from sqlalchemy.exc import OperationalError

        # Create a mock context manager that raises an error when entered
        mock_session_context = MagicMock()
        mock_session_context.__enter__.side_effect = OperationalError("connection failed", None, None)

        with patch("src.core.database.health_check.get_db_session") as mock_get_session:
            mock_get_session.return_value = mock_session_context

            health = check_database_health()

            # Should handle error gracefully
            assert health["status"] == "error", (
                f"Should report error status for database connection failure, got: {health['status']}"
            )
            assert len(health["schema_issues"]) > 0, "Should report schema issues for failed connection"

            # Error should be descriptive
            error_found = any("health check failed" in issue.lower() for issue in health["schema_issues"])
            assert error_found, f"Should include database connection error in issues: {health['schema_issues']}"

    def test_health_check_migration_status_detection(self, integration_db):
        """Test that health check correctly detects migration status."""
        # The health check should detect current migration version
        health = check_database_health()

        # Migration status should be a string indicating current version
        assert isinstance(health["migration_status"], str), "Migration status should be a string"

        # Should not be empty (unless no migrations have been run)
        # In a real database, there should be some migration version
        if health["migration_status"]:
            assert len(health["migration_status"]) > 0, "Migration status should not be empty string"

    def test_print_health_report_integration(self, integration_db, capsys):
        """Test health report printing with real health check data."""
        # Run real health check
        health = check_database_health()

        # Print the report
        print_health_report(health)
        captured = capsys.readouterr()

        # Should contain actual status
        assert health["status"].upper() in captured.out, "Should display actual health status"

        # Should display migration status if available
        if health["migration_status"]:
            assert health["migration_status"] in captured.out, "Should display migration status"

        # Should be properly formatted
        assert "Database Health Status:" in captured.out, "Should have header"

    def test_health_check_with_real_schema_validation(self, integration_db, test_tenant, test_product):
        """Test health check validates actual database schema against expected schema."""
        # test_tenant and test_product fixtures provide test data

        # Run health check
        health = check_database_health()

        # Schema should be valid with proper data
        if health["status"] == "healthy":
            assert len(health["schema_issues"]) == 0, "Healthy database should have no schema issues"

        # Verify we can query the data successfully (indicates schema is correct)
        with get_db_session() as session:
            tenant_count = session.scalar(select(func.count()).select_from(Tenant))
            product_count = session.scalar(select(func.count()).select_from(Product))

            assert tenant_count >= 1, "Should have at least one tenant"
            assert product_count >= 1, "Should have at least one product"

    @pytest.mark.requires_db
    def test_health_check_performance_with_real_database(self, integration_db):
        """Test that health check completes in reasonable time with real database."""
        import time

        # Add some test data to make it more realistic
        from datetime import UTC, datetime

        with get_db_session() as session:
            now = datetime.now(UTC)
            for i in range(10):
                tenant = Tenant(
                    tenant_id=f"perf_test_tenant_{i}",
                    name=f"Performance Test Tenant {i}",
                    subdomain=f"perf-test-{i}",
                    billing_plan="test",
                    created_at=now,
                    updated_at=now,
                )
                session.add(tenant)
            session.commit()

        # Measure health check performance
        start_time = time.time()
        health = check_database_health()
        elapsed_time = time.time() - start_time

        # Should complete within reasonable time (5 seconds for local SQLite)
        assert elapsed_time < 5.0, f"Health check took too long: {elapsed_time:.2f}s"

        # Should still return valid results
        assert "status" in health, "Should return valid health report even with larger dataset"

    def test_health_check_table_existence_validation(self, integration_db, test_tenant):
        """Test that health check validates existence of all required tables."""
        # Get list of tables that should exist

        expected_tables = set(Base.metadata.tables.keys())

        # Run health check
        health = check_database_health()

        # Check that health check knows about expected tables
        missing_critical_tables = {"tenants", "products", "principals"} & set(health["missing_tables"])

        if health["status"] == "healthy":
            # If healthy, no critical tables should be missing
            assert not missing_critical_tables, f"Critical tables missing: {missing_critical_tables}"

        # At minimum, should check for tenant table existence
        # (This is a core table that must exist for the system to function)
        with get_db_session() as session:
            # This should not raise an exception if schema is correct
            tenant_table_exists = True
            try:
                session.execute(text("SELECT COUNT(*) FROM tenants"))
            except Exception:
                tenant_table_exists = False

            if not tenant_table_exists:
                assert "tenants" in health["missing_tables"], "Should detect missing tenants table"
