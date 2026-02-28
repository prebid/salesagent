"""Regression tests for AuthContext dead fields and resolve_auth token passthrough.

Bug: AuthContext declares tenant_id and principal_id fields that are never
populated by anyone. The middleware correctly extracts auth_token + headers
(cheap, no DB), and handler-level resolve_identity() is the intentional pattern.
But:
1. Dead fields mislead readers into thinking they're populated
2. _resolve_auth_dep must pass the pre-extracted auth_token to resolve_identity()
3. is_authenticated() always returns False (principal_id never set)
4. get_auth_context FastAPI Depends is now used by resolve_auth/require_auth deps

Regression prevention: https://github.com/prebid/salesagent/pull/1066
Beads: salesagent-6931
"""

from unittest.mock import patch

from src.core.auth_context import AuthContext
from src.core.resolved_identity import ResolvedIdentity


class TestResolveAuthTokenPassthrough:
    """_resolve_auth_dep should pass the pre-extracted token to resolve_identity."""

    def test_resolve_auth_passes_extracted_token(self):
        """_resolve_auth_dep should pass auth_ctx.auth_token to resolve_identity()."""
        from src.core.auth_context import _resolve_auth_dep

        auth_ctx = AuthContext(
            auth_token="pre-extracted-token",
            headers={"authorization": "Bearer pre-extracted-token"},
        )

        mock_identity = ResolvedIdentity(
            principal_id="test_principal",
            tenant_id="default",
            tenant={"tenant_id": "default"},
            protocol="rest",
        )

        with patch("src.core.resolved_identity.resolve_identity", return_value=mock_identity) as mock_resolve:
            _resolve_auth_dep(auth_ctx)

        # resolve_identity should receive the pre-extracted token
        mock_resolve.assert_called_once()
        call_kwargs = mock_resolve.call_args
        assert call_kwargs.kwargs.get("auth_token") == "pre-extracted-token" or (
            len(call_kwargs.args) > 1 and call_kwargs.args[1] == "pre-extracted-token"
        ), (
            "_resolve_auth_dep should pass auth_ctx.auth_token to resolve_identity() "
            "to avoid redundant token extraction from headers."
        )


class TestAuthContextNoDeadFields:
    """AuthContext should not declare fields that nobody populates."""

    def test_auth_context_no_misleading_principal_id(self):
        """AuthContext should not have a principal_id field that nobody populates.

        Currently FAILS: AuthContext declares principal_id but middleware never
        sets it. Either remove the field or have something populate it.
        The fix should remove this dead field since identity resolution
        intentionally happens at the handler level via resolve_identity().
        """
        import dataclasses

        field_names = [f.name for f in dataclasses.fields(AuthContext)]
        assert "principal_id" not in field_names, (
            "AuthContext.principal_id is dead code — nobody populates it. "
            "Remove to avoid misleading readers. Identity resolution happens "
            "at handler level via resolve_identity(), not in middleware."
        )

    def test_auth_context_no_misleading_tenant_id(self):
        """AuthContext should not have a tenant_id field that nobody populates.

        Currently FAILS: AuthContext declares tenant_id but middleware never
        sets it. Tenant detection happens inside resolve_identity() at the
        handler level, using LazyTenantContext for lazy DB loading.
        """
        import dataclasses

        field_names = [f.name for f in dataclasses.fields(AuthContext)]
        assert "tenant_id" not in field_names, (
            "AuthContext.tenant_id is dead code — nobody populates it. "
            "Remove to avoid misleading readers. Tenant detection happens "
            "inside resolve_identity() with LazyTenantContext."
        )

    def test_no_dead_is_authenticated_method(self):
        """is_authenticated() should not exist if it always returns False.

        Currently FAILS: principal_id is never set, so is_authenticated()
        always returns False. Either remove the method or make it work
        based on auth_token presence.
        """
        # If AuthContext still has is_authenticated(), it should work
        # based on what's actually available (auth_token), not dead fields
        ctx = AuthContext(auth_token="valid-token")
        if hasattr(ctx, "is_authenticated"):
            # If the method exists, it should return True when we have a token
            # Currently returns False because it checks principal_id (always None)
            assert ctx.is_authenticated(), (
                "is_authenticated() returns False even with auth_token set, "
                "because it checks principal_id which nobody populates. "
                "Fix: base on auth_token presence, or remove the method."
            )


class TestAuthContextDocstringsMatchReality:
    """AuthContext docstrings should match what the code actually does."""

    def test_module_docstring_does_not_claim_resolution(self):
        """Module docstring should not claim middleware resolves auth + tenant."""
        import src.core.auth_context as mod

        docstring = mod.__doc__ or ""
        assert "resolves auth" not in docstring.lower(), (
            "Module docstring claims middleware 'resolves auth' but it only "
            "extracts auth_token. Fix the docstring to match reality."
        )

    def test_class_docstring_does_not_overclaim(self):
        """Class docstring should not claim auth/tenant resolution happens in middleware."""
        docstring = AuthContext.__doc__ or ""
        docstring_lower = docstring.lower()
        # The old docstring said "Populated by auth_context_middleware before handlers run"
        # implying full auth resolution. The fix should clarify it only extracts tokens.
        assert "zero auth logic in handlers" not in docstring_lower, (
            "AuthContext docstring claims 'zero auth logic in handlers' "
            "but handlers call resolve_identity(). Fix docstring."
        )
        assert "resolves auth" not in docstring_lower, (
            "AuthContext docstring claims auth resolution but middleware only extracts tokens."
        )
