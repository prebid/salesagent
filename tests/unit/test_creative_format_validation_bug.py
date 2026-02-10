"""Test for creative format validation bug fix.

This test verifies the fix for the bug where validate_creative_format_against_product
was called with creative.format (a string) instead of a FormatId object, causing:
    AttributeError: 'str' object has no attribute 'agent_url'

The database Creative model stores:
- agent_url: str (e.g., "https://creative.adcontextprotocol.org/")
- format: str (e.g., "display_970x250_image")

But validate_creative_format_against_product expects a FormatId object with both
agent_url and id attributes.

Additionally, the database Product model stores format_ids as list[dict], not list[FormatId],
so the validation function must handle both formats.
"""

from unittest.mock import MagicMock

import pytest

from src.core.helpers.creative_helpers import validate_creative_format_against_product
from src.core.schemas import FormatId


class TestCreativeFormatValidationBug:
    """Tests for the creative format validation bug fix."""

    def test_validate_with_string_format_raises_attribute_error(self):
        """Reproduce the bug: passing a string instead of FormatId raises AttributeError.

        This test demonstrates the bug before the fix.
        The code was passing creative.format (a string like "display_970x250_image")
        instead of a FormatId object.
        """
        # Simulate what the database returns: format as a plain string
        creative_format_string = "display_970x250_image"

        # Create a mock product with format_ids
        mock_product = MagicMock()
        mock_product.format_ids = [
            FormatId(agent_url="https://creative.adcontextprotocol.org/", id="display_970x250_image")
        ]
        mock_product.product_id = "test_product_1"
        mock_product.name = "Test Product"

        # This should raise AttributeError because string has no .agent_url
        with pytest.raises(AttributeError, match="'str' object has no attribute 'agent_url'"):
            validate_creative_format_against_product(
                creative_format_id=creative_format_string,  # Bug: passing string instead of FormatId
                product=mock_product,
            )

    def test_validate_with_format_id_object_works(self):
        """Verify the fix: passing a proper FormatId object works correctly.

        This test demonstrates the correct behavior after the fix.
        The code should construct a FormatId from creative.agent_url and creative.format.
        """
        # Simulate what the database returns
        creative_agent_url = "https://creative.adcontextprotocol.org/"
        creative_format = "display_970x250_image"

        # Construct FormatId from database fields (this is the fix)
        creative_format_id = FormatId(agent_url=creative_agent_url, id=creative_format)

        # Create a mock product with matching format_ids
        mock_product = MagicMock()
        mock_product.format_ids = [
            FormatId(agent_url="https://creative.adcontextprotocol.org/", id="display_970x250_image")
        ]
        mock_product.product_id = "test_product_1"
        mock_product.name = "Test Product"

        # This should work and return valid=True
        is_valid, error = validate_creative_format_against_product(
            creative_format_id=creative_format_id,
            product=mock_product,
        )

        assert is_valid is True
        assert error is None

    def test_validate_with_format_id_object_mismatch(self):
        """Verify validation correctly detects format mismatch."""
        # Creative has a different format than product supports
        creative_format_id = FormatId(
            agent_url="https://creative.adcontextprotocol.org/",
            id="video_300x250",  # Different format
        )

        # Product only supports display format
        mock_product = MagicMock()
        mock_product.format_ids = [
            FormatId(agent_url="https://creative.adcontextprotocol.org/", id="display_970x250_image")
        ]
        mock_product.product_id = "test_product_1"
        mock_product.name = "Test Product"

        # Should return invalid with error message
        is_valid, error = validate_creative_format_against_product(
            creative_format_id=creative_format_id,
            product=mock_product,
        )

        assert is_valid is False
        assert error is not None
        assert "video_300x250" in error
        assert "display_970x250_image" in error

    def test_validate_with_different_agent_url_mismatch(self):
        """Verify validation correctly detects agent_url mismatch."""
        # Creative from different agent
        creative_format_id = FormatId(
            agent_url="https://other-agent.example.com/",  # Different agent
            id="display_970x250_image",
        )

        # Product expects format from different agent
        mock_product = MagicMock()
        mock_product.format_ids = [
            FormatId(agent_url="https://creative.adcontextprotocol.org/", id="display_970x250_image")
        ]
        mock_product.product_id = "test_product_1"
        mock_product.name = "Test Product"

        # Should return invalid because agent_url doesn't match
        is_valid, error = validate_creative_format_against_product(
            creative_format_id=creative_format_id,
            product=mock_product,
        )

        assert is_valid is False
        assert error is not None

    def test_validate_with_empty_product_format_ids_accepts_all(self):
        """Verify products with no format restrictions accept all creatives."""
        creative_format_id = FormatId(agent_url="https://creative.adcontextprotocol.org/", id="any_format")

        # Product with no format restrictions
        mock_product = MagicMock()
        mock_product.format_ids = []  # No restrictions
        mock_product.product_id = "test_product_1"
        mock_product.name = "Test Product"

        # Should accept any creative
        is_valid, error = validate_creative_format_against_product(
            creative_format_id=creative_format_id,
            product=mock_product,
        )

        assert is_valid is True
        assert error is None

    def test_validate_with_none_product_format_ids_accepts_all(self):
        """Verify products with None format_ids accept all creatives."""
        creative_format_id = FormatId(agent_url="https://creative.adcontextprotocol.org/", id="any_format")

        # Product with None format_ids
        mock_product = MagicMock()
        mock_product.format_ids = None  # No restrictions
        mock_product.product_id = "test_product_1"
        mock_product.name = "Test Product"

        # Should accept any creative
        is_valid, error = validate_creative_format_against_product(
            creative_format_id=creative_format_id,
            product=mock_product,
        )

        assert is_valid is True
        assert error is None


class TestProductFormatIdsAsDicts:
    """Tests for handling product.format_ids as dicts (database storage format).

    The database Product model stores format_ids as list[dict[str, str]], not list[FormatId].
    The validate_creative_format_against_product function must handle both formats.
    """

    def test_validate_with_product_format_ids_as_dicts(self):
        """Verify validation works when product.format_ids contains dicts."""
        creative_format_id = FormatId(agent_url="https://creative.adcontextprotocol.org/", id="display_970x250_image")

        # Product with format_ids as dicts (how database stores them)
        mock_product = MagicMock()
        mock_product.format_ids = [
            {"agent_url": "https://creative.adcontextprotocol.org/", "id": "display_970x250_image"}
        ]
        mock_product.product_id = "test_product_1"
        mock_product.name = "Test Product"

        # Should work and return valid=True
        is_valid, error = validate_creative_format_against_product(
            creative_format_id=creative_format_id,
            product=mock_product,
        )

        assert is_valid is True
        assert error is None

    def test_validate_with_product_format_ids_as_dicts_mismatch(self):
        """Verify validation detects mismatch when product.format_ids contains dicts."""
        creative_format_id = FormatId(agent_url="https://creative.adcontextprotocol.org/", id="video_300x250")

        # Product with format_ids as dicts
        mock_product = MagicMock()
        mock_product.format_ids = [
            {"agent_url": "https://creative.adcontextprotocol.org/", "id": "display_970x250_image"}
        ]
        mock_product.product_id = "test_product_1"
        mock_product.name = "Test Product"

        # Should return invalid
        is_valid, error = validate_creative_format_against_product(
            creative_format_id=creative_format_id,
            product=mock_product,
        )

        assert is_valid is False
        assert error is not None
        assert "video_300x250" in error

    def test_validate_with_product_format_ids_using_format_id_key(self):
        """Verify validation works with legacy 'format_id' key instead of 'id'."""
        creative_format_id = FormatId(agent_url="https://creative.adcontextprotocol.org/", id="display_970x250_image")

        # Product with format_ids using legacy 'format_id' key
        mock_product = MagicMock()
        mock_product.format_ids = [
            {"agent_url": "https://creative.adcontextprotocol.org/", "format_id": "display_970x250_image"}
        ]
        mock_product.product_id = "test_product_1"
        mock_product.name = "Test Product"

        # Should work and return valid=True
        is_valid, error = validate_creative_format_against_product(
            creative_format_id=creative_format_id,
            product=mock_product,
        )

        assert is_valid is True
        assert error is None

    def test_validate_with_mixed_format_ids(self):
        """Verify validation works with mixed FormatId objects and dicts."""
        creative_format_id = FormatId(agent_url="https://creative.adcontextprotocol.org/", id="display_300x250_image")

        # Product with mixed format_ids (both FormatId objects and dicts)
        mock_product = MagicMock()
        mock_product.format_ids = [
            {"agent_url": "https://creative.adcontextprotocol.org/", "id": "display_970x250_image"},
            FormatId(agent_url="https://creative.adcontextprotocol.org/", id="display_300x250_image"),
        ]
        mock_product.product_id = "test_product_1"
        mock_product.name = "Test Product"

        # Should work and return valid=True (matches second format)
        is_valid, error = validate_creative_format_against_product(
            creative_format_id=creative_format_id,
            product=mock_product,
        )

        assert is_valid is True
        assert error is None
