"""Domain step definitions for product discovery with inventory profiles (#1162).

Given steps: tenant setup, inventory profile creation, product linking
When steps: get_products dispatch via call_via (all 4 transports)
Then steps: publisher_properties assertions (selection_type, field presence)

Steps store results in ctx:
    ctx["response"] — GetProductsResponse on success
    ctx["error"] — Exception on failure
"""

from __future__ import annotations

from typing import Any

from pytest_bdd import given, parsers, then, when

from tests.factories import (
    InventoryProfileFactory,
    PricingOptionFactory,
    ProductFactory,
    TenantFactory,
)

# ── Helpers ─────────────────────────────────────────────────────────


def _call_get_products(ctx: dict, **kwargs: Any) -> None:
    """Dispatch get_products through ctx['transport'] via call_via."""
    transport = ctx.get("transport")
    env = ctx["env"]
    kwargs.setdefault("brief", "inventory profile test")
    if transport is not None:
        try:
            result = env.call_via(transport, **kwargs)
            if result.is_error:
                ctx["error"] = result.error
            else:
                ctx["response"] = result.payload
        except Exception as exc:
            ctx["error"] = exc
    else:
        try:
            ctx["response"] = env.call_impl(**kwargs)
        except Exception as exc:
            ctx["error"] = exc


def _get_first_prop(ctx: dict) -> Any:
    """Get the inner model of the first publisher_properties entry."""
    product = ctx["first_product"]
    pp = product.publisher_properties
    assert pp is not None, "publisher_properties is None"
    assert len(pp) >= 1, "publisher_properties is empty"
    inner = pp[0]
    return inner.root if hasattr(inner, "root") else inner


# ── Given steps ─────────────────────────────────────────────────────


@given("a tenant is configured for product discovery")
def given_tenant(ctx: dict) -> None:
    """Create a tenant with required config for get_products."""
    tenant = TenantFactory(
        tenant_id="test_tenant",
        subdomain="test_tenant",
        ad_server="mock",
    )
    ctx["tenant"] = tenant


@given(parsers.parse('an inventory profile with property_ids "{ids}" for domain "{domain}"'))
def given_profile_by_id(ctx: dict, ids: str, domain: str) -> None:
    """Create profile with property_ids (no selection_type — triggers inference)."""
    tenant = ctx["tenant"]
    ctx["profile"] = InventoryProfileFactory(
        tenant=tenant,
        publisher_properties=[{"publisher_domain": domain, "property_ids": [ids]}],
    )


@given(parsers.parse('an inventory profile with property_tags "{tags}" for domain "{domain}"'))
def given_profile_by_tag(ctx: dict, tags: str, domain: str) -> None:
    """Create profile with property_tags (no selection_type — triggers inference)."""
    tenant = ctx["tenant"]
    ctx["profile"] = InventoryProfileFactory(
        tenant=tenant,
        publisher_properties=[{"publisher_domain": domain, "property_tags": [tags]}],
    )


@given(parsers.parse('an inventory profile with only domain "{domain}"'))
def given_profile_domain_only(ctx: dict, domain: str) -> None:
    """Create profile with only publisher_domain (no IDs, tags, or selection_type)."""
    tenant = ctx["tenant"]
    ctx["profile"] = InventoryProfileFactory(
        tenant=tenant,
        publisher_properties=[{"publisher_domain": domain}],
    )


@given(
    parsers.parse('an inventory profile with property_tags "{tags}" for domain "{domain}" and selection_type "{st}"')
)
def given_profile_with_selection_type(ctx: dict, tags: str, domain: str, st: str) -> None:
    """Create profile with selection_type already present (passthrough test)."""
    tenant = ctx["tenant"]
    ctx["profile"] = InventoryProfileFactory(
        tenant=tenant,
        publisher_properties=[{"publisher_domain": domain, "property_tags": [tags], "selection_type": st}],
    )


@given(parsers.parse('an inventory profile with property_ids "{ids}" for domain "{domain}" and legacy fields'))
def given_profile_legacy(ctx: dict, ids: str, domain: str) -> None:
    """Create profile with property_ids plus legacy extra fields that should be stripped."""
    tenant = ctx["tenant"]
    ctx["profile"] = InventoryProfileFactory(
        tenant=tenant,
        publisher_properties=[
            {
                "publisher_domain": domain,
                "property_ids": [ids],
                "property_name": "Legacy Name",
                "property_type": "website",
                "identifiers": ["old_id"],
            }
        ],
    )


@given("a product linked to that inventory profile with pricing")
def given_product_with_profile(ctx: dict) -> None:
    """Create a product referencing the inventory profile, with pricing."""
    tenant = ctx["tenant"]
    profile = ctx["profile"]
    product = ProductFactory(tenant=tenant, inventory_profile_id=profile.id)
    PricingOptionFactory(product=product)
    ctx["product"] = product


# ── When steps ──────────────────────────────────────────────────────


@when("the buyer requests products")
def when_request_products(ctx: dict) -> None:
    """Dispatch get_products through the current transport."""
    _call_get_products(ctx)


# ── Then steps ──────────────────────────────────────────────────────


@then("the response contains at least one product")
def then_has_products(ctx: dict) -> None:
    """Assert the response has at least one product."""
    assert "error" not in ctx, f"Request failed: {ctx.get('error')}"
    response = ctx["response"]
    assert response.products is not None, "Response has no products"
    assert len(response.products) >= 1, f"Expected >= 1 product, got {len(response.products)}"
    ctx["first_product"] = response.products[0]


@then(parsers.parse('the first product publisher_properties selection_type is "{expected}"'))
def then_selection_type(ctx: dict, expected: str) -> None:
    """Assert publisher_properties[0] has the expected selection_type."""
    inner = _get_first_prop(ctx)
    actual = getattr(inner, "selection_type", None) or (
        inner.get("selection_type") if isinstance(inner, dict) else None
    )
    assert actual == expected, f"Expected selection_type={expected!r}, got {actual!r}"


@then(parsers.parse('the first product publisher_properties property_ids contains "{expected}"'))
def then_has_property_ids(ctx: dict, expected: str) -> None:
    """Assert property_ids contains the expected value."""
    inner = _get_first_prop(ctx)
    ids = getattr(inner, "property_ids", None) or (inner.get("property_ids") if isinstance(inner, dict) else None)
    assert ids is not None, "property_ids is None"
    id_strings = [str(pid.root) if hasattr(pid, "root") else str(pid) for pid in ids]
    assert expected in id_strings, f"Expected {expected!r} in property_ids, got {id_strings}"


@then(parsers.parse('the first product publisher_properties property_tags contains "{expected}"'))
def then_has_property_tags(ctx: dict, expected: str) -> None:
    """Assert property_tags contains the expected value."""
    inner = _get_first_prop(ctx)
    tags = getattr(inner, "property_tags", None) or (inner.get("property_tags") if isinstance(inner, dict) else None)
    assert tags is not None, "property_tags is None"
    tag_strings = [str(t.root) if hasattr(t, "root") else str(t) for t in tags]
    assert expected in tag_strings, f"Expected {expected!r} in property_tags, got {tag_strings}"


@then(parsers.parse('the first product publisher_properties does not have field "{field}"'))
def then_no_field(ctx: dict, field: str) -> None:
    """Assert publisher_properties[0] does not contain the given field."""
    inner = _get_first_prop(ctx)
    if isinstance(inner, dict):
        assert field not in inner, f"Field {field!r} should not be present, got {inner}"
    else:
        assert not hasattr(inner, field), f"Field {field!r} should not be present on {type(inner).__name__}"
