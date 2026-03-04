"""Tests for shared AuthContext model and middleware.

Validates that:
- AuthContext model exists with correct attributes (auth_token, headers)
- Middleware populates request.state.auth_context
- get_auth_context() dependency reads from request.state
- Token extraction from Authorization and x-adcp-auth headers
- Unauthenticated requests get AuthContext with auth_token=None

beads: salesagent-b61l.12
"""

from starlette.testclient import TestClient

# ---------------------------------------------------------------------------
# AuthContext Model Tests
# ---------------------------------------------------------------------------


class TestAuthContextModel:
    """Verify AuthContext model exists with correct attributes."""

    def test_auth_context_exists(self):
        """AuthContext class must exist with auth_token and headers."""
        from src.core.auth_context import AuthContext

        ctx = AuthContext(
            auth_token="tok",
            headers={"host": "example.com"},
        )
        assert ctx.auth_token == "tok"
        assert ctx.headers == {"host": "example.com"}

    def test_unauthenticated_factory(self):
        """AuthContext.unauthenticated() creates a context with no token."""
        from src.core.auth_context import AuthContext

        ctx = AuthContext.unauthenticated(headers={"host": "localhost"})
        assert ctx.auth_token is None
        assert ctx.headers == {"host": "localhost"}

    def test_auth_context_is_frozen(self):
        """AuthContext should be immutable (frozen dataclass)."""
        import dataclasses

        from src.core.auth_context import AuthContext

        assert dataclasses.fields(AuthContext), "AuthContext should be a dataclass"
        ctx = AuthContext(auth_token="tok")
        import pytest

        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            ctx.auth_token = "other"  # type: ignore[misc]


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

    def test_no_auth_gives_none_token(self):
        """Requests without auth headers get AuthContext with auth_token=None."""
        from src.app import app
        from src.core.auth_context import get_auth_context

        @app.get("/test-auth/noauth-check-v2")
        def check_noauth(auth_ctx=get_auth_context):
            return {"has_token": auth_ctx.auth_token is not None}

        client = TestClient(app)
        response = client.get("/test-auth/noauth-check-v2")
        assert response.status_code == 200
        assert response.json()["has_token"] is False

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
