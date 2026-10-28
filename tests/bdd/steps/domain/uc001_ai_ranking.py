"""Domain step definitions for get_products and the seller's own AI configuration (UC-001).

Grades where ranking (BR-RULE-005) and the advertising-policy gate get their AI
configuration: the SELLER's own ``ai_config`` / ``gemini_api_key`` columns. The tenant
setup Given ("a tenant is configured for product discovery") is reused from
``uc_get_products_inventory``, same env (``ProductEnv``), and the error Then is the
generic ``the response contains error code``.

Every Then reads the WIRE (``wire_field``), never ``env.mock`` call args. The one fact
with no wire surface, which of ``rank_products_async``'s two text arguments received
the seller's prompt and which received the buyer's brief, is graded inside the
scripted-ranking stub in ``tests/harness/product.py``, where the call lands.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from pytest_bdd import given, parsers, then, when

from tests.bdd.steps._outcome_helpers import wire_field
from tests.bdd.steps.generic._dispatch import dispatch_request
from tests.factories import PricingOptionFactory, ProductFactory

# The ai_config a seller writes through the Admin UI. "gemini" rather than the
# canonical "google" on purpose: it is the legacy spelling the UI stores, and
# production canonicalizes it (``canonicalize_google_provider``) before use, so
# the scenario exercises the shape sellers actually have in their database.
_TENANT_AI_CONFIG: dict[str, Any] = {
    "provider": "gemini",
    "model": "gemini-2.0-flash",
    "api_key": "tenant-owned-ranking-key",
}

# Where the seller's own configuration lives, by the Outline's <where> wording: the
# (ai_config, gemini_api_key) column pair each one writes.
_CONFIG_SOURCES: dict[str, tuple[dict[str, Any] | None, str | None]] = {
    "in ai_config": (_TENANT_AI_CONFIG, None),
    "in the legacy gemini_api_key column": (None, "tenant-owned-legacy-key"),
}

# Configurations reading the tenant's own column can produce and that cannot rank, by
# the Outline's <configuration> wording.
_UNUSABLE_CONFIGS: dict[str, dict[str, Any]] = {
    # is_ai_enabled() accepts it (provider, model and key all present), but the factory
    # has no explicit-API-key integration for this provider and refuses to build a model
    # that would authenticate with some other credential (AdCPConfigurationError).
    "an ai_config the model factory refuses to build": {
        "provider": "gateway/anthropic",
        "model": "claude-x",
        "api_key": "tenant-owned-ranking-key",
    },
    # settings.temperature is bounded to [0, 2] by ModelSettings, so the stored row does
    # not validate as a TenantAIConfig.
    "a stored ai_config that does not parse": {
        "provider": "gemini",
        "api_key": "tenant-owned-ranking-key",
        "settings": {"temperature": 99},
    },
}


def _rows(datatable: Sequence[Sequence[object]]) -> list[dict[str, str]]:
    """Gherkin table -> list of {header: cell} dicts, header row consumed."""
    headers = [str(cell) for cell in datatable[0]]
    return [{headers[i]: str(cell) for i, cell in enumerate(row)} for row in datatable[1:]]


def _wire_product_ids(ctx: dict) -> list[str]:
    """The product_ids of ``products[]`` in the order the buyer receives them."""
    products = wire_field(ctx, "products")
    return [product["product_id"] for product in products]


# ── Given steps ─────────────────────────────────────────────────────


@given("the seller's catalog contains, in catalog order:")
def given_catalog_in_order(ctx: dict, datatable: Sequence[Sequence[object]]) -> None:
    """Create the table's products, each with pricing so it survives conversion.

    "in catalog order" is a claim about production, not about this table:
    ``ProductRepository.list_all`` orders by ``product_id``. The assertion below
    keeps the Gherkin honest — write the rows out of order and the scenario fails
    here rather than silently grading a different baseline order.
    """
    tenant = ctx["tenant"]
    rows = _rows(datatable)
    product_ids = [row["product_id"] for row in rows]
    assert product_ids == sorted(product_ids), (
        f"the table claims catalog order but product_ids {product_ids} are not sorted; "
        "ProductRepository.list_all orders by product_id, so the table must too"
    )
    for row in rows:
        product = ProductFactory(tenant=tenant, product_id=row["product_id"], name=row["name"])
        PricingOptionFactory(product=product)


@given(parsers.parse('the seller configured AI ranking {where} with the prompt "{prompt}"'))
def given_ranking_with_own_config(ctx: dict, where: str, prompt: str) -> None:
    """The seller has a ranking prompt and an AI configuration of its own, in ``where``."""
    ai_config, gemini_api_key = _CONFIG_SOURCES[where]
    ctx["env"].set_tenant_ai_ranking(ai_config, prompt, gemini_api_key=gemini_api_key)


@given(parsers.parse('the seller configured AI ranking with {configuration} and the prompt "{prompt}"'))
def given_ranking_with_unusable_config(ctx: dict, configuration: str, prompt: str) -> None:
    """The seller asked for ranking, but its own configuration cannot produce a ranking."""
    ctx["env"].set_tenant_ai_ranking(_UNUSABLE_CONFIGS[configuration], prompt)


@given("the seller's AI is configured only in ai_config")
def given_ai_only_in_ai_config(ctx: dict) -> None:
    """The seller's AI configuration is in the column the Admin UI writes, and nowhere else."""
    ctx["env"].set_tenant_ai_config(_TENANT_AI_CONFIG)


@given("the seller's ai_config names a provider and a model but no API key")
def given_keyless_ai_config(ctx: dict) -> None:
    """The row the settings page stores when the seller leaves the key blank.

    The page tells the seller "AI features will be disabled" as it saves this, so it is
    not a configuration of the seller's own.
    """
    ctx["env"].set_tenant_ai_config({"provider": "gemini", "model": "gemini-2.0-flash"})


@given("the seller has advertising-policy checks enabled")
def given_policy_checks_enabled(ctx: dict) -> None:
    """``advertising_policy.enabled`` is set on the tenant."""
    ctx["env"].enable_policy_checks()


@given("the seller's policy model blocks the brief")
def given_policy_blocks(ctx: dict) -> None:
    """The policy model (an LLM, so scripted) returns a BLOCKED verdict if it is asked."""
    ctx["env"].set_policy_blocked("Brief violates the seller's advertising policy")


@given("the ranking model, called with that prompt and the buyer's brief, scores:")
def given_ranking_scores(ctx: dict, datatable: Sequence[Sequence[object]]) -> None:
    """Score every product deterministically instead of calling a real model.

    The step name states two obligations and the stub behind it grades both: the scores
    (which decide the expected order) and the ROLES of the model's two text arguments —
    the seller's ``product_ranking_prompt`` arrives as ``custom_prompt``, the buyer's
    brief as ``brief``. The second is graded inside ``ProductEnv.set_ranking_scores``
    rather than in a Then step, because a Then would have to read in-process call
    arguments; see that method for why the swap is otherwise invisible.
    """
    scores = {row["product_id"]: float(row["relevance_score"]) for row in _rows(datatable)}
    ctx["env"].set_ranking_scores(scores)


# ── When steps ──────────────────────────────────────────────────────


@when(parsers.parse('the buyer requests products with the brief "{brief}"'))
def when_request_products_with_brief(ctx: dict, brief: str) -> None:
    """Dispatch get_products with the buyer's brief through the current transport."""
    dispatch_request(ctx, brief=brief)


# ── Then steps ──────────────────────────────────────────────────────


@then("the response products are exactly, in order:")
def then_products_exactly_in_order(ctx: dict, datatable: Sequence[Sequence[object]]) -> None:
    """Assert products[] is exactly the listed ids, in the listed order."""
    expected = [row["product_id"] for row in _rows(datatable)]
    actual = _wire_product_ids(ctx)
    assert actual == expected, f"Expected products[] {expected}, got {actual}"


@then(parsers.parse('the response does not contain the product "{product_id}"'))
def then_product_absent(ctx: dict, product_id: str) -> None:
    """Assert a below-threshold product was dropped rather than merely sorted last."""
    actual = _wire_product_ids(ctx)
    assert product_id not in actual, f"Expected {product_id!r} to be dropped, but products[] is {actual}"
