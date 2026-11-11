"""Domain step definitions for UC-001 discovery: the BR-RULE-002 policy and BR-RULE-005 ranking scenarios.

Bound scenario by scenario from ``tests/bdd/test_uc001_discover_available_inventory.py``.

Both rules run on the SELLER's own AI configuration: ``ai_config`` (the column the Admin UI
writes) or the legacy ``gemini_api_key`` column. Neither rule is in AdCP 3.1.1
(advertising_policy, the ranking prompt and the 0.1 threshold are implementation-only), so
these scenarios are ungraded by any storyboard; the one wire contract they share with the
spec is the ``POLICY_VIOLATION`` code, which is in the pinned 3.1 error-code enum.

The model is scripted, never called: in-process ``ProductEnv`` stubs it, and over e2e the
live server's Gemini client reaches the compose ``scripted-llm`` origin. The seller's API
key is ``env.scripted_model_key``, which is the key the e2e origin answers; any other key is
refused there, which is what makes "ranking ran on the seller's configuration" a wire fact.

Every Then reads the WIRE (``wire_field``), never ``env.mock`` call args. The one fact with
no wire surface, which of ``rank_products_async``'s two text arguments received the seller's
prompt and which the buyer's brief, is graded where the call lands: inside the scripted
model (``ProductEnv.set_ranking_scores``).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pytest_bdd import given, parsers, then, when

from tests.bdd.steps._outcome_helpers import wire_product_ids
from tests.bdd.steps.domain.uc_get_products_inventory import given_tenant
from tests.bdd.steps.generic._dispatch import dispatch_request
from tests.factories import PricingOptionFactory, ProductFactory

#: The products the scenarios name, in catalog order. ``ProductRepository.list_all`` orders
#: by ``product_id``, so "catalog order" is a claim about production, checked as each
#: product is added. The Background seeds the first; "a second product" adds the second.
_CATALOG: tuple[tuple[str, str], ...] = (
    ("prod_a_banner", "Homepage Banner"),
    ("prod_b_video", "Preroll Video"),
)

_BRIEF = "video campaign for a sports launch"
_RANKING_PROMPT = "Prefer video inventory"


def _add_catalog_product(ctx: dict, product_id: str, name: str) -> None:
    """Create a priced product (pricing so it survives conversion) at the end of the catalog."""
    catalog: list[str] = ctx.setdefault("catalog", [])
    assert not catalog or product_id > catalog[-1], (
        f"{product_id!r} does not sort after {catalog[-1]!r}; ProductRepository.list_all orders by "
        "product_id, so a product added 'after it in catalog order' must sort after it"
    )
    product = ProductFactory(tenant=ctx["tenant"], product_id=product_id, name=name)
    PricingOptionFactory(product=product)
    catalog.append(product_id)


# ── The seller's own AI configuration ───────────────────────────────


def _configure_seller_ai(env: Any, ai_config: dict[str, Any] | None, gemini_api_key: str | None = None) -> None:
    """Write the seller's AI configuration columns; every transport's resolver reads them."""
    env.configure_tenant_field("ai_config", ai_config)
    env.configure_tenant_field("gemini_api_key", gemini_api_key)


#: Where, and how, the seller configured AI, by the scenarios' <where> wording.
_SELLER_AI: dict[str, Callable[[Any], None]] = {
    # "gemini" rather than the canonical "google" on purpose: it is the legacy spelling
    # the Admin UI stores, and production canonicalizes it before use.
    "in ai_config": lambda env: _configure_seller_ai(
        env, {"provider": "gemini", "model": "gemini-2.0-flash", "api_key": env.scripted_model_key}
    ),
    "in the legacy gemini_api_key column": lambda env: _configure_seller_ai(env, None, env.scripted_model_key),
    "nowhere": lambda env: _configure_seller_ai(env, None),
    # What the settings page stores when the seller leaves the key blank, telling the
    # seller "AI features will be disabled" as it saves it.
    "in an ai_config row without an API key": lambda env: _configure_seller_ai(
        env, {"provider": "gemini", "model": "gemini-2.0-flash"}
    ),
    # A provider other than the platform's (Google) with a blank model: the platform's
    # default model belongs to another vendor and may not fill in, so no model resolves
    # and the factory refuses to build one.
    "in an ai_config the model factory refuses to build": lambda env: _configure_seller_ai(
        env, {"provider": "anthropic", "model": "", "api_key": env.scripted_model_key}
    ),
    # settings.temperature is bounded to [0, 2] by ModelSettings, so the stored row does
    # not validate as a TenantAIConfig.
    "in a stored ai_config that does not parse": lambda env: _configure_seller_ai(
        env, {"provider": "gemini", "api_key": env.scripted_model_key, "settings": {"temperature": 99}}
    ),
}


def _ranking_on(where: str) -> Callable[[Any], None]:
    """Ranking turned on with the seller's AI configured ``where``."""

    def _apply(env: Any) -> None:
        _SELLER_AI[where](env)
        env.set_tenant_ai_ranking(_RANKING_PROMPT)

    return _apply


def _seller_model_fails(fail: Callable[[Any], None]) -> Callable[[Any], None]:
    """Ranking on the seller's own ai_config, whose model then fails as ``fail`` sets up."""

    def _apply(env: Any) -> None:
        _ranking_on("in ai_config")(env)
        fail(env)

    return _apply


#: Why ranking cannot run, by the INV-4 Outline's <failure> wording.
_RANKING_FAILURES: dict[str, Callable[[Any], None]] = {
    "the AI ranking service fails": _seller_model_fails(lambda env: env.set_ranking_unavailable()),
    "the seller's AI provider drops the connection": _seller_model_fails(
        lambda env: env.set_ranking_connection_dropped()
    ),
    "the seller's ai_config is one the model factory refuses to build": _ranking_on(
        "in an ai_config the model factory refuses to build"
    ),
    "the seller's stored ai_config does not parse": _ranking_on("in a stored ai_config that does not parse"),
    "the seller's ai_config names a provider with no API key": _ranking_on("in an ai_config row without an API key"),
}


# ── Given steps ─────────────────────────────────────────────────────


@given("a tenant exists with at least one product in the catalog")
def given_tenant_with_a_product(ctx: dict) -> None:
    """The discovery tenant and principal, and one priced product: the scenarios' "a product"."""
    given_tenant(ctx)
    _add_catalog_product(ctx, *_CATALOG[0])


@given(parsers.re(r"the tenant has advertising_policy (?P<state>enabled|disabled)"))
def given_advertising_policy(ctx: dict, state: str) -> None:
    """``advertising_policy.enabled`` on the tenant, as the Admin UI's policy settings store it."""
    ctx["env"].configure_tenant_field("advertising_policy", {"enabled": state == "enabled"})


@given(parsers.parse("the seller's AI is configured {where}"))
def given_seller_ai(ctx: dict, where: str) -> None:
    _SELLER_AI[where](ctx["env"])


@given("the LLM evaluates the brief as BLOCKED")
def given_llm_blocks_the_brief(ctx: dict) -> None:
    """The policy model returns a BLOCKED verdict if it is asked."""
    ctx["env"].set_policy_blocked("Brief violates the seller's advertising policy")


@given("the LLM evaluates the brief as ALLOWED")
def given_llm_allows_the_brief(ctx: dict) -> None:
    """The policy model returns an ALLOWED verdict if it is asked."""
    ctx["env"].set_policy_approved()


@given("the LLM policy service is unavailable")
def given_policy_model_unavailable(ctx: dict) -> None:
    """The policy model fails if it is asked."""
    ctx["env"].set_policy_unavailable()


@given("the platform has an AI credential of its own")
def given_platform_ai_credential(ctx: dict) -> None:
    """A Gemini key in the platform's environment, on every transport."""
    ctx["env"].set_platform_ai_credential()


@given(
    parsers.parse(
        "ranking is applied (brief provided, ranking prompt configured) with the seller's AI configured {where}"
    )
)
def given_ranking_applied(ctx: dict, where: str) -> None:
    """The seller's ranking prompt and AI configuration; the When provides the brief."""
    _ranking_on(where)(ctx["env"])


@given(parsers.parse("ranking is configured but {failure}"))
def given_ranking_cannot_run(ctx: dict, failure: str) -> None:
    _RANKING_FAILURES[failure](ctx["env"])


def _score(ctx: dict, product_id: str, score: float) -> None:
    ctx.setdefault("relevance_scores", {})[product_id] = score
    ctx["env"].set_ranking_scores({product_id: score})


@given(parsers.parse("a product has relevance_score {score:f}"))
def given_product_score(ctx: dict, score: float) -> None:
    """Score "a product": the Background's one product, so the sentence names exactly one."""
    catalog = ctx["catalog"]
    assert len(catalog) == 1, f"'a product' is ambiguous in a catalog of {catalog}"
    _score(ctx, catalog[0], score)


@given(parsers.parse("a second product, after it in catalog order, has relevance_score {score:f}"))
def given_second_product_score(ctx: dict, score: float) -> None:
    product_id, name = _CATALOG[1]
    _add_catalog_product(ctx, product_id, name)
    _score(ctx, product_id, score)


# ── When steps ──────────────────────────────────────────────────────


@when("the system applies AI ranking")
@when("the Buyer Agent sends a get_products request with a brief")
@when("the Buyer Agent sends a get_products request with a non-compliant brief")
@when("the Buyer Agent sends a get_products request with any brief content")
@when("the Buyer Agent sends a get_products request with a compliant brief")
def when_get_products_with_brief(ctx: dict) -> None:
    """Dispatch get_products with a brief through the current transport.

    One brief for every wording: whether it is "non-compliant" or "compliant" is the
    scripted policy model's verdict (a Given), not a property of the text. It carries the
    env's nonce (``scripted_brief``), which is what the scripted verdict answers to over e2e.
    """
    dispatch_request(ctx, brief=ctx["env"].scripted_brief(_BRIEF))


# ── Then steps ──────────────────────────────────────────────────────


@then("the product should be excluded from results")
def then_product_excluded(ctx: dict) -> None:
    """The scored product is dropped, and nothing else is: products[] is the catalog without it."""
    (excluded,) = ctx["relevance_scores"]
    expected = [product_id for product_id in ctx["catalog"] if product_id != excluded]
    actual = wire_product_ids(ctx)
    assert actual == expected, f"Expected products[] {expected} ({excluded!r} excluded), got {actual}"


@then("the product should be included in results sorted by score descending")
def then_products_sorted_by_score(ctx: dict) -> None:
    """products[] is every scored product, highest score first.

    Every product the scenario scores is at or above the threshold, so all of them are
    expected. The scores must order the products differently from the catalog, or this
    passes whether or not ranking ran.
    """
    scores: dict[str, float] = ctx["relevance_scores"]
    expected = sorted(scores, key=scores.__getitem__, reverse=True)
    assert expected != ctx["catalog"], f"score order {expected} equals catalog order, so ranking is not observable"
    actual = wire_product_ids(ctx)
    assert actual == expected, f"Expected products[] sorted by score {expected}, got {actual}"


@then("products should be returned unranked with no threshold applied")
@then("the request should proceed to product discovery without policy check")
@then("the request should proceed (fail-open behavior)")
@then("the request should proceed with full product catalog")
def then_full_catalog_in_catalog_order(ctx: dict) -> None:
    """products[] is the whole catalog, in catalog order: nothing ranked, filtered or blocked."""
    expected = ctx["catalog"]
    actual = wire_product_ids(ctx)
    assert actual == expected, f"Expected the catalog {expected} in catalog order, got {actual}"
