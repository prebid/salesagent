#!/usr/bin/env python3
"""
Comprehensive authentication requirement tests for all AdCP tools.

Tests that all authenticated tools properly reject requests without valid authentication,
preventing database constraint violations and security issues.

Background:
-----------
Bug discovered where sync_creatives accepted requests without auth, leading to
NOT NULL constraint violations on principal_id. Investigation revealed all integration
tests provided mock auth, never testing the unauthenticated code path.

This test file ensures all tools that require authentication properly enforce it.
"""

from unittest.mock import Mock, patch

import pytest
from fastmcp.exceptions import ToolError

from src.core.tool_context import ToolContext


class TestAuthenticationRequirements:
    """Test that all authenticated tools enforce authentication requirements."""

    # =========================================================================
    # Creative Tools
    # =========================================================================

    def test_sync_creatives_requires_authentication(self):
        """sync_creatives must reject requests without authentication."""
        from src.core.tools.creatives import _sync_creatives_impl

        creatives = [
            {
                "creative_id": "test_creative",
                "name": "Test Creative",
                "format_id": "display_728x90_image",
                "assets": {
                    "banner_image": {
                        "asset_type": "image",
                        "url": "https://example.com/banner.png",
                        "width": 728,
                        "height": 90,
                    }
                },
            }
        ]

        # Call without context (no auth)
        with pytest.raises(ToolError) as exc_info:
            _sync_creatives_impl(creatives=creatives, context=None)

        error_msg = str(exc_info.value)
        assert "Authentication required" in error_msg
        assert "x-adcp-auth" in error_msg

    def test_sync_creatives_with_invalid_auth(self):
        """sync_creatives must reject requests with invalid authentication."""
        from src.core.tools.creatives import _sync_creatives_impl

        # Mock context with None principal_id (simulates invalid token)
        invalid_context = Mock(spec=ToolContext)
        invalid_context.principal_id = None
        invalid_context.tenant_id = "test_tenant"

        creatives = [
            {
                "creative_id": "test_creative",
                "name": "Test Creative",
                "format_id": "display_728x90_image",
                "assets": {"banner_image": {"url": "https://example.com/banner.png"}},
            }
        ]

        with pytest.raises(ToolError) as exc_info:
            _sync_creatives_impl(creatives=creatives, context=invalid_context)

        assert "Authentication required" in str(exc_info.value)

    def test_list_creatives_requires_authentication(self):
        """list_creatives must reject requests without authentication."""
        from src.core.tools.creatives import _list_creatives_impl

        # Call without context (no auth)
        with pytest.raises(ToolError) as exc_info:
            _list_creatives_impl(context=None)

        error_msg = str(exc_info.value)
        assert "x-adcp-auth" in error_msg

    # =========================================================================
    # Media Buy Tools
    # =========================================================================

    @patch("src.core.tools.media_buy_create.get_current_tenant")
    def test_create_media_buy_requires_authentication(self, mock_tenant):
        """create_media_buy must reject requests without authentication."""
        import asyncio

        from src.core.tools.media_buy_create import _create_media_buy_impl

        mock_tenant.return_value = {"tenant_id": "test_tenant"}

        # Minimal required params per AdCP spec
        params = {
            "buyer_ref": "test_buyer",
            "brand_manifest": {"name": "Test Brand"},
            "packages": [
                {
                    "package_id": "pkg1",
                    "product_id": "prod1",
                    "budget": 1000.0,  # AdCP v2.2.0: budget is a number, not an object
                }
            ],
            "start_time": "2025-01-01T00:00:00Z",
            "end_time": "2025-01-31T23:59:59Z",
            "context": None,  # No auth
        }

        # Call without context (no auth)
        with pytest.raises(ToolError) as exc_info:
            asyncio.run(_create_media_buy_impl(**params))

        error_msg = str(exc_info.value)
        # create_media_buy validates context presence first, then auth
        assert (
            "Principal ID not found" in error_msg
            or "authentication required" in error_msg.lower()
            or "Context is required" in error_msg
        )

    def test_update_media_buy_requires_authentication(self):
        """update_media_buy must reject requests without authentication."""
        from src.core.tools.media_buy_update import _verify_principal

        # Call without context (no auth)
        with pytest.raises(ToolError) as exc_info:
            _verify_principal(media_buy_id="test_buy", context=None)

        error_msg = str(exc_info.value)
        assert "Authentication required" in error_msg
        assert "x-adcp-auth" in error_msg

    def test_update_media_buy_with_invalid_auth(self):
        """update_media_buy must reject requests with invalid auth."""
        from src.core.tools.media_buy_update import _verify_principal

        # Mock context with None principal_id
        invalid_context = Mock(spec=ToolContext)
        invalid_context.principal_id = None
        invalid_context.tenant_id = "test_tenant"

        with pytest.raises(ToolError) as exc_info:
            _verify_principal(media_buy_id="test_buy", context=invalid_context)

        assert "Authentication required" in str(exc_info.value)

    def test_get_media_buy_delivery_requires_authentication(self):
        """get_media_buy_delivery must reject requests without authentication."""
        from src.core.schemas import GetMediaBuyDeliveryRequest
        from src.core.tools.media_buy_delivery import _get_media_buy_delivery_impl

        req = GetMediaBuyDeliveryRequest(media_buy_ids=["test_buy"])

        # Call without context (no auth)
        with pytest.raises((ToolError, ValueError)) as exc_info:
            _get_media_buy_delivery_impl(req=req, context=None)

        error_msg = str(exc_info.value)
        # May raise ToolError for missing auth or ValueError for missing context
        assert (
            "authentication required" in error_msg.lower()
            or "principal" in error_msg.lower()
            or "context" in error_msg.lower()
        )

    # =========================================================================
    # Performance Tools
    # =========================================================================

    def test_update_performance_index_requires_authentication(self):
        """update_performance_index must reject requests without authentication."""
        from src.core.tools.performance import _update_performance_index_impl

        # Call without context (no auth) - function signature: media_buy_id, performance_data, context
        with pytest.raises((ToolError, ValueError)) as exc_info:
            _update_performance_index_impl(
                media_buy_id="test_buy",
                performance_data=[{"product_id": "prod1", "performance_index": 0.8}],
                context=None,
            )

        error_msg = str(exc_info.value)
        # Either raises ToolError for missing auth or ValueError for missing context
        assert (
            "Context is required" in error_msg
            or "Principal ID not found" in error_msg
            or "authentication required" in error_msg.lower()
        )

    # =========================================================================
    # Signal Tools
    # =========================================================================

    def test_activate_signal_requires_authentication(self):
        """activate_signal must reject requests without authentication."""
        import asyncio

        from src.core.tools.signals import _activate_signal_impl

        # Call without context (no auth) - function signature: signal_id, campaign_id, media_buy_id, context
        with pytest.raises(ToolError) as exc_info:
            asyncio.run(_activate_signal_impl(signal_id="test_signal", media_buy_id="test_buy", context=None))

        error_msg = str(exc_info.value)
        assert "authentication required" in error_msg.lower() or "principal" in error_msg.lower()


class TestAuthenticationWithMockedContext:
    """Test authentication behavior with various mocked context scenarios."""

    def test_tool_context_with_none_principal_id(self):
        """ToolContext with None principal_id should be rejected."""
        from src.core.tools.creatives import _sync_creatives_impl

        # Create ToolContext with None principal_id (invalid token scenario)
        context = Mock(spec=ToolContext)
        context.principal_id = None
        context.tenant_id = "test_tenant"

        creatives = [{"creative_id": "test", "name": "Test", "assets": {}}]

        with pytest.raises(ToolError) as exc_info:
            _sync_creatives_impl(creatives=creatives, context=context)

        assert "Authentication required" in str(exc_info.value)

    def test_tool_context_with_empty_string_principal_id(self):
        """ToolContext with empty string principal_id should be rejected."""
        from src.core.tools.creatives import _sync_creatives_impl

        # Create ToolContext with empty principal_id
        context = Mock(spec=ToolContext)
        context.principal_id = ""  # Empty string
        context.tenant_id = "test_tenant"

        creatives = [{"creative_id": "test", "name": "Test", "assets": {}}]

        with pytest.raises(ToolError) as exc_info:
            _sync_creatives_impl(creatives=creatives, context=context)

        assert "Authentication required" in str(exc_info.value)


class TestAuthenticationErrorMessages:
    """Test that auth error messages are clear and actionable."""

    def test_sync_creatives_error_message_mentions_header(self):
        """Error message should mention x-adcp-auth header."""
        from src.core.tools.creatives import _sync_creatives_impl

        with pytest.raises(ToolError) as exc_info:
            _sync_creatives_impl(creatives=[], context=None)

        error_msg = str(exc_info.value)
        # Should mention the header name so users know what to fix
        assert "x-adcp-auth" in error_msg

    def test_update_media_buy_error_message_actionable(self):
        """Error message should be actionable for developers."""
        from src.core.tools.media_buy_update import _verify_principal

        with pytest.raises(ToolError) as exc_info:
            _verify_principal(media_buy_id="test", context=None)

        error_msg = str(exc_info.value)
        # Should explain what's missing
        assert "Authentication required" in error_msg
        assert "x-adcp-auth" in error_msg


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
