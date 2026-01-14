"""Unit tests for media_buy_create helper functions.

Tests the helper functions used in media buy creation, particularly
format specification retrieval, creative validation, status determination,
and URL extraction.
"""

from unittest.mock import AsyncMock, Mock, patch

from src.core.tools.media_buy_create import _get_format_spec_sync


class TestGetFormatSpecSync:
    """Test synchronous format specification retrieval."""

    def test_successful_format_retrieval(self):
        """Test successful format spec retrieval with mocked registry."""
        # Create mock format spec
        mock_format_spec = Mock()
        mock_format_spec.format_id = Mock()
        mock_format_spec.format_id.id = "display_300x250_image"
        mock_format_spec.name = "Medium Rectangle - Image"

        # Mock the registry to avoid HTTP calls
        mock_registry = Mock()
        mock_registry.get_format = AsyncMock(return_value=mock_format_spec)

        # Patch where the function is imported from
        with patch("src.core.creative_agent_registry.get_creative_agent_registry", return_value=mock_registry):
            format_spec = _get_format_spec_sync("https://creative.adcontextprotocol.org", "display_300x250_image")
            assert format_spec is not None
            assert format_spec.format_id.id == "display_300x250_image"
            assert format_spec.name == "Medium Rectangle - Image"

    def test_unknown_format_returns_none(self):
        """Test that unknown format returns None."""
        # Mock registry returning None for unknown format
        mock_registry = Mock()
        mock_registry.get_format = AsyncMock(return_value=None)

        # Patch where the function is imported from
        with patch("src.core.creative_agent_registry.get_creative_agent_registry", return_value=mock_registry):
            format_spec = _get_format_spec_sync("https://creative.adcontextprotocol.org", "unknown_format_xyz")
            assert format_spec is None
