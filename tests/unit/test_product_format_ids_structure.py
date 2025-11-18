"""Test that Product format_ids are serialized as proper FormatId objects."""

from tests.helpers.adcp_factories import create_test_product


def test_product_format_ids_serialize_as_objects():
    """Test that Product.format_ids serialize as objects with agent_url and id.

    This test verifies the fix for the Wonderstruck issue where format_ids were
    being serialized as string representations instead of proper objects.
    """
    # Use factory - automatically creates valid Product with FormatId objects
    product = create_test_product(
        product_id="test-product",
        format_ids=["display_300x250", "display_728x90"],  # Factory converts to FormatId objects
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
        assert str(fmt["agent_url"]).rstrip("/") == "https://creative.adcontextprotocol.org"
        assert fmt["id"] in ["display_300x250", "display_728x90"]

        # Verify it's NOT a string representation like "agent_url='...' format_id='...'"
        assert not isinstance(fmt, str), f"format_id should not be a string: {fmt}"


def test_product_format_ids_with_custom_agent():
    """Test that format IDs from custom creative agents serialize correctly.

    This ensures we support format IDs from different creative agent implementations.
    """
    # Use factory with mixed format IDs - some from standard agent, some from custom
    product = create_test_product(
        product_id="test-product",
        format_ids=[
            {"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"},
            {"agent_url": "https://custom-publisher.com/.well-known/adcp/sales", "id": "custom_format"},
        ],
    )

    # Serialize - should NOT raise an error
    serialized = product.model_dump(mode="json", by_alias=True)

    # Should handle custom agent URLs
    assert "format_ids" in serialized
    assert len(serialized["format_ids"]) == 2, "Should have 2 format_ids"

    # Verify both standard and custom format IDs preserve their agent URLs
    assert str(serialized["format_ids"][0]["agent_url"]).rstrip("/") == "https://creative.adcontextprotocol.org"
    assert serialized["format_ids"][0]["id"] == "display_300x250"

    assert str(serialized["format_ids"][1]["agent_url"]).rstrip("/") == "https://custom-publisher.com/.well-known/adcp/sales"
    assert serialized["format_ids"][1]["id"] == "custom_format"
