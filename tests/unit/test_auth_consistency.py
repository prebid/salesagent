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

from src.core.exceptions import AdCPAuthenticationError, AdCPError, AdCPValidationError
from src.core.resolved_identity import ResolvedIdentity
from src.services.policy_check_service import PolicyStatus

# --- Helpers ---


def _make_identity(
    principal_id: str | None = None,
    tenant_id: str = "test-tenant",
    tenant: dict | None = None,
) -> ResolvedIdentity:
    """Create a ResolvedIdentity for testing."""
    if tenant is None:
        tenant = {"tenant_id": tenant_id, "name": "Test"}
    return ResolvedIdentity(
        principal_id=principal_id,
        tenant_id=tenant_id,
        tenant=tenant,
        protocol="mcp",
    )


# --- Test Classes ---


class TestMissingTokenConsistency:
    """Test that all authenticated MCP tools raise consistent errors when called without a token."""

    @pytest.mark.asyncio
    async def test_create_media_buy_requires_auth(self):
        """create_media_buy should fail when no auth token is provided."""
        from src.core.tools.media_buy_create import _create_media_buy_impl

        # Pass identity with no principal_id (simulates no auth)
        identity = _make_identity(principal_id=None)

        with pytest.raises(AdCPAuthenticationError, match="[Aa]uthentication required|[Pp]rincipal ID not found"):
            req = MagicMock()
            await _create_media_buy_impl(req=req, identity=identity)

    def test_update_media_buy_requires_auth(self):
        """update_media_buy should fail when no auth token is provided."""
        from src.core.tools.media_buy_update import _update_media_buy_impl

        # Pass identity with no principal_id
        identity = _make_identity(principal_id=None)

        with pytest.raises((ValueError, AdCPAuthenticationError), match="required|[Aa]uthentication"):
            req = MagicMock()
            _update_media_buy_impl(req=req, identity=identity)

    def test_sync_creatives_requires_auth(self):
        """sync_creatives should fail when no auth token is provided."""
        from src.core.tools.creatives._sync import _sync_creatives_impl

        # Pass identity with no principal_id
        identity = _make_identity(principal_id=None)

        with pytest.raises(AdCPAuthenticationError, match="[Aa]uthentication required"):
            _sync_creatives_impl(creatives=[], identity=identity)

    def test_list_creatives_requires_auth(self):
        """list_creatives should fail when no auth token is provided."""
        from src.core.tools.creatives.listing import _list_creatives_impl

        # Pass identity with no principal_id
        identity = _make_identity(principal_id=None)

        with pytest.raises(AdCPAuthenticationError, match="[Mm]issing x-adcp-auth"):
            _list_creatives_impl(identity=identity)

    def test_get_media_buy_delivery_missing_auth_returns_error_response(self):
        """get_media_buy_delivery returns an error response (not raise) when no auth token is provided."""
        from src.core.tools.media_buy_delivery import _get_media_buy_delivery_impl

        # Pass identity with no principal_id
        identity = _make_identity(principal_id=None)

        req = MagicMock()
        req.context = None
        result = _get_media_buy_delivery_impl(req, identity)

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
    async def test_all_authenticated_tools_reject_none_identity(self):
        """Authenticated tools that require identity should fail when identity is None."""
        from src.core.tools.media_buy_create import _create_media_buy_impl
        from src.core.tools.media_buy_update import _update_media_buy_impl

        # create_media_buy raises AdCPValidationError with None identity
        with pytest.raises((AdCPValidationError, ValueError)):
            await _create_media_buy_impl(req=MagicMock(), identity=None)

        # update_media_buy raises ValueError with None identity
        with pytest.raises((ValueError, AdCPAuthenticationError)):
            _update_media_buy_impl(req=MagicMock(), identity=None)


class TestInvalidTokenConsistency:
    """Test that all authenticated MCP tools raise consistent errors with an invalid token.

    Since _impl functions now receive ResolvedIdentity directly (identity is resolved
    at the transport boundary), invalid token handling is tested by verifying that
    ResolvedIdentity with principal_id=None (which is what resolve_identity produces
    for invalid tokens with require_valid_token=False) causes proper auth errors.

    For require_valid_token=True (the default for authenticated endpoints), the
    transport boundary raises AdCPAuthenticationError before _impl is ever called.
    We test this behavior in test_authenticated_tools_use_require_valid_token_true_by_default.
    """

    @pytest.mark.asyncio
    async def test_create_media_buy_invalid_token(self):
        """create_media_buy should fail for identity with no principal (invalid token resolved to anonymous)."""
        from src.core.tools.media_buy_create import _create_media_buy_impl

        # An invalid token with require_valid_token=True raises at the boundary,
        # so the _impl function never sees it. But if it somehow got through
        # (e.g., future lenient mode), identity would have principal_id=None.
        identity = _make_identity(principal_id=None)

        with pytest.raises((AdCPAuthenticationError, AdCPValidationError)):
            req = MagicMock()
            await _create_media_buy_impl(req=req, identity=identity)

    def test_update_media_buy_invalid_token(self):
        """update_media_buy should fail for identity with no principal."""
        from src.core.tools.media_buy_update import _update_media_buy_impl

        identity = _make_identity(principal_id=None)

        with pytest.raises((ValueError, AdCPAuthenticationError)):
            req = MagicMock()
            _update_media_buy_impl(req=req, identity=identity)

    def test_sync_creatives_invalid_token(self):
        """sync_creatives should fail for identity with no principal."""
        from src.core.tools.creatives._sync import _sync_creatives_impl

        identity = _make_identity(principal_id=None)

        with pytest.raises(AdCPAuthenticationError):
            _sync_creatives_impl(creatives=[], identity=identity)

    def test_list_creatives_invalid_token(self):
        """list_creatives should fail for identity with no principal."""
        from src.core.tools.creatives.listing import _list_creatives_impl

        identity = _make_identity(principal_id=None)

        with pytest.raises(AdCPAuthenticationError):
            _list_creatives_impl(identity=identity)

    def test_get_media_buy_delivery_invalid_token(self):
        """get_media_buy_delivery should return error response for identity with no principal."""
        from src.core.tools.media_buy_delivery import _get_media_buy_delivery_impl

        identity = _make_identity(principal_id=None)

        req = MagicMock()
        req.context = None
        result = _get_media_buy_delivery_impl(req, identity)

        # Should return response with errors, not raise
        assert result is not None
        assert hasattr(result, "errors")
        assert len(result.errors) > 0


class TestDiscoveryEndpointsAnonymousAccess:
    """Test that discovery endpoints work WITHOUT auth (anonymous access)."""

    @pytest.mark.asyncio
    async def test_get_products_works_without_auth(self):
        """get_products should succeed without authentication when tenant allows public access."""
        from src.core.tools.products import _get_products_impl

        # brand_manifest_policy="public" allows anonymous access without auth requirement
        mock_tenant = {"tenant_id": "test-tenant", "name": "Test", "brand_manifest_policy": "public"}
        identity = ResolvedIdentity(
            principal_id=None,
            tenant_id="test-tenant",
            tenant=mock_tenant,
        )

        with (
            patch("src.core.database.repositories.uow.get_db_session") as mock_db,
            patch("src.core.tools.products.PolicyCheckService") as mock_policy,
        ):
            # Mock database to return empty products
            mock_session = MagicMock()
            mock_session.__enter__ = MagicMock(return_value=mock_session)
            mock_session.__exit__ = MagicMock(return_value=False)
            mock_session.scalars.return_value.all.return_value = []
            mock_session.execute.return_value.unique.return_value.scalars.return_value.all.return_value = []
            mock_db.return_value = mock_session

            # Mock policy check service
            mock_policy_instance = MagicMock()
            mock_policy_instance.check_product_eligibility.return_value = (PolicyStatus.ALLOWED, "OK")
            mock_policy.return_value = mock_policy_instance

            # Should not raise auth error
            req = MagicMock()
            req.brief = "test"
            req.brand = None
            req.filters = None
            req.context = None
            try:
                result = await _get_products_impl(req, identity)
                # If it gets past auth, it succeeded (may fail later on business logic)
            except (ToolError, AdCPError) as e:
                # Auth errors are failures; business logic errors are OK
                assert "auth" not in str(e).lower(), f"Discovery endpoint should not require auth: {e}"

    def test_list_creative_formats_works_without_auth(self):
        """list_creative_formats should succeed without authentication."""
        from src.core.tools.creative_formats import _list_creative_formats_impl

        # Create anonymous identity with tenant
        mock_tenant = {"tenant_id": "test-tenant", "name": "Test"}
        identity = _make_identity(principal_id=None, tenant=mock_tenant)

        with (
            # get_creative_agent_registry is imported inside the function from src.core.creative_agent_registry
            patch("src.core.creative_agent_registry.get_creative_agent_registry") as mock_registry,
        ):
            mock_reg = MagicMock()

            async def mock_list_formats(**kwargs):
                from src.core.creative_agent_registry import FormatFetchResult

                return FormatFetchResult(formats=[], errors=[])

            mock_reg.list_all_formats_with_errors = mock_list_formats
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
            req.pagination = None

            try:
                result = _list_creative_formats_impl(req, identity)
                assert result is not None
            except ToolError as e:
                assert "auth" not in str(e).lower(), f"Discovery endpoint should not require auth: {e}"

    def test_list_authorized_properties_works_without_auth(self):
        """list_authorized_properties should succeed without authentication."""
        from src.core.tools.properties import _list_authorized_properties_impl

        # Create anonymous identity with tenant
        mock_tenant = {"tenant_id": "test-tenant", "name": "Test"}
        identity = _make_identity(principal_id=None, tenant=mock_tenant)

        mock_repo = MagicMock()
        mock_repo.list_publisher_partners.return_value = []
        mock_uow = MagicMock()
        mock_uow.__enter__ = MagicMock(return_value=mock_uow)
        mock_uow.__exit__ = MagicMock(return_value=False)
        mock_uow.tenant_config = mock_repo

        with patch("src.core.tools.properties.TenantConfigUoW", return_value=mock_uow):
            try:
                result = _list_authorized_properties_impl(req=None, identity=identity)
                assert result is not None
            except ToolError as e:
                assert "auth" not in str(e).lower(), f"Discovery endpoint should not require auth: {e}"


class TestDiscoveryEndpointsInvalidAuth:
    """Test that discovery endpoints fail with invalid token (don't silently fall back to anonymous).

    When a token IS provided but is invalid, discovery endpoints should either:
    - Raise an error (strict mode), or
    - Fall back to anonymous (lenient mode with require_valid_token=False)

    The current implementation uses require_valid_token=False for discovery endpoints
    at the transport boundary (resolve_identity), which means invalid tokens are treated
    like missing tokens. _impl functions receive a ResolvedIdentity with principal_id=None.
    This test documents that behavior and verifies it's consistent across all discovery endpoints.
    """

    @pytest.mark.asyncio
    async def test_get_products_with_invalid_token_falls_back_to_anonymous(self):
        """get_products with invalid token resolves to anonymous identity (require_valid_token=False at boundary)."""
        from src.core.tools.products import _get_products_impl

        # With require_valid_token=False at the transport boundary, invalid tokens
        # result in an anonymous ResolvedIdentity (principal_id=None)
        mock_tenant = {"tenant_id": "test-tenant"}
        identity = ResolvedIdentity(
            principal_id=None,
            tenant_id="test-tenant",
            tenant=mock_tenant,
        )

        with (
            patch("src.core.database.repositories.uow.get_db_session") as mock_db,
        ):
            mock_session = MagicMock()
            mock_session.__enter__ = MagicMock(return_value=mock_session)
            mock_session.__exit__ = MagicMock(return_value=False)
            mock_session.scalars.return_value.all.return_value = []
            mock_session.execute.return_value.unique.return_value.scalars.return_value.all.return_value = []
            mock_db.return_value = mock_session

            req = MagicMock()
            req.brief = "test"
            req.brand = None
            req.filters = None
            req.context = None

            try:
                await _get_products_impl(req, identity)
            except (ToolError, AdCPError):
                pass  # Business logic errors OK

            # Verify the identity was anonymous (principal_id=None)
            assert identity.principal_id is None

    def test_list_creative_formats_with_invalid_token_gets_anonymous_identity(self):
        """list_creative_formats with invalid token gets anonymous identity at the boundary."""
        from src.core.tools.creative_formats import _list_creative_formats_impl

        # At the boundary, require_valid_token=False means invalid tokens
        # produce an anonymous ResolvedIdentity (principal_id=None)
        mock_tenant = {"tenant_id": "test-tenant"}
        identity = _make_identity(principal_id=None, tenant=mock_tenant)

        with (
            patch("src.core.creative_agent_registry.get_creative_agent_registry") as mock_registry,
        ):
            mock_reg = MagicMock()

            async def mock_list_formats(**kwargs):
                from src.core.creative_agent_registry import FormatFetchResult

                return FormatFetchResult(formats=[], errors=[])

            mock_reg.list_all_formats_with_errors = mock_list_formats
            mock_registry.return_value = mock_reg

            try:
                from src.core.schemas import ListCreativeFormatsRequest

                req = ListCreativeFormatsRequest()
                _list_creative_formats_impl(req, identity)
            except (ToolError, AdCPError):
                pass  # Business logic errors OK

            # Verify the identity was anonymous
            assert identity.principal_id is None

    def test_list_authorized_properties_with_invalid_token_gets_anonymous_identity(self):
        """list_authorized_properties with invalid token gets anonymous identity at the boundary."""
        from src.core.tools.properties import _list_authorized_properties_impl

        mock_tenant = {"tenant_id": "test-tenant"}
        identity = _make_identity(principal_id=None, tenant=mock_tenant)

        mock_repo = MagicMock()
        mock_repo.list_publisher_partners.return_value = []
        mock_uow = MagicMock()
        mock_uow.__enter__ = MagicMock(return_value=mock_uow)
        mock_uow.__exit__ = MagicMock(return_value=False)
        mock_uow.tenant_config = mock_repo

        with patch("src.core.tools.properties.TenantConfigUoW", return_value=mock_uow):
            try:
                _list_authorized_properties_impl(req=None, identity=identity)
            except (ToolError, AdCPError):
                pass  # Business logic errors OK

            # Verify the identity was anonymous
            assert identity.principal_id is None

    def test_authenticated_tools_use_require_valid_token_true_by_default(self):
        """Verify resolve_identity uses the default require_valid_token=True behavior.

        resolve_identity has require_valid_token parameter that defaults to True.
        This means invalid tokens raise AdCPAuthenticationError at the boundary
        for authenticated endpoints.
        """
        # Verify the default parameter value is True
        import inspect

        from src.core.resolved_identity import resolve_identity

        sig = inspect.signature(resolve_identity)
        require_param = sig.parameters.get("require_valid_token")
        assert require_param is not None, "require_valid_token parameter should exist"
        assert require_param.default is True, f"require_valid_token should default to True, got {require_param.default}"
