"""Steps for BR-UC-GET-PRODUCTS-format-id-filter: which products a format_ids filter selects.

Setup stores REAL ``products.format_ids`` documents through ``ProductFactory`` -- the same
factory every other product fixture uses -- and dispatch reuses the shared
``the buyer requests products`` dispatcher. Assertions read ``wire_field``, the serialized
body, so what is graded is the set of products the buyer actually received.

The two agents the scenario needs are both declared here, and the Given asserts they are
not canonically equal before anything is stored: a "different agent" that the
canonicalization normalizes into the first one would make the filter match for the boring
reason, and the obligation would go ungraded while reading green.
"""

from __future__ import annotations

from typing import Any

from pytest_bdd import given, parsers, then, when

from src.core.schemas import canonical_agent_url
from tests.bdd.steps._outcome_helpers import wire_field
from tests.bdd.steps.generic._dispatch import dispatch_request
from tests.factories import PricingOptionFactory, ProductFactory
from tests.factories.creative import DEFAULT_CREATIVE_AGENT_URL

#: The id BOTH products declare. A format id is unique only inside its declaring agent's
#: namespace (core/format-id.json: "Format identifier within the agent's namespace"), so
#: one id under two agents is a legal catalog and the collision is what the filter has to
#: tell apart. The value is one the captured reference catalog holds, so format resolution
#: succeeds for both products and neither drops out of the response for an unrelated reason.
_SHARED_FORMAT_ID = "display_300x250_image"

#: The two declaring agents. The second is the one the buyer's filter names.
_FIRST_AGENT = DEFAULT_CREATIVE_AGENT_URL
_SECOND_AGENT = "https://other-creative-agent.example.com"


def _product_declaring_shared_id_under(tenant: Any, agent_url: str, format_id: str = _SHARED_FORMAT_ID) -> Any:
    """Persist one product declaring *format_id* under *agent_url*, and return it."""
    return _priced_product(tenant, format_ids=[{"agent_url": agent_url, "id": format_id}])


def _priced_product(tenant: Any, **format_arms: Any) -> Any:
    """Persist one priced product declaring *format_arms* (``format_ids`` / ``format_options``).

    The pricing option is NOT incidental scaffolding, for the same measured reason its
    sibling in ``uc_get_products_format_options`` carries one: a product with none cannot
    be converted at all, so without it the scenario would grade the missing pricing option
    rather than the filter.

    Takes the TENANT and returns the row, rather than taking ``ctx`` and storing under a key
    the caller passes. A ``ctx``-first helper that subscripts ``ctx`` with one of its own
    parameters is the ``_require`` shape ``test_architecture_bdd_ctx_keys`` tracks BY
    FUNCTION NAME across every step module, so a helper in that shape also changes how the
    guard reads every same-named helper elsewhere -- measured: it made the sibling module's
    ``_store_product(ctx, <declaration list>)`` calls read as ctx keys and failed the guard.
    """
    product = ProductFactory(tenant=tenant, **format_arms)
    PricingOptionFactory(product=product)
    return product


# ── Given steps ─────────────────────────────────────────────────────


@given("the seller stores two products whose formats share one id under two different creative agents")
def given_two_products_sharing_a_format_id(ctx: dict) -> None:
    """Store the selected product and the counter-example, identical but for the agent.

    The guard is what makes the counter-example a counter-example: if the two agent URLs
    canonicalized to one value, a correct filter would return BOTH products and the
    exclusion outcome would be asserting something false of a conformant seller.
    """
    assert canonical_agent_url(_FIRST_AGENT) != canonical_agent_url(_SECOND_AGENT), (
        "the two declaring agents canonicalize to one value, so no filter can tell the two "
        "products apart and this scenario grades nothing"
    )
    tenant = ctx["tenant"]
    ctx["other_agent_product"] = _product_declaring_shared_id_under(tenant, _FIRST_AGENT)
    ctx["named_product"] = _product_declaring_shared_id_under(tenant, _SECOND_AGENT)


# ── When steps ──────────────────────────────────────────────────────


@when("the buyer requests products filtering format_ids by that id under the second agent only")
def when_request_products_filtered_by_foreign_format_id(ctx: dict) -> None:
    """Dispatch get_products carrying the one reference object the filter names.

    ``format_ids`` items are format_id OBJECTS (core/product-filters.json $refs
    core/format-id.json), never bare slugs, so the filter names both members.
    """
    dispatch_request(
        ctx,
        brief="format_id filter test",
        filters={"format_ids": [{"agent_url": _SECOND_AGENT, "id": _SHARED_FORMAT_ID}]},
    )


@given(
    parsers.parse(
        'the seller stores a product declaring "{format_id}" under "{declared}" and another declaring it under "{other}"'
    )
)
def given_product_and_path_sibling(ctx: dict, format_id: str, declared: str, other: str) -> None:
    """The product the filter will name in another spelling, and its same-host sibling.

    The sibling's agent must be a DIFFERENT agent after canonicalization, or a correct filter
    returns both and the exact-set outcome asserts something false of a conformant seller.
    """
    assert canonical_agent_url(declared) != canonical_agent_url(other), (
        f"{declared!r} and {other!r} canonicalize to one agent, so the sibling is no counter-example"
    )
    tenant = ctx["tenant"]
    ctx["named_product"] = _product_declaring_shared_id_under(tenant, declared, format_id)
    ctx["other_agent_product"] = _product_declaring_shared_id_under(tenant, other, format_id)
    ctx["declared_agent_url"] = declared


@when(parsers.parse('the buyer requests products filtering format_ids by "{format_id}" under "{requested}"'))
def when_request_products_filtered_by_respelled_agent(ctx: dict, format_id: str, requested: str) -> None:
    """Dispatch get_products whose one format_ids reference names the declaring agent in *requested*.

    The spelling must DIFFER from the stored one byte-for-byte and be the SAME agent after
    canonicalization; either half failing makes the row grade nothing.
    """
    declared = ctx["declared_agent_url"]
    assert requested != declared, f"{requested!r} is the stored spelling, so the row respells nothing"
    assert canonical_agent_url(requested) == canonical_agent_url(declared), (
        f"{requested!r} is not the agent {declared!r} names under canonicalization, so a match would be wrong"
    )
    dispatch_request(
        ctx, brief="format_id filter test", filters={"format_ids": [{"agent_url": requested, "id": format_id}]}
    )


#: Named product format contracts the canonical-shape outline stores, one product each. The
#: legacy ids are the reference agent's, so only the registry's normalization relates them to
#: a filter's id; the canonical options carry the parameters a projection is compared on
#: (canonical-formats.mdx: width, height, duration_ms_exact). ``format_ids=None`` on a
#: canonical-only product so the factory's default legacy arm does not answer for it.
_FILTER_PRODUCT_CONTRACTS: dict[str, dict[str, Any]] = {
    "display_300x250_image": {"format_ids": [{"agent_url": DEFAULT_CREATIVE_AGENT_URL, "id": "display_300x250_image"}]},
    "display_728x90_image": {"format_ids": [{"agent_url": DEFAULT_CREATIVE_AGENT_URL, "id": "display_728x90_image"}]},
    "image_300x250_option": {
        "format_ids": None,
        "format_options": [
            {"format_kind": "image", "format_option_id": "filter_image_mrec", "params": {"width": 300, "height": 250}}
        ],
    },
    "video_30s_option": {
        "format_ids": None,
        "format_options": [
            {
                "format_kind": "video_hosted",
                "format_option_id": "filter_video_30s",
                "params": {"duration_ms_exact": 30000},
            }
        ],
    },
    "video_15s_option": {
        "format_ids": None,
        "format_options": [
            {
                "format_kind": "video_hosted",
                "format_option_id": "filter_video_15s",
                "params": {"duration_ms_exact": 15000},
            }
        ],
    },
}


def _contract_names(listed: str) -> list[str]:
    """The comma-separated contract names a scenario cell lists."""
    return [name.strip() for name in listed.split(",") if name.strip()]


@given(parsers.parse('the seller stores products declaring "{accepting}" and a product declaring "{rejected}"'))
def given_products_of_named_contracts(ctx: dict, accepting: str, rejected: str) -> None:
    """One product per named contract: those the filter should select, and one it should not.

    The rejected product must be the SAME canonical kind as an accepting one, or a filter that
    compared kinds alone would exclude it for the boring reason and the row would not grade
    the parameters.
    """
    tenant = ctx["tenant"]
    ctx["accepting_products"] = [
        _priced_product(tenant, **_FILTER_PRODUCT_CONTRACTS[name]) for name in _contract_names(accepting)
    ]
    ctx["rejected_product"] = _priced_product(tenant, **_FILTER_PRODUCT_CONTRACTS[rejected])


@when(parsers.parse('the buyer requests products filtering format_ids by "{format_id}" at the standard creative agent'))
def when_request_products_filtered_by_legacy_id(ctx: dict, format_id: str) -> None:
    """Dispatch get_products whose one format_ids reference names *format_id* at the reference agent."""
    dispatch_request(
        ctx,
        brief="format_id filter test",
        filters={"format_ids": [{"agent_url": DEFAULT_CREATIVE_AGENT_URL, "id": format_id}]},
    )


# ── Then steps ──────────────────────────────────────────────────────


def _announced_product_ids(ctx: dict) -> set[str]:
    """The product_ids the buyer received, off the wire."""
    products: list[dict[str, Any]] = wire_field(ctx, "products")
    return {product["product_id"] for product in products}


@then("the announced products are exactly the product whose format the filter named")
def then_only_the_named_product_announced(ctx: dict) -> None:
    """An EXACT set, which is what separates pair matching from the two failure modes.

    Containment in either direction passes for one of them: ``>=`` passes on a filter that
    compared the id alone and returned both products, and ``<=`` passes on a filter that
    matched nothing and returned neither.
    """
    announced = _announced_product_ids(ctx)
    expected = {ctx["named_product"].product_id}
    assert announced == expected, (
        f"the format_ids filter did not select exactly the product it named: expected {sorted(expected)}, "
        f"got {sorted(announced)}"
    )


@then("the announced products do not include the product declaring that id under the other agent")
def then_other_agent_product_excluded(ctx: dict) -> None:
    """Named as its own outcome because it is the false positive the MUST exists to stop.

    core/format-id.json requires canonicalizing ``agent_url`` before treating two
    references as the same format. The other agent's product shares only the id, so a
    seller that returns it has answered a reference to a format it does not host.
    """
    announced = _announced_product_ids(ctx)
    other = ctx["other_agent_product"].product_id
    assert other not in announced, (
        f"product {other!r} declares {_SHARED_FORMAT_ID!r} under {_FIRST_AGENT!r}, which the "
        f"filter did not name, and the seller announced it anyway: {sorted(announced)}"
    )


@then("the announced products are exactly the ones whose contract accepts the filter's normalized shape")
def then_only_the_accepting_products_announced(ctx: dict) -> None:
    """An EXACT set: every accepting product, and not the rejected one.

    A raw-pair filter announces none of them (no product spells the filter's id), and a
    filter that ignored the parameters announces the rejected one too; each fails the equality.
    """
    announced = _announced_product_ids(ctx)
    expected = {product.product_id for product in ctx["accepting_products"]}
    assert announced == expected, (
        f"the format_ids filter did not select exactly the products accepting its normalized shape: "
        f"expected {sorted(expected)}, got {sorted(announced)} "
        f"(the product it must not select is {ctx['rejected_product'].product_id!r})"
    )
