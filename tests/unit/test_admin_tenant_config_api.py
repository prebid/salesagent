"""Tests for the tenant admin configuration API router.

Uses TestClient against the FastAPI app to verify:
- Auth enforcement (Bearer token per tenant)
- Adapter config endpoints
- Currency limit endpoints
- Property tag endpoints
"""

from unittest.mock import MagicMock, patch

import pytest
from starlette.testclient import TestClient

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

TENANT_ID = "tenant_test123"


@pytest.fixture
def mock_tenant():
    """A mock Tenant ORM object returned by auth."""
    tenant = MagicMock()
    tenant.tenant_id = TENANT_ID
    tenant.admin_token = "valid-token"
    return tenant


@pytest.fixture
def client(mock_tenant):
    """TestClient with auth dependency overridden."""
    from src.app import app
    from src.core.admin_auth import require_tenant_admin

    app.dependency_overrides[require_tenant_admin] = lambda: mock_tenant
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.pop(require_tenant_admin, None)


@pytest.fixture
def client_no_auth():
    """TestClient without auth override (for testing auth enforcement)."""
    from src.app import app
    from src.core.admin_auth import require_tenant_admin

    app.dependency_overrides.pop(require_tenant_admin, None)
    yield TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def auth_header():
    return {"Authorization": "Bearer valid-token"}


# ---------------------------------------------------------------------------
# Auth Tests
# ---------------------------------------------------------------------------


class TestTenantAdminAuthEnforcement:
    """Verify Bearer token auth is enforced on tenant admin endpoints."""

    def test_adapter_config_requires_auth(self, client_no_auth):
        """Without valid auth, endpoints should return 401."""
        response = client_no_auth.get(f"/api/v1/admin/{TENANT_ID}/adapter")
        assert response.status_code in (401, 403)


# ---------------------------------------------------------------------------
# Adapter Config Tests
# ---------------------------------------------------------------------------


class TestGetAdapterConfig:
    @patch("src.routes.admin_tenant._adapter_svc")
    def test_returns_config(self, mock_svc, client, auth_header):
        mock_svc.get_adapter_config.return_value = {
            "adapter_type": "mock",
            "config": {"mock_dry_run": False},
        }
        response = client.get(f"/api/v1/admin/{TENANT_ID}/adapter", headers=auth_header)
        assert response.status_code == 200
        assert response.json()["adapter_type"] == "mock"

    @patch("src.routes.admin_tenant._adapter_svc")
    def test_not_found_returns_404(self, mock_svc, client, auth_header):
        from src.core.exceptions import AdCPNotFoundError

        mock_svc.get_adapter_config.side_effect = AdCPNotFoundError("No adapter")
        response = client.get(f"/api/v1/admin/{TENANT_ID}/adapter", headers=auth_header)
        assert response.status_code == 404


class TestSaveAdapterConfig:
    @patch("src.routes.admin_tenant._adapter_svc")
    def test_saves_config(self, mock_svc, client, auth_header):
        mock_svc.save_adapter_config.return_value = {
            "adapter_type": "mock",
            "tenant_id": TENANT_ID,
            "updated_at": "2025-01-01T00:00:00",
        }
        response = client.put(
            f"/api/v1/admin/{TENANT_ID}/adapter",
            json={"adapter_type": "mock", "config": {"mock_dry_run": True}},
            headers=auth_header,
        )
        assert response.status_code == 200
        assert response.json()["adapter_type"] == "mock"


class TestTestConnection:
    @patch("src.routes.admin_tenant._adapter_svc")
    def test_connection_success(self, mock_svc, client, auth_header):
        mock_svc.test_connection.return_value = {"success": True, "message": "Mock adapter connected"}
        response = client.post(f"/api/v1/admin/{TENANT_ID}/adapter/test-connection", headers=auth_header)
        assert response.status_code == 200
        assert response.json()["success"] is True


class TestGetCapabilities:
    @patch("src.routes.admin_tenant._adapter_svc")
    def test_returns_capabilities(self, mock_svc, client, auth_header):
        mock_svc.get_adapter_config.return_value = {"adapter_type": "mock"}
        mock_svc.get_capabilities.return_value = {
            "supports_inventory_sync": False,
            "supported_pricing_models": ["cpm", "cpc"],
        }
        response = client.get(f"/api/v1/admin/{TENANT_ID}/adapter/capabilities", headers=auth_header)
        assert response.status_code == 200
        assert "supported_pricing_models" in response.json()


# ---------------------------------------------------------------------------
# Currency Limit Tests
# ---------------------------------------------------------------------------


class TestListCurrencyLimits:
    @patch("src.routes.admin_tenant._currency_svc")
    def test_returns_limits(self, mock_svc, client, auth_header):
        mock_svc.list_limits.return_value = [
            {"tenant_id": TENANT_ID, "currency_code": "USD", "min_package_budget": 100.0},
        ]
        response = client.get(f"/api/v1/admin/{TENANT_ID}/currency-limits", headers=auth_header)
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["currency_code"] == "USD"


class TestCreateCurrencyLimit:
    @patch("src.routes.admin_tenant._currency_svc")
    def test_creates_limit(self, mock_svc, client, auth_header):
        mock_svc.create_limit.return_value = {
            "tenant_id": TENANT_ID,
            "currency_code": "USD",
            "min_package_budget": 100.0,
            "max_daily_package_spend": 5000.0,
        }
        response = client.post(
            f"/api/v1/admin/{TENANT_ID}/currency-limits",
            json={"currency_code": "USD", "min_package_budget": 100, "max_daily_package_spend": 5000},
            headers=auth_header,
        )
        assert response.status_code == 201
        assert response.json()["currency_code"] == "USD"

    def test_invalid_currency_code_returns_422(self, client, auth_header):
        """Currency code too short should fail Pydantic validation."""
        response = client.post(
            f"/api/v1/admin/{TENANT_ID}/currency-limits",
            json={"currency_code": "US"},  # Too short
            headers=auth_header,
        )
        assert response.status_code == 422


class TestUpdateCurrencyLimit:
    @patch("src.routes.admin_tenant._currency_svc")
    def test_updates_limit(self, mock_svc, client, auth_header):
        mock_svc.update_limit.return_value = {
            "tenant_id": TENANT_ID,
            "currency_code": "USD",
            "min_package_budget": 200.0,
        }
        response = client.put(
            f"/api/v1/admin/{TENANT_ID}/currency-limits/USD",
            json={"min_package_budget": 200},
            headers=auth_header,
        )
        assert response.status_code == 200
        assert response.json()["min_package_budget"] == 200.0


class TestDeleteCurrencyLimit:
    @patch("src.routes.admin_tenant._currency_svc")
    def test_deletes_limit(self, mock_svc, client, auth_header):
        mock_svc.delete_limit.return_value = {"message": "Deleted", "currency_code": "USD"}
        response = client.delete(f"/api/v1/admin/{TENANT_ID}/currency-limits/USD", headers=auth_header)
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# Property Tag Tests
# ---------------------------------------------------------------------------


class TestListPropertyTags:
    @patch("src.routes.admin_tenant._tag_svc")
    def test_returns_tags(self, mock_svc, client, auth_header):
        mock_svc.list_tags.return_value = [
            {"tag_id": "all_inventory", "tenant_id": TENANT_ID, "name": "All Inventory", "description": "Default"},
        ]
        response = client.get(f"/api/v1/admin/{TENANT_ID}/property-tags", headers=auth_header)
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["tag_id"] == "all_inventory"


class TestCreatePropertyTag:
    @patch("src.routes.admin_tenant._tag_svc")
    def test_creates_tag(self, mock_svc, client, auth_header):
        mock_svc.create_tag.return_value = {
            "tag_id": "sports",
            "tenant_id": TENANT_ID,
            "name": "Sports",
            "description": "Sports section inventory",
        }
        response = client.post(
            f"/api/v1/admin/{TENANT_ID}/property-tags",
            json={"tag_id": "sports", "name": "Sports", "description": "Sports section inventory"},
            headers=auth_header,
        )
        assert response.status_code == 201
        assert response.json()["tag_id"] == "sports"


class TestDeletePropertyTag:
    @patch("src.routes.admin_tenant._tag_svc")
    def test_deletes_tag(self, mock_svc, client, auth_header):
        mock_svc.delete_tag.return_value = {"message": "Deleted", "tag_id": "sports"}
        response = client.delete(f"/api/v1/admin/{TENANT_ID}/property-tags/sports", headers=auth_header)
        assert response.status_code == 200

    @patch("src.routes.admin_tenant._tag_svc")
    def test_cannot_delete_default_tag(self, mock_svc, client, auth_header):
        from src.core.exceptions import AdCPValidationError

        mock_svc.delete_tag.side_effect = AdCPValidationError("Cannot delete 'all_inventory'")
        response = client.delete(f"/api/v1/admin/{TENANT_ID}/property-tags/all_inventory", headers=auth_header)
        assert response.status_code == 400


# ---------------------------------------------------------------------------
# OpenAPI Surface Test
# ---------------------------------------------------------------------------


class TestOpenAPISurface:
    """Verify all tenant admin endpoints appear in the OpenAPI spec."""

    def test_all_tenant_admin_endpoints_registered(self, client):
        response = client.get("/openapi.json")
        assert response.status_code == 200
        paths = response.json()["paths"]

        expected_paths = [
            "/api/v1/admin/{tenant_id}/adapter",
            "/api/v1/admin/{tenant_id}/adapter/test-connection",
            "/api/v1/admin/{tenant_id}/adapter/capabilities",
            "/api/v1/admin/{tenant_id}/currency-limits",
            "/api/v1/admin/{tenant_id}/currency-limits/{currency_code}",
            "/api/v1/admin/{tenant_id}/property-tags",
            "/api/v1/admin/{tenant_id}/property-tags/{tag_id}",
        ]

        for path in expected_paths:
            assert path in paths, f"Missing endpoint in OpenAPI spec: {path}"

    def test_tenant_admin_endpoints_tagged(self, client):
        response = client.get("/openapi.json")
        spec = response.json()

        for path, methods in spec["paths"].items():
            if path.startswith("/api/v1/admin/"):
                for method, details in methods.items():
                    if method in ("get", "post", "put", "delete"):
                        tags = details.get("tags", [])
                        assert "tenant-admin" in tags, f"{method.upper()} {path} missing 'tenant-admin' tag"
