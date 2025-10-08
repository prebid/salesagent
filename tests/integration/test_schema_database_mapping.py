#!/usr/bin/env python3
"""
Schema-Database Field Mapping Validation Tests

These tests validate that all Pydantic schema fields have corresponding database fields
and catch invalid field access patterns that could cause AttributeError at runtime.

This directly addresses the issue #161 root cause: missing validation that schema
fields map to valid database fields, allowing 'Product' object has no attribute 'pricing'
errors to reach production.
"""


import pytest
from sqlalchemy import delete

from src.core.database.database_session import get_db_session
from src.core.database.models import Creative, MediaBuy, Principal, Tenant
from src.core.database.models import Product as ProductModel
from src.core.schemas import Principal as PrincipalSchema
from src.core.schemas import Product
from tests.utils.database_helpers import (
    create_tenant_with_timestamps,
)


class TestSchemaFieldMapping:
    """Test that schema fields map correctly to database fields."""

    def test_product_schema_database_field_alignment(self):
        """Validate that Product schema fields align with database model fields."""
        # Get all fields from Product schema
        schema_fields = set(Product.model_fields.keys())

        # Get all columns from ProductModel database table
        db_columns = {column.name for column in ProductModel.__table__.columns}

        # Fields that exist in schema but should NOT be in database (computed properties)
        computed_fields = {
            "brief_relevance",  # Populated dynamically when brief is provided
            "expires_at",  # Optional field that may not be in all database versions
            # PR#79 fields - calculated dynamically from product_performance_metrics table
            "currency",  # ISO 4217 currency code, defaults to "USD"
            "estimated_exposures",  # Calculated from historical performance data
            "floor_cpm",  # Minimum CPM calculated from market data
            "recommended_cpm",  # Suggested CPM based on goals and historical data
        }

        # Fields that exist in database but should NOT be in external schema (internal only)
        internal_db_fields = {
            "tenant_id",  # Internal field for multi-tenancy
            "targeting_template",  # Internal targeting configuration
            "price_guidance",  # Legacy field not in AdCP spec
            "countries",  # Not part of AdCP Product schema
            "implementation_config",  # Ad server-specific configuration
        }

        # Check that required schema fields have database equivalents
        required_schema_fields = schema_fields - computed_fields
        missing_db_fields = required_schema_fields - db_columns - internal_db_fields

        assert not missing_db_fields, (
            f"Schema fields missing from database: {missing_db_fields}. "
            f"These fields are in the Product schema but have no corresponding database column. "
            f"This could cause 'object has no attribute' errors when accessing them."
        )

        # Verify critical fields exist in both
        critical_fields = {"product_id", "name", "description", "formats", "delivery_type", "is_fixed_price"}
        for field in critical_fields:
            assert field in schema_fields, f"Critical field '{field}' missing from Product schema"
            assert field in db_columns, f"Critical field '{field}' missing from ProductModel database"

    def test_database_field_access_validation(self, integration_db):
        """Test that all database fields can be accessed without AttributeError."""
        # Create a test tenant and product
        tenant_id = "test_field_access"
        with get_db_session() as session:
            # Clean up any existing test data
            session.execute(delete(ProductModel).where(ProductModel.tenant_id == tenant_id))
            session.execute(delete(Tenant).where(Tenant.tenant_id == tenant_id))

            # Create test tenant
            tenant = create_tenant_with_timestamps(
                tenant_id=tenant_id, name="Field Access Test", subdomain="field-test"
            )
            session.add(tenant)

            # Create minimal product
            product = ProductModel(
                tenant_id=tenant_id,
                product_id="field_test_001",
                name="Field Test Product",
                description="Test product for field access validation",
                formats=["display_300x250"],
                targeting_template={},
                delivery_type="non_guaranteed",
                is_fixed_price=False,
            )
            session.add(product)
            session.commit()
            session.refresh(product)

            # Test that all database columns can be accessed
            for column in ProductModel.__table__.columns:
                field_name = column.name
                try:
                    value = getattr(product, field_name)
                    # Access successful - this is good
                except AttributeError as e:
                    pytest.fail(f"Cannot access database field '{field_name}': {e}")

            # Test that non-existent fields raise AttributeError
            non_existent_fields = ["pricing", "cost", "revenue", "profit_margin"]
            for field_name in non_existent_fields:
                with pytest.raises(AttributeError, match=f".*{field_name}.*"):
                    getattr(product, field_name)

            # Cleanup
            session.delete(product)
            session.delete(tenant)
            session.commit()

    def test_principal_schema_database_alignment(self):
        """Test Principal schema aligns with database model."""
        schema_fields = set(PrincipalSchema.model_fields.keys())
        db_columns = {column.name for column in Principal.__table__.columns}

        # Fields that should be in schema but computed/derived
        computed_fields = {"adapter_mappings"}  # Derived from platform_mappings

        # Internal database fields not in external schema
        internal_fields = {"tenant_id", "access_token"}

        # Check alignment
        required_schema_fields = schema_fields - computed_fields
        missing_db_fields = required_schema_fields - db_columns - internal_fields

        assert not missing_db_fields, f"Principal schema fields missing from database: {missing_db_fields}"

    def test_schema_to_database_conversion_safety(self, integration_db):
        """Test that schema-to-database conversion only uses existing fields."""
        # This simulates the conversion logic in DatabaseProductCatalog
        tenant_id = "test_conversion_safety"

        with get_db_session() as session:
            # Create test data
            session.execute(delete(ProductModel).where(ProductModel.tenant_id == tenant_id))
            session.execute(delete(Tenant).where(Tenant.tenant_id == tenant_id))

            tenant = create_tenant_with_timestamps(
                tenant_id=tenant_id, name="Conversion Safety Test", subdomain="conversion-test"
            )
            session.add(tenant)

            product = ProductModel(
                tenant_id=tenant_id,
                product_id="conversion_test_001",
                name="Conversion Test Product",
                description="Product for testing safe conversion",
                formats=["display_300x250"],
                targeting_template={},
                delivery_type="non_guaranteed",
                is_fixed_price=False,
                cpm=5.50,
                min_spend=1000.00,
            )
            session.add(product)
            session.commit()
            session.refresh(product)

            # Test safe field access pattern (what the code should do)
            safe_fields = [
                "product_id",
                "name",
                "description",
                "formats",
                "delivery_type",
                "is_fixed_price",
                "cpm",
                "min_spend",
            ]

            conversion_data = {}
            for field in safe_fields:
                if hasattr(product, field):
                    conversion_data[field] = getattr(product, field)
                else:
                    pytest.fail(f"Safe field '{field}' not found in database model")

            # Verify we got the expected data
            assert conversion_data["product_id"] == "conversion_test_001"
            assert conversion_data["name"] == "Conversion Test Product"
            assert conversion_data["cpm"] == 5.50

            # Test that unsafe field access (what caused the bug) fails predictably
            unsafe_fields = ["pricing", "cost_basis", "margin"]
            for field in unsafe_fields:
                assert not hasattr(product, field), f"Database model should not have unsafe field '{field}'"

            # Cleanup
            session.delete(product)
            session.delete(tenant)
            session.commit()

    def test_all_database_models_have_required_fields(self):
        """Test that all database models have their required fields."""
        models_to_test = [
            (ProductModel, ["product_id", "name", "tenant_id"]),
            (Tenant, ["tenant_id", "name"]),
            (Principal, ["tenant_id", "principal_id", "name"]),
            (MediaBuy, ["tenant_id", "media_buy_id"]),
            (Creative, ["tenant_id", "creative_id"]),
        ]

        for model_class, required_fields in models_to_test:
            db_columns = {column.name for column in model_class.__table__.columns}

            for field in required_fields:
                assert (
                    field in db_columns
                ), f"Required field '{field}' missing from {model_class.__name__} database model"

    def test_pydantic_model_field_access_patterns(self):
        """Test patterns for safely accessing Pydantic model fields."""
        # Create a Product schema instance
        product_data = {
            "product_id": "test_pydantic_001",
            "name": "Pydantic Test Product",
            "description": "Testing Pydantic field access",
            "formats": ["display_300x250"],
            "delivery_type": "non_guaranteed",
            "is_fixed_price": False,
        }

        product = Product(**product_data)

        # Test safe field access methods
        # Method 1: Direct attribute access (safe for known fields)
        assert product.product_id == "test_pydantic_001"

        # Method 2: getattr with default (safe for optional fields)
        cpm_value = getattr(product, "cpm", None)
        assert cpm_value is None  # Not set in product_data

        # Method 3: model_dump() to get all fields as dict
        product_dict = product.model_dump()
        assert "product_id" in product_dict
        assert product_dict["product_id"] == "test_pydantic_001"

        # Method 4: Check if field exists before access
        if hasattr(product, "brief_relevance"):
            brief_relevance = product.brief_relevance
        else:
            brief_relevance = None
        assert brief_relevance is None  # Not set in product_data

        # Test that accessing non-existent fields fails
        with pytest.raises(AttributeError):
            _ = product.non_existent_field

    def test_database_json_field_handling(self, integration_db):
        """Test that JSON fields in database are handled correctly in schema conversion."""
        tenant_id = "test_json_handling"

        with get_db_session() as session:
            # Cleanup
            session.execute(delete(ProductModel).where(ProductModel.tenant_id == tenant_id))
            session.execute(delete(Tenant).where(Tenant.tenant_id == tenant_id))

            tenant = create_tenant_with_timestamps(
                tenant_id=tenant_id, name="JSON Handling Test", subdomain="json-test"
            )
            session.add(tenant)

            # Test with various JSON field formats
            product = ProductModel(
                tenant_id=tenant_id,
                product_id="json_test_001",
                name="JSON Test Product",
                description="Testing JSON field handling",
                formats=["display_300x250", "display_728x90"],  # List
                targeting_template={"geo": ["US"], "device": ["mobile"]},  # Dict
                delivery_type="non_guaranteed",
                is_fixed_price=False,
                measurement={"viewability": True, "brand_safety": True},  # Dict
                countries=["US", "CA", "UK"],  # List
            )
            session.add(product)
            session.commit()
            session.refresh(product)

            # Test that JSON fields are accessible and have correct types
            assert hasattr(product, "formats")
            assert hasattr(product, "targeting_template")
            assert hasattr(product, "measurement")
            assert hasattr(product, "countries")

            # Access the fields to ensure no AttributeError
            formats = product.formats
            targeting = product.targeting_template
            measurement = product.measurement
            countries = product.countries

            # Verify types are as expected (may be dicts/lists from JSONB or strings from JSON)
            assert formats is not None
            assert targeting is not None

            # Cleanup
            session.delete(product)
            session.delete(tenant)
            session.commit()

    def test_schema_validation_with_database_data(self, integration_db):
        """Test that data from database can be validated against Pydantic schemas."""
        tenant_id = "test_schema_validation"

        with get_db_session() as session:
            # Cleanup
            session.execute(delete(ProductModel).where(ProductModel.tenant_id == tenant_id))
            session.execute(delete(Tenant).where(Tenant.tenant_id == tenant_id))

            tenant = create_tenant_with_timestamps(
                tenant_id=tenant_id, name="Schema Validation Test", subdomain="schema-validation"
            )
            session.add(tenant)

            product = ProductModel(
                tenant_id=tenant_id,
                product_id="validation_test_001",
                name="Schema Validation Product",
                description="Testing schema validation with database data",
                formats=["display_300x250"],
                targeting_template={},
                delivery_type="non_guaranteed",
                is_fixed_price=False,
                cpm=7.25,
                min_spend=2000.00,
                is_custom=False,
            )
            session.add(product)
            session.commit()
            session.refresh(product)

            # Extract data in the same way the real conversion code does
            conversion_data = {
                "product_id": product.product_id,
                "name": product.name,
                "description": product.description,
                "formats": product.formats,
                "delivery_type": product.delivery_type,
                "is_fixed_price": product.is_fixed_price,
                "cpm": float(product.cpm) if product.cpm else None,
                "min_spend": float(product.min_spend) if product.min_spend else None,
                "is_custom": product.is_custom if product.is_custom is not None else False,
            }

            # This should succeed without validation errors
            try:
                validated_product = Product(**conversion_data)
                assert validated_product.product_id == "validation_test_001"
                assert validated_product.cpm == 7.25
                assert validated_product.min_spend == 2000.00
            except Exception as e:
                pytest.fail(f"Schema validation failed with database data: {e}")

            # Cleanup
            session.delete(product)
            session.delete(tenant)
            session.commit()


class TestFieldAccessPatterns:
    """Test different patterns for accessing model fields safely."""

    def test_safe_field_access_patterns(self):
        """Test recommended patterns for safe field access."""
        product_data = {
            "product_id": "pattern_test_001",
            "name": "Pattern Test Product",
            "description": "Testing safe access patterns",
            "formats": ["display_300x250"],
            "delivery_type": "non_guaranteed",
            "is_fixed_price": False,
        }

        product = Product(**product_data)

        # Pattern 1: Direct access for required fields (safe)
        assert product.product_id == "pattern_test_001"
        assert product.name == "Pattern Test Product"

        # Pattern 2: Conditional access for optional fields
        cpm = getattr(product, "cpm", None)
        assert cpm is None  # Optional field, not provided

        # Pattern 3: hasattr check before access
        if hasattr(product, "brief_relevance"):
            brief_relevance = product.brief_relevance
        else:
            brief_relevance = None
        assert brief_relevance is None

        # Pattern 4: Using model_dump() for safe dict access
        product_dict = product.model_dump()
        assert product_dict.get("product_id") == "pattern_test_001"
        assert product_dict.get("non_existent_field") is None

        # Pattern 5: Using model_fields to check field existence
        assert "product_id" in Product.model_fields
        assert "non_existent_field" not in Product.model_fields

    def test_unsafe_field_access_patterns(self):
        """Test patterns that would cause AttributeError (what to avoid)."""
        product_data = {
            "product_id": "unsafe_test_001",
            "name": "Unsafe Test Product",
            "description": "Testing unsafe access patterns",
            "formats": ["display_300x250"],
            "delivery_type": "non_guaranteed",
            "is_fixed_price": False,
        }

        product = Product(**product_data)

        # Unsafe Pattern 1: Direct access to non-existent field
        with pytest.raises(AttributeError):
            _ = product.pricing  # This would have caused the original bug

        # Unsafe Pattern 2: Direct access to optional field that wasn't set
        # This is OK for Pydantic models (returns None), but would fail for ORM objects
        min_spend = product.min_spend  # Optional field, should be None
        assert min_spend is None

        # Demonstrate what would happen with ORM object (simulated)
        class MockORM:
            def __init__(self):
                self.product_id = "mock_001"
                self.name = "Mock Product"

        mock_orm = MockORM()

        # This is safe - field exists
        assert mock_orm.product_id == "mock_001"

        # This would cause AttributeError (simulates the original bug)
        with pytest.raises(AttributeError):
            _ = mock_orm.pricing  # Field doesn't exist in ORM object

    def test_database_orm_field_validation(self):
        """Test validation against actual ORM model fields."""
        # Get all column names from the actual ProductModel
        actual_db_fields = {column.name for column in ProductModel.__table__.columns}

        # Fields that should exist in database
        expected_fields = {
            "tenant_id",
            "product_id",
            "name",
            "description",
            "formats",
            "targeting_template",
            "delivery_type",
            "is_fixed_price",
            "cpm",
            "min_spend",
            "measurement",
            "creative_policy",
            "price_guidance",
            "is_custom",
            "expires_at",
            "countries",
            "implementation_config",
        }

        # Verify all expected fields exist
        missing_fields = expected_fields - actual_db_fields
        assert not missing_fields, f"Expected database fields missing: {missing_fields}"

        # Fields that should NOT exist (would cause the original bug)
        forbidden_fields = {"pricing", "cost_basis", "margin", "format_ids"}

        # Verify forbidden fields don't exist
        existing_forbidden = forbidden_fields & actual_db_fields
        assert not existing_forbidden, f"Forbidden fields found in database: {existing_forbidden}"
