"""Tests for mock adapter publisher sync property creation.

These tests ensure that when syncing publishers for mock/dev adapters,
AuthorizedProperty and PropertyTag records are created correctly.

This is a regression test for an issue where mock tenants would have
verified publishers but no properties, causing the product creation UI
to show empty property/tag lists.
"""

from unittest.mock import patch

import pytest
from sqlalchemy import select

from src.core.config import get_settings
from src.core.database.database_session import get_db_session
from src.core.database.models import (
    AdapterConfig,
    AuthorizedProperty,
    PropertyTag,
    PublisherPartner,
    Tenant,
)
from tests.factories import UserFactory
from tests.helpers.admin_session import admin_member_session, admin_test_app


def _sync(tenant_id: str, email: str):
    """POST /tenant/<id>/publisher-partners/sync as an active member."""
    app = admin_test_app()
    with app.test_client() as client:
        admin_member_session(client, tenant_id, email)
        with patch.object(type(get_settings()), "publisher_auto_verify_allowed", property(lambda _self: True)):
            return client.post(f"/tenant/{tenant_id}/publisher-partners/sync")


@pytest.mark.requires_db
class TestMockAdapterPublisherSync:
    """Test that mock adapter publisher sync creates properties and tags."""

    @pytest.fixture
    def mock_tenant(self, integration_db):
        """Create a tenant with mock adapter configuration."""
        from datetime import UTC, datetime

        with get_db_session() as session:
            # Create tenant
            tenant = Tenant(
                tenant_id="test_mock_sync",
                name="Test Mock Sync Tenant",
                subdomain="test-mock-sync",
                virtual_host="test-mock-sync.adcp.test",
                ad_server="mock",
                authorized_emails=["test@example.com"],
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
            session.add(tenant)
            session.flush()

            # Create adapter config for mock adapter
            adapter_config = AdapterConfig(
                tenant_id="test_mock_sync",
                adapter_type="mock",
            )
            session.add(adapter_config)
            session.commit()

            yield "test_mock_sync"

            # Cleanup
            session.execute(select(AuthorizedProperty).where(AuthorizedProperty.tenant_id == "test_mock_sync"))
            for prop in session.scalars(
                select(AuthorizedProperty).where(AuthorizedProperty.tenant_id == "test_mock_sync")
            ).all():
                session.delete(prop)
            for tag in session.scalars(select(PropertyTag).where(PropertyTag.tenant_id == "test_mock_sync")).all():
                session.delete(tag)
            for partner in session.scalars(
                select(PublisherPartner).where(PublisherPartner.tenant_id == "test_mock_sync")
            ).all():
                session.delete(partner)
            session.delete(adapter_config)
            session.delete(tenant)
            session.commit()

    @pytest.fixture
    def publisher_partner(self, mock_tenant):
        """Create a publisher partner for the mock tenant."""
        from datetime import UTC, datetime

        with get_db_session() as session:
            partner = PublisherPartner(
                tenant_id=mock_tenant,
                publisher_domain="example.com",
                display_name="Example Publisher",
                sync_status="pending",
                is_verified=False,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
            session.add(partner)
            session.commit()

            return partner.id

    @pytest.fixture
    def member_email(self, mock_tenant, factory_session):
        """Active same-tenant member; factory email, not SUPER_ADMIN_EMAILS."""
        tenant = factory_session.scalars(select(Tenant).filter_by(tenant_id=mock_tenant)).one()
        return UserFactory(tenant=tenant).email

    def test_sync_creates_property_tag(self, mock_tenant, publisher_partner, member_email):
        """Test that sync creates 'all_inventory' PropertyTag for mock tenants."""
        response = _sync(mock_tenant, member_email)
        assert response.status_code == 200
        data = response.get_json()
        assert data["verified"] == 1
        assert data["tags_created"] == 1

        # Verify PropertyTag was created
        with get_db_session() as session:
            tag = session.scalars(
                select(PropertyTag).where(PropertyTag.tenant_id == mock_tenant, PropertyTag.tag_id == "all_inventory")
            ).first()

            assert tag is not None, "all_inventory PropertyTag should be created"
            assert tag.name == "All Inventory"

    def test_sync_creates_authorized_property(self, mock_tenant, publisher_partner, member_email):
        """Test that sync creates AuthorizedProperty for each publisher domain."""
        response = _sync(mock_tenant, member_email)
        assert response.status_code == 200
        data = response.get_json()
        assert data["verified"] == 1
        assert data["properties_created"] == 1

        # Verify AuthorizedProperty was created
        with get_db_session() as session:
            props = session.scalars(select(AuthorizedProperty).where(AuthorizedProperty.tenant_id == mock_tenant)).all()

            assert len(props) >= 1, "At least one AuthorizedProperty should be created"

            # Find the property for example.com
            example_prop = next((p for p in props if "example" in p.property_id), None)
            assert example_prop is not None, "Property for example.com should exist"
            assert example_prop.verification_status == "verified"
            assert example_prop.property_type == "website"
            assert "all_inventory" in (example_prop.tags or [])

    def test_sync_property_has_verified_status(self, mock_tenant, publisher_partner, member_email):
        """Test that created properties have verification_status='verified'.

        This is critical because the product creation UI only shows properties
        with verification_status='verified'.
        """
        response = _sync(mock_tenant, member_email)
        assert response.status_code == 200
        data = response.get_json()
        assert data["verified"] == 1
        assert data["properties_created"] == 1

        # Verify all properties have verified status
        with get_db_session() as session:
            props = session.scalars(select(AuthorizedProperty).where(AuthorizedProperty.tenant_id == mock_tenant)).all()

            for prop in props:
                assert prop.verification_status == "verified", (
                    f"Property {prop.property_id} should have verification_status='verified', "
                    f"got '{prop.verification_status}'"
                )

    def test_sync_is_idempotent(self, mock_tenant, publisher_partner, member_email):
        """Test that running sync multiple times doesn't create duplicate records."""
        first = _sync(mock_tenant, member_email)
        second = _sync(mock_tenant, member_email)
        assert first.status_code == 200
        assert second.status_code == 200
        assert first.get_json()["verified"] == 1
        assert second.get_json()["verified"] == 1

        # Verify no duplicates
        with get_db_session() as session:
            tags = session.scalars(
                select(PropertyTag).where(PropertyTag.tenant_id == mock_tenant, PropertyTag.tag_id == "all_inventory")
            ).all()
            assert len(tags) == 1, "Should have exactly one all_inventory tag"

            props = session.scalars(
                select(AuthorizedProperty).where(
                    AuthorizedProperty.tenant_id == mock_tenant,
                    AuthorizedProperty.publisher_domain == "example.com",
                )
            ).all()
            assert len(props) == 1, "Should have exactly one property per domain"
