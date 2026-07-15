"""Unit tests for validate_creative_format_against_product helper function.

Uses Product.model_construct() to bypass Pydantic validation and create minimal
test objects without all required fields. This tests the actual code path with
proper types while keeping tests simple.
"""

from src.core.helpers import validate_creative_format_against_product
from src.core.schemas import FormatId, Product


class TestValidateCreativeFormatAgainstProduct:
    """Test creative format validation against a product (binary check)."""

    def test_valid_format_matches_product(self):
        """Test that a valid creative format matches a product."""
        creative_format_id = FormatId(agent_url="https://creative.adcontextprotocol.org", id="display_300x250_image")
        product = Product.model_construct(
            product_id="product_1",
            name="Banner Product",
            format_ids=[FormatId(agent_url="https://creative.adcontextprotocol.org", id="display_300x250_image")],
        )

        is_valid, error = validate_creative_format_against_product(
            creative_format_id=creative_format_id,
            product=product,
        )

        assert is_valid is True
        assert error is None

    def test_invalid_format_no_match(self):
        """Test that an invalid creative format does not match the product."""
        creative_format_id = FormatId(agent_url="https://creative.adcontextprotocol.org", id="video_instream_15s")
        product = Product.model_construct(
            product_id="product_1",
            name="Banner Product",
            format_ids=[FormatId(agent_url="https://creative.adcontextprotocol.org", id="display_300x250_image")],
        )

        is_valid, error = validate_creative_format_against_product(
            creative_format_id=creative_format_id,
            product=product,
        )

        assert is_valid is False
        assert error is not None
        assert "does not match product" in error
        assert "video_instream_15s" in error
        assert "Banner Product" in error

    def test_product_with_no_format_restrictions_matches_all(self):
        """Test that products with no format_ids accept all creative formats."""
        creative_format_id = FormatId(agent_url="https://creative.adcontextprotocol.org", id="any_format")
        product = Product.model_construct(
            product_id="product_1",
            name="Unrestricted Product",
            format_ids=[],  # No format restrictions
        )

        is_valid, error = validate_creative_format_against_product(
            creative_format_id=creative_format_id,
            product=product,
        )

        assert is_valid is True
        assert error is None

    def test_product_with_multiple_formats(self):
        """Test that creative matches when product supports multiple formats."""
        creative_format_id = FormatId(agent_url="https://creative.adcontextprotocol.org", id="display_728x90_image")
        product = Product.model_construct(
            product_id="product_1",
            name="Multi-Format Product",
            format_ids=[
                FormatId(agent_url="https://creative.adcontextprotocol.org", id="display_300x250_image"),
                FormatId(agent_url="https://creative.adcontextprotocol.org", id="display_728x90_image"),
                FormatId(agent_url="https://creative.adcontextprotocol.org", id="video_instream_15s"),
            ],
        )

        is_valid, error = validate_creative_format_against_product(
            creative_format_id=creative_format_id,
            product=product,
        )

        assert is_valid is True
        assert error is None

    def test_error_message_includes_supported_formats(self):
        """Test that error message includes supported formats."""
        creative_format_id = FormatId(agent_url="https://creative.adcontextprotocol.org", id="display_300x250_image")
        product = Product.model_construct(
            product_id="product_1",
            name="Video Product",
            format_ids=[
                FormatId(agent_url="https://creative.adcontextprotocol.org", id="video_instream_15s"),
                FormatId(agent_url="https://creative.adcontextprotocol.org", id="video_instream_30s"),
            ],
        )

        is_valid, error = validate_creative_format_against_product(
            creative_format_id=creative_format_id,
            product=product,
        )

        assert is_valid is False
        assert error is not None
        assert "display_300x250_image" in error
        assert "video_instream_15s" in error
        assert "video_instream_30s" in error
        assert "Video Product" in error

    def test_different_agent_urls_do_not_match(self):
        """Test that creatives from different agents do not match."""
        creative_format_id = FormatId(agent_url="https://different-agent.example.com", id="display_300x250_image")
        product = Product.model_construct(
            product_id="product_1",
            name="Banner Product",
            format_ids=[FormatId(agent_url="https://creative.adcontextprotocol.org", id="display_300x250_image")],
        )

        is_valid, error = validate_creative_format_against_product(
            creative_format_id=creative_format_id,
            product=product,
        )

        assert is_valid is False
        assert error is not None

    def test_exact_match_required(self):
        """Test that format_id must match exactly (agent_url + id)."""
        creative_format_id = FormatId(agent_url="https://creative.adcontextprotocol.org", id="display_300x250_image")
        product = Product.model_construct(
            product_id="product_1",
            name="Banner Product",
            format_ids=[
                FormatId(
                    agent_url="https://creative.adcontextprotocol.org", id="display_300x600_image"
                )  # Different size
            ],
        )

        is_valid, error = validate_creative_format_against_product(
            creative_format_id=creative_format_id,
            product=product,
        )

        assert is_valid is False
        assert error is not None

    def test_product_with_none_format_ids(self):
        """Test that None format_ids behaves like empty list (accepts all).

        Edge case: Product.model_construct() can bypass validation and set format_ids=None.
        The function should treat this the same as an empty list (accept all formats).
        """
        creative_format_id = FormatId(agent_url="https://creative.adcontextprotocol.org", id="any_format")
        product = Product.model_construct(
            product_id="product_1",
            name="Unrestricted Product",
            format_ids=None,  # Bypass validation - None should behave like empty list
        )

        is_valid, error = validate_creative_format_against_product(
            creative_format_id=creative_format_id,
            product=product,
        )

        assert is_valid is True
        assert error is None

    def test_url_normalization_handled_by_pydantic(self):
        """Test that Pydantic URL normalization makes URLs match correctly.

        Pydantic normalizes URLs automatically (adds trailing slash to bare domains).
        This test verifies that normalized URLs match correctly.
        """
        # Pydantic normalizes "http://example.com" → "http://example.com/"
        creative_format_id = FormatId(agent_url="http://example.com", id="banner_300x250")
        product = Product.model_construct(
            product_id="product_1",
            name="Test Product",
            format_ids=[
                FormatId(agent_url="http://example.com", id="banner_300x250")  # Same URL, Pydantic normalizes both
            ],
        )

        is_valid, error = validate_creative_format_against_product(
            creative_format_id=creative_format_id,
            product=product,
        )

        assert is_valid is True
        assert error is None

    def test_transport_suffix_variants_match(self):
        """/a2a- and /mcp-suffixed agent_urls match the bare product URL (#1172).

        The consolidation onto one canonical comparison key widened the check
        from trailing-slash-only to transport-suffix tolerance; this pins the
        /a2a variant (the /mcp variant is pinned at tests/unit/test_creative.py).
        """
        product = Product.model_construct(
            product_id="product_1",
            name="Test Product",
            format_ids=[FormatId(agent_url="https://creative.example.com", id="banner_300x250")],
        )
        for suffix in ("/a2a", "/mcp"):
            creative_format_id = FormatId(agent_url=f"https://creative.example.com{suffix}", id="banner_300x250")
            is_valid, error = validate_creative_format_against_product(
                creative_format_id=creative_format_id,
                product=product,
            )
            assert is_valid is True, f"{suffix} variant rejected: {error}"
            assert error is None
