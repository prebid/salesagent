"""Brand shorthand BDD steps for get_products and create_media_buy (#1324)."""

from __future__ import annotations

import uuid

from pytest_bdd import given, parsers, then, when

from tests.bdd.steps.domain.uc002_create_media_buy import _get_response_field
from tests.bdd.steps.generic._brand_param import parse_brand_gherkin_param
from tests.bdd.steps.generic._dispatch import dispatch_request
from tests.bdd.steps.generic.given_media_buy import _ensure_request_defaults


@given("a tenant is configured for media buy creation")
def given_media_buy_creation_tenant(ctx: dict) -> None:
    """Harness seeds tenant/product/pricing; assert the fixture ran."""
    assert "default_product" in ctx, "MediaBuyCreateEnv must seed default_product"
    assert "default_pricing_option" in ctx, "MediaBuyCreateEnv must seed default_pricing_option"


@when(parsers.parse("the buyer sends create_media_buy with brand {brand}"))
def when_send_create_media_buy_with_brand(ctx: dict, brand: str) -> None:
    """Dispatch create_media_buy with a brand value (JSON dict or bare/quoted string)."""
    kwargs = _ensure_request_defaults(ctx)
    kwargs["brand"] = parse_brand_gherkin_param(brand)
    kwargs["po_number"] = f"PO-BRAND-{uuid.uuid4().hex[:8]}"
    dispatch_request(ctx, **kwargs)


@then("the create_media_buy request succeeds")
def then_create_media_buy_succeeds(ctx: dict) -> None:
    """Assert create_media_buy completed and returned a media_buy_id."""
    assert "error" not in ctx, f"Request failed: {ctx.get('error')}"
    response = ctx.get("response")
    assert response is not None, "No response recorded"
    media_buy_id = _get_response_field(response, "media_buy_id")
    assert media_buy_id, f"Expected media_buy_id in response, got {response!r}"
