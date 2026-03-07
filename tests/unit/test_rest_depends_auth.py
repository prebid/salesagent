"""Regression tests for REST Depends-based auth resolution.

Validates that REST routes use FastAPI Depends() for identity resolution
instead of manual _resolve_auth/_require_auth calls inside handler bodies.

Core invariant: Identity resolution for REST routes is declared in function
signatures via FastAPI Depends, never called manually inside handler bodies.

beads: salesagent-lkyi
"""

import inspect

from fastapi.params import Depends


class TestResolveAuthDependencyExists:
    """auth_context.py should export resolve_auth and require_auth Depends."""

    def test_resolve_auth_exists_in_auth_context(self):
        """resolve_auth should be exported from auth_context."""
        from src.core.auth_context import resolve_auth

        assert resolve_auth is not None

    def test_require_auth_exists_in_auth_context(self):
        """require_auth should be exported from auth_context."""
        from src.core.auth_context import require_auth

        assert require_auth is not None

    def test_resolve_auth_is_depends_instance(self):
        """resolve_auth should be a FastAPI Depends instance."""
        from src.core.auth_context import resolve_auth

        assert isinstance(resolve_auth, Depends), f"resolve_auth is {type(resolve_auth).__name__}, expected Depends"

    def test_require_auth_is_depends_instance(self):
        """require_auth should be a FastAPI Depends instance."""
        from src.core.auth_context import require_auth

        assert isinstance(require_auth, Depends), f"require_auth is {type(require_auth).__name__}, expected Depends"


class TestApiV1NoManualAuthCalls:
    """api_v1.py should not have _resolve_auth or _require_auth helpers."""

    def test_no_resolve_auth_in_api_v1(self):
        """_resolve_auth should not exist in api_v1 (moved to auth_context Depends)."""
        import src.routes.api_v1 as api_v1_mod

        assert not hasattr(api_v1_mod, "_resolve_auth"), (
            "_resolve_auth still exists in api_v1.py — should be replaced by Depends"
        )

    def test_no_require_auth_in_api_v1(self):
        """_require_auth should not exist in api_v1 (moved to auth_context Depends)."""
        import src.routes.api_v1 as api_v1_mod

        assert not hasattr(api_v1_mod, "_require_auth"), (
            "_require_auth still exists in api_v1.py — should be replaced by Depends"
        )


class TestRouteSignaturesUseDependsForIdentity:
    """Route handlers should declare identity in their function signature."""

    def _get_identity_param(self, func_name: str):
        """Get the 'identity' parameter from a route handler."""
        import src.routes.api_v1 as api_v1_mod

        func = getattr(api_v1_mod, func_name)
        sig = inspect.signature(func)
        return sig.parameters.get("identity")

    def test_get_products_has_identity_param(self):
        param = self._get_identity_param("get_products")
        assert param is not None, "get_products should have an 'identity' parameter"
        assert isinstance(param.default, Depends), "identity should use Depends"

    def test_get_capabilities_has_identity_param(self):
        param = self._get_identity_param("get_capabilities")
        assert param is not None, "get_capabilities should have an 'identity' parameter"
        assert isinstance(param.default, Depends), "identity should use Depends"

    def test_list_creative_formats_has_identity_param(self):
        param = self._get_identity_param("list_creative_formats")
        assert param is not None, "list_creative_formats should have an 'identity' parameter"
        assert isinstance(param.default, Depends), "identity should use Depends"

    def test_list_authorized_properties_has_identity_param(self):
        param = self._get_identity_param("list_authorized_properties")
        assert param is not None, "list_authorized_properties should have an 'identity' parameter"
        assert isinstance(param.default, Depends), "identity should use Depends"

    def test_create_media_buy_has_identity_param(self):
        param = self._get_identity_param("create_media_buy")
        assert param is not None, "create_media_buy should have an 'identity' parameter"
        assert isinstance(param.default, Depends), "identity should use Depends"

    def test_update_media_buy_has_identity_param(self):
        param = self._get_identity_param("update_media_buy")
        assert param is not None, "update_media_buy should have an 'identity' parameter"
        assert isinstance(param.default, Depends), "identity should use Depends"

    def test_get_media_buy_delivery_has_identity_param(self):
        param = self._get_identity_param("get_media_buy_delivery")
        assert param is not None, "get_media_buy_delivery should have an 'identity' parameter"
        assert isinstance(param.default, Depends), "identity should use Depends"

    def test_sync_creatives_has_identity_param(self):
        param = self._get_identity_param("sync_creatives")
        assert param is not None, "sync_creatives should have an 'identity' parameter"
        assert isinstance(param.default, Depends), "identity should use Depends"

    def test_list_creatives_has_identity_param(self):
        param = self._get_identity_param("list_creatives")
        assert param is not None, "list_creatives should have an 'identity' parameter"
        assert isinstance(param.default, Depends), "identity should use Depends"

    def test_update_performance_index_has_identity_param(self):
        param = self._get_identity_param("update_performance_index")
        assert param is not None, "update_performance_index should have an 'identity' parameter"
        assert isinstance(param.default, Depends), "identity should use Depends"

    def test_no_route_has_request_parameter(self):
        """No route handler should take a raw Request parameter anymore."""
        import src.routes.api_v1 as api_v1_mod

        route_names = [
            "get_products",
            "get_capabilities",
            "list_creative_formats",
            "list_authorized_properties",
            "create_media_buy",
            "update_media_buy",
            "get_media_buy_delivery",
            "sync_creatives",
            "list_creatives",
            "update_performance_index",
        ]
        for name in route_names:
            func = getattr(api_v1_mod, name)
            sig = inspect.signature(func)
            assert "request" not in sig.parameters, (
                f"{name} still takes 'request' parameter — should use Depends for auth"
            )


class TestResolveAuthDepBehavior:
    """Test the resolve_auth dependency function behavior directly."""

    def test_returns_none_without_token(self):
        """resolve_auth dep should return None when no auth token present."""
        from src.core.auth_context import AuthContext, _resolve_auth_dep

        auth_ctx = AuthContext.unauthenticated()
        result = _resolve_auth_dep(auth_ctx)
        assert result is None

    def test_returns_identity_with_valid_token(self):
        """resolve_auth dep should return ResolvedIdentity with valid token."""
        from unittest.mock import patch

        from src.core.auth_context import AuthContext, _resolve_auth_dep
        from src.core.resolved_identity import ResolvedIdentity

        auth_ctx = AuthContext(
            auth_token="test-token",
            headers={"x-adcp-auth": "test-token"},
        )

        mock_identity = ResolvedIdentity(
            principal_id="test_principal",
            tenant_id="default",
            tenant={"tenant_id": "default"},
            protocol="rest",
        )

        with patch("src.core.resolved_identity.resolve_identity", return_value=mock_identity):
            result = _resolve_auth_dep(auth_ctx)

        assert isinstance(result, ResolvedIdentity)
        assert result.principal_id == "test_principal"

    def test_passes_auth_token_to_resolve_identity(self):
        """resolve_auth dep should pass pre-extracted token to avoid redundant extraction."""
        from unittest.mock import patch

        from src.core.auth_context import AuthContext, _resolve_auth_dep
        from src.core.resolved_identity import ResolvedIdentity

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

        mock_resolve.assert_called_once()
        call_kwargs = mock_resolve.call_args
        assert call_kwargs.kwargs.get("auth_token") == "pre-extracted-token" or (
            len(call_kwargs.args) > 1 and call_kwargs.args[1] == "pre-extracted-token"
        )


class TestRequireAuthDepBehavior:
    """Test the require_auth dependency function behavior directly."""

    def test_raises_without_token(self):
        """require_auth dep should raise AdCPAuthenticationError without token."""
        import pytest

        from src.core.auth_context import AuthContext, _require_auth_dep
        from src.core.exceptions import AdCPAuthenticationError

        auth_ctx = AuthContext.unauthenticated()
        with pytest.raises(AdCPAuthenticationError, match="Authentication required"):
            _require_auth_dep(auth_ctx)

    def test_returns_identity_with_valid_token(self):
        """require_auth dep should return ResolvedIdentity with valid token."""
        from unittest.mock import patch

        from src.core.auth_context import AuthContext, _require_auth_dep
        from src.core.resolved_identity import ResolvedIdentity

        auth_ctx = AuthContext(
            auth_token="test-token",
            headers={"x-adcp-auth": "test-token"},
        )

        mock_identity = ResolvedIdentity(
            principal_id="test_principal",
            tenant_id="default",
            tenant={"tenant_id": "default"},
            protocol="rest",
        )

        with patch("src.core.resolved_identity.resolve_identity", return_value=mock_identity):
            result = _require_auth_dep(auth_ctx)

        assert isinstance(result, ResolvedIdentity)
        assert result.principal_id == "test_principal"
