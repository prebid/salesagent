"""get_products emits placements that validate against AdCP placement.json v3.1.1.

Regression for PR #1567 (adcp 5.7->6.6 bump). adcp 6.6 / spec
3.1.1 made Placement.kind and Placement.mode required and added an allOf
conditional: `if kind == "publisher_ref" then required: [publisher_domain]`
(and `if kind == "seller_inline" then required: [name]`). Legacy placement rows
stored before these fields existed carry `name`/`placement_id` but NOT
`publisher_domain`. src/core/product_conversion.py defaulted them to
`kind="publisher_ref"`, so the emitted placement object is schema-INVALID against
draft-07 (missing publisher_domain) even though the SDK's Pydantic model — which
does not encode the allOf conditional — accepts it.

The schema authority here is the placement.json bundled inside the pinned
adcp==6.6.0 package (`adcp/_schemas/3.1/core/placement.json`). It is byte-for-
semantics identical to the adcp repo's v3.1.1 tag (dist/schemas/3.1.1/core/
placement.json) — same `required`, same `anyOf`, same allOf conditionals — and
is frozen with the package, so validation is deterministic and offline. This is
the JSON schema artifact, not the SDK Pydantic model (which is not authoritative).
"""

from __future__ import annotations

from typing import Any

import pytest
from jsonschema.validators import Draft7Validator

from tests.factories import PricingOptionFactory, PrincipalFactory, ProductFactory, TenantFactory
from tests.harness.product import ProductEnv
from tests.harness.transport import Transport
from tests.helpers import pinned_schema

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


def _placement_validator() -> Draft7Validator:
    """Draft-07 validator for the pinned spec version's placement schema, with full $ref resolution.

    placement.json refs 6 sibling schemas (format-id.json, product-format-declaration.json,
    and 4 enum files under ../enums/) — delegating to tests.helpers.pinned_schema (this
    repo's single source of truth for pinned-schema resolution) wires those refs through
    a real registry instead of constructing a bare, ref-blind Draft7Validator.
    """
    return pinned_schema.validator_for("core/placement.json")


def _assert_placement_schema_valid(placement: dict[str, Any]) -> None:
    validator = _placement_validator()
    errors = sorted(validator.iter_errors(placement), key=lambda e: list(e.absolute_path))
    if errors:
        details = "\n".join(
            f"  at {'.'.join(str(p) for p in e.absolute_path) or '<root>'}: {e.message}" for e in errors
        )
        raise AssertionError(
            f"Emitted placement is not valid against adcp 3.1.1 placement.json:\n{placement}\n{details}"
        )


def test_placement_validator_resolves_format_ids_ref():
    """A placement with format_ids populated actually exercises placement.json's $ref to
    format-id.json — a bare, registry-less Draft7Validator only ever "passed" placements
    that happened not to populate any of the 6 sibling-schema refs (format_ids,
    format_options, video/audio/sponsored/social placement types); this pins that the ref
    is genuinely resolved and enforced, not silently skipped.
    """
    _assert_placement_schema_valid(
        {
            "kind": "seller_inline",
            "placement_id": "homepage_atf",
            "mode": "targetable",
            "name": "Homepage Above the Fold",
            "format_ids": [{"agent_url": "https://example.com", "id": "display_300x250"}],
        }
    )
    with pytest.raises(AssertionError):
        # Missing format-id.json's required "id" — must be caught, not waved through
        # by an unresolved $ref degrading to an always-valid schema.
        _assert_placement_schema_valid(
            {
                "kind": "seller_inline",
                "placement_id": "homepage_atf",
                "mode": "targetable",
                "name": "Homepage Above the Fold",
                "format_ids": [{"agent_url": "https://example.com"}],
            }
        )


@pytest.fixture
def legacy_placement_product_env(integration_db):
    """ProductEnv with one product whose placement is a legacy row (no kind/mode/publisher_domain)."""
    with ProductEnv(tenant_id="placement-schema-test", principal_id="test_principal") as env:
        tenant = TenantFactory(tenant_id="placement-schema-test")
        PrincipalFactory(tenant=tenant, principal_id="test_principal")
        product = ProductFactory(
            tenant=tenant,
            product_id="legacy_placement_product",
            name="Legacy Placement Product",
            description="Product carrying a legacy placement row",
            delivery_type="guaranteed",
            # Legacy placement: pre-3.1.1 shape — no kind, no mode, no publisher_domain.
            placements=[{"placement_id": "homepage_atf", "name": "Homepage Above the Fold"}],
        )
        PricingOptionFactory(product=product, pricing_model="cpm", rate="15.00", is_fixed=True, currency="USD")
        env.set_policy_approved()
        env.set_ranking_disabled()
        yield env


@pytest.mark.parametrize("transport", [Transport.IMPL, Transport.A2A, Transport.MCP, Transport.REST])
def test_legacy_placement_is_schema_valid_on_all_transports(legacy_placement_product_env, transport):
    """Every emitted placement validates against adcp 3.1.1 placement.json, on all transports."""
    result = legacy_placement_product_env.call_via(transport, brief="display ads")
    assert result.is_success, f"{transport} get_products failed: {result.error}"

    products = result.payload.products
    placements = [
        p.model_dump(mode="json") if hasattr(p, "model_dump") else p
        for product in products
        for p in (product.placements or [])
    ]
    assert placements, f"{transport}: expected at least one emitted placement to validate"
    for placement in placements:
        _assert_placement_schema_valid(placement)


@pytest.fixture
def nameless_legacy_placement_product_env(integration_db):
    """ProductEnv with a legacy placement row MISSING name (carries only placement_id).

    seller_inline requires only ``name`` (placement.json 3.1.1 allOf), so the
    seller_inline default assumed legacy rows carry it. This fixture covers the
    other legacy shape: the conversion's defined fallback derives ``name`` from
    ``placement_id`` rather than silently emitting a schema-invalid placement
    (PR #1567 round-2 cleanup).
    """
    with ProductEnv(tenant_id="placement-noname-test", principal_id="test_principal") as env:
        tenant = TenantFactory(tenant_id="placement-noname-test")
        PrincipalFactory(tenant=tenant, principal_id="test_principal")
        product = ProductFactory(
            tenant=tenant,
            product_id="nameless_placement_product",
            name="Nameless Placement Product",
            description="Product carrying a legacy placement row without a name",
            delivery_type="guaranteed",
            # Legacy shape variant: placement_id only — no name, no kind/mode/publisher_domain.
            placements=[{"placement_id": "sidebar_btf"}],
        )
        PricingOptionFactory(product=product, pricing_model="cpm", rate="15.00", is_fixed=True, currency="USD")
        env.set_policy_approved()
        env.set_ranking_disabled()
        yield env


@pytest.mark.parametrize("transport", [Transport.IMPL, Transport.A2A, Transport.MCP, Transport.REST])
def test_nameless_legacy_placement_gets_fallback_name_and_is_schema_valid(
    nameless_legacy_placement_product_env, transport
):
    """A legacy placement missing ``name`` emits a schema-valid seller_inline placement.

    The conversion's defined fallback sets name := placement_id (never a silent
    schema-invalid emission). Asserts the exact fallback value AND full
    placement.json validity on every transport.
    """
    result = nameless_legacy_placement_product_env.call_via(transport, brief="display ads")
    assert result.is_success, f"{transport} get_products failed: {result.error}"

    placements = [
        p.model_dump(mode="json") if hasattr(p, "model_dump") else p
        for product in result.payload.products
        for p in (product.placements or [])
    ]
    assert placements, f"{transport}: expected the nameless legacy placement to be emitted"
    for placement in placements:
        assert placement.get("name") == "sidebar_btf", (
            f"{transport}: fallback must derive name from placement_id, got {placement.get('name')!r}"
        )
        _assert_placement_schema_valid(placement)
