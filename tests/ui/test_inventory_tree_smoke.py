"""
Smoke tests for inventory tree pages.

Verifies that initInventoryTree() is called correctly and pages render
without JS errors. These tests catch function-signature mismatches between
the tree partial and consumer templates.

Requires: running Docker stack with at least one tenant.
GAM-specific tests are skipped if the tenant uses a non-GAM adapter.
"""

import json
import os

import pytest
from playwright.sync_api import Page, expect
from sqlalchemy import create_engine, text

pytestmark = pytest.mark.ui


class TestInventoryPageLoads:
    """All inventory-related pages load without JS errors."""

    def test_inventory_unified_loads(self, authenticated_page: Page, base_url):
        page = authenticated_page
        page.goto(f"{base_url}/tenant/default/inventory")
        page.wait_for_load_state("networkidle")

        # Verify we landed on an inventory page (not redirected to login)
        url = page.url
        assert "/login" not in url, f"Redirected to login: {url}"

        # No JS errors
        assert page.js_errors == [], f"JS errors on inventory unified: {page.js_errors}"

    def test_inventory_browser_tree_loads_if_gam(self, authenticated_page: Page, base_url):
        """On GAM tenants, the Browse Inventory page renders the ad unit tree."""
        page = authenticated_page
        page.goto(f"{base_url}/tenant/default/inventory/browse")
        page.wait_for_load_state("networkidle")

        # Tree container should exist and get populated by initInventoryTree
        tree = page.locator("#adUnitTree")
        expect(tree).to_be_visible(timeout=5000)

        tree_content = page.locator(
            "#adUnitTree .inventory-tree-view, #adUnitTree .tree-empty, #adUnitTree .tree-loading"
        )
        expect(tree_content.first).to_be_visible(timeout=5000)

        assert page.js_errors == [], f"JS errors on browse inventory tab: {page.js_errors}"

    def test_products_page_loads(self, authenticated_page: Page, base_url):
        page = authenticated_page
        page.goto(f"{base_url}/tenant/default/products")
        page.wait_for_load_state("networkidle")

        assert page.js_errors == [], f"JS errors on products page: {page.js_errors}"


class TestInventoryPickerTree:
    """Inventory picker modal loads tree via initInventoryTree."""

    def test_add_product_page_loads(self, authenticated_page: Page, base_url):
        """The add product page loads without JS errors.

        This page includes the inventory picker component which includes
        the tree partial — verifies no undefined function errors on load.
        """
        page = authenticated_page

        page.goto(f"{base_url}/tenant/default/products/add")
        page.wait_for_load_state("networkidle")

        assert page.js_errors == [], f"JS errors on add product page: {page.js_errors}"

    def test_picker_opens_without_js_errors(self, authenticated_page: Page, base_url):
        """Open the inventory picker modal and verify no JS errors."""
        page = authenticated_page

        page.goto(f"{base_url}/tenant/default/products/add")
        page.wait_for_load_state("networkidle")

        # Open the ad unit picker
        browse_btn = page.locator("text=Browse Ad Units").first

        browse_btn.click()
        page.wait_for_timeout(1000)

        assert page.js_errors == [], f"JS errors on inventory picker: {page.js_errors}"


class TestInventoryBundleEditor:
    """Inventory bundle editor smoke tests."""

    def test_add_bundle_picker_opens_without_js_errors(self, authenticated_page: Page, base_url):
        """The redesigned add-bundle page renders tree + flat inventory pickers."""
        page = authenticated_page

        page.goto(f"{base_url}/tenant/default/inventory-profiles/add")
        page.wait_for_load_state("networkidle")

        expect(page.get_by_role("heading", name="Create inventory bundle")).to_be_visible(timeout=5000)
        picker = page.locator("#inventory-picker-list")
        expect(picker).to_be_visible(timeout=5000)
        expect(picker).to_contain_text("Smoke Test Placement", timeout=5000)
        expect(picker).to_contain_text("Smoke Test Ad Unit", timeout=5000)

        placement_checkbox = page.locator("input[data-toggle-placement='smoke-placement-001']")
        expect(placement_checkbox).to_have_attribute("aria-label", "Select Smoke Test Placement")
        placement_checkbox.focus()
        page.keyboard.press("Space")
        expect(page.locator("input[data-toggle-placement='smoke-placement-001']")).to_be_focused()
        expect(page.locator("#inventory-selected-strip")).to_contain_text("Smoke Test Placement", timeout=5000)
        expect(page.locator("#targeted_placement_ids")).to_have_value('["smoke-placement-001"]')

        page.get_by_role("button", name="Flat ad units").click()
        expect(picker).to_contain_text("Smoke Test Ad Unit", timeout=5000)
        ad_unit_checkbox = page.locator("input[data-toggle-ad-unit='smoke-au-001']")
        expect(ad_unit_checkbox).to_have_attribute("aria-label", "Select Smoke Test Ad Unit")
        ad_unit_checkbox.check()
        expect(page.locator("#inventory-selected-strip")).to_contain_text("Smoke Test Ad Unit", timeout=5000)
        expect(page.locator("#targeted_ad_unit_ids")).to_have_value('["smoke-au-001"]')

        assert page.js_errors == [], f"JS errors on bundle picker: {page.js_errors}"

    def test_edit_bundle_respects_saved_descendant_toggle(self, authenticated_page: Page, base_url):
        """The edit picker renders persisted include_descendants=false before first paint."""
        page = authenticated_page

        profile_pk = _seed_descendants_off_bundle()

        page.goto(f"{base_url}/tenant/default/inventory-profiles/{profile_pk}/edit")
        page.wait_for_load_state("networkidle")

        expect(page.locator("input[name='include_descendants']")).not_to_be_checked()
        expect(page.locator("#inventory-selected-strip")).to_contain_text("Smoke Test Placement", timeout=5000)
        expect(page.locator("#inventory-picker-help")).to_contain_text("targets only that placement")
        child = page.locator("input[data-toggle-ad-unit='smoke-au-001']")
        expect(child).to_be_visible(timeout=5000)
        expect(child).not_to_be_checked()
        expect(child).to_be_enabled()

        assert page.js_errors == [], f"JS errors on bundle edit picker: {page.js_errors}"


def _seed_descendants_off_bundle() -> int:
    """Insert a bundle through SQL for the Docker UI stack."""
    pg_port = os.environ["POSTGRES_PORT"]
    engine = create_engine(f"postgresql://adcp_user:secure_password_change_me@localhost:{pg_port}/adcp")
    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM inventory_profiles WHERE tenant_id = 'default' AND profile_id = 'ui_include_false'")
        )
        row = conn.execute(
            text(
                """
                INSERT INTO inventory_profiles
                    (tenant_id, profile_id, name, description, inventory_config,
                     format_ids, publisher_properties, targeting_template,
                     created_at, updated_at)
                VALUES
                    ('default', 'ui_include_false', 'UI Descendants Off', '',
                     CAST(:inventory_config AS jsonb),
                     CAST(:format_ids AS jsonb),
                     CAST(:publisher_properties AS jsonb),
                     NULL, NOW(), NOW())
                RETURNING id
                """
            ),
            {
                "inventory_config": json.dumps(
                    {
                        "ad_units": [],
                        "placements": ["smoke-placement-001"],
                        "include_descendants": False,
                    }
                ),
                "format_ids": json.dumps([]),
                "publisher_properties": json.dumps(
                    [{"publisher_domain": "example.com", "property_tags": ["all_inventory"]}]
                ),
            },
        ).one()
    engine.dispose()
    return int(row.id)
