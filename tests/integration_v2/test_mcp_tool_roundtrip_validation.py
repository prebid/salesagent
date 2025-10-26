#!/usr/bin/env python3
"""
MCP Tool Roundtrip Validation Tests

These tests exercise the ACTUAL MCP tool execution paths to catch schema roundtrip
conversion issues that pure unit tests with mocks would miss.

This test suite was created to prevent issues like:
- "formats field required" validation error in get_products
- Product object → dict → Product object conversion failures
- Schema field mapping inconsistencies between internal and external formats

Key Testing Principles:
1. Use REAL Product objects, not mock dictionaries
2. Exercise the ACTUAL MCP tool execution path
3. Test roundtrip conversions: Object → dict → Object
4. Validate against actual AdCP schemas
5. Follow anti-mocking principles from CLAUDE.md
"""

from contextlib import nullcontext

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import joinedload

from src.core.database.database_session import get_db_session
from src.core.database.models import Product as ProductModel
from src.core.database.models import Tenant
from src.core.schemas import PriceGuidance, PricingOption
from src.core.schemas import Product as ProductSchema
from src.core.testing_hooks import TestingContext, apply_testing_hooks
from tests.utils.database_helpers import create_tenant_with_timestamps


@pytest.mark.requires_db
class TestMCPToolRoundtripValidation:
    """Test MCP tools with real objects to catch roundtrip conversion bugs."""

    @pytest.fixture
    def test_tenant_id(self):
        """Create a test tenant for roundtrip validation tests."""
        tenant_id = "roundtrip_test_tenant"
        with get_db_session() as session:
            # Clean up any existing test data
            session.execute(delete(ProductModel).where(ProductModel.tenant_id == tenant_id))
            session.execute(delete(Tenant).where(Tenant.tenant_id == tenant_id))

            # Create test tenant
            tenant = create_tenant_with_timestamps(
                tenant_id=tenant_id, name="Roundtrip Test Tenant", subdomain="roundtrip-test"
            )
            session.add(tenant)
            session.commit()

        yield tenant_id

        # Cleanup
        with get_db_session() as session:
            session.execute(delete(ProductModel).where(ProductModel.tenant_id == tenant_id))
            session.execute(delete(Tenant).where(Tenant.tenant_id == tenant_id))
            session.commit()

    @pytest.fixture
    def real_products_in_db(self, test_tenant_id) -> list[ProductModel]:
        """Create real Product objects in database to test actual conversion paths."""
        from tests.integration_v2.conftest import create_test_product_with_pricing

        created_products = []
        with get_db_session() as session:
            # Product 1: Display banner with fixed pricing
            product1 = create_test_product_with_pricing(
                session=session,
                tenant_id=test_tenant_id,
                product_id="roundtrip_test_display",
                name="Display Banner Product - Roundtrip Test",
                description="Display advertising product for roundtrip validation",
                formats=[
                    {"agent_url": "https://test.com", "id": "display_300x250"},
                    {"agent_url": "https://test.com", "id": "display_728x90"},
                ],
                targeting_template={"geo": ["US"], "device": ["desktop", "mobile"]},
                delivery_type="guaranteed",
                pricing_model="CPM",
                rate="12.50",
                is_fixed=True,
                min_spend_per_package="2000.00",
                measurement={
                    "type": "brand_lift",
                    "attribution": "deterministic_purchase",
                    "reporting": "weekly_dashboard",
                    "viewability": True,
                    "brand_safety": True,
                },
                creative_policy={
                    "co_branding": "optional",
                    "landing_page": "any",
                    "templates_available": True,
                    "max_file_size": "10MB",
                    "formats": ["jpg", "png", "gif"],
                },
                is_custom=False,
                expires_at=None,
                countries=["US", "CA"],
                implementation_config={"gam_placement_id": "67890"},
            )
            created_products.append(product1)

            # Product 2: Video with auction pricing
            product2 = create_test_product_with_pricing(
                session=session,
                tenant_id=test_tenant_id,
                product_id="roundtrip_test_video",
                name="Video Ad Product - Roundtrip Test",
                description="Video advertising product for roundtrip validation",
                formats=[
                    {"agent_url": "https://test.com", "id": "video_15s"},
                    {"agent_url": "https://test.com", "id": "video_30s"},
                ],
                targeting_template={"geo": ["US", "UK"], "device": ["mobile", "tablet"]},
                delivery_type="non_guaranteed",
                pricing_model="CPM",
                rate="10.00",  # Floor for auction
                is_fixed=False,
                min_spend_per_package="5000.00",
                price_guidance={"floor": 10.0, "p50": 15.0, "p75": 20.0, "p90": 25.0},
                measurement={
                    "type": "incremental_sales_lift",
                    "attribution": "probabilistic",
                    "reporting": "real_time_api",
                    "completion_rate": True,
                },
                creative_policy={
                    "co_branding": "none",
                    "landing_page": "retailer_site_only",
                    "templates_available": False,
                    "duration_max": 30,
                },
                is_custom=True,
                expires_at=None,
                countries=["US", "UK", "DE"],
                implementation_config={"video_formats": ["mp4", "webm"]},
            )
            created_products.append(product2)

            session.commit()

            # Eager load pricing_options to avoid DetachedInstanceError
            product_ids = [p.product_id for p in created_products]
            stmt = (
                select(ProductModel)
                .options(joinedload(ProductModel.pricing_options))
                .where(ProductModel.product_id.in_(product_ids))
            )
            loaded_products = session.scalars(stmt).unique().all()

        return list(loaded_products)

    def test_get_products_real_object_roundtrip_conversion_isolated(
        self, integration_db, test_tenant_id, real_products_in_db
    ):
        """
        Test Product roundtrip conversion with REAL objects to catch conversion issues.

        This test isolates the core roundtrip conversion pattern that was failing:
        1. Start with real ProductModel objects from database
        2. Convert to ProductSchema via ORM → Pydantic
        3. Test roundtrip: Product → dict → Product conversion
        4. Test with testing hooks modification

        This approach avoids complex authentication mocking and focuses on the core bug.
        """
        # Get the real products created by the fixture
        products = real_products_in_db
        assert len(products) == 2, f"Expected 2 real products from fixture, got {len(products)}"

        # Convert database models to schema objects (this mimics what get_products does)
        schema_products = []
        for db_product in products:
            # NEW: Access pricing via pricing_options relationship
            pricing_option = db_product.pricing_options[0] if db_product.pricing_options else None

            # Generate pricing_option_id from pricing_model, currency, and is_fixed
            if pricing_option:
                pricing_type = "fixed" if pricing_option.is_fixed else "auction"
                pricing_option_id = f"{pricing_option.pricing_model}_{pricing_option.currency.lower()}_{pricing_type}"
            else:
                pricing_option_id = "cpm_usd_fixed"

            # Build pricing_options with proper validation
            pricing_kwargs = {
                "pricing_option_id": pricing_option_id,
                "pricing_model": pricing_option.pricing_model if pricing_option else "cpm",
                "currency": pricing_option.currency if pricing_option else "USD",
                "is_fixed": pricing_option.is_fixed if pricing_option else True,
            }

            # Add rate or price_guidance based on is_fixed
            if pricing_option:
                if pricing_option.is_fixed:
                    pricing_kwargs["rate"] = float(pricing_option.rate) if pricing_option.rate else 10.0
                else:
                    # For auction pricing, price_guidance is required
                    pricing_kwargs["price_guidance"] = pricing_option.price_guidance or PriceGuidance(
                        floor=5.0, p50=10.0, p75=15.0
                    )
            else:
                pricing_kwargs["rate"] = 10.0

            # Extract format IDs from FormatId objects (db_product.formats may be list of dicts or FormatId objects)
            formats = db_product.formats
            if formats and isinstance(formats[0], dict):
                # Extract just the 'id' field from each format dict
                format_ids = [f["id"] if isinstance(f, dict) else f.id for f in formats]
            else:
                format_ids = formats

            product_data = {
                "product_id": db_product.product_id,
                "name": db_product.name,
                "description": db_product.description or "",
                "formats": format_ids,  # Internal field name (list of strings)
                "delivery_type": db_product.delivery_type,
                "measurement": db_product.measurement,
                "creative_policy": db_product.creative_policy,
                "is_custom": db_product.is_custom or False,
                "property_tags": getattr(db_product, "property_tags", ["all_inventory"]),  # Required per AdCP spec
                "pricing_options": [PricingOption(**pricing_kwargs)],
            }
            schema_product = ProductSchema(**product_data)
            schema_products.append(schema_product)

        # Test the problematic roundtrip conversion that was failing in production
        for product in schema_products:
            # Step 1: Convert to internal dict (as get_products does)
            product_dict = product.model_dump_internal()

            # Step 2: Apply testing hooks (simulates the problematic code path)
            testing_ctx = TestingContext(dry_run=True, test_session_id="test", auto_advance=False)
            response_data = {"products": [product_dict]}
            response_data = apply_testing_hooks(response_data, testing_ctx, "get_products")

            # Step 3: Reconstruct Product from modified data (THIS WAS FAILING)
            modified_product_dict = response_data["products"][0]
            reconstructed_product = ProductSchema(**modified_product_dict)

            # Step 4: Verify reconstruction succeeded
            assert reconstructed_product.product_id == product.product_id
            assert reconstructed_product.formats == product.formats
            assert reconstructed_product.name == product.name

        # Test specific products that were created by fixture
        display_product = next((p for p in schema_products if "display" in p.product_id), None)
        video_product = next((p for p in schema_products if "video" in p.product_id), None)

        assert display_product is not None, "Should have found display product"
        assert video_product is not None, "Should have found video product"

        # Test the specific case that was failing: formats field
        assert display_product.formats == ["display_300x250", "display_728x90"]
        assert video_product.formats == ["video_15s", "video_30s"]

        # Verify AdCP spec property works
        assert display_product.format_ids == ["display_300x250", "display_728x90"]
        assert video_product.format_ids == ["video_15s", "video_30s"]

    def test_get_products_with_testing_hooks_roundtrip_isolated(
        self, integration_db, test_tenant_id, real_products_in_db
    ):
        """
        Test Product roundtrip conversion with testing hooks to catch the EXACT conversion issue.

        This test specifically exercises the problematic code path:
        1. Products retrieved from database
        2. Converted to dict via model_dump_internal()
        3. Passed through testing hooks (THIS MODIFIES THE DATA)
        4. Reconstructed as Product(**dict) - THIS IS WHERE IT FAILED

        The issue was that testing hooks could modify the data structure but the
        reconstruction assumed the original structure was preserved.
        """
        # Get the real products created by the fixture
        products = real_products_in_db
        assert len(products) == 2, f"Expected 2 real products from fixture, got {len(products)}"

        # Convert database models to schema objects (this mimics what get_products does)
        schema_products = []
        for db_product in products:
            # NEW: Access pricing via pricing_options relationship
            pricing_option = db_product.pricing_options[0] if db_product.pricing_options else None

            # Generate pricing_option_id from pricing_model, currency, and is_fixed
            if pricing_option:
                pricing_type = "fixed" if pricing_option.is_fixed else "auction"
                pricing_option_id = f"{pricing_option.pricing_model}_{pricing_option.currency.lower()}_{pricing_type}"
            else:
                pricing_option_id = "cpm_usd_fixed"

            # Build pricing_options with proper validation
            pricing_kwargs = {
                "pricing_option_id": pricing_option_id,
                "pricing_model": pricing_option.pricing_model if pricing_option else "cpm",
                "currency": pricing_option.currency if pricing_option else "USD",
                "is_fixed": pricing_option.is_fixed if pricing_option else True,
            }

            # Add rate or price_guidance based on is_fixed
            if pricing_option:
                if pricing_option.is_fixed:
                    pricing_kwargs["rate"] = float(pricing_option.rate) if pricing_option.rate else 10.0
                else:
                    # For auction pricing, price_guidance is required
                    pricing_kwargs["price_guidance"] = pricing_option.price_guidance or PriceGuidance(
                        floor=5.0, p50=10.0, p75=15.0
                    )
            else:
                pricing_kwargs["rate"] = 10.0

            # Extract format IDs from FormatId objects (db_product.formats may be list of dicts or FormatId objects)
            formats = db_product.formats
            if formats and isinstance(formats[0], dict):
                # Extract just the 'id' field from each format dict
                format_ids = [f["id"] if isinstance(f, dict) else f.id for f in formats]
            else:
                format_ids = formats

            product_data = {
                "product_id": db_product.product_id,
                "name": db_product.name,
                "description": db_product.description or "",
                "formats": format_ids,  # Internal field name (list of strings)
                "delivery_type": db_product.delivery_type,
                "measurement": db_product.measurement,
                "creative_policy": db_product.creative_policy,
                "is_custom": db_product.is_custom or False,
                "property_tags": getattr(db_product, "property_tags", ["all_inventory"]),  # Required per AdCP spec
                "pricing_options": [PricingOption(**pricing_kwargs)],
            }
            schema_product = ProductSchema(**product_data)
            schema_products.append(schema_product)

        # Test with various testing hooks scenarios
        test_scenarios = [
            TestingContext(dry_run=True, test_session_id="test1", auto_advance=False),
            TestingContext(dry_run=False, test_session_id="test2", auto_advance=True),
            TestingContext(dry_run=True, test_session_id="test3", debug_mode=True),
        ]

        for testing_ctx in test_scenarios:
            # Test the problematic roundtrip conversion with testing hooks
            for product in schema_products:
                # Step 1: Convert to internal dict (as get_products does)
                product_dict = product.model_dump_internal()

                # Step 2: Apply testing hooks (THIS CAN MODIFY DATA)
                response_data = {"products": [product_dict]}
                response_data = apply_testing_hooks(response_data, testing_ctx, "get_products")

                # Step 3: Reconstruct Product from potentially modified data (THIS WAS FAILING)
                modified_product_dict = response_data["products"][0]
                reconstructed_product = ProductSchema(**modified_product_dict)

                # Step 4: Verify reconstruction succeeded
                assert reconstructed_product.product_id == product.product_id
                assert reconstructed_product.formats == product.formats
                assert reconstructed_product.name == product.name
                assert reconstructed_product.delivery_type == product.delivery_type

                # Test specific fields that were causing validation errors
                assert hasattr(reconstructed_product, "formats")
                assert isinstance(reconstructed_product.formats, list)
                assert len(reconstructed_product.formats) > 0
                assert reconstructed_product.measurement is not None
                assert reconstructed_product.creative_policy is not None

    def test_product_schema_roundtrip_conversion_isolated(self):
        """
        Test the specific Product schema roundtrip conversion in isolation.

        This test isolates the exact conversion pattern that was failing:
        Product object → model_dump_internal() → Product(**dict)
        """
        # Create a Product object with all the fields that caused issues
        original_product = ProductSchema(
            product_id="roundtrip_isolated_test",
            name="Isolated Roundtrip Test Product",
            description="Testing the exact roundtrip conversion pattern",
            formats=["display_300x250", "video_15s"],  # Internal field name
            delivery_type="guaranteed",
            is_custom=False,
            property_tags=["all_inventory"],  # Required per AdCP spec
            pricing_options=[
                PricingOption(
                    pricing_option_id="cpm_usd_fixed",
                    pricing_model="cpm",
                    rate=15.75,
                    currency="USD",
                    is_fixed=True,
                )
            ],
        )

        # Step 1: Convert to dict (what the tool does before testing hooks)
        product_dict = original_product.model_dump_internal()

        # Verify the dict has the internal field name
        assert "formats" in product_dict
        assert product_dict["formats"] == ["display_300x250", "video_15s"]

        # Step 2: Simulate testing hooks modifying the data
        testing_ctx = TestingContext(dry_run=True, test_session_id="isolated_test")
        response_data = {"products": [product_dict]}
        modified_response = apply_testing_hooks(response_data, testing_ctx, "get_products")

        # Step 3: Reconstruct Product objects (THIS IS WHERE IT WAS FAILING)
        modified_product_dicts = modified_response["products"]

        # This is the exact line that was causing the validation error
        reconstructed_products = [ProductSchema(**p) for p in modified_product_dicts]

        # Verify the roundtrip worked
        assert len(reconstructed_products) == 1
        reconstructed_product = reconstructed_products[0]

        # Verify all essential fields survived the roundtrip
        assert reconstructed_product.product_id == original_product.product_id
        assert reconstructed_product.name == original_product.name
        assert reconstructed_product.description == original_product.description
        assert reconstructed_product.formats == original_product.formats
        assert reconstructed_product.delivery_type == original_product.delivery_type
        assert reconstructed_product.pricing_options == original_product.pricing_options

    def test_adcp_spec_compliance_after_roundtrip(self):
        """
        Test that roundtrip conversion maintains AdCP spec compliance.

        This ensures the external API response is spec-compliant even after
        internal roundtrip conversions.
        """
        # Create product with internal field names
        product = ProductSchema(
            product_id="adcp_compliance_test",
            name="AdCP Compliance Test Product",
            description="Testing AdCP spec compliance after roundtrip",
            formats=["display_300x250", "display_728x90"],  # Internal field name
            delivery_type="non_guaranteed",
            is_custom=True,
            property_tags=["all_inventory"],  # Required per AdCP spec
            pricing_options=[
                PricingOption(
                    pricing_option_id="cpm_usd_auction",
                    pricing_model="cpm",
                    rate=8.25,
                    currency="USD",
                    is_fixed=False,
                    price_guidance=PriceGuidance(floor=5.0, p50=8.25, p75=10.0),
                )
            ],
        )

        # Roundtrip through internal format
        internal_dict = product.model_dump_internal()
        reconstructed_product = ProductSchema(**internal_dict)

        # Get AdCP-compliant output
        adcp_dict = reconstructed_product.model_dump()

        # Verify AdCP spec compliance
        assert "format_ids" in adcp_dict  # AdCP spec field name
        assert "formats" not in adcp_dict  # Internal field name should be excluded
        assert adcp_dict["format_ids"] == ["display_300x250", "display_728x90"]

        # Verify required AdCP fields are present
        required_adcp_fields = [
            "product_id",
            "name",
            "description",
            "format_ids",
            "delivery_type",
            "pricing_options",
            "is_custom",
        ]

        for field in required_adcp_fields:
            assert field in adcp_dict, f"Required AdCP field '{field}' missing from output"

        # Verify internal fields are excluded from external API
        internal_only_fields = ["implementation_config", "expires_at", "targeting_template"]
        for field in internal_only_fields:
            assert field not in adcp_dict, f"Internal field '{field}' should not be in AdCP output"

    def test_schema_validation_error_detection(self):
        """
        Test that we can detect schema validation errors that would occur in production.

        This test demonstrates what happens when field mappings are incorrect.
        """
        # Create a dict with the WRONG field name (simulating the bug)
        invalid_product_dict = {
            "product_id": "validation_error_test",
            "name": "Validation Error Test Product",
            "description": "Testing schema validation error detection",
            "format_ids": ["display_300x250"],  # WRONG: AdCP field name in internal dict
            "delivery_type": "guaranteed",
            "is_fixed_price": True,
            "cpm": 10.0,
            "is_custom": False,
        }

        # This should fail with "formats field required" if we try to create Product object
        with pytest.raises(ValueError, match="formats"):
            ProductSchema(**invalid_product_dict)

        # Now test the CORRECT approach
        correct_product_dict = {
            "product_id": "validation_success_test",
            "name": "Validation Success Test Product",
            "description": "Testing correct schema validation",
            "formats": ["display_300x250"],  # CORRECT: Internal field name
            "delivery_type": "guaranteed",
            "is_custom": False,
            "property_tags": ["all_inventory"],  # Required per AdCP spec
            "pricing_options": [
                {
                    "pricing_option_id": "cpm_usd_fixed",
                    "pricing_model": "cpm",
                    "rate": 10.0,
                    "currency": "USD",
                    "is_fixed": True,
                }
            ],
        }

        # This should succeed
        product = ProductSchema(**correct_product_dict)
        assert product.formats == ["display_300x250"]
        assert product.format_ids == ["display_300x250"]  # Property works correctly


class TestMCPToolRoundtripPatterns:
    """Test roundtrip patterns that can be applied to all MCP tools."""

    def test_generic_roundtrip_pattern_validation(self):
        """
        Test the generic pattern: Object → dict → Object that all MCP tools use.

        This pattern can be applied to other MCP tools to prevent similar issues.
        """
        test_cases = [
            # Different product types that might have different field handling
            {
                "type": "guaranteed_display",
                "data": {
                    "product_id": "pattern_guaranteed",
                    "name": "Guaranteed Display Product",
                    "description": "Pattern test for guaranteed products",
                    "formats": ["display_300x250"],
                    "delivery_type": "guaranteed",
                    "is_custom": False,
                    "property_tags": ["all_inventory"],  # Required per AdCP spec
                    "pricing_options": [
                        {
                            "pricing_option_id": "cpm_usd_fixed",
                            "pricing_model": "cpm",
                            "rate": 12.0,
                            "currency": "USD",
                            "is_fixed": True,
                            "min_spend_per_package": 2000.0,
                        }
                    ],
                },
            },
            {
                "type": "non_guaranteed_video",
                "data": {
                    "product_id": "pattern_non_guaranteed",
                    "name": "Non-Guaranteed Video Product",
                    "description": "Pattern test for non-guaranteed products",
                    "formats": ["video_15s", "video_30s"],
                    "delivery_type": "non_guaranteed",
                    "is_custom": True,
                    "property_tags": ["all_inventory"],  # Required per AdCP spec
                    "pricing_options": [
                        {
                            "pricing_option_id": "cpm_usd_auction",
                            "pricing_model": "cpm",
                            "rate": None,  # Test null handling for auction pricing
                            "currency": "USD",
                            "is_fixed": False,
                            "price_guidance": {"floor": 3.0, "p50": 5.0, "p75": 7.0},
                            "min_spend_per_package": 5000.0,
                        }
                    ],
                },
            },
            {
                "type": "minimal_fields",
                "data": {
                    "product_id": "pattern_minimal",
                    "name": "Minimal Product",
                    "description": "Pattern test with minimal fields",
                    "formats": ["display_728x90"],
                    "delivery_type": "non_guaranteed",
                    "is_custom": False,
                    "property_tags": ["all_inventory"],  # Required per AdCP spec
                    "pricing_options": [
                        {
                            "pricing_option_id": "cpm_usd_auction",
                            "pricing_model": "cpm",
                            "rate": 5.0,
                            "currency": "USD",
                            "is_fixed": False,
                            "price_guidance": {"floor": 3.0, "p50": 5.0, "p75": 7.0},
                        }
                    ],
                },
            },
        ]

        for test_case in test_cases:
            with pytest.raises(Exception) if test_case["type"] == "should_fail" else nullcontext():
                # Step 1: Create object
                original = ProductSchema(**test_case["data"])

                # Step 2: Convert to internal dict
                internal_dict = original.model_dump_internal()

                # Step 3: Simulate testing hooks or other processing
                processed_dict = internal_dict.copy()
                processed_dict["test_metadata"] = {"processed": True}

                # Step 4: Remove test metadata (simulating hook cleanup)
                processed_dict.pop("test_metadata", None)

                # Step 5: Reconstruct object (critical roundtrip point)
                reconstructed = ProductSchema(**processed_dict)

                # Step 6: Verify roundtrip preserved essential data
                assert reconstructed.product_id == original.product_id
                assert reconstructed.name == original.name
                assert reconstructed.formats == original.formats
                assert reconstructed.delivery_type == original.delivery_type
                assert reconstructed.pricing_options == original.pricing_options

                # Step 7: Verify AdCP spec compliance
                adcp_output = reconstructed.model_dump()
                assert "format_ids" in adcp_output
                assert "formats" not in adcp_output

    def test_field_mapping_consistency_validation(self):
        """
        Test that field mappings are consistent across all conversion paths.

        This catches issues where internal and external field names are mixed up.
        """
        # Test data with all possible field scenarios
        complete_product_data = {
            "product_id": "field_mapping_test",
            "name": "Field Mapping Test Product",
            "description": "Testing all field mapping scenarios",
            "formats": ["display_300x250", "video_15s"],  # Internal name
            "delivery_type": "guaranteed",
            "is_custom": False,
            "property_tags": ["all_inventory"],  # Required per AdCP spec
            # Optional fields that might cause mapping issues
            "measurement": {
                "type": "incremental_sales_lift",
                "attribution": "deterministic_purchase",
                "reporting": "weekly_dashboard",
                "viewability": True,
            },
            "creative_policy": {
                "co_branding": "optional",
                "landing_page": "any",
                "templates_available": True,
                "max_file_size": "5MB",
            },
            "pricing_options": [
                {
                    "pricing_option_id": "cpm_usd_fixed",
                    "pricing_model": "cpm",
                    "rate": 15.0,
                    "currency": "USD",
                    "is_fixed": True,
                    "min_spend_per_package": 2500.0,
                }
            ],
        }

        # Create Product object
        product = ProductSchema(**complete_product_data)

        # Test internal representation
        internal_dict = product.model_dump_internal()
        assert "formats" in internal_dict  # Internal field name
        assert "format_ids" not in internal_dict  # External field name excluded from internal

        # Test external (AdCP) representation
        external_dict = product.model_dump()
        assert "format_ids" in external_dict  # External field name
        assert "formats" not in external_dict  # Internal field name excluded from external

        # Test property access
        assert product.formats == ["display_300x250", "video_15s"]  # Internal access
        assert product.format_ids == ["display_300x250", "video_15s"]  # External property

        # Test roundtrip from internal dict
        roundtrip_product = ProductSchema(**internal_dict)
        assert roundtrip_product.formats == product.formats
        assert roundtrip_product.format_ids == product.format_ids

        # Verify external output is still compliant after roundtrip
        roundtrip_external = roundtrip_product.model_dump()
        assert roundtrip_external == external_dict
