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

    API key is provisioned via TENANT_MANAGEMENT_API_KEY env var in production.
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
            auto_approve_format_ids=[],
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

    def test_init_api_key_endpoint_removed(self, client):
        """Verify the unauthenticated init-api-key endpoint no longer exists."""
        response = client.post("/api/v1/tenant-management/init-api-key")
        assert response.status_code == 404  # Route removed entirely

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

        assert data["tenant_id"], "tenant_id should be a non-empty string"

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
            "adapter_config": {"gam_network_code": "987654321", "gam_trafficker_id": "trafficker_999"},
        }
        # NOTE: gam_company_id removed - advertiser_id is per-principal in platform_mappings
        # NOTE: max_daily_budget removed - moved to currency_limits table

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
        # max_daily_budget moved to currency_limits table, not in tenant settings anymore
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


class TestTenantManagementEnvVarAuth:
    """Test env var auth codepath for tenant management API."""

    def test_env_var_auth_succeeds(self, integration_db, monkeypatch):
        """TENANT_MANAGEMENT_API_KEY env var → auth succeeds."""
        monkeypatch.setenv("TENANT_MANAGEMENT_API_KEY", "env-test-key")

        app = Flask(__name__)
        app.config["TESTING"] = True
        app.register_blueprint(tenant_management_api)

        with app.test_client() as client:
            resp = client.get(
                "/api/v1/tenant-management/health",
                headers={"X-Tenant-Management-API-Key": "env-test-key"},
            )
            assert resp.status_code == 200

    def test_env_var_takes_priority_over_db(self, integration_db, mock_api_key_auth, monkeypatch):
        """When both env var and DB have keys, env var wins."""
        monkeypatch.setenv("TENANT_MANAGEMENT_API_KEY", "env-priority-key")

        app = Flask(__name__)
        app.config["TESTING"] = True
        app.register_blueprint(tenant_management_api)

        with app.test_client() as client:
            # DB key should be rejected when env var is set
            resp = client.get(
                "/api/v1/tenant-management/health",
                headers={"X-Tenant-Management-API-Key": mock_api_key_auth},
            )
            assert resp.status_code == 401

            # Env var key should succeed
            resp = client.get(
                "/api/v1/tenant-management/health",
                headers={"X-Tenant-Management-API-Key": "env-priority-key"},
            )
            assert resp.status_code == 200


class TestTenantManagementAuthRejection:
    """Test auth rejection cases for tenant management API."""

    def test_missing_header_returns_401(self, app, client):
        """Request without auth header returns 401."""
        resp = client.get("/api/v1/tenant-management/health")
        assert resp.status_code == 401
        assert "Missing API key" in resp.json["error"]

    def test_wrong_key_returns_401(self, app, client, mock_api_key_auth):
        """Request with incorrect key returns 401."""
        resp = client.get(
            "/api/v1/tenant-management/health",
            headers={"X-Tenant-Management-API-Key": "wrong-key"},
        )
        assert resp.status_code == 401
        assert "Invalid API key" in resp.json["error"]

    def test_unconfigured_returns_503(self, integration_db):
        """When no key is configured anywhere, returns 503."""
        app = Flask(__name__)
        app.config["TESTING"] = True
        app.register_blueprint(tenant_management_api)

        with app.test_client() as client:
            resp = client.get(
                "/api/v1/tenant-management/health",
                headers={"X-Tenant-Management-API-Key": "any-key"},
            )
            assert resp.status_code == 503
            assert "TENANT_MANAGEMENT_API_KEY" in resp.json["error"]


class TestSyncApiAuth:
    """Test auth for sync API using the shared helper."""

    @pytest.fixture
    def sync_app(self, integration_db):
        """Create test Flask app with sync API blueprint."""
        from src.admin.sync_api import sync_api

        app = Flask(__name__)
        app.config["TESTING"] = True
        app.register_blueprint(sync_api)
        return app

    def test_env_var_auth_succeeds(self, sync_app, monkeypatch):
        """SYNC_API_KEY env var → auth succeeds (hits real route)."""
        monkeypatch.setenv("SYNC_API_KEY", "sync-test-key")

        with sync_app.test_client() as client:
            resp = client.get(
                "/api/v1/sync/stats",
                headers={"X-API-Key": "sync-test-key"},
            )
            # Auth passed — we get past the decorator. Route may fail on DB
            # but must NOT return 401 (missing/invalid key) or 503 (unconfigured).
            assert resp.status_code not in (401, 403, 503), (
                f"Auth should have succeeded but got {resp.status_code}: {resp.json}"
            )

    def test_missing_header_returns_401(self, sync_app, monkeypatch):
        """Request without X-API-Key header returns 401."""
        monkeypatch.setenv("SYNC_API_KEY", "sync-test-key")

        with sync_app.test_client() as client:
            resp = client.post("/api/v1/sync/trigger/test-tenant")
            assert resp.status_code == 401

    def test_wrong_key_returns_401(self, sync_app, monkeypatch):
        """Request with incorrect key returns 401."""
        monkeypatch.setenv("SYNC_API_KEY", "correct-key")

        with sync_app.test_client() as client:
            resp = client.post(
                "/api/v1/sync/trigger/test-tenant",
                headers={"X-API-Key": "wrong-key"},
            )
            assert resp.status_code == 401

    def test_unconfigured_returns_503(self, sync_app):
        """When no key is configured anywhere, returns 503."""
        with sync_app.test_client() as client:
            resp = client.post(
                "/api/v1/sync/trigger/test-tenant",
                headers={"X-API-Key": "any-key"},
            )
            assert resp.status_code == 503
            assert "SYNC_API_KEY" in resp.json["error"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
