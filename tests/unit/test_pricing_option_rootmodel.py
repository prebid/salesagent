"""Test that pricing_option RootModel wrappers are correctly unwrapped.

This tests the fix for accessing pricing_options attributes when the adcp library
wraps pricing options in a PricingOption RootModel. The code must unwrap via .root
to access attributes like .pricing_model, .currency, and .is_fixed.

Root cause analysis:
- Database tests pass because they use SQLAlchemy models (no wrapper)
- Pydantic tests pass because they don't access nested properties directly
- Production code fails because it creates Pydantic models and accesses attributes directly
"""

from typing import Any

from src.core.schemas import Product
from tests.helpers.adcp_factories import (
    create_test_cpm_pricing_option,
    create_test_format_id,
    create_test_publisher_properties_by_tag,
)


def test_pricing_option_rootmodel_unwrapping():
    """Test that pricing options in Product are correctly accessed.

    The adcp library wraps pricing options in PricingOption (a RootModel).
    The extraction code must unwrap via .root to access attributes.
    """
    product = Product(
        product_id="test",
        name="Test Product",
        description="Test description",
        format_ids=[create_test_format_id("banner")],
        delivery_type="guaranteed",
        delivery_measurement={"provider": "test_provider", "notes": "Test"},
        publisher_properties=[create_test_publisher_properties_by_tag()],
        pricing_options=[create_test_cpm_pricing_option(currency="USD", rate=10.0)],
    )

    po = product.pricing_options[0]

    # Should be wrapped in RootModel
    assert hasattr(po, "root"), "pricing_options[0] should have .root attribute (is RootModel)"

    # adcp 3.9: PricingOption RootModel now proxies attributes from .root,
    # so .pricing_model is accessible directly on the wrapper
    assert hasattr(po, "pricing_model"), "pricing_options wrapper should proxy .pricing_model from .root"

    # Direct access works (proxy behavior)
    assert po.pricing_model == "cpm"
    assert po.currency == "USD"
    assert po.rate == 10.0
    assert po.is_fixed is True

    # Access via .root should also still work
    assert po.root.pricing_model == "cpm"
    assert po.root.currency == "USD"
    assert po.root.rate == 10.0
    assert po.root.is_fixed is True


def test_pricing_option_unwrap_helper():
    """Test the unwrap pattern used in media_buy_create.py."""
    product = Product(
        product_id="test",
        name="Test Product",
        description="Test description",
        format_ids=[create_test_format_id("banner")],
        delivery_type="guaranteed",
        delivery_measurement={"provider": "test_provider", "notes": "Test"},
        publisher_properties=[create_test_publisher_properties_by_tag()],
        pricing_options=[create_test_cpm_pricing_option(currency="EUR", rate=15.0, is_fixed=True)],
    )

    # This is the pattern used in media_buy_create.py
    def unwrap_po(po: Any) -> Any:
        return getattr(po, "root", po)

    first_option = unwrap_po(product.pricing_options[0])

    # Now we can access attributes directly
    assert first_option.pricing_model == "cpm"
    assert first_option.currency == "EUR"
    assert first_option.rate == 15.0
    assert first_option.is_fixed is True


def test_legacy_pricing_option_id_generation():
    """Test the legacy pricing_option_id generation logic.

    This tests the exact code path at lines 1527-1531 of media_buy_create.py
    that was failing due to missing RootModel unwrapping.
    """
    product = Product(
        product_id="test",
        name="Test Product",
        description="Test description",
        format_ids=[create_test_format_id("banner")],
        delivery_type="guaranteed",
        delivery_measurement={"provider": "test_provider", "notes": "Test"},
        publisher_properties=[create_test_publisher_properties_by_tag()],
        pricing_options=[create_test_cpm_pricing_option(currency="USD", rate=10.0, is_fixed=True)],
    )

    # This is the FIXED logic from media_buy_create.py
    first_option = product.pricing_options[0]
    first_option = getattr(first_option, "root", first_option)  # Unwrap RootModel
    pricing_model = first_option.pricing_model.lower()
    currency = first_option.currency.lower()
    is_fixed = "fixed" if first_option.is_fixed else "auction"
    pricing_option_id = f"{pricing_model}_{currency}_{is_fixed}"

    assert pricing_option_id == "cpm_usd_fixed"


def test_legacy_pricing_option_id_auction():
    """Test legacy pricing_option_id for auction pricing."""
    product = Product(
        product_id="test",
        name="Test Product",
        description="Test description",
        format_ids=[create_test_format_id("banner")],
        delivery_type="non_guaranteed",
        delivery_measurement={"provider": "test_provider", "notes": "Test"},
        publisher_properties=[create_test_publisher_properties_by_tag()],
        pricing_options=[
            {
                "pricing_option_id": "cpm_eur_auction",
                "pricing_model": "cpm",
                "currency": "EUR",
                "is_fixed": False,
                "price_guidance": {"floor": 1.0, "p50": 5.0},
            }
        ],
    )

    # FIXED logic
    first_option = product.pricing_options[0]
    first_option = getattr(first_option, "root", first_option)
    pricing_model = first_option.pricing_model.lower()
    currency = first_option.currency.lower()
    is_fixed = "fixed" if first_option.is_fixed else "auction"
    pricing_option_id = f"{pricing_model}_{currency}_{is_fixed}"

    assert pricing_option_id == "cpm_eur_auction"
