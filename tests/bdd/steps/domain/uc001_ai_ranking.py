"""Domain step definitions for BR-RULE-005 AI product ranking (UC-001).

Grades where ranking's AI configuration comes from: the SELLER's own ``ai_config``
column, not the platform environment key. The tenant setup Given
("a tenant is configured for product discovery") is reused from
``uc_get_products_inventory`` — same env (``ProductEnv``), same tenant/principal
contract — so only the ranking-specific steps live here.

Every Then reads the WIRE (``wire_dict``/``wire_field``), never ``env.mock`` call
args: mock arguments are an in-process-only fact, and asserting on them would make
the e2e parameterization of these scenarios structurally impossible. The one fact
with no wire surface — which of ``rank_products_async``'s two text arguments received
the seller's prompt and which received the buyer's brief — is graded inside the
scripted-ranking stub in ``tests/harness/product.py``, where the call actually lands,
so it needs no Then step reading harness state.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from pytest_bdd import given, parsers, then, when

from tests.bdd.steps._outcome_helpers import wire_dict, wire_field
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


def _rows(datatable: Sequence[Sequence[object]]) -> list[dict[str, str]]:
    """Gherkin table -> list of {header: cell} dicts, header row consumed."""
    headers = [str(cell) for cell in datatable[0]]
    return [{headers[i]: str(cell) for i, cell in enumerate(row)} for row in datatable[1:]]


def _wire_product_ids(ctx: dict) -> list[str]:
    """The product_ids of ``products[]`` in the order the buyer receives them."""
    products = wire_field(ctx, "products")
    return [product["product_id"] for product in products]


def _advisories(ctx: dict) -> list[dict[str, Any]]:
    """``errors[]`` as it appears on the wire — absent and empty are the same thing.

    A completed get_products response may carry advisory errors[] (pinned
    ``media-buy/get-products-response.json`` requires the field only when
    ``status == "failed"``), and a response with nothing to advise about omits it
    entirely. Both mean "no advisory", so both normalize to [].
    """
    return wire_dict(ctx).get("errors") or []


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
    ctx["catalog_product_ids"] = product_ids


@given(parsers.parse('the seller configured AI ranking in ai_config with the prompt "{prompt}"'))
def given_ranking_via_ai_config(ctx: dict, prompt: str) -> None:
    """The seller has both a ranking prompt and their own AI configuration."""
    ctx["env"].set_tenant_ai_ranking(_TENANT_AI_CONFIG, prompt)


@given(parsers.parse('the seller configured AI ranking with no ai_config and the prompt "{prompt}"'))
def given_ranking_without_ai_config(ctx: dict, prompt: str) -> None:
    """The seller asked for ranking but configured no AI at all — the advisory case."""
    ctx["env"].set_tenant_ai_ranking(None, prompt)


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


@then("the response carries no advisory errors")
def then_no_advisory(ctx: dict) -> None:
    """Assert a successfully ranked response says nothing on errors[]."""
    advisories = _advisories(ctx)
    assert advisories == [], f"Expected no advisory errors, got {advisories}"


@then(parsers.parse('the response carries one advisory error with code "{code}"'))
def then_one_advisory_with_code(ctx: dict, code: str) -> None:
    """Assert exactly one advisory rides on the SUCCESS envelope, with the given code."""
    advisories = _advisories(ctx)
    assert len(advisories) == 1, f"Expected exactly 1 advisory error, got {advisories}"
    assert advisories[0]["code"] == code, f"Expected advisory code {code!r}, got {advisories[0]}"
    ctx["advisory"] = advisories[0]


@then(parsers.parse('the advisory error is a non-fatal warning about field "{field}"'))
def then_advisory_is_warning(ctx: dict, field: str) -> None:
    """Assert the advisory's severity/recovery/field, and that it did NOT fail the task.

    ``severity: warning`` plus the absence of an envelope-level error is the pinned
    ``core/protocol-envelope.json`` contract for a non-fatal advisory; ``terminal``
    tells the buyer no retry supplies the seller's missing API key; ``field`` points
    at what the advisory is about.
    """
    advisory = _advisories(ctx)[0]
    assert advisory["severity"] == "warning", f"Expected severity 'warning', got {advisory}"
    assert advisory["recovery"] == "terminal", f"Expected recovery 'terminal', got {advisory}"
    assert advisory["field"] == field, f"Expected field {field!r}, got {advisory}"
    assert "PRODUCT_RANKING_UNAVAILABLE" in advisory["message"], (
        f"Expected the advisory message to name PRODUCT_RANKING_UNAVAILABLE, got {advisory['message']!r}"
    )


@then("the response envelope still reports the task as successful")
def then_envelope_reports_success(ctx: dict) -> None:
    """An advisory rides a COMPLETED task — it does not turn the response into a failure.

    This grades the ENVELOPE, where the two assertions above grade the payload. Pinned
    ``core/protocol-envelope.json`` on ``adcp_error``: "a fatal task failure SHOULD
    populate both this envelope-level field AND the payload's ``errors[]`` array ...
    Non-fatal warnings populate ONLY ``payload.errors[]`` with ``severity: warning`` —
    the envelope MUST NOT carry ``adcp_error`` for non-failures."

    A2A is the transport this bites on. ``AdCPRequestHandler._stamp_a2a_protocol_fields``
    stamps a ``success`` marker on every ``get_products`` response and derived it from
    "is ``errors[]`` non-empty", so the moment this advisory existed a seller carrying it
    reported ``success=false`` on EVERY discovery call. Reverting that derivation left
    this whole feature green (6 passed) before this step existed.

    ``success`` is asserted only where the envelope actually carries it: REST and MCP
    stamp no such marker, so requiring the key everywhere would fail those two for a
    reason that has nothing to do with the advisory. The two assertions that DO run on
    every transport come first, so no transport reaches the end of this step having
    graded nothing.
    """
    wire = wire_dict(ctx)
    assert wire.get("status") == "completed", f"Expected a completed task, got status {wire.get('status')!r}"
    assert wire.get("adcp_error") is None, (
        f"A non-fatal advisory must leave the envelope's adcp_error unset, got {wire.get('adcp_error')!r}"
    )
    if "success" in wire:
        assert wire["success"] is True, (
            f"Expected success=True on a completed response whose only errors[] entry is a "
            f"severity 'warning' advisory, got {wire['success']!r} (advisory: {_advisories(ctx)})"
        )
