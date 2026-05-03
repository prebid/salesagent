"""Test that GetProductsResponse __str__ provides human-readable content for protocols."""

from src.core.schemas import GetProductsResponse, Product
from tests.helpers.adcp_factories import (
    create_test_cpm_pricing_option,
    create_test_cpm_pricing_option_v3,
    create_test_format_id,
    create_test_pricing_option_library,
    create_test_publisher_properties_by_tag,
)


def _make_product(product_id: str, pricing_options: list) -> Product:
    return Product(
        product_id=product_id,
        name=f"Product {product_id}",
        description="A test",
        format_ids=[create_test_format_id("banner")],
        delivery_type="guaranteed",
        delivery_measurement={"provider": "test_provider", "notes": "Test measurement"},
        publisher_properties=[create_test_publisher_properties_by_tag(publisher_domain="test.com")],
        pricing_options=pricing_options,
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
    """__str__ adds auth message when every option lacks any rate-bearing field.

    "Anonymous" here means the buyer sees no rate-bearing data: no rate,
    no fixed_price, no floor_price, no price_guidance. The library RootModel
    permits this (extra='allow', no XOR) so we use a raw dict to construct
    a truly bare option that bypasses internal Pydantic validation.
    """
    bare_option = {
        "pricing_option_id": "cpm_usd_anonymous",
        "pricing_model": "cpm",
        "currency": "USD",
        # No rate, fixed_price, floor_price, or price_guidance — buyer sees nothing.
    }
    products = [_make_product(f"test{i}", [bare_option]) for i in range(2)]

    response = GetProductsResponse(products=products)
    content = str(response)

    assert (
        content
        == "Found 2 products that match your requirements. Please connect through an authorized buying agent for pricing data."
    )


def test_get_products_response_str_v3_authenticated_priced_no_anonymous_suffix():
    """Regression #1246: v3 authenticated buyer with fixed_price MUST NOT see auth suffix.

    The bug: the helper that gates the auth suffix only inspected the v2 field
    `rate`. Production-shape v3 pricing options carry `fixed_price` instead;
    the helper returned False on every v3 option, so authenticated buyers
    incorrectly saw the "Please connect through an authorized buying agent"
    suffix on every response.

    This test uses the production wire shape (adcp library RootModel) to pin
    the fix.
    """
    product = _make_product(
        "v3_fixed",
        [create_test_pricing_option_library(fixed_price=10.0)],
    )
    response = GetProductsResponse(products=[product])
    content = str(response)

    assert "authorized buying agent" not in content
    assert content == "Found 1 product that matches your requirements."


def test_get_products_response_str_v3_authenticated_floor_price_no_anonymous_suffix():
    """Regression #1246: v3 authenticated auction buyer (floor_price set) MUST NOT see auth suffix."""
    product = _make_product(
        "v3_auction",
        [create_test_pricing_option_library(floor_price=5.0, price_guidance={"p50": 7.0})],
    )
    response = GetProductsResponse(products=[product])

    assert "authorized buying agent" not in str(response)


def test_get_products_response_str_v3_authenticated_price_guidance_only_no_anonymous_suffix():
    """v3 spec-legal auction: only price_guidance (no fixed_price, no floor_price). Buyer sees percentile hints — priced."""
    product = _make_product(
        "v3_pg_only",
        [create_test_pricing_option_library(price_guidance={"p25": 4.0, "p50": 6.0, "p75": 8.0})],
    )
    response = GetProductsResponse(products=[product])

    assert "authorized buying agent" not in str(response)


def test_get_products_response_str_mixed_priced_and_bare_no_anonymous_suffix():
    """One priced product + one bare-option product: heuristic only fires when ALL options are rate-less."""
    bare_option = {
        "pricing_option_id": "bare",
        "pricing_model": "cpm",
        "currency": "USD",
    }
    products = [
        _make_product("priced", [create_test_cpm_pricing_option_v3(fixed_price=10.0)]),
        _make_product("bare", [bare_option]),
    ]
    response = GetProductsResponse(products=products)

    # The priced product breaks the inner all(...) — anonymous suffix should not fire.
    assert "authorized buying agent" not in str(response)


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
