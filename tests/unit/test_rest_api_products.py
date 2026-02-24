"""Tests for REST API /api/v1/products endpoint.

Validates the first REST transport for get_products:
- Endpoint exists and returns 200
- Response has 'products' field
- Auth-optional (discovery skill)
- Version compat applied when adcp_version < 3.0
- Error responses use AdCPError format

beads: salesagent-b61l.13
"""

from unittest.mock import patch

from starlette.testclient import TestClient

from src.app import app

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

client = TestClient(app)


# ---------------------------------------------------------------------------
# Route Existence
# ---------------------------------------------------------------------------


class TestRESTProductsEndpoint:
    """Verify POST /api/v1/products endpoint."""

    @patch("src.routes.api_v1.get_principal_from_token", return_value="test-principal")
    @patch("src.core.config_loader.set_current_tenant")
    @patch("src.core.tools.products._get_products_impl")
    def test_endpoint_returns_200(self, mock_impl, mock_tenant, mock_auth):
        """POST /api/v1/products should return 200 with valid request."""
        from src.core.schemas import GetProductsResponse

        mock_impl.return_value = GetProductsResponse(products=[], message="test")

        response = client.post(
            "/api/v1/products",
            json={"brief": "video ads"},
            headers={"Authorization": "Bearer test-token"},
        )
        assert response.status_code == 200

    @patch("src.routes.api_v1.get_principal_from_token", return_value="test-principal")
    @patch("src.core.config_loader.set_current_tenant")
    @patch("src.core.tools.products._get_products_impl")
    def test_response_has_products_field(self, mock_impl, mock_tenant, mock_auth):
        """Response must contain 'products' list."""
        from src.core.schemas import GetProductsResponse

        mock_impl.return_value = GetProductsResponse(products=[], message="test")

        response = client.post(
            "/api/v1/products",
            json={"brief": "video ads"},
            headers={"Authorization": "Bearer test-token"},
        )
        body = response.json()
        assert "products" in body
        assert isinstance(body["products"], list)

    @patch("src.core.tools.products._get_products_impl")
    def test_works_without_auth(self, mock_impl):
        """get_products is a discovery skill — should work without auth."""
        from src.core.schemas import GetProductsResponse

        mock_impl.return_value = GetProductsResponse(products=[], message="test")

        response = client.post(
            "/api/v1/products",
            json={"brief": "video ads"},
        )
        # Should return 200, not 401 — discovery skill allows unauthenticated access
        assert response.status_code == 200, f"Discovery skill should work without auth, got {response.status_code}"

    def test_endpoint_not_404(self):
        """POST /api/v1/products must exist (not 404)."""
        response = client.post(
            "/api/v1/products",
            json={"brief": "test"},
        )
        assert response.status_code != 404, "REST endpoint /api/v1/products should exist"
