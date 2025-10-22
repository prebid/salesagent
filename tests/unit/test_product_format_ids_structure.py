"""Test that Product format_ids are serialized as proper FormatId objects."""

from src.core.schemas import FormatId, PricingOption, Product


def test_product_format_ids_serialize_as_objects():
    """Test that Product.format_ids serialize as objects with agent_url and id.

    This test verifies the fix for the Wonderstruck issue where format_ids were
    being serialized as string representations instead of proper objects.
    """
    product = Product(
        product_id="test-product",
        name="Test Product",
        description="Test Description",
        formats=[
            FormatId(agent_url="https://creative.adcontextprotocol.org", id="display_300x250"),
            FormatId(agent_url="https://creative.adcontextprotocol.org", id="display_728x90"),
        ],
        delivery_type="guaranteed",
        property_tags=["all_inventory"],
        pricing_options=[
            PricingOption(
                pricing_option_id="opt1",
                pricing_model="cpm",
                is_auction=True,
                is_fixed=False,
                currency="USD",
                price_guidance={"floor": 5.0, "ceiling": 10.0},
            )
        ],
    )

    # Serialize using model_dump with alias (this is what gets sent to clients)
    serialized = product.model_dump(mode="json", by_alias=True)

    # Should have format_ids (not formats)
    assert "format_ids" in serialized, "Product should have format_ids field"
    assert "formats" not in serialized, "Product should not expose internal formats field"

    # format_ids should be a list
    assert isinstance(serialized["format_ids"], list), "format_ids should be a list"
    assert len(serialized["format_ids"]) == 2, "Should have 2 format_ids"

    # Each format_id should be an object with agent_url and id (NOT a string)
    for fmt in serialized["format_ids"]:
        assert isinstance(fmt, dict), f"format_id should be dict, got {type(fmt)}: {fmt}"
        assert "agent_url" in fmt, f"format_id missing agent_url: {fmt}"
        assert "id" in fmt, f"format_id missing id: {fmt}"
        assert fmt["agent_url"] == "https://creative.adcontextprotocol.org"
        assert fmt["id"] in ["display_300x250", "display_728x90"]

        # Verify it's NOT a string representation like "agent_url='...' format_id='...'"
        assert not isinstance(fmt, str), f"format_id should not be a string: {fmt}"


def test_product_format_ids_with_unknown_format():
    """Test that unknown format IDs get default agent_url and don't fail.

    This ensures graceful handling of legacy data with format IDs not in the cache.
    """
    product = Product(
        product_id="test-product",
        name="Test Product",
        description="Test Description",
        formats=["unknown_format", "video_15s"],  # Unknown formats as strings
        delivery_type="guaranteed",
        property_tags=["all_inventory"],
        pricing_options=[
            PricingOption(
                pricing_option_id="opt1",
                pricing_model="cpm",
                is_auction=True,
                is_fixed=False,
                currency="USD",
                price_guidance={"floor": 5.0, "ceiling": 10.0},
            )
        ],
    )

    # Serialize - should NOT raise an error
    serialized = product.model_dump(mode="json", by_alias=True)

    # Should gracefully handle unknown formats
    assert "format_ids" in serialized
    assert len(serialized["format_ids"]) == 2, "Should have 2 format_ids even for unknown formats"

    # Unknown formats should get default agent_url and proper structure
    for fmt in serialized["format_ids"]:
        assert isinstance(fmt, dict), f"Even unknown formats should be objects: {fmt}"
        assert "agent_url" in fmt, f"Unknown format missing agent_url: {fmt}"
        assert "id" in fmt, f"Unknown format missing id: {fmt}"
        # Default agent_url for unknown formats
        assert fmt["agent_url"] == "https://creative.adcontextprotocol.org"
        assert fmt["id"] in ["unknown_format", "video_15s"]
