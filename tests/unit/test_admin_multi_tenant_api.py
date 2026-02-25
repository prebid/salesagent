"""Tests for the multi-tenant platform API router.

Uses TestClient against the FastAPI app to verify:
- Auth enforcement on all endpoints
- Request validation
- Correct HTTP status codes
- Response structure
"""

from unittest.mock import patch

import pytest
from starlette.testclient import TestClient

from src.core.admin_auth import require_platform_api_key

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    """TestClient for the FastAPI app with auth dependency overridden."""
    from src.app import app

    app.dependency_overrides[require_platform_api_key] = lambda: "test-api-key"
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.pop(require_platform_api_key, None)


@pytest.fixture
def api_key_header():
    return {"X-Tenant-Management-API-Key": "test-api-key"}


# ---------------------------------------------------------------------------
# Auth Tests
# ---------------------------------------------------------------------------


class TestPlatformAuthEnforcement:
    """Verify auth is enforced on all endpoints."""

    def test_list_tenants_requires_auth(self):
        """Without the dependency override, auth should fail."""
        from src.app import app
        from src.core.exceptions import AdCPAuthenticationError

        # Remove override so real auth runs (and fails without a valid key)
        app.dependency_overrides.pop(require_platform_api_key, None)

        def _fail_auth():
            raise AdCPAuthenticationError("Missing key")

        app.dependency_overrides[require_platform_api_key] = _fail_auth
        try:
            with TestClient(app, raise_server_exceptions=False) as tc:
                response = tc.get("/api/v1/platform/tenants")
                assert response.status_code == 401
        finally:
            app.dependency_overrides.pop(require_platform_api_key, None)


# ---------------------------------------------------------------------------
# Tenant CRUD Tests
# ---------------------------------------------------------------------------


class TestListTenants:
    @patch("src.routes.admin_multi_tenant._tenant_svc")
    def test_returns_tenant_list(self, mock_svc, client, api_key_header):
        mock_svc.list_tenants.return_value = {
            "tenants": [
                {"tenant_id": "t1", "name": "Test", "subdomain": "test", "is_active": True},
            ],
            "count": 1,
        }
        response = client.get("/api/v1/platform/tenants", headers=api_key_header)
        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 1
        assert data["tenants"][0]["tenant_id"] == "t1"


class TestCreateTenant:
    @patch("src.routes.admin_multi_tenant._tenant_svc")
    def test_creates_tenant_returns_201(self, mock_svc, client, api_key_header):
        mock_svc.create_tenant.return_value = {
            "tenant_id": "tenant_abc",
            "name": "Acme",
            "subdomain": "acme",
            "admin_token": "at_test",
            "default_principal_token": "tok_test",
        }
        response = client.post(
            "/api/v1/platform/tenants",
            json={"name": "Acme", "subdomain": "acme", "ad_server": "mock"},
            headers=api_key_header,
        )
        assert response.status_code == 201
        data = response.json()
        assert data["admin_token"] == "at_test"
        assert data["default_principal_token"] == "tok_test"

    def test_missing_required_field_returns_422(self, client, api_key_header):
        """FastAPI validates Pydantic models — missing required fields get 422."""
        response = client.post(
            "/api/v1/platform/tenants",
            json={"name": "Acme"},  # Missing subdomain and ad_server
            headers=api_key_header,
        )
        assert response.status_code == 422

    def test_invalid_ad_server_returns_422(self, client, api_key_header):
        """ad_server must be a known adapter type — arbitrary strings rejected at schema level."""
        response = client.post(
            "/api/v1/platform/tenants",
            json={"name": "Acme", "subdomain": "acme", "ad_server": "totally_invalid_garbage"},
            headers=api_key_header,
        )
        assert response.status_code == 422

    def test_valid_ad_servers_accepted(self, client, api_key_header):
        """Each valid adapter type string passes schema validation (service failure is OK)."""
        for ad_server in ("google_ad_manager", "mock", "kevel", "triton", "broadstreet"):
            response = client.post(
                "/api/v1/platform/tenants",
                json={"name": "Acme", "subdomain": "acme", "ad_server": ad_server},
                headers=api_key_header,
            )
            assert response.status_code != 422, f"Valid ad_server '{ad_server}' was rejected with 422"


class TestGetTenant:
    @patch("src.routes.admin_multi_tenant._tenant_svc")
    def test_returns_tenant_detail(self, mock_svc, client, api_key_header):
        mock_svc.get_tenant.return_value = {
            "tenant_id": "t1",
            "name": "Test",
            "subdomain": "test",
            "is_active": True,
            "settings": {},
        }
        response = client.get("/api/v1/platform/tenants/t1", headers=api_key_header)
        assert response.status_code == 200
        assert response.json()["tenant_id"] == "t1"

    @patch("src.routes.admin_multi_tenant._tenant_svc")
    def test_not_found_returns_404(self, mock_svc, client, api_key_header):
        from src.core.exceptions import AdCPNotFoundError

        mock_svc.get_tenant.side_effect = AdCPNotFoundError("Not found")
        response = client.get("/api/v1/platform/tenants/nonexistent", headers=api_key_header)
        assert response.status_code == 404


class TestUpdateTenant:
    @patch("src.routes.admin_multi_tenant._tenant_svc")
    def test_updates_tenant(self, mock_svc, client, api_key_header):
        mock_svc.update_tenant.return_value = {
            "tenant_id": "t1",
            "name": "Updated",
            "updated_at": "2025-01-01T00:00:00",
        }
        response = client.put(
            "/api/v1/platform/tenants/t1",
            json={"name": "Updated"},
            headers=api_key_header,
        )
        assert response.status_code == 200
        assert response.json()["name"] == "Updated"


class TestDeleteTenant:
    @patch("src.routes.admin_multi_tenant._tenant_svc")
    def test_soft_delete_by_default(self, mock_svc, client, api_key_header):
        mock_svc.delete_tenant.return_value = {
            "message": "Tenant deactivated successfully",
            "tenant_id": "t1",
        }
        response = client.delete("/api/v1/platform/tenants/t1", headers=api_key_header)
        assert response.status_code == 200
        mock_svc.delete_tenant.assert_called_once_with("t1", hard_delete=False)


# ---------------------------------------------------------------------------
# Sync Tests
# ---------------------------------------------------------------------------


class TestTriggerSync:
    def test_invalid_sync_type_returns_422(self, client, api_key_header):
        """sync_type must be a known value — arbitrary strings rejected at schema level."""
        response = client.post(
            "/api/v1/platform/sync/t1",
            json={"sync_type": "garbage_value"},
            headers=api_key_header,
        )
        assert response.status_code == 422

    def test_valid_sync_types_accepted(self, client, api_key_header):
        """Each valid sync_type passes schema validation (service failure is OK here)."""
        for sync_type in ("full", "inventory", "targeting", "selective"):
            response = client.post(
                "/api/v1/platform/sync/t1",
                json={"sync_type": sync_type},
                headers=api_key_header,
            )
            assert response.status_code != 422, f"Valid sync_type '{sync_type}' was rejected with 422"

    @patch("src.routes.admin_multi_tenant._sync_svc")
    def test_triggers_sync(self, mock_svc, client, api_key_header):
        mock_svc.trigger_sync.return_value = {"sync_id": "sync_123", "status": "completed"}
        response = client.post(
            "/api/v1/platform/sync/t1",
            json={"sync_type": "full"},
            headers=api_key_header,
        )
        assert response.status_code == 200
        assert response.json()["sync_id"] == "sync_123"


class TestSyncStatus:
    @patch("src.routes.admin_multi_tenant._sync_svc")
    def test_returns_status(self, mock_svc, client, api_key_header):
        mock_svc.get_sync_status.return_value = {
            "sync_id": "sync_123",
            "tenant_id": "t1",
            "status": "completed",
        }
        response = client.get("/api/v1/platform/sync/status/sync_123", headers=api_key_header)
        assert response.status_code == 200
        assert response.json()["status"] == "completed"


class TestSyncHistory:
    @patch("src.routes.admin_multi_tenant._sync_svc")
    def test_returns_paginated_history(self, mock_svc, client, api_key_header):
        mock_svc.get_sync_history.return_value = {
            "total": 1,
            "limit": 10,
            "offset": 0,
            "results": [{"sync_id": "sync_1"}],
        }
        response = client.get("/api/v1/platform/sync/history/t1", headers=api_key_header)
        assert response.status_code == 200
        assert response.json()["total"] == 1


class TestInitApiKey:
    @patch("src.routes.admin_multi_tenant._tenant_svc")
    def test_initializes_key(self, mock_svc, client, monkeypatch):
        monkeypatch.setenv("BOOTSTRAP_SECRET", "test-bootstrap-secret")
        mock_svc.initialize_api_key.return_value = {
            "message": "Tenant management API key initialized",
            "api_key": "sk-new-key",
            "warning": "Save this key securely.",
        }
        response = client.post(
            "/api/v1/platform/init-api-key",
            headers={"x-bootstrap-secret": "test-bootstrap-secret"},
        )
        assert response.status_code == 201
        assert "api_key" in response.json()

    def test_rejects_without_bootstrap_secret_env(self, client):
        """Endpoint fails closed when BOOTSTRAP_SECRET is not set."""
        response = client.post("/api/v1/platform/init-api-key")
        assert response.status_code == 401

    def test_rejects_wrong_bootstrap_secret(self, client, monkeypatch):
        monkeypatch.setenv("BOOTSTRAP_SECRET", "correct-secret")
        response = client.post(
            "/api/v1/platform/init-api-key",
            headers={"x-bootstrap-secret": "wrong-secret"},
        )
        assert response.status_code == 401


# ---------------------------------------------------------------------------
# OpenAPI Surface Test
# ---------------------------------------------------------------------------


class TestOpenAPISurface:
    """Verify all platform endpoints appear in the OpenAPI spec."""

    def test_all_platform_endpoints_registered(self, client):
        response = client.get("/openapi.json")
        assert response.status_code == 200
        spec = response.json()
        paths = spec["paths"]

        expected_paths = [
            "/api/v1/platform/health",
            "/api/v1/platform/tenants",
            "/api/v1/platform/tenants/{tenant_id}",
            "/api/v1/platform/sync/{tenant_id}",
            "/api/v1/platform/sync/status/{sync_id}",
            "/api/v1/platform/sync/history/{tenant_id}",
            "/api/v1/platform/sync/stats",
            "/api/v1/platform/sync/tenants",
            "/api/v1/platform/init-api-key",
        ]

        for path in expected_paths:
            assert path in paths, f"Missing endpoint in OpenAPI spec: {path}"

    def test_platform_endpoints_tagged(self, client):
        response = client.get("/openapi.json")
        spec = response.json()

        for path, methods in spec["paths"].items():
            if path.startswith("/api/v1/platform"):
                for method, details in methods.items():
                    if method in ("get", "post", "put", "delete"):
                        tags = details.get("tags", [])
                        assert "multi-tenant" in tags, f"{method.upper()} {path} missing 'multi-tenant' tag"
