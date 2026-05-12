"""Integration tests for the authorized properties admin blueprint.

Tests property list, create, delete, and tag management via Flask test client.
Requires PostgreSQL (integration_db fixture).
"""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import delete, select

from src.admin.app import create_app
from src.core.database.database_session import get_db_session
from src.core.database.models import AuthorizedProperty, PropertyTag, Tenant
from tests.utils.database_helpers import create_tenant_with_timestamps

app = create_app()

pytestmark = [pytest.mark.admin, pytest.mark.requires_db]

_TENANT_ID = "prop_test_tenant"


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
    """Create a test tenant for authorized property tests."""
    with get_db_session() as session:
        try:
            session.execute(delete(AuthorizedProperty).where(AuthorizedProperty.tenant_id == _TENANT_ID))
            session.execute(delete(PropertyTag).where(PropertyTag.tenant_id == _TENANT_ID))
            session.execute(delete(Tenant).where(Tenant.tenant_id == _TENANT_ID))
            session.commit()
        except Exception:
            session.rollback()

        tenant = create_tenant_with_timestamps(
            tenant_id=_TENANT_ID,
            name="Property Test Tenant",
            subdomain="prop-test",
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


class TestAuthorizedPropertiesListPage:
    """Test the authorized properties list page."""

    def test_list_page_returns_200(self, client, test_tenant):
        """GET /tenant/<tid>/authorized-properties returns 200."""
        _auth_session(client, test_tenant)
        response = client.get(f"/tenant/{test_tenant}/authorized-properties")
        assert response.status_code == 200

    def test_list_page_shows_existing_property(self, client, test_tenant):
        """After creating a property, the list page shows it."""
        _auth_session(client, test_tenant)
        with get_db_session() as session:
            prop = AuthorizedProperty(
                property_id="prop_list_test",
                tenant_id=test_tenant,
                property_type="website",
                name="List Test Property",
                identifiers=[],
                publisher_domain="list-test.example.com",
                verification_status="pending",
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
            session.add(prop)
            session.commit()

        response = client.get(f"/tenant/{test_tenant}/authorized-properties")
        html = response.data.decode()
        assert "List Test Property" in html


class TestPropertyCreate:
    """Test authorized property creation."""

    def test_create_form_returns_200(self, client, test_tenant):
        """GET /tenant/<tid>/authorized-properties/create returns 200."""
        _auth_session(client, test_tenant)
        response = client.get(f"/tenant/{test_tenant}/authorized-properties/create")
        assert response.status_code == 200

    def test_create_property_saves_to_db(self, client, test_tenant):
        """POST valid data creates a property in the database."""
        _auth_session(client, test_tenant)
        response = client.post(
            f"/tenant/{test_tenant}/authorized-properties/create",
            data={
                "property_type": "website",
                "name": "Created Test Site",
                "publisher_domain": "created-test.example.com",
            },
            follow_redirects=False,
        )
        assert response.status_code in (302, 303)

        with get_db_session() as session:
            prop = session.scalars(
                select(AuthorizedProperty).where(
                    AuthorizedProperty.tenant_id == test_tenant,
                    AuthorizedProperty.name == "Created Test Site",
                )
            ).first()
        assert prop is not None
        assert prop.property_type == "website"
        assert prop.publisher_domain == "created-test.example.com"
        assert prop.verification_status == "pending"

    def test_create_property_missing_required_fields_redirects(self, client, test_tenant):
        """POST without required fields redirects back (no DB entry created)."""
        _auth_session(client, test_tenant)
        response = client.post(
            f"/tenant/{test_tenant}/authorized-properties/create",
            data={"name": "Incomplete"},
            follow_redirects=False,
        )
        # Should redirect (flash error) rather than 200 OK
        assert response.status_code in (302, 303)

        with get_db_session() as session:
            prop = session.scalars(
                select(AuthorizedProperty).where(
                    AuthorizedProperty.tenant_id == test_tenant,
                    AuthorizedProperty.name == "Incomplete",
                )
            ).first()
        assert prop is None


class TestPropertyDelete:
    """Test authorized property deletion."""

    def test_delete_property_removes_from_db(self, client, test_tenant):
        """POST to delete endpoint removes the property from the database."""
        _auth_session(client, test_tenant)
        prop_id = f"prop_del_{uuid.uuid4().hex[:8]}"
        with get_db_session() as session:
            session.add(
                AuthorizedProperty(
                    property_id=prop_id,
                    tenant_id=test_tenant,
                    property_type="website",
                    name="Delete Me",
                    identifiers=[],
                    publisher_domain="delete.example.com",
                    verification_status="pending",
                    created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                )
            )
            session.commit()

        response = client.post(
            f"/tenant/{test_tenant}/authorized-properties/{prop_id}/delete",
            follow_redirects=False,
        )
        assert response.status_code in (302, 303)

        with get_db_session() as session:
            prop = session.scalars(
                select(AuthorizedProperty).where(
                    AuthorizedProperty.tenant_id == test_tenant,
                    AuthorizedProperty.property_id == prop_id,
                )
            ).first()
        assert prop is None

    def test_delete_nonexistent_property_redirects(self, client, test_tenant):
        """POST delete for a nonexistent property redirects with error flash."""
        _auth_session(client, test_tenant)
        response = client.post(
            f"/tenant/{test_tenant}/authorized-properties/nonexistent_prop_id/delete",
            follow_redirects=False,
        )
        assert response.status_code in (302, 303)


class TestPropertyTagCreate:
    """Test property tag management."""

    def test_list_tags_returns_200(self, client, test_tenant):
        """GET /tenant/<tid>/property-tags returns 200."""
        _auth_session(client, test_tenant)
        response = client.get(f"/tenant/{test_tenant}/property-tags")
        assert response.status_code == 200

    def test_create_tag_saves_to_db(self, client, test_tenant):
        """POST valid tag data creates the tag in the database."""
        _auth_session(client, test_tenant)
        response = client.post(
            f"/tenant/{test_tenant}/property-tags/create",
            data={
                "tag_id": "test_tag_001",
                "name": "Test Tag 001",
                "description": "A tag created in tests",
            },
            follow_redirects=False,
        )
        assert response.status_code in (302, 303)

        with get_db_session() as session:
            tag = session.scalars(
                select(PropertyTag).where(
                    PropertyTag.tenant_id == test_tenant,
                    PropertyTag.tag_id == "test_tag_001",
                )
            ).first()
        assert tag is not None
        assert tag.name == "Test Tag 001"

    def test_create_tag_missing_fields_redirects_without_creation(self, client, test_tenant):
        """POST with missing fields redirects and does not create a tag."""
        _auth_session(client, test_tenant)
        response = client.post(
            f"/tenant/{test_tenant}/property-tags/create",
            data={"tag_id": "incomplete_tag"},  # missing name and description
            follow_redirects=False,
        )
        assert response.status_code in (302, 303)

        with get_db_session() as session:
            tag = session.scalars(
                select(PropertyTag).where(
                    PropertyTag.tenant_id == test_tenant,
                    PropertyTag.tag_id == "incomplete_tag",
                )
            ).first()
        assert tag is None
