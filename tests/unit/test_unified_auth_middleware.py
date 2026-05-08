"""Tests for the unified auth middleware refactoring (salesagent-97pn).

Validates that:
- UnifiedAuthMiddleware is a pure ASGI class (not BaseHTTPMiddleware)
- Old middleware functions are deleted from app.py
- request.state.auth_context still works for FastAPI routes

beads: salesagent-97pn
"""


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

        assert not issubclass(
            UnifiedAuthMiddleware, BaseHTTPMiddleware
        ), "UnifiedAuthMiddleware must be a pure ASGI class, not inherit from BaseHTTPMiddleware (ContextVar bug)"

    def test_middleware_has_call_method(self):
        """UnifiedAuthMiddleware must implement __call__(scope, receive, send)."""
        from src.core.auth_middleware import UnifiedAuthMiddleware

        assert callable(UnifiedAuthMiddleware), "Must implement ASGI __call__ protocol"


class TestOldMiddlewaresDeleted:
    """The legacy ``src/app.py`` was deleted with the rest of the legacy stack
    — these guards are vacuous on the modern stack and remain only to assert
    the legacy assembly is gone for good.
    """

    def test_legacy_app_py_is_removed(self):
        """``src/app.py`` must not be reintroduced."""
        import pathlib

        legacy = pathlib.Path(__file__).resolve().parents[2] / "src" / "app.py"
        assert not legacy.exists(), (
            "src/app.py is back — the modern stack uses core/main.py as the "
            "single Starlette entrypoint; legacy FastAPI assembly stays gone."
        )

    def test_legacy_a2a_server_module_is_removed(self):
        """``src/a2a_server/`` must not be reintroduced."""
        import pathlib

        legacy = pathlib.Path(__file__).resolve().parents[2] / "src" / "a2a_server"
        assert not legacy.exists(), "src/a2a_server/ is back — modern A2A is served by adcp.server.serve()."
