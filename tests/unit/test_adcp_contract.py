"""Contract tests to ensure database models match AdCP protocol schemas.

These tests verify that:
1. Database models have all required fields for AdCP schemas
2. Field types are compatible
3. Data can be correctly transformed between models and schemas
4. AdCP protocol requirements are met
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from adcp.types import CreativePolicy

from src.core.database.models import (
    Principal as PrincipalModel,
)  # Need both for contract test
from src.core.database.models import Product as ProductModel
from src.core.schemas import (
    Budget,
    CreateMediaBuyRequest,
    CreateMediaBuyResponse,
    Creative,
    CreativeApprovalStatus,
    CreativeAssignment,
    Format,
    FormatId,
    GetMediaBuyDeliveryRequest,
    GetMediaBuyDeliveryResponse,
    GetProductsRequest,
    GetProductsResponse,
    ListAuthorizedPropertiesRequest,
    ListAuthorizedPropertiesResponse,
    ListCreativeFormatsResponse,
    ListCreativesResponse,
    Measurement,
    MediaBuyDeliveryData,
    Package,
    Pagination,
    Property,
    PropertyIdentifier,
    PropertyTagMetadata,
    QuerySummary,
    Signal,
    SignalDeployment,
    SyncCreativesRequest,
    SyncCreativesResponse,
    Targeting,
    TaskStatus,
)
from src.core.schemas import (
    Principal as PrincipalSchema,
)
from src.core.schemas import (
    Product as ProductSchema,
)


class TestSchemaMatchesLibrary:
    """Validate that our schemas match the adcp library schemas.

    These tests ensure we don't accidentally deviate from the AdCP spec
    by comparing our field definitions against the library's generated schemas.
    """

    def test_all_request_schemas_match_library(self):
        """Comprehensive test that all request schemas match library definitions.

        This test documents any drift between our local schemas and the library.
        Non-spec fields should be explicitly documented and eventually removed.
        """
        from adcp import (
            CreateMediaBuyRequest as LibCreateMediaBuyRequest,
        )
        from adcp import (
            GetMediaBuyDeliveryRequest as LibGetMediaBuyDeliveryRequest,
        )
        from adcp import (
            GetSignalsRequest as LibGetSignalsRequest,
        )

        # NOTE: ListAuthorizedPropertiesRequest was removed from adcp 3.2.0
        # We define it locally in src/core/schemas.py
        from adcp import (
            ListCreativeFormatsRequest as LibListCreativeFormatsRequest,
        )
        from adcp import (
            ListCreativesRequest as LibListCreativesRequest,
        )
        from adcp import (
            SyncCreativesRequest as LibSyncCreativesRequest,
        )
        from adcp.types import (
            GetProductsWholesaleRequest as LibGetProductsRequest,
        )

        from src.core.schemas import (
            CreateMediaBuyRequest as LocalCreateMediaBuyRequest,
        )
        from src.core.schemas import (
            GetMediaBuyDeliveryRequest as LocalGetMediaBuyDeliveryRequest,
        )
        from src.core.schemas import (
            GetSignalsRequest as LocalGetSignalsRequest,
        )

        # NOTE: ListAuthorizedPropertiesRequest comparison skipped - removed from adcp 3.2.0
        from src.core.schemas import (
            ListCreativeFormatsRequest as LocalListCreativeFormatsRequest,
        )
        from src.core.schemas import (
            ListCreativesRequest as LocalListCreativesRequest,
        )
        from src.core.schemas import (
            SyncCreativesRequest as LocalSyncCreativesRequest,
        )

        # GetProductsRequest - local extends library with internal-only fields
        lib_fields = set(LibGetProductsRequest.model_fields.keys())
        local_fields = set(GetProductsRequest.model_fields.keys())
        # product_selectors — internal-only field (not in AdCP spec)
        # buying_mode and account are now in the library (adcp 3.9) but overridden locally
        # (buying_mode widened to str|None, account made optional)
        local_extensions = {"product_selectors"}
        assert lib_fields == local_fields - local_extensions, (
            f"GetProductsRequest drift: lib={lib_fields}, local={local_fields}"
        )

        # GetMediaBuyDeliveryRequest - local extends library with spec fields
        lib_fields = set(LibGetMediaBuyDeliveryRequest.model_fields.keys())
        local_fields = set(LocalGetMediaBuyDeliveryRequest.model_fields.keys())
        # adcp 3.9: all fields now in library — no local extensions remaining
        local_extensions: set[str] = set()
        assert lib_fields == local_fields - local_extensions, (
            f"GetMediaBuyDeliveryRequest drift: lib={lib_fields}, local={local_fields}"
        )

        # Document known drift for other schemas (to be fixed)
        # These assertions document the current state and will fail when fixed

        # CreateMediaBuyRequest - has many non-spec convenience fields
        # CreateMediaBuyRequest - now extends library, should match
        lib_fields = set(LibCreateMediaBuyRequest.model_fields.keys())
        local_fields = set(LocalCreateMediaBuyRequest.model_fields.keys())
        assert lib_fields == local_fields, f"CreateMediaBuyRequest drift: lib={lib_fields}, local={local_fields}"

        # ListCreativesRequest - now extends library, should match
        lib_fields = set(LibListCreativesRequest.model_fields.keys())
        local_fields = set(LocalListCreativesRequest.model_fields.keys())
        assert lib_fields == local_fields, f"ListCreativesRequest drift: lib={lib_fields}, local={local_fields}"

        # ListCreativeFormatsRequest - now extends library, should match
        lib_fields = set(LibListCreativeFormatsRequest.model_fields.keys())
        local_fields = set(LocalListCreativeFormatsRequest.model_fields.keys())
        assert lib_fields == local_fields, f"ListCreativeFormatsRequest drift: lib={lib_fields}, local={local_fields}"

        # NOTE: ListAuthorizedPropertiesRequest comparison skipped - type removed from adcp 3.2.0
        # We define it locally in src/core/schemas.py with fields: context, ext, property_tags, publisher_domains

        # GetSignalsRequest - adcp 3.9 now includes signal_ids and pagination
        lib_fields = set(LibGetSignalsRequest.model_fields.keys())
        local_fields = set(LocalGetSignalsRequest.model_fields.keys())
        assert lib_fields == local_fields, f"GetSignalsRequest drift: lib={lib_fields}, local={local_fields}"

        # SyncCreativesRequest - now has ext field, should match
        lib_fields = set(LibSyncCreativesRequest.model_fields.keys())
        local_fields = set(LocalSyncCreativesRequest.model_fields.keys())
        assert lib_fields == local_fields, f"SyncCreativesRequest drift: lib={lib_fields}, local={local_fields}"

    def test_get_products_request_field_optionality(self):
        """Verify GetProductsRequest fields match library optionality.

        Per AdCP spec, all fields in GetProductsRequest are optional.
        This test catches accidental regressions where we make fields required.
        In adcp 3.6.0, brand_manifest is replaced by brand (BrandReference with domain).
        """
        from adcp.types import GetProductsWholesaleRequest as LibraryGetProductsRequest

        # Verify library allows empty request (buying_mode is required for wholesale variant)
        lib_req = LibraryGetProductsRequest(buying_mode="wholesale")
        assert lib_req.brief is None
        assert lib_req.brand is None  # adcp 3.6.0: brand replaces brand_manifest
        assert lib_req.context is None
        assert lib_req.filters is None

        # Our schema widens buying_mode to optional, so empty request works
        our_req = GetProductsRequest()
        assert our_req.brief is None
        assert our_req.brand is None  # adcp 3.6.0: brand replaces brand_manifest

    def test_get_products_request_brand_accepts_domain(self):
        """Verify brand (BrandReference) accepts domain field per adcp 3.6.0."""
        from adcp.types import GetProductsWholesaleRequest as LibraryGetProductsRequest

        # Library accepts brand with domain (buying_mode required for wholesale variant)
        lib_req = LibraryGetProductsRequest(brand={"domain": "acme.com"}, buying_mode="wholesale")
        assert lib_req.brand is not None
        assert lib_req.brand.domain == "acme.com"

        # Our schema should also accept brand with domain
        our_req = GetProductsRequest(brand={"domain": "acme.com"})
        assert our_req.brand is not None

    def test_create_media_buy_request_brand_required(self):
        """Verify CreateMediaBuyRequest requires brand (unlike GetProductsRequest).

        In adcp 3.6.0, brand (BrandReference) is required for CreateMediaBuyRequest.
        """
        from adcp import CreateMediaBuyRequest as LibraryCreateMediaBuyRequest
        from pydantic import ValidationError

        # Library should require brand for CreateMediaBuyRequest
        with pytest.raises(ValidationError):
            LibraryCreateMediaBuyRequest(buyer_ref="test")

    def test_schema_validation_matches_library(self):
        """Compare our schema validation against library for common cases."""
        from adcp.types import GetProductsWholesaleRequest as LibraryGetProductsRequest

        # Test cases that should work in both (adcp 3.6.0: brand replaces brand_manifest)
        # buying_mode is required for the wholesale variant in adcp 3.9
        test_cases = [
            {"buying_mode": "wholesale"},  # Minimal valid
            {"buying_mode": "wholesale", "brief": "test"},  # Brief only
            {"buying_mode": "wholesale", "brand": {"domain": "acme.com"}},  # BrandReference
            {"buying_mode": "wholesale", "brief": "test", "brand": {"domain": "acme.com"}},  # Both
        ]

        for case in test_cases:
            # Library should accept
            lib_req = LibraryGetProductsRequest(**case)
            # Our schema should also accept
            our_req = GetProductsRequest(**case)

            # Basic field values should match
            assert (lib_req.brief is None) == (our_req.brief is None), f"brief mismatch for {case}"
            assert (lib_req.brand is None) == (our_req.brand is None), f"brand mismatch for {case}"


class TestAdCPContract:
    """Test that models and schemas align with AdCP protocol requirements."""

    @staticmethod
    def _make_pricing_option(
        tenant_id: str, product_id: str, is_fixed: bool = True, rate: float | None = 10.50
    ) -> dict:
        """Helper to create pricing option dict for tests."""
        return {
            "tenant_id": tenant_id,
            "product_id": product_id,
            "pricing_model": "cpm",
            "rate": Decimal(str(rate)) if rate else None,
            "currency": "USD",
            "is_fixed": is_fixed,
            "parameters": None,
            "min_spend_per_package": None,
        }

    def test_product_model_to_schema(self):
        """Test that Product model can be converted to AdCP Product schema."""
        # Create a model instance with all required fields
        model = ProductModel(
            tenant_id="test_tenant",
            product_id="test_product",
            name="Test Product",
            description="A test product for AdCP protocol",
            format_ids=[
                {"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"}
            ],  # Now stores FormatId objects per AdCP spec
            targeting_template={"geo_country": {"values": ["US", "CA"], "required": False}},
            delivery_type="guaranteed",  # AdCP: guaranteed or non_guaranteed
            is_custom=False,
            expires_at=None,
            countries=["US", "CA"],
            implementation_config={"internal": "config"},
        )

        # Create pricing option using library discriminated union format
        from tests.helpers.adcp_factories import create_test_cpm_pricing_option, create_test_publisher_properties_by_tag

        pricing_option = create_test_cpm_pricing_option(
            pricing_option_id="cpm_usd_fixed",
            currency="USD",
            rate=10.50,
        )

        # Convert to dict (simulating database retrieval and conversion)
        # format_ids are now FormatId objects per AdCP spec
        model_dict = {
            "product_id": model.product_id,
            "name": model.name,
            "description": model.description,
            "format_ids": model.format_ids,  # FormatId objects with agent_url and id
            "delivery_type": model.delivery_type,
            "pricing_options": [pricing_option],
            "is_custom": model.is_custom,
            "expires_at": model.expires_at,
            "publisher_properties": [
                create_test_publisher_properties_by_tag(publisher_domain="test.com")
            ],  # Required per AdCP spec - discriminated union format
            "delivery_measurement": {
                "provider": "test_provider",
                "notes": "Test measurement",
            },  # Required per AdCP spec
        }

        # Should be convertible to AdCP schema
        schema = ProductSchema(**model_dict)

        # Verify AdCP required fields
        assert schema.product_id == "test_product"
        assert schema.name == "Test Product"
        assert schema.description == "A test product for AdCP protocol"
        assert str(schema.delivery_type.value) in ["guaranteed", "non_guaranteed"]  # Enum value
        assert len(schema.format_ids) > 0

        # Verify format IDs match AdCP (now FormatId objects)
        assert schema.format_ids[0].id == "display_300x250"
        assert str(schema.format_ids[0].agent_url).rstrip("/") == "https://creative.adcontextprotocol.org"

    def test_product_non_guaranteed(self):
        """Test non-guaranteed product (AdCP spec compliant - no price_guidance)."""
        model = ProductModel(
            tenant_id="test_tenant",
            product_id="test_ng_product",
            name="Non-Guaranteed Product",
            description="AdCP non-guaranteed product",
            format_ids=[
                {"agent_url": "https://creative.adcontextprotocol.org", "id": "video_15s"}
            ],  # Now stores format IDs as strings
            targeting_template={},
            delivery_type="non_guaranteed",
            is_custom=False,
            expires_at=None,
            countries=["US"],
            implementation_config=None,
        )

        # Use library discriminated union format
        from tests.helpers.adcp_factories import create_test_cpm_pricing_option, create_test_publisher_properties_by_tag

        model_dict = {
            "product_id": model.product_id,
            "name": model.name,
            "description": model.description,
            "format_ids": model.format_ids,
            "delivery_type": model.delivery_type,
            "is_custom": model.is_custom,
            "expires_at": model.expires_at,
            "publisher_properties": [
                create_test_publisher_properties_by_tag(publisher_domain="test.com")
            ],  # Required per AdCP spec - discriminated union format
            "pricing_options": [
                create_test_cpm_pricing_option(
                    pricing_option_id="cpm_usd_fixed",
                    currency="USD",
                    rate=10.0,
                )
            ],
            "delivery_measurement": {
                "provider": "test_provider",
                "notes": "Test measurement",
            },  # Required per AdCP spec
        }

        schema = ProductSchema(**model_dict)

        # AdCP spec: non_guaranteed products use auction-based pricing (no price_guidance)
        assert str(schema.delivery_type.value) == "non_guaranteed"  # Enum value

    def test_principal_model_to_schema(self):
        """Test that Principal model matches AdCP authentication requirements."""
        model = PrincipalModel(
            tenant_id="test_tenant",
            principal_id="test_principal",
            name="Test Advertiser",
            access_token="secure_token_123",
            platform_mappings={"google_ad_manager": {"advertiser_id": "123456"}, "mock": {"id": "test"}},
        )

        # Convert to schema format
        schema = PrincipalSchema(
            principal_id=model.principal_id,
            name=model.name,
            platform_mappings=model.platform_mappings,
        )

        # Test AdCP authentication
        assert schema.principal_id == "test_principal"
        assert schema.name == "Test Advertiser"

        # Test adapter ID retrieval (AdCP requirement for multi-platform support)
        assert schema.get_adapter_id("gam") == "123456"
        assert schema.get_adapter_id("google_ad_manager") == "123456"
        assert schema.get_adapter_id("mock") == "test"

    def test_adcp_get_products_request(self):
        """Test AdCP get_products request per spec - all fields optional.

        In adcp 3.6.0, brand_manifest is replaced by brand (BrandReference with domain field).
        """
        # Per AdCP spec, all fields are optional
        # Empty request is valid
        empty_request = GetProductsRequest()
        assert empty_request.brief is None
        assert empty_request.brand is None  # adcp 3.6.0: brand replaces brand_manifest

        # Request with brief only
        brief_only = GetProductsRequest(brief="Looking for display ads on news sites")
        assert brief_only.brief == "Looking for display ads on news sites"
        assert brief_only.brand is None

        # Request with brand only (adcp 3.6.0: uses BrandReference with domain)
        brand_only = GetProductsRequest(
            brand={"domain": "saas.example.com"},
        )
        assert brand_only.brief is None
        assert brand_only.brand is not None

        # Request with both (common case)
        full_request = GetProductsRequest(
            brief="Looking for display ads",
            brand={"domain": "acme.com"},
        )
        assert full_request.brief is not None
        assert full_request.brand is not None

    def test_product_pr79_fields(self):
        """Test Product schema compliance with AdCP PR #79 (filtering and pricing enhancements).

        AdCP pricing enhancements:
        - min_exposures filter in get_products request
        - currency field (ISO 4217) in pricing_options
        - estimated_exposures for guaranteed products
        - price_guidance (floor, percentiles) in pricing_options for non-guaranteed products
        """
        from tests.helpers.adcp_factories import (
            create_test_cpm_pricing_option,
            create_test_publisher_properties_by_tag,
        )

        # Test guaranteed product with estimated_exposures
        guaranteed_product = ProductSchema(
            product_id="test_guaranteed",
            name="Guaranteed Product",
            description="Test product with exposure estimates",
            format_ids=[{"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"}],
            delivery_type="guaranteed",
            delivery_measurement={"provider": "test_provider", "notes": "Test measurement"},  # Required per AdCP spec
            pricing_options=[
                create_test_cpm_pricing_option(
                    pricing_option_id="cpm_usd_fixed",
                    currency="USD",
                    rate=15.0,
                )
            ],
            estimated_exposures=50000,
            publisher_properties=[
                create_test_publisher_properties_by_tag(publisher_domain="test.com")
            ],  # Required per AdCP spec
        )

        # Verify AdCP-compliant response includes PR #79 fields
        adcp_response = guaranteed_product.model_dump()
        assert "estimated_exposures" in adcp_response
        assert adcp_response["estimated_exposures"] == 50000

        # Test non-guaranteed product with price_guidance in pricing_options
        non_guaranteed_product = ProductSchema(
            product_id="test_non_guaranteed",
            name="Non-Guaranteed Product",
            description="Test product with CPM guidance",
            format_ids=[{"agent_url": "https://creative.adcontextprotocol.org", "id": "video_15s"}],
            delivery_type="non_guaranteed",
            delivery_measurement={"provider": "test_provider", "notes": "Test measurement"},  # Required per AdCP spec
            pricing_options=[
                {
                    "pricing_option_id": "cpm_eur_auction",
                    "pricing_model": "cpm",
                    "currency": "EUR",
                    # V3 Migration: is_fixed removed, floor moved to top-level floor_price
                    "floor_price": 5.0,  # V3: was price_guidance.floor
                    "price_guidance": {"p75": 8.5, "p90": 10.0},
                }
            ],
            publisher_properties=[
                create_test_publisher_properties_by_tag(publisher_domain="test.com")
            ],  # Required per AdCP spec
        )

        adcp_response = non_guaranteed_product.model_dump()
        # Currency is now in pricing_options, not at product level
        assert adcp_response["pricing_options"][0]["currency"] == "EUR"
        # V3 Migration: floor is now top-level floor_price, not inside price_guidance
        assert adcp_response["pricing_options"][0]["floor_price"] == 5.0
        assert adcp_response["pricing_options"][0]["price_guidance"]["p75"] == 8.5  # p75 used as recommended
        assert adcp_response["pricing_options"][0]["price_guidance"]["p90"] == 10.0

        # Verify GetProductsRequest accepts brand (BrandReference) when provided
        # Note: Per AdCP spec, brand is OPTIONAL (not required)
        # adcp 3.6.0: brand_manifest replaced by brand (BrandReference with required domain)
        request = GetProductsRequest(
            brief="Looking for high-volume campaigns",
            brand={"domain": "nike.com"},
        )
        assert request.brand is not None
        # Local schema stores brand as dict (library coerces to BrandReference)
        if isinstance(request.brand, dict):
            assert request.brand["domain"] == "nike.com"
        else:
            assert request.brand.domain == "nike.com"

        # Should succeed without brand (per AdCP spec, it's optional)
        brief_only_request = GetProductsRequest(brief="Just a brief")
        assert brief_only_request.brief == "Just a brief"
        assert brief_only_request.brand is None

    def test_product_publisher_properties_required(self):
        """Test Product schema requires publisher_properties per AdCP spec.

        AdCP spec requires products to have publisher_properties:
        - publisher_properties: Array of full Property objects for adagents.json validation
        """
        from tests.helpers.adcp_factories import (
            create_test_cpm_pricing_option,
            create_test_publisher_properties_by_tag,
        )

        # Test with publisher_properties (AdCP-compliant approach using factory)
        product_with_properties = ProductSchema(
            product_id="test_product_properties",
            name="Product with Properties",
            description="Product with full property objects",
            format_ids=[{"agent_url": "https://creative.adcontextprotocol.org", "id": "video_15s"}],
            delivery_type="non_guaranteed",
            delivery_measurement={"provider": "test_provider", "notes": "Test measurement"},  # Required per AdCP spec
            publisher_properties=[
                create_test_publisher_properties_by_tag(
                    publisher_domain="example.com", property_tags=["premium_sports"]
                )
            ],
            pricing_options=[
                {
                    "pricing_option_id": "cpm_usd_auction",
                    "pricing_model": "cpm",
                    "currency": "USD",
                    "is_fixed": False,  # Required in adcp 2.4.0+
                    "price_guidance": {"floor": 1.0, "p50": 5.0},
                }
            ],
        )

        adcp_response = product_with_properties.model_dump()
        assert "publisher_properties" in adcp_response
        assert len(adcp_response["publisher_properties"]) >= 1
        assert adcp_response["publisher_properties"][0]["publisher_domain"] == "example.com"
        assert adcp_response["publisher_properties"][0]["property_tags"] == ["premium_sports"]

        # Test without publisher_properties should fail (strict validation enabled)
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="publisher_properties"):
            ProductSchema(
                product_id="test_product_no_props",
                name="Invalid Product",
                description="Missing property information",
                format_ids=[{"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"}],
                delivery_type="guaranteed",
                delivery_measurement={
                    "provider": "test_provider",
                    "notes": "Test measurement",
                },  # Required per AdCP spec
                pricing_options=[
                    create_test_cpm_pricing_option(
                        pricing_option_id="cpm_usd_fixed",
                        currency="USD",
                        rate=10.0,
                    )
                ],
                # Missing publisher_properties
            )

    def test_product_format_ids_required_in_conversion(self):
        """Test that product conversion fails when format_ids is missing.

        Products without format_ids configured are invalid for media buys because
        we cannot validate creative compatibility. Per AdCP spec, products must
        specify supported formats to be available for purchase.
        """
        from unittest.mock import MagicMock

        from src.core.product_conversion import convert_product_model_to_schema

        # Create a mock product with no format_ids
        product_model = MagicMock()
        product_model.product_id = "prod_no_formats"
        product_model.name = "Product Without Formats"
        product_model.description = "This product has no format_ids configured"
        product_model.delivery_type = "guaranteed"
        product_model.effective_format_ids = []  # Empty - no formats configured
        product_model.effective_properties = [{"publisher_domain": "example.com", "property_tags": ["test"]}]
        product_model.pricing_options = [
            MagicMock(
                pricing_model="cpm",
                is_fixed=True,
                currency="USD",
                rate=10.0,
                price_guidance=None,
                min_spend_per_package=None,
                parameters=None,
            )
        ]

        # Conversion should fail with a clear error message
        with pytest.raises(ValueError, match="has no format_ids configured"):
            convert_product_model_to_schema(product_model)

        # Also test with None (another way format_ids might be missing)
        product_model.effective_format_ids = None
        with pytest.raises(ValueError, match="has no format_ids configured"):
            convert_product_model_to_schema(product_model)

    def test_adcp_create_media_buy_request(self):
        """Test AdCP create_media_buy request structure."""
        start_time = datetime.now(UTC) + timedelta(days=1)
        end_time = datetime.now(UTC) + timedelta(days=30)

        # Per AdCP spec, packages is required and budget is at package level
        # In adcp 3.6.0, brand_manifest is replaced by brand (BrandReference with domain field)
        request = CreateMediaBuyRequest(
            brand={"domain": "nike.com"},  # Required in adcp 3.6.0 (was brand_manifest)
            # Required per AdCP spec
            packages=[
                {"product_id": "product_1", "budget": 2500.0, "pricing_option_id": "opt_1"},
                {"product_id": "product_2", "budget": 2500.0, "pricing_option_id": "opt_2"},
            ],
            start_time=start_time,
            end_time=end_time,
            po_number="PO-12345",  # Optional per spec
        )

        # Verify AdCP requirements
        assert len(request.get_product_ids()) == 2
        assert request.get_total_budget() == 5000.0
        assert request.flight_end_date > request.flight_start_date

        # Verify spec-compliant fields are present
        assert request.brand is not None
        # buyer_ref removed from CreateMediaBuyRequest in adcp 3.12
        assert len(request.packages) == 2

    def test_format_schema_compliance(self):
        """Test that Format schema matches AdCP specifications."""
        from tests.helpers.adcp_factories import create_test_format_id

        # Create AdCP-compliant Format directly (only fields supported by adcp library)
        format_obj = Format(
            format_id=create_test_format_id("native_feed"),
            name="Native Feed Ad",
        )

        # AdCP format requirements (new spec structure)
        assert format_obj.format_id is not None
        # format_obj.type is an enum, check its value
        # type removed from Format in adcp 3.12
        assert format_obj.name == "Native Feed Ad"

    def test_field_mapping_consistency(self):
        """Test that field names are consistent between models and schemas."""
        # These fields should map correctly
        model_to_schema_mapping = {
            # Model field -> Schema field (AdCP spec compliant - no price_guidance)
            "product_id": "product_id",
            "name": "name",
            "description": "description",
            "delivery_type": "delivery_type",  # Must be "guaranteed" or "non_guaranteed"
            "format_ids": "format_ids",
            "is_custom": "is_custom",
            "expires_at": "expires_at",
        }

        # Create test data
        model = ProductModel(
            tenant_id="test",
            product_id="test_mapping",
            name="Test",
            description="Test product",
            format_ids=[],
            targeting_template={},
            delivery_type="guaranteed",
            is_custom=False,
            expires_at=None,
            countries=["US"],
            implementation_config=None,
        )

        # Verify each field maps correctly
        for model_field, schema_field in model_to_schema_mapping.items():
            assert hasattr(model, model_field), f"Model missing field: {model_field}"
            assert schema_field in ProductSchema.model_fields, f"Schema missing field: {schema_field}"

    def test_adcp_delivery_type_values(self):
        """Test that delivery_type uses AdCP-compliant values."""
        from tests.helpers.adcp_factories import (
            create_test_cpm_pricing_option,
            create_test_publisher_properties_by_tag,
        )

        # AdCP specifies exactly these two values
        valid_delivery_types = ["guaranteed", "non_guaranteed"]

        # Test valid values
        for delivery_type in valid_delivery_types:
            product = ProductSchema(
                product_id="test",
                name="Test",
                description="Test",
                format_ids=[],
                delivery_type=delivery_type,
                delivery_measurement={
                    "provider": "test_provider",
                    "notes": "Test measurement",
                },  # Required per AdCP spec
                publisher_properties=[create_test_publisher_properties_by_tag(publisher_domain="test.com")],
                pricing_options=[
                    create_test_cpm_pricing_option(
                        pricing_option_id="cpm_usd_fixed",
                        currency="USD",
                        rate=10.0,
                    )
                ],
            )
            # delivery_type is an enum, check its value
            assert product.delivery_type.value in valid_delivery_types

        # Invalid values should fail
        with pytest.raises(ValueError):
            ProductSchema(
                product_id="test",
                name="Test",
                description="Test",
                format_ids=[],
                delivery_type="programmatic",  # Not AdCP compliant
                delivery_measurement={
                    "provider": "test_provider",
                    "notes": "Test measurement",
                },  # Required per AdCP spec
                publisher_properties=[create_test_publisher_properties_by_tag(publisher_domain="test.com")],
                pricing_options=[
                    create_test_cpm_pricing_option(
                        pricing_option_id="cpm_usd_fixed",
                        currency="USD",
                        rate=10.0,
                    )
                ],
            )

    def test_adcp_response_excludes_internal_fields(self):
        """Test that AdCP responses don't expose internal fields."""
        from tests.helpers.adcp_factories import (
            create_test_cpm_pricing_option,
            create_test_publisher_properties_by_tag,
        )

        products = [
            ProductSchema(
                product_id="test",
                name="Test Product",
                description="Test",
                format_ids=[],
                delivery_type="guaranteed",
                delivery_measurement={
                    "provider": "test_provider",
                    "notes": "Test measurement",
                },  # Required per AdCP spec
                implementation_config={"internal": "data"},  # Should be excluded
                publisher_properties=[create_test_publisher_properties_by_tag(publisher_domain="test.com")],
                pricing_options=[
                    create_test_cpm_pricing_option(
                        pricing_option_id="cpm_usd_fixed",
                        currency="USD",
                        rate=10.0,
                    )
                ],
            )
        ]

        response = GetProductsResponse(products=products)
        response_dict = response.model_dump()

        # Verify implementation_config is excluded from response
        for product in response_dict["products"]:
            assert "implementation_config" not in product, "Internal config should not be in AdCP response"

    def test_adcp_signal_support(self):
        """Test AdCP v2.4 signal support in Targeting schema.

        Note: CreateMediaBuyRequest no longer has targeting_overlay (not in spec).
        Targeting is specified at the package level. This test verifies the
        Targeting schema itself supports signals.
        """
        from src.core.schemas import Targeting

        # Test Targeting schema directly (not CreateMediaBuyRequest)
        targeting = Targeting(
            signals=[
                "sports_enthusiasts",
                "auto_intenders_q1_2025",
                "high_income_households",
            ],
            key_value_pairs={"custom_audience_1": "abc123", "lookalike_model": "xyz789"},
        )

        # Verify signals are supported in Targeting schema
        assert hasattr(targeting, "signals")
        assert targeting.signals == [
            "sports_enthusiasts",
            "auto_intenders_q1_2025",
            "high_income_households",
        ]
        assert targeting.key_value_pairs is not None

    def test_creative_adcp_compliance(self):
        """Test that Creative model complies with AdCP listing Creative schema.

        The Creative extends the listing Creative (list_creatives_response.Creative):
        - Public model_dump() contains: creative_id, format_id, name, status,
          created_date, updated_date, assets, tags (listing schema fields)
        - Internal fields (principal_id) are excluded from model_dump()
          but available via model_dump_internal()
        """

        # Test creating a Creative with all fields (some public, some internal)
        creative = Creative(
            creative_id="test_creative_123",
            name="Test AdCP Creative",
            format_id=FormatId(agent_url="https://creative.adcontextprotocol.org", id="display_300x250"),
            assets={
                "banner_image": {
                    "url": "https://example.com/creative.jpg",
                    "width": 300,
                    "height": 250,
                    "asset_type": "image",
                },
                "click_url": {"url": "https://example.com/landing", "url_type": "clickthrough"},
            },
            tags=["display", "banner"],
            # Internal fields (optional, added by sales agent)
            principal_id="test_principal",
            created_date=datetime.now(tz=UTC),
            updated_date=datetime.now(tz=UTC),
            status="approved",
        )

        # Test AdCP-compliant model_dump (external response - listing schema fields)
        adcp_response = creative.model_dump()

        # Verify listing Creative public fields are present in model_dump()
        listing_public_fields = ["creative_id", "format_id", "name", "status", "created_date", "updated_date"]
        for field in listing_public_fields:
            assert field in adcp_response, f"Listing field '{field}' missing from response"
            assert adcp_response[field] is not None, f"Listing field '{field}' is None"

        # Verify internal-only fields are excluded from model_dump
        assert "principal_id" not in adcp_response, "Internal field 'principal_id' exposed in AdCP response"

        # Verify delivery-only fields are NOT present (we extend listing, not delivery)
        for field in ["variants", "variant_count", "totals", "media_buy_id"]:
            assert field not in adcp_response, f"Delivery field '{field}' should not be in listing response"

        # Verify format_id is FormatId object
        assert isinstance(adcp_response["format_id"], dict), "format_id should be FormatId object (as dict)"
        assert adcp_response["format_id"]["id"] == "display_300x250", "Format ID should be display_300x250"
        assert "agent_url" in adcp_response["format_id"], "format_id should have agent_url"

        # Test internal model_dump includes all fields
        internal_response = creative.model_dump_internal()
        assert "principal_id" in internal_response, "principal_id missing from internal response"
        assert "status" in internal_response, "status missing from internal response"

        # Verify internal response has principal_id that external doesn't
        internal_only_fields = set(internal_response.keys()) - set(adcp_response.keys())
        assert "principal_id" in internal_only_fields, "principal_id should be internal-only"

    def test_signal_adcp_compliance(self):
        """Test that Signal model complies with AdCP get-signals-response schema."""
        # Create signal with all required AdCP fields
        deployment = SignalDeployment(
            platform="google_ad_manager",
            account="123456789",
            is_live=True,
            type="platform",
            scope="account-specific",
            decisioning_platform_segment_id="gam_segment_123",
            estimated_activation_duration_minutes=0,
        )

        signal = Signal(
            signal_agent_segment_id="signal_auto_intenders_q1_2025",
            name="Auto Intenders Q1 2025",
            description="Consumers showing purchase intent for automotive products in Q1 2025",
            signal_type="marketplace",
            data_provider="Acme Data Solutions",
            coverage_percentage=85.5,
            deployments=[deployment],
            pricing_options=[
                {"pricing_option_id": "cpm_usd", "cpm": 2.50, "currency": "USD", "model": "cpm"},
            ],
            tenant_id="test_tenant",
            created_at=datetime.now(),
            updated_at=datetime.now(),
            metadata={"category": "automotive", "confidence": 0.92},
        )

        # Test AdCP-compliant model_dump (external response)
        adcp_response = signal.model_dump()

        # Verify required AdCP fields are present
        adcp_required_fields = [
            "signal_agent_segment_id",
            "name",
            "description",
            "signal_type",
            "data_provider",
            "coverage_percentage",
            "deployments",
            "pricing_options",
        ]
        for field in adcp_required_fields:
            assert field in adcp_response, f"Required AdCP field '{field}' missing from response"
            assert adcp_response[field] is not None, f"Required AdCP field '{field}' is None"

        # Verify internal fields are excluded from AdCP response
        internal_fields = ["tenant_id", "created_at", "updated_at", "metadata"]
        for field in internal_fields:
            assert field not in adcp_response, f"Internal field '{field}' exposed in AdCP response"

        # Verify AdCP-specific requirements
        assert adcp_response["signal_type"] in ["marketplace", "custom", "owned"], "signal_type must be valid enum"
        assert 0 <= adcp_response["coverage_percentage"] <= 100, "coverage_percentage must be 0-100"

        # Verify deployments array structure
        assert isinstance(adcp_response["deployments"], list), "deployments must be array"
        assert len(adcp_response["deployments"]) > 0, "deployments array must not be empty"
        deployment_obj = adcp_response["deployments"][0]
        required_deployment_fields = ["platform", "is_live", "type"]
        for field in required_deployment_fields:
            assert field in deployment_obj, f"Required deployment field '{field}' missing"
        # scope is an internal field (exclude=True), should not appear in AdCP response
        assert "scope" not in deployment_obj, "Internal field 'scope' exposed in AdCP response"

        # Verify pricing_options structure (adcp 3.9: pricing details in pricing_options)
        assert "pricing_options" in adcp_response, "pricing_options must be present"
        assert isinstance(adcp_response["pricing_options"], list), "pricing_options must be array"
        assert len(adcp_response["pricing_options"]) >= 1, "pricing_options must have at least one entry"

        # Verify the primary ID field works correctly
        # adcp 3.6.0: signal_id is now a separate field in the library (optional, distinct from signal_agent_segment_id)
        # The backward compat @property is superseded by the library field
        assert signal.signal_agent_segment_id == "signal_auto_intenders_q1_2025", "Primary ID should work"
        assert signal.signal_type == "marketplace", "signal_type field should work"

        # Test internal model_dump includes all fields
        internal_response = signal.model_dump_internal()
        for field in internal_fields:
            assert field in internal_response, f"Internal field '{field}' missing from internal response"

        # Verify field count expectations (flexible to allow AdCP spec evolution)
        assert len(adcp_response) >= 8, f"AdCP response should have at least 8 core fields, got {len(adcp_response)}"
        assert len(internal_response) >= len(adcp_response), (
            "Internal response should have at least as many fields as external response"
        )

        # Verify internal response has more fields than external (due to internal fields)
        internal_only_fields = set(internal_response.keys()) - set(adcp_response.keys())
        assert len(internal_only_fields) >= 3, (
            f"Expected at least 3 internal-only fields, got {len(internal_only_fields)}"
        )

    def test_package_adcp_compliance(self):
        """Test that Package model complies with AdCP package schema."""
        # Create package with all required AdCP fields and optional fields
        # Note: Package is response schema - has package_id, paused (adcp 2.12.0+)
        # product_id is optional per adcp library (not products plural)
        package = Package(
            package_id="pkg_test_123",
            paused=False,  # Changed from status="active" in adcp 2.12.0
            product_id="product_xyz",  # singular, not plural
            impressions=50000,
            creative_assignments=[
                {"creative_id": "creative_1", "weight": 70},
                {"creative_id": "creative_2", "weight": 30},
            ],
            tenant_id="test_tenant",
            media_buy_id="mb_12345",
            created_at=datetime.now(),
            updated_at=datetime.now(),
            metadata={"campaign_type": "awareness", "priority": "high"},
        )

        # Test AdCP-compliant model_dump (external response)
        adcp_response = package.model_dump()

        # Verify required AdCP fields are present
        adcp_required_fields = ["package_id"]  # paused is optional
        for field in adcp_required_fields:
            assert field in adcp_response, f"Required AdCP field '{field}' missing from response"
            assert adcp_response[field] is not None, f"Required AdCP field '{field}' is None"

        # Verify AdCP optional fields that were set are present
        # Per AdCP spec, optional fields should only appear in response if they have values
        # (Pydantic's default behavior is exclude_none=True)
        # Per adcp library Package schema (response schema, not request)
        # Test with fields that were actually set in the Package object above
        expected_optional_fields = {
            "product_id",  # We set this
            "impressions",  # We set this
            "creative_assignments",  # We set this
        }
        for field in expected_optional_fields:
            assert field in adcp_response, f"Expected optional field '{field}' missing from response"

        # Verify fields that weren't set are NOT in response (Pydantic excludes None by default)
        # These optional fields exist in the schema but weren't set, so shouldn't appear:
        # budget, targeting_overlay, pricing_option_id, format_ids_to_provide, bid_price, pacing

        # Verify internal fields are excluded from AdCP response
        internal_fields = ["tenant_id", "media_buy_id", "created_at", "updated_at", "metadata"]
        for field in internal_fields:
            assert field not in adcp_response, f"Internal field '{field}' exposed in AdCP response"

        # Verify AdCP-specific requirements
        # paused is a bool field in adcp 2.12.0+
        if "paused" in adcp_response:
            assert isinstance(adcp_response["paused"], bool), "paused must be boolean"
        if adcp_response.get("impressions") is not None:
            assert adcp_response["impressions"] >= 0, "impressions must be non-negative"

        # Verify creative_assignments structure if present
        if adcp_response.get("creative_assignments"):
            assert isinstance(adcp_response["creative_assignments"], list), "creative_assignments must be array"
            for assignment in adcp_response["creative_assignments"]:
                assert isinstance(assignment, dict), "each creative assignment must be object"

        # Test internal model_dump includes all fields
        internal_response = package.model_dump_internal()
        for field in internal_fields:
            assert field in internal_response, f"Internal field '{field}' missing from internal response"

        # Verify field count expectations (flexible to allow AdCP spec evolution)
        # Package has 1 required field (package_id) + any optional fields that are set
        # We set several optional fields above, so expect at least 1 field
        assert len(adcp_response) >= 1, f"AdCP response should have at least required fields, got {len(adcp_response)}"
        assert len(internal_response) >= len(adcp_response), (
            "Internal response should have at least as many fields as external response"
        )

        # Verify internal response has more fields than external (due to internal fields)
        internal_only_fields = set(internal_response.keys()) - set(adcp_response.keys())
        assert len(internal_only_fields) >= 3, (
            f"Expected at least 3 internal-only fields, got {len(internal_only_fields)}"
        )

    def test_package_ignores_invalid_fields(self):
        """Test that Package schema ignores fields that don't exist in AdCP spec.

        As of adcp 2.18.0, library schemas use extra="allow" for forward compatibility.
        Unknown fields are accepted but not stored on the model (ignored).
        This prevents breaking changes when new protocol fields are added.
        """
        # Extra fields should be accepted but ignored (not raise ValidationError)
        # 'status' - removed in AdCP 2.12.0, use 'paused' instead
        pkg = Package(package_id="test", status="active")
        assert not hasattr(pkg, "status") or pkg.model_extra.get("status") == "active"

        # 'format_ids' - PackageRequest field, use 'format_ids_to_provide' in Package
        pkg = Package(package_id="test", format_ids=[{"agent_url": "https://example.com", "id": "banner"}])
        assert pkg.package_id == "test"

        # 'creative_ids' - PackageRequest field, use 'creative_assignments' in Package
        pkg = Package(package_id="test", creative_ids=["creative_1"])
        assert pkg.package_id == "test"

        # 'creatives' - PackageRequest field, use 'creative_assignments' in Package
        pkg = Package(package_id="test", creatives=[{"creative_id": "c1"}])
        assert pkg.package_id == "test"

        # 'products' (plural) - incorrect field name
        pkg = Package(package_id="test", products=["prod_1"])
        assert pkg.package_id == "test"

    def test_targeting_adcp_compliance(self):
        """Test that Targeting model complies with AdCP targeting schema."""
        from adcp.types import TargetingOverlay

        # Create targeting with v3 structured geo fields and internal fields
        targeting = Targeting(
            geo_countries=["US", "CA"],
            geo_regions=["US-CA", "US-NY"],
            geo_metros=[{"system": "nielsen_dma", "values": ["803", "501"]}],
            geo_postal_areas=[{"system": "us_zip", "values": ["10001", "90210"]}],
            audiences_any_of=["segment_1", "segment_2"],
            signals=["auto_intenders_q1_2025", "sports_enthusiasts"],
            device_type_any_of=["desktop", "mobile", "tablet"],
            os_any_of=["windows", "macos", "ios", "android"],
            browser_any_of=["chrome", "firefox", "safari"],
            key_value_pairs={"aee_segment": "high_value", "aee_score": "0.85"},  # Managed-only
            tenant_id="test_tenant",  # Internal
            created_at=datetime.now(),  # Internal
            updated_at=datetime.now(),  # Internal
            metadata={"campaign_type": "awareness"},  # Internal
        )

        # Verify isinstance — Targeting IS a TargetingOverlay
        assert isinstance(targeting, TargetingOverlay)

        # Test AdCP-compliant model_dump (external response)
        adcp_response = targeting.model_dump()

        # Verify v3 structured geo fields are present
        adcp_optional_fields = [
            "geo_countries",
            "geo_regions",
            "geo_metros",
            "geo_postal_areas",
            "audiences_any_of",
            "signals",
            "device_type_any_of",
            "os_any_of",
            "browser_any_of",
        ]
        for field in adcp_optional_fields:
            if getattr(targeting, field) is not None:
                assert field in adcp_response, f"AdCP optional field '{field}' missing from response"

        # Verify managed and internal fields are excluded from AdCP response
        managed_internal_fields = [
            "key_value_pairs",  # Managed-only field
            "tenant_id",
            "created_at",
            "updated_at",
            "metadata",  # Internal fields
        ]
        for field in managed_internal_fields:
            assert field not in adcp_response, f"Managed/internal field '{field}' exposed in AdCP response"

        # Verify v3 geo structure
        if adcp_response.get("geo_countries"):
            for country in adcp_response["geo_countries"]:
                # GeoCountry serializes as a plain string (RootModel)
                assert isinstance(country, str) and len(country) == 2, "Country codes must be 2-letter ISO codes"

        if adcp_response.get("device_type_any_of"):
            valid_devices = ["desktop", "mobile", "tablet", "connected_tv", "smart_speaker"]
            for device in adcp_response["device_type_any_of"]:
                assert device in valid_devices, f"Invalid device type: {device}"

        if adcp_response.get("os_any_of"):
            valid_os = ["windows", "macos", "ios", "android", "linux", "roku", "tvos", "other"]
            for os in adcp_response["os_any_of"]:
                assert os in valid_os, f"Invalid OS: {os}"

        if adcp_response.get("browser_any_of"):
            valid_browsers = ["chrome", "firefox", "safari", "edge", "other"]
            for browser in adcp_response["browser_any_of"]:
                assert browser in valid_browsers, f"Invalid browser: {browser}"

        # Test internal model_dump includes all fields
        internal_response = targeting.model_dump_internal()
        for field in managed_internal_fields:
            assert field in internal_response, f"Managed/internal field '{field}' missing from internal response"

        # Test managed fields are accessible internally
        assert internal_response["key_value_pairs"]["aee_segment"] == "high_value", (
            "Managed field should be in internal response"
        )

        # Verify field count expectations (flexible - targeting has many optional fields)
        assert len(adcp_response) >= 9, f"AdCP response should have at least 9 fields, got {len(adcp_response)}"
        assert len(internal_response) >= len(adcp_response), (
            "Internal response should have at least as many fields as external response"
        )

        # Verify internal response has more fields than external (due to managed/internal fields)
        internal_only_fields = set(internal_response.keys()) - set(adcp_response.keys())
        assert len(internal_only_fields) >= 4, (
            f"Expected at least 4 internal/managed-only fields, got {len(internal_only_fields)}"
        )

    def test_budget_adcp_compliance(self):
        """Test that Budget model complies with AdCP budget schema."""
        budget = Budget(total=5000.0, currency="USD", daily_cap=250.0, pacing="even")

        # Test model_dump (Budget doesn't have internal fields, so standard dump should be fine)
        adcp_response = budget.model_dump()

        # Verify required AdCP fields are present
        adcp_required_fields = ["total", "currency"]
        for field in adcp_required_fields:
            assert field in adcp_response, f"Required AdCP field '{field}' missing from response"
            assert adcp_response[field] is not None, f"Required AdCP field '{field}' is None"

        # Verify AdCP optional fields are present
        adcp_optional_fields = ["daily_cap", "pacing"]
        for field in adcp_optional_fields:
            assert field in adcp_response, f"AdCP optional field '{field}' missing from response"

        # Verify AdCP-specific requirements
        assert adcp_response["total"] > 0, "Budget total must be positive"
        assert len(adcp_response["currency"]) == 3, "Currency must be 3-letter ISO code"
        assert adcp_response["pacing"] in ["even", "asap", "daily_budget"], "Invalid pacing value"

        # Verify field count: 4 fields present (auto_pause_on_budget_exhaustion=None excluded by exclude_none)
        assert len(adcp_response) == 4, f"Budget response should have exactly 4 fields, got {len(adcp_response)}"

    def test_measurement_adcp_compliance(self):
        """Test that Measurement model complies with AdCP measurement schema."""
        measurement = Measurement(
            type="incremental_sales_lift",
            attribution="deterministic_purchase",
            window={"interval": 30, "unit": "days"},
            reporting="daily",
        )

        # Test model_dump (Measurement doesn't have internal fields)
        adcp_response = measurement.model_dump()

        # Verify required AdCP fields are present
        adcp_required_fields = ["type", "attribution", "reporting"]
        for field in adcp_required_fields:
            assert field in adcp_response, f"Required AdCP field '{field}' missing from response"
            assert adcp_response[field] is not None, f"Required AdCP field '{field}' is None"

        # Verify AdCP optional fields are present
        adcp_optional_fields = ["window"]
        for field in adcp_optional_fields:
            assert field in adcp_response, f"AdCP optional field '{field}' missing from response"

        # Verify field count (Measurement is simple, count should be stable)
        assert len(adcp_response) == 4, f"Measurement response should have exactly 4 fields, got {len(adcp_response)}"

    def test_creative_policy_adcp_compliance(self):
        """Test that CreativePolicy model complies with AdCP creative-policy schema."""
        policy = CreativePolicy(co_branding="required", landing_page="retailer_site_only", templates_available=True)

        # Test model_dump with mode="json" (library enums serialize to strings)
        adcp_response = policy.model_dump(mode="json")

        # Verify required AdCP fields are present
        adcp_required_fields = ["co_branding", "landing_page", "templates_available"]
        for field in adcp_required_fields:
            assert field in adcp_response, f"Required AdCP field '{field}' missing from response"
            assert adcp_response[field] is not None, f"Required AdCP field '{field}' is None"

        # Verify AdCP-specific requirements
        assert adcp_response["co_branding"] in ["required", "optional", "none"], "Invalid co_branding value"
        assert adcp_response["landing_page"] in [
            "any",
            "retailer_site_only",
            "must_include_retailer",
        ], "Invalid landing_page value"
        assert isinstance(adcp_response["templates_available"], bool), "templates_available must be boolean"

        # Verify field count (CreativePolicy is simple, count should be stable)
        assert len(adcp_response) == 3, (
            f"CreativePolicy response should have exactly 3 fields, got {len(adcp_response)}"
        )

    def test_creative_status_adcp_compliance(self):
        """Test that CreativeApprovalStatus model complies with AdCP creative-status schema."""
        status = CreativeApprovalStatus(
            creative_id="creative_123",
            status="approved",
            detail="Creative approved for all placements",
            estimated_approval_time=datetime.now() + timedelta(hours=1),
        )

        # Test model_dump (CreativeApprovalStatus doesn't have internal fields currently)
        adcp_response = status.model_dump()

        # Verify required AdCP fields are present
        adcp_required_fields = ["creative_id", "status", "detail"]
        for field in adcp_required_fields:
            assert field in adcp_response, f"Required AdCP field '{field}' missing from response"
            assert adcp_response[field] is not None, f"Required AdCP field '{field}' is None"

        # Verify AdCP optional fields are present
        adcp_optional_fields = ["estimated_approval_time", "suggested_adaptations"]
        for field in adcp_optional_fields:
            assert field in adcp_response, f"AdCP optional field '{field}' missing from response"

        # Verify AdCP-specific requirements
        valid_statuses = ["pending_review", "approved", "rejected", "adaptation_required"]
        assert adcp_response["status"] in valid_statuses, f"Invalid status value: {adcp_response['status']}"

        # Verify field count (flexible - optional fields vary)
        assert len(adcp_response) >= 3, (
            f"CreativeStatus response should have at least 3 core fields, got {len(adcp_response)}"
        )

    def test_creative_assignment_adcp_compliance(self):
        """Test that CreativeAssignment model complies with AdCP creative-assignment schema."""
        assignment = CreativeAssignment(
            assignment_id="assign_123",
            media_buy_id="mb_456",
            package_id="pkg_789",
            creative_id="creative_abc",
            weight=75,
            percentage_goal=60.0,
            rotation_type="weighted",
            override_click_url="https://example.com/override",
            override_start_date=datetime.now(UTC),
            override_end_date=datetime.now(UTC) + timedelta(days=7),
        )

        # Test model_dump (CreativeAssignment may have internal fields)
        adcp_response = assignment.model_dump()

        # Verify required AdCP fields are present
        adcp_required_fields = ["assignment_id", "media_buy_id", "package_id", "creative_id"]
        for field in adcp_required_fields:
            assert field in adcp_response, f"Required AdCP field '{field}' missing from response"
            assert adcp_response[field] is not None, f"Required AdCP field '{field}' is None"

        # Verify AdCP optional fields are present
        adcp_optional_fields = [
            "weight",
            "percentage_goal",
            "rotation_type",
            "override_click_url",
            "override_start_date",
            "override_end_date",
            "targeting_overlay",
        ]
        for field in adcp_optional_fields:
            if hasattr(assignment, field) and getattr(assignment, field) is not None:
                assert field in adcp_response, f"AdCP optional field '{field}' missing from response"

        # Verify AdCP-specific requirements
        if adcp_response.get("rotation_type"):
            valid_rotations = ["weighted", "sequential", "even"]
            assert adcp_response["rotation_type"] in valid_rotations, (
                f"Invalid rotation_type: {adcp_response['rotation_type']}"
            )

        if adcp_response.get("weight") is not None:
            assert adcp_response["weight"] >= 0, "Weight must be non-negative"

        if adcp_response.get("percentage_goal") is not None:
            assert 0 <= adcp_response["percentage_goal"] <= 100, "Percentage goal must be 0-100"

        # Verify field count (flexible - optional fields vary)
        assert len(adcp_response) >= 4, (
            f"CreativeAssignment response should have at least 4 core fields, got {len(adcp_response)}"
        )

    def test_sync_creatives_request_adcp_compliance(self):
        """Test that SyncCreativesRequest model complies with AdCP v2.4 sync-creatives schema."""
        # Create Creative objects with AdCP v1 spec-compliant format
        creative = Creative(
            creative_id="creative_123",
            variants=[],
            name="Test Creative",
            format_id=FormatId(agent_url="https://creative.adcontextprotocol.org", id="display_300x250"),
            assets={
                "banner_image": {
                    "url": "https://example.com/creative.jpg",
                    "width": 300,
                    "height": 250,
                    "asset_type": "image",
                },
                "click_url": {"url": "https://example.com/click", "url_type": "clickthrough"},
            },
            tags=["sports", "premium"],
            # Internal fields (added by sales agent during processing)
            principal_id="principal_456",
            created_date=datetime.now(),
            updated_date=datetime.now(),
        )

        # Test with spec-compliant fields only (adcp 3.9)
        from adcp.types.generated_poc.creative.sync_creatives_request import Assignment

        request = SyncCreativesRequest(
            creatives=[creative],
            assignments=[
                Assignment(creative_id="creative_123", package_id="pkg_1"),
                Assignment(creative_id="creative_123", package_id="pkg_2"),
            ],
            # creative_ids: AdCP 2.5 replaces the deprecated patch parameter
            delete_missing=False,
            dry_run=False,
            validation_mode="strict",
        )

        # Test model_dump (SyncCreativesRequest doesn't have internal fields)
        adcp_response = request.model_dump()

        # Verify required AdCP fields are present
        adcp_required_fields = ["creatives"]
        for field in adcp_required_fields:
            assert field in adcp_response, f"Required AdCP field '{field}' missing from response"
            assert adcp_response[field] is not None, f"Required AdCP field '{field}' is None"

        # Verify AdCP v2.5 optional fields - some may be excluded when None
        # Note: 'patch' was removed in AdCP 2.5, replaced by 'creative_ids'
        # Fields with default values should be present, fields with None defaults may be excluded
        adcp_fields_with_defaults = ["delete_missing", "dry_run", "validation_mode"]
        for field in adcp_fields_with_defaults:
            assert field in adcp_response, f"AdCP field '{field}' missing from response"

        # Optional fields that may be None: creative_ids, assignments, context, push_notification_config
        # These are correctly excluded from output when None

        # Verify non-spec fields are NOT present
        non_spec_fields = ["media_buy_id", "buyer_ref", "assign_to_packages", "upsert", "patch"]
        for field in non_spec_fields:
            assert field not in adcp_response, f"Non-spec field '{field}' should not be in response"

        # Verify creatives array structure
        assert isinstance(adcp_response["creatives"], list), "Creatives must be an array"
        assert len(adcp_response["creatives"]) > 0, "Creatives array must not be empty"

        # Test creative object structure
        # Creative extends listing Creative: model_dump() contains listing fields
        # (creative_id, format_id, name, status, created_date, updated_date, assets, tags)
        # Only principal_id is internal/excluded
        creative_obj = adcp_response["creatives"][0]
        creative_public_fields = ["creative_id", "format_id", "name", "status", "created_date", "updated_date"]
        for field in creative_public_fields:
            assert field in creative_obj, f"Creative public field '{field}' missing"
            assert creative_obj[field] is not None, f"Creative public field '{field}' is None"

        # Delivery-only fields should NOT be present
        for field in ["variants", "variant_count", "totals", "media_buy_id"]:
            assert field not in creative_obj, f"Delivery field '{field}' should not be in listing response"

        # Internal fields should NOT be in the response
        assert "principal_id" not in creative_obj, "Internal field 'principal_id' exposed in response"

        # Verify assignments structure (adcp 3.9: list of Assignment objects)
        if adcp_response.get("assignments"):
            assert isinstance(adcp_response["assignments"], list), "Assignments must be a list"
            for assignment in adcp_response["assignments"]:
                assert "creative_id" in assignment, "Assignment must have creative_id"
                assert "package_id" in assignment, "Assignment must have package_id"

        # Verify field count (flexible due to optional fields)
        assert len(adcp_response) >= 1, f"SyncCreativesRequest should have at least 1 field, got {len(adcp_response)}"

    def test_sync_creatives_response_adcp_compliance(self):
        """Test that SyncCreativesResponse model complies with AdCP sync-creatives response schema."""
        from src.core.schemas import SyncCreativeResult

        # Build AdCP-compliant response with domain fields only (per AdCP PR #113)
        # Protocol fields (message, status, task_id, context_id) added by transport layer
        response = SyncCreativesResponse(
            creatives=[
                SyncCreativeResult(
                    creative_id="creative_123",
                    action="created",
                    status="approved",
                ),
                SyncCreativeResult(
                    creative_id="creative_456",
                    action="updated",
                    status="pending",
                    changes=["url", "name"],
                ),
                SyncCreativeResult(
                    creative_id="creative_789",
                    action="failed",
                    errors=["Invalid format"],
                ),
            ],
        )

        # Test model_dump
        adcp_response = response.model_dump()

        # Verify AdCP domain fields are present (per AdCP PR #113 and official spec)
        # Protocol fields (adcp_version, message, status, task_id, context_id) added by transport layer

        # Required field per official spec
        assert "creatives" in adcp_response, "SyncCreativesResponse must have 'creatives' field"
        assert isinstance(adcp_response["creatives"], list), "'creatives' must be a list"

        # Verify creatives structure
        if adcp_response["creatives"]:
            result = adcp_response["creatives"][0]
            assert "creative_id" in result, "Result must have creative_id"
            assert "action" in result, "Result must have action"

        # Optional fields per official spec
        if "dry_run" in adcp_response and adcp_response["dry_run"] is not None:
            assert isinstance(adcp_response["dry_run"], bool), "dry_run must be boolean"

    def test_list_creatives_request_adcp_compliance(self):
        """Test that ListCreativesRequest model complies with AdCP list-creatives schema.

        Now extends library ListCreativesRequest directly - all fields are spec-compliant.
        """
        from adcp.types import CreativeFilters as LibraryCreativeFilters

        # adcp 3.6.0: Request pagination uses PaginationRequest (cursor + max_results)
        from adcp.types.generated_poc.core.pagination_request import PaginationRequest
        from adcp.types.generated_poc.creative.list_creatives_request import Sort as LibrarySort

        from src.core.schemas import ListCreativesRequest

        # Create request using spec-compliant structured objects
        # adcp 3.10: include_performance and include_sub_assets removed from spec;
        # include_assignments, include_snapshot, include_items, include_variables remain
        request = ListCreativesRequest(
            filters=LibraryCreativeFilters(
                status="approved",
                format="display_300x250",
                tags=["sports", "premium"],
                created_after=datetime.now(UTC) - timedelta(days=30),
                created_before=datetime.now(UTC),
                media_buy_ids=["mb_123"],
                buyer_refs=["buyer_456"],
            ),
            pagination=PaginationRequest(max_results=50),  # Request pagination uses cursor/max_results
            sort=LibrarySort(field="created_date", direction="desc"),  # type: ignore[arg-type]
            include_assignments=True,
        )

        # Test model_dump - should output AdCP-compliant structured fields
        adcp_response = request.model_dump(exclude_none=False)

        # Verify structured AdCP fields are present
        assert "filters" in adcp_response, "AdCP structured 'filters' field must be present"
        assert "sort" in adcp_response, "AdCP structured 'sort' field must be present"
        assert "pagination" in adcp_response, "AdCP structured 'pagination' field must be present"

        # Verify filters structure
        filters = adcp_response["filters"]
        # V3: Status is serialized as string (not enum) in model_dump
        status = filters["status"]
        status_value = status.value if hasattr(status, "value") else status
        assert status_value == "approved", "filters.status should match input"
        assert filters["format"] == "display_300x250", "filters.format should match input"
        assert filters["tags"] == ["sports", "premium"], "filters.tags should match input"
        assert "created_after" in filters, "filters.created_after should be present"
        assert "created_before" in filters, "filters.created_before should be present"
        assert filters["media_buy_ids"] == ["mb_123"], "filters.media_buy_ids should match input"
        assert filters["buyer_refs"] == ["buyer_456"], "filters.buyer_refs should match input"

        # Verify pagination structure (adcp 3.6.0: cursor-based pagination)
        pagination = adcp_response["pagination"]
        assert pagination["max_results"] == 50, "pagination.max_results should match input"

        # Verify sort structure
        sort = adcp_response["sort"]
        # V3: Enums serialized as strings in model_dump
        field_val = sort["field"].value if hasattr(sort["field"], "value") else sort["field"]
        direction_val = sort["direction"].value if hasattr(sort["direction"], "value") else sort["direction"]
        assert field_val == "created_date", "sort.field should match input"
        assert direction_val == "desc", "sort.direction should match input"

        # Fields WITH defaults should be present (adcp 3.10 spec fields)
        assert "include_assignments" in adcp_response, "Field with default should be present"
        assert adcp_response["include_assignments"] is True, "Default value should match"

        # Verify all spec fields are present (per adcp 3.10 library schema)
        spec_fields = {
            "context",
            "ext",
            "fields",
            "filters",
            "include_assignments",
            "include_items",
            "include_snapshot",
            "include_variables",
            "pagination",
            "sort",
        }
        assert set(adcp_response.keys()) == spec_fields, f"Fields should match spec: {set(adcp_response.keys())}"

    def test_list_creatives_response_adcp_compliance(self):
        """Test that ListCreativesResponse model complies with AdCP list-creatives response schema."""
        creative1 = Creative(
            creative_id="creative_123",
            variants=[],
            name="Test Creative 1",
            format_id=FormatId(agent_url="https://creative.adcontextprotocol.org", id="display_300x250"),
            assets={
                "banner_image": {
                    "url": "https://example.com/creative1.jpg",
                    "width": 300,
                    "height": 250,
                    "asset_type": "image",
                }
            },
            tags=["sports"],
            # Internal fields
            principal_id="principal_1",
            status="approved",
            created_date=datetime.now(),
            updated_date=datetime.now(),
        )

        creative2 = Creative(
            creative_id="creative_456",
            variants=[],
            name="Test Creative 2",
            format_id=FormatId(agent_url="https://creative.adcontextprotocol.org", id="video_1280x720"),
            assets={
                "video_file": {
                    "url": "https://example.com/creative2.mp4",
                    "width": 1280,
                    "height": 720,
                    "asset_type": "video",
                }
            },
            tags=["premium"],
            # Internal fields
            principal_id="principal_1",
            status="pending_review",
            created_date=datetime.now(),
            updated_date=datetime.now(),
        )

        # Response Pagination in adcp 3.6.0: has_more (required), cursor/total_count (optional)
        response = ListCreativesResponse(
            creatives=[creative1, creative2],
            query_summary=QuerySummary(
                total_matching=2,
                returned=2,
                filters_applied=[],
            ),
            pagination=Pagination(
                has_more=False,
            ),
        )

        # Test model_dump
        adcp_response = response.model_dump()

        # Verify required AdCP fields are present
        adcp_required_fields = ["creatives", "query_summary", "pagination"]
        for field in adcp_required_fields:
            assert field in adcp_response, f"Required AdCP field '{field}' missing from response"
            assert adcp_response[field] is not None, f"Required AdCP field '{field}' is None"

        # Verify response structure requirements
        assert isinstance(adcp_response["creatives"], list), "Creatives must be array"
        assert isinstance(adcp_response["query_summary"], dict), "Query summary must be dict"
        assert isinstance(adcp_response["pagination"], dict), "Pagination must be dict"

        # Verify query_summary structure
        assert "total_matching" in adcp_response["query_summary"]
        assert "returned" in adcp_response["query_summary"]
        assert adcp_response["query_summary"]["total_matching"] >= 0

        # Verify pagination structure (adcp 3.6.0: has_more required, cursor/total_count optional)
        assert "has_more" in adcp_response["pagination"]

        # Test creative object structure in response
        if len(adcp_response["creatives"]) > 0:
            creative = adcp_response["creatives"][0]
            # Per adcp 3.6.0, Creative public fields are: creative_id, variants, format_id,
            # media_buy_id, totals, variant_count
            # Fields name/assets/status/created_date/updated_date/tags are now internal-only
            assert "creative_id" in creative, "Creative required field 'creative_id' missing"
            assert creative["creative_id"] is not None, "creative_id must not be None"

            # Verify internal-only fields are excluded (should NOT be in client responses)
            internal_fields = ["principal_id"]
            for field in internal_fields:
                assert field not in creative, f"Internal field '{field}' should be excluded from client response"

        # Verify required fields are present
        # Per AdCP spec, only query_summary, pagination, and creatives are required
        # Optional fields (format_summary, status_summary, etc.) are omitted if not set
        required_fields = ["query_summary", "pagination", "creatives"]
        for field in required_fields:
            assert field in adcp_response, f"Required field '{field}' missing from response"

        # Verify we have at least the required fields (and possibly some optional ones)
        assert len(adcp_response) >= len(required_fields), (
            f"Response should have at least {len(required_fields)} required fields, got {len(adcp_response)}"
        )

    def test_create_media_buy_response_adcp_compliance(self):
        """Test that CreateMediaBuyResponse complies with AdCP create-media-buy-response schema.

        Per AdCP PR #186, responses use oneOf discriminator for atomic semantics.
        Success responses have media_buy_id + packages, error responses have errors array.
        """
        # Create success response with domain fields only (per AdCP PR #113)
        # Protocol fields (status, task_id, message) are added by transport layer
        # Note: creative_deadline must be timezone-aware datetime (adcp 2.0.0)
        # Note: packages in response require package_id and paused field (adcp 2.12.0+)
        from src.core.schemas import CreateMediaBuyError, CreateMediaBuySuccess

        successful_response = CreateMediaBuySuccess(
            media_buy_id="mb_12345",
            packages=[{"package_id": "pkg_1", "paused": False}],
            creative_deadline=datetime.now(UTC) + timedelta(days=7),
        )

        # Test successful response AdCP compliance
        adcp_response = successful_response.model_dump()

        # Verify required AdCP domain fields present and non-null
        required_fields = []  # buyer_ref removed in adcp 3.12, media_buy_id is required
        for field in required_fields:
            assert field in adcp_response, f"Required AdCP field '{field}' missing from response"
            assert adcp_response[field] is not None, f"Required AdCP field '{field}' is None"

        # Verify optional AdCP domain fields that were set are present with valid values
        # Per AdCP spec, optional fields with None values are omitted (not present with null)
        assert "media_buy_id" in adcp_response, "media_buy_id was set, should be present"
        assert isinstance(adcp_response["media_buy_id"], str), "media_buy_id must be string"
        assert len(adcp_response["media_buy_id"]) > 0, "media_buy_id must not be empty"

        assert "packages" in adcp_response, "packages was set, should be present"
        assert isinstance(adcp_response["packages"], list), "packages must be array"

        assert "creative_deadline" in adcp_response, "creative_deadline was set, should be present"

        # Per oneOf constraint: success responses cannot have errors field
        assert "errors" not in adcp_response, "Success response cannot have errors field"

        # Test error response (oneOf error branch)
        error_response = CreateMediaBuyError(
            errors=[{"code": "test_error", "message": "test error"}],
        )
        adcp_error = error_response.model_dump()
        assert "errors" in adcp_error, "Error response must have errors field"
        assert isinstance(adcp_error["errors"], list), "errors must be array"
        assert len(adcp_error["errors"]) > 0, "errors array must not be empty"

        # Per oneOf constraint: error responses cannot have success fields
        assert "media_buy_id" not in adcp_error, "Error response cannot have media_buy_id"
        assert "packages" not in adcp_error, "Error response cannot have packages"

        # Test that Union type works for type hints

        success_via_union: CreateMediaBuyResponse = CreateMediaBuySuccess(
            media_buy_id="mb_union",
            packages=[],
        )
        error_via_union: CreateMediaBuyResponse = CreateMediaBuyError(
            errors=[{"code": "test", "message": "test"}],
        )

        # Verify Union type assignments work
        assert isinstance(success_via_union, CreateMediaBuySuccess)
        assert isinstance(error_via_union, CreateMediaBuyError)

        # Verify field count for success response
        assert len(adcp_response) >= 3, (
            f"CreateMediaBuySuccess should have at least 3 required fields, got {len(adcp_response)}"
        )

    def test_get_products_response_adcp_compliance(self):
        """Test that GetProductsResponse complies with AdCP get-products-response schema."""
        # Create Product using the actual Product model (not ProductSchema)
        from src.core.schemas import Product as ProductModel
        from tests.helpers.adcp_factories import (
            create_test_cpm_pricing_option,
            create_test_publisher_properties_by_tag,
        )

        product = ProductModel(
            product_id="prod_1",
            name="Premium Display",
            description="High-quality display advertising",
            format_ids=[
                {"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"},
                {"agent_url": "https://creative.adcontextprotocol.org", "id": "display_728x90"},
            ],
            delivery_type="guaranteed",
            delivery_measurement={"provider": "test_provider", "notes": "Test measurement"},  # Required per AdCP spec
            measurement=None,
            creative_policy=None,
            is_custom=False,
            publisher_properties=[create_test_publisher_properties_by_tag(publisher_domain="test.com")],
            pricing_options=[
                create_test_cpm_pricing_option(
                    pricing_option_id="cpm_usd_fixed",
                    currency="USD",
                    rate=10.0,
                )
            ],
        )

        # Create response with products
        response = GetProductsResponse(
            products=[product],
            errors=[],
        )

        # Test AdCP-compliant response
        adcp_response = response.model_dump()

        # Verify required AdCP fields present and non-null
        required_fields = ["products"]
        for field in required_fields:
            assert field in adcp_response, f"Required AdCP field '{field}' missing from response"
            assert adcp_response[field] is not None, f"Required AdCP field '{field}' is None"

        # Verify optional AdCP fields present (can be null)
        # Note: message field removed - handled via __str__() for protocol layer
        optional_fields = ["errors"]
        for field in optional_fields:
            assert field in adcp_response, f"Optional AdCP field '{field}' missing from response"

        # Verify message is provided via __str__() not as schema field
        assert "message" not in adcp_response, "message should not be in schema (use __str__() instead)"
        assert str(response) == "Found 1 product that matches your requirements."

        # Verify optional status field (AdCP PR #77 - MCP Status System)
        # Status field is optional and only present when explicitly set
        if "status" in adcp_response:
            assert isinstance(adcp_response["status"], str), "status must be string when present"

        # Verify specific field types and constraints
        assert isinstance(adcp_response["products"], list), "products must be array"
        assert len(adcp_response["products"]) > 0, "products array should not be empty"

        # Verify product structure - Product.model_dump() should convert formats -> format_ids
        product_data = adcp_response["products"][0]
        assert "product_id" in product_data, "product must have product_id"
        assert "format_ids" in product_data, "product must have format_ids (not formats)"
        assert "formats" not in product_data, "product should not have formats field (use format_ids)"

        # Test empty response case
        empty_response = GetProductsResponse(products=[], errors=[])

        empty_adcp_response = empty_response.model_dump()
        assert empty_adcp_response["products"] == [], "Empty products list should be empty array"
        # Verify __str__() provides appropriate empty message
        assert str(empty_response) == "No products matched your requirements."
        # Allow 2 or 3 fields (status is optional and may not be present, message removed)
        assert len(empty_adcp_response) >= 2 and len(empty_adcp_response) <= 3, (
            f"GetProductsResponse should have 2-3 fields (status optional), got {len(empty_adcp_response)}"
        )

    def test_list_creative_formats_response_adcp_compliance(self):
        """Test that ListCreativeFormatsResponse complies with AdCP list-creative-formats-response schema."""

        # Create response with formats using actual Format schema
        response = ListCreativeFormatsResponse(
            formats=[
                Format(
                    format_id=FormatId(agent_url="https://creative.adcontextprotocol.org", id="display_300x250"),
                    name="Medium Rectangle",
                    type="display",
                    is_standard=True,
                    iab_specification="IAB Display",
                    requirements={"width": 300, "height": 250, "file_types": ["jpg", "png", "gif"]},
                    assets=None,  # Use new 'assets' field (assets_required is deprecated)
                )
            ],
            # errors omitted - per AdCP spec, optional fields with None/empty values should be omitted
        )

        # Test AdCP-compliant response
        adcp_response = response.model_dump()

        # Verify required AdCP fields present and non-null
        required_fields = ["formats"]
        for field in required_fields:
            assert field in adcp_response, f"Required AdCP field '{field}' missing from response"
            assert adcp_response[field] is not None, f"Required AdCP field '{field}' is None"

        # Verify optional AdCP fields with None values are omitted (not present with null)
        # Note: message, adcp_version, status fields removed - handled via protocol envelope
        assert "errors" not in adcp_response, "errors with None/empty value should be omitted"
        assert "creative_agents" not in adcp_response, "creative_agents with None value should be omitted"

        # Verify message is provided via __str__() not as schema field
        assert "message" not in adcp_response, "message should not be in schema (use __str__() instead)"
        assert str(response) == "Found 1 creative format."

        # Verify specific field types and constraints
        assert isinstance(adcp_response["formats"], list), "formats must be array"

        # Verify format structure (using actual Format schema fields)
        if len(adcp_response["formats"]) > 0:
            format_obj = adcp_response["formats"][0]
            assert "format_id" in format_obj, "format must have format_id"
            assert "name" in format_obj, "format must have name"
            assert "type" in format_obj, "format must have type"
            # Note: width/height are in requirements dict, not direct fields

        # Verify field count - only required fields + non-None optional fields
        # formats is required; errors and creative_agents are omitted (None values)
        assert len(adcp_response) >= 1, (
            f"ListCreativeFormatsResponse should have at least required fields, got {len(adcp_response)}"
        )

    def test_update_media_buy_response_adcp_compliance(self):
        """Test that UpdateMediaBuyResponse complies with AdCP update-media-buy-response schema.

        Per AdCP PR #186, responses use oneOf discriminator for atomic semantics.
        Success responses have media_buy_id + buyer_ref, error responses have errors array.
        """
        # Create successful update response (oneOf success branch)
        # Note: implementation_date must be timezone-aware datetime (adcp 2.0.0)
        # Note: affected_packages now uses full Package type with paused field (adcp 2.12.0+)
        from src.core.schemas import UpdateMediaBuyError, UpdateMediaBuySuccess

        response = UpdateMediaBuySuccess(
            media_buy_id="buy_123",
            implementation_date=datetime.now(UTC) + timedelta(hours=1),
            affected_packages=[{"package_id": "pkg_1", "paused": False}],
        )

        # Test AdCP-compliant response
        adcp_response = response.model_dump()

        # Verify required AdCP fields present and non-null
        required_fields = ["media_buy_id"]  # buyer_ref removed in adcp 3.12
        for field in required_fields:
            assert field in adcp_response, f"Required AdCP field '{field}' missing from response"
            assert adcp_response[field] is not None, f"Required AdCP field '{field}' is None"

        # Verify affected_packages if provided
        if "affected_packages" in adcp_response:
            assert isinstance(adcp_response["affected_packages"], list), "affected_packages must be array"

        # Note: implementation_date and affected_packages are internal fields
        # excluded by model_dump() per AdCP PR #113
        # They are only included in model_dump_internal() for database storage

        # Per oneOf constraint: success responses cannot have errors field
        assert "errors" not in adcp_response, "Success response cannot have errors field"

        # Test error response (oneOf error branch)
        error_response = UpdateMediaBuyError(
            errors=[{"code": "update_failed", "message": "Update operation failed"}],
        )
        adcp_error = error_response.model_dump()
        assert "errors" in adcp_error, "Error response must have errors field"
        assert len(adcp_error["errors"]) > 0, "errors array must not be empty"

        # Per oneOf constraint: error responses cannot have success fields
        assert "media_buy_id" not in adcp_error, "Error response cannot have media_buy_id"
        assert "buyer_ref" not in adcp_error, "Error response cannot have buyer_ref"

        # Verify field count for success response (media_buy_id, buyer_ref are required)
        assert len(adcp_response) >= 2, (
            f"UpdateMediaBuySuccess should have at least 2 required fields, got {len(adcp_response)}"
        )

    def test_get_media_buy_delivery_request_adcp_compliance(self):
        """Test that GetMediaBuyDeliveryRequest complies with AdCP get-media-buy-delivery-request schema."""

        # Test request with all required + optional fields
        # buyer_refs removed in adcp 3.12
        request = GetMediaBuyDeliveryRequest(
            media_buy_ids=["mb_123", "mb_456"],
            status_filter="active",
            start_date="2025-01-01",
            end_date="2025-01-31",
        )

        # Test AdCP-compliant request
        adcp_request = request.model_dump()

        # Verify all fields are optional in AdCP spec (buyer_refs removed in 3.12)
        adcp_optional_fields = ["media_buy_ids", "status_filter", "start_date", "end_date"]
        for field in adcp_optional_fields:
            assert field in adcp_request, f"AdCP optional field '{field}' missing from request"

        # Verify field types and constraints
        if adcp_request.get("media_buy_ids") is not None:
            assert isinstance(adcp_request["media_buy_ids"], list), "media_buy_ids must be array"

        if adcp_request.get("status_filter") is not None:
            # Can be string or array according to AdCP spec
            # AdCP MediaBuyStatus enum: pending_activation, active, paused, completed
            valid_statuses = ["pending_activation", "active", "paused", "completed"]
            if isinstance(adcp_request["status_filter"], str):
                assert adcp_request["status_filter"] in valid_statuses, (
                    f"Invalid status: {adcp_request['status_filter']}"
                )
            elif isinstance(adcp_request["status_filter"], list):
                for status in adcp_request["status_filter"]:
                    assert status in valid_statuses, f"Invalid status in array: {status}"

        # Verify date format if provided
        if adcp_request.get("start_date") is not None:
            import re

            date_pattern = r"^\d{4}-\d{2}-\d{2}$"
            assert re.match(date_pattern, adcp_request["start_date"]), "start_date must be YYYY-MM-DD format"

        if adcp_request.get("end_date") is not None:
            import re

            date_pattern = r"^\d{4}-\d{2}-\d{2}$"
            assert re.match(date_pattern, adcp_request["end_date"]), "end_date must be YYYY-MM-DD format"

        # Test minimal request (all fields optional)
        minimal_request = GetMediaBuyDeliveryRequest()
        minimal_adcp_request = minimal_request.model_dump()

        # Should work with no fields set
        assert isinstance(minimal_adcp_request, dict), "Minimal request should be valid"

        # Test array status_filter (using valid AdCP MediaBuyStatus values)
        array_request = GetMediaBuyDeliveryRequest(status_filter=["active", "completed"])
        array_adcp_request = array_request.model_dump()
        assert isinstance(array_adcp_request["status_filter"], list), "status_filter should support array format"

    def test_get_media_buy_delivery_response_adcp_compliance(self):
        """Test that GetMediaBuyDeliveryResponse complies with AdCP get-media-buy-delivery-response schema."""
        from src.core.schemas import (
            AggregatedTotals,
            DailyBreakdown,
            DeliveryTotals,
            PackageDelivery,
        )

        # Create AdCP-compliant delivery data using new models
        package_delivery = PackageDelivery(
            package_id="pkg_123",
            impressions=25000.0,
            spend=500.75,
            clicks=125.0,
            video_completions=None,
            pacing_index=1.0,
        )

        daily_breakdown = DailyBreakdown(date="2025-01-15", impressions=1250.0, spend=25.05)

        delivery_totals = DeliveryTotals(
            impressions=25000.0, spend=500.75, clicks=125.0, ctr=0.005, video_completions=None, completion_rate=None
        )

        delivery_data = MediaBuyDeliveryData(
            media_buy_id="mb_12345",
            status="active",
            totals=delivery_totals,
            by_package=[package_delivery.model_dump()],
            daily_breakdown=[daily_breakdown.model_dump()],
        )

        # adcp 3.6.0: Use dict for reporting_period - the response uses media_buy's ReportingPeriod
        # which is a different class from the local ReportingPeriod (creative delivery version)
        reporting_period_dict = {"start": "2025-01-01T00:00:00Z", "end": "2025-01-31T23:59:59Z"}

        aggregated_totals = AggregatedTotals(
            impressions=25000.0, spend=500.75, clicks=125.0, video_completions=None, media_buy_count=1
        )

        # Create AdCP-compliant response
        response = GetMediaBuyDeliveryResponse(
            reporting_period=reporting_period_dict,
            currency="USD",
            aggregated_totals=aggregated_totals,
            media_buy_deliveries=[delivery_data],
            errors=None,
        )

        # Test AdCP-compliant response
        adcp_response = response.model_dump()

        # Verify required AdCP fields present and non-null
        required_fields = ["reporting_period", "currency", "media_buy_deliveries"]
        for field in required_fields:
            assert field in adcp_response, f"Required AdCP field '{field}' missing from response"
            assert adcp_response[field] is not None, f"Required AdCP field '{field}' is None"

        # Verify optional AdCP fields that were set are present
        assert "aggregated_totals" in adcp_response, "aggregated_totals was set, should be present"

        # errors=None was set, so it should be omitted per AdCP spec
        assert "errors" not in adcp_response, "errors with None value should be omitted"

        # Verify currency format
        import re

        currency_pattern = r"^[A-Z]{3}$"
        assert re.match(currency_pattern, adcp_response["currency"]), "currency must be 3-letter ISO code"

        # Verify reporting_period structure
        reporting_period_obj = adcp_response["reporting_period"]
        assert "start" in reporting_period_obj, "reporting_period must have start"
        assert "end" in reporting_period_obj, "reporting_period must have end"

        # Verify aggregated_totals structure
        aggregated_obj = adcp_response["aggregated_totals"]
        assert "impressions" in aggregated_obj, "aggregated_totals must have impressions"
        assert "spend" in aggregated_obj, "aggregated_totals must have spend"
        assert "media_buy_count" in aggregated_obj, "aggregated_totals must have media_buy_count"
        assert aggregated_obj["impressions"] >= 0, "impressions must be non-negative"
        assert aggregated_obj["spend"] >= 0, "spend must be non-negative"
        assert aggregated_obj["media_buy_count"] >= 0, "media_buy_count must be non-negative"

        # Verify media_buy_deliveries array structure
        assert isinstance(adcp_response["media_buy_deliveries"], list), "media_buy_deliveries must be array"

        if len(adcp_response["media_buy_deliveries"]) > 0:
            delivery = adcp_response["media_buy_deliveries"][0]

            # Verify required delivery fields
            delivery_required_fields = ["media_buy_id", "status", "totals", "by_package"]
            for field in delivery_required_fields:
                assert field in delivery, f"delivery must have {field}"
                assert delivery[field] is not None, f"delivery {field} must not be None"

            # Verify delivery optional fields
            delivery_optional_fields = ["daily_breakdown"]  # buyer_ref removed in adcp 3.12
            for field in delivery_optional_fields:
                assert field in delivery, f"delivery optional field '{field}' missing"

            # Verify status enum
            valid_statuses = ["pending", "active", "paused", "completed", "failed"]
            assert delivery["status"] in valid_statuses, f"Invalid delivery status: {delivery['status']}"

            # Verify totals structure
            totals = delivery["totals"]
            assert "impressions" in totals, "totals must have impressions"
            assert "spend" in totals, "totals must have spend"
            assert totals["impressions"] >= 0, "totals impressions must be non-negative"
            assert totals["spend"] >= 0, "totals spend must be non-negative"

            # Verify by_package array
            assert isinstance(delivery["by_package"], list), "by_package must be array"
            if len(delivery["by_package"]) > 0:
                package = delivery["by_package"][0]
                package_required_fields = ["package_id", "impressions", "spend"]
                for field in package_required_fields:
                    assert field in package, f"package must have {field}"
                    assert package[field] is not None, f"package {field} must not be None"

        # Test empty response case
        empty_aggregated = AggregatedTotals(impressions=0, spend=0, media_buy_count=0)
        empty_response = GetMediaBuyDeliveryResponse(
            reporting_period=reporting_period_dict,
            currency="USD",
            aggregated_totals=empty_aggregated,
            media_buy_deliveries=[],
        )

        empty_adcp_response = empty_response.model_dump()
        assert empty_adcp_response["media_buy_deliveries"] == [], (
            "Empty media_buy_deliveries list should be empty array"
        )

        # Verify field count - required fields + non-None optional fields
        # reporting_period, currency, media_buy_deliveries are required; aggregated_totals set; errors=None omitted
        assert len(adcp_response) >= 3, (
            f"GetMediaBuyDeliveryResponse should have at least 3 required fields, got {len(adcp_response)}"
        )

    def test_property_identifier_adcp_compliance(self):
        """Test that PropertyIdentifier complies with AdCP property identifier schema."""
        # Create identifier with all required fields
        identifier = PropertyIdentifier(type="domain", value="example.com")

        # Test AdCP-compliant response
        adcp_response = identifier.model_dump()

        # Verify required AdCP fields present and non-null
        required_fields = ["type", "value"]
        for field in required_fields:
            assert field in adcp_response
            assert adcp_response[field] is not None

        # Verify field count expectations
        assert len(adcp_response) == 2

    def test_property_adcp_compliance(self):
        """Test that Property complies with AdCP property schema.

        adcp 3.10: Property schema requires property_type (enum), name (str),
        identifiers (list of {type, value} dicts).
        Optional fields: property_id, tags, supported_channels, publisher_domain.
        """
        # Create property with required fields (adcp 3.10 schema)
        property_obj = Property(
            property_type="website",
            name="Example",
            identifiers=[{"type": "domain", "value": "example.com"}],
        )

        # Test AdCP-compliant response (mode="json" serializes enums to strings)
        adcp_response = property_obj.model_dump(mode="json", exclude_none=True)

        # Verify required AdCP fields present and non-null
        required_fields = ["property_type", "name", "identifiers"]
        for field in required_fields:
            assert field in adcp_response
            assert adcp_response[field] is not None

        # Verify property type is valid enum value (as string after json serialization)
        valid_types = ["website", "mobile_app", "ctv_app", "desktop_app", "dooh", "podcast", "radio", "streaming_audio"]
        assert adcp_response["property_type"] in valid_types

        # Verify identifiers structure
        assert len(adcp_response["identifiers"]) == 1
        assert adcp_response["identifiers"][0]["type"] == "domain"
        assert adcp_response["identifiers"][0]["value"] == "example.com"

        # None-valued optional fields should be excluded (using exclude_none=True)
        assert "tags" not in adcp_response
        assert "supported_channels" not in adcp_response
        assert "publisher_domain" not in adcp_response

        # Test with optional fields
        property_with_extras = Property(
            property_type="mobile_app",
            name="Example App",
            identifiers=[{"type": "ios_bundle", "value": "com.example.app"}],
            publisher_domain="example.com",
        )
        extras_response = property_with_extras.model_dump(mode="json", exclude_none=True)
        assert extras_response["property_type"] == "mobile_app"
        assert extras_response["name"] == "Example App"
        assert extras_response["identifiers"][0]["value"] == "com.example.app"
        assert extras_response["publisher_domain"] == "example.com"

    def test_property_tag_metadata_adcp_compliance(self):
        """Test that PropertyTagMetadata complies with AdCP tag metadata schema."""
        # Create tag metadata with all required fields
        tag_metadata = PropertyTagMetadata(
            name="Premium Content", description="High-quality editorial content from trusted publishers"
        )

        # Test AdCP-compliant response
        adcp_response = tag_metadata.model_dump()

        # Verify required AdCP fields present and non-null
        required_fields = ["name", "description"]
        for field in required_fields:
            assert field in adcp_response
            assert adcp_response[field] is not None

        # Verify field count expectations
        assert len(adcp_response) == 2

    def test_list_authorized_properties_request_adcp_compliance(self):
        """Test that ListAuthorizedPropertiesRequest complies with AdCP list-authorized-properties-request schema."""
        # Create request with optional fields per spec
        # Per AdCP spec: context, ext, publisher_domains, property_tags are all optional
        request = ListAuthorizedPropertiesRequest(publisher_domains=["example.com", "news.example.com"])

        # Test AdCP-compliant response - use exclude_none=False to see all fields
        adcp_response = request.model_dump(exclude_none=False)

        # Per AdCP spec, all fields are optional
        optional_fields = ["context", "ext", "publisher_domains", "property_tags"]
        for field in optional_fields:
            assert field in adcp_response

        # Verify publisher_domains is array when present
        if adcp_response["publisher_domains"] is not None:
            assert isinstance(adcp_response["publisher_domains"], list)

        # Verify field count expectations - all 4 optional fields
        assert len(adcp_response) == 4

    def test_list_authorized_properties_response_adcp_compliance(self):
        """Test that ListAuthorizedPropertiesResponse complies with AdCP v2.4 list-authorized-properties-response schema."""
        # Create response with required fields only (per AdCP spec, optional fields should be omitted if not set)
        # Per /schemas/v1/media-buy/list-authorized-properties-response.json, only these fields are spec-compliant:
        # - publisher_domains (required)
        # - primary_channels, primary_countries, portfolio_description, advertising_policies, last_updated, errors (optional)
        response = ListAuthorizedPropertiesResponse(
            publisher_domains=["example.com"],
            # All optional fields omitted - per AdCP spec, optional fields with None/empty values should be omitted
        )

        # Test AdCP-compliant response
        adcp_response = response.model_dump()

        # Verify required AdCP fields present and non-null
        required_fields = ["publisher_domains"]
        for field in required_fields:
            assert field in adcp_response
            assert adcp_response[field] is not None

        # Verify publisher_domains is array
        assert isinstance(adcp_response["publisher_domains"], list)

        # Verify optional fields with None values are omitted per AdCP spec
        assert "errors" not in adcp_response, "errors with None/empty value should be omitted"
        assert "primary_channels" not in adcp_response, "primary_channels with None value should be omitted"
        assert "primary_countries" not in adcp_response, "primary_countries with None value should be omitted"
        assert "portfolio_description" not in adcp_response, "portfolio_description with None value should be omitted"
        assert "advertising_policies" not in adcp_response, "advertising_policies with None value should be omitted"
        assert "last_updated" not in adcp_response, "last_updated with None value should be omitted"

        # Verify message is provided via __str__() not as schema field
        assert str(response) == "Found 1 authorized publisher domain."

        # Test with optional fields set to non-None values
        response_with_optionals = ListAuthorizedPropertiesResponse(
            publisher_domains=["example.com", "example.org"],
            primary_channels=["display", "video"],
            advertising_policies="No tobacco ads",
        )
        adcp_with_optionals = response_with_optionals.model_dump()
        assert "primary_channels" in adcp_with_optionals, "Set optional fields should be present"
        assert "advertising_policies" in adcp_with_optionals, "Set optional fields should be present"
        assert isinstance(adcp_with_optionals["primary_channels"], list)
        assert isinstance(adcp_with_optionals["advertising_policies"], str)

    def test_get_signals_request_adcp_compliance(self):
        """Test that GetSignalsRequest model complies with AdCP get-signals-request schema."""
        # adcp 3.9: GetSignalsRequest is a regular model (not RootModel).
        # deliver_to replaced with top-level destinations + countries fields.

        from adcp.types.generated_poc.core.destination import Destination1

        from src.core.schemas import GetSignalsRequest, SignalFilters

        # Test AdCP-compliant request with all fields
        adcp_request = GetSignalsRequest(
            signal_spec="Sports enthusiasts in automotive market",
            destinations=[
                Destination1(platform="google_ad_manager", type="platform", account="123456"),
                Destination1(platform="the_trade_desk", type="platform", account="ttd789"),
            ],
            countries=["US", "CA"],
            filters=SignalFilters(
                catalog_types=["marketplace", "custom"],
                data_providers=["Acme Data Solutions"],
                max_cpm=5.0,
                min_coverage_percentage=75.0,
            ),
            max_results=50,
        )

        adcp_response = adcp_request.model_dump(mode="json", exclude_none=True)

        # Verify signal_spec present
        assert "signal_spec" in adcp_response, "signal_spec missing from response"
        assert adcp_response["signal_spec"] is not None

        # Verify optional fields present when provided
        for field in ["destinations", "countries", "filters", "max_results"]:
            assert field in adcp_response, f"Optional AdCP field '{field}' missing from response"

        # Verify destinations structure
        destinations = adcp_response["destinations"]
        assert isinstance(destinations, list), "destinations must be array"
        assert len(destinations) >= 1, "destinations must have at least one entry"

        # Verify countries are 2-letter ISO codes
        for country in adcp_response["countries"]:
            assert len(country) == 2, f"Country code '{country}' must be 2-letter ISO code"
            assert country.isupper(), f"Country code '{country}' must be uppercase"

        # Verify filters structure when present
        filters = adcp_response["filters"]
        if filters.get("catalog_types"):
            valid_catalog_types = ["marketplace", "custom", "owned"]
            for catalog_type in filters["catalog_types"]:
                assert catalog_type in valid_catalog_types, f"Invalid catalog_type: {catalog_type}"

        if filters.get("min_coverage_percentage") is not None:
            assert 0 <= filters["min_coverage_percentage"] <= 100, "min_coverage_percentage must be 0-100"

        # Verify max_results constraint
        if adcp_response.get("max_results") is not None:
            assert adcp_response["max_results"] >= 1, "max_results must be positive"

        # Test minimal request (only signal_spec)
        minimal_request = GetSignalsRequest(
            signal_spec="Automotive intenders",
        )
        minimal_response = minimal_request.model_dump(exclude_none=True)
        assert "signal_spec" in minimal_response

        # adcp 3.9: direct attribute access (no longer RootModel)
        assert adcp_request.signal_spec == "Sports enthusiasts in automotive market"

        # Verify field count
        assert len(adcp_response) >= 2, f"AdCP request should have at least 2 fields, got {len(adcp_response)}"

    def test_update_media_buy_request_adcp_compliance(self):
        """Test that UpdateMediaBuyRequest model complies with AdCP update-media-buy-request schema."""
        # ✅ FIXED: Implementation now matches AdCP spec
        # AdCP spec requires: oneOf(media_buy_id OR buyer_ref), optional active/start_time/end_time/budget/packages

        from datetime import UTC, datetime

        from src.core.schemas import AdCPPackageUpdate, Budget, UpdateMediaBuyRequest

        # Test AdCP-compliant request with media_buy_id (oneOf option 1)
        adcp_request_id = UpdateMediaBuyRequest(
            media_buy_id="mb_12345",
            paused=False,  # adcp 2.12.0+: replaced 'active' with 'paused'
            start_time=datetime(2025, 2, 1, 9, 0, 0, tzinfo=UTC),
            end_time=datetime(2025, 2, 28, 23, 59, 59, tzinfo=UTC),
            budget=Budget(total=5000.0, currency="USD", pacing="even"),
            packages=[AdCPPackageUpdate(package_id="pkg_123", paused=False, budget=2500.0)],  # adcp 2.12.0+
        )

        adcp_response_id = adcp_request_id.model_dump()

        # ✅ VERIFY ADCP COMPLIANCE: media_buy_id is required (buyer_ref removed in adcp 3.12)
        assert "media_buy_id" in adcp_response_id, "media_buy_id must be present"
        assert adcp_response_id["media_buy_id"] is not None, "media_buy_id must not be None"

        # ✅ VERIFY ADCP COMPLIANCE: Optional fields present when provided
        optional_fields = ["paused", "start_time", "end_time", "budget", "packages"]  # adcp 2.12.0+
        for field in optional_fields:
            if getattr(adcp_request_id, field) is not None:
                assert field in adcp_response_id, f"Optional AdCP field '{field}' missing from response"

        # ✅ VERIFY start_time/end_time are datetime (not date)
        if adcp_response_id.get("start_time"):
            # Should be datetime object (model_dump preserves datetime objects)
            start_time_obj = adcp_response_id["start_time"]
            assert isinstance(start_time_obj, datetime), "start_time should be datetime object"

        if adcp_response_id.get("end_time"):
            # Should be datetime object (model_dump preserves datetime objects)
            end_time_obj = adcp_response_id["end_time"]
            assert isinstance(end_time_obj, datetime), "end_time should be datetime object"

        # ✅ VERIFY packages array structure
        if adcp_response_id.get("packages"):
            assert isinstance(adcp_response_id["packages"], list), "packages must be array"
            for package in adcp_response_id["packages"]:
                # Each package must have package_id (buyer_ref removed in adcp 3.12)
                has_package_id = package.get("package_id") is not None
                assert has_package_id, "Each package must have package_id"

        # adcp 3.12: media_buy_id is required (buyer_ref identification removed)
        import pytest
        from pydantic import ValidationError as PydanticValidationError

        with pytest.raises((PydanticValidationError, ValueError)):
            UpdateMediaBuyRequest(paused=False)  # missing required media_buy_id

    def test_task_status_mcp_integration(self):
        """Test TaskStatus integration with MCP response schemas (AdCP PR #77)."""

        # Test that TaskStatus enum has expected values
        assert TaskStatus.SUBMITTED == "submitted"
        assert TaskStatus.WORKING == "working"
        assert TaskStatus.INPUT_REQUIRED == "input-required"
        assert TaskStatus.COMPLETED == "completed"
        assert TaskStatus.FAILED == "failed"
        assert TaskStatus.AUTH_REQUIRED == "auth-required"

        # Test TaskStatus helper method - basic cases
        status = TaskStatus.from_operation_state("discovery")
        assert status == TaskStatus.COMPLETED

        status = TaskStatus.from_operation_state("creation", requires_approval=True)
        assert status == TaskStatus.INPUT_REQUIRED

        # Test precedence rules
        status = TaskStatus.from_operation_state("creation", has_errors=True, requires_approval=True)
        assert status == TaskStatus.FAILED  # Errors take precedence

        status = TaskStatus.from_operation_state("discovery", requires_auth=True)
        assert status == TaskStatus.AUTH_REQUIRED  # Auth requirement takes highest precedence

        # Test edge cases
        status = TaskStatus.from_operation_state("unknown_operation")
        assert status == TaskStatus.UNKNOWN

        # Test that response schemas no longer have status field (moved to protocol envelope)
        # Per AdCP PR #113, status is handled at transport layer via ProtocolEnvelope
        response = GetProductsResponse(products=[])

        data = response.model_dump()
        assert "status" not in data  # Status field removed from domain models

    def test_package_excludes_internal_fields(self):
        """Test that Package model_dump excludes internal fields from AdCP responses.

        Internal fields like platform_line_item_id, tenant_id, etc. should NOT appear
        in external AdCP responses but SHOULD appear in internal database operations.
        """
        # Create package with internal fields
        pkg = Package(
            package_id="pkg_test_123",
            paused=False,  # Changed from status="active" in adcp 2.12.0
            # Internal fields (should be excluded from external responses)
            platform_line_item_id="gam_987654321",
            tenant_id="tenant_test",
            media_buy_id="mb_test_456",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            metadata={"internal_key": "internal_value"},
        )

        # External response (AdCP protocol) - should exclude internal fields
        external_dump = pkg.model_dump()
        assert "package_id" in external_dump
        # buyer_ref removed from Package in adcp 3.12
        assert "platform_line_item_id" not in external_dump, "platform_line_item_id should NOT be in AdCP response"
        assert "tenant_id" not in external_dump, "tenant_id should NOT be in AdCP response"
        assert "media_buy_id" not in external_dump, "media_buy_id should NOT be in AdCP response"
        assert "created_at" not in external_dump, "created_at should NOT be in AdCP response"
        assert "updated_at" not in external_dump, "updated_at should NOT be in AdCP response"
        assert "metadata" not in external_dump, "metadata should NOT be in AdCP response"

        # Internal database dump - should include internal fields
        internal_dump = pkg.model_dump_internal()
        assert "package_id" in internal_dump
        assert "paused" in internal_dump  # Changed from status in adcp 2.12.0
        assert "platform_line_item_id" in internal_dump, "platform_line_item_id SHOULD be in internal dump"
        assert internal_dump["platform_line_item_id"] == "gam_987654321"
        assert "tenant_id" in internal_dump, "tenant_id SHOULD be in internal dump"
        assert internal_dump["tenant_id"] == "tenant_test"
        assert "media_buy_id" in internal_dump, "media_buy_id SHOULD be in internal dump"
        assert internal_dump["media_buy_id"] == "mb_test_456"

    def test_create_media_buy_asap_start_time(self):
        """Test that CreateMediaBuyRequest accepts 'asap' as start_time per AdCP v1.7.0."""
        end_date = datetime.now(UTC) + timedelta(days=30)

        # Test with 'asap' start_time
        # Per AdCP spec, budget is at package level, not request level
        # adcp 3.6.0: brand_manifest replaced by brand (BrandReference with required domain)
        request = CreateMediaBuyRequest(
            brand={"domain": "flashsale.com"},
            start_time="asap",  # AdCP v1.7.0 supports literal "asap"
            end_time=end_date,
            packages=[{"product_id": "product_1", "pricing_option_id": "test_pricing", "budget": 5000.0}],
        )

        # Verify asap is accepted (library wraps in StartTiming)
        if hasattr(request.start_time, "root"):
            assert request.start_time.root == "asap"
        else:
            assert request.start_time == "asap"

        # Verify it serializes correctly
        data = request.model_dump()
        assert data["start_time"] == "asap"

    def test_update_media_buy_asap_start_time(self):
        """Test that UpdateMediaBuyRequest accepts 'asap' as start_time per AdCP v1.7.0."""
        from src.core.schemas import UpdateMediaBuyRequest

        # Test with 'asap' start_time
        request = UpdateMediaBuyRequest(
            media_buy_id="mb_test_123",
            start_time="asap",  # AdCP v1.7.0 supports literal "asap"
        )

        # Verify asap is accepted
        assert request.start_time == "asap"

        # Verify it serializes correctly
        data = request.model_dump()
        assert data["start_time"] == "asap"

    def test_create_media_buy_datetime_start_time_still_works(self):
        """Test that CreateMediaBuyRequest still accepts datetime for start_time."""
        start_date = datetime.now(UTC) + timedelta(days=1)
        end_date = datetime.now(UTC) + timedelta(days=30)

        # Test with datetime start_time (should still work)
        # Per AdCP spec, budget is at package level, not request level
        # adcp 3.6.0: brand_manifest replaced by brand (BrandReference with required domain)
        request = CreateMediaBuyRequest(
            brand={"domain": "scheduled.com"},
            start_time=start_date,
            end_time=end_date,
            packages=[{"product_id": "product_1", "pricing_option_id": "test_pricing", "budget": 5000.0}],
        )

        # Verify datetime is still accepted (library wraps in StartTiming)
        if hasattr(request.start_time, "root"):
            assert isinstance(request.start_time.root, datetime)
            assert request.start_time.root == start_date
        else:
            assert isinstance(request.start_time, datetime)
            assert request.start_time == start_date

    def test_product_publisher_properties_constraint(self):
        """Test that Product requires publisher_properties per AdCP spec."""
        from src.core.schemas import Product
        from tests.helpers.adcp_factories import (
            create_test_cpm_pricing_option,
            create_test_publisher_properties_by_tag,
        )

        # Valid: publisher_properties using factory
        product_with_properties = Product(
            product_id="p1",
            name="Property Product",
            description="Product using full properties",
            format_ids=[{"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"}],
            delivery_type="guaranteed",
            delivery_measurement={"provider": "test_provider", "notes": "Test measurement"},  # Required per AdCP spec
            publisher_properties=[create_test_publisher_properties_by_tag(publisher_domain="example.com")],
            pricing_options=[
                create_test_cpm_pricing_option(
                    pricing_option_id="cpm_usd_fixed",
                    currency="USD",
                    rate=10.0,
                )
            ],
        )
        assert len(product_with_properties.publisher_properties) == 1
        # publisher_properties is a discriminated union with RootModel wrapper (adcp 2.14.0+)
        # Access via .root attribute
        assert product_with_properties.publisher_properties[0].root.publisher_domain == "example.com"

        # Invalid: missing publisher_properties (required)
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="publisher_properties"):
            Product(
                product_id="p2",
                name="Invalid Product",
                description="Product without publisher_properties",
                format_ids=[{"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"}],
                delivery_type="guaranteed",
                delivery_measurement={
                    "provider": "test_provider",
                    "notes": "Test measurement",
                },  # Required per AdCP spec
                pricing_options=[
                    create_test_cpm_pricing_option(
                        pricing_option_id="cpm_usd_fixed",
                        currency="USD",
                        rate=10.0,
                    )
                ],
                # Missing publisher_properties - should fail
            )

    def test_create_media_buy_with_brand_inline(self):
        """Test CreateMediaBuyRequest with inline brand reference (adcp 3.6.0).

        adcp 3.6.0: brand_manifest replaced by brand (BrandReference).
        BrandReference requires domain field, optionally brand_id.
        """
        start_date = datetime.now(UTC) + timedelta(days=1)
        end_date = datetime.now(UTC) + timedelta(days=30)

        # Test with inline brand reference
        # Per AdCP spec, budget is at package level, not request level
        request = CreateMediaBuyRequest(
            brand={"domain": "nike.com"},
            packages=[{"product_id": "product_1", "pricing_option_id": "test_pricing", "budget": 5000.0}],
            start_time=start_date,
            end_time=end_date,
        )

        # Verify brand is properly stored
        assert request.brand is not None
        assert request.brand.domain == "nike.com"

        # Verify fields still work (buyer_ref removed in adcp 3.12)
        assert len(request.packages) == 1

    def test_create_media_buy_with_brand_and_brand_id(self):
        """Test CreateMediaBuyRequest with brand reference including brand_id (adcp 3.6.0)."""
        start_date = datetime.now(UTC) + timedelta(days=1)
        end_date = datetime.now(UTC) + timedelta(days=30)

        # Test with brand reference + optional brand_id
        request = CreateMediaBuyRequest(
            brand={"domain": "nike.com", "brand_id": "brand_nike_001"},
            packages=[{"product_id": "product_1", "pricing_option_id": "test_pricing", "budget": 5000.0}],
            start_time=start_date,
            end_time=end_date,
        )

        # Verify brand fields
        assert request.brand.domain == "nike.com"
        # brand_id is wrapped in a BrandId RootModel
        brand_id = request.brand.brand_id
        brand_id_val = brand_id.root if hasattr(brand_id, "root") else brand_id
        assert brand_id_val == "brand_nike_001"

    def test_get_signals_response_adcp_compliance(self):
        """Test that GetSignalsResponse model complies with AdCP get-signals response schema.

        Per AdCP PR #113 and official schema, protocol fields (message, context_id)
        are added by the protocol layer, not the domain response.
        """
        from src.core.schemas import GetSignalsResponse

        # Minimal required fields - only signals is required per AdCP spec
        response = GetSignalsResponse(signals=[])

        # Convert to AdCP format (excludes internal fields)
        adcp_response = response.model_dump(exclude_none=True)

        # Verify required fields are present
        assert "signals" in adcp_response

        # Verify field count (signals is required, errors is optional)
        # Per AdCP PR #113, protocol fields removed from domain responses
        assert len(adcp_response) >= 1, (
            f"GetSignalsResponse should have at least 1 core field (signals), got {len(adcp_response)}"
        )

        # Test with all fields
        signal_data = {
            "signal_agent_segment_id": "seg_123",
            "name": "Premium Audiences",
            "description": "High-value customer segment",
            "signal_type": "marketplace",
            "data_provider": "Acme Data",
            "coverage_percentage": 85.5,
            "deployments": [{"platform": "GAM", "is_live": True, "type": "platform"}],
            "pricing_options": [
                {"pricing_option_id": "cpm_usd", "cpm": 2.50, "currency": "USD", "model": "cpm"},
            ],
        }
        # Test with optional errors field
        full_response = GetSignalsResponse(signals=[signal_data], errors=None)
        full_dump = full_response.model_dump(exclude_none=True)
        assert len(full_dump["signals"]) == 1

    def test_activate_signal_response_adcp_compliance(self):
        """Test that ActivateSignalResponse model complies with AdCP activate-signal response schema."""
        from src.core.schemas import ActivateSignalResponse

        # Minimal required fields (per AdCP PR #113 - only domain fields)
        response = ActivateSignalResponse(signal_id="sig_123")

        # Convert to AdCP format (excludes internal fields)
        adcp_response = response.model_dump(exclude_none=True)

        # Verify required fields are present (protocol fields like task_id, status removed)
        assert "signal_id" in adcp_response

        # Verify field count (domain fields only: signal_id, activation_details, errors)
        assert len(adcp_response) >= 1, (
            f"ActivateSignalResponse should have at least 1 core field, got {len(adcp_response)}"
        )

        # Test with activation details (domain data)
        full_response = ActivateSignalResponse(
            signal_id="sig_456",
            activation_details={"platform_id": "seg_789", "estimated_duration_minutes": 5.0},
            errors=None,
        )
        full_dump = full_response.model_dump(exclude_none=True)
        assert full_dump["signal_id"] == "sig_456"
        assert full_dump["activation_details"]["platform_id"] == "seg_789"


class TestProductV36FieldContract:
    """Contract tests for Product fields added in adcp v3.4.0-v3.6.0.

    Tests cover:
    - delivery_measurement (REQUIRED): presence + default behavior
    - delivery_type (REQUIRED): already tested in TestAdCPContract, verified here for completeness
    - product_card (optional): presence-when-set + absence-when-null
    - product_card_detailed (optional): presence-when-set + absence-when-null
    - placements (optional): presence-when-set + absence-when-null
    - reporting_capabilities (optional): presence-when-set + absence-when-null
    - signal_targeting_allowed (optional, default=False): presence + default
    - property_targeting_allowed (optional, default=False): presence + default
    - catalog_match (optional): presence-when-set + absence-when-null
    - catalog_types (optional): presence-when-set + absence-when-null
    - conversion_tracking (optional): presence-when-set + absence-when-null
    - data_provider_signals (optional): presence-when-set + absence-when-null
    - forecast (optional): presence-when-set + absence-when-null
    - channels (optional): presence-when-set + absence-when-null
    """

    @staticmethod
    def _make_base_product(**overrides):
        """Create a minimal valid Product with required fields only."""
        from src.core.schemas import Product
        from tests.helpers.adcp_factories import (
            create_test_cpm_pricing_option,
            create_test_publisher_properties_by_tag,
        )

        defaults = {
            "product_id": "v36_test",
            "name": "V3.6 Test Product",
            "description": "Product for v3.6 field contract tests",
            "format_ids": [{"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"}],
            "delivery_type": "guaranteed",
            "delivery_measurement": {"provider": "publisher", "notes": "Standard measurement"},
            "publisher_properties": [create_test_publisher_properties_by_tag()],
            "pricing_options": [create_test_cpm_pricing_option()],
        }
        defaults.update(overrides)
        return Product(**defaults)

    # --- delivery_measurement (REQUIRED) ---

    def test_delivery_measurement_required(self):
        """delivery_measurement is required per AdCP spec; omitting it fails validation."""
        from src.core.schemas import Product
        from tests.helpers.adcp_factories import (
            create_test_cpm_pricing_option,
            create_test_publisher_properties_by_tag,
        )

        # adcp 3.10: delivery_measurement is now optional (was required in 3.6-3.9)
        product = Product(
            product_id="no_dm",
            name="No DM",
            description="Missing delivery_measurement",
            format_ids=[{"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"}],
            delivery_type="guaranteed",
            publisher_properties=[create_test_publisher_properties_by_tag()],
            pricing_options=[create_test_cpm_pricing_option()],
            # delivery_measurement intentionally omitted — now optional per adcp 3.10
        )
        assert product.delivery_measurement is None

    def test_delivery_measurement_present_in_dump(self):
        """delivery_measurement appears in model_dump with correct structure."""
        product = self._make_base_product(
            delivery_measurement={"provider": "ias", "notes": "IAS viewability"},
        )
        dump = product.model_dump()
        assert "delivery_measurement" in dump
        assert dump["delivery_measurement"]["provider"] == "ias"
        assert dump["delivery_measurement"]["notes"] == "IAS viewability"

    def test_delivery_measurement_provider_only(self):
        """delivery_measurement with provider only (notes is optional)."""
        product = self._make_base_product(
            delivery_measurement={"provider": "moat"},
        )
        dump = product.model_dump()
        assert dump["delivery_measurement"]["provider"] == "moat"

    # --- property_targeting_allowed (optional, default=False) ---

    def test_property_targeting_allowed_default(self):
        """property_targeting_allowed defaults to False and appears in dump."""
        product = self._make_base_product()
        dump = product.model_dump()
        assert "property_targeting_allowed" in dump
        assert dump["property_targeting_allowed"] is False

    def test_property_targeting_allowed_when_true(self):
        """property_targeting_allowed=True appears correctly in dump."""
        product = self._make_base_product(property_targeting_allowed=True)
        dump = product.model_dump()
        assert dump["property_targeting_allowed"] is True

    # --- signal_targeting_allowed (optional, default=False) ---

    def test_signal_targeting_allowed_default(self):
        """signal_targeting_allowed defaults to False and appears in dump."""
        product = self._make_base_product()
        dump = product.model_dump()
        assert "signal_targeting_allowed" in dump
        assert dump["signal_targeting_allowed"] is False

    def test_signal_targeting_allowed_when_true(self):
        """signal_targeting_allowed=True appears correctly in dump."""
        product = self._make_base_product(signal_targeting_allowed=True)
        dump = product.model_dump()
        assert dump["signal_targeting_allowed"] is True

    # --- channels (optional, default=None) ---

    def test_channels_absent_when_null(self):
        """channels not in model_dump when not set (None)."""
        product = self._make_base_product()
        dump = product.model_dump()
        assert "channels" not in dump

    def test_channels_present_when_set(self):
        """channels appears in model_dump with MediaChannel enum values."""
        product = self._make_base_product(channels=["display", "olv", "ctv"])
        dump = product.model_dump()
        assert "channels" in dump
        assert len(dump["channels"]) == 3

        # JSON serialization should produce strings
        json_dump = product.model_dump(mode="json")
        assert json_dump["channels"] == ["display", "olv", "ctv"]

    # --- product_card (optional, default=None) ---

    def test_product_card_absent_when_null(self):
        """product_card not in model_dump when not set."""
        product = self._make_base_product()
        dump = product.model_dump()
        assert "product_card" not in dump

    def test_product_card_present_when_set(self):
        """product_card appears in model_dump with correct structure."""
        card = {
            "format_id": {"agent_url": "https://creative.adcontextprotocol.org", "id": "product_card_v1"},
            "manifest": {"headline": "Premium Display", "cta": "Learn More"},
        }
        product = self._make_base_product(product_card=card)
        dump = product.model_dump()
        assert "product_card" in dump
        assert dump["product_card"]["manifest"]["headline"] == "Premium Display"

        json_dump = product.model_dump(mode="json")
        assert json_dump["product_card"]["format_id"]["id"] == "product_card_v1"

    # --- product_card_detailed (optional, default=None) ---

    def test_product_card_detailed_absent_when_null(self):
        """product_card_detailed not in model_dump when not set."""
        product = self._make_base_product()
        dump = product.model_dump()
        assert "product_card_detailed" not in dump

    def test_product_card_detailed_present_when_set(self):
        """product_card_detailed appears in model_dump with correct structure."""
        card = {
            "format_id": {"agent_url": "https://creative.adcontextprotocol.org", "id": "detail_card_v1"},
            "manifest": {"sections": [{"title": "Overview", "body": "Detailed product info"}]},
        }
        product = self._make_base_product(product_card_detailed=card)
        dump = product.model_dump()
        assert "product_card_detailed" in dump
        assert dump["product_card_detailed"]["manifest"]["sections"][0]["title"] == "Overview"

    # --- placements (optional, default=None) ---

    def test_placements_absent_when_null(self):
        """placements not in model_dump when not set."""
        product = self._make_base_product()
        dump = product.model_dump()
        assert "placements" not in dump

    def test_placements_present_when_set(self):
        """placements appears in model_dump with correct Placement structure."""
        placements = [
            {"placement_id": "top_banner", "name": "Top Banner", "description": "Above the fold"},
            {
                "placement_id": "sidebar",
                "name": "Sidebar",
                "format_ids": [{"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"}],
            },
        ]
        product = self._make_base_product(placements=placements)
        dump = product.model_dump()
        assert "placements" in dump
        assert len(dump["placements"]) == 2
        assert dump["placements"][0]["placement_id"] == "top_banner"
        assert dump["placements"][0]["name"] == "Top Banner"
        assert dump["placements"][1]["placement_id"] == "sidebar"

    # --- reporting_capabilities (optional, default=None) ---

    def test_reporting_capabilities_absent_when_null(self):
        """reporting_capabilities not in model_dump when not set."""
        product = self._make_base_product()
        dump = product.model_dump()
        assert "reporting_capabilities" not in dump

    def test_reporting_capabilities_present_when_set(self):
        """reporting_capabilities appears in model_dump with correct structure."""
        rc = {
            "available_metrics": ["impressions", "clicks", "spend"],
            "available_reporting_frequencies": ["daily", "hourly"],
            "date_range_support": "date_range",
            "expected_delay_minutes": 120,
            "supports_webhooks": True,
            "timezone": "America/New_York",
        }
        product = self._make_base_product(reporting_capabilities=rc)
        dump = product.model_dump()
        assert "reporting_capabilities" in dump
        assert dump["reporting_capabilities"]["expected_delay_minutes"] == 120
        assert dump["reporting_capabilities"]["supports_webhooks"] is True
        assert dump["reporting_capabilities"]["timezone"] == "America/New_York"

        # JSON mode should serialize enums to strings
        json_dump = product.model_dump(mode="json")
        assert "impressions" in json_dump["reporting_capabilities"]["available_metrics"]
        assert json_dump["reporting_capabilities"]["date_range_support"] == "date_range"

    # --- catalog_match (optional, default=None) ---

    def test_catalog_match_absent_when_null(self):
        """catalog_match not in model_dump when not set."""
        product = self._make_base_product()
        dump = product.model_dump()
        assert "catalog_match" not in dump

    def test_catalog_match_present_when_set(self):
        """catalog_match appears in model_dump with correct CatalogMatch structure."""
        cm = {"submitted_count": 500, "matched_count": 420, "matched_ids": ["sku_001", "sku_002"]}
        product = self._make_base_product(catalog_match=cm)
        dump = product.model_dump()
        assert "catalog_match" in dump
        assert dump["catalog_match"]["submitted_count"] == 500
        assert dump["catalog_match"]["matched_count"] == 420
        assert dump["catalog_match"]["matched_ids"] == ["sku_001", "sku_002"]

    # --- catalog_types (optional, default=None) ---

    def test_catalog_types_absent_when_null(self):
        """catalog_types not in model_dump when not set."""
        product = self._make_base_product()
        dump = product.model_dump()
        assert "catalog_types" not in dump

    def test_catalog_types_present_when_set(self):
        """catalog_types appears in model_dump with CatalogType enum values."""
        product = self._make_base_product(catalog_types=["offering", "product", "store"])
        dump = product.model_dump()
        assert "catalog_types" in dump
        assert len(dump["catalog_types"]) == 3

        # JSON mode should serialize enums to strings
        json_dump = product.model_dump(mode="json")
        assert json_dump["catalog_types"] == ["offering", "product", "store"]

    # --- conversion_tracking (optional, default=None) ---

    def test_conversion_tracking_absent_when_null(self):
        """conversion_tracking not in model_dump when not set."""
        product = self._make_base_product()
        dump = product.model_dump()
        assert "conversion_tracking" not in dump

    def test_conversion_tracking_present_when_set(self):
        """conversion_tracking appears in model_dump with correct structure."""
        ct = {"platform_managed": True, "action_sources": ["website", "app"]}
        product = self._make_base_product(conversion_tracking=ct)
        dump = product.model_dump()
        assert "conversion_tracking" in dump
        assert dump["conversion_tracking"]["platform_managed"] is True

        json_dump = product.model_dump(mode="json")
        assert "website" in json_dump["conversion_tracking"]["action_sources"]

    # --- data_provider_signals (optional, default=None) ---

    def test_data_provider_signals_absent_when_null(self):
        """data_provider_signals not in model_dump when not set."""
        product = self._make_base_product()
        dump = product.model_dump()
        assert "data_provider_signals" not in dump

    def test_data_provider_signals_present_when_set(self):
        """data_provider_signals appears in model_dump with discriminated union structure."""
        dps = [
            {"selection_type": "all", "data_provider_domain": "acmedata.com"},
            {"selection_type": "by_id", "data_provider_domain": "betadata.com", "signal_ids": ["sig_001", "sig_002"]},
        ]
        product = self._make_base_product(data_provider_signals=dps)
        dump = product.model_dump()
        assert "data_provider_signals" in dump
        assert len(dump["data_provider_signals"]) == 2

        json_dump = product.model_dump(mode="json")
        assert json_dump["data_provider_signals"][0]["selection_type"] == "all"
        assert json_dump["data_provider_signals"][0]["data_provider_domain"] == "acmedata.com"
        assert json_dump["data_provider_signals"][1]["selection_type"] == "by_id"

    # --- forecast (optional, default=None) ---

    def test_forecast_absent_when_null(self):
        """forecast not in model_dump when not set."""
        product = self._make_base_product()
        dump = product.model_dump()
        assert "forecast" not in dump

    def test_forecast_present_when_set(self):
        """forecast appears in model_dump with correct DeliveryForecast structure."""
        fc = {
            "method": "estimate",
            "currency": "USD",
            "points": [
                {"budget": 1000.0, "metrics": {"impressions": {"mid": 50000.0, "low": 40000.0, "high": 60000.0}}},
                {"budget": 5000.0, "metrics": {"impressions": {"mid": 250000.0}}},
            ],
        }
        product = self._make_base_product(forecast=fc)
        dump = product.model_dump()
        assert "forecast" in dump
        assert dump["forecast"]["currency"] == "USD"
        assert len(dump["forecast"]["points"]) == 2
        assert dump["forecast"]["points"][0]["budget"] == 1000.0
        assert dump["forecast"]["points"][0]["metrics"]["impressions"]["mid"] == 50000.0

        json_dump = product.model_dump(mode="json")
        assert json_dump["forecast"]["method"] == "estimate"

    # --- Roundtrip: DB model -> product_conversion -> schema -> model_dump ---

    def test_v36_fields_roundtrip_conversion(self):
        """Roundtrip: mock DB model -> convert_product_model_to_schema -> model_dump produces valid AdCP JSON."""
        from unittest.mock import MagicMock

        from src.core.product_conversion import convert_product_model_to_schema

        m = MagicMock()
        m.product_id = "rt_v36"
        m.name = "Roundtrip V36"
        m.description = "Roundtrip test with all v3.6 fields"
        m.delivery_type = "guaranteed"
        m.effective_format_ids = [{"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"}]
        m.effective_properties = [
            {"selection_type": "by_tag", "publisher_domain": "test.com", "property_tags": ["all_inventory"]}
        ]
        m.delivery_measurement = {"provider": "pub_direct", "notes": "Publisher direct measurement"}
        m.measurement = None
        m.creative_policy = None
        m.countries = None
        m.channels = ["display", "olv"]
        m.product_card = {
            "format_id": {"agent_url": "https://creative.adcontextprotocol.org", "id": "card"},
            "manifest": {"headline": "Test"},
        }
        m.product_card_detailed = {
            "format_id": {"agent_url": "https://creative.adcontextprotocol.org", "id": "detail"},
            "manifest": {"body": "Details"},
        }
        m.placements = [{"placement_id": "top", "name": "Top Banner"}]
        m.reporting_capabilities = {
            "available_metrics": ["impressions", "clicks"],
            "available_reporting_frequencies": ["daily"],
            "date_range_support": "date_range",
            "expected_delay_minutes": 30,
            "supports_webhooks": False,
            "timezone": "UTC",
        }
        m.is_custom = False
        m.property_targeting_allowed = True
        m.signal_targeting_allowed = True
        m.catalog_match = {"submitted_count": 100, "matched_count": 80}
        m.catalog_types = ["offering", "product"]
        m.conversion_tracking = {"platform_managed": True}
        m.data_provider_signals = [{"selection_type": "all", "data_provider_domain": "data.example.com"}]
        m.forecast = {
            "method": "estimate",
            "currency": "USD",
            "points": [{"budget": 2000.0, "metrics": {"impressions": {"mid": 100000.0}}}],
        }
        m.effective_implementation_config = None
        m.allowed_principal_ids = None

        # Mock pricing option
        po = MagicMock()
        po.pricing_model = "cpm"
        po.currency = "USD"
        po.fixed_price = 10.0
        po.floor_price = None
        po.price_guidance = None
        po.min_spend_per_package = None
        po.parameters = None
        po.pricing_option_id = "cpm_usd"
        m.pricing_options = [po]

        # Convert and serialize
        schema = convert_product_model_to_schema(m)
        dump = schema.model_dump()
        json_dump = schema.model_dump(mode="json")

        # Verify all v3.6 fields survived the roundtrip
        assert dump["property_targeting_allowed"] is True
        assert dump["signal_targeting_allowed"] is True
        assert len(dump["channels"]) == 2
        assert dump["product_card"]["manifest"]["headline"] == "Test"
        assert dump["product_card_detailed"]["manifest"]["body"] == "Details"
        assert dump["placements"][0]["placement_id"] == "top"
        assert dump["reporting_capabilities"]["expected_delay_minutes"] == 30
        assert dump["catalog_match"]["submitted_count"] == 100
        assert len(dump["catalog_types"]) == 2
        assert dump["conversion_tracking"]["platform_managed"] is True
        assert dump["data_provider_signals"][0]["data_provider_domain"] == "data.example.com"
        assert dump["forecast"]["currency"] == "USD"
        assert dump["forecast"]["points"][0]["metrics"]["impressions"]["mid"] == 100000.0

        # Verify JSON serialization produces strings for enums
        assert json_dump["forecast"]["method"] == "estimate"
        assert json_dump["channels"] == ["display", "olv"]
        assert json_dump["catalog_types"] == ["offering", "product"]

        # Verify internal fields are excluded
        assert "implementation_config" not in dump
        assert "countries" not in dump
        assert "allowed_principal_ids" not in dump

    def test_v36_fields_roundtrip_null_omission(self):
        """Roundtrip: DB model with null v3.6 fields -> model_dump omits them."""
        from unittest.mock import MagicMock

        from src.core.product_conversion import convert_product_model_to_schema

        m = MagicMock()
        m.product_id = "rt_null"
        m.name = "Roundtrip Null"
        m.description = "Roundtrip test with null v3.6 fields"
        m.delivery_type = "non_guaranteed"
        m.effective_format_ids = [{"agent_url": "https://creative.adcontextprotocol.org", "id": "video_15s"}]
        m.effective_properties = [
            {"selection_type": "by_tag", "publisher_domain": "test.com", "property_tags": ["all"]}
        ]
        m.delivery_measurement = {"provider": "publisher"}
        m.measurement = None
        m.creative_policy = None
        m.countries = None
        m.channels = None
        m.product_card = None
        m.product_card_detailed = None
        m.placements = None
        m.reporting_capabilities = None
        m.is_custom = False
        m.property_targeting_allowed = None
        m.signal_targeting_allowed = None
        m.catalog_match = None
        m.catalog_types = None
        m.conversion_tracking = None
        m.data_provider_signals = None
        m.forecast = None
        m.effective_implementation_config = None
        m.allowed_principal_ids = None

        po = MagicMock()
        po.pricing_model = "cpm"
        po.currency = "USD"
        po.fixed_price = None
        po.floor_price = 2.0
        po.price_guidance = {"p75": 5.0}
        po.min_spend_per_package = None
        po.parameters = None
        po.pricing_option_id = "cpm_usd_auction"
        m.pricing_options = [po]

        schema = convert_product_model_to_schema(m)
        dump = schema.model_dump()

        # None-valued optional fields should be omitted from dump
        absent_fields = [
            "channels",
            "product_card",
            "product_card_detailed",
            "placements",
            "reporting_capabilities",
            "catalog_match",
            "catalog_types",
            "conversion_tracking",
            "data_provider_signals",
            "forecast",
        ]
        for field in absent_fields:
            assert field not in dump, f"Null field '{field}' should not appear in model_dump"

    def test_v36_product_in_get_products_response(self):
        """Product with v3.6 fields serializes correctly inside GetProductsResponse."""
        product = self._make_base_product(
            channels=["display"],
            signal_targeting_allowed=True,
            property_targeting_allowed=True,
            catalog_types=["offering"],
        )

        response = GetProductsResponse(products=[product])
        response_dict = response.model_dump()

        product_data = response_dict["products"][0]
        assert product_data["signal_targeting_allowed"] is True
        assert product_data["property_targeting_allowed"] is True
        assert product_data["channels"] is not None
        assert product_data["catalog_types"] is not None

        # Internal fields must still be excluded
        assert "implementation_config" not in product_data
        assert "countries" not in product_data


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
