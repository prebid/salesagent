"""Integration tests for database health check functionality."""

import pytest

from src.core.database.health_check import (
    check_database_health,
    check_table_exists,
    get_table_info,
    print_health_report,
)


class TestDatabaseHealthIntegration:
    """Integration tests for database health check with real database."""

    @pytest.mark.requires_db
    def test_real_database_health_check(self):
        """Test health check against real test database."""
        health = check_database_health()

        # Should return valid health report structure
        assert isinstance(health, dict)
        assert "status" in health
        assert "missing_tables" in health
        assert "schema_issues" in health
        assert health["status"] in ["healthy", "unhealthy", "warning", "error"]

    @pytest.mark.requires_db
    def test_real_table_existence_checks(self, integration_db):
        """Test table existence checks against real database."""
        # These tables should always exist in test database
        assert check_table_exists("tenants") is True
        assert check_table_exists("audit_logs") is True

        # This table definitely should not exist
        assert check_table_exists("definitely_nonexistent_table_12345") is False

    @pytest.mark.requires_db
    def test_real_table_info_audit_logs(self, integration_db):
        """Test getting real table info for audit_logs."""
        info = get_table_info("audit_logs")

        assert info["exists"] is True
        assert "log_id" in info["columns"]
        assert "tenant_id" in info["columns"]
        assert "operation" in info["columns"]

    @pytest.mark.requires_db
    def test_health_check_detects_missing_workflow_tables(self):
        """Test that health check can detect missing workflow tables in edge cases."""
        health = check_database_health()

        # Workflow tables should exist in a properly set up test database
        # If they don't, health check should report this
        if "workflow_steps" in health["missing_tables"]:
            assert health["status"] == "unhealthy"
            assert any("workflow_steps" in issue for issue in health["schema_issues"])

    def test_print_health_report_healthy(self, capsys):
        """Test print_health_report for healthy status."""
        report = {
            "status": "healthy",
            "missing_tables": [],
            "extra_tables": [],
            "schema_issues": [],
            "migration_status": "020_fix_tasks_schema_properly",
            "recommendations": [],
        }

        print_health_report(report)

        captured = capsys.readouterr()
        assert "✅ Database Health Status: HEALTHY" in captured.out
        assert "020_fix_tasks_schema_properly" in captured.out

    def test_print_health_report_unhealthy(self, capsys):
        """Test print_health_report for unhealthy status."""
        report = {
            "status": "unhealthy",
            "missing_tables": ["workflow_steps"],
            "extra_tables": ["old_table"],
            "schema_issues": ["Critical table 'workflow_steps' is missing"],
            "migration_status": "020_fix_tasks_schema_properly",
            "recommendations": ["Run migrations to create missing tables"],
        }

        print_health_report(report)

        captured = capsys.readouterr()
        assert "❌ Database Health Status: UNHEALTHY" in captured.out
        assert "Missing Tables" in captured.out
        assert "workflow_steps" in captured.out
        assert "Unexpected Tables" in captured.out
        assert "old_table" in captured.out
        assert "Schema Issues" in captured.out
        assert "Critical table" in captured.out
        assert "Recommendations" in captured.out
        assert "Run migrations" in captured.out
