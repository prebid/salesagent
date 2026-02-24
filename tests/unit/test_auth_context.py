"""Tests for shared AuthContext model and middleware.

Validates that:
- AuthContext model exists with correct attributes
- Middleware populates request.state.auth_context
- get_auth_context() dependency reads from request.state
- Token extraction from Authorization and x-adcp-auth headers
- Unauthenticated requests get AuthContext with is_authenticated()=False

beads: salesagent-b61l.12
"""

from starlette.testclient import TestClient

# ---------------------------------------------------------------------------
# AuthContext Model Tests
# ---------------------------------------------------------------------------


class TestAuthContextModel:
    """Verify AuthContext model exists with correct attributes."""

    def test_auth_context_exists(self):
        """AuthContext class must exist."""
        from src.core.auth_context import AuthContext

        ctx = AuthContext(
            tenant_id="t1",
            principal_id="p1",
            auth_token="tok",
            headers={"host": "example.com"},
        )
        assert ctx.tenant_id == "t1"
        assert ctx.principal_id == "p1"
        assert ctx.auth_token == "tok"

    def test_is_authenticated_with_principal(self):
        """is_authenticated() returns True when principal_id is set."""
        from src.core.auth_context import AuthContext

        ctx = AuthContext(
            tenant_id="t1",
            principal_id="p1",
            auth_token="tok",
            headers={},
        )
        assert ctx.is_authenticated() is True

    def test_is_not_authenticated_without_principal(self):
        """is_authenticated() returns False when principal_id is None."""
        from src.core.auth_context import AuthContext

        ctx = AuthContext(
            tenant_id="t1",
            principal_id=None,
            auth_token=None,
            headers={},
        )
        assert ctx.is_authenticated() is False

    def test_unauthenticated_factory(self):
        """AuthContext.unauthenticated() creates a non-authenticated context."""
        from src.core.auth_context import AuthContext

        ctx = AuthContext.unauthenticated(headers={"host": "localhost"})
        assert ctx.is_authenticated() is False
        assert ctx.principal_id is None
        assert ctx.auth_token is None


# ---------------------------------------------------------------------------
# Middleware Tests
# ---------------------------------------------------------------------------


class TestAuthContextMiddleware:
    """Verify middleware populates request.state.auth_context."""

    def test_bearer_token_extracted(self):
        """Middleware extracts token from Authorization: Bearer header."""
        from src.app import app
        from src.core.auth_context import get_auth_context

        @app.get("/test-auth/bearer-check")
        def check_bearer(auth_ctx=get_auth_context):
            return {"token": auth_ctx.auth_token}

        client = TestClient(app)
        response = client.get(
            "/test-auth/bearer-check",
            headers={"Authorization": "Bearer my-test-token"},
        )
        assert response.status_code == 200
        assert response.json()["token"] == "my-test-token"

    def test_adcp_auth_header_extracted(self):
        """Middleware extracts token from x-adcp-auth header."""
        from src.app import app
        from src.core.auth_context import get_auth_context

        @app.get("/test-auth/adcp-check")
        def check_adcp(auth_ctx=get_auth_context):
            return {"token": auth_ctx.auth_token}

        client = TestClient(app)
        response = client.get(
            "/test-auth/adcp-check",
            headers={"x-adcp-auth": "adcp-token-123"},
        )
        assert response.status_code == 200
        assert response.json()["token"] == "adcp-token-123"

    def test_no_auth_gives_unauthenticated_context(self):
        """Requests without auth headers get AuthContext with is_authenticated()=False."""
        from src.app import app
        from src.core.auth_context import get_auth_context

        @app.get("/test-auth/noauth-check")
        def check_noauth(auth_ctx=get_auth_context):
            return {"authenticated": auth_ctx.is_authenticated()}

        client = TestClient(app)
        response = client.get("/test-auth/noauth-check")
        assert response.status_code == 200
        assert response.json()["authenticated"] is False

    def test_headers_captured_in_context(self):
        """Middleware captures request headers in AuthContext."""
        from src.app import app
        from src.core.auth_context import get_auth_context

        @app.get("/test-auth/headers-check")
        def check_headers(auth_ctx=get_auth_context):
            return {"has_host": "host" in auth_ctx.headers}

        client = TestClient(app)
        response = client.get("/test-auth/headers-check")
        assert response.status_code == 200
        assert response.json()["has_host"] is True
