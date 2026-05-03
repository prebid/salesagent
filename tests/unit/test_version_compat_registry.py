"""Tests for ``add_get_products_v2_compat`` (dict-in/dict-out v2 backward-compat).

Validates the contract:
- Pre-3.0 clients get ``is_fixed`` / ``rate`` / ``price_guidance.floor`` keys
  added to every pricing option, derived from the v3 fields already present.
- V3+ clients get the dict back unchanged.
- ``adcp_version=None`` is treated as pre-3.0 (safe default).
- Auction options (``floor_price`` only) get ``is_fixed=False`` and the floor
  copied into ``price_guidance.floor``; ``rate`` is NOT added.
- Responses without a ``products`` key (e.g., error envelopes) pass through.

Historical note: previous tests passed a typed ``GetProductsResponse`` to
``apply_version_compat(tool_name, response, version)``. That contract was
silently broken for months because every transport caller pre-serialized
to dict before invoking the function — see PR #1081's squash and issue
#1246. The current dict-in contract matches what the four production
callers actually do.
"""

from __future__ import annotations

from adcp.types.generated_poc.pricing_options.cpm_option import CpmPricingOption

from src.core.schemas import GetProductsResponse, Product
from src.core.version_compat import add_get_products_v2_compat
from tests.helpers.adcp_factories import (
    create_test_format_id,
    create_test_publisher_properties_by_tag,
)


def _make_response_dict(fixed_price: float | None = None, floor_price: float | None = None) -> dict:
    """Build a serialized response dict matching what production transports pass in.

    Mirrors the model_dump(mode="json") output of GetProductsResponse — i.e.,
    the adcp library's PricingOption(RootModel) wrapper has been flattened so
    v3 fields sit at the top level of each pricing-option entry.
    """
    kwargs: dict = {
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
    return GetProductsResponse(products=[product]).model_dump(mode="json")


class TestAddGetProductsV2Compat:
    def test_v2_client_gets_is_fixed_and_rate_from_fixed_price(self) -> None:
        response = _make_response_dict(fixed_price=5.0)
        result = add_get_products_v2_compat(response, "2.0.0")
        po = result["products"][0]["pricing_options"][0]
        assert po["is_fixed"] is True
        assert po["rate"] == 5.0
        # v3 fields are still present alongside (additive transform).
        assert po["fixed_price"] == 5.0

    def test_v2_client_auction_pricing_gets_floor_in_price_guidance(self) -> None:
        response = _make_response_dict(floor_price=2.0)
        result = add_get_products_v2_compat(response, "2.0.0")
        po = result["products"][0]["pricing_options"][0]
        assert po["is_fixed"] is False
        assert "rate" not in po
        assert po["price_guidance"]["floor"] == 2.0
        assert po["floor_price"] == 2.0

    def test_v3_client_gets_clean_response_unchanged(self) -> None:
        response = _make_response_dict(fixed_price=5.0)
        before = response["products"][0]["pricing_options"][0].copy()
        result = add_get_products_v2_compat(response, "3.0.0")
        po = result["products"][0]["pricing_options"][0]
        # No v2 keys added.
        assert "is_fixed" not in po
        assert "rate" not in po
        # Original v3 fields preserved.
        assert po == before

    def test_none_version_applies_compat_safe_default(self) -> None:
        response = _make_response_dict(fixed_price=3.0)
        result = add_get_products_v2_compat(response, None)
        po = result["products"][0]["pricing_options"][0]
        assert po["is_fixed"] is True
        assert po["rate"] == 3.0

    def test_response_without_products_passes_through(self) -> None:
        """Response dicts without a ``products`` key are untouched."""
        response = {"data": "unchanged", "errors": ["something"]}
        result = add_get_products_v2_compat(response, "2.0.0")
        assert result == {"data": "unchanged", "errors": ["something"]}

    def test_a2a_envelope_keys_preserved(self) -> None:
        """A2A wrappers add ``message`` and ``success`` to the dict before calling.

        These envelope keys must survive the transform untouched.
        """
        response = _make_response_dict(fixed_price=5.0)
        response["message"] = "Found 1 product..."
        response["success"] = True
        result = add_get_products_v2_compat(response, "2.0.0")
        assert result["message"] == "Found 1 product..."
        assert result["success"] is True
        assert result["products"][0]["pricing_options"][0]["is_fixed"] is True

    def test_returns_same_dict_object(self) -> None:
        """The transform mutates in place and returns the same object (caller ergonomics)."""
        response = _make_response_dict(fixed_price=5.0)
        result = add_get_products_v2_compat(response, "2.0.0")
        assert result is response

    def test_handles_explicit_none_price_guidance_defensively(self) -> None:
        """A user-constructed dict with explicit ``price_guidance: None`` doesn't crash.

        Production callers pass ``model_dump(mode="json")`` output, which omits
        None-valued optional fields entirely — so this shape isn't currently
        produced. But the function's contract is "operate on any v3-shaped
        dict", so we coalesce explicitly rather than assuming model_dump's
        omission semantics.
        """
        response = {
            "products": [
                {
                    "pricing_options": [
                        {
                            "pricing_option_id": "x",
                            "pricing_model": "cpm",
                            "currency": "USD",
                            "floor_price": 5.0,
                            "price_guidance": None,
                        }
                    ]
                }
            ]
        }
        result = add_get_products_v2_compat(response, "2.0.0")
        po = result["products"][0]["pricing_options"][0]
        assert po["price_guidance"] == {"floor": 5.0}
        assert po["is_fixed"] is False

    def test_multiple_products_each_get_v2_keys(self) -> None:
        """Every product in the list is walked, not just the first."""
        response = _make_response_dict(fixed_price=5.0)
        # Duplicate the product to simulate a multi-product response.
        original_product = response["products"][0]
        response["products"].append({**original_product, "product_id": "p2"})
        # Refresh the duplicated nested pricing_options to a separate dict.
        response["products"][1]["pricing_options"] = [
            {**original_product["pricing_options"][0], "pricing_option_id": "cpm_usd_test_2"}
        ]
        result = add_get_products_v2_compat(response, "2.0.0")
        for product in result["products"]:
            assert product["pricing_options"][0]["is_fixed"] is True
            assert product["pricing_options"][0]["rate"] == 5.0
