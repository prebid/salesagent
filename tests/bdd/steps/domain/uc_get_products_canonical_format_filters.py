"""Steps for BR-UC-GET-PRODUCTS-canonical-format-filters: which products the canonical
format filters select.

Built exactly like ``uc_get_products_format_id_filter``, its legacy-arm sibling: setup
stores REAL ``products`` rows through ``ProductFactory`` -- including one the filter must
NOT select -- dispatch goes through the shared wire dispatcher, and the outcome reads
``wire_field`` rather than the typed payload, because the typed payload has already
coerced every field to its declared type.

Each scenario's catalogue holds a counter-example, so the expected set is never derived
from the seeded data. A filter test whose every product the filter selects passes with
the filter deleted.
"""

from __future__ import annotations

from typing import Any

from adcp.types.canonical_creative import CanonicalFormatKind
from pytest_bdd import given, then, when

from tests.bdd.steps._outcome_helpers import wire_field
from tests.bdd.steps.generic._dispatch import dispatch_request
from tests.factories import PricingOptionFactory, ProductFactory

#: The kind the filter names, and the kind it must not select. Both are members of the
#: pinned sixteen-kind vocabulary, so neither product is dropped for declaring something
#: unparseable rather than for the filter's decision.
_NAMED_KIND = "image"
_OTHER_KIND = "video_hosted"

#: A third kind, for the multi-value filter: named alongside ``_NAMED_KIND`` while
#: ``_OTHER_KIND`` stays unnamed. ``html5`` is a member of the pinned vocabulary
#: (core/canonical-format-kind.json) whose params accept ``_PARAMS``. There is no bare
#: ``audio`` member -- the audio kinds are ``audio_hosted``, ``audio_vast`` and
#: ``audio_daast`` -- and a product declaring a non-member fails conversion, which
#: get_products answers with INTERNAL_ERROR for the whole catalogue.
_SECOND_NAMED_KIND = "html5"

#: The one member of that vocabulary that names a format OUTSIDE the IAB standard set,
#: which is what ``standard_formats_only`` has to exclude. Written from the member list
#: rather than invented: there is no ``video`` member, so a set like {"image", "video"}
#: would raise rather than filter.
_CUSTOM_KIND = "custom"

#: Params for a declaration. Concrete, so a product dropped for an unparseable declaration
#: is distinguishable from one the filter excluded.
_PARAMS: dict[str, Any] = {"width": 300, "height": 250}


#: The option id BOTH scope-collision products declare. An option id is unique only inside
#: its scope: core/format-option-ref.json's two arms make publisher_domain required on one
#: and forbidden on the other, so one id under two scopes is a legal catalogue and the
#: collision is what the filter has to tell apart.
_SHARED_OPTION_ID = "premium_above_fold"

#: The publisher whose catalogue the OTHER product's option belongs to. The filter names the
#: product-scoped option, so this one is the counter-example.
_PUBLISHER_DOMAIN = "publisher.example.com"

#: A second publisher declaring the same option id. The publisher-scoped filter names
#: ``_PUBLISHER_DOMAIN``, so this one is the counter-example that only an exact
#: ``publisher_domain`` comparison excludes.
_OTHER_PUBLISHER_DOMAIN = "other-publisher.example.com"


def _canonical_product_declaring_option(tenant: Any, publisher_domain: str | None) -> Any:
    """Persist a canonical-only product declaring ``_SHARED_OPTION_ID`` in one scope.

    ``publisher_domain=None`` is PRODUCT scope and a domain is PUBLISHER scope, which is how
    the pin discriminates the two arms -- the product arm declares ``"publisher_domain":
    false``, forbidding the member outright, so presence is the discriminator rather than a
    stored ``scope`` beside it. The two products are otherwise identical, so a failing row
    reports on the scope comparison and nothing else.
    """
    declaration: dict[str, Any] = {
        "format_kind": _NAMED_KIND,
        "params": dict(_PARAMS),
        "format_option_id": _SHARED_OPTION_ID,
    }
    if publisher_domain is not None:
        declaration["publisher_domain"] = publisher_domain
    product = ProductFactory(tenant=tenant, format_ids=None, format_options=[declaration])
    PricingOptionFactory(product=product)
    return product


def _canonical_product_declaring(tenant: Any, format_kind: str) -> Any:
    """Persist one CANONICAL-ONLY product declaring *format_kind*, and return it.

    ``format_ids=None`` so the product carries no legacy arm at all -- which is the shape
    migration 08cd415b3d91 made representable, and the shape that makes these scenarios
    grade the canonical filters rather than the legacy one. A dual-authored product would
    let the legacy ``format_ids`` filter answer the same question and the scenario could
    not tell which arm decided.

    The pricing option is NOT incidental scaffolding, for the same measured reason its two
    siblings carry one: a product with none cannot be converted at all, so without it the
    scenario would grade the missing pricing option rather than the filter.

    Takes the TENANT and returns the row rather than taking ``ctx`` and a key to store
    under. A ``ctx``-first helper that subscripts ``ctx`` with one of its own parameters is
    the shape ``test_architecture_bdd_ctx_keys`` tracks BY FUNCTION NAME across every step
    module, so writing one here would change how that guard reads same-named helpers
    elsewhere -- which it has already done once, to this feature's siblings.
    """
    product = ProductFactory(
        tenant=tenant,
        format_ids=None,
        format_options=[{"format_kind": format_kind, "params": dict(_PARAMS)}],
    )
    PricingOptionFactory(product=product)
    return product


# ── Given steps ─────────────────────────────────────────────────────


@given("the seller stores two canonical products declaring different format kinds")
def given_two_canonical_products_with_different_kinds(ctx: dict) -> None:
    """Store the product the filter names and the counter-example it must not select."""
    assert _NAMED_KIND != _OTHER_KIND, (
        "the two products declare one kind, so no filter can tell them apart and this scenario grades nothing"
    )
    tenant = ctx["tenant"]
    ctx["named_products"] = [_canonical_product_declaring(tenant, _NAMED_KIND)]
    ctx["excluded_products"] = [_canonical_product_declaring(tenant, _OTHER_KIND)]


@given("the seller stores three canonical products declaring three different format kinds")
def given_three_canonical_products_with_different_kinds(ctx: dict) -> None:
    """Store two products the multi-value filter names and the one it must not select."""
    kinds = {_NAMED_KIND, _SECOND_NAMED_KIND, _OTHER_KIND}
    assert len(kinds) == 3, "two of the three products declare one kind, so the filter cannot tell them apart"
    unpinned = kinds - {kind.value for kind in CanonicalFormatKind}
    assert not unpinned, (
        f"{sorted(unpinned)} not in the pinned CanonicalFormatKind; a product declaring one fails "
        "conversion and get_products answers INTERNAL_ERROR instead of filtering"
    )
    tenant = ctx["tenant"]
    ctx["named_products"] = [
        _canonical_product_declaring(tenant, _NAMED_KIND),
        _canonical_product_declaring(tenant, _SECOND_NAMED_KIND),
    ]
    ctx["excluded_products"] = [_canonical_product_declaring(tenant, _OTHER_KIND)]


@given("the seller stores two products whose format options share one id under two different scopes")
def given_two_products_sharing_an_option_id(ctx: dict) -> None:
    """Store the product-scoped option the filter names and its publisher-scoped twin."""
    tenant = ctx["tenant"]
    ctx["named_products"] = [_canonical_product_declaring_option(tenant, None)]
    ctx["excluded_products"] = [_canonical_product_declaring_option(tenant, _PUBLISHER_DOMAIN)]


@given(
    "the seller stores three products declaring one option id under product scope, "
    "under the named publisher and under another publisher"
)
def given_three_products_sharing_an_option_id(ctx: dict) -> None:
    """Store one option id under three identities: product scope and two publishers.

    The When step decides which of them the filter names, so this Given records each
    product under its scope rather than as named or excluded.
    """
    tenant = ctx["tenant"]
    ctx["option_scope_products"] = {
        "product": _canonical_product_declaring_option(tenant, None),
        "publisher": _canonical_product_declaring_option(tenant, _PUBLISHER_DOMAIN),
        "other_publisher": _canonical_product_declaring_option(tenant, _OTHER_PUBLISHER_DOMAIN),
    }


@given("the seller stores two canonical products, one declaring a custom format kind")
def given_two_canonical_products_one_custom(ctx: dict) -> None:
    """Store a standard-kind product and the custom-kind counter-example."""
    tenant = ctx["tenant"]
    ctx["named_products"] = [_canonical_product_declaring(tenant, _NAMED_KIND)]
    ctx["excluded_products"] = [_canonical_product_declaring(tenant, _CUSTOM_KIND)]


# ── When steps ──────────────────────────────────────────────────────


@when("the buyer requests products filtering format_kinds by the first product's kind only")
def when_request_products_filtered_by_format_kind(ctx: dict) -> None:
    """Dispatch get_products naming ONE canonical kind.

    The filter's items are canonical-format-kind values, so the request carries the kind
    itself rather than a reference object -- unlike the legacy ``format_ids`` filter, whose
    items are ``core/format-id.json`` references.
    """
    dispatch_request(ctx, brief="format_kinds filter test", filters={"format_kinds": [_NAMED_KIND]})


@when("the buyer requests products filtering format_option_refs by the product-scoped option only")
def when_request_products_filtered_by_option_ref(ctx: dict) -> None:
    """Dispatch get_products naming ONE option ref, in product scope.

    ``scope`` is carried explicitly because core/format-option-ref.json declares
    ``discriminator: {propertyName: "scope"}``: a reference without it names no arm of the
    union, and the request would be refused rather than filtered.
    """
    dispatch_request(
        ctx,
        brief="format_option_refs filter test",
        filters={"format_option_refs": [{"scope": "product", "format_option_id": _SHARED_OPTION_ID}]},
    )


@when("the buyer requests products filtering format_kinds by two of the three products' kinds")
def when_request_products_filtered_by_two_format_kinds(ctx: dict) -> None:
    """Dispatch get_products naming TWO canonical kinds.

    The pin says "any of these", so the answer is the union. Each stored product declares
    exactly one kind, so an AND reading selects nothing and a first-element reading
    selects one product: both fail the exact-set outcome.
    """
    dispatch_request(
        ctx,
        brief="multi-value format_kinds filter test",
        filters={"format_kinds": [_NAMED_KIND, _SECOND_NAMED_KIND]},
    )


#: The two references the option-scope When steps can name, keyed like
#: ``ctx["option_scope_products"]``. Built from core/format-option-ref.json's two arms:
#: ``scope`` is the discriminator, and ``publisher_domain`` is required on the publisher
#: arm and forbidden on the product arm.
_OPTION_REFS: dict[str, dict[str, str]] = {
    "product": {"scope": "product", "format_option_id": _SHARED_OPTION_ID},
    "publisher": {
        "scope": "publisher",
        "publisher_domain": _PUBLISHER_DOMAIN,
        "format_option_id": _SHARED_OPTION_ID,
    },
}


def _dispatch_option_refs_filter(ctx: dict, named_scopes: tuple[str, ...]) -> None:
    """Dispatch a format_option_refs filter naming *named_scopes*, and record the split.

    The products under the named scopes are the expected answer and every other product
    in ``ctx["option_scope_products"]`` is a counter-example, so the expected set comes
    from the request rather than from the seeded catalogue.
    """
    by_scope: dict[str, Any] = ctx["option_scope_products"]
    ctx["named_products"] = [by_scope[scope] for scope in named_scopes]
    ctx["excluded_products"] = [product for scope, product in by_scope.items() if scope not in named_scopes]
    dispatch_request(
        ctx,
        brief="format_option_refs filter test",
        filters={"format_option_refs": [_OPTION_REFS[scope] for scope in named_scopes]},
    )


@when("the buyer requests products filtering format_option_refs by the named publisher's option only")
def when_request_products_filtered_by_publisher_option_ref(ctx: dict) -> None:
    """Dispatch get_products naming ONE option ref, in publisher scope."""
    _dispatch_option_refs_filter(ctx, ("publisher",))


@when(
    "the buyer requests products filtering format_option_refs by the product-scoped option "
    "and the named publisher's option"
)
def when_request_products_filtered_by_two_option_refs(ctx: dict) -> None:
    """Dispatch get_products naming TWO option refs, one per scope arm."""
    _dispatch_option_refs_filter(ctx, ("product", "publisher"))


@when("the buyer requests products with standard_formats_only")
def when_request_products_standard_only(ctx: dict) -> None:
    """Dispatch get_products asking for IAB standard formats only."""
    dispatch_request(ctx, brief="standard_formats_only filter test", filters={"standard_formats_only": True})


# ── Then steps ──────────────────────────────────────────────────────


def _announced_product_ids(ctx: dict) -> set[str]:
    """The product_ids the buyer received, off the wire."""
    products: list[dict[str, Any]] = wire_field(ctx, "products")
    return {product["product_id"] for product in products}


def _assert_exactly_the_named_products(ctx: dict, *, filter_name: str) -> None:
    """An EXACT set, which is what separates a working filter from its two failure modes.

    Containment in either direction passes for one of them: ``>=`` passes on an IGNORED
    filter that returned both products, and ``<=`` passes on a filter that matched nothing
    and returned neither.
    """
    announced = _announced_product_ids(ctx)
    expected = {product.product_id for product in ctx["named_products"]}
    assert announced == expected, (
        f"the {filter_name} filter did not select exactly the products it named: "
        f"expected {sorted(expected)}, got {sorted(announced)}"
    )


def _assert_counter_example_excluded(ctx: dict, *, what: str) -> None:
    """Named as its own outcome because it is the false positive the filter exists to stop."""
    announced = _announced_product_ids(ctx)
    excluded = {product.product_id for product in ctx["excluded_products"]}
    assert excluded, "the scenario stored no counter-example, so exclusion grades nothing"
    assert not (excluded & announced), (
        f"the buyer received the product declaring {what}, which it asked to exclude: "
        f"{sorted(excluded & announced)} in {sorted(announced)}"
    )


@then("the announced products are exactly the product declaring the named kind")
@then("the announced products are exactly the two products declaring a named kind")
def then_only_named_kind_product_announced(ctx: dict) -> None:
    _assert_exactly_the_named_products(ctx, filter_name="format_kinds")


@then("the announced products do not include the product declaring the other kind")
def then_other_kind_product_excluded(ctx: dict) -> None:
    _assert_counter_example_excluded(ctx, what=f"the other kind ({_OTHER_KIND})")


@then("the announced products are exactly the product whose option the filter named")
@then("the announced products are exactly the products declaring either named option")
@then("the announced products are exactly the product declaring the option under the named publisher")
def then_only_named_option_product_announced(ctx: dict) -> None:
    _assert_exactly_the_named_products(ctx, filter_name="format_option_refs")


@then("the announced products do not include the product declaring that id under publisher scope")
def then_publisher_scoped_product_excluded(ctx: dict) -> None:
    _assert_counter_example_excluded(ctx, what=f"the same option id under publisher scope ({_PUBLISHER_DOMAIN})")


@then("the announced products do not include the products declaring that id under product scope or another publisher")
def then_product_scope_and_other_publisher_excluded(ctx: dict) -> None:
    _assert_counter_example_excluded(
        ctx, what=f"the same option id under product scope or under {_OTHER_PUBLISHER_DOMAIN}"
    )


@then("the announced products do not include the product declaring that id under another publisher")
def then_other_publisher_excluded(ctx: dict) -> None:
    _assert_counter_example_excluded(ctx, what=f"the same option id under {_OTHER_PUBLISHER_DOMAIN}")


@then("the announced products are exactly the product declaring the standard kind")
def then_only_standard_product_announced(ctx: dict) -> None:
    _assert_exactly_the_named_products(ctx, filter_name="standard_formats_only")


@then("the announced products do not include the product declaring the custom kind")
def then_custom_kind_product_excluded(ctx: dict) -> None:
    _assert_counter_example_excluded(ctx, what=f"a custom format kind ({_CUSTOM_KIND})")
