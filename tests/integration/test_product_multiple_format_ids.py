"""Integration test for product creation with multiple format_ids.

Tests that products with multiple format IDs:
1. Can be created in the database
2. Preserve all format_ids (not just the first one)
3. Have correct agent URLs after migration
"""

import pytest
from sqlalchemy import select
from sqlalchemy.orm import attributes

from src.core.database.models import Product, Tenant
from tests.helpers.adcp_factories import create_test_db_product


@pytest.fixture
def test_tenant(integration_db):
    """Create a test tenant."""
    from src.core.database.database_session import get_db_session

    with get_db_session() as session:
        tenant = Tenant(
            tenant_id="test_tenant_multi_format",
            name="Test Tenant",
            subdomain="testmulti",
        )
        session.add(tenant)
        session.commit()
        return tenant.tenant_id


@pytest.mark.requires_db
def test_create_product_with_multiple_format_ids(integration_db, test_tenant):
    """Test creating a product with multiple format_ids."""
    from src.core.database.database_session import get_db_session

    # Create product with 3 different format IDs
    with get_db_session() as session:
        db_product = create_test_db_product(
            tenant_id=test_tenant,
            product_id="multi_format_product",
            name="Multi-Format Product",
            description="Product with multiple formats",
            format_ids=[
                {"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"},
                {"agent_url": "https://creative.adcontextprotocol.org", "id": "display_728x90"},
                {"agent_url": "https://creative.adcontextprotocol.org", "id": "video_15s"},
            ],
        )
        session.add(db_product)
        session.commit()

    # Verify all format_ids were saved
    with get_db_session() as session:
        stmt = select(Product).filter_by(product_id="multi_format_product", tenant_id=test_tenant)
        product = session.scalars(stmt).first()

        assert product is not None, "Product should be created"
        assert len(product.format_ids) == 3, "All 3 format_ids should be saved"

        # Verify each format_id has correct structure
        format_ids_set = {fmt["id"] for fmt in product.format_ids}
        assert format_ids_set == {"display_300x250", "display_728x90", "video_15s"}

        # Verify all format_ids have agent_url field
        for fmt in product.format_ids:
            assert "agent_url" in fmt, f"Format {fmt['id']} should have agent_url"
            assert "id" in fmt, "Format should have id field"
            # Verify agent_url is correct (not 'creatives')
            assert (
                "creative.adcontextprotocol.org" in fmt["agent_url"]
            ), f"Format {fmt['id']} should use 'creative' not 'creatives'"


@pytest.mark.requires_db
def test_update_product_format_ids_preserves_all_formats(integration_db, test_tenant):
    """Test that updating format_ids preserves all formats, not just the first one."""
    from sqlalchemy.orm import attributes

    from src.core.database.database_session import get_db_session

    # Create initial product with 2 formats
    with get_db_session() as session:
        db_product = create_test_db_product(
            tenant_id=test_tenant,
            product_id="update_format_product",
            name="Update Format Test Product",
            format_ids=[
                {"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"},
                {"agent_url": "https://creative.adcontextprotocol.org", "id": "display_728x90"},
            ],
        )
        session.add(db_product)
        session.commit()

    # Update to add a third format
    with get_db_session() as session:
        stmt = select(Product).filter_by(product_id="update_format_product", tenant_id=test_tenant)
        product = session.scalars(stmt).first()

        # Add a third format
        product.format_ids.append({"agent_url": "https://creative.adcontextprotocol.org", "id": "video_30s"})

        # Flag as modified
        attributes.flag_modified(product, "format_ids")
        session.commit()

    # Verify all 3 formats are saved
    with get_db_session() as session:
        stmt = select(Product).filter_by(product_id="update_format_product", tenant_id=test_tenant)
        product = session.scalars(stmt).first()

        assert len(product.format_ids) == 3, "All 3 format_ids should be saved"
        format_ids_set = {fmt["id"] for fmt in product.format_ids}
        assert format_ids_set == {"display_300x250", "display_728x90", "video_30s"}


@pytest.mark.requires_db
def test_product_format_ids_migration_compatibility(integration_db, test_tenant):
    """Test that format_ids with old 'creatives' URL can be migrated.

    This tests the migration fix where we iterate through ALL format_ids,
    not just the first one.
    """
    from src.core.database.database_session import get_db_session

    # Create product with old 'creatives' URL (pre-migration format)
    # Note: We still use create_test_db_product but manually override format_ids with old URLs
    with get_db_session() as session:
        product = create_test_db_product(
            tenant_id=test_tenant,
            product_id="legacy_format_product",
            name="Legacy Product",
            description="Product with old agent URLs",
            format_ids=[
                {"agent_url": "https://creatives.adcontextprotocol.org", "id": "format_1"},
                {"agent_url": "https://creatives.adcontextprotocol.org", "id": "format_2"},
                {"agent_url": "https://creatives.adcontextprotocol.org", "id": "format_3"},
            ],
        )
        session.add(product)
        session.commit()

    # Simulate migration: update all agent URLs
    with get_db_session() as session:
        stmt = select(Product).filter_by(product_id="legacy_format_product", tenant_id=test_tenant)
        product = session.scalars(stmt).first()

        # Update all format_ids (migration logic)
        updated_formats = []
        for fmt in product.format_ids:
            updated_fmt = fmt.copy()
            updated_fmt["agent_url"] = updated_fmt["agent_url"].replace(
                "creatives.adcontextprotocol.org", "creative.adcontextprotocol.org"
            )
            updated_formats.append(updated_fmt)

        product.format_ids = updated_formats
        attributes.flag_modified(product, "format_ids")
        session.commit()

    # Verify ALL format_ids were migrated (not just the first one)
    with get_db_session() as session:
        stmt = select(Product).filter_by(product_id="legacy_format_product", tenant_id=test_tenant)
        product = session.scalars(stmt).first()

        assert len(product.format_ids) == 3, "All 3 format_ids should be preserved"

        # Verify ALL have the new URL (this was the bug - only first one was updated)
        for i, fmt in enumerate(product.format_ids):
            assert (
                "creative.adcontextprotocol.org" in fmt["agent_url"]
            ), f"Format {i} should have migrated URL, got: {fmt['agent_url']}"
            assert "creatives.adcontextprotocol.org" not in fmt["agent_url"], f"Format {i} should not have old URL"
