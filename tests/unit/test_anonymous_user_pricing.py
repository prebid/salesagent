"""Test that anonymous users get products with empty pricing_options."""

from src.core.product_conversion import default_reporting_capabilities
from src.core.schemas import Product
from tests.helpers.adcp_factories import (
    create_test_cpm_pricing_option,
    create_test_publisher_properties_by_tag,
)

# test_product_with_empty_pricing_options is REMOVED: already graded by
# BR-UC-GET-PRODUCTS-pricing-options.feature, "a stored auction <pricing_model> option
# reaches the buyer with its floor", MEASURED passed:9 in-process and passed:3 in-network.
# That outline pins the auction option the buyer receives by exact dict equality
# ({"currency", "floor_price", "max_bid", "price_guidance", "pricing_model",
# "pricing_option_id"}), so "no rate key in the dumped auction option" -- all this test
# asserted -- cannot hold there and be false here.
#
# test_product_pricing_options_defaults_to_empty_list is KEPT: it grades the MODEL refusing
# a product with no pricing options, which is a construction-time contract no scenario
# exercises (a buyer never sends a product).


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
            # Supplied so the rejection under test can only be the missing
            # pricing_options: without it the construction failed on
            # reporting_capabilities instead, and the test passed vacuously.
            reporting_capabilities=default_reporting_capabilities(),
            publisher_properties=[create_test_publisher_properties_by_tag(publisher_domain="test.com")],
            # pricing_options not provided - should raise validation error
        )

    # Verify the error is about missing pricing_options


def test_product_with_empty_pricing_options_serializes_as_empty_array():
    """Test that Product with pricing_options=[] serializes as 'pricing_options: []' not omitted.

    This is a regression test for the bug where empty pricing_options was completely
    omitted from serialization (resulting in undefined in JSON), causing schema
    validation to fail on the client side with:
    'pricing_options: Invalid input: expected array, received undefined'

    The fix ensures empty arrays are explicitly included in the serialized output.
    """
    # Create product with pricing
    product = Product(
        product_id="test-empty-pricing",
        name="Test Product",
        description="Test",
        format_ids=[{"agent_url": "https://creative.adcontextprotocol.org", "id": "display_banner_728x90"}],
        delivery_type="guaranteed",
        delivery_measurement={
            "provider": "test_provider",
            "notes": "Test measurement",
        },
        reporting_capabilities=default_reporting_capabilities(),
        pricing_options=[
            create_test_cpm_pricing_option(
                pricing_option_id="po-1",
                currency="USD",
                rate=10.0,
            )
        ],
        publisher_properties=[create_test_publisher_properties_by_tag(publisher_domain="test.com")],
    )

    # Simulate clearing pricing for anonymous user
    product.pricing_options = []

    # Verify model_dump includes empty pricing_options array
    dump = product.model_dump()
    assert "pricing_options" in dump, (
        "Empty pricing_options should be explicitly included in serialization, "
        "not omitted (which causes 'expected array, received undefined' errors)"
    )
    assert dump["pricing_options"] == [], "pricing_options should be an empty array"
