"""GAMInventoryService.suggest_inventory_for_product sizes a product from its format contract.

A product linked to an inventory profile is served the profile's declarations
(``Product.format_contract``), the contract get_products announces and create_media_buy
grades, so the ad-unit scorer must size it from them and not from the product's own arms.
"""

from __future__ import annotations

import pytest

from src.services.gam_inventory_service import GAMInventoryService
from tests.factories import GAMInventoryFactory, InventoryProfileFactory, ProductFactory, TenantFactory
from tests.harness._base import IntegrationEnv

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


def _ad_unit(tenant, inventory_id: str, width: int, height: int) -> None:
    """An active ad unit of one size, whose name matches no product keyword."""
    GAMInventoryFactory(
        tenant=tenant,
        inventory_id=inventory_id,
        name=f"unit {inventory_id}",
        path=[f"unit {inventory_id}"],
        inventory_metadata={"sizes": [{"width": width, "height": height}]},
    )


def test_a_profile_linked_product_is_sized_from_its_profiles_declarations(integration_db):
    """The product's own declaration names 300x250 and its profile's names 728x90.

    Only the 728x90 unit is a size match. A scorer reading the product's own columns
    matches the 300x250 unit instead.
    """
    with IntegrationEnv() as env:
        tenant = TenantFactory(tenant_id="test-gam-suggest-profile", subdomain="gam-suggest-profile")
        profile = InventoryProfileFactory(
            tenant=tenant,
            format_ids=None,
            format_options=[{"format_kind": "image", "params": {"width": 728, "height": 90}}],
        )
        product = ProductFactory(
            tenant=tenant,
            product_id="suggest_profile_linked",
            name="qqq",
            description="",
            inventory_profile_id=profile.id,
            format_options=[{"format_kind": "image", "params": {"width": 300, "height": 250}}],
        )
        _ad_unit(tenant, "au_leaderboard", 728, 90)
        _ad_unit(tenant, "au_mrec", 300, 250)
        env._commit_factory_data()

        suggestions = GAMInventoryService(env._session).suggest_inventory_for_product(
            tenant.tenant_id, product.product_id
        )

    size_matches = {
        suggestion["inventory"]["id"]: [reason for reason in suggestion["reasons"] if reason.startswith("Size match")]
        for suggestion in suggestions
    }
    assert size_matches == {"au_leaderboard": ["Size match: 728x90"]}
