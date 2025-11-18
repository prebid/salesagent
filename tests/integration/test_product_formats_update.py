"""Test that product format updates are properly saved to database.

This tests the bug fix for JSONB columns not being flagged as modified.
"""

import pytest
from sqlalchemy import select
from sqlalchemy.orm import attributes

from src.core.database.models import Product
from tests.helpers.adcp_factories import create_test_product


@pytest.fixture
def sample_product(integration_db):
    """Create a sample product for testing using factory."""
    from src.core.database.database_session import get_db_session
    from src.core.database.models import Tenant

    with get_db_session() as session:
        # Create tenant
        tenant = Tenant(
            tenant_id="test_tenant",
            name="Test Tenant",
            subdomain="test",
        )
        session.add(tenant)
        session.flush()

        # Use factory to create product with proper ADCP format_ids
        product_data = create_test_product(
            product_id="test_product",
            name="Test Product",
            description="Test Description",
            format_ids=["old_format_1", "old_format_2"],
        )

        # Convert to database model
        product = Product(
            tenant_id="test_tenant",
            product_id=product_data.product_id,
            name=product_data.name,
            description=product_data.description,
            format_ids=[fmt.model_dump(mode="json") for fmt in product_data.format_ids],
            targeting_template={},
            delivery_type=(
                product_data.delivery_type.value
                if hasattr(product_data.delivery_type, "value")
                else product_data.delivery_type
            ),
            property_tags=["all_inventory"],
        )
        session.add(product)
        session.commit()

        return product.product_id


@pytest.mark.requires_db
def test_product_formats_update_with_flag_modified(integration_db, sample_product):
    """Test that updating product.format_ids with flag_modified saves changes."""
    from src.core.database.database_session import get_db_session

    # Update the product's formats
    with get_db_session() as session:
        stmt = select(Product).filter_by(product_id=sample_product)
        product = session.scalars(stmt).first()
        assert product is not None

        # Update formats (using proper ADCP agent URL)
        product.format_ids = [
            {"agent_url": "https://creative.adcontextprotocol.org", "id": "new_format_1"},
            {"agent_url": "https://creative.adcontextprotocol.org", "id": "new_format_2"},
            {"agent_url": "https://creative.adcontextprotocol.org", "id": "new_format_3"},
        ]

        # Flag as modified (this is the fix)
        attributes.flag_modified(product, "format_ids")

        session.commit()

    # Verify the changes were saved
    with get_db_session() as session:
        stmt = select(Product).filter_by(product_id=sample_product)
        product = session.scalars(stmt).first()
        assert product is not None
        assert len(product.format_ids) == 3
        assert product.format_ids[0]["id"] == "new_format_1"
        assert product.format_ids[1]["id"] == "new_format_2"
        assert product.format_ids[2]["id"] == "new_format_3"


@pytest.mark.requires_db
def test_product_formats_update_without_flag_modified_fails(integration_db, sample_product):
    """Test that reassigning product.format_ids DOES save changes even without flag_modified.

    When you reassign the entire field (product.format_ids = [...]), SQLAlchemy detects the change.
    flag_modified is only needed for in-place mutations like product.format_ids[0] = {...}.
    """
    from src.core.database.database_session import get_db_session

    # Reassign the product's formats (full reassignment, not in-place mutation)
    with get_db_session() as session:
        stmt = select(Product).filter_by(product_id=sample_product)
        product = session.scalars(stmt).first()
        assert product is not None

        # Reassign formats (this IS detected by SQLAlchemy, using proper ADCP agent URL)
        product.format_ids = [
            {"agent_url": "https://creative.adcontextprotocol.org", "id": "should_save_1"},
            {"agent_url": "https://creative.adcontextprotocol.org", "id": "should_save_2"},
        ]

        # NOTE: NOT calling flag_modified, but reassignment is detected
        # attributes.flag_modified(product, "format_ids")

        session.commit()

    # Verify the changes WERE saved (reassignment is detected)
    with get_db_session() as session:
        stmt = select(Product).filter_by(product_id=sample_product)
        product = session.scalars(stmt).first()
        assert product is not None
        # Changes were saved because we reassigned the entire field
        assert len(product.format_ids) == 2
        assert product.format_ids[0]["id"] == "should_save_1"
        assert product.format_ids[1]["id"] == "should_save_2"


@pytest.mark.requires_db
def test_product_countries_update_with_flag_modified(integration_db, sample_product):
    """Test that updating product.countries with flag_modified saves changes."""
    from src.core.database.database_session import get_db_session

    # Update the product's countries
    with get_db_session() as session:
        stmt = select(Product).filter_by(product_id=sample_product)
        product = session.scalars(stmt).first()
        assert product is not None

        # Update countries
        product.countries = ["US", "CA", "GB"]

        # Flag as modified (this is the fix)
        attributes.flag_modified(product, "countries")

        session.commit()

    # Verify the changes were saved
    with get_db_session() as session:
        stmt = select(Product).filter_by(product_id=sample_product)
        product = session.scalars(stmt).first()
        assert product is not None
        assert product.countries == ["US", "CA", "GB"]
