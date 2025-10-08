"""Integration tests for tenant dashboard to prevent regressions.

This test file ensures the tenant dashboard loads correctly and all
field mappings are correct between the database schema and the application code.
"""

from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from src.core.database.database_session import get_db_session
from src.core.database.models import MediaBuy, Principal, Tenant


@pytest.mark.requires_db
@pytest.mark.integration
class TestTenantDashboard:
    """Test tenant dashboard functionality and field mappings."""

    def test_dashboard_loads_without_error(self, authenticated_admin_session, test_tenant_with_data):
        """Test that the tenant dashboard loads without AttributeError or other issues."""
        response = authenticated_admin_session.get(f"/tenant/{test_tenant_with_data['tenant_id']}")

        # Should load successfully
        assert response.status_code == 200, f"Dashboard failed to load: {response.status_code}"

        # Should contain dashboard elements
        assert b"Dashboard" in response.data or b"tenant" in response.data.lower()

        # Should NOT contain error messages
        assert b"Error loading dashboard" not in response.data
        assert b"AttributeError" not in response.data
        # Don't check for "500" as it appears in Socket.IO config (reconnectionDelay: 5000)
        assert b"Internal Server Error" not in response.data
        assert b"500 Internal" not in response.data  # More specific error check

    def test_dashboard_with_media_buys(self, authenticated_admin_session, integration_db):
        """Test dashboard loads correctly with media buys using correct field names."""
        # Create test tenant
        with get_db_session() as db_session:
            tenant = Tenant(
                tenant_id="test_dashboard",
                name="Test Dashboard Tenant",
                subdomain="test-dashboard",
                is_active=True,
                # Use new schema fields
                enable_axe_signals=True,
                human_review_required=False,
                auto_approve_formats=["display_300x250"],
            )
            db_session.add(tenant)

            # Create principal with valid platform mapping
            principal = Principal(
                tenant_id="test_dashboard",
                principal_id="test_principal",
                name="Test Principal",
                access_token="test_token",
                platform_mappings={"mock": {"advertiser_id": "test_advertiser"}},  # Valid mapping
            )
            db_session.add(principal)

            # Create media buy with CORRECT field names
            media_buy = MediaBuy(
                tenant_id="test_dashboard",
                media_buy_id="test_buy_1",
                principal_id="test_principal",
                order_name="Test Order",  # Required field
                advertiser_name="Test Advertiser",
                status="active",
                budget=5000.0,
                raw_request={},  # Required field
                # These are the CORRECT field names in the model
                start_date=datetime.now().date(),
                end_date=(datetime.now() + timedelta(days=30)).date(),
                created_at=datetime.now(),
            )
            db_session.add(media_buy)
            db_session.commit()

        # Add tenant_id to session for tenant access
        with authenticated_admin_session.session_transaction() as sess:
            sess["tenant_id"] = "test_dashboard"

        # Access dashboard
        response = authenticated_admin_session.get("/tenant/test_dashboard")

        # Should load successfully
        assert response.status_code == 200

        # Should show media buy info
        assert b"Test Advertiser" in response.data or b"test_buy_1" in response.data

    def test_dashboard_metrics_calculation(self, authenticated_admin_session, integration_db):
        """Test that dashboard metrics are calculated correctly with proper field access."""
        # Create test data
        with get_db_session() as db_session:
            tenant = Tenant(
                tenant_id="test_metrics",
                name="Test Metrics Tenant",
                subdomain="test-metrics",
                is_active=True,
            )
            db_session.add(tenant)

            # Create multiple media buys
            for i in range(3):
                buy = MediaBuy(
                    tenant_id="test_metrics",
                    media_buy_id=f"buy_{i}",
                    principal_id=f"principal_{i}",
                    order_name=f"Order {i}",  # Required field
                    advertiser_name=f"Advertiser {i}",
                    status="active" if i < 2 else "completed",
                    budget=1000.0 * (i + 1),
                    raw_request={},  # Required field
                    start_date=datetime.now().date(),
                    end_date=(datetime.now() + timedelta(days=30)).date(),
                )
                db_session.add(buy)

            db_session.commit()

        response = authenticated_admin_session.get("/tenant/test_metrics")

        # Should calculate metrics without errors
        assert response.status_code == 200

        # Check for metrics display (live media buys, total revenue, etc.)
        # Look for key dashboard elements rather than broad error checking
        assert b"Total Revenue" in response.data
        assert b"Live Media Buys" in response.data
        assert b"Needs Attention" in response.data

        # Check for specific error messages that would indicate dashboard failures
        assert b"Error loading dashboard" not in response.data
        assert b"Dashboard error" not in response.data
        assert b"Failed to load" not in response.data

    def test_tenant_config_building(self, integration_db):
        """Test that tenant configuration is built correctly from new schema."""
        with get_db_session() as db_session:
            tenant = Tenant(
                tenant_id="test_config",
                name="Test Config Tenant",
                subdomain="test-config",
                is_active=True,
                # New schema fields
                enable_axe_signals=True,
                human_review_required=True,
                auto_approve_formats=["display_300x250", "video_16x9"],
            )
            db_session.add(tenant)
            db_session.commit()

            # Retrieve and check
            tenant_obj = db_session.scalars(select(Tenant).filter_by(tenant_id="test_config")).first()

            # Build config like the application does
            features_config = {
                "enable_axe_signals": tenant_obj.enable_axe_signals,
            }

            creative_config = {
                "auto_approve_formats": tenant_obj.auto_approve_formats or [],
                "human_review_required": tenant_obj.human_review_required,
            }

            # Verify config can be built without AttributeError
            assert features_config["enable_axe_signals"] is True
            assert creative_config["human_review_required"] is True
            assert "display_300x250" in creative_config["auto_approve_formats"]

    def test_all_dashboard_routes_accessible(self, authenticated_admin_session, test_tenant_with_data):
        """Test that all dashboard-related routes are accessible without errors."""
        tenant_id = test_tenant_with_data["tenant_id"]

        # Add tenant_id to session for tenant access
        with authenticated_admin_session.session_transaction() as sess:
            sess["tenant_id"] = tenant_id

        dashboard_routes = [
            f"/tenant/{tenant_id}",  # Main dashboard
            f"/tenant/{tenant_id}/products/",  # Products page (needs trailing slash)
            f"/tenant/{tenant_id}/principals",  # Principals/Advertisers page
            f"/tenant/{tenant_id}/creative-formats/",  # Creative formats (needs trailing slash)
            f"/tenant/{tenant_id}/settings",  # Tenant settings
        ]

        for route in dashboard_routes:
            response = authenticated_admin_session.get(route)
            # Should either load (200) or redirect (302/308) but not error (500)
            # 308 is a permanent redirect for missing trailing slash
            assert response.status_code in [200, 302, 308], f"Route {route} failed with status {response.status_code}"

            # Should not contain error messages
            if response.status_code == 200:
                assert b"AttributeError" not in response.data, f"Route {route} has AttributeError"
                # Check for actual 500 error messages, not just the number 500 (which appears in config)
                assert b"500 Internal Server Error" not in response.data, f"Route {route} shows 500 error"
                assert b"Error 500" not in response.data, f"Route {route} shows Error 500"

    def test_dashboard_with_empty_tenant(self, authenticated_admin_session, integration_db):
        """Test dashboard loads correctly for tenant with no data."""
        # Create minimal tenant
        with get_db_session() as db_session:
            tenant = Tenant(tenant_id="empty_tenant", name="Empty Tenant", subdomain="empty", is_active=True)
            db_session.add(tenant)
            db_session.commit()

        response = authenticated_admin_session.get("/tenant/empty_tenant")

        # Should load without errors even with no data
        assert response.status_code == 200
        assert b"Error loading dashboard" not in response.data

        # Should show empty state or zero metrics
        # (exact text depends on template implementation)
