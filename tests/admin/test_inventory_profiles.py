"""Integration tests for the inventory profiles admin blueprint.

Tests profile list, create, edit, and delete via Flask test client.
Requires PostgreSQL (integration_db fixture).
"""

import json

import pytest
from sqlalchemy import delete, select

from src.admin.app import create_app
from src.core.database.database_session import get_db_session
from src.core.database.models import InventoryProfile, Tenant
from tests.utils.database_helpers import create_tenant_with_timestamps

app = create_app()

pytestmark = [pytest.mark.admin, pytest.mark.requires_db]

_TENANT_ID = "inv_prof_test_tenant"


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

    def test_delete_nonexistent_profile_returns_404(self, client, test_tenant):
        """POST delete for a nonexistent profile returns 404."""
        _auth_session(client, test_tenant)
        response = client.post(
            f"/tenant/{test_tenant}/inventory-profiles/999999/delete",
            follow_redirects=False,
        )
        assert response.status_code == 404
