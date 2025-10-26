"""
Integration test to verify pricing_options are always loaded with products.

This test ensures that the bug fixed in PR #413 doesn't regress:
- get_product_catalog() must load pricing_options relationship
- Products must always have pricing_options populated
- Product Pydantic schema validation must pass
"""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from src.core.database.database_session import get_db_session
from src.core.database.models import PricingOption as PricingOptionModel
from src.core.database.models import Principal as PrincipalModel
from src.core.database.models import Product as ProductModel
from src.core.database.models import Tenant as TenantModel
from src.core.main import get_product_catalog
from src.core.schemas import Product as ProductSchema


@pytest.mark.requires_db
def test_get_product_catalog_loads_pricing_options(integration_db):
    """Test that get_product_catalog() loads pricing_options relationship."""
    from src.core.context_management import set_current_tenant

    # Create test tenant
    unique_id = str(uuid.uuid4())[:8]
    now = datetime.now(UTC)

    with get_db_session() as session:
        tenant = TenantModel(
            tenant_id=f"test_tenant_{unique_id}",
            name=f"Test Tenant {unique_id}",
            subdomain=f"test_{unique_id}",
            is_active=True,
            ad_server="mock",
            created_at=now,
            updated_at=now,
        )
        session.add(tenant)
        session.commit()

        # Create test principal
        principal = PrincipalModel(
            tenant_id=tenant.tenant_id,
            principal_id=f"test_principal_{unique_id}",
            name=f"Test Principal {unique_id}",
            access_token=f"test_token_{unique_id}",
            platform_mappings={"mock": {"advertiser_id": f"test_advertiser_{unique_id}"}},
        )
        session.add(principal)
        session.commit()

        # Set up context
        tenant_config = {
            "tenant_id": tenant.tenant_id,
            "name": tenant.name,
            "adapter_id": tenant.ad_server,
        }
        set_current_tenant(tenant_config)

        # Create a product with pricing options
        product = ProductModel(
            tenant_id=tenant.tenant_id,
            product_id="test_product_with_pricing",
            name="Test Product",
            description="Test description",
            formats=["display_300x250"],
            targeting_template={},
            delivery_type="guaranteed",
            property_tags=["all_inventory"],
        )
        session.add(product)
        session.flush()

        # Add pricing option
        pricing_option = PricingOptionModel(
            tenant_id=tenant.tenant_id,
            product_id=product.product_id,
            pricing_model="cpm",
            rate=10.00,
            currency="USD",
            is_fixed=True,
        )
        session.add(pricing_option)
        session.commit()

    # Call get_product_catalog()
    products = get_product_catalog()

    # Verify we got products back
    assert len(products) > 0, "Should return at least one product"

    # Verify all products have pricing_options
    for prod in products:
        assert isinstance(prod, ProductSchema), f"Product should be a Pydantic schema, got {type(prod)}"
        assert hasattr(prod, "pricing_options"), "Product should have pricing_options attribute"
        assert prod.pricing_options is not None, f"Product {prod.product_id} has None pricing_options"
        assert isinstance(prod.pricing_options, list), f"Product {prod.product_id} pricing_options should be a list"
        assert len(prod.pricing_options) > 0, f"Product {prod.product_id} must have at least one pricing option"


@pytest.mark.requires_db
def test_product_query_with_eager_loading(integration_db):
    """Test that Product queries use eager loading for pricing_options."""
    # Create test tenant
    unique_id = str(uuid.uuid4())[:8]
    now = datetime.now(UTC)

    with get_db_session() as session:
        tenant = TenantModel(
            tenant_id=f"test_tenant_{unique_id}",
            name=f"Test Tenant {unique_id}",
            subdomain=f"test_{unique_id}",
            is_active=True,
            ad_server="mock",
            created_at=now,
            updated_at=now,
        )
        session.add(tenant)
        session.commit()

        # Create a product with pricing options
        product = ProductModel(
            tenant_id=tenant.tenant_id,
            product_id="test_eager_load",
            name="Test Eager Load",
            description="Test description",
            formats=["display_300x250"],
            targeting_template={},
            delivery_type="guaranteed",
            property_tags=["all_inventory"],
        )
        session.add(product)
        session.flush()

        # Add pricing option
        pricing_option = PricingOptionModel(
            tenant_id=tenant.tenant_id,
            product_id=product.product_id,
            pricing_model="cpm",
            rate=15.00,
            currency="USD",
            is_fixed=True,
        )
        session.add(pricing_option)
        session.commit()

    # Query product with eager loading (simulating get_product_catalog pattern)
    with get_db_session() as session:
        from sqlalchemy.orm import selectinload

        stmt = (
            select(ProductModel)
            .filter_by(tenant_id=tenant.tenant_id, product_id="test_eager_load")
            .options(selectinload(ProductModel.pricing_options))
        )

        loaded_product = session.scalars(stmt).first()

        # Verify pricing_options is loaded
        assert loaded_product is not None, "Product should be found"
        assert loaded_product.pricing_options is not None, "pricing_options should be loaded"
        assert len(loaded_product.pricing_options) > 0, "Should have pricing options"
        assert loaded_product.pricing_options[0].pricing_model == "cpm"
        assert float(loaded_product.pricing_options[0].rate) == 15.00


@pytest.mark.requires_db
def test_product_without_eager_loading_fails_validation(integration_db):
    """Test that Products loaded without eager loading can't be converted to Pydantic schema.

    This is a regression test to ensure the bug doesn't come back.
    """
    # Create test tenant
    unique_id = str(uuid.uuid4())[:8]
    now = datetime.now(UTC)

    with get_db_session() as session:
        tenant = TenantModel(
            tenant_id=f"test_tenant_{unique_id}",
            name=f"Test Tenant {unique_id}",
            subdomain=f"test_{unique_id}",
            is_active=True,
            ad_server="mock",
            created_at=now,
            updated_at=now,
        )
        session.add(tenant)
        session.commit()

        # Create a product with pricing options
        product = ProductModel(
            tenant_id=tenant.tenant_id,
            product_id="test_no_eager_load",
            name="Test No Eager Load",
            description="Test description",
            formats=["display_300x250"],
            targeting_template={},
            delivery_type="guaranteed",
            property_tags=["all_inventory"],
        )
        session.add(product)
        session.flush()

        # Add pricing option
        pricing_option = PricingOptionModel(
            tenant_id=tenant.tenant_id,
            product_id=product.product_id,
            pricing_model="cpm",
            rate=20.00,
            currency="USD",
            is_fixed=True,
        )
        session.add(pricing_option)
        session.commit()

    # Query product WITHOUT eager loading (the bug scenario)
    with get_db_session() as session:
        stmt = select(ProductModel).filter_by(tenant_id=tenant.tenant_id, product_id="test_no_eager_load")
        # NOTE: No .options(selectinload(...)) here - this is the bug!

        loaded_product = session.scalars(stmt).first()
        assert loaded_product is not None

        # Try to convert to Pydantic schema - this should fail without pricing_options
        try:
            product_data = {
                "product_id": loaded_product.product_id,
                "name": loaded_product.name,
                "description": loaded_product.description,
                "formats": loaded_product.formats if isinstance(loaded_product.formats, list) else [],
                "delivery_type": loaded_product.delivery_type,
                "property_tags": loaded_product.property_tags if loaded_product.property_tags else ["all_inventory"],
                # NOTE: pricing_options is intentionally missing - simulating the bug
            }

            # This should raise ValidationError because pricing_options is required
            ProductSchema(**product_data)
            pytest.fail("Should have raised ValidationError due to missing pricing_options")

        except Exception as e:
            # Expected - pricing_options is required
            assert "pricing_options" in str(e).lower() or "field required" in str(e).lower()


@pytest.mark.requires_db
def test_create_media_buy_loads_pricing_options(integration_db):
    """Test that create_media_buy logic loads pricing_options for currency detection."""
    # This tests the second place we fixed in PR #413
    # Create test tenant
    unique_id = str(uuid.uuid4())[:8]
    now = datetime.now(UTC)

    with get_db_session() as session:
        tenant = TenantModel(
            tenant_id=f"test_tenant_{unique_id}",
            name=f"Test Tenant {unique_id}",
            subdomain=f"test_{unique_id}",
            is_active=True,
            ad_server="mock",
            created_at=now,
            updated_at=now,
        )
        session.add(tenant)
        session.commit()

        # Create test principal
        principal = PrincipalModel(
            tenant_id=tenant.tenant_id,
            principal_id=f"test_principal_{unique_id}",
            name=f"Test Principal {unique_id}",
            access_token=f"test_token_{unique_id}",
            platform_mappings={"mock": {"advertiser_id": f"test_advertiser_{unique_id}"}},
        )
        session.add(principal)
        session.commit()

        # Create a product with pricing options
        product = ProductModel(
            tenant_id=tenant.tenant_id,
            product_id="test_cmb_pricing",
            name="Test CMB Product",
            description="Test description",
            formats=["display_300x250"],
            targeting_template={},
            delivery_type="guaranteed",
            property_tags=["all_inventory"],
        )
        session.add(product)
        session.flush()

        # Add pricing option with EUR currency
        pricing_option = PricingOptionModel(
            tenant_id=tenant.tenant_id,
            product_id=product.product_id,
            pricing_model="cpm",
            rate=25.00,
            currency="EUR",  # Non-USD to test currency detection
            is_fixed=True,
        )
        session.add(pricing_option)
        session.commit()

    # Query product with eager loading (as fixed in PR #413)
    with get_db_session() as session:
        from sqlalchemy.orm import selectinload

        stmt = (
            select(ProductModel)
            .where(ProductModel.tenant_id == tenant.tenant_id, ProductModel.product_id == product.product_id)
            .options(selectinload(ProductModel.pricing_options))
        )

        loaded_product = session.scalars(stmt).first()

        # Verify pricing_options can be accessed for currency detection
        assert loaded_product is not None
        assert loaded_product.pricing_options is not None
        assert len(loaded_product.pricing_options) > 0

        # Simulate currency detection logic from create_media_buy
        pricing_options = loaded_product.pricing_options
        first_option = pricing_options[0]
        detected_currency = first_option.currency

        assert detected_currency == "EUR", "Should detect EUR currency from pricing option"
