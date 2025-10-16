"""Test product deletion with pricing_options constraint trigger.

This test verifies that the prevent_empty_pricing_options trigger correctly
allows product deletion while still enforcing the constraint for manual
pricing option deletion.
"""

import pytest
from sqlalchemy import select, text

from src.core.database.database_session import get_db_session
from src.core.database.models import PricingOption, Product, Tenant


@pytest.mark.requires_db
def test_product_deletion_cascades_pricing_options(integration_db):
    """Test that deleting a product cascades to pricing_options despite trigger."""
    with get_db_session() as session:
        # Create test tenant
        tenant = Tenant(
            tenant_id="test_trigger",
            name="Test Trigger Tenant",
            subdomain="test-trigger",
            is_active=True,
        )
        session.add(tenant)
        session.flush()

        # Create test product
        product = Product(
            tenant_id="test_trigger",
            product_id="test_prod_001",
            name="Test Product",
            description="Product for testing trigger",
            formats=[{"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250_image"}],
            targeting_template={},
            delivery_type="guaranteed",
            property_tags=["all_inventory"],
        )
        session.add(product)
        session.flush()

        # Create pricing option
        pricing_option = PricingOption(
            tenant_id="test_trigger",
            product_id="test_prod_001",
            pricing_model="cpm",
            currency="USD",
            rate=5.0,
            is_fixed=True,
        )
        session.add(pricing_option)
        session.commit()

        # Verify product and pricing option exist
        stmt = select(Product).filter_by(tenant_id="test_trigger", product_id="test_prod_001")
        product_check = session.scalars(stmt).first()
        assert product_check is not None

        stmt = select(PricingOption).filter_by(tenant_id="test_trigger", product_id="test_prod_001")
        pricing_check = session.scalars(stmt).first()
        assert pricing_check is not None

        # Delete the product - this should cascade to pricing_options
        session.delete(product_check)
        session.commit()

        # Verify both product and pricing_options are deleted
        stmt = select(Product).filter_by(tenant_id="test_trigger", product_id="test_prod_001")
        product_after = session.scalars(stmt).first()
        assert product_after is None, "Product should be deleted"

        stmt = select(PricingOption).filter_by(tenant_id="test_trigger", product_id="test_prod_001")
        pricing_after = session.scalars(stmt).first()
        assert pricing_after is None, "Pricing option should be cascaded deleted"

        # Cleanup
        session.execute(text("DELETE FROM tenants WHERE tenant_id = 'test_trigger'"))
        session.commit()


@pytest.mark.requires_db
def test_trigger_still_blocks_manual_deletion_of_last_pricing_option(integration_db):
    """Test that the trigger still prevents manual deletion of the last pricing option."""
    with get_db_session() as session:
        # Create test tenant
        tenant = Tenant(
            tenant_id="test_trigger_2",
            name="Test Trigger Tenant 2",
            subdomain="test-trigger-2",
            is_active=True,
        )
        session.add(tenant)
        session.flush()

        # Create test product
        product = Product(
            tenant_id="test_trigger_2",
            product_id="test_prod_002",
            name="Test Product 2",
            description="Product for testing trigger constraint",
            formats=[{"agent_url": "https://creative.adcontextprotocol.org", "id": "display_728x90_image"}],
            targeting_template={},
            delivery_type="guaranteed",
            property_tags=["all_inventory"],
        )
        session.add(product)
        session.flush()

        # Create single pricing option
        pricing_option = PricingOption(
            tenant_id="test_trigger_2",
            product_id="test_prod_002",
            pricing_model="cpm",
            currency="USD",
            rate=10.0,
            is_fixed=True,
        )
        session.add(pricing_option)
        session.commit()

        # Verify pricing option exists
        stmt = select(PricingOption).filter_by(tenant_id="test_trigger_2", product_id="test_prod_002")
        pricing_check = session.scalars(stmt).first()
        assert pricing_check is not None

        # Try to manually delete the last pricing option - should be blocked by trigger
        with pytest.raises(Exception) as exc_info:
            session.delete(pricing_check)
            session.commit()

        # Verify error message mentions the constraint
        error_msg = str(exc_info.value)
        assert "Cannot delete last pricing option" in error_msg
        assert "test_prod_002" in error_msg

        # Rollback the failed transaction
        session.rollback()

        # Verify pricing option still exists after rollback
        stmt = select(PricingOption).filter_by(tenant_id="test_trigger_2", product_id="test_prod_002")
        pricing_after = session.scalars(stmt).first()
        assert pricing_after is not None, "Pricing option should still exist after blocked deletion"

        # Cleanup - delete product (which cascades to pricing_options)
        stmt = select(Product).filter_by(tenant_id="test_trigger_2", product_id="test_prod_002")
        product_to_delete = session.scalars(stmt).first()
        session.delete(product_to_delete)
        session.commit()

        # Cleanup tenant
        session.execute(text("DELETE FROM tenants WHERE tenant_id = 'test_trigger_2'"))
        session.commit()


@pytest.mark.requires_db
def test_product_deletion_with_multiple_pricing_options(integration_db):
    """Test product deletion with multiple pricing options."""
    with get_db_session() as session:
        # Create test tenant
        tenant = Tenant(
            tenant_id="test_trigger_3",
            name="Test Trigger Tenant 3",
            subdomain="test-trigger-3",
            is_active=True,
        )
        session.add(tenant)
        session.flush()

        # Create test product
        product = Product(
            tenant_id="test_trigger_3",
            product_id="test_prod_003",
            name="Test Product 3",
            description="Product with multiple pricing options",
            formats=[{"agent_url": "https://creative.adcontextprotocol.org", "id": "video_640x480_video"}],
            targeting_template={},
            delivery_type="guaranteed",
            property_tags=["all_inventory"],
        )
        session.add(product)
        session.flush()

        # Create multiple pricing options
        pricing_option_1 = PricingOption(
            tenant_id="test_trigger_3",
            product_id="test_prod_003",
            pricing_model="cpm",
            currency="USD",
            rate=15.0,
            is_fixed=True,
        )
        pricing_option_2 = PricingOption(
            tenant_id="test_trigger_3",
            product_id="test_prod_003",
            pricing_model="vcpm",
            currency="USD",
            is_fixed=False,
            price_guidance={"floor": 10.0, "p50": 12.0},
        )
        session.add_all([pricing_option_1, pricing_option_2])
        session.commit()

        # Verify product and pricing options exist
        stmt = select(PricingOption).filter_by(tenant_id="test_trigger_3", product_id="test_prod_003")
        pricing_options = session.scalars(stmt).all()
        assert len(pricing_options) == 2

        # Delete the product - should cascade all pricing options
        stmt = select(Product).filter_by(tenant_id="test_trigger_3", product_id="test_prod_003")
        product_to_delete = session.scalars(stmt).first()
        session.delete(product_to_delete)
        session.commit()

        # Verify all pricing options are deleted
        stmt = select(PricingOption).filter_by(tenant_id="test_trigger_3", product_id="test_prod_003")
        pricing_after = session.scalars(stmt).all()
        assert len(pricing_after) == 0, "All pricing options should be cascaded deleted"

        # Cleanup
        session.execute(text("DELETE FROM tenants WHERE tenant_id = 'test_trigger_3'"))
        session.commit()
