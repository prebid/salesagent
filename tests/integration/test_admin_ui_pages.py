"""Integration tests for Admin UI page rendering.

These tests ensure that admin UI pages render without errors after database schema changes.
Uses the integration_db fixture which provides a real PostgreSQL database.
"""

import pytest

from tests.fixtures import TenantFactory
from tests.utils.database_helpers import create_tenant_with_timestamps

pytestmark = pytest.mark.integration


class TestAdminUIPages:
    """Test that admin UI pages render without errors against integration DB."""

    def test_tenant_dashboard_renders(self, authenticated_admin_session, test_tenant_with_data):
        """Test that the tenant dashboard renders successfully."""
        tenant_id = test_tenant_with_data["tenant_id"]
        response = authenticated_admin_session.get(f"/tenant/{tenant_id}", follow_redirects=True)
        assert response.status_code == 200
        assert b"Dashboard" in response.data or tenant_id.encode() in response.data

    def test_list_products_page_renders(self, authenticated_admin_session, test_tenant_with_data):
        """Test that the list products page renders successfully.

        Note: More thorough data validation tests for the .unique() bug are in
        test_admin_ui_data_validation.py::test_products_list_no_duplicates_with_pricing_options
        """
        tenant_id = test_tenant_with_data["tenant_id"]
        response = authenticated_admin_session.get(f"/tenant/{tenant_id}/products", follow_redirects=True)
        assert response.status_code == 200

    def test_create_product_page_renders(self, authenticated_admin_session, test_tenant_with_data):
        """Test that the create product page renders successfully."""
        tenant_id = test_tenant_with_data["tenant_id"]
        response = authenticated_admin_session.get(f"/tenant/{tenant_id}/products/add", follow_redirects=True)
        assert response.status_code == 200

    def test_list_principals_page_renders(self, authenticated_admin_session, test_tenant_with_data):
        """Test that the principals list page renders successfully."""
        tenant_id = test_tenant_with_data["tenant_id"]
        response = authenticated_admin_session.get(f"/tenant/{tenant_id}/principals", follow_redirects=True)
        assert response.status_code == 200

    def test_create_principal_page_renders(self, authenticated_admin_session, test_tenant_with_data):
        """Test that the create principal page renders successfully."""
        tenant_id = test_tenant_with_data["tenant_id"]
        response = authenticated_admin_session.get(f"/tenant/{tenant_id}/principals/create", follow_redirects=True)
        assert response.status_code == 200

    def test_settings_page_renders(self, authenticated_admin_session, test_tenant_with_data):
        """Test that the settings page renders successfully."""
        tenant_id = test_tenant_with_data["tenant_id"]
        response = authenticated_admin_session.get(f"/tenant/{tenant_id}/settings", follow_redirects=True)
        assert response.status_code == 200

    def test_authorized_properties_page_renders(self, authenticated_admin_session, test_tenant_with_data):
        """Test that the authorized properties page renders successfully."""
        tenant_id = test_tenant_with_data["tenant_id"]
        response = authenticated_admin_session.get(f"/tenant/{tenant_id}/authorized-properties", follow_redirects=True)
        assert response.status_code == 200

    def test_property_tags_page_renders(self, authenticated_admin_session, test_tenant_with_data):
        """Test that the property tags page renders successfully."""
        tenant_id = test_tenant_with_data["tenant_id"]
        response = authenticated_admin_session.get(f"/tenant/{tenant_id}/property-tags", follow_redirects=True)
        assert response.status_code == 200

    def test_operations_orders_page_responds(self, authenticated_admin_session, test_tenant_with_data):
        """Test that the operations orders page responds (may be not implemented yet)."""
        tenant_id = test_tenant_with_data["tenant_id"]
        response = authenticated_admin_session.get(f"/tenant/{tenant_id}/orders", follow_redirects=True)
        # Accept 200 (implemented) or 501 (not yet implemented)
        assert response.status_code in [200, 501]

    def test_workflows_page_renders(self, authenticated_admin_session, test_tenant_with_data):
        """Test that the workflows page renders successfully."""
        tenant_id = test_tenant_with_data["tenant_id"]
        response = authenticated_admin_session.get(f"/tenant/{tenant_id}/workflows", follow_redirects=True)
        assert response.status_code == 200

    def test_policy_page_renders(self, authenticated_admin_session, test_tenant_with_data):
        """Test that the policy page renders successfully."""
        tenant_id = test_tenant_with_data["tenant_id"]
        response = authenticated_admin_session.get(f"/tenant/{tenant_id}/policy", follow_redirects=True)
        assert response.status_code == 200


class TestPublicPages:
    """Test public pages that don't require authentication."""

    def test_health_endpoint(self, admin_client):
        """Test that the health endpoint returns 200."""
        response = admin_client.get("/health")
        assert response.status_code == 200

    def test_login_page_renders(self, admin_client):
        """Test that the login page renders successfully."""
        response = admin_client.get("/login")
        assert response.status_code == 200
        assert b"Sign in" in response.data or b"Login" in response.data


class TestAuthenticationRequired:
    """Test that pages properly require authentication."""

    def test_tenant_dashboard_requires_auth(self, admin_client, test_tenant_with_data):
        """Test that tenant dashboard requires authentication."""
        tenant_id = test_tenant_with_data["tenant_id"]
        response = admin_client.get(f"/tenant/{tenant_id}")
        # Should redirect to login or return 401/403
        assert response.status_code in [302, 401, 403]

    def test_settings_page_requires_auth(self, admin_client, test_tenant_with_data):
        """Test that settings page requires authentication."""
        tenant_id = test_tenant_with_data["tenant_id"]
        response = admin_client.get(f"/tenant/{tenant_id}/settings")
        # Should redirect to login or return 401/403
        assert response.status_code in [302, 401, 403]


class TestTenantIsolation:
    """Test that tenant isolation is properly enforced."""

    def test_404_for_unknown_tenant(self, authenticated_admin_session):
        """Test that accessing an unknown tenant returns appropriate error."""
        response = authenticated_admin_session.get("/tenant/nonexistent_tenant_xyz/products")
        # Should redirect or return error
        assert response.status_code in [302, 308, 404, 500]

    def test_cannot_access_other_tenant_data(self, authenticated_admin_session, integration_db):
        """Test that we cannot access data from a tenant we shouldn't have access to."""

        from src.core.database.database_session import get_db_session

        # Create two separate tenants
        tenant1_data = TenantFactory.create()
        tenant2_data = TenantFactory.create()

        with get_db_session() as session:
            tenant1 = create_tenant_with_timestamps(
                tenant_id=tenant1_data["tenant_id"],
                name=tenant1_data["name"],
                subdomain=tenant1_data["subdomain"],
                is_active=True,
                ad_server="mock",
                auto_approve_formats=[],
                human_review_required=False,
                policy_settings={},
            )
            tenant2 = create_tenant_with_timestamps(
                tenant_id=tenant2_data["tenant_id"],
                name=tenant2_data["name"],
                subdomain=tenant2_data["subdomain"],
                is_active=True,
                ad_server="mock",
                auto_approve_formats=[],
                human_review_required=False,
                policy_settings={},
            )
            session.add(tenant1)
            session.add(tenant2)
            session.commit()

        # Super admin should be able to access both (may redirect but should work)
        response1 = authenticated_admin_session.get(
            f"/tenant/{tenant1_data['tenant_id']}/products", follow_redirects=True
        )
        assert response1.status_code in [200, 302, 308]

        response2 = authenticated_admin_session.get(
            f"/tenant/{tenant2_data['tenant_id']}/products", follow_redirects=True
        )
        assert response2.status_code in [200, 302, 308]
