#!/usr/bin/env python3
"""
Schema Contract Validation for AdCP Protocol Compliance

This test suite ensures that all schema models produce AdCP spec-compliant output
and prevents field mapping issues that could cause production validation errors.

Key Validations:
1. AdCP spec compliance - correct field names and types
2. Internal vs external field separation
3. Required fields presence and validation
4. Roundtrip conversion safety
5. Schema evolution compatibility

This test suite would have caught the "formats field required" error that reached
production by validating the complete Object → dict → Object conversion cycle.
"""

from datetime import datetime
from decimal import Decimal
from typing import Any

import pytest

from src.core.schemas import (
    Budget,
    Creative,
    GetProductsResponse,
    Product,
    Signal,
    SignalDeployment,
    SignalPricing,
    Targeting,
)


class AdCPSchemaContractValidator:
    """Validator for AdCP protocol schema compliance."""

    def validate_schema_contract(
        self,
        schema_class: type,
        test_data: dict[str, Any],
        adcp_spec_fields: set[str],
        internal_only_fields: set[str] = None,
    ) -> None:
        """
        Validate that a schema meets AdCP contract requirements.

        Args:
            schema_class: The Pydantic model class to test
            test_data: Valid test data for creating the model
            adcp_spec_fields: Fields that MUST be present in AdCP output
            internal_only_fields: Fields that MUST NOT be present in AdCP output
        """
        internal_only_fields = internal_only_fields or set()

        # Step 1: Create model instance
        model_instance = schema_class(**test_data)

        # Step 2: Test external (AdCP) output
        adcp_output = model_instance.model_dump()

        # Step 3: Validate required AdCP fields are present
        for field in adcp_spec_fields:
            assert field in adcp_output, f"Required AdCP field '{field}' missing from {schema_class.__name__} output"
            assert (
                adcp_output[field] is not None
            ), f"Required AdCP field '{field}' is null in {schema_class.__name__} output"

        # Step 4: Validate internal fields are excluded from AdCP output
        for field in internal_only_fields:
            assert (
                field not in adcp_output
            ), f"Internal field '{field}' should not appear in {schema_class.__name__} AdCP output"

        # Step 5: Test internal output (if available)
        if hasattr(model_instance, "model_dump_internal"):
            internal_output = model_instance.model_dump_internal()

            # Internal output should include all fields
            for field in test_data.keys():
                assert (
                    field in internal_output
                ), f"Field '{field}' missing from internal output of {schema_class.__name__}"

        # Step 6: Test roundtrip conversion safety
        if hasattr(model_instance, "model_dump_internal"):
            internal_dict = model_instance.model_dump_internal()
        else:
            internal_dict = model_instance.model_dump()

        # Reconstruct model from internal dict
        reconstructed_model = schema_class(**internal_dict)

        # Verify reconstruction preserved essential data
        reconstructed_adcp = reconstructed_model.model_dump()

        # Essential fields should match after roundtrip
        for field in adcp_spec_fields:
            original_value = adcp_output.get(field)
            reconstructed_value = reconstructed_adcp.get(field)
            assert (
                reconstructed_value == original_value
            ), f"Field '{field}' changed during roundtrip: {original_value} → {reconstructed_value}"

    def validate_field_mapping_consistency(
        self, schema_class: type, test_data: dict[str, Any], internal_external_mappings: dict[str, str]
    ) -> None:
        """
        Validate that internal and external field mappings are consistent.

        Args:
            schema_class: The Pydantic model class to test
            test_data: Valid test data for creating the model
            internal_external_mappings: Dict mapping internal field names to external field names
        """
        model_instance = schema_class(**test_data)

        # Test that internal fields map to external fields correctly
        for internal_field, external_field in internal_external_mappings.items():
            if internal_field in test_data:
                # Get value via internal access
                internal_value = getattr(model_instance, internal_field, None)

                # Get value via external property/mapping
                if hasattr(model_instance, external_field):
                    external_value = getattr(model_instance, external_field)
                    assert (
                        external_value == internal_value
                    ), f"Field mapping inconsistency: {internal_field} ({internal_value}) != {external_field} ({external_value})"

                # Verify external field appears in AdCP output
                adcp_output = model_instance.model_dump()
                assert external_field in adcp_output, f"External field '{external_field}' missing from AdCP output"
                assert (
                    internal_field not in adcp_output
                ), f"Internal field '{internal_field}' should not appear in AdCP output"


class TestProductSchemaContract:
    """Product schema contract validation tests."""

    @pytest.fixture
    def validator(self):
        return AdCPSchemaContractValidator()

    def test_product_adcp_contract_compliance(self, validator):
        """Test Product schema AdCP spec compliance."""
        test_data = {
            "product_id": "contract_test_product",
            "name": "Contract Test Product",
            "description": "Product for testing AdCP contract compliance",
            "formats": ["display_300x250", "video_15s"],  # Internal field name
            "delivery_type": "guaranteed",
            "is_fixed_price": True,
            "cpm": 15.0,
            "min_spend": 2000.0,
            "measurement": {
                "type": "brand_lift",
                "attribution": "deterministic_purchase",
                "reporting": "weekly_dashboard",
                "viewability": True,
                "brand_safety": True,
            },
            "creative_policy": {
                "co_branding": "optional",
                "landing_page": "any",
                "templates_available": True,
                "max_file_size": "10MB",
            },
            "is_custom": False,
            "property_tags": ["all_inventory"],  # Required per AdCP spec
            "brief_relevance": "Highly relevant for display advertising",
            # Internal fields
            "expires_at": datetime(2025, 12, 31),
            "implementation_config": {"gam_placement_id": "12345"},
        }

        # AdCP spec required fields
        adcp_spec_fields = {
            "product_id",
            "name",
            "description",
            "format_ids",
            "delivery_type",
            "is_fixed_price",
            "is_custom",
            "cpm",
            "min_spend",
        }

        # Internal-only fields that should not appear in AdCP output
        internal_only_fields = {"expires_at", "implementation_config", "targeting_template"}

        validator.validate_schema_contract(Product, test_data, adcp_spec_fields, internal_only_fields)

    def test_product_field_mapping_consistency(self, validator):
        """Test Product internal/external field mapping consistency."""
        test_data = {
            "product_id": "mapping_test_product",
            "name": "Mapping Test Product",
            "description": "Testing field mapping consistency",
            "formats": ["display_300x250", "display_728x90"],  # Internal field
            "delivery_type": "non_guaranteed",
            "is_fixed_price": False,
            "is_custom": True,
            "property_tags": ["all_inventory"],  # Required per AdCP spec
        }

        # Internal to external field mappings
        field_mappings = {"formats": "format_ids"}  # Internal: formats, External: format_ids

        validator.validate_field_mapping_consistency(Product, test_data, field_mappings)

    def test_product_roundtrip_conversion_safety(self, validator):
        """Test Product roundtrip conversion safety with all field types."""
        # Test with complex data that includes all possible field scenarios
        complex_product_data = {
            "product_id": "roundtrip_safety_test",
            "name": "Roundtrip Safety Test Product",
            "description": "Testing roundtrip safety with complex data",
            "formats": ["display_300x250", "video_15s", "audio_30s"],
            "delivery_type": "guaranteed",
            "is_fixed_price": True,
            "cpm": 25.75,
            "min_spend": 5000.0,
            "measurement": {
                "type": "incremental_sales_lift",
                "attribution": "probabilistic",
                "reporting": "real_time_api",
                "viewability": True,
                "brand_safety": True,
                "completion_rate": True,
                "custom_metrics": ["engagement_rate", "attention_score"],
            },
            "creative_policy": {
                "co_branding": "required",
                "landing_page": "must_include_retailer",
                "templates_available": True,
                "max_file_size": "50MB",
                "formats": ["jpg", "png", "gif", "mp4"],
                "aspect_ratios": ["16:9", "1:1", "9:16"],
                "duration_constraints": {"min": 5, "max": 30},
            },
            "is_custom": True,
            "property_tags": ["all_inventory"],  # Required per AdCP spec
            "brief_relevance": "Perfect match for multi-format campaign requirements",
        }

        # Required fields that must survive roundtrip
        required_fields = {"product_id", "name", "description", "format_ids", "delivery_type", "is_fixed_price"}

        validator.validate_schema_contract(Product, complex_product_data, required_fields)

    def test_product_minimal_data_contract(self, validator):
        """Test Product contract with minimal required data only."""
        minimal_data = {
            "product_id": "minimal_contract_test",
            "name": "Minimal Contract Test",
            "description": "Testing with minimal required fields only",
            "formats": ["display_300x250"],
            "delivery_type": "non_guaranteed",
            "is_fixed_price": False,
            "is_custom": False,
            "property_tags": ["all_inventory"],  # Required per AdCP spec
        }

        required_fields = {
            "product_id",
            "name",
            "description",
            "format_ids",
            "delivery_type",
            "is_fixed_price",
            "is_custom",
        }

        validator.validate_schema_contract(Product, minimal_data, required_fields)


class TestCreativeSchemaContract:
    """Creative schema contract validation tests."""

    @pytest.fixture
    def validator(self):
        return AdCPSchemaContractValidator()

    def test_creative_adcp_contract_compliance(self, validator):
        """Test Creative schema AdCP spec compliance."""
        from datetime import datetime

        test_data = {
            "creative_id": "creative_contract_test",
            "name": "Creative Contract Test",
            "format_id": "display_300x250",
            "status": "approved",
            "content_uri": "https://example.com/creative.jpg",
            "principal_id": "test_principal",
            "width": 300,
            "height": 250,
            "created_at": datetime.now(),
            "updated_at": datetime.now(),
        }

        # AdCP spec required fields for creatives
        adcp_spec_fields = {"creative_id", "name", "format", "status", "url"}

        validator.validate_schema_contract(Creative, test_data, adcp_spec_fields)

    def test_video_creative_contract(self, validator):
        """Test video creative specific contract requirements."""
        from datetime import datetime

        test_data = {
            "creative_id": "video_contract_test",
            "name": "Video Creative Contract Test",
            "format_id": "video_640x480",
            "status": "pending",
            "content_uri": "https://example.com/video.mp4",
            "principal_id": "test_principal",
            "width": 1920,
            "height": 1080,
            "duration": 30.0,
            "created_at": datetime.now(),
            "updated_at": datetime.now(),
        }

        # Video creatives have additional required fields
        adcp_spec_fields = {"creative_id", "name", "format", "status", "url", "duration"}

        validator.validate_schema_contract(Creative, test_data, adcp_spec_fields)


class TestTargetingSchemaContract:
    """Targeting schema contract validation tests."""

    @pytest.fixture
    def validator(self):
        return AdCPSchemaContractValidator()

    def test_targeting_adcp_contract_compliance(self, validator):
        """Test Targeting schema AdCP spec compliance."""
        test_data = {
            "geo_country_any_of": ["US", "CA", "GB"],
            "geo_region_any_of": ["NY", "CA", "TX"],
            "geo_city_any_of": ["New York", "Los Angeles", "London"],
            "device_type_any_of": ["desktop", "mobile", "tablet"],
            "os_any_of": ["iOS", "Android", "Windows"],
            "browser_any_of": ["Chrome", "Safari", "Firefox"],
            "signals": ["sports_signal_id", "news_signal_id", "technology_signal_id"],
        }

        # Targeting schemas have flexible field requirements
        # All provided fields should be preserved in AdCP output
        adcp_spec_fields = set(test_data.keys())

        validator.validate_schema_contract(Targeting, test_data, adcp_spec_fields)

    def test_minimal_targeting_contract(self, validator):
        """Test minimal targeting configuration contract."""
        test_data = {
            "geo_country_any_of": ["US"],
        }

        adcp_spec_fields = {"geo_country_any_of"}

        validator.validate_schema_contract(Targeting, test_data, adcp_spec_fields)


class TestSignalSchemaContract:
    """Signal schema contract validation tests."""

    @pytest.fixture
    def validator(self):
        return AdCPSchemaContractValidator()

    def test_signal_adcp_contract_compliance(self, validator):
        """Test Signal schema AdCP spec compliance."""
        test_data = {
            "signal_agent_segment_id": "signal_contract_test",
            "name": "Signal Contract Test",
            "description": "Testing signal contract compliance",
            "signal_type": "marketplace",
            "data_provider": "Test Data Provider",
            "coverage_percentage": 95.0,
            "deployments": [SignalDeployment(platform="test_platform", is_live=True, scope="platform-wide")],
            "pricing": SignalPricing(cpm=3.50, currency="USD"),
        }

        # AdCP spec required fields for signals
        adcp_spec_fields = {
            "signal_agent_segment_id",
            "name",
            "description",
            "signal_type",
            "data_provider",
            "coverage_percentage",
            "deployments",
            "pricing",
        }

        validator.validate_schema_contract(Signal, test_data, adcp_spec_fields)


class TestBudgetSchemaContract:
    """Budget schema contract validation tests."""

    @pytest.fixture
    def validator(self):
        return AdCPSchemaContractValidator()

    def test_budget_adcp_contract_compliance(self, validator):
        """Test Budget schema AdCP spec compliance."""
        test_data = {
            "total": 50000.0,
            "currency": "USD",
            "daily_cap": 2000.0,
            "pacing": "even",
            "auto_pause_on_budget_exhaustion": True,
        }

        # AdCP spec required fields for budgets
        adcp_spec_fields = {"total", "currency", "pacing"}

        validator.validate_schema_contract(Budget, test_data, adcp_spec_fields)

    def test_minimal_budget_contract(self, validator):
        """Test minimal budget configuration contract."""
        test_data = {
            "total": 10000.0,
            "currency": "USD",
            "pacing": "asap",
        }

        adcp_spec_fields = {"total", "currency", "pacing"}

        validator.validate_schema_contract(Budget, test_data, adcp_spec_fields)


class TestGetProductsResponseContract:
    """GetProductsResponse contract validation tests."""

    @pytest.fixture
    def validator(self):
        return AdCPSchemaContractValidator()

    def test_get_products_response_contract(self, validator):
        """Test GetProductsResponse AdCP contract compliance."""
        # Create sample products
        products = [
            Product(
                product_id="response_test_1",
                name="Response Test Product 1",
                description="First product for response testing",
                formats=["display_300x250"],
                delivery_type="guaranteed",
                is_fixed_price=True,
                cpm=10.0,
                is_custom=False,
                property_tags=["all_inventory"],  # Required per AdCP spec
            ),
            Product(
                product_id="response_test_2",
                name="Response Test Product 2",
                description="Second product for response testing",
                formats=["video_15s"],
                delivery_type="non_guaranteed",
                is_fixed_price=False,
                is_custom=True,
                property_tags=["all_inventory"],  # Required per AdCP spec
            ),
        ]

        test_data = {"products": products}

        # AdCP spec required fields for get_products response (message is NOT in spec - provided via __str__())
        adcp_spec_fields = {"products"}

        validator.validate_schema_contract(GetProductsResponse, test_data, adcp_spec_fields)

        # Additional validation: ensure all products in response are AdCP compliant
        response = GetProductsResponse(**test_data)
        response_dict = response.model_dump()

        assert "products" in response_dict
        assert isinstance(response_dict["products"], list)
        assert len(response_dict["products"]) == 2

        # Each product should be AdCP compliant
        for product_dict in response_dict["products"]:
            assert "format_ids" in product_dict  # AdCP field name
            assert "formats" not in product_dict  # Internal field name excluded
            assert "product_id" in product_dict
            assert "name" in product_dict
            assert "description" in product_dict


class TestSchemaEvolutionSafety:
    """Test schema evolution safety for backward compatibility."""

    def test_new_field_addition_safety(self):
        """Test that adding new fields doesn't break existing contracts."""
        # Simulate an existing Product without new fields
        existing_product_data = {
            "product_id": "evolution_test",
            "name": "Evolution Test Product",
            "description": "Testing schema evolution safety",
            "formats": ["display_300x250"],
            "delivery_type": "guaranteed",
            "is_fixed_price": True,
            "is_custom": False,
            "property_tags": ["all_inventory"],  # Required per AdCP spec
        }

        # Should still work with existing data
        product = Product(**existing_product_data)
        adcp_output = product.model_dump()

        # Essential fields should still be present
        essential_fields = ["product_id", "name", "description", "format_ids", "delivery_type"]
        for field in essential_fields:
            assert field in adcp_output

    def test_field_removal_safety(self):
        """Test that removing optional fields doesn't break contracts."""
        # Create product with only required fields
        minimal_data = {
            "product_id": "removal_test",
            "name": "Removal Test Product",
            "description": "Testing field removal safety",
            "formats": ["display_300x250"],
            "delivery_type": "non_guaranteed",
            "is_fixed_price": False,
            "is_custom": False,
            "property_tags": ["all_inventory"],  # Required per AdCP spec
        }

        product = Product(**minimal_data)
        adcp_output = product.model_dump()

        # Should produce valid AdCP output even with minimal fields
        required_fields = ["product_id", "name", "description", "format_ids"]
        for field in required_fields:
            assert field in adcp_output
            assert adcp_output[field] is not None

    def test_type_evolution_safety(self):
        """Test that type changes maintain compatibility."""
        # Test numeric type handling (Decimal vs float)
        product_with_decimal = Product(
            product_id="type_evolution_test",
            name="Type Evolution Test",
            description="Testing numeric type evolution",
            formats=["display_300x250"],
            delivery_type="guaranteed",
            is_fixed_price=True,
            cpm=Decimal("15.50"),  # Decimal input
            min_spend=Decimal("2000.00"),  # Decimal input
            is_custom=False,
            property_tags=["all_inventory"],  # Required per AdCP spec
        )

        adcp_output = product_with_decimal.model_dump()

        # Numeric fields should be converted to appropriate types for AdCP
        assert isinstance(adcp_output["cpm"], int | float)
        assert isinstance(adcp_output["min_spend"], int | float)
        assert adcp_output["cpm"] == 15.5  # Decimal converted to float
        assert adcp_output["min_spend"] == 2000.0  # Decimal converted to float
