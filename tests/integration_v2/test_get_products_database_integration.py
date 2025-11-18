#!/usr/bin/env python3
"""
Database Integration Tests for get_products - Real Database Tests (V2 Pricing Model)

These tests validate the actual database-to-schema transformation with real ORM models
using the new pricing_options table, to catch field access bugs that mocks would miss.

MIGRATION NOTE: Migrated from tests/integration/test_get_products_database_integration.py
to use the new pricing_options model.
"""

import threading
import time
from decimal import Decimal

import pytest
from sqlalchemy import delete, func, select

from src.core.database.database_session import get_db_session
from src.core.database.models import PricingOption, Tenant
from src.core.database.models import Product as ProductModel
from tests.integration_v2.conftest import (
    add_required_setup_data,
    create_auction_product,
    create_test_product_with_pricing,
)
from tests.utils.database_helpers import create_tenant_with_timestamps

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


@pytest.mark.requires_db
class TestDatabaseProductsIntegration:
    """Integration tests using real database without excessive mocking."""

    @pytest.fixture
    def test_tenant_id(self, integration_db):
        """Create a test tenant for database integration tests."""
        tenant_id = "test_integration_tenant"
        with get_db_session() as session:
            # Clean up any existing test data
            session.execute(delete(PricingOption).where(PricingOption.tenant_id == tenant_id))
            session.execute(delete(ProductModel).where(ProductModel.tenant_id == tenant_id))
            session.execute(delete(Tenant).where(Tenant.tenant_id == tenant_id))

            # Create test tenant
            tenant = create_tenant_with_timestamps(
                tenant_id=tenant_id, name="Test Integration Tenant", subdomain="test-integration"
            )
            session.add(tenant)
            session.commit()

            # Add required setup data
            add_required_setup_data(session, tenant_id)
            session.commit()

        yield tenant_id

        # Cleanup
        with get_db_session() as session:
            session.execute(delete(PricingOption).where(PricingOption.tenant_id == tenant_id))
            session.execute(delete(ProductModel).where(ProductModel.tenant_id == tenant_id))
            session.execute(delete(Tenant).where(Tenant.tenant_id == tenant_id))
            session.commit()

    def test_database_model_to_schema_conversion_without_mocking(self, test_tenant_id):
        """Test actual ORM model to Pydantic schema conversion with real database."""
        # Create a real product with pricing in the database
        with get_db_session() as session:
            db_product = create_test_product_with_pricing(
                session=session,
                tenant_id=test_tenant_id,
                product_id="test_prod_001",
                name="Integration Test Product",
                description="A test product for database integration testing",
                format_ids=[{"agent_url": "https://test.com", "id": "300x250"}],
                targeting_template={"geo": ["country"], "device": ["desktop", "mobile"]},
                delivery_type="non_guaranteed",
                pricing_model="CPM",
                rate=Decimal("5.50"),
                is_fixed=False,
                currency="USD",
                min_spend_per_package=Decimal("1000.00"),
                measurement={"viewability": True, "brand_safety": True},
                creative_policy={"max_file_size": "5MB"},
                price_guidance={"floor": 2.0, "p50": 5.0, "p75": 8.0, "p90": 10.0},
                is_custom=False,
                countries=["US", "CA"],
                implementation_config={"gam_placement_id": "12345"},
            )
            session.commit()

            # Refresh to get the actual database object
            session.refresh(db_product)

            # Test database field access on Product model
            assert hasattr(db_product, "product_id")
            assert hasattr(db_product, "name")
            assert hasattr(db_product, "description")
            assert hasattr(db_product, "format_ids")
            assert hasattr(db_product, "delivery_type")

            # Test pricing_options relationship exists
            assert hasattr(db_product, "pricing_options")
            assert len(db_product.pricing_options) == 1

            # Test PricingOption fields
            pricing = db_product.pricing_options[0]
            assert hasattr(pricing, "pricing_model")
            assert hasattr(pricing, "rate")
            assert hasattr(pricing, "currency")
            assert hasattr(pricing, "is_fixed")
            assert pricing.pricing_model == "cpm"
            assert pricing.rate == Decimal("5.50")
            assert pricing.is_fixed is False
            assert pricing.currency == "USD"

            # These legacy fields should NOT exist on Product model
            assert not hasattr(db_product, "is_fixed_price")  # Moved to PricingOption.is_fixed
            assert not hasattr(db_product, "cpm")  # Moved to PricingOption.rate
            assert not hasattr(db_product, "pricing")  # Never existed, would cause bugs

    def test_database_field_access_validation(self, test_tenant_id):
        """Validate that we only access database fields that actually exist."""
        with get_db_session() as session:
            db_product = create_test_product_with_pricing(
                session=session,
                tenant_id=test_tenant_id,
                product_id="test_field_access",
                name="Field Access Test",
                pricing_model="CPM",
                rate=Decimal("10.00"),
            )
            session.commit()
            session.refresh(db_product)

            # Test all fields that SHOULD exist on Product model
            valid_product_fields = [
                "product_id",
                "name",
                "description",
                "format_ids",
                "delivery_type",
                "targeting_template",
                "measurement",
                "creative_policy",
                "is_custom",
                "countries",
                "implementation_config",
                "property_tags",
                "pricing_options",  # Relationship
            ]

            for field in valid_product_fields:
                assert hasattr(db_product, field), f"Product model missing expected field: {field}"
                # Access the field to ensure no AttributeError
                getattr(db_product, field)

            # Test that legacy pricing fields raise AttributeError
            legacy_fields = ["is_fixed_price", "cpm", "min_spend", "pricing"]
            for field in legacy_fields:
                with pytest.raises(AttributeError, match=f"object has no attribute '{field}'"):
                    getattr(db_product, field)

            # Test PricingOption fields
            assert len(db_product.pricing_options) == 1
            pricing = db_product.pricing_options[0]
            valid_pricing_fields = [
                "pricing_model",
                "rate",
                "currency",
                "is_fixed",
                "price_guidance",
                "min_spend_per_package",
            ]

            for field in valid_pricing_fields:
                assert hasattr(pricing, field), f"PricingOption missing expected field: {field}"
                getattr(pricing, field)

    def test_multiple_products_database_conversion(self, test_tenant_id):
        """Test conversion with multiple products of different types."""
        with get_db_session() as session:
            # Guaranteed product with fixed pricing
            create_test_product_with_pricing(
                session=session,
                tenant_id=test_tenant_id,
                product_id="test_display_001",
                name="Display Banner Product",
                description="Display advertising product",
                format_ids=[{"agent_url": "https://test.com", "id": "300x250"}],
                delivery_type="guaranteed",
                pricing_model="CPM",
                rate=Decimal("10.00"),
                is_fixed=True,
                is_custom=False,
            )

            # Non-guaranteed product with auction pricing
            create_auction_product(
                session=session,
                tenant_id=test_tenant_id,
                product_id="test_video_001",
                name="Video Ad Product",
                description="Video advertising product",
                format_ids=[{"agent_url": "https://test.com", "id": "video_15s"}],
                delivery_type="non_guaranteed",
                pricing_model="CPM",
                floor_cpm=Decimal("5.00"),
                is_custom=True,
            )

            session.commit()

        # Query products directly
        with get_db_session() as session:
            stmt = select(ProductModel).filter_by(tenant_id=test_tenant_id).order_by(ProductModel.product_id)
            products = session.scalars(stmt).all()

            assert len(products) == 2

            # Verify guaranteed product
            display_product = next(p for p in products if p.product_id == "test_display_001")
            assert display_product.name == "Display Banner Product"
            assert len(display_product.pricing_options) == 1
            assert display_product.pricing_options[0].pricing_model == "cpm"
            assert display_product.pricing_options[0].rate == Decimal("10.00")
            assert display_product.pricing_options[0].is_fixed is True

            # Verify auction product
            video_product = next(p for p in products if p.product_id == "test_video_001")
            assert video_product.name == "Video Ad Product"
            assert len(video_product.pricing_options) == 1
            assert video_product.pricing_options[0].pricing_model == "cpm"
            assert video_product.pricing_options[0].rate == Decimal("5.00")
            assert video_product.pricing_options[0].is_fixed is False
            assert video_product.pricing_options[0].price_guidance is not None


class TestDatabasePerformanceOptimization:
    """Performance-optimized database tests with faster cleanup and connection pooling."""

    @pytest.fixture
    def optimized_test_setup(self, integration_db):
        """Performance-optimized test setup with transaction rollbacks."""
        tenant_id = "perf_test_tenant"

        # Use a single transaction for the entire test setup
        with get_db_session() as session:
            # Clean up any existing test data
            try:
                session.execute(delete(PricingOption).where(PricingOption.tenant_id == tenant_id))
                session.execute(delete(ProductModel).where(ProductModel.tenant_id == tenant_id))
                session.execute(delete(Tenant).where(Tenant.tenant_id == tenant_id))
            except Exception:
                # Tables might not exist yet, that's OK
                session.rollback()

            # Create test tenant with proper timestamps
            tenant = create_tenant_with_timestamps(
                tenant_id=tenant_id, name="Performance Test Tenant", subdomain="perf-test", billing_plan="test"
            )
            session.add(tenant)
            session.flush()

            # Add required setup data
            add_required_setup_data(session, tenant_id)
            session.commit()

            yield tenant_id

            # Cleanup
            session.execute(delete(PricingOption).where(PricingOption.tenant_id == tenant_id))
            session.execute(delete(ProductModel).where(ProductModel.tenant_id == tenant_id))
            session.execute(delete(Tenant).where(Tenant.tenant_id == tenant_id))
            session.commit()

    def test_large_dataset_conversion_performance(self, optimized_test_setup):
        """Test database conversion performance with large datasets."""
        tenant_id = optimized_test_setup

        # Create large dataset (100 products)
        with get_db_session() as session:
            for i in range(100):
                create_test_product_with_pricing(
                    session=session,
                    tenant_id=tenant_id,
                    product_id=f"perf_test_{i:03d}",
                    name=f"Performance Test Product {i}",
                    description=f"Product {i} for performance testing",
                    format_ids=[{"agent_url": "https://test.com", "id": "300x250"}],
                    targeting_template={"geo": ["US"], "device": ["desktop", "mobile"]},
                    delivery_type="non_guaranteed",
                    pricing_model="CPM",
                    rate=Decimal("5.0") + (Decimal(str(i)) * Decimal("0.1")),
                    is_fixed=False,
                    is_custom=False,
                )

            session.commit()

        # Measure query performance
        start_time = time.time()

        with get_db_session() as session:
            stmt = select(ProductModel).filter_by(tenant_id=tenant_id)
            products = session.scalars(stmt).all()

            # Force load pricing_options relationships
            for product in products:
                _ = product.pricing_options

        query_time = time.time() - start_time

        # Verify results and performance
        assert len(products) == 100
        assert query_time < 2.0, f"Query took {query_time:.2f}s, expected < 2.0s"

        # Verify all products have pricing_options
        for i, product in enumerate(products):
            assert len(product.pricing_options) == 1
            assert product.product_id == f"perf_test_{i:03d}"
            expected_rate = Decimal("5.0") + (Decimal(str(i)) * Decimal("0.1"))
            assert product.pricing_options[0].rate == expected_rate

        # Performance regression test
        print(f"✅ Queried {len(products)} products with pricing in {query_time:.3f}s")

    def test_concurrent_field_access(self, optimized_test_setup):
        """Test concurrent access to database fields to catch race conditions."""
        tenant_id = optimized_test_setup

        # Create test product
        with get_db_session() as session:
            create_test_product_with_pricing(
                session=session,
                tenant_id=tenant_id,
                product_id="concurrent_test_001",
                name="Concurrent Test Product",
                description="Product for concurrent field access testing",
                pricing_model="CPM",
                rate=Decimal("10.00"),
            )
            session.commit()

        results = []
        errors = []

        def access_fields():
            """Function to access fields concurrently."""
            try:
                with get_db_session() as session:
                    stmt = select(ProductModel).filter_by(tenant_id=tenant_id, product_id="concurrent_test_001")
                    db_product = session.scalars(stmt).first()

                    # Test concurrent field access
                    field_values = {
                        "product_id": db_product.product_id,
                        "name": db_product.name,
                        "delivery_type": db_product.delivery_type,
                        "pricing_model": db_product.pricing_options[0].pricing_model,
                        "rate": db_product.pricing_options[0].rate,
                        "is_fixed": db_product.pricing_options[0].is_fixed,
                    }

                    # Test that accessing legacy fields fails consistently
                    try:
                        _ = db_product.is_fixed_price
                        errors.append("Should have failed accessing legacy 'is_fixed_price' field")
                    except AttributeError:
                        pass  # Expected

                    try:
                        _ = db_product.cpm
                        errors.append("Should have failed accessing legacy 'cpm' field")
                    except AttributeError:
                        pass  # Expected

                    results.append(field_values)

            except Exception as e:
                errors.append(str(e))

        # Run concurrent field access (10 threads)
        threads = []
        for _ in range(10):
            thread = threading.Thread(target=access_fields)
            threads.append(thread)
            thread.start()

        # Wait for all threads to complete
        for thread in threads:
            thread.join()

        # Verify results
        assert len(errors) == 0, f"Concurrent access errors: {errors}"
        assert len(results) == 10, f"Expected 10 results, got {len(results)}"

        # Verify all results are consistent
        expected_values = {
            "product_id": "concurrent_test_001",
            "name": "Concurrent Test Product",
            "delivery_type": "guaranteed_impressions",  # Default from helper
            "pricing_model": "cpm",
            "rate": Decimal("10.00"),
            "is_fixed": True,  # Default from helper
        }

        for result in results:
            for key, expected_value in expected_values.items():
                assert result[key] == expected_value, f"Inconsistent {key}: {result[key]} != {expected_value}"

    @pytest.mark.slow
    def test_database_connection_pooling_efficiency(self, integration_db):
        """Test that connection pooling works efficiently under load."""
        results = []
        start_time = time.time()

        def database_operation(operation_id):
            """Simulate database operation that would use connection pooling."""
            try:
                with get_db_session() as session:
                    # Simulate typical database operations
                    product_count = session.scalar(select(func.count()).select_from(ProductModel))
                    tenant_count = session.scalar(select(func.count()).select_from(Tenant))
                    pricing_count = session.scalar(select(func.count()).select_from(PricingOption))

                    # Record timing for this operation
                    operation_time = time.time() - start_time
                    results.append(
                        {
                            "operation_id": operation_id,
                            "time": operation_time,
                            "product_count": product_count,
                            "tenant_count": tenant_count,
                            "pricing_count": pricing_count,
                        }
                    )

            except Exception as e:
                results.append({"operation_id": operation_id, "error": str(e)})

        # Run multiple concurrent database operations
        threads = []
        for i in range(20):
            thread = threading.Thread(target=database_operation, args=(i,))
            threads.append(thread)
            thread.start()

        # Wait for all operations to complete
        for thread in threads:
            thread.join()

        total_time = time.time() - start_time

        # Verify all operations completed successfully
        errors = [r for r in results if "error" in r]
        assert len(errors) == 0, f"Database operations failed: {errors}"

        # Verify connection pooling efficiency
        assert len(results) == 20, "All operations should complete"
        assert total_time < 5.0, f"Connection pooling should be efficient: {total_time:.2f}s"

        # Verify no connection leaks or deadlocks
        successful_operations = [r for r in results if "error" not in r]
        assert len(successful_operations) == 20, "All operations should succeed with pooling"

        print(f"✅ Completed 20 parallel database operations in {total_time:.3f}s")
