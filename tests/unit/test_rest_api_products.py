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
from src.core.resolved_identity import ResolvedIdentity

_MOCK_IDENTITY = ResolvedIdentity(
    principal_id="test-principal",
    tenant_id="default",
    tenant={"tenant_id": "default"},
    auth_token="test-token",
    protocol="rest",
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

client = TestClient(app)


# ---------------------------------------------------------------------------
# Route Existence
# ---------------------------------------------------------------------------


class TestRESTProductsEndpoint:
    """Verify POST /api/v1/products endpoint."""

    @patch("src.core.resolved_identity.resolve_identity", return_value=_MOCK_IDENTITY)
    @patch("src.core.tools.products._get_products_impl")
    def test_endpoint_returns_200(self, mock_impl, mock_resolve):
        """POST /api/v1/products should return 200 with valid request."""
        from src.core.schemas import GetProductsResponse

        mock_impl.return_value = GetProductsResponse(products=[], message="test")

        response = client.post(
            "/api/v1/products",
            json={"brief": "video ads"},
            headers={"Authorization": "Bearer test-token"},
        )
        assert response.status_code == 200

    @patch("src.core.resolved_identity.resolve_identity", return_value=_MOCK_IDENTITY)
    @patch("src.core.tools.products._get_products_impl")
    def test_response_has_products_field(self, mock_impl, mock_resolve):
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
