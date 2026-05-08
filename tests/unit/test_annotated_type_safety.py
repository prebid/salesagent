"""Regression tests: auth deps must provide real types, not Any.

Core invariant: Route signature types must flow through to mypy —
every dependency parameter gets its real type, never Any.

beads: salesagent-dnpq
"""

import typing


class TestAuthDepsAreAnnotated:
    """Auth dependency exports must be Annotated types, not Any."""

    def test_get_auth_context_is_not_any(self):
        """GetAuthContext must not be typed as Any."""
        from src.core.auth_context import GetAuthContext

        # Annotated types have __metadata__ attribute
        origin = typing.get_origin(GetAuthContext)
        assert (
            origin is typing.Annotated
        ), f"GetAuthContext should be Annotated[AuthContext, Depends(...)], got {GetAuthContext}"

    def test_resolve_auth_is_not_any(self):
        """ResolveAuth must not be typed as Any."""
        from src.core.auth_context import ResolveAuth

        origin = typing.get_origin(ResolveAuth)
        assert (
            origin is typing.Annotated
        ), f"ResolveAuth should be Annotated[ResolvedIdentity | None, Depends(...)], got {ResolveAuth}"

    def test_require_auth_is_not_any(self):
        """RequireAuth must not be typed as Any."""
        from src.core.auth_context import RequireAuth

        origin = typing.get_origin(RequireAuth)
        assert (
            origin is typing.Annotated
        ), f"RequireAuth should be Annotated[ResolvedIdentity, Depends(...)], got {RequireAuth}"
