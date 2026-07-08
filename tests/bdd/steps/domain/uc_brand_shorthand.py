"""Brand shorthand BDD steps for get_products and create_media_buy (#1324)."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from pytest_bdd import given, parsers, then, when

from tests.bdd.steps.domain.uc002_create_media_buy import _get_response_field
from tests.bdd.steps.generic._dispatch import dispatch_request


def _pricing_option_id(pricing_option) -> str:
    fixed_str = "fixed" if pricing_option.is_fixed else "auction"
    return f"{pricing_option.pricing_model}_{pricing_option.currency.lower()}_{fixed_str}"


def _parse_brand_param(brand: str) -> Any:
    try:
        return json.loads(brand)
    except json.JSONDecodeError:
        return brand


def _build_create_media_buy_brand_kwargs(ctx: dict, brand: Any) -> dict[str, Any]:
    product = ctx["default_product"]
    pricing_option = ctx["default_pricing_option"]
    now = datetime.now(UTC)
    return {
        "brand": brand,
        "po_number": f"PO-BRAND-{uuid.uuid4().hex[:8]}",
        "start_time": (now + timedelta(days=1)).isoformat(),
        "end_time": (now + timedelta(days=30)).isoformat(),
        "packages": [
            {
                "product_id": product.product_id,
                "budget": 5000.0,
                "pricing_option_id": _pricing_option_id(pricing_option),
            }
        ],
    }


@given("a tenant is configured for media buy creation")
def given_media_buy_creation_tenant(ctx: dict) -> None:
    """Harness seeds tenant/product/pricing; assert the fixture ran."""
    assert "default_product" in ctx, "MediaBuyCreateEnv must seed default_product"
    assert "default_pricing_option" in ctx, "MediaBuyCreateEnv must seed default_pricing_option"


@when(parsers.parse("the buyer sends create_media_buy with brand {brand}"))
def when_send_create_media_buy_with_brand(ctx: dict, brand: str) -> None:
    """Dispatch create_media_buy with a brand value (JSON dict or bare/quoted string)."""
    dispatch_request(ctx, **_build_create_media_buy_brand_kwargs(ctx, _parse_brand_param(brand)))


@then("the create_media_buy request succeeds")
def then_create_media_buy_succeeds(ctx: dict) -> None:
    """Assert create_media_buy completed and returned a media_buy_id."""
    assert "error" not in ctx, f"Request failed: {ctx.get('error')}"
    response = ctx.get("response")
    assert response is not None, "No response recorded"
    media_buy_id = _get_response_field(response, "media_buy_id")
    assert media_buy_id, f"Expected media_buy_id in response, got {response!r}"
