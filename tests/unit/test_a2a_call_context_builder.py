"""Tests for A2A CallContextBuilder integration (salesagent-6n5v).

Validates that:
- AdCPCallContextBuilder exists and builds context from request.state.auth_context
- A2AStarletteApplication is constructed with AdCPCallContextBuilder
- Handler's _get_auth_token reads from ServerCallContext when context is provided
- Handler's _resolve_a2a_identity reads headers from ServerCallContext when context is provided
- ContextVar fallback still works when context is None (test path)

beads: salesagent-6n5v
"""

from a2a.server.context import ServerCallContext

from src.core.auth_context import AuthContext


class TestAdCPCallContextBuilderExists:
    """Verify AdCPCallContextBuilder class exists and follows SDK pattern."""

    def test_builder_class_exists(self):
        """AdCPCallContextBuilder must exist in context_builder module."""
        from src.a2a_server.context_builder import AdCPCallContextBuilder

        assert AdCPCallContextBuilder is not None

    def test_builder_inherits_call_context_builder(self):
        """AdCPCallContextBuilder must inherit from SDK's CallContextBuilder."""
        from a2a.server.apps.jsonrpc.jsonrpc_app import CallContextBuilder

        from src.a2a_server.context_builder import AdCPCallContextBuilder

        assert issubclass(AdCPCallContextBuilder, CallContextBuilder)

    def test_builder_has_build_method(self):
        """AdCPCallContextBuilder must implement build(request) -> ServerCallContext."""
        from src.a2a_server.context_builder import AdCPCallContextBuilder

        builder = AdCPCallContextBuilder()
        assert hasattr(builder, "build") and callable(builder.build)


class TestAdCPCallContextBuilderBehavior:
    """Verify builder correctly extracts auth from request.state."""

    def test_build_extracts_auth_context_from_request_state(self):
        """Builder should read AuthContext from request.state.auth_context."""
        from unittest.mock import MagicMock

        from src.a2a_server.context_builder import AdCPCallContextBuilder

        # Simulate a request with auth_context set by UnifiedAuthMiddleware
        request = MagicMock()
        auth_ctx = AuthContext(auth_token="test-token", headers={"host": "test.example.com"})
        request.state.auth_context = auth_ctx
        request.headers = MagicMock()
        request.headers.getlist = MagicMock(return_value=[])

        builder = AdCPCallContextBuilder()
        context = builder.build(request)

        assert isinstance(context, ServerCallContext)
        assert context.state["auth_context"] is auth_ctx

    def test_build_handles_missing_auth_context(self):
        """Builder should handle requests where auth_context is not set."""
        from unittest.mock import MagicMock

        from src.a2a_server.context_builder import AdCPCallContextBuilder

        request = MagicMock()
        request.state = MagicMock(spec=[])  # No auth_context attribute
        request.headers = MagicMock()
        request.headers.getlist = MagicMock(return_value=[])

        builder = AdCPCallContextBuilder()
        context = builder.build(request)

        assert isinstance(context, ServerCallContext)
        # Should have a default/unauthenticated AuthContext
        assert "auth_context" in context.state


class TestA2AAppUsesCustomContextBuilder:
    """Verify A2AStarletteApplication is wired with AdCPCallContextBuilder."""

    def test_app_py_uses_adcp_call_context_builder(self):
        """src/app.py must construct A2AStarletteApplication with context_builder."""
        import pathlib

        source = (pathlib.Path(__file__).resolve().parents[2] / "src" / "app.py").read_text()

        assert "context_builder" in source, (
            "A2AStarletteApplication in app.py must be constructed with context_builder parameter"
        )
        assert "AdCPCallContextBuilder" in source, (
            "A2AStarletteApplication must use AdCPCallContextBuilder, not DefaultCallContextBuilder"
        )


class TestHandlerReadsFromContext:
    """Verify handler methods prefer ServerCallContext over ContextVar."""

    def test_get_auth_token_accepts_context_parameter(self):
        """_get_auth_token must accept an optional context parameter."""
        import inspect

        from src.a2a_server.adcp_a2a_server import AdCPRequestHandler

        handler = AdCPRequestHandler()
        sig = inspect.signature(handler._get_auth_token)
        params = list(sig.parameters.keys())
        assert "context" in params, "_get_auth_token must accept a 'context' parameter to read from ServerCallContext"

    def test_get_auth_token_reads_from_context_when_provided(self):
        """_get_auth_token should read from context.state when context is provided."""
        from src.a2a_server.adcp_a2a_server import AdCPRequestHandler

        handler = AdCPRequestHandler()
        auth_ctx = AuthContext(auth_token="context-token", headers={"host": "test.example.com"})
        context = ServerCallContext(state={"auth_context": auth_ctx})

        token = handler._get_auth_token(context=context)
        assert token == "context-token", f"Expected 'context-token' from context.state, got {token!r}"

    def test_resolve_identity_accepts_context_parameter(self):
        """_resolve_a2a_identity must accept an optional context parameter."""
        import inspect

        from src.a2a_server.adcp_a2a_server import AdCPRequestHandler

        handler = AdCPRequestHandler()
        sig = inspect.signature(handler._resolve_a2a_identity)
        params = list(sig.parameters.keys())
        assert "context" in params, (
            "_resolve_a2a_identity must accept a 'context' parameter to read from ServerCallContext"
        )
