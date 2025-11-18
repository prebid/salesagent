"""Test that GetProductsResponse __str__ provides human-readable content for protocols."""

from src.core.schemas import GetProductsResponse, Product
from tests.helpers.adcp_factories import (
    create_test_cpm_pricing_option,
    create_test_format_id,
    create_test_publisher_properties_by_tag,
)


def test_get_products_response_str_single_product():
    """Test that __str__ returns appropriate message for single product."""
    product = Product(
        product_id="test",
        name="Test Product",
        description="A test",
        format_ids=[create_test_format_id("banner")],
        delivery_type="guaranteed",
        delivery_measurement={"provider": "test_provider", "notes": "Test measurement"},
        publisher_properties=[create_test_publisher_properties_by_tag(publisher_domain="test.com")],
        pricing_options=[
            create_test_cpm_pricing_option(
                pricing_option_id="cpm_usd_fixed",
                currency="USD",
                rate=10.0,
                min_spend_per_package=100.0,
            )
        ],
    )

    response = GetProductsResponse(products=[product])

    content = str(response)

    # Should return human-readable message, not JSON
    assert content == "Found 1 product that matches your requirements."
    assert "{" not in content  # Should not contain JSON
    assert "product_id" not in content  # Should not contain field names


def test_get_products_response_str_multiple_products():
    """Test that __str__ generates appropriate message for multiple products."""
    products = [
        Product(
            product_id=f"test{i}",
            name=f"Test {i}",
            description="A test",
            format_ids=[create_test_format_id("banner")],
            delivery_type="guaranteed",
            delivery_measurement={"provider": "test_provider", "notes": "Test measurement"},
            publisher_properties=[create_test_publisher_properties_by_tag(publisher_domain="test.com")],
            pricing_options=[
                create_test_cpm_pricing_option(
                    pricing_option_id="cpm_usd_fixed",
                    currency="USD",
                    rate=10.0,
                    min_spend_per_package=100.0,
                )
            ],
        )
        for i in range(3)
    ]

    response = GetProductsResponse(products=products)
    content = str(response)

    assert content == "Found 3 products that match your requirements."
    assert "{" not in content


def test_get_products_response_str_empty():
    """Test that __str__ handles empty product list."""
    response = GetProductsResponse(products=[])
    content = str(response)

    assert content == "No products matched your requirements."


def test_get_products_response_str_anonymous_user():
    """Test that __str__ detects anonymous users (no pricing) and adds auth message."""
    products = [
        Product(
            product_id=f"test{i}",
            name=f"Test {i}",
            description="A test",
            format_ids=[create_test_format_id("banner")],
            delivery_type="guaranteed",
            delivery_measurement={"provider": "test_provider", "notes": "Test measurement"},
            publisher_properties=[create_test_publisher_properties_by_tag(publisher_domain="test.com")],
            pricing_options=[
                {
                    "pricing_option_id": "cpm_usd_auction",
                    "pricing_model": "cpm",
                    "currency": "USD",
                    "is_fixed": False,  # Required in adcp 2.4.0+
                    "price_guidance": {"floor": 1.0, "p50": 5.0},
                    # Auction pricing (anonymous user)
                }
            ],
        )
        for i in range(2)
    ]

    response = GetProductsResponse(products=products)
    content = str(response)

    assert (
        content
        == "Found 2 products that match your requirements. Please connect through an authorized buying agent for pricing data."
    )


def test_get_products_response_model_dump_still_has_full_data():
    """Verify that model_dump() still returns full structured data."""
    product = Product(
        product_id="test",
        name="Test Product",
        description="A test",
        format_ids=[create_test_format_id("banner")],
        delivery_type="guaranteed",
        delivery_measurement={"provider": "test_provider", "notes": "Test measurement"},
        publisher_properties=[create_test_publisher_properties_by_tag(publisher_domain="test.com")],
        pricing_options=[
            create_test_cpm_pricing_option(
                pricing_option_id="cpm_usd_fixed",
                currency="USD",
                rate=10.0,
                min_spend_per_package=100.0,
            )
        ],
    )

    response = GetProductsResponse(products=[product])

    # str() should be human-readable
    assert str(response) == "Found 1 product that matches your requirements."

    # model_dump() should have full structure
    data = response.model_dump()
    assert "products" in data
    assert len(data["products"]) == 1
    assert data["products"][0]["product_id"] == "test"
    # message field no longer exists in schema (handled by __str__())
    assert "message" not in data
