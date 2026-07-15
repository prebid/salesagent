"""Test for creative format validation bug fix.

This test verifies the fix for the bug where validate_creative_format_against_product
was called with creative.format (a string) instead of a FormatId object, causing:
    AttributeError: 'str' object has no attribute 'agent_url'

The database Creative model stores:
- agent_url: str (e.g., "https://creative.adcontextprotocol.org/")
- format: str (e.g., "display_970x250_image")

But validate_creative_format_against_product expects a FormatId object with both
agent_url and id attributes.

Since GH #1172 the Product.format_ids column is typed at the DB boundary
(JSONType(model=FormatId, is_list=True)) — ORM reads yield list[FormatId], so the
validation function trusts typed entries and no longer re-parses dict shapes.
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


# NOTE (GH #1172): the former TestProductFormatIdsAsDicts class pinned dict-shaped
# (and legacy "format_id"-keyed) product.format_ids entries. That storage shape was
# retired when the column became JSONType(model=FormatId, is_list=True) — the ORM
# now yields typed FormatId models on every read, so the dict-tolerant branches it
# exercised were deleted from validate_creative_format_against_product. The typed
# match/mismatch behavior stays covered by TestCreativeFormatValidationBug above and
# tests/unit/test_validate_creative_format_against_product.py.
