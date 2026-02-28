"""Regression tests: auth deps must provide real types, not Any.

Core invariant: Route signature types must flow through to mypy —
every dependency parameter gets its real type, never Any.

These tests verify:
1. Auth dependency exports are Annotated types (not bare Any)
2. context_builder.build() accepts typed Request (not object)
3. _log_a2a_operation has proper Optional annotations

beads: salesagent-dnpq
"""

import typing
from typing import get_type_hints


class TestAuthDepsAreAnnotated:
    """Auth dependency exports must be Annotated types, not Any."""

    def test_get_auth_context_is_not_any(self):
        """GetAuthContext must not be typed as Any."""
        from src.core.auth_context import GetAuthContext

        # Annotated types have __metadata__ attribute
        origin = typing.get_origin(GetAuthContext)
        assert origin is typing.Annotated, (
            f"GetAuthContext should be Annotated[AuthContext, Depends(...)], got {GetAuthContext}"
        )

    def test_resolve_auth_is_not_any(self):
        """ResolveAuth must not be typed as Any."""
        from src.core.auth_context import ResolveAuth

        origin = typing.get_origin(ResolveAuth)
        assert origin is typing.Annotated, (
            f"ResolveAuth should be Annotated[ResolvedIdentity | None, Depends(...)], got {ResolveAuth}"
        )

    def test_require_auth_is_not_any(self):
        """RequireAuth must not be typed as Any."""
        from src.core.auth_context import RequireAuth

        origin = typing.get_origin(RequireAuth)
        assert origin is typing.Annotated, (
            f"RequireAuth should be Annotated[ResolvedIdentity, Depends(...)], got {RequireAuth}"
        )


class TestContextBuilderTypeSafety:
    """AdCPCallContextBuilder.build() should accept Request, not object."""

    def test_build_parameter_is_request(self):
        """build() parameter should be typed as Request, not object."""
        from starlette.requests import Request

        from src.a2a_server.context_builder import AdCPCallContextBuilder

        hints = get_type_hints(AdCPCallContextBuilder.build)
        request_type = hints.get("request")
        assert request_type is Request, f"build(request) should be typed as Request, got {request_type}"


class TestLogOperationTypeSafety:
    """_log_a2a_operation should have proper Optional annotations."""

    def test_details_param_accepts_none(self):
        """details param should be dict | None, not bare dict."""
        from src.a2a_server.adcp_a2a_server import AdCPRequestHandler

        hints = get_type_hints(AdCPRequestHandler._log_a2a_operation)
        details_type = hints.get("details")
        # Should be dict[str, Any] | None — check it accepts None
        assert details_type is not dict, f"details should be dict[str, Any] | None, got {details_type}"
