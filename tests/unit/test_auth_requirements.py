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

Migration note:
--------------
_impl functions now accept `identity: ResolvedIdentity | None` instead of
transport-specific context objects. Tests pass identity=None for unauthenticated
scenarios and ResolvedIdentity(principal_id=None) for invalid auth scenarios.
"""

import pytest
from fastmcp.exceptions import ToolError

from src.core.exceptions import AdCPAuthenticationError, AdCPValidationError
from src.core.resolved_identity import ResolvedIdentity


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

        # Call without identity (no auth) — _impl raises AdCPAuthenticationError (transport-agnostic)
        with pytest.raises(AdCPAuthenticationError) as exc_info:
            _sync_creatives_impl(creatives=creatives, identity=None)

        error_msg = str(exc_info.value)
        assert "Authentication required" in error_msg
        assert "x-adcp-auth" in error_msg

    def test_sync_creatives_with_invalid_auth(self):
        """sync_creatives must reject requests with invalid authentication."""
        from src.core.tools.creatives import _sync_creatives_impl

        # ResolvedIdentity with None principal_id (simulates invalid token)
        invalid_identity = ResolvedIdentity(principal_id=None, tenant_id="test_tenant")

        creatives = [
            {
                "creative_id": "test_creative",
                "name": "Test Creative",
                "format_id": "display_728x90_image",
                "assets": {"banner_image": {"url": "https://example.com/banner.png"}},
            }
        ]

        with pytest.raises(AdCPAuthenticationError) as exc_info:
            _sync_creatives_impl(creatives=creatives, identity=invalid_identity)

        assert "Authentication required" in str(exc_info.value)

    def test_list_creatives_requires_authentication(self):
        """list_creatives must reject requests without authentication."""
        from src.core.tools.creatives import _list_creatives_impl

        # Call without identity (no auth) — _impl raises AdCPAuthenticationError (transport-agnostic)
        with pytest.raises(AdCPAuthenticationError) as exc_info:
            _list_creatives_impl(identity=None)

        error_msg = str(exc_info.value)
        assert "x-adcp-auth" in error_msg

    # =========================================================================
    # Media Buy Tools
    # =========================================================================

    def test_create_media_buy_requires_authentication(self):
        """create_media_buy must reject requests without authentication."""
        import asyncio

        from src.core.schemas import CreateMediaBuyRequest
        from src.core.tools.media_buy_create import _create_media_buy_impl

        # Construct spec-compliant request at the test boundary (matches refactored _impl signature)
        req = CreateMediaBuyRequest(
            buyer_ref="test_buyer",
            brand={"domain": "testbrand.com"},
            packages=[
                {
                    "buyer_ref": "pkg1",
                    "product_id": "prod1",
                    "budget": 1000.0,
                    "pricing_option_id": "test_pricing",
                }
            ],
            start_time="2025-01-01T00:00:00Z",
            end_time="2025-01-31T23:59:59Z",
        )

        # Call without identity (no auth) — _impl raises AdCPValidationError (transport-agnostic)
        with pytest.raises((AdCPValidationError, AdCPAuthenticationError)) as exc_info:
            asyncio.run(_create_media_buy_impl(req=req, identity=None))

        error_msg = str(exc_info.value)
        # create_media_buy validates identity presence first
        assert (
            "Identity is required" in error_msg
            or "Principal ID not found" in error_msg
            or "authentication required" in error_msg.lower()
        )

    def test_update_media_buy_requires_authentication(self):
        """update_media_buy must reject requests without authentication."""
        from unittest.mock import MagicMock

        from src.core.resolved_identity import ResolvedIdentity
        from src.core.tools.media_buy_update import _verify_principal

        # ResolvedIdentity with no principal_id — _verify_principal raises AdCPAuthenticationError
        no_auth_identity = ResolvedIdentity(
            principal_id=None, tenant_id="default", tenant={"tenant_id": "default"}, protocol="rest"
        )
        # repo is not accessed when principal_id is None (early exit)
        with pytest.raises(AdCPAuthenticationError) as exc_info:
            _verify_principal(media_buy_id="test_buy", context=no_auth_identity, repo=MagicMock())

        error_msg = str(exc_info.value)
        assert "Authentication required" in error_msg
        assert "x-adcp-auth" in error_msg

    def test_update_media_buy_with_invalid_auth(self):
        """update_media_buy must reject requests with invalid auth."""
        from unittest.mock import MagicMock

        from src.core.resolved_identity import ResolvedIdentity
        from src.core.tools.media_buy_update import _verify_principal

        # ResolvedIdentity with None principal_id
        invalid_identity = ResolvedIdentity(
            principal_id=None, tenant_id="test_tenant", tenant={"tenant_id": "test_tenant"}, protocol="rest"
        )

        # repo is not accessed when principal_id is None (early exit)
        with pytest.raises(AdCPAuthenticationError) as exc_info:
            _verify_principal(media_buy_id="test_buy", context=invalid_identity, repo=MagicMock())

        assert "Authentication required" in str(exc_info.value)

    def test_get_media_buy_delivery_requires_authentication(self):
        """get_media_buy_delivery must reject requests without authentication."""
        from src.core.schemas import GetMediaBuyDeliveryRequest
        from src.core.tools.media_buy_delivery import _get_media_buy_delivery_impl

        req = GetMediaBuyDeliveryRequest(media_buy_ids=["test_buy"])

        # Call without identity (no auth) — _impl raises AdCPValidationError (transport-agnostic)
        with pytest.raises((AdCPValidationError, AdCPAuthenticationError, ToolError, ValueError)) as exc_info:
            _get_media_buy_delivery_impl(req=req, identity=None)

        error_msg = str(exc_info.value)
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

        # Call without identity (no auth) — _impl raises ValueError or AdCPAuthenticationError (transport-agnostic)
        with pytest.raises((AdCPValidationError, AdCPAuthenticationError, ToolError, ValueError)) as exc_info:
            _update_performance_index_impl(
                media_buy_id="test_buy",
                performance_data=[{"product_id": "prod1", "performance_index": 0.8}],
                identity=None,
            )

        error_msg = str(exc_info.value)
        assert (
            "Identity is required" in error_msg
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

        # Call without identity (no auth) — _impl raises an error before proceeding.
        # May raise RuntimeError (no tenant context), AdCPAuthenticationError, or AdCPValidationError.
        with pytest.raises((AdCPAuthenticationError, AdCPValidationError, RuntimeError)) as exc_info:
            asyncio.run(
                _activate_signal_impl(signal_agent_segment_id="test_signal", media_buy_id="test_buy", identity=None)
            )

        error_msg = str(exc_info.value).lower()
        assert "authentication required" in error_msg or "context" in error_msg or "tenant" in error_msg


class TestAuthenticationWithMockedContext:
    """Test authentication behavior with various identity scenarios."""

    def test_identity_with_none_principal_id(self):
        """ResolvedIdentity with None principal_id should be rejected."""
        from src.core.tools.creatives import _sync_creatives_impl

        # ResolvedIdentity with None principal_id (invalid token scenario)
        identity = ResolvedIdentity(principal_id=None, tenant_id="test_tenant")

        creatives = [{"creative_id": "test", "name": "Test", "assets": {}}]

        with pytest.raises(AdCPAuthenticationError) as exc_info:
            _sync_creatives_impl(creatives=creatives, identity=identity)

        assert "Authentication required" in str(exc_info.value)

    def test_identity_with_empty_string_principal_id(self):
        """ResolvedIdentity with empty string principal_id should be rejected."""
        from src.core.tools.creatives import _sync_creatives_impl

        # ResolvedIdentity with empty principal_id
        identity = ResolvedIdentity(principal_id="", tenant_id="test_tenant")

        creatives = [{"creative_id": "test", "name": "Test", "assets": {}}]

        with pytest.raises(AdCPAuthenticationError) as exc_info:
            _sync_creatives_impl(creatives=creatives, identity=identity)

        assert "Authentication required" in str(exc_info.value)


class TestAuthenticationErrorMessages:
    """Test that auth error messages are clear and actionable."""

    def test_sync_creatives_error_message_mentions_header(self):
        """Error message should mention x-adcp-auth header."""
        from src.core.tools.creatives import _sync_creatives_impl

        with pytest.raises(AdCPAuthenticationError) as exc_info:
            _sync_creatives_impl(creatives=[], identity=None)

        error_msg = str(exc_info.value)
        # Should mention the header name so users know what to fix
        assert "x-adcp-auth" in error_msg

    def test_update_media_buy_error_message_actionable(self):
        """Error message should be actionable for developers."""
        from unittest.mock import MagicMock

        from src.core.resolved_identity import ResolvedIdentity
        from src.core.tools.media_buy_update import _verify_principal

        no_auth = ResolvedIdentity(
            principal_id=None, tenant_id="default", tenant={"tenant_id": "default"}, protocol="rest"
        )
        # repo is not accessed when principal_id is None (early exit)
        with pytest.raises(AdCPAuthenticationError) as exc_info:
            _verify_principal(media_buy_id="test", context=no_auth, repo=MagicMock())

        error_msg = str(exc_info.value)
        # Should explain what's missing
        assert "Authentication required" in error_msg
        assert "x-adcp-auth" in error_msg


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
