#!/usr/bin/env python3
"""Integration tests for the Tenant Management API - tests with actual database."""


import pytest
from flask import Flask
from sqlalchemy import delete

from src.admin.tenant_management_api import tenant_management_api
from src.core.database.models import Tenant

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


# temp_db fixture removed - using integration_db from conftest instead


@pytest.fixture
def mock_api_key_auth(integration_db):
    """Mock API key authentication to always pass.

    This fixture bypasses the require_tenant_management_api_key decorator
    by creating a valid API key in the database that all tests can use.

    We keep separate tests for authentication itself (test_init_api_key).
    """
    from datetime import UTC, datetime

    from src.core.database.database_session import get_db_session
    from src.core.database.models import TenantManagementConfig

    # Create a test API key in the database
    test_api_key = "sk-test-integration-key"

    with get_db_session() as session:
        # Check if key already exists
        from sqlalchemy import select

        stmt = select(TenantManagementConfig).filter_by(config_key="tenant_management_api_key")
        existing = session.scalars(stmt).first()

        if not existing:
            config = TenantManagementConfig(
                config_key="tenant_management_api_key",
                config_value=test_api_key,
                description="Test API key for integration tests",
                updated_at=datetime.now(UTC),
                updated_by="pytest",
            )
            session.add(config)
            session.commit()

    return test_api_key


@pytest.fixture
def app(integration_db, mock_api_key_auth):
    """Create test Flask app with auth configured."""
    # integration_db ensures database is properly initialized
    # mock_api_key_auth ensures API key exists in database
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(tenant_management_api)
    return app


@pytest.fixture
def client(app):
    """Create test client."""
    return app.test_client()


@pytest.fixture
def test_tenant(integration_db):
    """Create a test tenant."""
    from src.core.database.database_session import get_db_session
    from tests.utils.database_helpers import create_tenant_with_timestamps

    with get_db_session() as session:
        # Create a test tenant
        tenant = create_tenant_with_timestamps(
            tenant_id="test_tenant",
            name="Test Tenant",
            subdomain="test",
            ad_server="mock",
            enable_axe_signals=True,
            auto_approve_formats=[],
            human_review_required=False,
            billing_plan="basic",
            is_active=True,
        )
        session.add(tenant)
        session.commit()

    yield tenant

    # Cleanup
    with get_db_session() as session:
        session.execute(delete(Tenant).where(Tenant.tenant_id == "test_tenant"))
        session.commit()


class TestTenantManagementAPIIntegration:
    """Integration tests for Tenant Management API."""

    def test_init_api_key(self, client):
        """Test API key initialization."""
        response = client.post("/api/v1/tenant-management/init-api-key")
        # May be 201 (created) or 409 (already exists)
        assert response.status_code in [201, 409]

        if response.status_code == 201:
            data = response.json
            assert "api_key" in data
            assert data["api_key"].startswith("sk-")

    def test_health_check(self, client, mock_api_key_auth):
        """Test health check endpoint."""
        response = client.get(
            "/api/v1/tenant-management/health", headers={"X-Tenant-Management-API-Key": mock_api_key_auth}
        )

        assert response.status_code == 200
        assert response.json["status"] == "healthy"

    def test_create_minimal_gam_tenant(self, client, mock_api_key_auth):
        """Test creating a minimal GAM tenant with just refresh token."""
        tenant_data = {
            "name": "Test Sports Publisher",
            "subdomain": "test-sports",
            "ad_server": "google_ad_manager",
            "gam_refresh_token": "1//test-refresh-token",
            "creator_email": "test@sports.com",  # Required for access control
        }

        response = client.post(
            "/api/v1/tenant-management/tenants",
            headers={"X-Tenant-Management-API-Key": mock_api_key_auth},
            json=tenant_data,
        )

        assert response.status_code == 201
        data = response.json

        # Verify response
        assert "tenant_id" in data
        assert data["name"] == "Test Sports Publisher"
        assert data["subdomain"] == "test-sports"
        assert "admin_token" in data
        assert "admin_ui_url" in data
        assert "default_principal_token" in data

        # Store tenant_id for later tests
        return data["tenant_id"]

    def test_create_full_gam_tenant(self, client, mock_api_key_auth):
        """Test creating a GAM tenant with all fields."""
        tenant_data = {
            "name": "Test News Publisher",
            "subdomain": "test-news",
            "ad_server": "google_ad_manager",
            "gam_refresh_token": "1//test-refresh-token-full",
            "gam_network_code": "123456789",
            "gam_trafficker_id": "trafficker_456",
            "authorized_emails": ["admin@testnews.com"],
            "authorized_domains": ["testnews.com"],
            "billing_plan": "premium",
        }
        # NOTE: gam_company_id removed - advertiser_id is per-principal in platform_mappings

        response = client.post(
            "/api/v1/tenant-management/tenants",
            headers={"X-Tenant-Management-API-Key": mock_api_key_auth},
            json=tenant_data,
        )

        assert response.status_code == 201
        data = response.json

        # Verify response
        assert data["name"] == "Test News Publisher"
        assert data["subdomain"] == "test-news"

    def test_list_tenants(self, client, mock_api_key_auth, test_tenant):
        """Test listing all tenants."""
        response = client.get(
            "/api/v1/tenant-management/tenants", headers={"X-Tenant-Management-API-Key": mock_api_key_auth}
        )

        assert response.status_code == 200
        data = response.json

        assert "tenants" in data
        assert "count" in data
        assert isinstance(data["tenants"], list)

        # Should have at least the default tenant plus any we created
        assert data["count"] >= 1

    def test_get_tenant_details(self, client, mock_api_key_auth):
        """Test getting specific tenant details."""
        # First create a tenant
        create_response = client.post(
            "/api/v1/tenant-management/tenants",
            headers={"X-Tenant-Management-API-Key": mock_api_key_auth},
            json={
                "name": "Test Detail Publisher",
                "subdomain": "test-detail",
                "ad_server": "google_ad_manager",
                "gam_refresh_token": "1//test-detail-token",
                "creator_email": "test@detail.com",
            },
        )

        tenant_id = create_response.json["tenant_id"]

        # Now get the details
        response = client.get(
            f"/api/v1/tenant-management/tenants/{tenant_id}", headers={"X-Tenant-Management-API-Key": mock_api_key_auth}
        )

        assert response.status_code == 200
        data = response.json

        # Verify all expected fields
        assert data["tenant_id"] == tenant_id
        assert data["name"] == "Test Detail Publisher"
        assert data["subdomain"] == "test-detail"
        assert data["ad_server"] == "google_ad_manager"
        assert "settings" in data
        assert "adapter_config" in data

        # Verify adapter config
        assert data["adapter_config"]["adapter_type"] == "google_ad_manager"
        assert data["adapter_config"]["has_refresh_token"] is True

    def test_update_tenant(self, client, mock_api_key_auth, test_tenant):
        """Test updating a tenant."""
        # First create a tenant
        create_response = client.post(
            "/api/v1/tenant-management/tenants",
            headers={"X-Tenant-Management-API-Key": mock_api_key_auth},
            json={
                "name": "Test Update Publisher",
                "subdomain": "test-update",
                "ad_server": "google_ad_manager",
                "gam_refresh_token": "1//test-update-token",
                "creator_email": "test@update.com",
            },
        )

        tenant_id = create_response.json["tenant_id"]

        # Update the tenant
        update_data = {
            "billing_plan": "enterprise",
            "max_daily_budget": 50000,
            "adapter_config": {"gam_network_code": "987654321", "gam_trafficker_id": "trafficker_999"},
        }
        # NOTE: gam_company_id removed - advertiser_id is per-principal in platform_mappings

        response = client.put(
            f"/api/v1/tenant-management/tenants/{tenant_id}",
            headers={"X-Tenant-Management-API-Key": mock_api_key_auth},
            json=update_data,
        )

        assert response.status_code == 200

        # Verify the update
        get_response = client.get(
            f"/api/v1/tenant-management/tenants/{tenant_id}", headers={"X-Tenant-Management-API-Key": mock_api_key_auth}
        )

        updated_data = get_response.json
        assert updated_data["billing_plan"] == "enterprise"
        assert updated_data["settings"]["max_daily_budget"] == 50000
        assert updated_data["adapter_config"]["gam_network_code"] == "987654321"
        assert updated_data["adapter_config"]["gam_trafficker_id"] == "trafficker_999"

    def test_soft_delete_tenant(self, client, mock_api_key_auth, test_tenant):
        """Test soft deleting a tenant."""
        # First create a tenant
        create_response = client.post(
            "/api/v1/tenant-management/tenants",
            headers={"X-Tenant-Management-API-Key": mock_api_key_auth},
            json={
                "name": "Test Delete Publisher",
                "subdomain": "test-delete",
                "ad_server": "mock",
                "creator_email": "test@delete.com",
            },
        )

        tenant_id = create_response.json["tenant_id"]

        # Soft delete
        response = client.delete(
            f"/api/v1/tenant-management/tenants/{tenant_id}", headers={"X-Tenant-Management-API-Key": mock_api_key_auth}
        )

        assert response.status_code == 200
        assert "deactivated" in response.json["message"]

        # Verify tenant still exists but is inactive
        get_response = client.get(
            f"/api/v1/tenant-management/tenants/{tenant_id}", headers={"X-Tenant-Management-API-Key": mock_api_key_auth}
        )

        assert get_response.status_code == 200
        assert get_response.json["is_active"] is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
