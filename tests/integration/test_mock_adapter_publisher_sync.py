"""Tests for mock adapter publisher sync property creation.

These tests ensure that when syncing publishers for mock/dev adapters,
AuthorizedProperty and PropertyTag records are created correctly.

This is a regression test for an issue where mock tenants would have
verified publishers but no properties, causing the product creation UI
to show empty property/tag lists.
"""

import pytest
from sqlalchemy import select

from src.core.database.database_session import get_db_session
from src.core.database.models import (
    AdapterConfig,
    AuthorizedProperty,
    PropertyTag,
    PublisherPartner,
    Tenant,
)


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

    def test_sync_creates_property_tag(self, mock_tenant, publisher_partner):
        """Test that sync creates 'all_inventory' PropertyTag for mock tenants."""
        from unittest.mock import patch

        from src.admin.app import create_app
        from src.admin.blueprints.publisher_partners import sync_publisher_partners
        from src.core.config import AppConfig

        # Mock config to return development environment (triggers auto-verify)
        mock_config = AppConfig()
        mock_config.environment = "development"

        app, _ = create_app()

        with app.test_request_context():
            with patch("src.admin.blueprints.publisher_partners.get_config", return_value=mock_config):
                response = sync_publisher_partners(mock_tenant)
                data = response.get_json()

                assert data["verified"] == 1
                assert data.get("tags_created", 0) >= 0  # May be 0 if tag already exists

        # Verify PropertyTag was created
        with get_db_session() as session:
            tag = session.scalars(
                select(PropertyTag).where(PropertyTag.tenant_id == mock_tenant, PropertyTag.tag_id == "all_inventory")
            ).first()

            assert tag is not None, "all_inventory PropertyTag should be created"
            assert tag.name == "All Inventory"

    def test_sync_creates_authorized_property(self, mock_tenant, publisher_partner):
        """Test that sync creates AuthorizedProperty for each publisher domain."""
        from unittest.mock import patch

        from src.admin.app import create_app
        from src.admin.blueprints.publisher_partners import sync_publisher_partners
        from src.core.config import AppConfig

        mock_config = AppConfig()
        mock_config.environment = "development"

        app, _ = create_app()

        with app.test_request_context():
            with patch("src.admin.blueprints.publisher_partners.get_config", return_value=mock_config):
                response = sync_publisher_partners(mock_tenant)
                data = response.get_json()

                assert data["verified"] == 1
                assert data.get("properties_created", 0) >= 1

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

    def test_sync_property_has_verified_status(self, mock_tenant, publisher_partner):
        """Test that created properties have verification_status='verified'.

        This is critical because the product creation UI only shows properties
        with verification_status='verified'.
        """
        from unittest.mock import patch

        from src.admin.app import create_app
        from src.admin.blueprints.publisher_partners import sync_publisher_partners
        from src.core.config import AppConfig

        mock_config = AppConfig()
        mock_config.environment = "development"

        app, _ = create_app()

        with app.test_request_context():
            with patch("src.admin.blueprints.publisher_partners.get_config", return_value=mock_config):
                sync_publisher_partners(mock_tenant)

        # Verify all properties have verified status
        with get_db_session() as session:
            props = session.scalars(select(AuthorizedProperty).where(AuthorizedProperty.tenant_id == mock_tenant)).all()

            for prop in props:
                assert prop.verification_status == "verified", (
                    f"Property {prop.property_id} should have verification_status='verified', "
                    f"got '{prop.verification_status}'"
                )

    def test_sync_is_idempotent(self, mock_tenant, publisher_partner):
        """Test that running sync multiple times doesn't create duplicate records."""
        from unittest.mock import patch

        from src.admin.app import create_app
        from src.admin.blueprints.publisher_partners import sync_publisher_partners
        from src.core.config import AppConfig

        mock_config = AppConfig()
        mock_config.environment = "development"

        app, _ = create_app()

        # Run sync twice
        with app.test_request_context():
            with patch("src.admin.blueprints.publisher_partners.get_config", return_value=mock_config):
                sync_publisher_partners(mock_tenant)
                sync_publisher_partners(mock_tenant)

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
