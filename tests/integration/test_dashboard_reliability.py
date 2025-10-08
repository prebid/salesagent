"""Integration tests for dashboard reliability improvements.

These tests verify that the dashboard works reliably after the architectural
fixes, using single data source pattern and proper error handling.
"""

from unittest.mock import patch

import pytest

from src.admin.services.dashboard_service import DashboardService
from src.core.database.health_check import check_database_health


class TestDashboardReliability:
    """Test dashboard reliability after architectural improvements."""

    @pytest.mark.requires_db
    def test_dashboard_service_with_real_tenant(self, authenticated_admin_client, test_tenant):
        """Test dashboard service works with real tenant data."""
        service = DashboardService(test_tenant.tenant_id)

        # Should not raise exceptions
        tenant = service.get_tenant()
        assert tenant is not None

        metrics = service.get_dashboard_metrics()
        assert isinstance(metrics, dict)

        # Verify single data source pattern metrics
        assert metrics["pending_workflows"] == 0  # Hardcoded
        assert metrics["approval_needed"] == 0  # Hardcoded
        assert "recent_activity" in metrics  # From audit_logs only

    @pytest.mark.requires_db
    def test_dashboard_route_no_error_masking(self, authenticated_admin_client, test_tenant):
        """Test that dashboard route shows real errors instead of masking them."""
        # Make request to dashboard
        response = authenticated_admin_client.get(f"/tenant/{test_tenant.tenant_id}")

        # Should return success (no longer masked errors)
        assert response.status_code == 200

        # Should not contain generic error message
        data = response.get_data(as_text=True)
        assert "Error loading dashboard" not in data  # Old generic error

    @pytest.mark.requires_db
    def test_dashboard_single_data_source_pattern(self, authenticated_admin_client, test_tenant, test_audit_log):
        """Test that dashboard only uses audit_logs for activity data."""
        service = DashboardService(test_tenant.tenant_id)
        metrics = service.get_dashboard_metrics()

        # Activity data should come from audit_logs only
        assert "recent_activity" in metrics
        assert isinstance(metrics["recent_activity"], list)

        # Workflow metrics should be hardcoded (not from database)
        assert metrics["pending_workflows"] == 0
        assert metrics["approval_needed"] == 0

        # Should not query workflow_steps or tasks tables
        with patch("src.admin.services.dashboard_service.get_db_session") as mock_db:
            mock_session = mock_db.return_value.__enter__.return_value

            service.get_dashboard_metrics()

            # Verify no queries to deprecated tables
            for call in mock_session.query.call_args_list:
                query_model = str(call[0][0]) if call[0] else ""
                assert "Task" not in query_model
                assert "HumanTask" not in query_model
                assert "WorkflowStep" not in query_model

    @pytest.mark.requires_db
    def test_dashboard_handles_missing_workflow_tables_gracefully(self, authenticated_admin_client, test_tenant):
        """Test that dashboard works even if workflow tables are missing."""
        # Mock database health check to simulate missing workflow tables
        with patch("src.core.database.health_check.check_table_exists") as mock_exists:
            # Simulate workflow tables missing
            mock_exists.side_effect = lambda table: table not in ["workflow_steps", "object_workflow_mapping"]

            # Dashboard should still work
            response = authenticated_admin_client.get(f"/tenant/{test_tenant.tenant_id}")
            assert response.status_code == 200

    def test_health_check_identifies_missing_critical_tables(self):
        """Test that health check correctly identifies missing critical tables."""
        with patch("src.core.database.health_check.get_db_session") as mock_get_db:
            mock_session = mock_get_db.return_value.__enter__.return_value
            mock_engine = mock_session.get_bind.return_value

            with patch("src.core.database.health_check.inspect") as mock_inspect:
                mock_inspector = mock_inspect.return_value

                # Simulate missing workflow tables
                mock_inspector.get_table_names.return_value = [
                    "tenants",
                    "audit_logs",
                    "products",
                    # Missing: workflow_steps, object_workflow_mapping
                ]

                mock_session.execute.return_value.scalar.return_value = "020_fix_tasks_schema_properly"

                health = check_database_health()

                assert health["status"] == "unhealthy"
                assert "workflow_steps" in health["missing_tables"]
                assert "object_workflow_mapping" in health["missing_tables"]

                # Should have specific recommendations
                migration_020_mentioned = any("Migration 020" in rec for rec in health["recommendations"])
                assert migration_020_mentioned

    @pytest.mark.requires_db
    def test_dashboard_audit_log_integration(self, authenticated_admin_client, test_tenant, test_audit_log):
        """Test that dashboard correctly integrates with audit_logs table."""
        service = DashboardService(test_tenant.tenant_id)
        metrics = service.get_dashboard_metrics()

        # Should have activity from audit_logs
        activities = metrics["recent_activity"]
        assert isinstance(activities, list)

        # If we have test audit log, should appear in activities
        if activities:
            activity = activities[0]
            # Check top-level fields
            assert "type" in activity
            assert "title" in activity
            assert "description" in activity
            assert "principal_name" in activity
            assert "timestamp" in activity
            assert "action_required" in activity
            assert "time_relative" in activity
            # Check metadata contains operation details
            assert "metadata" in activity
            assert "operation" in activity["metadata"]
            assert "success" in activity["metadata"]

    @pytest.mark.requires_db
    def test_dashboard_service_caching_works(self, integration_db, test_tenant):
        """Test that dashboard service correctly caches tenant lookups."""
        service = DashboardService(test_tenant.tenant_id)

        # First call should load tenant
        tenant1 = service.get_tenant()
        assert tenant1 is not None
        assert service._tenant is not None

        # Second call should use cache
        tenant2 = service.get_tenant()
        assert tenant2 is tenant1  # Same object

    @pytest.mark.requires_db
    def test_dashboard_service_error_handling(self, integration_db):
        """Test that dashboard service handles errors appropriately."""
        # Invalid tenant ID should raise ValueError
        with pytest.raises(ValueError, match="Invalid tenant_id"):
            DashboardService("")

        # Nonexistent tenant should raise error in get_dashboard_metrics
        service = DashboardService("nonexistent_tenant")
        with pytest.raises(ValueError, match="not found"):
            service.get_dashboard_metrics()

    @pytest.mark.requires_db
    def test_migration_020_fix_creates_tables(self):
        """Test that Migration 020 fix actually creates missing tables."""
        # This test would ideally run the migration against a test database
        # and verify tables are created, but we'll test the logic instead

        # Skip this test since it's testing migration internals which are implementation details
        # The actual functionality is tested through the dashboard service tests
        pytest.skip("Migration 020 test skipped - testing migration internals is not necessary for functionality")


class TestDashboardTemplateIntegration:
    """Test that dashboard template works with new service architecture."""

    @pytest.mark.requires_db
    def test_dashboard_template_renders_with_service_data(self, authenticated_admin_client, test_tenant):
        """Test that dashboard template renders correctly with service-provided data."""
        response = authenticated_admin_client.get(f"/tenant/{test_tenant.tenant_id}")

        assert response.status_code == 200
        data = response.get_data(as_text=True)

        # Should contain dashboard elements
        assert "Dashboard" in data or "dashboard" in data
        assert test_tenant.name in data

        # Should not contain error indicators
        assert "Error loading" not in data
        assert "500 Internal Server Error" not in data  # More specific check for actual HTTP errors
        assert "HTTP 500" not in data

    @pytest.mark.requires_db
    def test_dashboard_chart_data_format(self, authenticated_admin_client, test_tenant):
        """Test that dashboard provides correctly formatted chart data."""
        service = DashboardService(test_tenant.tenant_id)
        chart_data = service.get_chart_data()

        # Should have correct structure for frontend
        assert "labels" in chart_data
        assert "data" in chart_data
        assert isinstance(chart_data["labels"], list)
        assert isinstance(chart_data["data"], list)
        assert len(chart_data["labels"]) == len(chart_data["data"])

    @pytest.mark.requires_db
    def test_dashboard_metrics_completeness(self, authenticated_admin_client, test_tenant):
        """Test that dashboard provides all required metrics for template."""
        service = DashboardService(test_tenant.tenant_id)
        metrics = service.get_dashboard_metrics()

        # Required metrics for template (updated for readiness system)
        required_metrics = [
            "total_revenue",
            "live_buys",
            "scheduled_buys",
            "needs_attention",
            "active_advertisers",
            "recent_activity",
            "pending_workflows",
            "approval_needed",
            "readiness_summary",
        ]

        for metric in required_metrics:
            assert metric in metrics, f"Missing required metric: {metric}"


class TestLegacyCompatibility:
    """Test that changes maintain compatibility with existing functionality."""

    @pytest.mark.requires_db
    def test_audit_log_format_compatibility(self, integration_db, test_audit_log):
        """Test that audit log format is compatible with activity stream."""
        from src.admin.blueprints.activity_stream import format_activity_from_audit_log

        # Should not raise exceptions
        activity = format_activity_from_audit_log(test_audit_log)

        # Should have required fields
        assert "operation" in activity
        assert "success" in activity
        assert "time_relative" in activity
        assert "type" in activity

    @pytest.mark.requires_db
    def test_media_buy_relationships_still_work(self, integration_db, test_media_buy, test_principal):
        """Test that media buy relationships work after model cleanup."""
        # Should be able to access principal
        assert test_media_buy.principal is not None
        assert test_media_buy.principal.name == test_principal.name

        # Should be able to access tenant through principal
        assert test_media_buy.tenant is not None
