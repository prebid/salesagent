"""Test that anonymous users get products with empty pricing_options."""

from src.core.schemas import Product
from tests.helpers.adcp_factories import (
    create_test_cpm_pricing_option,
    create_test_publisher_properties_by_tag,
)


def test_product_with_empty_pricing_options():
    """Test that Product with auction pricing (no rate) works for anonymous users.

    Note: AdCP library requires at least 1 pricing option. For anonymous users,
    we use auction pricing (price_guidance only, no rate field) instead of empty list.
    """
    product = Product(
        product_id="test-1",
        name="Test Product",
        description="Test",
        format_ids=[{"agent_url": "https://creative.adcontextprotocol.org", "id": "display_banner_728x90"}],
        delivery_type="guaranteed",
        delivery_measurement={
            "provider": "test_provider",
            "notes": "Test measurement",
        },
        pricing_options=[
            {
                "pricing_option_id": "cpm_usd_auction",
                "pricing_model": "cpm",
                "currency": "USD",
                "is_fixed": False,  # Required in adcp 2.4.0+
                "price_guidance": {"floor": 1.0, "p50": 5.0},  # Median guidance for auction
                # Auction pricing (anonymous user view)
            }
        ],
        publisher_properties=[create_test_publisher_properties_by_tag(publisher_domain="test.com")],
    )

    # Verify the product serializes correctly
    dump = product.model_dump()
    assert "pricing_options" in dump
    assert "product_id" in dump
    assert "name" in dump
    assert "description" in dump
    # Verify no rate in pricing options (anonymous user case)
    assert "rate" not in dump["pricing_options"][0]


def test_product_with_pricing_options():
    """Test that Product includes pricing_options when populated (authenticated user case)."""
    product = Product(
        product_id="test-2",
        name="Test Product",
        description="Test",
        format_ids=[{"agent_url": "https://creative.adcontextprotocol.org", "id": "display_banner_728x90"}],
        delivery_type="guaranteed",
        delivery_measurement={
            "provider": "test_provider",
            "notes": "Test measurement",
        },
        pricing_options=[
            create_test_cpm_pricing_option(
                pricing_option_id="po-1",
                currency="USD",
                rate=10.0,
            )
        ],
        publisher_properties=[create_test_publisher_properties_by_tag(publisher_domain="test.com")],
    )

    # Verify the product serializes with pricing_options
    dump = product.model_dump()
    assert "pricing_options" in dump, "Non-empty pricing_options should be included in serialization"
    assert len(dump["pricing_options"]) == 1
    assert dump["pricing_options"][0]["pricing_model"] == "cpm"


def test_product_pricing_options_defaults_to_empty_list():
    """Test that pricing_options is required per AdCP spec.

    Note: AdCP library requires at least 1 pricing option - it does NOT default to empty list.
    This test verifies the requirement is enforced.
    """
    import pytest
    from pydantic import ValidationError

    # Attempting to create product without pricing_options should fail
    with pytest.raises(ValidationError) as exc_info:
        Product(
            product_id="test-3",
            name="Test Product",
            description="Test",
            format_ids=[{"agent_url": "https://creative.adcontextprotocol.org", "id": "display_banner_728x90"}],
            delivery_type="guaranteed",
            delivery_measurement={
                "provider": "test_provider",
                "notes": "Test measurement",
            },
            publisher_properties=[create_test_publisher_properties_by_tag(publisher_domain="test.com")],
            # pricing_options not provided - should raise validation error
        )

    # Verify the error is about missing pricing_options
    assert "pricing_options" in str(exc_info.value)
