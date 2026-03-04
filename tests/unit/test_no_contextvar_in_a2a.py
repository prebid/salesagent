"""Regression tests: A2A auth must NOT use ContextVar fallback.

Core invariant: All auth context flows through explicit function parameters
(scope["state"] -> ServerCallContext -> handler methods), never through
ambient ContextVar state.

These tests verify the ContextVar fallback is removed from the A2A handler.
They FAIL before the refactoring (TDD red step) and PASS after.

beads: salesagent-zmb1
"""

from unittest.mock import patch

from a2a.server.context import ServerCallContext

from src.core.auth_context import AuthContext


class TestNoContextVarFallbackInA2AHandler:
    """A2A handler methods must not fall back to ContextVar."""

    def test_get_auth_token_returns_none_without_context(self):
        """_get_auth_token(context=None) should return None, not read ContextVar."""
        from src.a2a_server.adcp_a2a_server import AdCPRequestHandler

        handler = AdCPRequestHandler()
        result = handler._get_auth_token(context=None)
        assert result is None

    def test_get_auth_token_reads_from_explicit_context(self):
        """_get_auth_token should read token from explicit ServerCallContext."""
        from src.a2a_server.adcp_a2a_server import AdCPRequestHandler

        handler = AdCPRequestHandler()
        auth_ctx = AuthContext(auth_token="explicit-token", headers={"host": "test.example.com"})
        context = ServerCallContext(state={"auth_context": auth_ctx})
        result = handler._get_auth_token(context=context)
        assert result == "explicit-token"

    def test_resolve_a2a_identity_uses_context_headers_not_contextvar(self):
        """_resolve_a2a_identity should read headers from context, not ContextVar."""
        from src.a2a_server.adcp_a2a_server import AdCPRequestHandler
        from src.core.resolved_identity import ResolvedIdentity

        handler = AdCPRequestHandler()
        ctx_headers = {"host": "context-host.example.com", "x-adcp-tenant": "from-context"}
        auth_ctx = AuthContext(auth_token="test-token", headers=ctx_headers)
        context = ServerCallContext(state={"auth_context": auth_ctx})

        mock_identity = ResolvedIdentity(
            principal_id="test", tenant_id="from-context", tenant={"tenant_id": "from-context"}, protocol="a2a"
        )

        with patch("src.core.resolved_identity.resolve_identity", return_value=mock_identity) as mock_resolve:
            handler._resolve_a2a_identity("test-token", context=context)

        call_kwargs = mock_resolve.call_args.kwargs
        assert call_kwargs["headers"] == ctx_headers, (
            f"Expected headers from context ({ctx_headers}), got {call_kwargs['headers']}. "
            "Handler may still be reading from ContextVar."
        )

    def test_resolve_a2a_identity_uses_empty_headers_without_context(self):
        """_resolve_a2a_identity(context=None) should use empty headers, not ContextVar."""
        from src.a2a_server.adcp_a2a_server import AdCPRequestHandler
        from src.core.resolved_identity import ResolvedIdentity

        handler = AdCPRequestHandler()
        mock_identity = ResolvedIdentity(
            principal_id="test", tenant_id="default", tenant={"tenant_id": "default"}, protocol="a2a"
        )

        with patch("src.core.resolved_identity.resolve_identity", return_value=mock_identity) as mock_resolve:
            handler._resolve_a2a_identity("test-token", context=None)

        call_kwargs = mock_resolve.call_args.kwargs
        assert call_kwargs["headers"] == {}, (
            f"Expected empty headers for context=None, got {call_kwargs['headers']}. "
            "Handler may still be reading from ContextVar."
        )


class TestNoContextVarInMiddleware:
    """UnifiedAuthMiddleware must not write to _auth_context_var."""

    def test_middleware_module_does_not_export_auth_context_var(self):
        """auth_middleware module must not have _auth_context_var in its namespace."""
        import src.core.auth_middleware as mw_mod

        assert not hasattr(mw_mod, "_auth_context_var"), (
            "UnifiedAuthMiddleware still imports _auth_context_var. Only scope['state'] should be written to."
        )


class TestNoContextVarInfrastructure:
    """ContextVar infrastructure must not exist in auth_context module."""

    def test_no_auth_context_var_in_module(self):
        """auth_context.py must not define _auth_context_var."""
        import src.core.auth_context as ac_mod

        assert not hasattr(ac_mod, "_auth_context_var"), (
            "_auth_context_var still exists in auth_context module. Remove it after all consumers are migrated."
        )

    def test_no_get_current_auth_context_in_module(self):
        """auth_context.py must not define get_current_auth_context."""
        import src.core.auth_context as ac_mod

        assert not hasattr(ac_mod, "get_current_auth_context"), (
            "get_current_auth_context() still exists in auth_context module. "
            "Remove it after all consumers are migrated."
        )
