"""Tests for AdCP exception hierarchy and FastAPI exception handlers.

Validates that:
- Exception classes exist with proper inheritance and attributes
- FastAPI handlers return correct HTTP status codes and response format
- Exception → A2A SDK error mapping exists
- Exception → ToolError format mapping exists

beads: salesagent-b61l.11
"""

from starlette.testclient import TestClient

# ---------------------------------------------------------------------------
# Exception Hierarchy Tests
# ---------------------------------------------------------------------------


class TestExceptionHierarchy:
    """Verify AdCP exception classes exist with correct attributes."""

    def test_base_exception_exists(self):
        """AdCPError base class must exist."""
        from src.core.exceptions import AdCPError

        exc = AdCPError("test error")
        assert str(exc) == "test error"
        assert isinstance(exc, Exception)

    def test_validation_error(self):
        """AdCPValidationError must have status_code=400."""
        from src.core.exceptions import AdCPError, AdCPValidationError

        exc = AdCPValidationError("invalid field")
        assert isinstance(exc, AdCPError)
        assert exc.status_code == 400
        assert exc.error_code == "VALIDATION_ERROR"

    def test_authentication_error(self):
        """AdCPAuthenticationError must have status_code=401."""
        from src.core.exceptions import AdCPAuthenticationError, AdCPError

        exc = AdCPAuthenticationError("bad token")
        assert isinstance(exc, AdCPError)
        assert exc.status_code == 401
        assert exc.error_code == "AUTHENTICATION_ERROR"

    def test_authorization_error(self):
        """AdCPAuthorizationError must have status_code=403."""
        from src.core.exceptions import AdCPAuthorizationError, AdCPError

        exc = AdCPAuthorizationError("forbidden")
        assert isinstance(exc, AdCPError)
        assert exc.status_code == 403
        assert exc.error_code == "AUTHORIZATION_ERROR"

    def test_not_found_error(self):
        """AdCPNotFoundError must have status_code=404."""
        from src.core.exceptions import AdCPError, AdCPNotFoundError

        exc = AdCPNotFoundError("resource missing")
        assert isinstance(exc, AdCPError)
        assert exc.status_code == 404
        assert exc.error_code == "NOT_FOUND"

    def test_rate_limit_error(self):
        """AdCPRateLimitError must have status_code=429."""
        from src.core.exceptions import AdCPError, AdCPRateLimitError

        exc = AdCPRateLimitError("too many requests")
        assert isinstance(exc, AdCPError)
        assert exc.status_code == 429
        assert exc.error_code == "RATE_LIMIT_EXCEEDED"

    def test_adapter_error(self):
        """AdCPAdapterError must have status_code=502."""
        from src.core.exceptions import AdCPAdapterError, AdCPError

        exc = AdCPAdapterError("GAM unavailable")
        assert isinstance(exc, AdCPError)
        assert exc.status_code == 502
        assert exc.error_code == "ADAPTER_ERROR"

    def test_exception_carries_details(self):
        """Exceptions must support optional details dict."""
        from src.core.exceptions import AdCPValidationError

        details = {"field": "budget", "constraint": "must be positive"}
        exc = AdCPValidationError("invalid budget", details=details)
        assert exc.details == details

    def test_exception_to_dict(self):
        """Exceptions must be serializable to dict for response bodies."""
        from src.core.exceptions import AdCPValidationError

        exc = AdCPValidationError("bad field", details={"field": "name"})
        d = exc.to_dict()
        assert d["error_code"] == "VALIDATION_ERROR"
        assert d["message"] == "bad field"
        assert d["details"] == {"field": "name"}


# ---------------------------------------------------------------------------
# FastAPI Exception Handler Tests
# ---------------------------------------------------------------------------


class TestFastAPIExceptionHandlers:
    """Verify FastAPI exception handlers return correct HTTP responses."""

    def test_validation_error_returns_400(self):
        """AdCPValidationError raised in a route must return 400."""
        from src.app import app
        from src.core.exceptions import AdCPValidationError

        # Add a temporary test route that raises
        @app.get("/test-exc/validation")
        def raise_validation():
            raise AdCPValidationError("test validation error")

        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/test-exc/validation")
        assert response.status_code == 400
        body = response.json()
        assert body["error_code"] == "VALIDATION_ERROR"
        assert "test validation error" in body["message"]

    def test_authentication_error_returns_401(self):
        """AdCPAuthenticationError raised in a route must return 401."""
        from src.app import app
        from src.core.exceptions import AdCPAuthenticationError

        @app.get("/test-exc/auth")
        def raise_auth():
            raise AdCPAuthenticationError("bad token")

        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/test-exc/auth")
        assert response.status_code == 401
        body = response.json()
        assert body["error_code"] == "AUTHENTICATION_ERROR"

    def test_not_found_error_returns_404(self):
        """AdCPNotFoundError raised in a route must return 404."""
        from src.app import app
        from src.core.exceptions import AdCPNotFoundError

        @app.get("/test-exc/notfound")
        def raise_not_found():
            raise AdCPNotFoundError("resource gone")

        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/test-exc/notfound")
        assert response.status_code == 404
        body = response.json()
        assert body["error_code"] == "NOT_FOUND"

    def test_adapter_error_returns_502(self):
        """AdCPAdapterError raised in a route must return 502."""
        from src.app import app
        from src.core.exceptions import AdCPAdapterError

        @app.get("/test-exc/adapter")
        def raise_adapter():
            raise AdCPAdapterError("GAM down")

        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/test-exc/adapter")
        assert response.status_code == 502
        body = response.json()
        assert body["error_code"] == "ADAPTER_ERROR"

    def test_error_response_has_standard_envelope(self):
        """Error responses must have {error_code, message, details} envelope."""
        from src.app import app
        from src.core.exceptions import AdCPValidationError

        @app.get("/test-exc/envelope")
        def raise_with_details():
            raise AdCPValidationError("bad", details={"field": "x"})

        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/test-exc/envelope")
        body = response.json()
        assert "error_code" in body
        assert "message" in body
        assert "details" in body
        assert body["details"] == {"field": "x"}


# ---------------------------------------------------------------------------
# A2A Error Mapping Tests
# ---------------------------------------------------------------------------


class TestA2AErrorMapping:
    """Verify mapping from AdCP exceptions to A2A SDK error types."""

    def test_validation_maps_to_invalid_params(self):
        """AdCPValidationError should map to InvalidParamsError code (-32602)."""
        from src.core.exceptions import AdCPValidationError, to_a2a_error_code

        assert to_a2a_error_code(AdCPValidationError("x")) == -32602

    def test_auth_maps_to_invalid_request(self):
        """AdCPAuthenticationError should map to InvalidRequestError code (-32600)."""
        from src.core.exceptions import AdCPAuthenticationError, to_a2a_error_code

        assert to_a2a_error_code(AdCPAuthenticationError("x")) == -32600

    def test_not_found_maps_to_task_not_found(self):
        """AdCPNotFoundError should map to a not-found code."""
        from src.core.exceptions import AdCPNotFoundError, to_a2a_error_code

        assert to_a2a_error_code(AdCPNotFoundError("x")) == -32001

    def test_adapter_maps_to_internal_error(self):
        """AdCPAdapterError should map to InternalError code (-32603)."""
        from src.core.exceptions import AdCPAdapterError, to_a2a_error_code

        assert to_a2a_error_code(AdCPAdapterError("x")) == -32603
