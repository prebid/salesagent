"""Steps for the wired UC-001 main get_products scenario."""

from __future__ import annotations

import json
from typing import Any

from pytest_bdd import given, parsers, then, when

from tests.bdd.steps._outcome_helpers import _require_response, wire_field
from tests.bdd.steps.generic._dispatch import dispatch_request


def _datatable_to_kwargs(datatable: list) -> dict[str, Any]:
    """Convert a two-column field/value datatable to request kwargs."""
    headers = [str(header).strip() for header in datatable[0]]
    field_index, value_index = headers.index("field"), headers.index("value")
    kwargs: dict[str, Any] = {}
    for row in datatable[1:]:
        field = str(row[field_index]).strip()
        raw = str(row[value_index]).strip()
        if field == "buying_mode":
            continue
        kwargs[field] = json.loads(raw) if raw.startswith(("{", "[")) else raw
    return kwargs


def _wire_products(ctx: dict) -> list[dict[str, Any]]:
    """Return products exactly as serialized for the buyer."""
    return wire_field(ctx, "products")


@given("a tenant exists with at least one product in the catalog")
def given_tenant_with_catalog(ctx: dict) -> None:
    """Assert that the UC-001 harness seeded the catalog."""
    assert ctx["ranked_product_ids"], "UC-001 harness did not seed ranked products"


@given(parsers.parse('the tenant brand_manifest_policy is "{policy}"'))
def given_brand_manifest_policy(ctx: dict, policy: str) -> None:
    """Configure the tenant's product-discovery authentication policy."""
    ctx["tenant"].brand_manifest_policy = policy
    ctx["env"]._commit_factory_data()


@given("the tenant has an advertising_policy configured")
def given_advertising_policy(ctx: dict) -> None:
    """Configure a valid, disabled-by-default advertising policy."""
    ctx["tenant"].advertising_policy = {"prohibited_categories": ["weapons"]}
    ctx["env"]._commit_factory_data()


@given(
    "the product catalog contains products with valid schema "
    "(format_ids, publisher_properties, pricing_options, reporting_capabilities)"
)
def given_valid_catalog(ctx: dict) -> None:
    """Assert that the scenario has a non-vacuous product catalog."""
    assert len(ctx["ranked_product_ids"]) == 2


@when("the Buyer Agent sends a get_products request with:")
def when_send_get_products(ctx: dict, datatable: list) -> None:
    """Dispatch get_products through the parametrized wire transport."""
    dispatch_request(ctx, **_datatable_to_kwargs(datatable))


@then(parsers.parse('the response status should be "completed"'))
def then_status_completed(ctx: dict) -> None:
    """Assert synchronous product discovery completed successfully."""
    assert ctx.get("error") is None, f"Request failed: {ctx.get('error')!r}"
    assert _require_response(ctx).products is not None


@then('the response should contain "products" array')
def then_contains_products_array(ctx: dict) -> None:
    """Assert products are present on the serialized response."""
    products = _wire_products(ctx)
    assert isinstance(products, list)
    assert products, "products array is empty — ranking assertions would be vacuous"


@then(
    "each product should have product_id, name, format_ids, publisher_properties, "
    "pricing_options, and reporting_capabilities"
)
def then_products_have_required_fields(ctx: dict) -> None:
    """Assert the core product fields are present on the wire."""
    required = (
        "product_id",
        "name",
        "format_ids",
        "publisher_properties",
        "pricing_options",
        "reporting_capabilities",
    )
    for product in _wire_products(ctx):
        for field in required:
            assert product.get(field) is not None, f"{product.get('product_id')!r} missing {field!r}"


@then("the products should be ordered by relevance_score descending")
def then_products_ordered_by_relevance(ctx: dict) -> None:
    """Assert wire order follows the ranker's descending internal scores."""
    actual = [product["product_id"] for product in _wire_products(ctx)]
    assert actual == ctx["ranked_product_ids"], f"unexpected relevance order: {actual}"


@then("each product should include brief_relevance explanation")
def then_products_have_brief_relevance(ctx: dict) -> None:
    """Assert each ranked product exposes its ranker's explanation."""
    for product in _wire_products(ctx):
        expected = ctx["ranking_reasons"][product["product_id"]]
        assert product.get("brief_relevance") == expected
