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
from tests.helpers import assert_envelope_shape

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
    def test_endpoint_accepts_buying_mode(self, mock_impl, mock_resolve):
        """A spec-valid ``buying_mode`` must be accepted.

        buying_mode is the sole entry in the required array of
        get-products-request at the pinned spec (3.1.1), so a spec-valid client
        always sends it. Under the dev/CI extra="forbid" policy an undeclared
        field is rejected, so REST has to declare it.

        Not framed as MCP/A2A parity: MCP rejects buying_mode in dev/CI
        (VALIDATION_ERROR, "Unexpected keyword argument") because its tool
        schema is additionalProperties: false without it, and A2A's acceptance
        is meaningless since it accepts any unknown field.

        Deletion oracle: drop ``buying_mode`` from GetProductsBody and this
        reddens with HTTP 400 (INVALID_REQUEST, recovery=correctable) —
        not the 422 an earlier version of this docstring claimed. FastAPI's
        default 422 is replaced by the RequestValidationError handler in
        src/app.py, which maps INVALID_REQUEST to 400.
        """
        from src.core.schemas import GetProductsResponse

        mock_impl.return_value = GetProductsResponse(products=[], message="test")

        response = client.post(
            "/api/v1/products",
            json={"brief": "video ads", "buying_mode": "brief"},
            headers={"Authorization": "Bearer test-token"},
        )
        assert response.status_code == 200, (
            f"buying_mode should be accepted, got {response.status_code}: {response.text}"
        )

    @patch("src.core.resolved_identity.resolve_identity", return_value=_MOCK_IDENTITY)
    @patch("src.core.tools.products._get_products_impl")
    def test_undeclared_field_is_rejected_with_the_adcp_envelope(self, mock_impl, mock_resolve):
        """An undeclared field 400s with the two-layer envelope, not FastAPI's 422.

        This is the other half of the buying_mode test and the reason that one
        cannot pass vacuously: it proves extra="forbid" is actually engaged on
        this route. Without it, a GetProductsBody that had silently fallen back
        to extra="ignore" would accept buying_mode for the wrong reason and the
        positive test would still be green.

        It also backs this module's docstring claim that error responses use the
        AdCPError format, which until now no test in the file asserted.
        """
        from src.core.schemas import GetProductsResponse

        mock_impl.return_value = GetProductsResponse(products=[], message="test")

        response = client.post(
            "/api/v1/products",
            json={"brief": "video ads", "not_a_real_field": "x"},
            headers={"Authorization": "Bearer test-token"},
        )

        assert response.status_code == 400, f"expected 400, got {response.status_code}: {response.text}"
        assert_envelope_shape(response.json(), "INVALID_REQUEST", recovery="correctable")

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
