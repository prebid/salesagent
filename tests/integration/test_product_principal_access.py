"""Integration test for product principal access control.

Tests that products with allowed_principal_ids are correctly filtered
in the get_products flow.
"""

import pytest
from sqlalchemy import select

from src.core.database.database_session import get_db_session
from src.core.database.models import (
    CurrencyLimit,
    PricingOption,
    Principal,
    Product,
    PropertyTag,
    Tenant,
)
from src.core.product_conversion import convert_product_model_to_schema


@pytest.mark.requires_db
def test_product_stores_and_retrieves_allowed_principal_ids(integration_db):
    """Test that allowed_principal_ids is correctly stored and retrieved from database."""
    tenant_id = "test_principal_access"

    with get_db_session() as session:
        # Create tenant
        tenant = Tenant(
            tenant_id=tenant_id,
            name="Test Tenant",
            subdomain="test-principal",
            ad_server="mock",
            billing_plan="basic",
            is_active=True,
        )
        session.add(tenant)

        # Create required setup data
        currency_limit = CurrencyLimit(
            tenant_id=tenant_id,
            currency_code="USD",
            min_package_budget=100.0,
            max_daily_package_spend=10000.0,
        )
        session.add(currency_limit)

        property_tag = PropertyTag(
            tenant_id=tenant_id,
            tag_id="all_inventory",
            name="All Inventory",
            description="All inventory",
        )
        session.add(property_tag)

        # Create a product with allowed_principal_ids
        product = Product(
            product_id="restricted_product",
            tenant_id=tenant_id,
            name="Restricted Product",
            description="Only visible to specific principals",
            format_ids=[{"id": "display_300x250", "agent_url": "https://creative.adcontextprotocol.org"}],
            delivery_type="guaranteed",
            targeting_template={},
            implementation_config={},
            property_tags=["all_inventory"],
            allowed_principal_ids=["principal_1", "principal_2"],
        )
        session.add(product)
        session.commit()

        # Retrieve and verify
        stmt = select(Product).filter_by(product_id="restricted_product")
        retrieved_product = session.scalars(stmt).first()

        assert retrieved_product is not None
        assert retrieved_product.allowed_principal_ids == ["principal_1", "principal_2"]


@pytest.mark.requires_db
def test_product_with_null_allowed_principal_ids(integration_db):
    """Test that products with null allowed_principal_ids work correctly."""
    tenant_id = "test_null_principal"

    with get_db_session() as session:
        # Create tenant
        tenant = Tenant(
            tenant_id=tenant_id,
            name="Test Tenant",
            subdomain="test-null-principal",
            ad_server="mock",
            billing_plan="basic",
            is_active=True,
        )
        session.add(tenant)

        # Create required setup data
        currency_limit = CurrencyLimit(
            tenant_id=tenant_id,
            currency_code="USD",
            min_package_budget=100.0,
            max_daily_package_spend=10000.0,
        )
        session.add(currency_limit)

        property_tag = PropertyTag(
            tenant_id=tenant_id,
            tag_id="all_inventory",
            name="All Inventory",
            description="All inventory",
        )
        session.add(property_tag)

        # Create a product without allowed_principal_ids (visible to all)
        product = Product(
            product_id="public_product",
            tenant_id=tenant_id,
            name="Public Product",
            description="Visible to all principals",
            format_ids=[{"id": "display_300x250", "agent_url": "https://creative.adcontextprotocol.org"}],
            delivery_type="guaranteed",
            targeting_template={},
            implementation_config={},
            property_tags=["all_inventory"],
            # allowed_principal_ids not set - should be None
        )
        session.add(product)
        session.commit()

        # Retrieve and verify
        stmt = select(Product).filter_by(product_id="public_product")
        retrieved_product = session.scalars(stmt).first()

        assert retrieved_product is not None
        assert retrieved_product.allowed_principal_ids is None


@pytest.mark.requires_db
def test_convert_product_includes_allowed_principal_ids(integration_db):
    """Test that convert_product_model_to_schema includes allowed_principal_ids."""
    tenant_id = "test_convert_principal"

    with get_db_session() as session:
        # Create tenant and setup
        tenant = Tenant(
            tenant_id=tenant_id,
            name="Test Tenant",
            subdomain="test-convert",
            ad_server="mock",
            billing_plan="basic",
            is_active=True,
        )
        session.add(tenant)

        currency_limit = CurrencyLimit(
            tenant_id=tenant_id,
            currency_code="USD",
            min_package_budget=100.0,
            max_daily_package_spend=10000.0,
        )
        session.add(currency_limit)

        property_tag = PropertyTag(
            tenant_id=tenant_id,
            tag_id="all_inventory",
            name="All Inventory",
            description="All inventory",
        )
        session.add(property_tag)

        # Create product with restrictions
        product_model = Product(
            product_id="convert_test_product",
            tenant_id=tenant_id,
            name="Convert Test Product",
            description="Test conversion",
            format_ids=[{"id": "display_300x250", "agent_url": "https://creative.adcontextprotocol.org"}],
            delivery_type="guaranteed",
            targeting_template={},
            implementation_config={},
            property_tags=["all_inventory"],
            allowed_principal_ids=["allowed_principal"],
        )
        session.add(product_model)

        # Create pricing option (required for valid products)
        pricing_option = PricingOption(
            tenant_id=tenant_id,
            product_id="convert_test_product",
            pricing_model="cpm",
            rate=10.0,
            currency="USD",
            is_fixed=True,
        )
        session.add(pricing_option)
        session.commit()

        # Refresh to load relationships
        session.refresh(product_model)

        # Convert to schema
        product_schema = convert_product_model_to_schema(product_model)

        # Verify allowed_principal_ids is included
        assert product_schema.allowed_principal_ids == ["allowed_principal"]


@pytest.mark.requires_db
def test_allowed_principal_ids_excluded_from_serialization(integration_db):
    """Test that allowed_principal_ids is excluded from API serialization."""
    tenant_id = "test_serialize_principal"

    with get_db_session() as session:
        # Create tenant and setup
        tenant = Tenant(
            tenant_id=tenant_id,
            name="Test Tenant",
            subdomain="test-serialize",
            ad_server="mock",
            billing_plan="basic",
            is_active=True,
        )
        session.add(tenant)

        currency_limit = CurrencyLimit(
            tenant_id=tenant_id,
            currency_code="USD",
            min_package_budget=100.0,
            max_daily_package_spend=10000.0,
        )
        session.add(currency_limit)

        property_tag = PropertyTag(
            tenant_id=tenant_id,
            tag_id="all_inventory",
            name="All Inventory",
            description="All inventory",
        )
        session.add(property_tag)

        # Create product with restrictions
        product_model = Product(
            product_id="serialize_test_product",
            tenant_id=tenant_id,
            name="Serialize Test Product",
            description="Test serialization",
            format_ids=[{"id": "display_300x250", "agent_url": "https://creative.adcontextprotocol.org"}],
            delivery_type="guaranteed",
            targeting_template={},
            implementation_config={},
            property_tags=["all_inventory"],
            allowed_principal_ids=["secret_principal"],
        )
        session.add(product_model)

        pricing_option = PricingOption(
            tenant_id=tenant_id,
            product_id="serialize_test_product",
            pricing_model="cpm",
            rate=10.0,
            currency="USD",
            is_fixed=True,
        )
        session.add(pricing_option)
        session.commit()

        session.refresh(product_model)

        # Convert to schema and serialize
        product_schema = convert_product_model_to_schema(product_model)
        serialized = product_schema.model_dump()

        # allowed_principal_ids should NOT be in serialized output
        assert "allowed_principal_ids" not in serialized
        # But it should still be accessible on the object
        assert product_schema.allowed_principal_ids == ["secret_principal"]


@pytest.mark.requires_db
def test_principal_model_exists_for_access_control(integration_db):
    """Test that Principal model can be created and used for access control."""
    tenant_id = "test_principal_model"

    with get_db_session() as session:
        # Create tenant
        tenant = Tenant(
            tenant_id=tenant_id,
            name="Test Tenant",
            subdomain="test-principal-model",
            ad_server="mock",
            billing_plan="basic",
            is_active=True,
        )
        session.add(tenant)

        # Create principals (with required fields)
        # platform_mappings must have at least one platform (google_ad_manager or mock)
        principal1 = Principal(
            principal_id="adv_001",
            tenant_id=tenant_id,
            name="Advertiser One",
            platform_mappings={"mock": {"advertiser_id": "mock_adv_001"}},
            access_token="test_token_adv_001",
        )
        principal2 = Principal(
            principal_id="adv_002",
            tenant_id=tenant_id,
            name="Advertiser Two",
            platform_mappings={"mock": {"advertiser_id": "mock_adv_002"}},
            access_token="test_token_adv_002",
        )
        session.add_all([principal1, principal2])
        session.commit()

        # Query principals
        stmt = select(Principal).filter_by(tenant_id=tenant_id).order_by(Principal.name)
        principals = session.scalars(stmt).all()

        assert len(principals) == 2
        assert principals[0].name == "Advertiser One"
        assert principals[1].name == "Advertiser Two"
