"""
Smoke test for integration_v2 infrastructure.

Validates that:
1. integration_db fixture works
2. Pricing helpers create products correctly
3. PricingOption relationship loads properly
"""

import pytest
from sqlalchemy import select

from src.core.database.database_session import get_db_session
from src.core.database.models import Product, Tenant
from tests.integration.conftest import (
    create_auction_product,
    create_flat_rate_product,
    create_test_product_with_pricing,
)


@pytest.mark.requires_db
class TestPricingHelpers:
    """Test pricing helper utilities for integration_v2."""

    def test_create_product_with_cpm_pricing(self, integration_db):
        """Test create_test_product_with_pricing creates Product with CPM pricing."""
        with get_db_session() as session:
            # Create tenant
            tenant = Tenant(
                tenant_id="test_tenant",
                name="Test Tenant",
                subdomain="test",
            )
            session.add(tenant)
            session.commit()

            # Create product with CPM pricing using helper
            product = create_test_product_with_pricing(
                session=session,
                tenant_id="test_tenant",
                product_id="test_prod_1",
                name="Test CPM Product",
                pricing_model="CPM",
                rate="15.00",
                is_fixed=True,
                currency="USD",
            )
            session.commit()

            # Verify product created
            assert product.product_id == "test_prod_1"
            assert product.name == "Test CPM Product"

            # Verify pricing_options relationship
            assert len(product.pricing_options) == 1
            pricing = product.pricing_options[0]
            assert pricing.pricing_model == "cpm"  # Lowercase per v2 pricing model
            assert float(pricing.rate) == 15.0
            assert pricing.is_fixed is True
            assert pricing.currency == "USD"

    def test_create_auction_product(self, integration_db):
        """Test create_auction_product creates Product with auction pricing."""
        with get_db_session() as session:
            # Create tenant
            tenant = Tenant(
                tenant_id="test_tenant",
                name="Test Tenant",
                subdomain="test",
            )
            session.add(tenant)
            session.commit()

            # Create auction product using helper
            product = create_auction_product(
                session=session,
                tenant_id="test_tenant",
                product_id="test_auction_1",
                name="Auction Product",
                pricing_model="CPM",
                floor_cpm="2.50",
            )
            session.commit()

            # Verify auction pricing
            assert len(product.pricing_options) == 1
            pricing = product.pricing_options[0]
            assert pricing.pricing_model == "cpm"  # Lowercase per v2 pricing model
            assert float(pricing.rate) == 2.50
            assert pricing.is_fixed is False  # Auction pricing
            assert pricing.currency == "USD"

    def test_create_flat_rate_product(self, integration_db):
        """Test create_flat_rate_product creates Product with FLAT_RATE pricing."""
        with get_db_session() as session:
            # Create tenant
            tenant = Tenant(
                tenant_id="test_tenant",
                name="Test Tenant",
                subdomain="test",
            )
            session.add(tenant)
            session.commit()

            # Create flat-rate product using helper
            product = create_flat_rate_product(
                session=session,
                tenant_id="test_tenant",
                product_id="test_flat_1",
                name="Flat Rate Product",
                rate="5000.00",
            )
            session.commit()

            # Verify flat-rate pricing
            assert len(product.pricing_options) == 1
            pricing = product.pricing_options[0]
            assert pricing.pricing_model == "flat_rate"  # Lowercase per v2 pricing model
            assert float(pricing.rate) == 5000.0
            assert pricing.is_fixed is True
            assert pricing.currency == "USD"

    def test_auto_generated_product_id(self, integration_db):
        """Test pricing helper auto-generates product_id if not provided."""
        with get_db_session() as session:
            # Create tenant
            tenant = Tenant(
                tenant_id="test_tenant",
                name="Test Tenant",
                subdomain="test",
            )
            session.add(tenant)
            session.commit()

            # Create product without specifying product_id
            product = create_test_product_with_pricing(
                session=session,
                tenant_id="test_tenant",
                # product_id omitted - should auto-generate
                name="Auto ID Product",
                pricing_model="CPM",
                rate="10.00",
            )
            session.commit()

            # Verify product_id was auto-generated
            assert product.product_id is not None
            assert product.product_id.startswith("test_product_")
            assert len(product.pricing_options) == 1

    def test_multiple_products_with_pricing(self, integration_db):
        """Test creating multiple products with different pricing models."""
        with get_db_session() as session:
            # Create tenant
            tenant = Tenant(
                tenant_id="test_tenant",
                name="Test Tenant",
                subdomain="test",
            )
            session.add(tenant)
            session.commit()

            # Create multiple products
            cpm_product = create_test_product_with_pricing(
                session=session,
                tenant_id="test_tenant",
                product_id="cpm_prod",
                pricing_model="CPM",
                rate="15.00",
            )

            vcpm_product = create_test_product_with_pricing(
                session=session,
                tenant_id="test_tenant",
                product_id="vcpm_prod",
                pricing_model="VCPM",
                rate="20.00",
            )

            cpc_product = create_test_product_with_pricing(
                session=session,
                tenant_id="test_tenant",
                product_id="cpc_prod",
                pricing_model="CPC",
                rate="0.50",
            )

            session.commit()

            # Query all products
            stmt = select(Product).where(Product.tenant_id == "test_tenant")
            products = session.scalars(stmt).all()

            assert len(products) == 3
            pricing_models = {p.pricing_options[0].pricing_model for p in products}
            assert pricing_models == {"cpm", "vcpm", "cpc"}  # Lowercase per v2 pricing model
