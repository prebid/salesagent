"""Tests for the redesigned ``list_inventory_profiles`` view.

Covers the data shape the new template depends on:

* ``coverage`` payload (bundles count, ad units bundled/total, placements
  bundled/total, products composed)
* ``bundles`` list with the enriched per-card fields
* ``unbundled_items`` list (synced GAMInventory rows not in any
  ``InventoryBundleReference``)
* GAM vs non-GAM branches (coverage + unbundled rail are GAM-only today)
"""

from __future__ import annotations

import pytest

from src.admin.app import create_app
from src.admin.blueprints.inventory_profiles import (
    _build_bundle_card,
    _build_coverage_summary,
    _list_unbundled_inventory,
)
from src.services.inventory_bundle_reference_sync import recompute_bundle_references
from tests.factories import (
    GAMInventoryFactory,
    InventoryProfileFactory,
    TenantFactory,
)

pytestmark = pytest.mark.requires_db


@pytest.fixture(autouse=True)
def _flask_request_context():
    """``url_for`` in the blueprint needs a request context."""
    app = create_app({"TESTING": True, "SECRET_KEY": "test", "WTF_CSRF_ENABLED": False})
    with app.test_request_context():
        yield


class TestBuildBundleCard:
    """``_build_bundle_card`` shapes one ``InventoryProfile`` for the template."""

    def test_minimal_profile(self, factory_session):
        tenant = TenantFactory()
        profile = InventoryProfileFactory(
            tenant=tenant,
            tenant_id=tenant.tenant_id,
            name="Premium",
            description="news",
        )

        card = _build_bundle_card(profile, product_count=0)

        assert card["name"] == "Premium"
        assert card["description"] == "news"
        assert card["products_using"] == 0

    def test_ad_unit_and_placement_counts(self, factory_session):
        tenant = TenantFactory()
        profile = InventoryProfileFactory(
            tenant=tenant,
            tenant_id=tenant.tenant_id,
            inventory_config={"ad_units": ["a", "b", "c"], "placements": ["p1"], "include_descendants": True},
        )

        card = _build_bundle_card(profile, product_count=0)

        assert card["ad_unit_count"] == 3
        assert card["placement_count"] == 1

    def test_property_tags_collected_and_deduped(self, factory_session):
        tenant = TenantFactory()
        profile = InventoryProfileFactory(
            tenant=tenant,
            tenant_id=tenant.tenant_id,
            publisher_properties=[
                {"publisher_domain": "a.com", "property_tags": ["news", "sports"], "selection_type": "by_tag"},
                {"publisher_domain": "b.com", "property_tags": ["sports"], "selection_type": "by_tag"},
            ],
        )

        card = _build_bundle_card(profile, product_count=0)

        assert card["property_mode"] == "tag"
        assert card["property_tags"] == ["news", "sports"]

    def test_property_id_mode(self, factory_session):
        tenant = TenantFactory()
        profile = InventoryProfileFactory(
            tenant=tenant,
            tenant_id=tenant.tenant_id,
            publisher_properties=[
                {"publisher_domain": "a.com", "property_ids": ["p1", "p2"], "selection_type": "by_id"},
            ],
        )

        card = _build_bundle_card(profile, product_count=0)

        assert card["property_mode"] == "ids"
        assert card["property_id_count"] == 2

    def test_products_using_passed_through(self, factory_session):
        tenant = TenantFactory()
        profile = InventoryProfileFactory(tenant=tenant, tenant_id=tenant.tenant_id)

        card = _build_bundle_card(profile, product_count=4)

        assert card["products_using"] == 4


class TestBuildCoverageSummary:
    """Coverage strip numbers come from GAMInventory + InventoryBundleReference."""

    def test_empty_tenant_returns_zeros(self, factory_session):
        tenant = TenantFactory()

        cov = _build_coverage_summary(factory_session, tenant.tenant_id, bundles_data=[])

        assert cov == {
            "bundles": 0,
            "adUnitsBundled": 0,
            "adUnitsTotal": 0,
            "placementsBundled": 0,
            "placementsTotal": 0,
            "productsComposed": 0,
        }

    def test_counts_reflect_synced_inventory_and_bundle_references(self, factory_session):
        tenant = TenantFactory(ad_server="google_ad_manager")
        # 3 synced ad units, 1 placement
        for inv_id in ("1", "2", "3"):
            GAMInventoryFactory(
                tenant=tenant, tenant_id=tenant.tenant_id, inventory_type="ad_unit", inventory_id=inv_id
            )
        GAMInventoryFactory(tenant=tenant, tenant_id=tenant.tenant_id, inventory_type="placement", inventory_id="p1")

        # Bundle that references 2 of the ad units + the placement
        InventoryProfileFactory(
            tenant=tenant,
            tenant_id=tenant.tenant_id,
            inventory_config={"ad_units": ["1", "2"], "placements": ["p1"], "include_descendants": True},
        )
        recompute_bundle_references(factory_session, tenant.tenant_id)
        factory_session.flush()

        bundles_data = [{"products_using": 2}]
        cov = _build_coverage_summary(factory_session, tenant.tenant_id, bundles_data=bundles_data)

        assert cov["bundles"] == 1
        assert cov["adUnitsBundled"] == 2
        assert cov["adUnitsTotal"] == 3
        assert cov["placementsBundled"] == 1
        assert cov["placementsTotal"] == 1
        assert cov["productsComposed"] == 2


class TestUnbundledInventory:
    """The ``What's not bundled`` rail rows."""

    def test_returns_synced_inventory_not_in_any_bundle(self, factory_session):
        tenant = TenantFactory(ad_server="google_ad_manager")
        GAMInventoryFactory(
            tenant=tenant,
            tenant_id=tenant.tenant_id,
            inventory_type="ad_unit",
            inventory_id="bundled_unit",
            name="Bundled Unit",
        )
        GAMInventoryFactory(
            tenant=tenant,
            tenant_id=tenant.tenant_id,
            inventory_type="ad_unit",
            inventory_id="orphan_unit",
            name="Orphan Unit",
        )
        InventoryProfileFactory(
            tenant=tenant,
            tenant_id=tenant.tenant_id,
            inventory_config={"ad_units": ["bundled_unit"], "placements": [], "include_descendants": True},
        )
        recompute_bundle_references(factory_session, tenant.tenant_id)
        factory_session.flush()

        rows = _list_unbundled_inventory(factory_session, tenant.tenant_id, limit=50)

        names = [r["name"] for r in rows]
        assert names == ["Orphan Unit"]
        assert rows[0]["adapter_id"] == "orphan_unit"
        assert rows[0]["kind"] == "ad_unit"

    def test_limit_caps_the_list(self, factory_session):
        tenant = TenantFactory(ad_server="google_ad_manager")
        for i in range(10):
            GAMInventoryFactory(
                tenant=tenant,
                tenant_id=tenant.tenant_id,
                inventory_type="ad_unit",
                inventory_id=f"u{i:03d}",
                name=f"Unit {i:03d}",
            )

        rows = _list_unbundled_inventory(factory_session, tenant.tenant_id, limit=3)

        assert len(rows) == 3

    def test_other_entity_types_excluded(self, factory_session):
        """Only ad_unit + placement rows show in the rail — not custom_targeting_key, etc."""
        tenant = TenantFactory(ad_server="google_ad_manager")
        GAMInventoryFactory(
            tenant=tenant,
            tenant_id=tenant.tenant_id,
            inventory_type="custom_targeting_key",
            inventory_id="ck1",
            name="Custom Key",
        )

        rows = _list_unbundled_inventory(factory_session, tenant.tenant_id, limit=50)

        assert rows == []


# End-to-end route auth setup in test_client is brittle (the auth check
# inspects more than ``session["authenticated"]``). The data-shape helpers
# above are the load-bearing contract for the new template — manual browser
# verification confirms end-to-end render.
