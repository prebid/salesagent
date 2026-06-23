"""Domain step definitions for get_products buying_mode (brief / wholesale / refine).

Covers UC-001-MODE-{BRIEF,WHOLESALE,REFINE,VALIDATION}-01. Steps assert on real
production output via ProductEnv (selected by the ``@buying_mode`` tag in conftest's
``_harness_env``). Dispatch goes through the shared ``_dispatch.dispatch_request``,
so each scenario runs against whatever transport conftest parametrizes; results land
in ``ctx["response"]`` (success) or ``ctx["error"]`` (rejection).

Generic steps are reused for status and array containment (then_success /
then_payload); only the mode-distinguishing assertions live here.
"""

from __future__ import annotations

import json
from typing import Any

from pytest_bdd import given, parsers, then, when

from src.core.helpers import enum_value
from tests.bdd.steps.generic._dispatch import dispatch_request
from tests.factories import (
    PrincipalFactory,
    TenantFactory,
    create_buying_mode_test_products,
)

# product_ids seeded by create_buying_mode_test_products — the curated catalog.
_CATALOG_PRODUCT_IDS = {"display_premium", "video_premium"}


def _parse_request_table(datatable: Any) -> dict[str, Any]:
    """Parse a Gherkin field/value table into get_products kwargs (JSON-shaped values parsed)."""
    headers = datatable[0]
    rows = [dict(zip(headers, row, strict=True)) for row in datatable[1:]]
    kwargs: dict[str, Any] = {}
    for row in rows:
        field, value = row.get("field"), row.get("value")
        if field is None or value is None:
            continue
        v: Any = value.strip()
        if v.startswith("{") or v.startswith("["):
            v = json.loads(v)
        kwargs[field] = v
    return kwargs


def _require_response(ctx: dict) -> Any:
    resp = ctx.get("response")
    assert resp is not None, f"Expected a response but got error: {ctx.get('error')!r}"
    return resp


# ── Given ───────────────────────────────────────────────────────────


@given("the buyer is authenticated for buying-mode discovery")
def given_authenticated_buyer(ctx: dict) -> None:
    """Create the tenant + principal matching the ProductEnv identity defaults.

    tenant_id matches ProductEnv's default ("test_tenant") so the env identity resolves;
    subdomain is hyphenated ("test-tenant") because publisher_domain is derived as
    f"{subdomain}.example.com" and must satisfy the AdCP domain pattern (no underscores).
    """
    tenant = TenantFactory(tenant_id="test_tenant", subdomain="test-tenant", ad_server="mock")
    PrincipalFactory(tenant=tenant, principal_id="test_principal")
    ctx["tenant"] = tenant


@given("the product catalog contains buying-mode test products")
def given_buying_mode_catalog(ctx: dict) -> None:
    """Seed the brief/wholesale/refine test products for the authenticated tenant."""
    create_buying_mode_test_products(ctx["tenant"])


# ── When ────────────────────────────────────────────────────────────


@when("the Buyer Agent sends a get_products request with:")
def when_send_get_products(ctx: dict, datatable: Any) -> None:
    """Dispatch get_products with the table's fields through the scenario's transport."""
    dispatch_request(ctx, **_parse_request_table(datatable))


# ── Then: response shape per mode ───────────────────────────────────


@then("the response includes the buying-mode catalog products")
def then_includes_catalog_products(ctx: dict) -> None:
    resp = _require_response(ctx)
    returned = {p.product_id for p in resp.products}
    assert _CATALOG_PRODUCT_IDS <= returned, (
        f"Expected catalog products {_CATALOG_PRODUCT_IDS} in response, got {returned}"
    )


@then("no product should include a brief_relevance value")
def then_no_brief_relevance(ctx: dict) -> None:
    """Assert the harness-default: every product's brief_relevance is None.

    This does NOT prove the wholesale-bypasses-ranker contract. The BDD harness runs
    with the AI ranker disabled (see the feature preamble), so brief_relevance is None
    in every mode here, not specifically because wholesale skipped ranking. The
    "wholesale bypasses the ranker" gate is pinned by the ranker unit tests and
    test_get_products_mode_branching.py. This step is the transport-observable floor:
    it reddens if any product ever carries a brief_relevance value under the disabled
    harness — catching a leak of ranker state into the wholesale path.
    """
    resp = _require_response(ctx)
    offenders = {p.product_id: p.brief_relevance for p in resp.products if p.brief_relevance is not None}
    assert not offenders, f"Wholesale products must not carry brief_relevance, got {offenders}"


@then('the response should NOT contain "proposals" array')
def then_no_proposals(ctx: dict) -> None:
    """Forward-guard: wholesale responses carry no proposals (a refine-mode concept).

    Per refinement.mdx ("Proposals in refine mode"), sellers MAY surface proposals
    in refine mode; wholesale/brief responses must not. No production path currently
    populates ``proposals`` on a get_products response, so this assertion does not
    discriminate today's behavior — it reddens only if a future change leaks a
    populated proposals array into a wholesale response. Retained because
    BR-UC-001-discover-available-inventory also relies on it for the wholesale shape.
    """
    resp = _require_response(ctx)
    proposals = getattr(resp, "proposals", None)
    assert proposals in (None, []), f"Expected no proposals array, got {proposals!r}"


@then("each refinement_applied entry should have a recognized status")
def then_refinement_status_recognized(ctx: dict) -> None:
    """Every refinement_applied entry carries status 'unable' — the documented behavior until
    proposal-state persistence lands. Pinning the concrete value (not just enum membership) keeps
    the assertion non-circular: it reddens if a future change emits 'applied'/'partial' before
    refinement is actually applied. The full per-transport contract lives in the integration sibling.
    """
    resp = _require_response(ctx)
    assert resp.refinement_applied, "refine response must carry refinement_applied entries"
    for entry in resp.refinement_applied:
        inner = getattr(entry, "root", entry)
        assert enum_value(inner.status) == "unable", (
            f"refinement status must be 'unable' until refinement application is implemented, "
            f"got {enum_value(inner.status)!r}"
        )


# ── Then: validation rejects ────────────────────────────────────────


@then(parsers.parse('the get_products request is rejected with error code "{code}"'))
def then_rejected_with_code(ctx: dict, code: str) -> None:
    """Cross-mode / v3 missing-mode violations reject with the given AdCP error code."""
    from src.core.exceptions import AdCPError

    error = ctx.get("error")
    assert error is not None, f"Expected rejection but got response: {ctx.get('response')!r}"
    assert isinstance(error, AdCPError), f"Expected AdCPError, got {type(error).__name__}: {error!r}"
    assert error.error_code == code, f"Expected error_code {code!r}, got {error.error_code!r}"
