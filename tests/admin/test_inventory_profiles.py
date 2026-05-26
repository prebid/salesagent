"""Integration tests for the inventory profiles admin blueprint.

Tests profile list, create, edit, and delete via Flask test client.
Requires PostgreSQL (integration_db fixture).
"""

import json
from pathlib import Path

import pytest
from sqlalchemy import delete, select
from werkzeug.datastructures import MultiDict

from src.admin.app import create_app
from src.core.database.database_session import get_db_session
from src.core.database.models import GAMInventory, InventoryProfile, Tenant
from tests.factories import AuthorizedPropertyFactory, GAMInventoryFactory, InventoryProfileFactory, TenantFactory
from tests.utils.database_helpers import create_tenant_with_timestamps

app = create_app()

pytestmark = [pytest.mark.admin, pytest.mark.requires_db]

_TENANT_ID = "inv_prof_test_tenant"


def test_inventory_profile_editor_derives_native_and_olv_formats_client_side():
    template = Path("templates/edit_inventory_profile.html").read_text()

    assert "slotKind === 'native'" in template
    assert "native_standard" in template
    assert "slotKind === 'olv'" in template
    assert "GAM_CANONICAL_VIDEO_FORMAT_IDS" in template


@pytest.fixture
def client():
    """Flask test client with test configuration."""
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    app.config["SESSION_COOKIE_PATH"] = "/"
    with app.test_client() as client:
        yield client


@pytest.fixture
def test_tenant(integration_db):
    """Create a test tenant for inventory profile tests."""
    with get_db_session() as session:
        try:
            session.execute(delete(InventoryProfile).where(InventoryProfile.tenant_id == _TENANT_ID))
            session.execute(delete(Tenant).where(Tenant.tenant_id == _TENANT_ID))
            session.commit()
        except Exception:
            session.rollback()

        tenant = create_tenant_with_timestamps(
            tenant_id=_TENANT_ID,
            name="Inventory Profile Test Tenant",
            subdomain="inv-prof-test",
            ad_server="mock",
            is_active=True,
        )
        session.add(tenant)
        session.commit()

    return _TENANT_ID


def _auth_session(client, tenant_id):
    """Set up authenticated session for test client."""
    with client.session_transaction() as sess:
        sess["authenticated"] = True
        sess["user"] = {"email": "test@example.com", "is_super_admin": True}
        sess["email"] = "test@example.com"
        sess["tenant_id"] = tenant_id
        sess["test_user"] = "test@example.com"
        sess["test_user_role"] = "super_admin"
        sess["test_user_name"] = "Test User"
        sess["test_tenant_id"] = tenant_id


def _create_sample_profile(tenant_id: str, name: str = "Sample Profile", profile_id: str = "sample_profile") -> int:
    """Create a sample inventory profile in the database. Returns the PK id."""
    from datetime import UTC, datetime

    with get_db_session() as session:
        profile = InventoryProfile(
            tenant_id=tenant_id,
            profile_id=profile_id,
            name=name,
            description="A sample profile for testing",
            inventory_config={"ad_units": [], "placements": [], "include_descendants": False},
            format_ids=[{"agent_url": "https://formats.example.com", "id": "display_300x250_image"}],
            publisher_properties=[
                {
                    "publisher_domain": f"{tenant_id}.example.com",
                    "property_tags": ["all_inventory"],
                    "selection_type": "by_tag",
                }
            ],
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        session.add(profile)
        session.commit()
        return profile.id


class TestInventoryProfileList:
    """Test the inventory profiles list page."""

    def test_list_returns_200(self, client, test_tenant):
        """GET /tenant/<tid>/inventory-profiles/ returns 200."""
        _auth_session(client, test_tenant)
        response = client.get(f"/tenant/{test_tenant}/inventory-profiles/")
        assert response.status_code == 200

    def test_list_shows_profile_names(self, client, test_tenant):
        """List page includes names of existing profiles."""
        _auth_session(client, test_tenant)
        _create_sample_profile(test_tenant, name="Visible Profile", profile_id="visible_profile")

        response = client.get(f"/tenant/{test_tenant}/inventory-profiles/")
        html = response.data.decode()
        assert "Visible Profile" in html


class TestInventoryProfileCreate:
    """Test inventory profile creation."""

    def test_create_form_returns_200(self, client, test_tenant):
        """GET /tenant/<tid>/inventory-profiles/add returns 200."""
        _auth_session(client, test_tenant)
        response = client.get(f"/tenant/{test_tenant}/inventory-profiles/add")
        assert response.status_code == 200
        html = response.data.decode()
        assert "Create inventory bundle" in html
        assert "Create bundle" in html
        assert "Summary" in html
        assert "Properties" in html
        assert "Publisher domain" in html
        assert "Add domain" in html
        assert "Domain-first AAO lookup" in html
        assert "all authorized properties" not in html
        assert 'type="hidden" name="publisher_domain[]"' in html

    def test_create_profile_with_tags_saves_to_db(self, client, test_tenant):
        """POST with valid tag-based config creates a profile."""
        _auth_session(client, test_tenant)
        response = client.post(
            f"/tenant/{test_tenant}/inventory-profiles/add",
            data={
                "name": "New Tag Profile",
                "profile_id": "new_tag_profile",
                "description": "Created via test",
                "targeted_ad_unit_ids": "[]",
                "targeted_placement_ids": "[]",
                "formats": json.dumps([{"agent_url": "https://formats.example.com", "id": "display_300x250_image"}]),
                "property_mode": "tags",
                "property_tags": "all_inventory",
            },
            follow_redirects=False,
        )
        # Redirect indicates success (profile saved)
        assert response.status_code in (302, 303)

        with get_db_session() as session:
            profile = session.scalars(
                select(InventoryProfile).where(
                    InventoryProfile.tenant_id == test_tenant,
                    InventoryProfile.profile_id == "new_tag_profile",
                )
            ).first()
        assert profile is not None
        assert profile.name == "New Tag Profile"

    def test_create_profile_derives_canonical_formats_from_selected_gam_inventory(self, client, factory_session):
        """Selected GAM sizes save as canonical parameterized display formats."""
        tenant = TenantFactory(ad_server="google_ad_manager")
        GAMInventoryFactory(
            tenant=tenant,
            tenant_id=tenant.tenant_id,
            inventory_id="au_formats",
            name="Format Source Ad Unit",
            inventory_metadata={
                "parent_id": None,
                "has_children": False,
                "sizes": [{"width": 300, "height": 250}, {"width": 728, "height": 90}],
            },
        )
        factory_session.commit()

        _auth_session(client, tenant.tenant_id)
        response = client.post(
            f"/tenant/{tenant.tenant_id}/inventory-profiles/add",
            data={
                "name": "Derived Format Bundle",
                "profile_id": "derived_format_bundle",
                "description": "Created via test",
                "targeted_ad_unit_ids": json.dumps(["au_formats"]),
                "targeted_placement_ids": "[]",
                "formats": "[]",
                "property_mode": "tags",
                "property_tags": "all_inventory",
            },
            follow_redirects=False,
        )
        assert response.status_code in (302, 303)

        with get_db_session() as session:
            profile = session.scalars(
                select(InventoryProfile).where(
                    InventoryProfile.tenant_id == tenant.tenant_id,
                    InventoryProfile.profile_id == "derived_format_bundle",
                )
            ).first()

        assert profile is not None
        assert {fmt["id"] for fmt in profile.format_ids} == {"display_image", "display_html", "display_js"}
        assert {(fmt["width"], fmt["height"]) for fmt in profile.format_ids} == {(300, 250), (728, 90)}
        assert all(not fmt["id"].startswith("display_300x250") for fmt in profile.format_ids)

    def test_create_profile_blocks_unclassified_gam_one_by_one_inventory(self, client, factory_session):
        """GAM 1x1 is special inventory and must be classified before bundling."""
        tenant = TenantFactory(ad_server="google_ad_manager")
        GAMInventoryFactory(
            tenant=tenant,
            tenant_id=tenant.tenant_id,
            inventory_id="au_special_1x1",
            name="Fluid Native Slot",
            inventory_metadata={
                "parent_id": None,
                "has_children": False,
                "sizes": [{"width": 1, "height": 1}],
            },
        )
        factory_session.commit()

        _auth_session(client, tenant.tenant_id)
        response = client.post(
            f"/tenant/{tenant.tenant_id}/inventory-profiles/add",
            data={
                "name": "Unclassified Special Bundle",
                "profile_id": "unclassified_special_bundle",
                "description": "Created via test",
                "targeted_ad_unit_ids": json.dumps(["au_special_1x1"]),
                "targeted_placement_ids": "[]",
                "formats": "[]",
                "property_mode": "tags",
                "property_tags": "all_inventory",
            },
            follow_redirects=False,
        )
        assert response.status_code in (302, 303)

        with get_db_session() as session:
            profile = session.scalars(
                select(InventoryProfile).where(
                    InventoryProfile.tenant_id == tenant.tenant_id,
                    InventoryProfile.profile_id == "unclassified_special_bundle",
                )
            ).first()

        assert profile is None

    def test_create_profile_derives_responsive_formats_from_classified_gam_one_by_one_inventory(
        self, client, factory_session
    ):
        """A classified GAM 1x1 slot derives responsive display formats."""
        tenant = TenantFactory(ad_server="google_ad_manager")
        GAMInventoryFactory(
            tenant=tenant,
            tenant_id=tenant.tenant_id,
            inventory_id="au_responsive_1x1",
            name="Responsive Display Slot",
            inventory_metadata={
                "parent_id": None,
                "has_children": False,
                "sizes": [{"width": 1, "height": 1}],
                "adcp_capabilities": {
                    "slot_kind": "responsive_display",
                    "render_modes": {"image": True, "html": True, "js": False, "vast": False},
                    "dimensions": {"min_width": 300, "max_width": 970, "min_height": 90, "max_height": 250},
                    "safeframe": "supported",
                    "special_size": {"kind": "responsive_display"},
                },
            },
        )
        factory_session.commit()

        _auth_session(client, tenant.tenant_id)
        response = client.post(
            f"/tenant/{tenant.tenant_id}/inventory-profiles/add",
            data={
                "name": "Responsive Special Bundle",
                "profile_id": "responsive_special_bundle",
                "description": "Created via test",
                "targeted_ad_unit_ids": json.dumps(["au_responsive_1x1"]),
                "targeted_placement_ids": "[]",
                "formats": "[]",
                "property_mode": "tags",
                "property_tags": "all_inventory",
            },
            follow_redirects=False,
        )
        assert response.status_code in (302, 303)

        with get_db_session() as session:
            profile = session.scalars(
                select(InventoryProfile).where(
                    InventoryProfile.tenant_id == tenant.tenant_id,
                    InventoryProfile.profile_id == "responsive_special_bundle",
                )
            ).first()

        assert profile is not None
        assert {fmt["id"] for fmt in profile.format_ids} == {"display_image", "display_html"}
        assert all("width" not in fmt and "height" not in fmt for fmt in profile.format_ids)
        assert {
            (fmt["min_width"], fmt["max_width"], fmt["min_height"], fmt["max_height"]) for fmt in profile.format_ids
        } == {(300, 970, 90, 250)}

    def test_create_profile_uses_placement_capabilities_for_child_gam_one_by_one_inventory(
        self, client, factory_session
    ):
        """Placement capability setup classifies child 1x1 ad units in that placement."""
        tenant = TenantFactory(ad_server="google_ad_manager")
        GAMInventoryFactory(
            tenant=tenant,
            tenant_id=tenant.tenant_id,
            inventory_id="au_child_1x1",
            name="Child Fluid Slot",
            inventory_metadata={
                "parent_id": "root",
                "has_children": False,
                "sizes": [{"width": 1, "height": 1}],
            },
        )
        GAMInventoryFactory(
            tenant=tenant,
            tenant_id=tenant.tenant_id,
            inventory_type="placement",
            inventory_id="pl_responsive",
            name="Responsive Placement",
            inventory_metadata={
                "ad_unit_ids": ["au_child_1x1"],
                "adcp_capabilities": {
                    "slot_kind": "responsive_display",
                    "render_modes": {"image": False, "html": True, "js": True, "vast": False},
                    "dimensions": {"min_width": 320, "max_width": 1280, "min_height": 50, "max_height": 600},
                    "safeframe": "required",
                    "special_size": {"kind": "responsive_display"},
                },
            },
        )
        factory_session.commit()

        _auth_session(client, tenant.tenant_id)
        response = client.post(
            f"/tenant/{tenant.tenant_id}/inventory-profiles/add",
            data={
                "name": "Placement Responsive Bundle",
                "profile_id": "placement_responsive_bundle",
                "description": "Created via test",
                "targeted_ad_unit_ids": "[]",
                "targeted_placement_ids": json.dumps(["pl_responsive"]),
                "include_descendants": "on",
                "formats": "[]",
                "property_mode": "tags",
                "property_tags": "all_inventory",
            },
            follow_redirects=False,
        )
        assert response.status_code in (302, 303)

        with get_db_session() as session:
            profile = session.scalars(
                select(InventoryProfile).where(
                    InventoryProfile.tenant_id == tenant.tenant_id,
                    InventoryProfile.profile_id == "placement_responsive_bundle",
                )
            ).first()

        assert profile is not None
        assert {fmt["id"] for fmt in profile.format_ids} == {"display_html", "display_js"}
        assert {
            (fmt["min_width"], fmt["max_width"], fmt["min_height"], fmt["max_height"]) for fmt in profile.format_ids
        } == {(320, 1280, 50, 600)}

    def test_create_profile_missing_name_redirects_without_creation(self, client, test_tenant):
        """POST without a name redirects back without creating a profile."""
        _auth_session(client, test_tenant)
        response = client.post(
            f"/tenant/{test_tenant}/inventory-profiles/add",
            data={
                "formats": json.dumps([{"id": "display_300x250_image"}]),
                "property_mode": "tags",
                "property_tags": "all_inventory",
            },
            follow_redirects=False,
        )
        assert response.status_code in (302, 303)

        with get_db_session() as session:
            count = len(
                list(session.scalars(select(InventoryProfile).where(InventoryProfile.tenant_id == test_tenant)).all())
            )
        assert count == 0

    def test_create_profile_missing_formats_redirects_without_creation(self, client, test_tenant):
        """POST without formats redirects back without creating a profile."""
        _auth_session(client, test_tenant)
        response = client.post(
            f"/tenant/{test_tenant}/inventory-profiles/add",
            data={
                "name": "No Formats Profile",
                "formats": "[]",
                "property_mode": "tags",
                "property_tags": "all_inventory",
            },
            follow_redirects=False,
        )
        assert response.status_code in (302, 303)

        with get_db_session() as session:
            prop = session.scalars(
                select(InventoryProfile).where(
                    InventoryProfile.tenant_id == test_tenant,
                    InventoryProfile.name == "No Formats Profile",
                )
            ).first()
        assert prop is None

    def test_create_form_seed_placement_prefills_picker_and_chip(self, client, factory_session):
        """GET /add?seed_placement=... preloads the placement selection (#546)."""
        tenant = TenantFactory(ad_server="google_ad_manager")
        GAMInventoryFactory(
            tenant=tenant,
            tenant_id=tenant.tenant_id,
            inventory_type="placement",
            inventory_id="pl_seed",
            name="Seed Placement",
            path=["Network", "Seed"],
        )
        factory_session.commit()

        _auth_session(client, tenant.tenant_id)
        response = client.get(f"/tenant/{tenant.tenant_id}/inventory-profiles/add?seed_placement=pl_seed")

        assert response.status_code == 200
        html = response.data.decode()
        assert "Create inventory bundle" in html
        assert "Seed Placement" in html
        assert "pl_seed" in html
        assert "INVENTORY_PICKER" in html

    def test_create_profile_with_multiple_domain_rows_saves_to_db(self, client, factory_session):
        """Add page supports the same multi-domain tag rows as edit (#546)."""
        tenant = TenantFactory()
        AuthorizedPropertyFactory(tenant=tenant, tenant_id=tenant.tenant_id, publisher_domain="sports.example.com")
        factory_session.commit()

        _auth_session(client, tenant.tenant_id)
        form_response = client.get(f"/tenant/{tenant.tenant_id}/inventory-profiles/add")
        form_html = form_response.data.decode()
        assert "Publisher domain" in form_html
        assert "Add domain" in form_html
        assert "Domain-first AAO lookup" in form_html

        response = client.post(
            f"/tenant/{tenant.tenant_id}/inventory-profiles/add",
            data=MultiDict(
                [
                    ("name", "New Multi Domain"),
                    ("profile_id", "new_multi_domain"),
                    ("description", "Created via redesigned add page"),
                    ("targeted_ad_unit_ids", "[]"),
                    ("targeted_placement_ids", '["pl_1"]'),
                    (
                        "formats",
                        json.dumps([{"agent_url": "https://formats.example.com", "id": "display_300x250_image"}]),
                    ),
                    ("property_mode", "tags"),
                    ("publisher_domain[]", tenant.primary_domain),
                    ("property_tags[]", "premium, news"),
                    ("publisher_domain[]", "sports.example.com"),
                    ("property_tags[]", "sports"),
                ]
            ),
            follow_redirects=False,
        )

        assert response.status_code in (302, 303)
        saved = factory_session.scalars(
            select(InventoryProfile).where(
                InventoryProfile.tenant_id == tenant.tenant_id,
                InventoryProfile.profile_id == "new_multi_domain",
            )
        ).first()
        assert saved is not None
        assert saved.inventory_config["placements"] == ["pl_1"]
        assert [p["publisher_domain"] for p in saved.publisher_properties] == [
            tenant.primary_domain,
            "sports.example.com",
        ]

    def test_create_profile_rejects_unauthorized_domain_row(self, client, factory_session):
        """Tampered add POST cannot persist an unowned publisher domain."""
        tenant = TenantFactory()
        factory_session.commit()

        _auth_session(client, tenant.tenant_id)
        response = client.post(
            f"/tenant/{tenant.tenant_id}/inventory-profiles/add",
            data=MultiDict(
                [
                    ("name", "Bad Domain"),
                    ("profile_id", "bad_domain"),
                    ("targeted_ad_unit_ids", "[]"),
                    ("targeted_placement_ids", "[]"),
                    (
                        "formats",
                        json.dumps([{"agent_url": "https://formats.example.com", "id": "display_300x250_image"}]),
                    ),
                    ("property_mode", "tags"),
                    ("publisher_domain[]", "attacker.example.com"),
                    ("property_tags[]", "premium"),
                ]
            ),
            follow_redirects=False,
        )

        assert response.status_code in (302, 303)
        saved = factory_session.scalars(
            select(InventoryProfile).where(
                InventoryProfile.tenant_id == tenant.tenant_id,
                InventoryProfile.profile_id == "bad_domain",
            )
        ).first()
        assert saved is None


class TestInventoryCapabilities:
    """Test the inventory capability setup layer."""

    def test_capabilities_form_renders_for_synced_inventory(self, client, factory_session):
        tenant = TenantFactory(ad_server="google_ad_manager")
        GAMInventoryFactory(
            tenant=tenant,
            tenant_id=tenant.tenant_id,
            inventory_id="au_capability_form",
            name="Capability Form Ad Unit",
            inventory_metadata={"sizes": [{"width": 1, "height": 1}]},
        )
        factory_session.commit()

        _auth_session(client, tenant.tenant_id)
        response = client.get(
            f"/tenant/{tenant.tenant_id}/inventory/capabilities/ad_unit/au_capability_form",
        )

        assert response.status_code == 200
        html = response.data.decode()
        assert "Inventory capabilities" in html
        assert "Capability Form Ad Unit" in html
        assert "1x1" in html

    def test_capabilities_form_saves_metadata(self, client, factory_session):
        tenant = TenantFactory(ad_server="google_ad_manager")
        GAMInventoryFactory(
            tenant=tenant,
            tenant_id=tenant.tenant_id,
            inventory_id="au_capability_save",
            name="Capability Save Ad Unit",
            inventory_metadata={"sizes": [{"width": 1, "height": 1}]},
        )
        factory_session.commit()

        _auth_session(client, tenant.tenant_id)
        response = client.post(
            f"/tenant/{tenant.tenant_id}/inventory/capabilities/ad_unit/au_capability_save",
            data={
                "slot_kind": "responsive_display",
                "min_width": "300",
                "max_width": "970",
                "min_height": "90",
                "max_height": "250",
                "render_image": "on",
                "render_html": "on",
                "safeframe": "supported",
                "safeframe_allow_overlay_expansion": "on",
                "notes": "Responsive display slot.",
            },
            follow_redirects=False,
        )
        assert response.status_code in (302, 303)

        with get_db_session() as session:
            row = session.scalars(
                select(GAMInventory).where(
                    GAMInventory.tenant_id == tenant.tenant_id,
                    GAMInventory.inventory_type == "ad_unit",
                    GAMInventory.inventory_id == "au_capability_save",
                )
            ).one()

        capabilities = row.inventory_metadata["adcp_capabilities"]
        assert capabilities["slot_kind"] == "responsive_display"
        assert capabilities["dimensions"] == {
            "min_width": 300,
            "max_width": 970,
            "min_height": 90,
            "max_height": 250,
        }
        assert capabilities["render_modes"] == {"image": True, "html": True, "js": False, "vast": False}
        assert capabilities["safeframe"] == "supported"
        assert capabilities["safeframe_config"] == {
            "allow_overlay_expansion": True,
            "allow_push_expansion": False,
        }


class TestInventoryProfileDelete:
    """Test inventory profile deletion."""

    def test_delete_profile_removes_from_db(self, client, test_tenant):
        """POST delete removes the profile from the database."""
        _auth_session(client, test_tenant)
        profile_pk = _create_sample_profile(test_tenant, name="Delete Me Profile", profile_id="delete_me_profile")

        response = client.post(
            f"/tenant/{test_tenant}/inventory-profiles/{profile_pk}/delete",
            follow_redirects=False,
        )
        assert response.status_code in (302, 303)

        with get_db_session() as session:
            profile = session.get(InventoryProfile, profile_pk)
        assert profile is None

    def test_delete_nonexistent_profile_via_post_flashes_and_redirects(self, client, test_tenant):
        """POST delete for a nonexistent profile flashes and redirects to the list."""
        _auth_session(client, test_tenant)
        response = client.post(
            f"/tenant/{test_tenant}/inventory-profiles/999999/delete",
            follow_redirects=False,
        )
        assert response.status_code in (302, 303)
        assert f"/tenant/{test_tenant}/inventory-profiles/" in response.headers.get("Location", "")

    def test_delete_nonexistent_profile_via_delete_returns_404(self, client, test_tenant):
        """DELETE for a nonexistent profile returns 404 JSON."""
        _auth_session(client, test_tenant)
        response = client.delete(
            f"/tenant/{test_tenant}/inventory-profiles/999999/delete",
        )
        assert response.status_code == 404


class TestInventoryProfileEdit:
    """Test the redesigned edit page renders with the new sidebar data."""

    def test_edit_get_renders_with_summary_and_blast_radius(self, client, test_tenant):
        """GET /<id>/edit returns 200 and includes the new sidebar cards."""
        _auth_session(client, test_tenant)
        pk = _create_sample_profile(test_tenant, name="Editable", profile_id="editable")
        response = client.get(f"/tenant/{test_tenant}/inventory-profiles/{pk}/edit")

        assert response.status_code == 200
        html = response.data.decode()
        # Sidebar cards
        assert "Summary" in html
        assert 'data-validator="description"' in html
        assert "Also in other bundles" in html
        # Section cards in main column
        assert "Basics" in html
        assert "Inventory" in html
        assert "Creative formats" in html
        # Sticky form bar
        assert "Save bundle" in html
        assert "beforeunload" in html
        assert "Saving..." in html
        assert "KNOWN_PROPERTY_TAGS" in html
        assert "Properties" in html
        assert "Publisher domain" in html
        assert "Domain-first AAO lookup" in html
        assert "Preview" in html  # action moved into formbar
        assert "Duplicate" in html  # action moved into formbar
        # Back link to list page
        assert "Back to Inventory bundles" in html

    def test_edit_get_exposes_reuse_base_url_for_chip_links(self, client, test_tenant):
        """Per-chip Reuse links (#542) need REUSE_BASE_URL in the inline JS.

        The chip render function builds links by appending ``?item=...&kind=...``
        to this constant. Confirms the template wires the URL correctly.
        """
        _auth_session(client, test_tenant)
        pk = _create_sample_profile(test_tenant, name="ChipReuse", profile_id="chip_reuse")
        response = client.get(f"/tenant/{test_tenant}/inventory-profiles/{pk}/edit")

        assert response.status_code == 200
        html = response.data.decode()
        assert "REUSE_BASE_URL" in html
        assert f"/tenant/{test_tenant}/inventory-profiles/reuse" in html

    def test_edit_get_embeds_inventory_picker_payload(self, client, factory_session):
        """The in-page picker (#545) renders synced GAM ad units + placements."""
        tenant = TenantFactory(ad_server="google_ad_manager")
        profile = InventoryProfileFactory(
            tenant=tenant,
            tenant_id=tenant.tenant_id,
            name="Picker Bundle",
            inventory_config={"ad_units": [], "placements": [], "include_descendants": True},
            publisher_properties=[
                {
                    "publisher_domain": tenant.primary_domain,
                    "property_tags": ["all_inventory"],
                    "selection_type": "by_tag",
                }
            ],
        )
        GAMInventoryFactory(
            tenant=tenant,
            tenant_id=tenant.tenant_id,
            inventory_type="ad_unit",
            inventory_id="au_picker",
            name="Homepage Top",
            path=["Network", "Homepage", "Top"],
            inventory_metadata={"sizes": [{"width": 1, "height": 1}]},
        )
        GAMInventoryFactory(
            tenant=tenant,
            tenant_id=tenant.tenant_id,
            inventory_type="placement",
            inventory_id="pl_picker",
            name="Homepage Placement",
            path=["Network", "Homepage"],
        )
        factory_session.commit()

        _auth_session(client, tenant.tenant_id)
        response = client.get(f"/tenant/{tenant.tenant_id}/inventory-profiles/{profile.id}/edit")

        assert response.status_code == 200
        html = response.data.decode()
        assert 'id="inventory-picker-list"' in html
        assert 'id="inventory-picker-mode"' in html
        assert "Flat ad units" in html
        assert "Browse inventory" in html
        assert "INVENTORY_PICKER" in html
        assert "INVENTORY_PICKER_LIMIT" in html
        assert "Homepage Top" in html
        assert "au_picker" in html
        assert '"needs_capability_setup":true' in html or '"needs_capability_setup": true' in html
        assert "Homepage Placement" in html
        assert "pl_picker" in html


class TestInventoryProfilePreview:
    """Preview surface — HTML at /preview, JSON at /api/preview (#531)."""

    def test_html_preview_renders_buyer_facing_shape(self, client, test_tenant):
        """GET /<id>/preview returns HTML rendering the bundle as a buyer sees it."""
        _auth_session(client, test_tenant)
        pk = _create_sample_profile(test_tenant, name="Buyer View Bundle", profile_id="buyer_view")

        response = client.get(f"/tenant/{test_tenant}/inventory-profiles/{pk}/preview")

        assert response.status_code == 200
        assert response.headers["Content-Type"].startswith("text/html")
        html = response.data.decode()
        # The buyer-facing card surfaces the bundle's user-visible fields.
        assert "Buyer View Bundle" in html
        assert "Accepted creative formats" in html
        assert "Publisher properties" in html
        assert "300x250" in html
        assert "display_300x250_image" not in html
        # Page framing makes the "as buyer sees it" intent clear.
        assert "This is what buyers see" in html
        assert "list_products" not in html
        # Back link to editor.
        assert f"/inventory-profiles/{pk}/edit" in html

    def test_html_preview_missing_bundle_redirects_to_list(self, client, test_tenant):
        """A missing bundle PK flashes and redirects to the list page, not 404 JSON."""
        _auth_session(client, test_tenant)
        response = client.get(
            f"/tenant/{test_tenant}/inventory-profiles/999999/preview",
            follow_redirects=False,
        )
        assert response.status_code in (302, 303)
        assert f"/tenant/{test_tenant}/inventory-profiles/" in response.headers.get("Location", "")

    def test_json_preview_endpoint_still_works(self, client, test_tenant):
        """/<id>/api/preview keeps returning JSON for machine callers (e.g. GAM product form)."""
        _auth_session(client, test_tenant)
        pk = _create_sample_profile(test_tenant, name="JSON Caller Bundle", profile_id="json_caller")

        response = client.get(f"/tenant/{test_tenant}/inventory-profiles/{pk}/api/preview")

        assert response.status_code == 200
        assert response.headers["Content-Type"].startswith("application/json")
        data = response.get_json()
        assert data["name"] == "JSON Caller Bundle"
        # Shape unchanged from the legacy endpoint.
        for key in ("id", "profile_id", "ad_unit_count", "placement_count", "format_count"):
            assert key in data

    def test_json_preview_404s_for_missing_bundle(self, client, test_tenant):
        """JSON endpoint preserves its 404-with-error-body contract."""
        _auth_session(client, test_tenant)
        response = client.get(f"/tenant/{test_tenant}/inventory-profiles/999999/api/preview")
        assert response.status_code == 404
        assert response.get_json()["error"]


class TestInventoryReuseFlow:
    """Reverse-add Reuse page (#524) — one item → many bundles in one save."""

    def test_get_reuse_renders_picklist_with_membership(self, client, test_tenant):
        """GET /reuse?item=...&kind=... renders picklist with already-includes marker."""
        _auth_session(client, test_tenant)
        # Two bundles: one already contains au_1, one doesn't.
        _create_sample_profile(test_tenant, name="Already Has It", profile_id="has_it")
        with get_db_session() as session:
            already = session.scalars(
                select(InventoryProfile).where(
                    InventoryProfile.tenant_id == test_tenant,
                    InventoryProfile.profile_id == "has_it",
                )
            ).first()
            already.inventory_config = {"ad_units": ["au_1"], "placements": [], "include_descendants": True}
            session.commit()
        _create_sample_profile(test_tenant, name="Empty Bundle", profile_id="empty")

        response = client.get(f"/tenant/{test_tenant}/inventory-profiles/reuse?item=au_1&kind=ad_unit")

        assert response.status_code == 200
        html = response.data.decode()
        assert "Add to bundles" in html
        assert "Already Has It" in html
        assert "Empty Bundle" in html
        assert "Already includes this" in html

    def test_get_reuse_missing_params_redirects_to_list(self, client, test_tenant):
        """Missing item/kind flashes + redirects to the list page."""
        _auth_session(client, test_tenant)
        response = client.get(
            f"/tenant/{test_tenant}/inventory-profiles/reuse",
            follow_redirects=False,
        )
        assert response.status_code in (302, 303)
        assert f"/tenant/{test_tenant}/inventory-profiles/" in response.headers.get("Location", "")

    def test_post_reuse_adds_item_to_selected_bundles(self, client, test_tenant):
        """POST adds the item to each selected bundle's inventory_config.ad_units."""
        _auth_session(client, test_tenant)
        a = _create_sample_profile(test_tenant, name="Bundle A", profile_id="a")
        b = _create_sample_profile(test_tenant, name="Bundle B", profile_id="b")
        _create_sample_profile(test_tenant, name="Bundle C (not picked)", profile_id="c")

        response = client.post(
            f"/tenant/{test_tenant}/inventory-profiles/reuse",
            data=MultiDict(
                [
                    ("item", "new_au"),
                    ("kind", "ad_unit"),
                    ("bundle_ids", str(a)),
                    ("bundle_ids", str(b)),
                ]
            ),
            follow_redirects=False,
        )

        assert response.status_code in (302, 303)
        with get_db_session() as session:
            for pk in (a, b):
                bundle = session.get(InventoryProfile, pk)
                assert "new_au" in bundle.inventory_config["ad_units"]
            # Unselected bundle stays as-is.
            unselected = session.scalars(
                select(InventoryProfile).where(
                    InventoryProfile.tenant_id == test_tenant,
                    InventoryProfile.profile_id == "c",
                )
            ).first()
            assert "new_au" not in unselected.inventory_config.get("ad_units", [])

    def test_post_reuse_no_selection_redirects_back_to_reuse_page(self, client, test_tenant):
        """Submitting with no bundles picked round-trips back, no DB writes."""
        _auth_session(client, test_tenant)
        pk = _create_sample_profile(test_tenant, name="Unchanged", profile_id="unchanged")
        before = client.get(f"/tenant/{test_tenant}/inventory-profiles/reuse?item=x&kind=ad_unit")
        assert before.status_code == 200

        response = client.post(
            f"/tenant/{test_tenant}/inventory-profiles/reuse",
            data={"item": "x", "kind": "ad_unit"},
            follow_redirects=False,
        )
        assert response.status_code in (302, 303)
        assert "/reuse" in response.headers.get("Location", "")

        with get_db_session() as session:
            bundle = session.get(InventoryProfile, pk)
            assert "x" not in bundle.inventory_config.get("ad_units", [])

    def test_post_reuse_skips_bundles_that_already_have_the_item(self, client, test_tenant):
        """If a selected bundle already has the item, it's silently skipped."""
        _auth_session(client, test_tenant)
        pk = _create_sample_profile(test_tenant, name="Already Has", profile_id="already")
        with get_db_session() as session:
            bundle = session.get(InventoryProfile, pk)
            bundle.inventory_config = {"ad_units": ["dup"], "placements": [], "include_descendants": True}
            session.commit()

        response = client.post(
            f"/tenant/{test_tenant}/inventory-profiles/reuse",
            data=MultiDict([("item", "dup"), ("kind", "ad_unit"), ("bundle_ids", str(pk))]),
            follow_redirects=False,
        )
        assert response.status_code in (302, 303)
        with get_db_session() as session:
            bundle = session.get(InventoryProfile, pk)
            # No duplication.
            assert bundle.inventory_config["ad_units"].count("dup") == 1

    def test_post_reuse_cross_tenant_ids_silently_dropped(self, client, test_tenant):
        """Selecting a bundle id from another tenant is ignored, not a 500."""
        _auth_session(client, test_tenant)
        # Bundle id from a fictional other-tenant scope. The repository's
        # get_by_pk filters by tenant_id and will return None.
        response = client.post(
            f"/tenant/{test_tenant}/inventory-profiles/reuse",
            data=MultiDict([("item", "x"), ("kind", "ad_unit"), ("bundle_ids", "999999")]),
            follow_redirects=False,
        )
        assert response.status_code in (302, 303)


class TestInventoryProfileMultiDomain:
    """Multi-row publisher_properties editor (#532)."""

    def test_edit_post_with_multiple_domain_rows_persists_each(self, client, test_tenant, factory_session):
        """POST with N (domain, tags) rows builds N publisher_properties entries."""
        _auth_session(client, test_tenant)
        pk = _create_sample_profile(test_tenant, name="Multi", profile_id="multi_dom")
        tenant = factory_session.get(Tenant, test_tenant)
        AuthorizedPropertyFactory(tenant=tenant, tenant_id=test_tenant, publisher_domain="sports.example.com")
        factory_session.commit()

        response = client.post(
            f"/tenant/{test_tenant}/inventory-profiles/{pk}/edit",
            data=MultiDict(
                [
                    ("name", "Multi"),
                    ("profile_id", "multi_dom"),
                    ("description", "two-row"),
                    ("targeted_ad_unit_ids", "[]"),
                    ("targeted_placement_ids", "[]"),
                    ("formats", json.dumps([{"agent_url": "https://x", "id": "display_300x250_image"}])),
                    ("property_mode", "tags"),
                    ("publisher_domain[]", tenant.primary_domain),
                    ("property_tags[]", "premium, news"),
                    ("publisher_domain[]", "sports.example.com"),
                    ("property_tags[]", "sports, premium"),
                ]
            ),
            follow_redirects=False,
        )
        assert response.status_code in (302, 303)

        with get_db_session() as session:
            saved = session.get(InventoryProfile, pk)
            domains = sorted(p["publisher_domain"] for p in saved.publisher_properties)
            assert len(saved.publisher_properties) == 2
            assert domains == sorted([tenant.primary_domain, "sports.example.com"])
            by_domain = {p["publisher_domain"]: p for p in saved.publisher_properties}
            assert sorted(by_domain[tenant.primary_domain]["property_tags"]) == ["news", "premium"]
            assert sorted(by_domain["sports.example.com"]["property_tags"]) == ["premium", "sports"]

    def test_edit_post_with_full_publisher_properties_persists_mixed_tags_and_ids(
        self, client, test_tenant, factory_session
    ):
        """Canonical publisher_properties JSON supports per-domain tags and IDs."""
        _auth_session(client, test_tenant)
        pk = _create_sample_profile(test_tenant, name="Full JSON", profile_id="full_json")
        tenant = factory_session.get(Tenant, test_tenant)
        AuthorizedPropertyFactory(
            tenant=tenant,
            tenant_id=test_tenant,
            publisher_domain="sports.example.com",
            property_id="sports_home",
            name="Sports Home",
        )
        factory_session.commit()

        response = client.post(
            f"/tenant/{test_tenant}/inventory-profiles/{pk}/edit",
            data={
                "name": "Full JSON",
                "profile_id": "full_json",
                "description": "domain-first",
                "targeted_ad_unit_ids": "[]",
                "targeted_placement_ids": "[]",
                "formats": json.dumps([{"agent_url": "https://x", "id": "display_300x250_image"}]),
                "property_mode": "full",
                "publisher_properties": json.dumps(
                    [
                        {
                            "publisher_domain": tenant.primary_domain,
                            "property_tags": ["all_inventory"],
                            "selection_type": "by_tag",
                        },
                        {
                            "publisher_domain": "sports.example.com",
                            "property_ids": ["sports_home"],
                            "selection_type": "by_id",
                        },
                    ]
                ),
            },
            follow_redirects=False,
        )
        assert response.status_code in (302, 303)

        with get_db_session() as session:
            saved = session.get(InventoryProfile, pk)
            by_domain = {p["publisher_domain"]: p for p in saved.publisher_properties}
            assert by_domain[tenant.primary_domain]["property_tags"] == ["all_inventory"]
            assert by_domain["sports.example.com"]["property_ids"] == ["sports_home"]

    def test_edit_post_with_full_publisher_properties_rejects_unknown_property_id(
        self, client, test_tenant, factory_session
    ):
        """Tampered property IDs must already exist for that publisher domain."""
        _auth_session(client, test_tenant)
        pk = _create_sample_profile(test_tenant, name="Bad Full JSON", profile_id="bad_full_json")
        tenant = factory_session.get(Tenant, test_tenant)
        AuthorizedPropertyFactory(
            tenant=tenant,
            tenant_id=test_tenant,
            publisher_domain="sports.example.com",
            property_id="sports_home",
            name="Sports Home",
        )
        factory_session.commit()

        response = client.post(
            f"/tenant/{test_tenant}/inventory-profiles/{pk}/edit",
            data={
                "name": "Bad Full JSON",
                "profile_id": "bad_full_json",
                "targeted_ad_unit_ids": "[]",
                "targeted_placement_ids": "[]",
                "formats": json.dumps([{"agent_url": "https://x", "id": "display_300x250_image"}]),
                "property_mode": "full",
                "publisher_properties": json.dumps(
                    [
                        {
                            "publisher_domain": "sports.example.com",
                            "property_ids": ["attacker_property"],
                            "selection_type": "by_id",
                        }
                    ]
                ),
            },
            follow_redirects=False,
        )
        assert response.status_code in (302, 303)
        with get_db_session() as session:
            saved = session.get(InventoryProfile, pk)
            assert saved.publisher_properties[0]["property_tags"] == ["all_inventory"]

    def test_edit_post_rejects_unauthorized_domain_row(self, client, test_tenant):
        """Tampered edit POST cannot persist an unowned publisher domain."""
        _auth_session(client, test_tenant)
        pk = _create_sample_profile(test_tenant, name="Bad Domain", profile_id="bad_domain")

        response = client.post(
            f"/tenant/{test_tenant}/inventory-profiles/{pk}/edit",
            data=MultiDict(
                [
                    ("name", "Bad Domain"),
                    ("profile_id", "bad_domain"),
                    ("targeted_ad_unit_ids", "[]"),
                    ("targeted_placement_ids", "[]"),
                    ("formats", json.dumps([{"agent_url": "https://x", "id": "display_300x250_image"}])),
                    ("property_mode", "tags"),
                    ("publisher_domain[]", "attacker.example.com"),
                    ("property_tags[]", "premium"),
                ]
            ),
            follow_redirects=False,
        )
        assert response.status_code in (302, 303)

        with get_db_session() as session:
            saved = session.get(InventoryProfile, pk)
            assert saved.publisher_properties[0]["property_tags"] == ["all_inventory"]

    def test_edit_post_back_compat_single_field(self, client, test_tenant):
        """Legacy single `property_tags` field still works (no list submission)."""
        _auth_session(client, test_tenant)
        pk = _create_sample_profile(test_tenant, name="Compat", profile_id="compat")

        response = client.post(
            f"/tenant/{test_tenant}/inventory-profiles/{pk}/edit",
            data={
                "name": "Compat",
                "profile_id": "compat",
                "targeted_ad_unit_ids": "[]",
                "targeted_placement_ids": "[]",
                "formats": json.dumps([{"agent_url": "https://x", "id": "display_300x250_image"}]),
                "property_mode": "tags",
                "property_tags": "all_inventory",
            },
            follow_redirects=False,
        )
        assert response.status_code in (302, 303)
        with get_db_session() as session:
            saved = session.get(InventoryProfile, pk)
            assert len(saved.publisher_properties) == 1
            assert saved.publisher_properties[0]["property_tags"] == ["all_inventory"]

    def test_edit_post_rejects_row_missing_tags(self, client, test_tenant):
        """An empty tags input on any row rejects the entire save."""
        _auth_session(client, test_tenant)
        pk = _create_sample_profile(test_tenant, name="Bad", profile_id="bad")

        response = client.post(
            f"/tenant/{test_tenant}/inventory-profiles/{pk}/edit",
            data=MultiDict(
                [
                    ("name", "Bad"),
                    ("profile_id", "bad"),
                    ("targeted_ad_unit_ids", "[]"),
                    ("targeted_placement_ids", "[]"),
                    ("formats", json.dumps([{"agent_url": "https://x", "id": "display_300x250_image"}])),
                    ("property_mode", "tags"),
                    ("publisher_domain[]", f"{test_tenant}.example.com"),
                    ("property_tags[]", "premium"),
                    ("publisher_domain[]", "empty.example.com"),
                    ("property_tags[]", ""),
                ]
            ),
            follow_redirects=False,
        )
        # Redirect back to the editor (flash error); bundle's properties unchanged.
        assert response.status_code in (302, 303)
        with get_db_session() as session:
            saved = session.get(InventoryProfile, pk)
            # Sample-profile default: one tag entry under `inv_prof_test_tenant.example.com`.
            assert saved.publisher_properties[0]["property_tags"] == ["all_inventory"]


class TestInventoryProfileDuplicate:
    """Test inventory profile duplication."""

    def test_duplicate_creates_copy_and_redirects_to_edit(self, client, test_tenant):
        """POST /duplicate creates a copy with the same fields and redirects to its edit page."""
        _auth_session(client, test_tenant)
        source_pk = _create_sample_profile(test_tenant, name="Source Bundle", profile_id="source_bundle")

        response = client.post(
            f"/tenant/{test_tenant}/inventory-profiles/{source_pk}/duplicate",
            follow_redirects=False,
        )
        assert response.status_code in (302, 303)

        with get_db_session() as session:
            source = session.get(InventoryProfile, source_pk)
            copy = session.scalars(
                select(InventoryProfile).where(
                    InventoryProfile.tenant_id == test_tenant,
                    InventoryProfile.name == "Source Bundle (copy)",
                )
            ).first()

        assert copy is not None
        assert copy.id != source_pk
        assert copy.profile_id == "source_bundle_copy"
        assert copy.inventory_config == source.inventory_config
        assert copy.format_ids == source.format_ids
        assert copy.publisher_properties == source.publisher_properties
        assert f"/inventory-profiles/{copy.id}/edit" in response.headers.get("Location", "")

    def test_duplicate_twice_generates_unique_profile_ids(self, client, test_tenant):
        """Duplicating twice yields ..._copy and ..._copy_2."""
        _auth_session(client, test_tenant)
        source_pk = _create_sample_profile(test_tenant, name="Twice Bundle", profile_id="twice_bundle")

        client.post(f"/tenant/{test_tenant}/inventory-profiles/{source_pk}/duplicate", follow_redirects=False)
        client.post(f"/tenant/{test_tenant}/inventory-profiles/{source_pk}/duplicate", follow_redirects=False)

        with get_db_session() as session:
            ids = sorted(
                session.scalars(
                    select(InventoryProfile.profile_id).where(InventoryProfile.tenant_id == test_tenant)
                ).all()
            )
        assert "twice_bundle" in ids
        assert "twice_bundle_copy" in ids
        assert "twice_bundle_copy_2" in ids

    def test_duplicate_nonexistent_redirects_to_list(self, client, test_tenant):
        """Duplicating a missing bundle redirects without creating anything."""
        _auth_session(client, test_tenant)
        response = client.post(
            f"/tenant/{test_tenant}/inventory-profiles/999999/duplicate",
            follow_redirects=False,
        )
        assert response.status_code in (302, 303)
        with get_db_session() as session:
            count = len(
                list(session.scalars(select(InventoryProfile).where(InventoryProfile.tenant_id == test_tenant)).all())
            )
        assert count == 0
