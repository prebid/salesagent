"""Signal driven product discovery through the shared wire dispatcher."""

from pytest_bdd import given, parsers, then, when

from tests.bdd.steps._outcome_helpers import wire_field
from tests.bdd.steps.generic._dispatch import dispatch_request


@given("a signal driven catalogue with a new variant")
def given_new_catalog(ctx):
    ctx["env"].seed_catalog()


@given("a signal driven catalogue with a legacy unpriced variant")
def given_legacy_catalog(ctx):
    ctx["env"].seed_catalog(legacy=True)


@given("a signal driven catalogue restricted to another buyer")
def given_restricted_catalog(ctx):
    ctx["env"].seed_catalog(restricted=True)


@given("a signal driven catalogue with a legacy variant restricted to another buyer")
def given_restricted_legacy_catalog(ctx):
    ctx["env"].seed_catalog(legacy=True, restricted=True)


@given("a signal driven catalogue with an existing negotiated variant price")
def given_priced_catalog(ctx):
    ctx["env"].seed_catalog(priced=True)


@given("a signal driven catalogue with an invalid signal before a valid signal")
def given_invalid_signal_catalog(ctx):
    ctx["env"].seed_catalog(invalid_signal=True)


@when("the buyer discovers the signal driven catalogue")
def when_discover(ctx):
    dispatch_request(ctx, brief="premium readers")


@when("the seller restricts the template to another buyer")
def when_restrict_template(ctx):
    ctx["env"].restrict_template()


@when("the buyer discovers the signal driven catalogue twice")
def when_discover_twice(ctx):
    for _ in range(2):
        dispatch_request(ctx, brief="premium readers")
        assert ctx["result"].is_success


@then("the catalogue includes static inventory and one priced variant")
def then_priced_variant(ctx):
    products = wire_field(ctx, "products")
    assert sorted(p["product_id"] for p in products) == ["static", "template", ctx["env"].variant_id]
    variant = next(p for p in products if p["product_id"] == ctx["env"].variant_id)
    assert sorted((o["pricing_option_id"], o["fixed_price"]) for o in variant["pricing_options"]) == [
        ("click-rate", 1.35),
        ("negotiated-cpm", 7.25),
    ]


@then("the stored variant has exactly its two template pricing options")
def then_stored_options(ctx):
    assert ctx["env"].variant_pricing_ids() == ["click-rate", "negotiated-cpm"]


@then("the variant retains only its negotiated buyer-contract price of 12.50")
def then_existing_price(ctx):
    products = wire_field(ctx, "products")
    assert sorted(p["product_id"] for p in products) == ["static", "template", ctx["env"].variant_id]
    variant = next(p for p in products if p["product_id"] == ctx["env"].variant_id)
    assert [(o["pricing_option_id"], o["fixed_price"]) for o in variant["pricing_options"]] == [
        ("buyer-contract", 12.5)
    ]
    assert ctx["env"].variant_pricing_ids() == ["buyer-contract"]


@then("the catalogue contains only static inventory")
def then_only_static(ctx):
    assert [p["product_id"] for p in wire_field(ctx, "products")] == ["static"]


@then(parsers.parse('the variant name is "{name}"'))
def then_variant_name(ctx, name):
    variant = next(p for p in wire_field(ctx, "products") if p["product_id"] == ctx["env"].variant_id)
    assert variant["name"] == name
