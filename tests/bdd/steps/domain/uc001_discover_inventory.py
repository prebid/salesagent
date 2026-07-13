"""Steps for UC-001 discover available inventory (get_products) — salesagent-8wf2.

Wired scenarios dispatch a real get_products through the parametrized wire
transport (a2a/mcp/rest) via ProductEnv. The catalog is seeded by the UC-001
conftest branch (three products: guaranteed/US, non_guaranteed/GB, and one
restricted to another principal) so visibility and filter assertions are
non-vacuous. Success-path asserts read the REAL serialized wire body via
``wire_field`` where the transport stashes it (REST always; A2A/MCP via the
client/handler dispatchers), so the v3.1.1 format_id object contract on the
products wire is graded here (r50r follow-up landed with 8wf2).
"""

from __future__ import annotations

import json
from typing import Any

from pytest_bdd import given, parsers, then, when

from tests.bdd.steps._outcome_helpers import wire_field
from tests.bdd.steps.generic._dispatch import dispatch_request


def _datatable_to_kwargs(datatable: list) -> dict[str, Any]:
    """Convert a two-column field/value datatable to request kwargs.

    JSON-looking values (objects, arrays) are parsed; everything else stays a
    string. ``buying_mode`` is dropped: production defaults it to brief
    (BR-RULE-079 pre-v3 fallback) and GetProductsBody does not declare it yet
    (spec-gap tracked in salesagent-ilbv) — sending it would trip the Pattern
    #7 dev-forbid gate on REST rather than exercise discovery.
    """
    headers = [str(h).strip() for h in datatable[0]]
    field_idx, value_idx = headers.index("field"), headers.index("value")
    kwargs: dict[str, Any] = {}
    for row in datatable[1:]:
        field = str(row[field_idx]).strip()
        raw = str(row[value_idx]).strip()
        if field == "buying_mode":
            continue
        if raw.startswith(("{", "[")):
            kwargs[field] = json.loads(raw)
        else:
            kwargs[field] = raw
    return kwargs


# ── Given steps ─────────────────────────────────────────────────────


@given("a tenant exists with at least one product in the catalog")
def given_tenant_with_catalog(ctx: dict) -> None:
    """Background step: the UC-001 conftest branch seeded tenant + catalog."""
    assert ctx.get("tenant") is not None, "harness wiring did not populate ctx['tenant']"
    assert ctx.get("seeded_products"), "UC-001 conftest branch did not seed the catalog"


@given(parsers.parse('the tenant brand_manifest_policy is "{policy}"'))
def given_brand_manifest_policy(ctx: dict, policy: str) -> None:
    """Set the tenant's brand manifest policy column (products.py:194 reads it)."""
    env = ctx["env"]
    tenant = ctx["tenant"]
    tenant.brand_manifest_policy = policy
    env.get_session().commit()


@given("the tenant has an advertising_policy configured")
def given_advertising_policy(ctx: dict) -> None:
    """Give the tenant a non-empty advertising policy."""
    env = ctx["env"]
    tenant = ctx["tenant"]
    tenant.advertising_policy = {"prohibited_categories": ["weapons"]}
    env.get_session().commit()


@given(
    "the product catalog contains products with valid schema "
    "(format_ids, publisher_properties, pricing_options, reporting_capabilities)"
)
def given_valid_catalog(ctx: dict) -> None:
    """The conftest UC-001 branch seeded the catalog; assert it is present."""
    assert ctx.get("seeded_products"), "UC-001 conftest branch did not seed the catalog"


@given("no products match the specified filters and brief")
def given_no_matching_products(ctx: dict) -> None:
    """Constrain the request so the seeded catalog matches nothing.

    The catalog rows exist (seeded by the fixture); 'no products match the
    SPECIFIED filters' is made literally true by specifying a country filter
    no seeded product serves.
    """
    ctx.setdefault("request_preset", {})["filters"] = {"countries": ["ZZ"]}


# ── When step ───────────────────────────────────────────────────────


@when("the Buyer Agent sends a get_products request with:")
def when_send_get_products(ctx: dict, datatable: list) -> None:
    """Dispatch get_products with the table fields through the wire transport."""
    kwargs = {**_datatable_to_kwargs(datatable), **ctx.get("request_preset", {})}
    if "dispatch_identity" in ctx:
        dispatch_request(ctx, identity=ctx["dispatch_identity"], **kwargs)
    else:
        dispatch_request(ctx, **kwargs)


# ── Then steps ──────────────────────────────────────────────────────


def _wire_products(ctx: dict) -> list[dict[str, Any]]:
    """The products array as the buyer sees it on the serialized wire."""
    return wire_field(ctx, "products")


# 'the response status should be "completed"' is served by the GENERIC step
# (steps/generic/then_success.py) — a domain copy here would shadow it for
# every use case (it did: uc003 regression). products is in
# _STATUSLESS_SUCCESS_ATTRS so the generic step proves UC-001 completion.


@then('the response should contain "products" array')
def then_contains_products_array(ctx: dict) -> None:
    products = _wire_products(ctx)
    assert isinstance(products, list), f"products is not an array on the wire: {type(products).__name__}"
    assert products, "products array is empty — seeded catalog should be visible"
    # v3.1.1 core/format-id.json: every format_id on the products wire is an
    # object carrying agent_url + id, never a bare string (federation contract).
    from tests.helpers.format_assertions import assert_wire_format_id_is_object

    for product in products:
        for fid in product.get("format_ids", []):
            assert_wire_format_id_is_object(fid)


@then('the response "products" array should be empty')
def then_products_array_empty(ctx: dict) -> None:
    products = _wire_products(ctx)
    assert products == [], f"expected empty products array, got {len(products)} entries"


@then("every product should have pricing_options as an empty array")
def then_pricing_suppressed(ctx: dict) -> None:
    """Anonymous requests get pricing suppressed — only meaningful non-vacuously."""
    products = _wire_products(ctx)
    assert products, "no products returned — pricing suppression is unobservable on an empty catalog"
    for product in products:
        assert product.get("pricing_options") == [], (
            f"pricing_options not suppressed for anonymous buyer on {product.get('product_id')!r}: "
            f"{product.get('pricing_options')!r}"
        )


@then("no products with allowed_principal_ids restrictions should be visible")
def then_restricted_products_hidden(ctx: dict) -> None:
    products = _wire_products(ctx)
    restricted_ids = ctx.get("restricted_product_ids", set())
    assert restricted_ids, "fixture seeded no restricted product — assertion would be vacuous"
    visible = {p.get("product_id") for p in products}
    leaked = visible & restricted_ids
    assert not leaked, f"restricted products visible to anonymous buyer: {leaked}"


@then(parsers.parse('every product should match the delivery_type "{expected}"'))
def then_products_match_delivery_type(ctx: dict, expected: str) -> None:
    products = _wire_products(ctx)
    assert products, "no products returned — delivery_type filter is unobservable on an empty result"
    for product in products:
        assert product.get("delivery_type") == expected, (
            f"{product.get('product_id')!r} has delivery_type {product.get('delivery_type')!r}, expected {expected!r}"
        )


@then(parsers.parse("every product should have countries overlapping with {countries}"))
def then_products_match_countries(ctx: dict, countries: str) -> None:
    """Country filtering is observable by product identity, not a wire field.

    ``Product.countries`` is an internal filter field (``exclude=True`` in the
    schema — never serialized), so the filter's effect is asserted against the
    seeded catalog: only products whose DB row serves an overlapping country
    may come back, and at least one must (the catalog seeds a matching one).
    """
    requested = set(json.loads(countries))
    products = _wire_products(ctx)
    assert products, "no products returned — country filter is unobservable on an empty result"
    matching_ids = {p.product_id for p in ctx["seeded_products"] if set(p.countries or []) & requested}
    returned_ids = {p.get("product_id") for p in products}
    off_target = returned_ids - matching_ids
    assert not off_target, (
        f"products not serving {sorted(requested)} leaked through the country filter: {sorted(off_target)}"
    )


@then(
    "each product should have product_id, name, format_ids, publisher_properties, "
    "pricing_options, and reporting_capabilities"
)
def then_products_have_required_fields(ctx: dict) -> None:
    products = _wire_products(ctx)
    assert products, "no products returned"
    required = ("product_id", "name", "format_ids", "publisher_properties", "pricing_options", "reporting_capabilities")
    for product in products:
        for field in required:
            assert product.get(field) is not None, f"{product.get('product_id')!r} missing required field {field!r}"


@then("the products should be ordered by relevance_score descending")
def then_products_ordered_by_relevance(ctx: dict) -> None:
    """Production gap: relevance_score is not emitted on the products wire.

    The impl sorts internally when AI ranking is enabled but never serializes
    a relevance_score field — this hard assert keeps T-UC-001-main strict-xfail
    until production emits it (see conftest _UC001_XFAIL_TAGS).
    """
    products = _wire_products(ctx)
    scores = [p.get("relevance_score") for p in products]
    assert all(s is not None for s in scores), f"relevance_score missing on the wire: {scores}"
    assert scores == sorted(scores, reverse=True), f"products not ordered by relevance_score desc: {scores}"


@then("each product should include brief_relevance explanation")
def then_products_have_brief_relevance(ctx: dict) -> None:
    """Production gap twin of the relevance ordering assert (strict-xfail)."""
    products = _wire_products(ctx)
    assert products, "no products returned"
    for product in products:
        assert product.get("brief_relevance"), (
            f"{product.get('product_id')!r} has no brief_relevance explanation on the wire"
        )
