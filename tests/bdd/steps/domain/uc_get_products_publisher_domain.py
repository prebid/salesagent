"""Steps for BR-UC-GET-PRODUCTS-publisher-domain: which publishers a product names (#1845).

A property is authorized only by a publisher's adagents.json: each Given writes what the
publisher's file says and the seller ingests it (``PublisherAdagentsMixin``), then stores
REAL ``Product`` rows through the factory. Dispatch goes through the shared
``dispatch_request`` (via the inventory-profile module's ``When the buyer requests
products``), so the same rows are read on every transport.

Assertions read the serialized wire body: ``publisher_properties`` and ``format_options`` are
compared whole, so a selector naming any other domain -- the seller's own agent host
included -- fails it.
"""

from __future__ import annotations

import json

from pytest_bdd import given, parsers, then

from tests.bdd.steps._outcome_helpers import wire_dict, wire_entry, wire_field
from tests.bdd.steps.generic._table import comma_list
from tests.factories import InventoryProfileFactory, PricingOptionFactory, ProductFactory


def _offer(ctx: dict, product_id: str, selectors: list[dict]) -> None:
    """Store a priced product selling the publishers *selectors* name."""
    product = ProductFactory(tenant=ctx["tenant"], product_id=product_id, properties=selectors)
    PricingOptionFactory(product=product)


# ── Given steps ─────────────────────────────────────────────────────


@given(parsers.parse('the seller is authorized for property "{property_id}" of publisher "{domain}" tagged "{tag}"'))
def given_authorized_property(ctx: dict, property_id: str, domain: str, tag: str) -> None:
    """The publisher's file lists the property and authorizes this agent for it; the seller ingests it."""
    env = ctx["env"]
    env.list_property(domain, property_id, tags=[tag])
    env.authorize(domain, ctx["tenant"].agent_url, authorization_type="property_ids", property_ids=[property_id])
    env.ingest_publisher(domain)


@given(
    parsers.parse(
        'the publisher "{domain}" lists property "{property_id}" tagged "{tag}" without authorizing the seller for it'
    )
)
def given_unauthorized_property(ctx: dict, property_id: str, domain: str, tag: str) -> None:
    """The publisher's file lists the property and no entry for this agent names it; the seller ingests it."""
    env = ctx["env"]
    env.list_property(domain, property_id, tags=[tag])
    env.ingest_publisher(domain)


@given(
    parsers.parse(
        'the seller offers product "{product_id}" selling publisher "{domain}" whole with format option "{option_id}" '
        'of "{option_domain}"'
    )
)
def given_product_with_publisher_option(
    ctx: dict, product_id: str, domain: str, option_id: str, option_domain: str
) -> None:
    _offer_with_option(ctx, product_id, {"publisher_domain": domain, "selection_type": "all"}, option_id, option_domain)


@given(
    parsers.parse(
        'the seller offers product "{product_id}" selling property "{property_id}" of publisher "{domain}" '
        'with format option "{option_id}" of "{option_domain}"'
    )
)
def given_product_of_property_with_publisher_option(
    ctx: dict, product_id: str, property_id: str, domain: str, option_id: str, option_domain: str
) -> None:
    selector = {"publisher_domain": domain, "property_ids": [property_id], "selection_type": "by_id"}
    _offer_with_option(ctx, product_id, selector, option_id, option_domain)


@given(
    parsers.parse(
        'the seller offers product "{product_id}" carrying format option "{option_id}" of "{option_domain}" '
        "and naming publisher_properties {selectors}"
    )
)
def given_product_selling_selectors_with_publisher_option(
    ctx: dict, product_id: str, option_id: str, option_domain: str, selectors: str
) -> None:
    _offer_with_options(ctx, product_id, json.loads(selectors), option_id, option_domain)


def _offer_with_option(ctx: dict, product_id: str, selector: dict, option_id: str, option_domain: str) -> None:
    _offer_with_options(ctx, product_id, [selector], option_id, option_domain)


def _offer_with_options(ctx: dict, product_id: str, selectors: list[dict], option_id: str, option_domain: str) -> None:
    """A priced product selling *selectors*, storing a publisher-backed format option as its key alone.

    Owner decision 2: the product stores ``{publisher_domain, format_option_id}`` and nothing
    else; the publisher's catalog supplies ``format_kind`` and ``params`` on every read.
    """
    product = ProductFactory(
        tenant=ctx["tenant"],
        product_id=product_id,
        properties=selectors,
        format_ids=None,
        format_options=[{"publisher_domain": option_domain, "format_option_id": option_id}],
    )
    PricingOptionFactory(product=product)


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


@given(parsers.parse('the tenant also holds product "{product_id}" that no authorized property backs'))
def given_unoffered_product(ctx: dict, product_id: str) -> None:
    # A publisher whose file the seller never fetched, so it authorizes nothing.
    _offer(ctx, product_id, [{"publisher_domain": "unbacked-publisher-psatest.com", "selection_type": "all"}])


@given(
    parsers.parse(
        'the tenant also holds product "{product_id}" declaring format option "{option_id}" under the seller\'s own host'
    )
)
def given_product_with_agent_host_option(ctx: dict, product_id: str, option_id: str) -> None:
    """A product selling a verified publisher whose one format option names the agent host as its publisher.

    The row the removed tenant catalog let a seller author: ``publisher_domain`` set to the
    host this agent serves its adagents.json at (``Tenant.virtual_host_name``).
    """
    tenant = ctx["tenant"]
    declaration = {
        "format_kind": "image",
        "format_option_id": option_id,
        "publisher_domain": tenant.virtual_host_name,
        "params": {"width": 300, "height": 250},
    }
    product = ProductFactory(tenant=tenant, product_id=product_id, format_ids=None, format_options=[declaration])
    PricingOptionFactory(product=product)


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


@given(parsers.parse('the operator saves product "{product_id}" selecting properties "{property_ids}"'))
def given_operator_saved_product(ctx: dict, product_id: str, property_ids: str) -> None:
    """Save the product through the real admin form (priced), and require that it was stored."""
    env = ctx["env"]
    page = env.create_product(product_id, property_mode="property_ids", selected_property_ids=comma_list(property_ids))
    assert env.stored_product(product_id) is not None, (
        f"the form stored no product {product_id!r}; it answered {page.status_code}"
    )


@given(
    parsers.parse(
        'the operator saved the edit form of product "{product_id}" with format option "{option_id}" of "{publisher}"'
    )
)
def given_operator_picked_publisher_option(ctx: dict, product_id: str, option_id: str, publisher: str) -> None:
    """Open the product's edit form and save it with one publisher option, picked by its key."""
    env = ctx["env"]
    env.open_product_edit_form(product_id)
    page = env.save_open_form_with(
        format_options=json.dumps([{"publisher_domain": publisher, "format_option_id": option_id}])
    )
    stored = env.stored_product(product_id)
    assert stored is not None and stored.format_options == [
        {"publisher_domain": publisher, "format_option_id": option_id}
    ], f"the form did not store the option; it answered {page.status_code}"


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


@then(parsers.parse('product "{product_id}" announces format_options {expected}'))
def then_product_announces_format_options(ctx: dict, product_id: str, expected: str) -> None:
    """Exactly the *expected* options, by key, each with the publisher's ``format_kind`` and ``params``.

    The wire model fills each declaration's pinned defaults (``canonical_formats_only``,
    the params' own defaults), so each expected member must be on the wire with its value
    and the options are matched whole by their key; nothing the publisher declared may be
    lost or changed.
    """
    announced = wire_entry(ctx, "products", product_id=product_id).get("format_options") or []
    wanted = json.loads(expected)
    key = lambda option: (option.get("publisher_domain"), option.get("format_option_id"))  # noqa: E731
    assert sorted(map(key, announced), key=str) == sorted(map(key, wanted), key=str), (
        f"product {product_id!r} announced format_options {announced!r}"
    )
    for option in wanted:
        served = next(o for o in announced if key(o) == key(option))
        lost = {
            name: (value, served.get(name))
            for name, value in option.items()
            if name != "params" and served.get(name) != value
        } | {
            f"params.{name}": (value, (served.get("params") or {}).get(name))
            for name, value in option.get("params", {}).items()
            if (served.get("params") or {}).get(name) != value
        }
        assert not lost, f"the publisher's declaration came back changed or absent (expected, got): {lost}"


@then(parsers.parse('product "{product_id}" announces publisher_properties {expected}'))
def then_product_announces(ctx: dict, product_id: str, expected: str) -> None:
    announced = wire_entry(ctx, "products", product_id=product_id)["publisher_properties"]
    assert announced == json.loads(expected), f"product {product_id!r} announced {announced!r}"


@then("no product announces the seller's own host as a format option's publisher_domain")
def then_no_agent_host_format_namespace(ctx: dict) -> None:
    host = ctx["tenant"].virtual_host_name
    products = wire_field(ctx, "products")
    assert products, "the buyer received no products, so no format option was announced at all"
    named = [
        (product["product_id"], option.get("format_option_id"))
        for product in products
        for option in product.get("format_options") or []
        if option.get("publisher_domain") == host
    ]
    assert named == [], f"format options announced under the seller's own host {host!r}: {named}"


@then(parsers.parse('the buyer receives exactly the products "{product_ids}"'))
def then_exact_products(ctx: dict, product_ids: str) -> None:
    received = sorted(product["product_id"] for product in wire_field(ctx, "products"))
    assert received == sorted(comma_list(product_ids)), f"received products {received!r}"


@then("the buyer receives no products")
def then_no_products(ctx: dict) -> None:
    received = [product["product_id"] for product in wire_dict(ctx).get("products") or []]
    assert received == [], f"received products {received!r}"
