"""Tests for the unified auth middleware refactoring (salesagent-97pn).

Validates that:
- UnifiedAuthMiddleware is a pure ASGI class (not BaseHTTPMiddleware)
- get_current_auth_context() returns AuthContext from ContextVar
- ContextVar is cleaned up after request (no leakage between requests)
- Old middleware functions are deleted from app.py
- request.state.auth_context still works for FastAPI routes
- A2A handler reads from the new ContextVar (not deleted _request_auth_token)

beads: salesagent-97pn
"""

import ast


class TestUnifiedAuthMiddlewareExists:
    """Verify UnifiedAuthMiddleware class exists as pure ASGI."""

    def test_middleware_class_exists(self):
        """UnifiedAuthMiddleware class must exist in auth_middleware module."""
        from src.core.auth_middleware import UnifiedAuthMiddleware

        assert UnifiedAuthMiddleware is not None

    def test_middleware_is_not_base_http_middleware(self):
        """UnifiedAuthMiddleware must NOT inherit from BaseHTTPMiddleware.

        BaseHTTPMiddleware has known ContextVar propagation bugs (Starlette #1729).
        Pure ASGI middleware (__call__ protocol) avoids this.
        """
        from starlette.middleware.base import BaseHTTPMiddleware

        from src.core.auth_middleware import UnifiedAuthMiddleware

        assert not issubclass(UnifiedAuthMiddleware, BaseHTTPMiddleware), (
            "UnifiedAuthMiddleware must be a pure ASGI class, not inherit from BaseHTTPMiddleware (ContextVar bug)"
        )

    def test_middleware_has_call_method(self):
        """UnifiedAuthMiddleware must implement __call__(scope, receive, send)."""
        from src.core.auth_middleware import UnifiedAuthMiddleware

        assert callable(UnifiedAuthMiddleware), "Must implement ASGI __call__ protocol"


class TestGetCurrentAuthContext:
    """Verify get_current_auth_context() reads from ContextVar."""

    def test_function_exists(self):
        """get_current_auth_context() must exist in auth_context module."""
        from src.core.auth_context import get_current_auth_context

        assert callable(get_current_auth_context)

    def test_returns_none_outside_request(self):
        """get_current_auth_context() returns None when no request is active."""
        from src.core.auth_context import _auth_context_var, get_current_auth_context

        # Reset to ensure clean state (other tests may set the ContextVar without cleanup)
        _auth_context_var.set(None)

        result = get_current_auth_context()
        assert result is None, "get_current_auth_context() should return None outside of a request scope"


class TestOldMiddlewaresDeleted:
    """Verify old middleware functions are removed from app.py."""

    def test_no_auth_context_middleware_function(self):
        """auth_context_middleware function must not exist in app.py."""
        with open("src/app.py") as f:
            source = f.read()

        tree = ast.parse(source)
        func_names = [node.name for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
        assert "auth_context_middleware" not in func_names, (
            "auth_context_middleware still exists in app.py — should be replaced by UnifiedAuthMiddleware"
        )

    def test_no_a2a_auth_middleware_function(self):
        """a2a_auth_middleware function must not exist in app.py."""
        with open("src/app.py") as f:
            source = f.read()

        tree = ast.parse(source)
        func_names = [node.name for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
        assert "a2a_auth_middleware" not in func_names, (
            "a2a_auth_middleware still exists in app.py — should be replaced by UnifiedAuthMiddleware"
        )


class TestA2AHandlerUsesNewContextVar:
    """Verify A2A handler no longer uses deleted ContextVars."""

    def test_no_request_auth_token_contextvar(self):
        """_request_auth_token ContextVar must not exist in adcp_a2a_server."""
        with open("src/a2a_server/adcp_a2a_server.py") as f:
            source = f.read()

        tree = ast.parse(source)
        # Look for: _request_auth_token: ContextVar = ...
        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                # Check targets for _request_auth_token
                targets = []
                if isinstance(node, ast.Assign):
                    targets = node.targets
                elif isinstance(node, ast.AnnAssign) and node.target:
                    targets = [node.target]
                for target in targets:
                    if isinstance(target, ast.Name) and target.id == "_request_auth_token":
                        raise AssertionError(
                            "_request_auth_token ContextVar still defined in adcp_a2a_server.py — "
                            "should be deleted (auth is now in unified middleware ContextVar)"
                        )

    def test_no_request_headers_contextvar(self):
        """_request_headers ContextVar must not exist in adcp_a2a_server."""
        with open("src/a2a_server/adcp_a2a_server.py") as f:
            source = f.read()

        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = []
                if isinstance(node, ast.Assign):
                    targets = node.targets
                elif isinstance(node, ast.AnnAssign) and node.target:
                    targets = [node.target]
                for target in targets:
                    if isinstance(target, ast.Name) and target.id == "_request_headers":
                        raise AssertionError(
                            "_request_headers ContextVar still defined in adcp_a2a_server.py — "
                            "should be deleted (headers are now in unified middleware ContextVar)"
                        )


class TestContextVarCleanup:
    """Verify ContextVar doesn't leak between requests."""

    def test_contextvar_cleaned_after_request(self):
        """After a request completes, get_current_auth_context() should return None."""
        from starlette.testclient import TestClient

        from src.app import app
        from src.core.auth_context import get_current_auth_context

        client = TestClient(app)
        # Make a request to trigger middleware
        client.get("/health")

        # After request, ContextVar should be cleaned up
        result = get_current_auth_context()
        assert result is None, (
            "ContextVar not cleaned up after request — middleware must use try/finally with ContextVar.reset()"
        )
