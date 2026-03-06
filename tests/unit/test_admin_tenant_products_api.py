"""Tests for the tenant admin products & principals API router.

Uses TestClient against the FastAPI app to verify:
- Product CRUD endpoints
- Creative formats endpoint
- Principal CRUD endpoints
- Token regeneration
- GAM advertiser search
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
def auth_header():
    return {"Authorization": "Bearer valid-token"}


# ---------------------------------------------------------------------------
# Product Tests
# ---------------------------------------------------------------------------


class TestListProducts:
    @patch("src.routes.admin_tenant._product_svc")
    def test_returns_products(self, mock_svc, client, auth_header):
        mock_svc.list_products.return_value = {
            "products": [
                {"product_id": "prod_1", "name": "Homepage Banner", "delivery_type": "guaranteed"},
            ],
            "count": 1,
        }
        response = client.get(f"/api/v1/admin/{TENANT_ID}/products", headers=auth_header)
        assert response.status_code == 200
        assert response.json()["count"] == 1

    @patch("src.routes.admin_tenant._product_svc")
    def test_route_passes_tenant_id_string_not_orm_object(self, mock_svc, client, auth_header):
        """Detached Tenant safety: route handlers must pass tenant_id (str) to services, not the Tenant ORM object.

        The Tenant returned by require_tenant_admin is expunged from the DB session.
        Accessing lazy-loaded relationships (tenant.products, tenant.principals, etc.)
        on an expunged Tenant would raise DetachedInstanceError. This test guards
        against future regressions where a developer accidentally passes _tenant to
        a service method that accesses relationships.
        """
        mock_svc.list_products.return_value = {"products": [], "count": 0}
        client.get(f"/api/v1/admin/{TENANT_ID}/products", headers=auth_header)
        # Service must be called with the string tenant_id from the URL, not the Tenant ORM object
        mock_svc.list_products.assert_called_once_with(TENANT_ID)


class TestCreateProduct:
    @patch("src.routes.admin_tenant._product_svc")
    def test_creates_product(self, mock_svc, client, auth_header):
        mock_svc.create_product.return_value = {
            "product_id": "prod_abc",
            "tenant_id": TENANT_ID,
            "name": "Homepage Banner",
        }
        response = client.post(
            f"/api/v1/admin/{TENANT_ID}/products",
            json={
                "name": "Homepage Banner",
                "delivery_type": "guaranteed",
                "pricing_options": [{"pricing_model": "cpm", "rate": 15.0, "currency": "USD"}],
                "property_tags": ["all_inventory"],
            },
            headers=auth_header,
        )
        assert response.status_code == 201
        assert response.json()["product_id"] == "prod_abc"

    def test_missing_name_returns_422(self, client, auth_header):
        """FastAPI validates required fields."""
        response = client.post(
            f"/api/v1/admin/{TENANT_ID}/products",
            json={"delivery_type": "guaranteed"},  # Missing name
            headers=auth_header,
        )
        assert response.status_code == 422


class TestGetProduct:
    @patch("src.routes.admin_tenant._product_svc")
    def test_returns_product(self, mock_svc, client, auth_header):
        mock_svc.get_product.return_value = {
            "product_id": "prod_1",
            "name": "Banner",
            "pricing_options": [],
        }
        response = client.get(f"/api/v1/admin/{TENANT_ID}/products/prod_1", headers=auth_header)
        assert response.status_code == 200
        assert response.json()["product_id"] == "prod_1"

    @patch("src.routes.admin_tenant._product_svc")
    def test_not_found_returns_404(self, mock_svc, client, auth_header):
        from src.core.exceptions import AdCPNotFoundError

        mock_svc.get_product.side_effect = AdCPNotFoundError("Not found")
        response = client.get(f"/api/v1/admin/{TENANT_ID}/products/nonexistent", headers=auth_header)
        assert response.status_code == 404


class TestUpdateProduct:
    @patch("src.routes.admin_tenant._product_svc")
    def test_updates_product(self, mock_svc, client, auth_header):
        mock_svc.update_product.return_value = {"product_id": "prod_1", "name": "Updated"}
        response = client.put(
            f"/api/v1/admin/{TENANT_ID}/products/prod_1",
            json={"name": "Updated"},
            headers=auth_header,
        )
        assert response.status_code == 200


class TestDeleteProduct:
    @patch("src.routes.admin_tenant._product_svc")
    def test_deletes_product(self, mock_svc, client, auth_header):
        mock_svc.delete_product.return_value = {"message": "Deleted", "product_id": "prod_1"}
        response = client.delete(f"/api/v1/admin/{TENANT_ID}/products/prod_1", headers=auth_header)
        assert response.status_code == 200


class TestCreativeFormats:
    @patch("src.routes.admin_tenant._product_svc")
    def test_returns_formats(self, mock_svc, client, auth_header):
        mock_svc.list_creative_formats.return_value = {
            "formats": [{"id": "display_300x250", "name": "Medium Rectangle"}],
            "count": 1,
        }
        response = client.get(f"/api/v1/admin/{TENANT_ID}/creative-formats", headers=auth_header)
        assert response.status_code == 200
        assert response.json()["count"] == 1


# ---------------------------------------------------------------------------
# Principal Tests
# ---------------------------------------------------------------------------


class TestListPrincipals:
    @patch("src.routes.admin_tenant._principal_svc")
    def test_returns_principals(self, mock_svc, client, auth_header):
        mock_svc.list_principals.return_value = {
            "principals": [
                {"principal_id": "prin_1", "name": "Acme Corp", "media_buy_count": 3},
            ],
            "count": 1,
        }
        response = client.get(f"/api/v1/admin/{TENANT_ID}/principals", headers=auth_header)
        assert response.status_code == 200
        assert response.json()["count"] == 1


class TestCreatePrincipal:
    @patch("src.routes.admin_tenant._principal_svc")
    def test_creates_principal_returns_token(self, mock_svc, client, auth_header):
        mock_svc.create_principal.return_value = {
            "principal_id": "prin_abc",
            "name": "Brand X",
            "access_token": "tok_secret123",
        }
        response = client.post(
            f"/api/v1/admin/{TENANT_ID}/principals",
            json={"name": "Brand X"},
            headers=auth_header,
        )
        assert response.status_code == 201
        data = response.json()
        assert data["principal_id"] == "prin_abc"
        assert data["access_token"] == "tok_secret123"


class TestGetPrincipal:
    @patch("src.routes.admin_tenant._principal_svc")
    def test_returns_principal(self, mock_svc, client, auth_header):
        mock_svc.get_principal.return_value = {
            "principal_id": "prin_1",
            "name": "Acme",
            "media_buy_count": 5,
        }
        response = client.get(f"/api/v1/admin/{TENANT_ID}/principals/prin_1", headers=auth_header)
        assert response.status_code == 200


class TestUpdatePrincipal:
    @patch("src.routes.admin_tenant._principal_svc")
    def test_updates_principal(self, mock_svc, client, auth_header):
        mock_svc.update_principal.return_value = {"principal_id": "prin_1", "name": "Updated"}
        response = client.put(
            f"/api/v1/admin/{TENANT_ID}/principals/prin_1",
            json={"name": "Updated"},
            headers=auth_header,
        )
        assert response.status_code == 200


class TestDeletePrincipal:
    @patch("src.routes.admin_tenant._principal_svc")
    def test_deletes_principal(self, mock_svc, client, auth_header):
        mock_svc.delete_principal.return_value = {"message": "Deleted", "principal_id": "prin_1"}
        response = client.delete(f"/api/v1/admin/{TENANT_ID}/principals/prin_1", headers=auth_header)
        assert response.status_code == 200


class TestRegenerateToken:
    @patch("src.routes.admin_tenant._principal_svc")
    def test_regenerates_token(self, mock_svc, client, auth_header):
        mock_svc.regenerate_token.return_value = {
            "principal_id": "prin_1",
            "access_token": "tok_new_secret",
            "message": "Token regenerated",
        }
        response = client.post(
            f"/api/v1/admin/{TENANT_ID}/principals/prin_1/regenerate-token",
            headers=auth_header,
        )
        assert response.status_code == 200
        assert response.json()["access_token"] == "tok_new_secret"


class TestSearchGAMAdvertisers:
    @patch("src.routes.admin_tenant._principal_svc")
    def test_returns_advertisers(self, mock_svc, client, auth_header):
        mock_svc.search_gam_advertisers.return_value = {
            "advertisers": [{"id": "123", "name": "Acme"}],
            "count": 1,
        }
        response = client.post(
            f"/api/v1/admin/{TENANT_ID}/gam/advertisers",
            json={"search": "Acme"},
            headers=auth_header,
        )
        assert response.status_code == 200
        assert response.json()["count"] == 1


# ---------------------------------------------------------------------------
# OpenAPI Surface Test
# ---------------------------------------------------------------------------


class TestPhase4OpenAPISurface:
    """Verify all Phase 4 endpoints appear in the OpenAPI spec."""

    def test_all_product_and_principal_endpoints_registered(self, client):
        response = client.get("/openapi.json")
        assert response.status_code == 200
        paths = response.json()["paths"]

        expected_paths = [
            "/api/v1/admin/{tenant_id}/products",
            "/api/v1/admin/{tenant_id}/products/{product_id}",
            "/api/v1/admin/{tenant_id}/creative-formats",
            "/api/v1/admin/{tenant_id}/principals",
            "/api/v1/admin/{tenant_id}/principals/{principal_id}",
            "/api/v1/admin/{tenant_id}/principals/{principal_id}/regenerate-token",
            "/api/v1/admin/{tenant_id}/gam/advertisers",
        ]

        for path in expected_paths:
            assert path in paths, f"Missing endpoint in OpenAPI spec: {path}"
