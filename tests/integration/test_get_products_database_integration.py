#!/usr/bin/env python3
"""
Database Integration Tests for get_products - Real Database Tests

These tests validate the actual database-to-schema transformation with real ORM models
to catch field access bugs that mocks would miss.

This addresses the gap identified in issue #161 where a 'Product' object has no attribute 'pricing'
error reached production because tests over-mocked the database layer.
"""

import threading
import time
from decimal import Decimal
from typing import Any

import pytest

from product_catalog_providers.database import DatabaseProductCatalog
from src.core.database.database_session import get_db_session
from src.core.database.models import Product as ProductModel
from src.core.database.models import Tenant
from src.core.schemas import Product as ProductSchema
from tests.utils.database_helpers import create_tenant_with_timestamps


class TestDatabaseProductsIntegration:
    """Integration tests using real database without excessive mocking."""

    @pytest.fixture
    def test_tenant_id(self):
        """Create a test tenant for database integration tests."""
        tenant_id = "test_integration_tenant"
        with get_db_session() as session:
            # Clean up any existing test data
            session.query(ProductModel).filter_by(tenant_id=tenant_id).delete()
            session.query(Tenant).filter_by(tenant_id=tenant_id).delete()

            # Create test tenant
            tenant = create_tenant_with_timestamps(
                tenant_id=tenant_id, name="Test Integration Tenant", subdomain="test-integration"
            )
            session.add(tenant)
            session.commit()

        yield tenant_id

        # Cleanup
        with get_db_session() as session:
            session.query(ProductModel).filter_by(tenant_id=tenant_id).delete()
            session.query(Tenant).filter_by(tenant_id=tenant_id).delete()
            session.commit()

    @pytest.fixture
    def sample_product_data(self) -> dict[str, Any]:
        """Sample product data matching database schema exactly."""
        return {
            "product_id": "test_prod_001",
            "name": "Integration Test Product",
            "description": "A test product for database integration testing",
            "formats": ["display_300x250", "display_728x90"],
            "targeting_template": {"geo": ["country"], "device": ["desktop", "mobile"]},
            "delivery_type": "non_guaranteed",
            "is_fixed_price": False,
            "cpm": Decimal("5.50"),
            "min_spend": Decimal("1000.00"),
            "measurement": {"viewability": True, "brand_safety": True},
            "creative_policy": {"max_file_size": "5MB"},
            "price_guidance": {"min": 2.0, "max": 8.0},
            "is_custom": False,
            "expires_at": None,
            "countries": ["US", "CA"],
            "implementation_config": {"gam_placement_id": "12345"},
        }

    def test_database_model_to_schema_conversion_without_mocking(self, test_tenant_id, sample_product_data):
        """Test actual ORM model to Pydantic schema conversion with real database."""
        # Create a real product in the database
        with get_db_session() as session:
            db_product = ProductModel(tenant_id=test_tenant_id, **sample_product_data)
            session.add(db_product)
            session.commit()

            # Refresh to get the actual database object
            session.refresh(db_product)

            # Test database field access - this would catch 'pricing' attribute errors
            assert hasattr(db_product, "product_id")
            assert hasattr(db_product, "name")
            assert hasattr(db_product, "description")
            assert hasattr(db_product, "formats")
            assert hasattr(db_product, "cpm")
            assert hasattr(db_product, "min_spend")
            assert hasattr(db_product, "is_fixed_price")
            assert hasattr(db_product, "delivery_type")

            # These fields should NOT exist - would catch if someone tries to access them
            assert not hasattr(db_product, "pricing")  # This would have caused the bug
            assert not hasattr(db_product, "format_ids")  # Schema property, not DB field

            # Verify field values are correct types from database
            assert isinstance(db_product.cpm, Decimal)
            assert isinstance(db_product.min_spend, Decimal)
            assert isinstance(db_product.is_fixed_price, bool)
            assert db_product.cpm == Decimal("5.50")
            assert db_product.min_spend == Decimal("1000.00")

    @pytest.mark.asyncio
    async def test_database_provider_real_conversion(self, test_tenant_id, sample_product_data):
        """Test DatabaseProductCatalog with real database conversion."""
        # Create product in database
        with get_db_session() as session:
            db_product = ProductModel(tenant_id=test_tenant_id, **sample_product_data)
            session.add(db_product)
            session.commit()

        # Use real DatabaseProductCatalog (no mocking)
        catalog = DatabaseProductCatalog(config={})

        # This should work without accessing non-existent fields
        products = await catalog.get_products(
            brief="test brief", tenant_id=test_tenant_id, principal_id="test_principal"
        )

        # Validate results
        assert len(products) == 1
        product = products[0]

        # Verify it's a proper Pydantic model
        assert isinstance(product, ProductSchema)

        # Verify field mapping worked correctly
        assert product.product_id == "test_prod_001"
        assert product.name == "Integration Test Product"
        assert product.cpm == 5.50  # Converted from Decimal to float
        assert product.min_spend == 1000.00  # Converted from Decimal to float
        assert product.is_fixed_price is False
        assert product.delivery_type == "non_guaranteed"
        assert product.formats == ["display_300x250", "display_728x90"]

        # Verify AdCP compliance - these internal fields should be excluded
        product_dict = product.model_dump()
        assert "targeting_template" not in product_dict
        assert "price_guidance" not in product_dict
        assert "implementation_config" not in product_dict
        assert "countries" not in product_dict

    def test_database_field_access_validation(self, test_tenant_id, sample_product_data):
        """Validate that we only access database fields that actually exist."""
        with get_db_session() as session:
            db_product = ProductModel(tenant_id=test_tenant_id, **sample_product_data)
            session.add(db_product)
            session.commit()
            session.refresh(db_product)

            # Test all fields that the conversion code accesses
            valid_fields = [
                "product_id",
                "name",
                "description",
                "formats",
                "delivery_type",
                "is_fixed_price",
                "cpm",
                "min_spend",
                "measurement",
                "creative_policy",
                "price_guidance",
                "is_custom",
                "countries",
                "implementation_config",
                "targeting_template",
                "expires_at",
            ]

            for field in valid_fields:
                assert hasattr(db_product, field), f"Database model missing expected field: {field}"
                # Access the field to ensure no AttributeError
                getattr(db_product, field)

            # Test that accessing non-existent fields raises AttributeError
            invalid_fields = ["pricing", "format_ids", "brief_relevance"]
            for field in invalid_fields:
                with pytest.raises(
                    AttributeError, match=f"'{ProductModel.__name__}' object has no attribute '{field}'"
                ):
                    getattr(db_product, field)

    @pytest.mark.asyncio
    async def test_multiple_products_database_conversion(self, test_tenant_id):
        """Test conversion with multiple products of different types."""
        products_data = [
            {
                "product_id": "test_display_001",
                "name": "Display Banner Product",
                "description": "Display advertising product",
                "formats": ["display_300x250"],
                "targeting_template": {},
                "delivery_type": "guaranteed",
                "is_fixed_price": True,
                "cpm": Decimal("10.00"),
                "min_spend": None,  # Test NULL handling
                "is_custom": False,
            },
            {
                "product_id": "test_video_001",
                "name": "Video Ad Product",
                "description": "Video advertising product",
                "formats": ["video_15s", "video_30s"],
                "targeting_template": {},
                "delivery_type": "non_guaranteed",
                "is_fixed_price": False,
                "cpm": None,  # Test NULL handling
                "min_spend": Decimal("5000.00"),
                "is_custom": True,
            },
        ]

        # Create products in database
        with get_db_session() as session:
            for product_data in products_data:
                db_product = ProductModel(tenant_id=test_tenant_id, **product_data)
                session.add(db_product)
            session.commit()

        # Test conversion
        catalog = DatabaseProductCatalog(config={})
        products = await catalog.get_products(brief="test brief", tenant_id=test_tenant_id)

        assert len(products) == 2

        # Verify both products converted correctly
        product_ids = [p.product_id for p in products]
        assert "test_display_001" in product_ids
        assert "test_video_001" in product_ids

        # Test NULL value handling
        for product in products:
            if product.product_id == "test_display_001":
                assert product.cpm == 10.00
                assert product.min_spend is None
            elif product.product_id == "test_video_001":
                assert product.cpm is None
                assert product.min_spend == 5000.00

    def test_database_schema_mismatch_detection(self, test_tenant_id):
        """Test that schema-database mismatches are detected."""
        # Create a product with minimal required fields
        with get_db_session() as session:
            db_product = ProductModel(
                tenant_id=test_tenant_id,
                product_id="test_minimal",
                name="Minimal Product",
                description="Minimal test product",
                formats=["display_300x250"],
                targeting_template={},
                delivery_type="non_guaranteed",
                is_fixed_price=False,
            )
            session.add(db_product)
            session.commit()
            session.refresh(db_product)

            # Verify that trying to access fields that don't exist fails
            # This simulates what would happen if code tried to access 'pricing'
            try:
                pricing_value = db_product.pricing  # This should fail
                pytest.fail("Expected AttributeError for non-existent 'pricing' field")
            except AttributeError as e:
                assert "pricing" in str(e)
                assert "object has no attribute" in str(e)

    @pytest.mark.asyncio
    async def test_database_conversion_with_json_fields(self, test_tenant_id):
        """Test handling of JSON/JSONB fields in database conversion."""
        # Test both string (SQLite) and dict (PostgreSQL) JSON handling
        with get_db_session() as session:
            db_product = ProductModel(
                tenant_id=test_tenant_id,
                product_id="test_json_fields",
                name="JSON Test Product",
                description="Product with JSON fields",
                formats='["display_300x250", "display_728x90"]',  # JSON string format
                targeting_template={"geo": ["US"], "device": ["mobile"]},  # Dict format
                delivery_type="non_guaranteed",
                is_fixed_price=False,
                measurement='{"viewability": true}',  # JSON string
                creative_policy={"max_file_size": "10MB"},  # Dict format
            )
            session.add(db_product)
            session.commit()

        # Test conversion handles both JSON string and dict formats
        catalog = DatabaseProductCatalog(config={})
        products = await catalog.get_products(brief="test brief", tenant_id=test_tenant_id)

        assert len(products) == 1
        product = products[0]

        # Verify JSON fields were parsed correctly
        assert product.formats == ["display_300x250", "display_728x90"]

        # Verify internal JSON fields are excluded from external schema
        product_dict = product.model_dump()
        assert "targeting_template" not in product_dict
        assert "measurement" not in product_dict
        assert "creative_policy" not in product_dict

    async def test_product_schema_excludes_internal_fields_regression(self, test_tenant_id, sample_product_data):
        """
        Regression test: Ensure internal-only fields don't leak to API responses.

        This test prevents the recurrence of the 'Product' object has no attribute 'pricing' bug
        where code tried to access non-existent fields on database models.

        Background: The original bug occurred when the Product schema had a 'pricing' field
        that didn't exist in the ProductModel database table, causing AttributeError in production.
        """
        # Create a real product in the database
        with get_db_session() as session:
            db_product = ProductModel(tenant_id=test_tenant_id, **sample_product_data)
            session.add(db_product)
            session.commit()
            session.refresh(db_product)

        # Get products through the catalog (simulates API path)
        catalog = DatabaseProductCatalog(config={})
        products = await catalog.get_products(brief="test", tenant_id=test_tenant_id)

        assert len(products) > 0, "Should return at least one product"
        product = products[0]

        # Convert to dict (what API response returns)
        schema_dict = product.model_dump()

        # These fields caused the original "pricing" bug - they should NEVER appear in API responses
        forbidden_internal_fields = [
            "pricing",  # Original bug: accessed product.pricing which doesn't exist
            "cost_basis",  # Would cause same AttributeError
            "margin",  # Should be computed, not stored/accessed
            "profit",  # Should be computed, not stored/accessed
        ]

        for field in forbidden_internal_fields:
            assert field not in schema_dict, (
                f"REGRESSION: Forbidden internal field '{field}' leaked to API response. "
                f"This is a regression of the 'pricing' field access bug. "
                f"Internal fields should not be in Product schema or should be marked as computed_field."
            )

        # Database-internal fields should also not leak
        database_internal_fields = [
            "tenant_id",  # Multi-tenancy field
            "targeting_template",  # Internal targeting config
            "implementation_config",  # Adapter-specific config
        ]

        for field in database_internal_fields:
            assert field not in schema_dict, (
                f"Database internal field '{field}' leaked to API response. "
                f"These fields are for internal use only and should be filtered from API responses."
            )


class TestDatabasePerformanceOptimization:
    """Performance-optimized database tests with faster cleanup and connection pooling."""

    @pytest.fixture
    def optimized_test_setup(self):
        """Performance-optimized test setup with transaction rollbacks."""
        tenant_id = "perf_test_tenant"

        # Use a single transaction for the entire test setup
        with get_db_session() as session:
            # Begin a savepoint for faster rollback
            savepoint = session.begin_nested()

            try:
                # Clean up any existing test data (tables may not exist)
                try:
                    session.query(ProductModel).filter_by(tenant_id=tenant_id).delete()
                except Exception:
                    # Table might not exist, rollback and continue
                    session.rollback()
                    savepoint = session.begin_nested()  # Start a new savepoint
                try:
                    session.query(Tenant).filter_by(tenant_id=tenant_id).delete()
                except Exception:
                    # Table might not exist, rollback and continue
                    session.rollback()
                    savepoint = session.begin_nested()  # Start a new savepoint

                # Create test tenant with proper timestamps
                tenant = create_tenant_with_timestamps(
                    tenant_id=tenant_id, name="Performance Test Tenant", subdomain="perf-test", billing_plan="test"
                )
                session.add(tenant)
                session.flush()  # Flush to get IDs without committing

                yield tenant_id

            finally:
                # Rollback to savepoint for fast cleanup
                try:
                    if savepoint.is_active:
                        savepoint.rollback()
                except Exception:
                    # Savepoint may have been closed by nested sessions, that's OK
                    pass

    @pytest.mark.asyncio
    async def test_large_dataset_conversion_performance(self, optimized_test_setup):
        """Test database conversion performance with large datasets."""
        tenant_id = optimized_test_setup

        # Create large dataset (100 products)
        products_data = []
        with get_db_session() as session:
            for i in range(100):
                product = ProductModel(
                    tenant_id=tenant_id,
                    product_id=f"perf_test_{i:03d}",
                    name=f"Performance Test Product {i}",
                    description=f"Product {i} for performance testing",
                    formats=["display_300x250", "display_728x90"],
                    targeting_template={"geo": ["US"], "device": ["desktop", "mobile"]},
                    delivery_type="non_guaranteed",
                    is_fixed_price=False,
                    cpm=Decimal("5.0") + (Decimal(str(i)) * Decimal("0.1")),
                    min_spend=Decimal("1000.00"),
                    is_custom=False,
                )
                session.add(product)
                products_data.append(product)

            session.commit()

        # Measure conversion performance
        start_time = time.time()

        catalog = DatabaseProductCatalog(config={})
        products = await catalog.get_products(brief="performance test", tenant_id=tenant_id)

        conversion_time = time.time() - start_time

        # Verify results and performance
        assert len(products) == 100
        assert conversion_time < 2.0, f"Conversion took {conversion_time:.2f}s, expected < 2.0s"

        # Verify all products converted correctly
        for i, product in enumerate(products):
            assert isinstance(product, ProductSchema)
            assert product.product_id == f"perf_test_{i:03d}"
            # Use consistent decimal arithmetic to avoid floating point precision issues
            expected_cpm = float(Decimal("5.0") + (Decimal(str(i)) * Decimal("0.1")))
            assert product.cpm == expected_cpm

        # Performance regression test
        print(f"✅ Converted {len(products)} products in {conversion_time:.3f}s")

    def test_concurrent_field_access(self, optimized_test_setup):
        """Test concurrent access to database fields to catch race conditions."""
        tenant_id = optimized_test_setup

        # Create test product
        with get_db_session() as session:
            product = ProductModel(
                tenant_id=tenant_id,
                product_id="concurrent_test_001",
                name="Concurrent Test Product",
                description="Product for concurrent field access testing",
                formats=["display_300x250"],
                targeting_template={},
                delivery_type="non_guaranteed",
                is_fixed_price=False,
                cpm=Decimal("10.00"),
                min_spend=Decimal("1000.00"),
            )
            session.add(product)
            session.commit()

        results = []
        errors = []

        def access_fields():
            """Function to access fields concurrently."""
            try:
                with get_db_session() as session:
                    db_product = (
                        session.query(ProductModel)
                        .filter_by(tenant_id=tenant_id, product_id="concurrent_test_001")
                        .first()
                    )

                    # Test concurrent field access
                    field_values = {
                        "product_id": db_product.product_id,
                        "name": db_product.name,
                        "cpm": db_product.cpm,
                        "min_spend": db_product.min_spend,
                        "delivery_type": db_product.delivery_type,
                        "is_fixed_price": db_product.is_fixed_price,
                    }

                    # Test that accessing non-existent fields fails consistently
                    try:
                        _ = db_product.pricing
                        errors.append("Should have failed accessing 'pricing' field")
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
            "cpm": Decimal("10.00"),
            "min_spend": Decimal("1000.00"),
            "delivery_type": "non_guaranteed",
            "is_fixed_price": False,
        }

        for result in results:
            for key, expected_value in expected_values.items():
                assert result[key] == expected_value, f"Inconsistent {key}: {result[key]} != {expected_value}"


class TestDatabaseSchemaEvolution:
    """Tests for database schema evolution scenarios."""

    @pytest.fixture
    def schema_evolution_setup(self):
        """Setup for schema evolution testing."""
        tenant_id = "schema_evolution_test"

        with get_db_session() as session:
            # Clean up
            session.query(ProductModel).filter_by(tenant_id=tenant_id).delete()
            session.query(Tenant).filter_by(tenant_id=tenant_id).delete()

            # Create tenant with proper timestamps
            tenant = create_tenant_with_timestamps(
                tenant_id=tenant_id, name="Schema Evolution Test", subdomain="schema-evolution", billing_plan="test"
            )
            session.add(tenant)
            session.commit()

        yield tenant_id

        # Cleanup
        with get_db_session() as session:
            session.query(ProductModel).filter_by(tenant_id=tenant_id).delete()
            session.query(Tenant).filter_by(tenant_id=tenant_id).delete()
            session.commit()

    def test_new_field_addition_backward_compatibility(self, schema_evolution_setup):
        """Test that adding new fields doesn't break existing field access."""
        tenant_id = schema_evolution_setup

        # Create product with minimal fields (simulating older schema)
        with get_db_session() as session:
            product = ProductModel(
                tenant_id=tenant_id,
                product_id="evolution_test_001",
                name="Schema Evolution Product",
                description="Product for testing schema evolution",
                formats=["display_300x250"],
                targeting_template={},
                delivery_type="non_guaranteed",
                is_fixed_price=False,
                # Note: No cpm, min_spend fields (simulating older schema)
            )
            session.add(product)
            session.commit()
            session.refresh(product)

            # Test that accessing new fields works with None values
            assert hasattr(product, "cpm")
            assert product.cpm is None
            assert hasattr(product, "min_spend")
            assert product.min_spend is None

            # Test that old fields still work
            assert product.product_id == "evolution_test_001"
            assert product.name == "Schema Evolution Product"
            assert product.delivery_type == "non_guaranteed"
            assert product.is_fixed_price is False

    def test_field_removal_safety(self, schema_evolution_setup):
        """Test safe handling of removed fields."""
        tenant_id = schema_evolution_setup

        with get_db_session() as session:
            product = ProductModel(
                tenant_id=tenant_id,
                product_id="field_removal_test",
                name="Field Removal Test Product",
                description="Testing field removal safety",
                formats=["display_300x250"],
                targeting_template={},
                delivery_type="non_guaranteed",
                is_fixed_price=False,
            )
            session.add(product)
            session.commit()
            session.refresh(product)

            # Test that accessing hypothetically removed fields fails safely
            removed_fields = ["legacy_pricing", "old_cost_field", "deprecated_margin"]

            for field in removed_fields:
                assert not hasattr(product, field), f"Field '{field}' should not exist"

                # Test safe access patterns
                value = getattr(product, field, None)
                assert value is None, f"Safe access to '{field}' should return None"

    @pytest.mark.asyncio
    async def test_schema_conversion_with_missing_fields(self, schema_evolution_setup):
        """Test schema conversion when database has missing fields."""
        tenant_id = schema_evolution_setup

        # Create product with some fields missing
        with get_db_session() as session:
            product = ProductModel(
                tenant_id=tenant_id,
                product_id="missing_fields_test",
                name="Missing Fields Test Product",
                description="Testing conversion with missing fields",
                formats=["display_300x250"],
                targeting_template={},
                delivery_type="non_guaranteed",
                is_fixed_price=False,
                # Missing: cmp, min_spend, measurement, creative_policy
            )
            session.add(product)
            session.commit()

        # Test that conversion handles missing fields gracefully
        catalog = DatabaseProductCatalog(config={})
        products = await catalog.get_products(brief="missing fields test", tenant_id=tenant_id)

        assert len(products) == 1
        product = products[0]

        # Verify conversion worked despite missing fields
        assert product.product_id == "missing_fields_test"
        assert product.name == "Missing Fields Test Product"
        assert product.cpm is None  # Missing field handled as None
        assert product.min_spend is None  # Missing field handled as None

        # Verify AdCP compliance is maintained
        product_dict = product.model_dump()
        assert "product_id" in product_dict
        assert "name" in product_dict
        assert "cpm" in product_dict  # Should be present even if None


class TestParallelTestExecution:
    """Tests for parallel test execution with isolated databases."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("test_id", [f"parallel_{i:02d}" for i in range(5)])
    async def test_parallel_database_isolation(self, test_id):
        """Test that parallel tests can run with isolated database state."""
        tenant_id = f"parallel_test_{test_id}"

        # Each test gets its own isolated tenant and data
        with get_db_session() as session:
            # Clean up any existing data for this test
            session.query(ProductModel).filter_by(tenant_id=tenant_id).delete()
            session.query(Tenant).filter_by(tenant_id=tenant_id).delete()

            # Create isolated tenant for this test with proper timestamps
            tenant = create_tenant_with_timestamps(
                tenant_id=tenant_id,
                name=f"Parallel Test Tenant {test_id}",
                subdomain=f"parallel-{test_id}",
                billing_plan="test",
            )
            session.add(tenant)

            # Create unique products for this test
            for i in range(3):
                product = ProductModel(
                    tenant_id=tenant_id,
                    product_id=f"{test_id}_product_{i}",
                    name=f"Parallel Product {test_id}_{i}",
                    description=f"Product {i} for parallel test {test_id}",
                    formats=[f"display_{300+i*50}x{250+i*25}"],
                    targeting_template={},
                    delivery_type="non_guaranteed",
                    is_fixed_price=False,
                    cpm=Decimal(f"{5 + i}.00"),
                    min_spend=Decimal(f"{1000 + i*100}.00"),
                )
                session.add(product)

            session.commit()

        try:
            # Test database operations in isolation
            catalog = DatabaseProductCatalog(config={})
            products = await catalog.get_products(brief=f"parallel test {test_id}", tenant_id=tenant_id)

            # Verify isolation - each test should only see its own data
            assert len(products) == 3, f"Test {test_id} should see exactly 3 products"

            for i, product in enumerate(products):
                assert product.product_id == f"{test_id}_product_{i}"
                assert product.name == f"Parallel Product {test_id}_{i}"
                assert product.cpm == float(5 + i)
                assert product.min_spend == float(1000 + i * 100)

                # Test field access safety in parallel
                assert hasattr(product, "cpm")
                assert not hasattr(product, "pricing")  # Would catch the original bug

            # Test concurrent schema validation
            for product in products:
                product_dict = product.model_dump()
                assert "product_id" in product_dict
                assert "pricing" not in product_dict
                assert "tenant_id" not in product_dict

        finally:
            # Cleanup isolated data
            with get_db_session() as session:
                session.query(ProductModel).filter_by(tenant_id=tenant_id).delete()
                session.query(Tenant).filter_by(tenant_id=tenant_id).delete()
                session.commit()

    @pytest.mark.integration
    @pytest.mark.slow
    def test_database_connection_pooling_efficiency(self):
        """Test that connection pooling works efficiently under load."""
        results = []
        start_time = time.time()

        def database_operation(operation_id):
            """Simulate database operation that would use connection pooling."""
            try:
                with get_db_session() as session:
                    # Simulate typical database operations
                    count = session.query(ProductModel).count()
                    tenant_count = session.query(Tenant).count()

                    # Record timing for this operation
                    operation_time = time.time() - start_time
                    results.append(
                        {
                            "operation_id": operation_id,
                            "time": operation_time,
                            "product_count": count,
                            "tenant_count": tenant_count,
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
