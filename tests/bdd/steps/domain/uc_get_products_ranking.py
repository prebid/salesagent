"""Steps for BR-UC-GET-PRODUCTS-ranking-fail-open: AI ranking against a failing provider.

The tenant's ranking prompt and AI provider go through ``configure_tenant_field``, which
writes the Tenant row (what the resolver reads on a2a, mcp and e2e_rest) and the identity
REST is handed, so every transport ranks with the same configuration. Nothing is patched:
the env is ``RealRankingProductEnv``, and the provider fails because the test processes
point Gemini at a closed port (see the feature's header).

The Background's "a tenant is configured for product discovery" and the When "the buyer
requests products" (which sends a brief) are the shared steps in
``uc_get_products_inventory.py``.
"""

from __future__ import annotations

from pytest_bdd import given, parsers, then

from tests.bdd.steps._outcome_helpers import wire_advisory_errors, wire_field
from tests.bdd.steps.generic._table import quoted_list
from tests.factories import PricingOptionFactory, ProductFactory

# ── Given steps ─────────────────────────────────────────────────────


@given(parsers.parse('the tenant ranks products with the prompt "{prompt}"'))
def given_ranking_prompt(ctx: dict, prompt: str) -> None:
    ctx["env"].configure_tenant_field("product_ranking_prompt", prompt)


@given(parsers.parse('the tenant\'s AI provider is "{provider}" with API key "{api_key}"'))
def given_ai_provider(ctx: dict, provider: str, api_key: str) -> None:
    ctx["env"].configure_tenant_field("ai_config", {"provider": provider, "api_key": api_key})


@given(parsers.parse("the seller stores products {product_ids}"))
def given_products(ctx: dict, product_ids: str) -> None:
    for product_id in quoted_list(product_ids):
        PricingOptionFactory(product=ProductFactory(tenant=ctx["tenant"], product_id=product_id))


# ── Then steps ──────────────────────────────────────────────────────


@then(parsers.parse("the buyer receives products {product_ids} in catalog order with no errors"))
def then_products_unranked(ctx: dict, product_ids: str) -> None:
    """Every stored product, in catalog order, on a successful response with no advisory.

    ``wire_field`` fails, naming the error, when the request was refused, so this is also
    the "no error envelope" half. Catalog order is ``product_id`` order (the repository's
    ``list_all``): ranking would have sorted by score, and its 0.1 threshold would have
    dropped products, so the full catalog in that order is what unranked looks like.
    """
    received = [product["product_id"] for product in wire_field(ctx, "products")]
    assert received == quoted_list(product_ids), f"expected the unranked catalog, got {received!r}"
    assert wire_advisory_errors(ctx) == [], f"ranking failure surfaced as an advisory: {wire_advisory_errors(ctx)!r}"
