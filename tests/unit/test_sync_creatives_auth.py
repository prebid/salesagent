"""Test authentication requirement for sync_creatives."""

import pytest
from fastmcp.exceptions import ToolError

from src.core.tools.creatives import _sync_creatives_impl


def test_sync_creatives_requires_authentication():
    """sync_creatives should raise ToolError when principal_id is None (no auth)."""
    # Prepare minimal creative data
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

    # Call without context (simulates missing auth header)
    with pytest.raises(ToolError) as exc_info:
        _sync_creatives_impl(creatives=creatives, context=None)

    # Verify error message mentions authentication
    error_msg = str(exc_info.value)
    assert "Authentication required" in error_msg
    assert "x-adcp-auth" in error_msg


def test_sync_creatives_with_invalid_auth():
    """sync_creatives should raise ToolError when auth token is invalid."""
    from unittest.mock import Mock

    from src.core.tool_context import ToolContext

    # Create context with invalid principal_id (simulates invalid token)
    # In real scenario, get_principal_id_from_context returns None for invalid tokens
    invalid_context = Mock(spec=ToolContext)
    invalid_context.principal_id = None  # Invalid/missing principal
    invalid_context.tenant_id = "test_tenant"

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

    # Call with invalid auth context
    with pytest.raises(ToolError) as exc_info:
        _sync_creatives_impl(creatives=creatives, context=invalid_context)

    # Verify error message
    error_msg = str(exc_info.value)
    assert "Authentication required" in error_msg or "x-adcp-auth" in error_msg


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
