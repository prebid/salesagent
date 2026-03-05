"""Database Integration Tests for get_products - Real Database Tests (V2 Pricing Model)

These tests validate the actual database-to-schema transformation with real ORM models
using the pricing_options table, to catch field access bugs that mocks would miss.

MIGRATED: Uses factory-based setup via IntegrationEnv session binding.
"""

import threading
import time
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from src.core.database.database_session import get_db_session
from src.core.database.models import PricingOption, Tenant
from src.core.database.models import Product as ProductModel
from tests.factories import PricingOptionFactory, ProductFactory, TenantFactory
from tests.harness._base import IntegrationEnv

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


@pytest.mark.requires_db
class TestDatabaseProductsIntegration:
    """Integration tests using real database without excessive mocking."""

    def test_database_model_to_schema_conversion_without_mocking(self, integration_db):
        """Test actual ORM model to Pydantic schema conversion with real database."""
        with IntegrationEnv() as _env:
            tenant = TenantFactory(tenant_id="conv-test", subdomain="conv-test")
            product = ProductFactory(
                tenant=tenant,
                product_id="test_prod_001",
                name="Integration Test Product",
                description="A test product for database integration testing",
                format_ids=[{"agent_url": "https://test.com", "id": "300x250"}],
                targeting_template={"geo": ["country"], "device": ["desktop", "mobile"]},
                delivery_type="non_guaranteed",
                countries=["US", "CA"],
                implementation_config={"gam_placement_id": "12345"},
            )
            PricingOptionFactory(
                product=product,
                pricing_model="cpm",
                rate=Decimal("5.50"),
                is_fixed=False,
                min_spend_per_package=Decimal("1000.00"),
                price_guidance={"floor": 2.0, "p50": 5.0, "p75": 8.0, "p90": 10.0},
            )

        with get_db_session() as session:
            db_product = session.scalars(
                select(ProductModel).filter_by(tenant_id="conv-test", product_id="test_prod_001")
            ).first()

            assert db_product is not None
            assert hasattr(db_product, "product_id")
            assert hasattr(db_product, "name")
            assert hasattr(db_product, "description")
            assert hasattr(db_product, "format_ids")
            assert hasattr(db_product, "delivery_type")

            assert hasattr(db_product, "pricing_options")
            assert len(db_product.pricing_options) == 1

            pricing = db_product.pricing_options[0]
            assert pricing.pricing_model == "cpm"
            assert pricing.rate == Decimal("5.50")
            assert pricing.is_fixed is False
            assert pricing.currency == "USD"

            # Legacy fields should NOT exist
            assert not hasattr(db_product, "is_fixed_price")
            assert not hasattr(db_product, "cpm")
            assert not hasattr(db_product, "pricing")

    def test_database_field_access_validation(self, integration_db):
        """Validate that we only access database fields that actually exist."""
        with IntegrationEnv() as _env:
            tenant = TenantFactory(tenant_id="field-access-test", subdomain="field-access")
            product = ProductFactory(
                tenant=tenant,
                product_id="test_field_access",
                name="Field Access Test",
            )
            PricingOptionFactory(product=product, rate=Decimal("10.00"))

        with get_db_session() as session:
            db_product = session.scalars(
                select(ProductModel).filter_by(tenant_id="field-access-test", product_id="test_field_access")
            ).first()

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
                "pricing_options",
            ]
            for field in valid_product_fields:
                assert hasattr(db_product, field), f"Product model missing expected field: {field}"
                getattr(db_product, field)

            legacy_fields = ["is_fixed_price", "cpm", "min_spend", "pricing"]
            for field in legacy_fields:
                with pytest.raises(AttributeError, match=f"object has no attribute '{field}'"):
                    getattr(db_product, field)

            assert len(db_product.pricing_options) == 1
            pricing = db_product.pricing_options[0]
            for field in ["pricing_model", "rate", "currency", "is_fixed", "price_guidance", "min_spend_per_package"]:
                assert hasattr(pricing, field), f"PricingOption missing expected field: {field}"

    def test_multiple_products_database_conversion(self, integration_db):
        """Test conversion with multiple products of different types."""
        with IntegrationEnv() as _env:
            tenant = TenantFactory(tenant_id="multi-prod-test", subdomain="multi-prod")

            p1 = ProductFactory(
                tenant=tenant,
                product_id="test_display_001",
                name="Display Banner Product",
                delivery_type="guaranteed",
            )
            PricingOptionFactory(product=p1, pricing_model="cpm", rate=Decimal("10.00"), is_fixed=True)

            p2 = ProductFactory(
                tenant=tenant,
                product_id="test_video_001",
                name="Video Ad Product",
                delivery_type="non_guaranteed",
                is_custom=True,
            )
            PricingOptionFactory(
                product=p2,
                pricing_model="cpm",
                rate=Decimal("5.00"),
                is_fixed=False,
                price_guidance={"floor": 5.0, "p50": 7.5, "p75": 10.0, "p90": 12.5},
            )

        with get_db_session() as session:
            products = session.scalars(
                select(ProductModel).filter_by(tenant_id="multi-prod-test").order_by(ProductModel.product_id)
            ).all()

            assert len(products) == 2

            display = next(p for p in products if p.product_id == "test_display_001")
            assert display.name == "Display Banner Product"
            assert display.pricing_options[0].rate == Decimal("10.00")
            assert display.pricing_options[0].is_fixed is True

            video = next(p for p in products if p.product_id == "test_video_001")
            assert video.name == "Video Ad Product"
            assert video.pricing_options[0].rate == Decimal("5.00")
            assert video.pricing_options[0].is_fixed is False
            assert video.pricing_options[0].price_guidance is not None


class TestDatabasePerformanceOptimization:
    """Performance-optimized database tests."""

    def test_large_dataset_conversion_performance(self, integration_db):
        """Test database conversion performance with large datasets."""
        with IntegrationEnv() as _env:
            tenant = TenantFactory(tenant_id="perf-test", subdomain="perf-test")
            for i in range(100):
                product = ProductFactory(
                    tenant=tenant,
                    product_id=f"perf_test_{i:03d}",
                    name=f"Performance Test Product {i}",
                    delivery_type="non_guaranteed",
                )
                PricingOptionFactory(
                    product=product,
                    pricing_model="cpm",
                    rate=Decimal("5.0") + (Decimal(str(i)) * Decimal("0.1")),
                    is_fixed=False,
                )

        start_time = time.time()

        with get_db_session() as session:
            products = session.scalars(select(ProductModel).filter_by(tenant_id="perf-test")).all()
            for product in products:
                _ = product.pricing_options

        query_time = time.time() - start_time

        assert len(products) == 100
        assert query_time < 2.0, f"Query took {query_time:.2f}s, expected < 2.0s"

        for i, product in enumerate(products):
            assert len(product.pricing_options) == 1
            expected_rate = Decimal("5.0") + (Decimal(str(i)) * Decimal("0.1"))
            assert product.pricing_options[0].rate == expected_rate

    def test_concurrent_field_access(self, integration_db):
        """Test concurrent access to database fields to catch race conditions."""
        with IntegrationEnv() as _env:
            tenant = TenantFactory(tenant_id="concurrent-test", subdomain="concurrent-test")
            product = ProductFactory(
                tenant=tenant,
                product_id="concurrent_test_001",
                name="Concurrent Test Product",
                delivery_type="standard",
            )
            PricingOptionFactory(product=product, rate=Decimal("10.00"))

        results = []
        errors = []

        def access_fields():
            try:
                with get_db_session() as session:
                    db_product = session.scalars(
                        select(ProductModel).filter_by(
                            tenant_id="concurrent-test",
                            product_id="concurrent_test_001",
                        )
                    ).first()

                    field_values = {
                        "product_id": db_product.product_id,
                        "name": db_product.name,
                        "delivery_type": db_product.delivery_type,
                        "pricing_model": db_product.pricing_options[0].pricing_model,
                        "rate": db_product.pricing_options[0].rate,
                        "is_fixed": db_product.pricing_options[0].is_fixed,
                    }

                    try:
                        _ = db_product.is_fixed_price
                        errors.append("Should have failed accessing legacy 'is_fixed_price'")
                    except AttributeError:
                        pass

                    try:
                        _ = db_product.cpm
                        errors.append("Should have failed accessing legacy 'cpm'")
                    except AttributeError:
                        pass

                    results.append(field_values)
            except Exception as e:
                errors.append(str(e))

        threads = [threading.Thread(target=access_fields) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"Concurrent access errors: {errors}"
        assert len(results) == 10

        expected = {
            "product_id": "concurrent_test_001",
            "name": "Concurrent Test Product",
            "delivery_type": "standard",
            "pricing_model": "cpm",
            "rate": Decimal("10.00"),
            "is_fixed": True,
        }
        for result in results:
            for key, val in expected.items():
                assert result[key] == val, f"Inconsistent {key}: {result[key]} != {val}"

    @pytest.mark.slow
    def test_database_connection_pooling_efficiency(self, integration_db):
        """Test that connection pooling works efficiently under load."""
        results = []
        start_time = time.time()

        def database_operation(operation_id):
            try:
                with get_db_session() as session:
                    product_count = session.scalar(select(func.count()).select_from(ProductModel))
                    tenant_count = session.scalar(select(func.count()).select_from(Tenant))
                    pricing_count = session.scalar(select(func.count()).select_from(PricingOption))

                    results.append(
                        {
                            "operation_id": operation_id,
                            "time": time.time() - start_time,
                            "product_count": product_count,
                            "tenant_count": tenant_count,
                            "pricing_count": pricing_count,
                        }
                    )
            except Exception as e:
                results.append({"operation_id": operation_id, "error": str(e)})

        threads = [threading.Thread(target=database_operation, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        total_time = time.time() - start_time

        errors = [r for r in results if "error" in r]
        assert len(errors) == 0, f"Database operations failed: {errors}"
        assert len(results) == 20
        assert total_time < 5.0, f"Connection pooling should be efficient: {total_time:.2f}s"
