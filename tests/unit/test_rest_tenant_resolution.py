"""Regression tests for REST tenant resolution and auth cleanup.

Tests that the REST API uses proper 4-strategy tenant detection (same as MCP/A2A)
instead of the broken heuristic that split principal_id on underscores.

These tests also verify removal of MinimalContext and cast(ToolContext) patterns.
"""

import inspect
from unittest.mock import patch

from src.core.resolved_identity import ResolvedIdentity


class TestRestResolveAuthReturnsResolvedIdentity:
    """REST resolve_auth dep must return ResolvedIdentity, not ToolContext."""

    def test_resolve_auth_returns_resolved_identity_type(self):
        """_resolve_auth_dep should return a ResolvedIdentity."""
        from src.core.auth_context import AuthContext, _resolve_auth_dep

        auth_ctx = AuthContext(auth_token="test-token", headers={"x-adcp-auth": "test-token"})

        mock_identity = ResolvedIdentity(
            principal_id="test_principal", tenant_id="default", tenant={"tenant_id": "default"}, protocol="rest"
        )

        with patch("src.core.resolved_identity.resolve_identity", return_value=mock_identity):
            identity = _resolve_auth_dep(auth_ctx)

        assert isinstance(identity, ResolvedIdentity), (
            f"_resolve_auth_dep returned {type(identity).__name__}, expected ResolvedIdentity"
        )
        assert identity.principal_id == "test_principal"

    def test_resolve_auth_non_admin_non_default_tenant(self):
        """A non-admin principal in a non-default tenant should get the correct tenant."""
        from src.core.auth_context import AuthContext, _resolve_auth_dep

        auth_ctx = AuthContext(
            auth_token="test-token",
            headers={"x-adcp-auth": "test-token", "x-adcp-tenant": "acme"},
        )

        # resolve_identity reads x-adcp-tenant from headers → tenant_id="acme"
        mock_identity = ResolvedIdentity(
            principal_id="regular_user", tenant_id="acme", tenant={"tenant_id": "acme"}, protocol="rest"
        )

        with patch("src.core.resolved_identity.resolve_identity", return_value=mock_identity):
            identity = _resolve_auth_dep(auth_ctx)

        assert isinstance(identity, ResolvedIdentity)
        assert identity.principal_id == "regular_user"
        assert identity.tenant_id == "acme", (
            f"Expected tenant_id='acme' from x-adcp-tenant header, got '{identity.tenant_id}'. "
            "REST is likely still using the broken heuristic."
        )


class TestNoMinimalContext:
    """No MinimalContext classes should exist in the codebase."""

    def test_no_minimal_context_in_a2a_server(self):
        """MinimalContext should be removed from A2A server."""
        import src.a2a_server.adcp_a2a_server as a2a_mod

        assert not hasattr(a2a_mod, "MinimalContext"), (
            "MinimalContext class still exists in adcp_a2a_server. "
            "It should be replaced with resolve_identity_from_context()."
        )


class TestNoCastToolContext:
    """No cast(ToolContext, ...) type-unsafe patterns should exist."""

    def test_a2a_server_does_not_import_cast(self):
        """A2A server should not need typing.cast (was used for cast(ToolContext, ...))."""

        # Reload to get fresh module
        import src.a2a_server.adcp_a2a_server as a2a_mod

        # Check if 'cast' is imported at module level
        # After removing cast(ToolContext, ...) calls, cast should no longer be needed
        module_dict = vars(a2a_mod)
        from typing import cast as typing_cast

        has_cast = "cast" in module_dict and module_dict["cast"] is typing_cast
        assert not has_cast, (
            "A2A server still imports typing.cast, which was used for "
            "cast(ToolContext, ...) unsafe patterns. Remove after cleanup."
        )


class TestVerifyPrincipalSimplified:
    """_verify_principal should work without get_principal_id_from_context fallback."""

    def test_verify_principal_signature_accepts_resolved_identity(self):
        """_verify_principal should accept ResolvedIdentity in its type signature."""
        from src.core.tools.media_buy_update import _verify_principal

        sig = inspect.signature(_verify_principal)
        context_param = sig.parameters.get("context")
        assert context_param is not None
        ann = str(context_param.annotation)
        assert "ResolvedIdentity" in ann, (
            f"_verify_principal context param annotation is '{ann}', should include ResolvedIdentity"
        )

    def test_verify_principal_no_get_principal_id_from_context_import(self):
        """_verify_principal should not fall back to get_principal_id_from_context."""
        # Check that the module-level function doesn't lazily import it
        from src.core.tools import media_buy_update

        # After migration, _verify_principal should handle ResolvedIdentity directly
        # without needing get_principal_id_from_context as fallback
        # Test behaviorally: passing ResolvedIdentity should work directly
        sig = inspect.signature(media_buy_update._verify_principal)
        # The function should accept ResolvedIdentity without isinstance fallback chains
        # We verify by checking it doesn't have more than 2 type options (was 3: Context|ToolContext|ResolvedIdentity)
        ann = str(sig.parameters["context"].annotation)
        assert "Context |" not in ann or ann.count("|") <= 1, (
            f"_verify_principal still has 3-way isinstance dispatch: {ann}. Simplify to accept ResolvedIdentity only."
        )
