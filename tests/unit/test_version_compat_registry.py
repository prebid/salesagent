"""Tests for version compat transform registry.

Validates that:
- apply_version_compat() exists and applies model-level v2 compat
- get_products transform adds v2 fields from pricing option models
- Unknown tools serialize with standard model_dump
- V3+ clients get clean responses without compat fields
- Dict pass-through works for legacy callers

beads: salesagent-b61l.14
"""

from adcp import CpmPricingOption

from src.core.schemas import GetProductsResponse, Product
from tests.helpers.adcp_factories import (
    create_test_format_id,
    create_test_publisher_properties_by_tag,
)


def _make_response(fixed_price: float | None = None, floor_price: float | None = None) -> GetProductsResponse:
    """Build a GetProductsResponse with a single product and pricing option."""
    kwargs = {
        "pricing_option_id": "cpm_usd_test",
        "pricing_model": "cpm",
        "currency": "USD",
    }
    if fixed_price is not None:
        kwargs["fixed_price"] = fixed_price
    if floor_price is not None:
        kwargs["floor_price"] = floor_price
        kwargs["price_guidance"] = {"p50": floor_price * 2}

    cpm = CpmPricingOption(**kwargs)
    product = Product(
        product_id="p1",
        name="Test",
        description="Test",
        format_ids=[create_test_format_id("banner")],
        delivery_type="guaranteed",
        delivery_measurement={"provider": "test", "notes": "test"},
        publisher_properties=[create_test_publisher_properties_by_tag()],
        pricing_options=[cpm],
    )
    return GetProductsResponse(products=[product])


# ---------------------------------------------------------------------------
# Registry API Tests
# ---------------------------------------------------------------------------


class TestVersionCompatRegistry:
    """Verify the version compat registry exists and works."""

    def test_apply_version_compat_exists(self):
        """apply_version_compat function must exist."""
        from src.core.version_compat import apply_version_compat

        assert callable(apply_version_compat)

    def test_get_products_v2_adds_is_fixed_and_rate(self):
        """V2 clients get is_fixed=True and rate from fixed_price model attribute."""
        from src.core.version_compat import apply_version_compat

        response = _make_response(fixed_price=5.0)
        result = apply_version_compat("get_products", response, "2.0.0")
        po = result["products"][0]["pricing_options"][0]
        assert po["is_fixed"] is True
        assert po["rate"] == 5.0

    def test_get_products_v2_auction_pricing(self):
        """V2 clients get is_fixed=False and price_guidance.floor for auction pricing."""
        from src.core.version_compat import apply_version_compat

        response = _make_response(floor_price=2.0)
        result = apply_version_compat("get_products", response, "2.0.0")
        po = result["products"][0]["pricing_options"][0]
        assert po["is_fixed"] is False
        assert "rate" not in po
        assert po["price_guidance"]["floor"] == 2.0

    def test_v3_clients_skip_transform(self):
        """V3+ clients should get clean response without compat fields."""
        from src.core.version_compat import apply_version_compat

        response = _make_response(fixed_price=5.0)
        result = apply_version_compat("get_products", response, "3.0.0")
        po = result["products"][0]["pricing_options"][0]
        assert "is_fixed" not in po
        assert "rate" not in po

    def test_unknown_tool_passes_through_dict(self):
        """Dict responses pass through unchanged (legacy path)."""
        from src.core.version_compat import apply_version_compat

        response = {"data": "unchanged"}
        result = apply_version_compat("nonexistent_tool", response, "2.0.0")
        assert result == {"data": "unchanged"}

    def test_none_version_applies_compat(self):
        """None adcp_version should apply compat (safe default)."""
        from src.core.version_compat import apply_version_compat

        response = _make_response(fixed_price=3.0)
        result = apply_version_compat("get_products", response, None)
        po = result["products"][0]["pricing_options"][0]
        assert po["is_fixed"] is True
        assert po["rate"] == 3.0
