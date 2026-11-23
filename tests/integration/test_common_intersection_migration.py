"""Revision 7a74ba340f11 narrows a multi-publisher product's format options to their common intersection.

``core/product.json`` ``format_options`` (v3.2.7): a product of several publishers that omits
``placements[]`` "MUST therefore be the common intersection accepted across every selected
publisher/property scope". A publisher-scoped declaration is outside it, so the revision
keeps the product-local declarations, where the contract is stored: on the product, or on the
inventory profile that supplies it. An empty intersection is left as stored.

Runs against a MIGRATED database (``migration_db``), at the revision before this one, with
rows inserted as that schema stores them.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.integration.migration_helpers import (
    reset_to_revision,
    run_alembic_upgrade,
    seed_inventory_profile,
    seed_product,
    stored_column,
)

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

_REVISION = "7a74ba340f11"
_PREVIOUS = "c4e7a2b9d1f0"
_TENANT = "mig_intersection"

_LOCAL = {"format_kind": "image", "format_option_id": "mrec", "params": {"width": 300, "height": 250}}
_SCOPED = {
    "format_kind": "image",
    "format_option_id": "leaderboard",
    "params": {"width": 728, "height": 90},
    "publisher_domain": "first-psatest.com",
}
_TWO = [{"publisher_domain": d, "selection_type": "all"} for d in ("first-psatest.com", "second-psatest.com")]
_PLACEMENT = [{"kind": "seller_inline", "mode": "included", "placement_id": "hero", "name": "Hero"}]


def _insert_profile(engine, profile_id: str, format_options: Any, publisher_properties: Any) -> int:
    return seed_inventory_profile(
        engine,
        tenant_id=_TENANT,
        profile_id=profile_id,
        format_options=format_options,
        publisher_properties=publisher_properties,
    )


def _insert_product(
    engine, product_id: str, format_options: Any, properties: Any, *, placements: Any = None, profile: int | None = None
) -> None:
    seed_product(
        engine,
        tenant_id=_TENANT,
        product_id=product_id,
        inventory_profile_id=profile,
        format_ids=None,
        format_options=format_options,
        properties=properties,
        property_tags=None if properties is not None else ["all_inventory"],
        placements=placements,
    )


def test_the_upgrade_keeps_the_common_intersection_where_the_contract_is_stored(migration_db):
    """Each row differs from ``two_publishers`` in one thing, and only the violating contracts change."""
    engine, db_url = reset_to_revision(
        migration_db, revision=_PREVIOUS, tenant_id=_TENANT, tenant_name="Intersection", subdomain="mig-intersection"
    )
    _insert_product(engine, "two_publishers", [_LOCAL, _SCOPED], _TWO)
    _insert_product(engine, "one_publisher", [_LOCAL, _SCOPED], _TWO[:1])
    _insert_product(engine, "with_placements", [_LOCAL, _SCOPED], _TWO, placements=_PLACEMENT)
    _insert_product(engine, "only_scoped", [_SCOPED], _TWO)
    shared = _insert_profile(engine, "shared", [_LOCAL, _SCOPED], _TWO)
    _insert_product(engine, "linked", None, None, profile=shared)
    legacy_profile = _insert_profile(engine, "legacy_profile", None, _TWO)
    _insert_product(engine, "linked_to_legacy_profile", [_LOCAL, _SCOPED], None, profile=legacy_profile)

    run_alembic_upgrade(db_url, _REVISION)

    products = {
        product_id: stored_column(engine, "products", "format_options", product_id=product_id)
        for product_id in (
            "two_publishers",
            "one_publisher",
            "with_placements",
            "only_scoped",
            "linked_to_legacy_profile",
        )
    }
    assert products == {
        "two_publishers": [_LOCAL],
        "one_publisher": [_LOCAL, _SCOPED],
        "with_placements": [_LOCAL, _SCOPED],
        # An empty intersection has no row the pin admits; it is logged and left as stored.
        "only_scoped": [_SCOPED],
        # A profile with no canonical arm leaves the product's own declarations as the contract.
        "linked_to_legacy_profile": [_LOCAL],
    }
    assert stored_column(engine, "inventory_profiles", "format_options", id=shared) == [_LOCAL]
    assert stored_column(engine, "products", "format_options", product_id="linked") is None, (
        "the profile's product was rewritten instead"
    )
