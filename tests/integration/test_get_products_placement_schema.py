"""Stored placements state kind and mode, and get_products serves them as stored.

``core/placement.json`` requires ``kind``, ``placement_id`` and ``mode`` of a product
placement, and its allOf conditionals require ``name`` of a ``seller_inline`` one and
``publisher_domain`` of a ``publisher_ref`` one. Nothing fills a missing member when a row is
read: ``ck_products_placements_explicit`` refuses a row that omits one when it is written, and
revision 89442a2be9e1 brought the rows stored before the rule up to it.

The schema authority is the pinned spec's placement.json (``tests.helpers.pinned_schema``),
validated with draft-07 and full ``$ref`` resolution, not the SDK Pydantic model, which does
not encode the allOf conditionals.
"""

from __future__ import annotations

from typing import Any

import pytest
from jsonschema.validators import Draft7Validator
from sqlalchemy.exc import IntegrityError

from tests.factories import (
    PricingOptionFactory,
    PrincipalFactory,
    ProductFactory,
    StoredPlacementFactory,
    TenantFactory,
)
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
            f"Emitted placement is not valid against the pinned placement.json:\n{placement}\n{details}"
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


#: What the seller stores: one placement per kind, and both modes.
_STORED_PLACEMENTS = [
    StoredPlacementFactory(placement_id="homepage_atf", name="Homepage Above the Fold"),
    StoredPlacementFactory(placement_id="sidebar_btf", name="Sidebar", mode="included"),
    # publisher_ref takes its name from the publisher's catalog, so the row states none.
    {
        "kind": "publisher_ref",
        "placement_id": "pub_header",
        "publisher_domain": "publisher.example",
        "mode": "targetable",
    },
]


@pytest.fixture
def placement_product_env(integration_db):
    """ProductEnv with one product storing ``_STORED_PLACEMENTS``."""
    with ProductEnv(tenant_id="placement-schema-test", principal_id="test_principal") as env:
        tenant = TenantFactory(tenant_id="placement-schema-test")
        PrincipalFactory(tenant=tenant, principal_id="test_principal")
        product = ProductFactory(
            tenant=tenant,
            product_id="placement_product",
            name="Placement Product",
            description="Product carrying explicit placements",
            delivery_type="guaranteed",
            placements=_STORED_PLACEMENTS,
        )
        PricingOptionFactory(product=product, pricing_model="cpm", rate="15.00", is_fixed=True, currency="USD")
        env.set_policy_approved()
        env.set_ranking_disabled()
        yield env


@pytest.mark.parametrize("transport", [Transport.A2A, Transport.MCP, Transport.REST])
def test_stored_placements_are_served_as_stored_on_all_transports(placement_product_env, transport):
    """Every stored member reaches the wire unchanged, and every placement is schema-valid."""
    result = placement_product_env.call_via(transport, brief="display ads")
    assert result.is_success, f"{transport} get_products failed: {result.error}"

    served = [
        p.model_dump(mode="json", exclude_none=True) if hasattr(p, "model_dump") else p
        for product in result.payload.products
        for p in (product.placements or [])
    ]
    assert [p["placement_id"] for p in served] == [p["placement_id"] for p in _STORED_PLACEMENTS], (
        f"{transport}: served placements {served} are not the stored ones {_STORED_PLACEMENTS}"
    )
    for wire, row in zip(served, _STORED_PLACEMENTS, strict=True):
        assert {k: wire.get(k) for k in row} == row, f"{transport}: placement served as {wire}, stored as {row}"
        _assert_placement_schema_valid(wire)


@pytest.mark.parametrize(
    ("placement", "why"),
    [
        ({"placement_id": "p", "name": "P", "mode": "targetable"}, "no kind"),
        ({"kind": "seller_inline", "placement_id": "p", "name": "P"}, "no mode"),
        ({"kind": "seller_inline", "name": "P", "mode": "targetable"}, "no placement_id"),
        ({"kind": "seller_inline", "placement_id": "p", "mode": "targetable"}, "seller_inline without name"),
        ({"kind": "publisher_ref", "placement_id": "p", "mode": "targetable"}, "publisher_ref without domain"),
    ],
)
def test_a_placement_omitting_a_required_member_is_refused_when_written(integration_db, placement, why):
    """The write fails; nothing would fill the member in when the row is read."""
    with ProductEnv(tenant_id="placement-refusal-test", principal_id="test_principal") as env:
        tenant = TenantFactory(tenant_id="placement-refusal-test")
        with pytest.raises(IntegrityError, match="ck_products_placements_explicit"):
            ProductFactory(tenant=tenant, product_id="refused_placement", placements=[placement])
            env.get_session()


def test_a_complete_placement_beside_a_refused_one_does_not_save_the_row(integration_db):
    """Every element is graded: one complete placement does not admit an incomplete sibling.

    The control in the same shape: the complete placement alone is accepted.
    """
    complete = StoredPlacementFactory(placement_id="complete")
    with ProductEnv(tenant_id="placement-sibling-test", principal_id="test_principal") as env:
        tenant = TenantFactory(tenant_id="placement-sibling-test")
        ProductFactory(tenant=tenant, product_id="complete_only", placements=[complete])
        with pytest.raises(IntegrityError, match="ck_products_placements_explicit"):
            ProductFactory(
                tenant=tenant,
                product_id="complete_and_bare",
                placements=[complete, {"placement_id": "bare", "name": "Bare"}],
            )
            env.get_session()
