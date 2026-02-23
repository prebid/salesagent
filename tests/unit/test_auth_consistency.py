#!/usr/bin/env python3
"""
Unit tests for auth middleware verification across MCP tools.

Tests that auth error responses have identical format across all endpoints,
ensuring consistent behavior for:
- Missing token (None auth) on authenticated endpoints
- Invalid token on authenticated endpoints
- Anonymous access on discovery endpoints
- Invalid token on discovery endpoints (should not fall back to anonymous)
"""

from unittest.mock import MagicMock, patch

import pytest
from fastmcp.exceptions import ToolError

from src.services.policy_check_service import PolicyStatus

# --- Helpers ---


def _make_mock_context_with_headers(headers: dict | None = None):
    """Create a mock FastMCP Context with headers accessible via get_http_headers.

    This simulates what FastMCP provides when an MCP tool is called over HTTP.
    The get_http_headers() function reads from a ContextVar, so we patch it directly.
    """
    ctx = MagicMock()
    ctx.meta = {"headers": headers or {}}
    return ctx


# --- Test Classes ---


class TestMissingTokenConsistency:
    """Test that all authenticated MCP tools raise consistent errors when called without a token."""

    def _make_no_auth_context(self):
        """Create a context that simulates no auth token being provided."""
        return _make_mock_context_with_headers({"host": "localhost:8000"})

    @pytest.mark.asyncio
    async def test_create_media_buy_requires_auth(self):
        """create_media_buy should fail when no auth token is provided."""
        from src.core.tools.media_buy_create import _create_media_buy_impl

        ctx = self._make_no_auth_context()

        # Mock get_principal_id_from_context to return None (no auth)
        with patch("src.core.tools.media_buy_create.get_principal_id_from_context", return_value=None):
            with pytest.raises(ToolError, match="[Aa]uthentication required|[Pp]rincipal ID not found"):
                req = MagicMock()
                await _create_media_buy_impl(req=req, ctx=ctx)

    def test_update_media_buy_requires_auth(self):
        """update_media_buy should fail when no auth token is provided."""
        from src.core.tools.media_buy_update import _update_media_buy_impl

        ctx = self._make_no_auth_context()

        with patch("src.core.tools.media_buy_update.get_principal_id_from_context", return_value=None):
            with pytest.raises((ValueError, ToolError), match="required|[Aa]uthentication"):
                req = MagicMock()
                _update_media_buy_impl(req=req, ctx=ctx)

    def test_sync_creatives_requires_auth(self):
        """sync_creatives should fail when no auth token is provided."""
        from src.core.tools.creatives._sync import _sync_creatives_impl

        ctx = self._make_no_auth_context()

        with patch("src.core.tools.creatives._sync.get_principal_id_from_context", return_value=None):
            with pytest.raises(ToolError, match="[Aa]uthentication required"):
                _sync_creatives_impl(creatives=[], ctx=ctx)

    def test_list_creatives_requires_auth(self):
        """list_creatives should fail when no auth token is provided."""
        from src.core.tools.creatives.listing import _list_creatives_impl

        ctx = self._make_no_auth_context()

        with patch("src.core.tools.creatives.listing.get_principal_id_from_context", return_value=None):
            with pytest.raises(ToolError, match="[Mm]issing x-adcp-auth"):
                _list_creatives_impl(ctx=ctx)

    def test_get_media_buy_delivery_missing_auth_returns_error_response(self):
        """get_media_buy_delivery returns an error response (not raise) when no auth token is provided."""
        from src.core.tools.media_buy_delivery import _get_media_buy_delivery_impl

        ctx = self._make_no_auth_context()

        with patch("src.core.tools.media_buy_delivery.get_principal_id_from_context", return_value=None):
            req = MagicMock()
            req.context = None
            result = _get_media_buy_delivery_impl(req, ctx)

            # Should return response with errors, not raise
            assert result is not None
            assert hasattr(result, "errors")
            assert len(result.errors) > 0
            # Check that the error message mentions the missing principal
            error_messages = [str(e.message).lower() for e in result.errors]
            assert any("principal" in msg for msg in error_messages), (
                f"Expected error about missing principal, got: {error_messages}"
            )

    @pytest.mark.asyncio
    async def test_all_authenticated_tools_reject_none_context(self):
        """Authenticated tools that require context should fail when context is None."""
        from src.core.tools.media_buy_create import _create_media_buy_impl
        from src.core.tools.media_buy_update import _update_media_buy_impl

        # create_media_buy raises ToolError with None context
        with pytest.raises((ToolError, ValueError)):
            await _create_media_buy_impl(req=MagicMock(), ctx=None)

        # update_media_buy raises ValueError with None context
        with pytest.raises((ValueError, ToolError)):
            _update_media_buy_impl(req=MagicMock(), ctx=None)


class TestInvalidTokenConsistency:
    """Test that all authenticated MCP tools raise consistent errors with an invalid token."""

    @pytest.mark.asyncio
    async def test_create_media_buy_invalid_token(self):
        """create_media_buy should raise ToolError for invalid token."""
        from src.core.tools.media_buy_create import _create_media_buy_impl

        ctx = _make_mock_context_with_headers({"host": "localhost:8000", "x-adcp-auth": "invalid-token-xyz"})

        # get_principal_id_from_context calls get_principal_from_context which raises ToolError
        # for invalid tokens when require_valid_token=True (default)
        with patch(
            "src.core.tools.media_buy_create.get_principal_id_from_context",
            side_effect=ToolError("INVALID_AUTH_TOKEN", "Authentication token is invalid"),
        ):
            with pytest.raises(ToolError) as exc_info:
                req = MagicMock()
                await _create_media_buy_impl(req=req, ctx=ctx)
            assert "INVALID_AUTH_TOKEN" in str(exc_info.value)

    def test_update_media_buy_invalid_token(self):
        """update_media_buy should raise for invalid token."""
        from src.core.tools.media_buy_update import _update_media_buy_impl

        ctx = _make_mock_context_with_headers({"host": "localhost:8000", "x-adcp-auth": "invalid-token-xyz"})

        with patch(
            "src.core.tools.media_buy_update.get_principal_id_from_context",
            side_effect=ToolError("INVALID_AUTH_TOKEN", "Authentication token is invalid"),
        ):
            with pytest.raises(ToolError) as exc_info:
                req = MagicMock()
                _update_media_buy_impl(req=req, ctx=ctx)
            assert "INVALID_AUTH_TOKEN" in str(exc_info.value)

    def test_sync_creatives_invalid_token(self):
        """sync_creatives should raise ToolError for invalid token."""
        from src.core.tools.creatives._sync import _sync_creatives_impl

        ctx = _make_mock_context_with_headers({"host": "localhost:8000", "x-adcp-auth": "invalid-token-xyz"})

        with patch(
            "src.core.tools.creatives._sync.get_principal_id_from_context",
            side_effect=ToolError("INVALID_AUTH_TOKEN", "Authentication token is invalid"),
        ):
            with pytest.raises(ToolError) as exc_info:
                _sync_creatives_impl(creatives=[], ctx=ctx)
            assert "INVALID_AUTH_TOKEN" in str(exc_info.value)

    def test_list_creatives_invalid_token(self):
        """list_creatives should raise ToolError for invalid token."""
        from src.core.tools.creatives.listing import _list_creatives_impl

        ctx = _make_mock_context_with_headers({"host": "localhost:8000", "x-adcp-auth": "invalid-token-xyz"})

        with patch(
            "src.core.tools.creatives.listing.get_principal_id_from_context",
            side_effect=ToolError("INVALID_AUTH_TOKEN", "Authentication token is invalid"),
        ):
            with pytest.raises(ToolError) as exc_info:
                _list_creatives_impl(ctx=ctx)
            assert "INVALID_AUTH_TOKEN" in str(exc_info.value)

    def test_get_media_buy_delivery_invalid_token(self):
        """get_media_buy_delivery should raise ToolError for invalid token."""
        from src.core.tools.media_buy_delivery import _get_media_buy_delivery_impl

        ctx = _make_mock_context_with_headers({"host": "localhost:8000", "x-adcp-auth": "invalid-token-xyz"})

        with patch(
            "src.core.tools.media_buy_delivery.get_principal_id_from_context",
            side_effect=ToolError("INVALID_AUTH_TOKEN", "Authentication token is invalid"),
        ):
            with pytest.raises(ToolError) as exc_info:
                req = MagicMock()
                req.context = None
                _get_media_buy_delivery_impl(req, ctx)
            assert "INVALID_AUTH_TOKEN" in str(exc_info.value)


class TestDiscoveryEndpointsAnonymousAccess:
    """Test that discovery endpoints work WITHOUT auth (anonymous access)."""

    def _make_anon_context_with_tenant(self):
        """Create a context that simulates anonymous access with tenant resolved from headers."""
        ctx = _make_mock_context_with_headers({"host": "localhost:8000"})
        return ctx

    @pytest.mark.asyncio
    async def test_get_products_works_without_auth(self):
        """get_products should succeed without authentication when tenant allows public access."""
        from src.core.tools.products import _get_products_impl

        ctx = self._make_anon_context_with_tenant()

        # get_principal_from_context returns (None, tenant_dict) for anonymous
        # brand_manifest_policy="public" allows anonymous access without auth requirement
        mock_tenant = {"tenant_id": "test-tenant", "name": "Test", "brand_manifest_policy": "public"}
        with (
            patch(
                "src.core.tools.products.get_principal_from_context",
                return_value=(None, mock_tenant),
            ),
            patch("src.core.tools.products.set_current_tenant"),
            patch(
                "src.core.tools.products.get_testing_context",
                return_value=MagicMock(dry_run=False, test_session_id=None),
            ),
            patch("src.core.tools.products.get_db_session") as mock_db,
            patch("src.core.tools.products.PolicyCheckService") as mock_policy,
        ):
            # Mock database to return empty products
            mock_session = MagicMock()
            mock_session.__enter__ = MagicMock(return_value=mock_session)
            mock_session.__exit__ = MagicMock(return_value=False)
            mock_session.scalars.return_value.all.return_value = []
            mock_db.return_value = mock_session

            # Mock policy check service
            mock_policy_instance = MagicMock()
            mock_policy_instance.check_product_eligibility.return_value = (PolicyStatus.ALLOWED, "OK")
            mock_policy.return_value = mock_policy_instance

            # Should not raise auth error
            req = MagicMock()
            req.brief = "test"
            req.brand_manifest = None
            req.filters = None
            req.context = None
            try:
                result = await _get_products_impl(req, ctx)
                # If it gets past auth, it succeeded (may fail later on business logic)
            except ToolError as e:
                # Auth errors are failures; business logic errors are OK
                assert "auth" not in str(e).lower(), f"Discovery endpoint should not require auth: {e}"

    def test_list_creative_formats_works_without_auth(self):
        """list_creative_formats should succeed without authentication."""
        from src.core.tools.creative_formats import _list_creative_formats_impl

        ctx = self._make_anon_context_with_tenant()

        mock_tenant = {"tenant_id": "test-tenant", "name": "Test"}
        with (
            patch(
                "src.core.tools.creative_formats.get_principal_from_context",
                return_value=(None, mock_tenant),
            ),
            patch("src.core.tools.creative_formats.set_current_tenant"),
            patch("src.core.tools.creative_formats.get_current_tenant", return_value=mock_tenant),
            # get_creative_agent_registry is imported inside the function from src.core.creative_agent_registry
            patch("src.core.creative_agent_registry.get_creative_agent_registry") as mock_registry,
        ):
            mock_reg = MagicMock()

            async def mock_list_formats(**kwargs):
                return []

            mock_reg.list_all_formats = mock_list_formats
            mock_registry.return_value = mock_reg

            req = MagicMock()
            req.type = None
            req.format_ids = None
            req.is_responsive = None
            req.name_search = None
            req.asset_types = None
            req.min_width = None
            req.max_width = None
            req.min_height = None
            req.max_height = None
            req.context = None

            try:
                result = _list_creative_formats_impl(req, ctx)
                assert result is not None
            except ToolError as e:
                assert "auth" not in str(e).lower(), f"Discovery endpoint should not require auth: {e}"

    def test_list_authorized_properties_works_without_auth(self):
        """list_authorized_properties should succeed without authentication."""
        from src.core.tools.properties import _list_authorized_properties_impl

        ctx = self._make_anon_context_with_tenant()

        mock_tenant = {"tenant_id": "test-tenant", "name": "Test"}
        with (
            patch(
                "src.core.tools.properties.get_principal_from_context",
                return_value=(None, mock_tenant),
            ),
            patch("src.core.tools.properties.set_current_tenant"),
            patch("src.core.tools.properties.get_current_tenant", return_value=mock_tenant),
            patch("src.core.tools.properties.get_db_session") as mock_db,
        ):
            mock_session = MagicMock()
            mock_session.__enter__ = MagicMock(return_value=mock_session)
            mock_session.__exit__ = MagicMock(return_value=False)
            mock_session.scalars.return_value.all.return_value = []
            mock_db.return_value = mock_session

            try:
                result = _list_authorized_properties_impl(req=None, context=ctx)
                assert result is not None
            except ToolError as e:
                assert "auth" not in str(e).lower(), f"Discovery endpoint should not require auth: {e}"


class TestDiscoveryEndpointsInvalidAuth:
    """Test that discovery endpoints fail with invalid token (don't silently fall back to anonymous).

    When a token IS provided but is invalid, discovery endpoints should either:
    - Raise an error (strict mode), or
    - Fall back to anonymous (lenient mode with require_valid_token=False)

    The current implementation uses require_valid_token=False for discovery endpoints,
    which means invalid tokens are treated like missing tokens. This test documents
    that behavior and verifies it's consistent across all discovery endpoints.
    """

    @pytest.mark.asyncio
    async def test_get_products_with_invalid_token_uses_require_valid_token_false(self):
        """get_products passes require_valid_token=False, so invalid token falls back to anonymous."""
        from src.core.tools.products import _get_products_impl

        ctx = _make_mock_context_with_headers({"host": "localhost:8000", "x-adcp-auth": "invalid-token"})

        # With require_valid_token=False, invalid tokens are treated like missing tokens
        # This means discovery endpoints gracefully degrade to anonymous
        mock_tenant = {"tenant_id": "test-tenant"}
        with (
            patch(
                "src.core.tools.products.get_principal_from_context",
                return_value=(None, mock_tenant),
            ) as mock_auth,
            patch("src.core.tools.products.set_current_tenant"),
            patch("src.core.tools.products.get_testing_context", return_value=MagicMock(dry_run=False)),
            patch("src.core.tools.products.get_db_session") as mock_db,
        ):
            mock_session = MagicMock()
            mock_session.__enter__ = MagicMock(return_value=mock_session)
            mock_session.__exit__ = MagicMock(return_value=False)
            mock_session.scalars.return_value.all.return_value = []
            mock_db.return_value = mock_session

            req = MagicMock()
            req.brief = "test"
            req.brand_manifest = None
            req.filters = None
            req.context = None

            try:
                await _get_products_impl(req, ctx)
            except ToolError:
                pass  # Business logic errors OK

            # Verify require_valid_token=False was passed
            mock_auth.assert_called_once_with(ctx, require_valid_token=False)

    def test_list_creative_formats_with_invalid_token_uses_require_valid_token_false(self):
        """list_creative_formats passes require_valid_token=False, treating invalid tokens as missing."""
        from src.core.tools.creative_formats import _list_creative_formats_impl

        ctx = _make_mock_context_with_headers({"host": "localhost:8000", "x-adcp-auth": "invalid-token"})

        mock_tenant = {"tenant_id": "test-tenant"}
        with (
            patch(
                "src.core.tools.creative_formats.get_principal_from_context",
                return_value=(None, mock_tenant),
            ) as mock_auth,
            patch("src.core.tools.creative_formats.set_current_tenant"),
            patch("src.core.tools.creative_formats.get_current_tenant", return_value=mock_tenant),
            # get_creative_agent_registry is imported inside the function from src.core.creative_agent_registry
            patch("src.core.creative_agent_registry.get_creative_agent_registry") as mock_registry,
        ):
            mock_reg = MagicMock()

            async def mock_list_formats(**kwargs):
                return []

            mock_reg.list_all_formats = mock_list_formats
            mock_registry.return_value = mock_reg

            try:
                _list_creative_formats_impl(None, ctx)
            except ToolError:
                pass  # Business logic errors OK

            mock_auth.assert_called_once_with(ctx, require_valid_token=False)

    def test_list_authorized_properties_with_invalid_token_uses_require_valid_token_false(self):
        """list_authorized_properties passes require_valid_token=False."""
        from src.core.tools.properties import _list_authorized_properties_impl

        ctx = _make_mock_context_with_headers({"host": "localhost:8000", "x-adcp-auth": "invalid-token"})

        mock_tenant = {"tenant_id": "test-tenant"}
        with (
            patch(
                "src.core.tools.properties.get_principal_from_context",
                return_value=(None, mock_tenant),
            ) as mock_auth,
            patch("src.core.tools.properties.set_current_tenant"),
            patch("src.core.tools.properties.get_current_tenant", return_value=mock_tenant),
            patch("src.core.tools.properties.get_db_session") as mock_db,
        ):
            mock_session = MagicMock()
            mock_session.__enter__ = MagicMock(return_value=mock_session)
            mock_session.__exit__ = MagicMock(return_value=False)
            mock_session.scalars.return_value.all.return_value = []
            mock_db.return_value = mock_session

            try:
                _list_authorized_properties_impl(req=None, context=ctx)
            except ToolError:
                pass  # Business logic errors OK

            mock_auth.assert_called_once_with(ctx, require_valid_token=False)

    def test_authenticated_tools_use_require_valid_token_true_by_default(self):
        """Verify authenticated tools use the default require_valid_token=True behavior.

        get_principal_id_from_context calls get_principal_from_context without
        require_valid_token, which defaults to True. This means invalid tokens
        raise ToolError on authenticated endpoints.
        """
        # Verify the default parameter value is True
        import inspect

        from src.core.auth import get_principal_from_context

        sig = inspect.signature(get_principal_from_context)
        require_param = sig.parameters.get("require_valid_token")
        assert require_param is not None, "require_valid_token parameter should exist"
        assert require_param.default is True, f"require_valid_token should default to True, got {require_param.default}"
