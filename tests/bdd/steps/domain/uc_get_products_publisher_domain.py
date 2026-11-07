"""Steps for BR-UC-GET-PRODUCTS-publisher-domain: which publishers a product names (#1845).

Setup stores REAL ``AuthorizedProperty`` and ``Product`` rows through their factories, and
dispatch goes through the shared ``dispatch_request`` (via the inventory-profile module's
``When the buyer requests products``), so the same rows are read on every transport.

Assertions read the serialized wire body: ``publisher_properties`` is compared whole, so a
selector naming any other domain -- the seller's own agent host included -- fails it.
"""

from __future__ import annotations

import json

from pytest_bdd import given, parsers, then

from tests.bdd.steps._outcome_helpers import wire_entry, wire_field
from tests.bdd.steps.generic._table import comma_list
from tests.factories import AuthorizedPropertyFactory, InventoryProfileFactory, PricingOptionFactory, ProductFactory


def _offer(ctx: dict, product_id: str, selectors: list[dict]) -> None:
    """Store a priced product selling the publishers *selectors* name."""
    product = ProductFactory(tenant=ctx["tenant"], product_id=product_id, properties=selectors)
    PricingOptionFactory(product=product)


# ── Given steps ─────────────────────────────────────────────────────


@given(parsers.parse('the seller is authorized for property "{property_id}" of publisher "{domain}" tagged "{tag}"'))
def given_authorized_property(ctx: dict, property_id: str, domain: str, tag: str) -> None:
    AuthorizedPropertyFactory(tenant=ctx["tenant"], property_id=property_id, publisher_domain=domain, tags=[tag])


@given(
    parsers.parse('the seller\'s property "{property_id}" of publisher "{domain}" tagged "{tag}" awaits verification')
)
def given_pending_property(ctx: dict, property_id: str, domain: str, tag: str) -> None:
    """A property as the add form and the upload store it: its publisher not yet seen to authorize us."""
    AuthorizedPropertyFactory(
        tenant=ctx["tenant"],
        property_id=property_id,
        publisher_domain=domain,
        tags=[tag],
        verification_status="pending",
    )


@given(parsers.parse('the seller offers product "{product_id}" migrated from legacy property tags "{tags}"'))
def given_product_migrated_from_tags(ctx: dict, product_id: str, tags: str) -> None:
    """A product the legacy-selection migration converted from its ``property_tags`` column."""
    _offer(ctx, product_id, ctx["env"].migrated_selection(property_tags=comma_list(tags)))


@given(parsers.parse('the seller offers product "{product_id}" migrated from legacy property ids "{property_ids}"'))
def given_product_migrated_from_ids(ctx: dict, product_id: str, property_ids: str) -> None:
    """A product the legacy-selection migration converted from its ``property_ids`` column."""
    _offer(ctx, product_id, ctx["env"].migrated_selection(property_ids=comma_list(property_ids)))


@given(parsers.parse('the seller offers product "{product_id}" migrated from a legacy selection of no properties'))
def given_product_migrated_from_nothing(ctx: dict, product_id: str) -> None:
    """A product the migration converted from an empty ``property_tags``, which selected every publisher."""
    _offer(ctx, product_id, ctx["env"].migrated_selection(property_tags=[]))


@given(parsers.parse('the tenant also holds product "{product_id}" that no verified property backs'))
def given_unoffered_product(ctx: dict, product_id: str) -> None:
    # A publisher the tenant holds no property of, so ProductFactory seeds none for it.
    _offer(ctx, product_id, [{"publisher_domain": "unbacked-publisher.example", "selection_type": "all"}])


@given(parsers.parse('the seller offers product "{product_id}" naming publisher_properties {selectors}'))
def given_product_naming_selectors(ctx: dict, product_id: str, selectors: str) -> None:
    """A product storing explicit selectors, the shape the admin product form writes."""
    product = ProductFactory(tenant=ctx["tenant"], product_id=product_id, properties=json.loads(selectors))
    PricingOptionFactory(product=product)


@given("an inventory profile the old form saved naming the seller's own host")
def given_profile_naming_agent_host(ctx: dict) -> None:
    """The row the profile form's removed "Use Default" button wrote: the agent host as a publisher."""
    tenant = ctx["tenant"]
    ctx["profile"] = InventoryProfileFactory(
        tenant=tenant,
        publisher_properties=[
            {
                "publisher_domain": tenant.virtual_host_name,
                "property_tags": ["all_inventory"],
                "selection_type": "by_tag",
            }
        ],
    )


@given(parsers.parse('the operator saves inventory profile "{profile_id}" selecting properties "{property_ids}"'))
def given_operator_saved_profile(ctx: dict, profile_id: str, property_ids: str) -> None:
    """Save the profile through the real admin form, and hold the row it stored."""
    env = ctx["env"]
    page = env.create_inventory_profile(
        profile_id, property_mode="property_ids", selected_property_ids=comma_list(property_ids)
    )
    profile = env.stored_inventory_profile(profile_id)
    assert profile is not None, f"the form stored no profile {profile_id!r}; it answered {page.status_code}"
    ctx["profile"] = profile


# ── Then steps ──────────────────────────────────────────────────────


@then(parsers.parse('product "{product_id}" announces publisher_properties {expected}'))
def then_product_announces(ctx: dict, product_id: str, expected: str) -> None:
    announced = wire_entry(ctx, "products", product_id=product_id)["publisher_properties"]
    assert announced == json.loads(expected), f"product {product_id!r} announced {announced!r}"


@then(parsers.parse('the buyer receives exactly the products "{product_ids}"'))
def then_exact_products(ctx: dict, product_ids: str) -> None:
    received = sorted(product["product_id"] for product in wire_field(ctx, "products"))
    assert received == sorted(comma_list(product_ids)), f"received products {received!r}"
