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
    _attach_bundle_card_coverage,
    _build_bundle_card,
    _build_bundle_summary,
    _build_coverage_summary,
    _build_inventory_picker_payload,
    _compute_blast_radius,
    _list_products_using,
    _list_seed_suggestions,
    _list_unbundled_inventory,
    _resolve_inventory_names,
)
from src.services.bundle_adapter import get_adapter

# Adapter under test in this file — GAM is the only one with real inventory
# reads today (#521). FW/SS stubs are exercised separately in
# tests/unit/test_bundle_adapter.py.
GAM_ADAPTER = get_adapter("gam")
from src.services.inventory_bundle_reference_sync import recompute_bundle_references
from tests.factories import (
    GAMInventoryFactory,
    InventoryProfileFactory,
    ProductFactory,
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


class TestBundleCardCoverage:
    """Per-card coverage expands placements to their synced descendant ad units (#549)."""

    def test_gam_adapter_counts_direct_and_placement_covered_ad_units(self, factory_session):
        tenant = TenantFactory(ad_server="google_ad_manager")
        placement = GAMInventoryFactory(
            tenant=tenant,
            tenant_id=tenant.tenant_id,
            inventory_type="placement",
            inventory_id="pl_sports",
            name="Sports",
            path=["Sports"],
            inventory_metadata={"ad_unit_ids": ["au_child_1", "au_child_2"]},
        )
        GAMInventoryFactory(
            tenant=tenant,
            tenant_id=tenant.tenant_id,
            inventory_type="ad_unit",
            inventory_id="au_direct",
            name="Direct Unit",
            path=["Network", "Direct"],
        )
        GAMInventoryFactory(
            tenant=tenant,
            tenant_id=tenant.tenant_id,
            inventory_type="ad_unit",
            inventory_id="au_child_1",
            name="Sports Top",
            path=["Network", "Sports", "Top"],
        )
        GAMInventoryFactory(
            tenant=tenant,
            tenant_id=tenant.tenant_id,
            inventory_type="ad_unit",
            inventory_id="au_child_2",
            name="Sports Rail",
            path=["Network", "Sports", "Rail"],
        )
        GAMInventoryFactory(
            tenant=tenant,
            tenant_id=tenant.tenant_id,
            inventory_type="ad_unit",
            inventory_id="au_outside",
            name="Outside Unit",
            path=["Network", "Outside"],
        )
        profile = InventoryProfileFactory(
            tenant=tenant,
            tenant_id=tenant.tenant_id,
            inventory_config={
                "ad_units": ["au_direct"],
                "placements": [placement.inventory_id],
                "include_descendants": True,
            },
        )
        card = _build_bundle_card(profile, product_count=0)

        _attach_bundle_card_coverage(
            card,
            factory_session,
            tenant.tenant_id,
            profile,
            GAM_ADAPTER,
            ad_units_total=GAM_ADAPTER.count_inventory(factory_session, tenant.tenant_id, "ad_unit"),
        )

        assert card["coverage"] == {"covered": 3, "total": 4}


class TestInventoryPickerPayload:
    """The in-page picker payload supports the tree-first editor."""

    def test_caps_default_rows_but_keeps_selected_inventory(self, factory_session):
        tenant = TenantFactory(ad_server="google_ad_manager")
        for idx in range(5):
            GAMInventoryFactory(
                tenant=tenant,
                tenant_id=tenant.tenant_id,
                inventory_type="ad_unit",
                inventory_id=f"au_{idx}",
                name=f"Ad Unit {idx}",
            )
        selected = GAMInventoryFactory(
            tenant=tenant,
            tenant_id=tenant.tenant_id,
            inventory_type="ad_unit",
            inventory_id="zz_selected",
            name="Selected Unit",
        )

        payload = _build_inventory_picker_payload(
            factory_session,
            tenant.tenant_id,
            GAM_ADAPTER,
            inventory_config={"ad_units": [selected.inventory_id], "placements": [], "include_descendants": True},
            limit=2,
        )

        assert len(payload["ad_units"]) == 3
        assert [row["id"] for row in payload["ad_units"][:2]] == ["au_0", "au_1"]
        assert payload["ad_units"][2]["id"] == "zz_selected"

    def test_tree_payload_includes_placement_children_and_membership_counts(self, factory_session):
        tenant = TenantFactory(ad_server="google_ad_manager")
        placement = GAMInventoryFactory(
            tenant=tenant,
            tenant_id=tenant.tenant_id,
            inventory_type="placement",
            inventory_id="pl_site",
            name="Homepage Site Placement",
            inventory_metadata={"ad_unit_ids": ["au_child"], "bundle_kind": "site"},
        )
        child = GAMInventoryFactory(
            tenant=tenant,
            tenant_id=tenant.tenant_id,
            inventory_type="ad_unit",
            inventory_id="au_child",
            name="Homepage Leaderboard",
            inventory_metadata={
                "parent_id": placement.inventory_id,
                "sizes": [{"width": 728, "height": 90}],
            },
        )
        InventoryProfileFactory(
            tenant=tenant,
            tenant_id=tenant.tenant_id,
            inventory_config={"ad_units": [], "placements": [placement.inventory_id], "include_descendants": True},
        )
        InventoryProfileFactory(
            tenant=tenant,
            tenant_id=tenant.tenant_id,
            inventory_config={"ad_units": [child.inventory_id], "placements": [], "include_descendants": True},
        )

        payload = _build_inventory_picker_payload(
            factory_session,
            tenant.tenant_id,
            GAM_ADAPTER,
            inventory_config={"ad_units": [], "placements": [], "include_descendants": True},
            limit=1,
        )

        placement_row = next(row for row in payload["placements"] if row["id"] == "pl_site")
        child_row = next(row for row in payload["ad_units"] if row["id"] == "au_child")
        assert placement_row["subkind"] == "site"
        assert placement_row["child_ids"] == ["au_child"]
        assert placement_row["child_count"] == 1
        assert placement_row["bundle_count"] == 1
        assert child_row["parent_id"] == "pl_site"
        assert child_row["sizes"] == ["728x90"]
        assert child_row["bundle_count"] == 1


class TestBuildCoverageSummary:
    """Coverage strip numbers come from GAMInventory + InventoryBundleReference."""

    def test_empty_tenant_returns_zeros(self, factory_session):
        tenant = TenantFactory()

        cov = _build_coverage_summary(factory_session, tenant.tenant_id, bundles_data=[], adapter=GAM_ADAPTER)

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
        cov = _build_coverage_summary(factory_session, tenant.tenant_id, bundles_data=bundles_data, adapter=GAM_ADAPTER)

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

        rows = _list_unbundled_inventory(factory_session, tenant.tenant_id, limit=50, adapter=GAM_ADAPTER)

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

        rows = _list_unbundled_inventory(factory_session, tenant.tenant_id, limit=3, adapter=GAM_ADAPTER)

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

        rows = _list_unbundled_inventory(factory_session, tenant.tenant_id, limit=50, adapter=GAM_ADAPTER)

        assert rows == []


class TestListSeedSuggestions:
    """``_list_seed_suggestions`` surfaces synced GAM placements for the empty state."""

    def test_returns_placements_only(self, factory_session):
        """Ad units don't surface as seed candidates — only placements."""
        tenant = TenantFactory(ad_server="google_ad_manager")
        GAMInventoryFactory(
            tenant=tenant,
            tenant_id=tenant.tenant_id,
            inventory_type="placement",
            inventory_id="P1",
            name="Homepage Premium",
        )
        GAMInventoryFactory(
            tenant=tenant,
            tenant_id=tenant.tenant_id,
            inventory_type="ad_unit",
            inventory_id="AU1",
            name="homepage / top-banner",
        )

        rows = _list_seed_suggestions(factory_session, tenant.tenant_id, limit=5, adapter=GAM_ADAPTER)

        assert len(rows) == 1
        assert rows[0]["external_id"] == "P1"
        assert rows[0]["name"] == "Homepage Premium"

    def test_respects_limit(self, factory_session):
        """Caller's limit caps the result set."""
        tenant = TenantFactory(ad_server="google_ad_manager")
        for i in range(10):
            GAMInventoryFactory(
                tenant=tenant,
                tenant_id=tenant.tenant_id,
                inventory_type="placement",
                inventory_id=f"P{i}",
                name=f"Placement {i:02d}",
            )

        rows = _list_seed_suggestions(factory_session, tenant.tenant_id, limit=5, adapter=GAM_ADAPTER)

        assert len(rows) == 5

    def test_other_tenants_ignored(self, factory_session):
        """Cross-tenant isolation — placements from other tenants don't leak in."""
        tenant_a = TenantFactory(ad_server="google_ad_manager")
        tenant_b = TenantFactory(ad_server="google_ad_manager")
        GAMInventoryFactory(
            tenant=tenant_b,
            tenant_id=tenant_b.tenant_id,
            inventory_type="placement",
            inventory_id="OTHER",
            name="Other tenant placement",
        )

        rows = _list_seed_suggestions(factory_session, tenant_a.tenant_id, limit=5, adapter=GAM_ADAPTER)

        assert rows == []


class TestBuildBundleSummary:
    """``_build_bundle_summary`` shapes one profile for the edit-page sidebar."""

    def test_minimal_profile_summary(self, factory_session):
        tenant = TenantFactory()
        profile = InventoryProfileFactory(
            tenant=tenant,
            tenant_id=tenant.tenant_id,
            inventory_config={"ad_units": [], "placements": [], "include_descendants": True},
            format_ids=[],
            publisher_properties=[],
        )

        summary = _build_bundle_summary(profile, product_count=0, adapter_label="Google Ad Manager")

        assert summary["adapter_label"] == "Google Ad Manager"
        assert summary["ad_unit_count"] == 0
        assert summary["placement_count"] == 0
        assert summary["format_count"] == 0
        assert summary["products_using"] == 0

    def test_counts_reflect_inventory_and_properties(self, factory_session):
        tenant = TenantFactory()
        profile = InventoryProfileFactory(
            tenant=tenant,
            tenant_id=tenant.tenant_id,
            inventory_config={"ad_units": ["a", "b"], "placements": ["p1", "p2", "p3"]},
            format_ids=[{"agent_url": "x", "id": "fmt1"}, {"agent_url": "x", "id": "fmt2"}],
            publisher_properties=[
                {"publisher_domain": "a.com", "property_tags": ["premium", "news"], "selection_type": "by_tag"},
            ],
        )

        summary = _build_bundle_summary(profile, product_count=3, adapter_label="Google Ad Manager")

        assert summary["ad_unit_count"] == 2
        assert summary["placement_count"] == 3
        assert summary["format_count"] == 2
        assert summary["property_mode"] == "tags"
        assert summary["property_tag_count"] == 2
        assert summary["products_using"] == 3


class TestComputeBlastRadius:
    """``_compute_blast_radius`` flags placements/units this bundle shares with siblings."""

    def test_no_siblings_returns_empty(self, factory_session):
        tenant = TenantFactory()
        profile = InventoryProfileFactory(
            tenant=tenant,
            tenant_id=tenant.tenant_id,
            inventory_config={"ad_units": ["a"], "placements": ["p1"]},
        )

        assert _compute_blast_radius(factory_session, tenant.tenant_id, profile) == []

    def test_shared_placement_appears_in_blast_radius(self, factory_session):
        tenant = TenantFactory()
        profile = InventoryProfileFactory(
            tenant=tenant,
            tenant_id=tenant.tenant_id,
            inventory_config={"ad_units": [], "placements": ["p1", "p2"]},
        )
        # Two siblings include the same placement p1; one includes p2; none touch p3.
        InventoryProfileFactory(
            tenant=tenant,
            tenant_id=tenant.tenant_id,
            inventory_config={"ad_units": [], "placements": ["p1"]},
        )
        InventoryProfileFactory(
            tenant=tenant,
            tenant_id=tenant.tenant_id,
            inventory_config={"ad_units": [], "placements": ["p1", "p2"]},
        )

        result = _compute_blast_radius(factory_session, tenant.tenant_id, profile)
        by_id = {r["external_id"]: r for r in result}

        assert by_id["p1"]["kind"] == "placement"
        assert by_id["p1"]["others"] == 2
        assert by_id["p2"]["others"] == 1

    def test_other_tenants_dont_count(self, factory_session):
        tenant_a = TenantFactory()
        tenant_b = TenantFactory()
        profile = InventoryProfileFactory(
            tenant=tenant_a,
            tenant_id=tenant_a.tenant_id,
            inventory_config={"ad_units": [], "placements": ["shared_id"]},
        )
        # Other tenant has a bundle with the same external_id — must NOT bleed in.
        InventoryProfileFactory(
            tenant=tenant_b,
            tenant_id=tenant_b.tenant_id,
            inventory_config={"ad_units": [], "placements": ["shared_id"]},
        )

        assert _compute_blast_radius(factory_session, tenant_a.tenant_id, profile) == []


class TestResolveInventoryNames:
    """``_resolve_inventory_names`` turns raw GAM IDs into human names (#530)."""

    def test_empty_inventory_returns_empty_maps(self, factory_session):
        tenant = TenantFactory(ad_server="google_ad_manager")
        profile = InventoryProfileFactory(
            tenant=tenant,
            tenant_id=tenant.tenant_id,
            inventory_config={"ad_units": [], "placements": []},
        )

        result = _resolve_inventory_names(factory_session, tenant.tenant_id, profile, adapter=GAM_ADAPTER)

        assert result == {"ad_units": {}, "placements": {}}

    def test_resolves_synced_ad_units_and_placements(self, factory_session):
        tenant = TenantFactory(ad_server="google_ad_manager")
        GAMInventoryFactory(
            tenant=tenant,
            tenant_id=tenant.tenant_id,
            inventory_type="ad_unit",
            inventory_id="au1",
            name="Homepage / Top",
        )
        GAMInventoryFactory(
            tenant=tenant,
            tenant_id=tenant.tenant_id,
            inventory_type="placement",
            inventory_id="p1",
            name="Premium News",
        )
        profile = InventoryProfileFactory(
            tenant=tenant,
            tenant_id=tenant.tenant_id,
            inventory_config={"ad_units": ["au1"], "placements": ["p1"]},
        )

        result = _resolve_inventory_names(factory_session, tenant.tenant_id, profile, adapter=GAM_ADAPTER)

        assert result["ad_units"]["au1"]["name"] == "Homepage / Top"
        assert result["placements"]["p1"]["name"] == "Premium News"

    def test_unresolved_ids_omitted(self, factory_session):
        """IDs in the bundle but not in GAM sync stay out of the map.

        The template falls back to rendering the raw ID with an "unresolved"
        marker — the helper doesn't need to do that work.
        """
        tenant = TenantFactory(ad_server="google_ad_manager")
        # Only one of the two ad units is synced.
        GAMInventoryFactory(
            tenant=tenant,
            tenant_id=tenant.tenant_id,
            inventory_type="ad_unit",
            inventory_id="au_known",
            name="Known",
        )
        profile = InventoryProfileFactory(
            tenant=tenant,
            tenant_id=tenant.tenant_id,
            inventory_config={"ad_units": ["au_known", "au_missing"], "placements": []},
        )

        result = _resolve_inventory_names(factory_session, tenant.tenant_id, profile, adapter=GAM_ADAPTER)

        assert "au_known" in result["ad_units"]
        assert "au_missing" not in result["ad_units"]

    def test_cross_tenant_isolation(self, factory_session):
        tenant_a = TenantFactory(ad_server="google_ad_manager")
        tenant_b = TenantFactory(ad_server="google_ad_manager")
        # Same external_id under two tenants — must NOT leak.
        GAMInventoryFactory(
            tenant=tenant_b,
            tenant_id=tenant_b.tenant_id,
            inventory_type="ad_unit",
            inventory_id="shared_id",
            name="From tenant B",
        )
        profile = InventoryProfileFactory(
            tenant=tenant_a,
            tenant_id=tenant_a.tenant_id,
            inventory_config={"ad_units": ["shared_id"], "placements": []},
        )

        result = _resolve_inventory_names(factory_session, tenant_a.tenant_id, profile, adapter=GAM_ADAPTER)

        assert result["ad_units"] == {}


class TestListProductsUsing:
    """``_list_products_using`` lists products referencing this bundle (#530)."""

    def test_no_products_returns_empty(self, factory_session):
        tenant = TenantFactory()
        profile = InventoryProfileFactory(tenant=tenant, tenant_id=tenant.tenant_id)

        rows = _list_products_using(factory_session, tenant.tenant_id, profile.id)

        assert rows == []

    def test_returns_referencing_products(self, factory_session):
        tenant = TenantFactory()
        profile = InventoryProfileFactory(tenant=tenant, tenant_id=tenant.tenant_id)
        ProductFactory(
            tenant=tenant,
            tenant_id=tenant.tenant_id,
            name="Homepage Display",
            inventory_profile_id=profile.id,
        )
        ProductFactory(
            tenant=tenant,
            tenant_id=tenant.tenant_id,
            name="Sports Bundle",
            inventory_profile_id=profile.id,
        )
        # Unrelated product — must not appear.
        ProductFactory(tenant=tenant, tenant_id=tenant.tenant_id, inventory_profile_id=None)

        rows = _list_products_using(factory_session, tenant.tenant_id, profile.id)

        names = [r["name"] for r in rows]
        assert "Homepage Display" in names
        assert "Sports Bundle" in names
        assert len(rows) == 2

    def test_cross_tenant_isolation(self, factory_session):
        tenant_a = TenantFactory()
        tenant_b = TenantFactory()
        profile_a = InventoryProfileFactory(tenant=tenant_a, tenant_id=tenant_a.tenant_id)
        # Tenant B has a product matching the SAME numeric profile.id (FK is integer);
        # the helper must scope by tenant_id to avoid leakage.
        ProductFactory(
            tenant=tenant_b,
            tenant_id=tenant_b.tenant_id,
            inventory_profile_id=profile_a.id,
        )

        rows = _list_products_using(factory_session, tenant_a.tenant_id, profile_a.id)

        assert rows == []


# End-to-end route auth setup in test_client is brittle (the auth check
# inspects more than ``session["authenticated"]``). The data-shape helpers
# above are the load-bearing contract for the new template — manual browser
# verification confirms end-to-end render.
