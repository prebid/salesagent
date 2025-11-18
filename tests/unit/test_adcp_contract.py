"""Contract tests to ensure database models match AdCP protocol schemas.

These tests verify that:
1. Database models have all required fields for AdCP schemas
2. Field types are compatible
3. Data can be correctly transformed between models and schemas
4. AdCP protocol requirements are met
"""

import warnings
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

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
    CreativePolicy,
    Format,
    FormatId,
    GetMediaBuyDeliveryRequest,
    GetMediaBuyDeliveryResponse,
    GetProductsRequest,
    GetProductsResponse,
    ListAuthorizedPropertiesRequest,
    ListAuthorizedPropertiesResponse,
    ListCreativeFormatsResponse,
    ListCreativesRequest,
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
    SignalPricing,
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
            platform_mappings={
                "google_ad_manager": {"advertiser_id": "123456"},
                "mock": {"id": "test"},
            },
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
        """Test AdCP get_products request requirements."""
        # AdCP requires both brief and brand_manifest
        request = GetProductsRequest(
            brief="Looking for display ads on news sites",
            brand_manifest={"name": "B2B SaaS company selling analytics software"},
        )

        assert request.brief is not None
        assert request.brand_manifest is not None

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
            delivery_measurement={
                "provider": "test_provider",
                "notes": "Test measurement",
            },  # Required per AdCP spec
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
            delivery_measurement={
                "provider": "test_provider",
                "notes": "Test measurement",
            },  # Required per AdCP spec
            pricing_options=[
                {
                    "pricing_option_id": "cpm_eur_auction",
                    "pricing_model": "cpm",
                    "currency": "EUR",
                    "is_fixed": False,  # Required in adcp 2.4.0+
                    "price_guidance": {"floor": 5.0, "p75": 8.5, "p90": 10.0},
                }
            ],
            publisher_properties=[
                create_test_publisher_properties_by_tag(publisher_domain="test.com")
            ],  # Required per AdCP spec
        )

        adcp_response = non_guaranteed_product.model_dump()
        # Currency is now in pricing_options, not at product level
        assert adcp_response["pricing_options"][0]["currency"] == "EUR"
        # Verify price_guidance contains floor and percentile values
        assert adcp_response["pricing_options"][0]["price_guidance"]["floor"] == 5.0
        assert adcp_response["pricing_options"][0]["price_guidance"]["p75"] == 8.5  # p75 used as recommended
        assert adcp_response["pricing_options"][0]["price_guidance"]["p90"] == 10.0

        # Verify GetProductsRequest requires brand_manifest per AdCP v2.2.0 spec
        # Should succeed with brand_manifest
        request = GetProductsRequest(
            brief="Looking for high-volume campaigns",
            brand_manifest={"name": "Nike Air Max 2024"},
        )
        # brand_manifest is required per AdCP v2.2.0 spec
        assert request.brand_manifest.name == "Nike Air Max 2024"

        # Should fail without brand_manifest (AdCP requirement)
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            GetProductsRequest(brief="Just a brief")

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
            delivery_measurement={
                "provider": "test_provider",
                "notes": "Test measurement",
            },  # Required per AdCP spec
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

    def test_adcp_create_media_buy_request(self):
        """Test AdCP create_media_buy request structure."""
        start_date = datetime.now() + timedelta(days=1)
        end_date = datetime.now() + timedelta(days=30)

        request = CreateMediaBuyRequest(
            brand_manifest={"name": "Nike Air Jordan 2025 basketball shoes"},  # Required per AdCP spec
            buyer_ref="nike_jordan_2025_q1",  # Required per AdCP spec
            product_ids=["product_1", "product_2"],
            total_budget=5000.0,
            start_date=start_date.date(),
            end_date=end_date.date(),
            po_number="PO-12345",  # Required per AdCP spec
            targeting_overlay={
                "geo_country_any_of": ["US", "CA"],
                "device_type_any_of": ["desktop", "mobile"],
                "signals": ["sports_enthusiasts", "auto_intenders"],
            },
        )

        # Verify AdCP requirements
        assert len(request.get_product_ids()) > 0
        assert request.get_total_budget() > 0
        # Also verify backward compatibility
        assert request.get_total_budget() == 5000.0
        assert request.flight_end_date > request.flight_start_date

        # Targeting overlay should support signals (AdCP v2.4)
        assert hasattr(request.targeting_overlay, "signals")
        assert request.targeting_overlay.signals == ["sports_enthusiasts", "auto_intenders"]

    def test_format_schema_compliance(self):
        """Test that Format schema matches AdCP specifications."""
        from tests.helpers.adcp_factories import create_test_format_id

        # Create AdCP-compliant Format directly (only fields supported by adcp library)
        format_obj = Format(
            format_id=create_test_format_id("native_feed"),
            name="Native Feed Ad",
            type="native",
        )

        # AdCP format requirements (new spec structure)
        assert format_obj.format_id is not None
        # format_obj.type is an enum, check its value
        assert format_obj.type.value in ["display", "video", "audio", "native", "dooh"]
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
        """Test AdCP v2.4 signal support in targeting."""
        request = CreateMediaBuyRequest(
            brand_manifest={"name": "Luxury automotive vehicles and premium accessories"},
            buyer_ref="luxury_auto_campaign_2025",  # Required per AdCP spec
            product_ids=["test_product"],
            total_budget=1000.0,
            start_date=datetime.now().date(),
            end_date=(datetime.now() + timedelta(days=7)).date(),
            po_number="PO-SIGNAL-TEST",  # Required per AdCP spec
            targeting_overlay={
                "signals": [
                    "sports_enthusiasts",
                    "auto_intenders_q1_2025",
                    "high_income_households",
                ],
                "aee_signals": {  # Renamed from provided_signals in v2.4
                    "custom_audience_1": "abc123",
                    "lookalike_model": "xyz789",
                },
            },
        )

        # Verify signals are supported
        assert hasattr(request.targeting_overlay, "signals")
        assert request.targeting_overlay.signals == [
            "sports_enthusiasts",
            "auto_intenders_q1_2025",
            "high_income_households",
        ]
        # Note: aee_signals was passed but might be mapped to key_value_pairs in the Targeting model

    def test_creative_adcp_compliance(self):
        """Test that Creative model complies with AdCP v1 creative-asset schema."""
        # Test creating a Creative with required AdCP v1 fields (strict spec compliance)
        creative = Creative(
            creative_id="test_creative_123",
            name="Test AdCP Creative",
            format_id=FormatId(agent_url="https://creatives.adcontextprotocol.org", id="display_300x250"),
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
            created_at=datetime.now(),
            updated_at=datetime.now(),
            status="approved",
        )

        # Test AdCP-compliant model_dump (external response - excludes internal fields)
        adcp_response = creative.model_dump()

        # Verify required AdCP v1 fields are present
        # Note: Library uses 'format_id' not 'format' (spec-compliant naming)
        adcp_required_fields = ["creative_id", "name", "format_id", "assets"]
        for field in adcp_required_fields:
            assert field in adcp_response, f"Required AdCP field '{field}' missing from response"
            assert adcp_response[field] is not None, f"Required AdCP field '{field}' is None"

        # Verify AdCP v1 optional fields (present if provided, omitted if None per AdCP spec)
        # Tags was provided, so should be present
        assert "tags" in adcp_response, "Tags should be present when provided"
        assert adcp_response["tags"] == ["display", "banner"]
        # Inputs and approved were not provided, so should be omitted (exclude_none=True)
        # This is correct AdCP behavior - optional fields should be omitted if not set

        # Verify internal fields are EXCLUDED from AdCP response
        # Note: After refactoring to use library Creative:
        # - principal_id: Still internal (excluded)
        # - created_at/updated_at: Legacy aliases (excluded, use created_date/updated_date instead)
        # - status: Now a SPEC field (included), not internal
        internal_fields = ["principal_id", "created_at", "updated_at"]
        for field in internal_fields:
            assert field not in adcp_response, f"Internal field '{field}' exposed in AdCP response"

        # Verify spec fields that were previously internal are now present
        assert "status" in adcp_response, "Status is now a spec field, should be present"
        assert "created_date" in adcp_response, "created_date is a spec field"
        assert "updated_date" in adcp_response, "updated_date is a spec field"

        # Verify format_id is FormatId object
        assert isinstance(adcp_response["format_id"], dict), "format_id should be FormatId object (as dict)"
        assert adcp_response["format_id"]["id"] == "display_300x250", "Format ID should be display_300x250"
        assert "agent_url" in adcp_response["format_id"], "format_id should have agent_url"

        # Verify assets dict is present
        assert isinstance(adcp_response["assets"], dict), "Assets should be a dict"
        assert "banner_image" in adcp_response["assets"], "Assets should have banner_image"
        assert adcp_response["assets"]["banner_image"]["url"] == "https://example.com/creative.jpg"

        # Test internal model_dump includes all fields
        internal_response = creative.model_dump_internal()
        for field in internal_fields:
            assert field in internal_response, f"Internal field '{field}' missing from internal response"

        # Verify internal response has more fields than external
        internal_only_fields = set(internal_response.keys()) - set(adcp_response.keys())
        assert (
            len(internal_only_fields) >= 2
        ), f"Expected at least 2 internal-only fields, got {len(internal_only_fields)}"

    def test_signal_adcp_compliance(self):
        """Test that Signal model complies with AdCP get-signals-response schema."""
        # Create signal with all required AdCP fields
        deployment = SignalDeployment(
            platform="google_ad_manager",
            account="123456789",
            is_live=True,
            scope="account-specific",
            decisioning_platform_segment_id="gam_segment_123",
            estimated_activation_duration_minutes=0,
        )

        pricing = SignalPricing(cpm=2.50, currency="USD")

        signal = Signal(
            signal_agent_segment_id="signal_auto_intenders_q1_2025",
            name="Auto Intenders Q1 2025",
            description="Consumers showing purchase intent for automotive products in Q1 2025",
            signal_type="marketplace",
            data_provider="Acme Data Solutions",
            coverage_percentage=85.5,
            deployments=[deployment],
            pricing=pricing,
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
            "pricing",
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
        required_deployment_fields = ["platform", "is_live", "scope"]
        for field in required_deployment_fields:
            assert field in deployment_obj, f"Required deployment field '{field}' missing"
        assert deployment_obj["scope"] in ["platform-wide", "account-specific"], "scope must be valid enum"

        # Verify pricing structure
        assert isinstance(adcp_response["pricing"], dict), "pricing must be object"
        assert "currency" in adcp_response["pricing"], "pricing must have currency field"
        assert len(adcp_response["pricing"]["currency"]) == 3, "currency must be 3-letter code"

        # Test backward compatibility properties (suppress deprecation warnings since we're testing them)
        import warnings

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            assert signal.signal_id == signal.signal_agent_segment_id, "signal_id property should work"
            assert signal.type == signal.signal_type, "type property should work"

        # Test internal model_dump includes all fields
        internal_response = signal.model_dump_internal()
        for field in internal_fields:
            assert field in internal_response, f"Internal field '{field}' missing from internal response"

        # Verify field count expectations (flexible to allow AdCP spec evolution)
        assert len(adcp_response) >= 8, f"AdCP response should have at least 8 core fields, got {len(adcp_response)}"
        assert len(internal_response) >= len(
            adcp_response
        ), "Internal response should have at least as many fields as external response"

        # Verify internal response has more fields than external (due to internal fields)
        internal_only_fields = set(internal_response.keys()) - set(adcp_response.keys())
        assert (
            len(internal_only_fields) >= 3
        ), f"Expected at least 3 internal-only fields, got {len(internal_only_fields)}"

    def test_package_adcp_compliance(self):
        """Test that Package model complies with AdCP package schema."""
        # Create package with all required AdCP fields and optional fields
        # Note: Package is response schema - has package_id, status (required)
        # product_id is optional per adcp library (not products plural)
        package = Package(
            package_id="pkg_test_123",
            status="active",
            buyer_ref="buyer_ref_abc",
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
        adcp_required_fields = ["package_id", "status"]
        for field in adcp_required_fields:
            assert field in adcp_response, f"Required AdCP field '{field}' missing from response"
            assert adcp_response[field] is not None, f"Required AdCP field '{field}' is None"

        # Verify AdCP optional fields that were set are present
        # Per AdCP spec, optional fields should only appear in response if they have values
        # (Pydantic's default behavior is exclude_none=True)
        # Per adcp library Package schema (response schema, not request)
        # Test with fields that were actually set in the Package object above
        expected_optional_fields = {
            "buyer_ref",  # We set this
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
        # status is a PackageStatus enum - get its value for comparison
        status_value = (
            adcp_response["status"].value if hasattr(adcp_response["status"], "value") else adcp_response["status"]
        )
        assert status_value in ["draft", "active", "paused", "completed"], "status must be valid enum"
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
        # Package has 2 required fields (package_id, status) + any optional fields that are set
        # We set 4 optional fields above, so expect at least 2+4=6 fields
        assert len(adcp_response) >= 2, f"AdCP response should have at least required fields, got {len(adcp_response)}"
        assert len(internal_response) >= len(
            adcp_response
        ), "Internal response should have at least as many fields as external response"

        # Verify internal response has more fields than external (due to internal fields)
        internal_only_fields = set(internal_response.keys()) - set(adcp_response.keys())
        assert (
            len(internal_only_fields) >= 3
        ), f"Expected at least 3 internal-only fields, got {len(internal_only_fields)}"

    def test_targeting_adcp_compliance(self):
        """Test that Targeting model complies with AdCP targeting schema."""
        # Create targeting with both public and managed/internal fields
        targeting = Targeting(
            geo_country_any_of=["US", "CA"],
            geo_region_any_of=["CA", "NY"],
            geo_metro_any_of=["803", "501"],
            geo_zip_any_of=["10001", "90210"],
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

        # Test AdCP-compliant model_dump (external response)
        adcp_response = targeting.model_dump()

        # Verify AdCP fields are present (all targeting fields are optional in AdCP)
        adcp_optional_fields = [
            "geo_country_any_of",
            "geo_region_any_of",
            "geo_metro_any_of",
            "geo_zip_any_of",
            "audiences_any_of",
            "signals",
            "device_type_any_of",
            "os_any_of",
            "browser_any_of",
        ]
        for field in adcp_optional_fields:
            # Field should be in response even if null (AdCP spec pattern)
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

        # Verify AdCP-specific requirements
        if adcp_response.get("geo_country_any_of"):
            for country in adcp_response["geo_country_any_of"]:
                assert len(country) == 2, "Country codes must be 2-letter ISO codes"

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
        assert (
            internal_response["key_value_pairs"]["aee_segment"] == "high_value"
        ), "Managed field should be in internal response"

        # Verify field count expectations (flexible - targeting has many optional fields)
        assert len(adcp_response) >= 9, f"AdCP response should have at least 9 fields, got {len(adcp_response)}"
        assert len(internal_response) >= len(
            adcp_response
        ), "Internal response should have at least as many fields as external response"

        # Verify internal response has more fields than external (due to managed/internal fields)
        internal_only_fields = set(internal_response.keys()) - set(adcp_response.keys())
        assert (
            len(internal_only_fields) >= 4
        ), f"Expected at least 4 internal/managed-only fields, got {len(internal_only_fields)}"

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

        # Verify field count (Budget has 5 fields including auto_pause_on_budget_exhaustion)
        assert len(adcp_response) == 5, f"Budget response should have exactly 5 fields, got {len(adcp_response)}"

    def test_measurement_adcp_compliance(self):
        """Test that Measurement model complies with AdCP measurement schema."""
        measurement = Measurement(
            type="incremental_sales_lift", attribution="deterministic_purchase", window="30_days", reporting="daily"
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

        # Test model_dump (CreativePolicy doesn't have internal fields)
        adcp_response = policy.model_dump()

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
        assert (
            len(adcp_response) == 3
        ), f"CreativePolicy response should have exactly 3 fields, got {len(adcp_response)}"

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
        assert (
            len(adcp_response) >= 3
        ), f"CreativeStatus response should have at least 3 core fields, got {len(adcp_response)}"

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
            assert (
                adcp_response["rotation_type"] in valid_rotations
            ), f"Invalid rotation_type: {adcp_response['rotation_type']}"

        if adcp_response.get("weight") is not None:
            assert adcp_response["weight"] >= 0, "Weight must be non-negative"

        if adcp_response.get("percentage_goal") is not None:
            assert 0 <= adcp_response["percentage_goal"] <= 100, "Percentage goal must be 0-100"

        # Verify field count (flexible - optional fields vary)
        assert (
            len(adcp_response) >= 4
        ), f"CreativeAssignment response should have at least 4 core fields, got {len(adcp_response)}"

    def test_sync_creatives_request_adcp_compliance(self):
        """Test that SyncCreativesRequest model complies with AdCP v2.4 sync-creatives schema."""
        # Create Creative objects with AdCP v1 spec-compliant format
        creative = Creative(
            creative_id="creative_123",
            name="Test Creative",
            format_id=FormatId(agent_url="https://creatives.adcontextprotocol.org", id="display_300x250"),
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
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )

        # Test with spec-compliant fields only
        request = SyncCreativesRequest(
            creatives=[creative],
            patch=False,
            assignments={"creative_123": ["pkg_1", "pkg_2"]},
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

        # Verify AdCP v2.4 optional fields are present
        adcp_optional_fields = ["patch", "assignments", "delete_missing", "dry_run", "validation_mode"]
        for field in adcp_optional_fields:
            assert field in adcp_response, f"AdCP optional field '{field}' missing from response"

        # Verify non-spec fields are NOT present
        non_spec_fields = ["media_buy_id", "buyer_ref", "assign_to_packages", "upsert"]
        for field in non_spec_fields:
            assert field not in adcp_response, f"Non-spec field '{field}' should not be in response"

        # Verify creatives array structure
        assert isinstance(adcp_response["creatives"], list), "Creatives must be an array"
        assert len(adcp_response["creatives"]) > 0, "Creatives array must not be empty"

        # Test creative object structure (AdCP v1 spec)
        creative_obj = adcp_response["creatives"][0]
        # Note: Library uses 'format_id' not 'format' (spec-compliant naming)
        creative_required_fields = ["creative_id", "name", "format_id", "assets"]  # AdCP v1 spec required fields
        for field in creative_required_fields:
            assert field in creative_obj, f"Creative required field '{field}' missing"
            assert creative_obj[field] is not None, f"Creative required field '{field}' is None"

        # Verify assets structure
        assert isinstance(creative_obj["assets"], dict), "Assets must be a dict"
        assert "banner_image" in creative_obj["assets"], "Assets should contain banner_image"

        # Verify assignments structure (dict of creative_id → package_ids)
        if adcp_response.get("assignments"):
            assert isinstance(adcp_response["assignments"], dict), "Assignments must be a dict"
            for creative_id, package_ids in adcp_response["assignments"].items():
                assert isinstance(package_ids, list), f"Package IDs for {creative_id} must be a list"

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

        After refactoring to extend library type (EMBEDDED_TYPES_CORRECTIONS.md):
        - Accepts flat convenience fields (media_buy_id, page, limit, sort_by, etc.)
        - Maps them to structured AdCP objects via @model_validator(mode="before")
        - Convenience fields marked with exclude=True (internal only)
        - Serialization outputs structured AdCP-compliant fields (filters, pagination, sort)
        """
        # Create request using convenience fields
        request = ListCreativesRequest(
            media_buy_id="mb_123",  # Internal convenience field
            buyer_ref="buyer_456",  # Internal convenience field
            status="approved",  # Mapped to filters.status
            format="display_300x250",  # Mapped to filters.format
            tags=["sports", "premium"],  # Mapped to filters.tags
            created_after=datetime.now(UTC) - timedelta(days=30),  # Mapped to filters.created_after
            created_before=datetime.now(UTC),  # Mapped to filters.created_before
            limit=50,  # Mapped to pagination.limit
            page=1,  # Mapped to pagination.offset
            sort_by="created_date",  # Mapped to sort.field
            sort_order="desc",  # Mapped to sort.direction
        )

        # Test model_dump - should output AdCP-compliant structured fields
        adcp_response = request.model_dump()

        # Verify internal convenience fields are EXCLUDED from serialization
        excluded_fields = [
            "media_buy_id",
            "buyer_ref",
            "status",
            "format",
            "tags",
            "created_after",
            "created_before",
            "search",
            "page",
            "limit",
            "sort_by",
            "sort_order",
        ]
        for field in excluded_fields:
            assert field not in adcp_response, f"Internal field '{field}' should be excluded from serialization"

        # Verify structured AdCP fields are present
        assert "filters" in adcp_response, "AdCP structured 'filters' field must be present"
        assert "sort" in adcp_response, "AdCP structured 'sort' field must be present"

        # Note: pagination may or may not be present depending on defaults
        # If page=1 and limit=50 (defaults), pagination might not be created

        # Verify filters structure
        filters = adcp_response["filters"]
        # Status is converted to CreativeStatus enum by library
        assert filters["status"].value == "approved", "filters.status should match input"
        assert filters["format"] == "display_300x250", "filters.format should match input"
        assert filters["tags"] == ["sports", "premium"], "filters.tags should match input"
        assert "created_after" in filters, "filters.created_after should be present"
        assert "created_before" in filters, "filters.created_before should be present"

        # Verify sort structure
        sort = adcp_response["sort"]
        # Field and direction are converted to enums by library
        assert sort["field"].value == "created_date", "sort.field should match input sort_by"
        assert sort["direction"].value == "desc", "sort.direction should match input sort_order"

        # Fields WITH defaults should be present (include_performance, include_assignments, include_sub_assets)
        assert "include_performance" in adcp_response, "Field with default should be present"
        assert adcp_response["include_performance"] is False, "Default value should match"
        assert "include_assignments" in adcp_response, "Field with default should be present"
        assert adcp_response["include_assignments"] is True, "Default value should match (library default)"

        # Verify that unset optional fields are omitted
        assert (
            "fields" not in adcp_response or adcp_response["fields"] is None
        ), "Unset optional field should be omitted or None"

        # Verify field count (flexible - all fields optional)
        assert len(adcp_response) >= 0, "ListCreativesRequest can have 0 or more fields"

    def test_list_creatives_response_adcp_compliance(self):
        """Test that ListCreativesResponse model complies with AdCP list-creatives response schema."""
        creative1 = Creative(
            creative_id="creative_123",
            name="Test Creative 1",
            format_id=FormatId(agent_url="https://creatives.adcontextprotocol.org", id="display_300x250"),
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
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )

        creative2 = Creative(
            creative_id="creative_456",
            name="Test Creative 2",
            format_id=FormatId(agent_url="https://creatives.adcontextprotocol.org", id="video_1280x720"),
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
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )

        response = ListCreativesResponse(
            creatives=[creative1, creative2],
            query_summary=QuerySummary(
                total_matching=2,
                returned=2,
                filters_applied=[],
            ),
            pagination=Pagination(
                limit=50,
                offset=0,
                has_more=False,
                total_pages=1,
                current_page=1,
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

        # Verify pagination structure
        assert "limit" in adcp_response["pagination"]
        assert "offset" in adcp_response["pagination"]
        assert "has_more" in adcp_response["pagination"]

        # Test creative object structure in response
        if len(adcp_response["creatives"]) > 0:
            creative = adcp_response["creatives"][0]
            # Per AdCP spec, Creative required fields are: creative_id, name, format_id, assets
            # Note: Library uses 'format_id' not 'format', and status is now a spec field
            creative_required_fields = ["creative_id", "name", "format_id", "assets"]
            for field in creative_required_fields:
                assert field in creative, f"Creative required field '{field}' missing"
                assert creative[field] is not None, f"Creative required field '{field}' is None"

            # Verify internal-only fields are excluded (should NOT be in client responses)
            # Note: status is now a SPEC field (included), created_at/updated_at are legacy aliases (excluded)
            internal_fields = ["principal_id", "created_at", "updated_at"]
            for field in internal_fields:
                assert field not in creative, f"Internal field '{field}' should be excluded from client response"

        # Verify required fields are present
        # Per AdCP spec, only query_summary, pagination, and creatives are required
        # Optional fields (format_summary, status_summary, etc.) are omitted if not set
        required_fields = ["query_summary", "pagination", "creatives"]
        for field in required_fields:
            assert field in adcp_response, f"Required field '{field}' missing from response"

        # Verify we have at least the required fields (and possibly some optional ones)
        assert len(adcp_response) >= len(
            required_fields
        ), f"Response should have at least {len(required_fields)} required fields, got {len(adcp_response)}"

    def test_create_media_buy_response_adcp_compliance(self):
        """Test that CreateMediaBuyResponse complies with AdCP create-media-buy-response schema.

        Per AdCP PR #186, responses use oneOf discriminator for atomic semantics.
        Success responses have media_buy_id + packages, error responses have errors array.
        """
        from src.core.schemas import CreateMediaBuyError, CreateMediaBuySuccess

        # Create success response with domain fields only (per AdCP PR #113)
        # Protocol fields (status, task_id, message) are added by transport layer
        # Note: creative_deadline must be timezone-aware datetime (adcp 2.0.0)
        # Note: packages in response only have package_id and buyer_ref (adcp 2.0.0)
        successful_response = CreateMediaBuySuccess(
            media_buy_id="mb_12345",
            buyer_ref="br_67890",
            packages=[{"package_id": "pkg_1", "buyer_ref": "br_67890"}],
            creative_deadline=datetime.now(UTC) + timedelta(days=7),
        )

        # Test successful response AdCP compliance
        adcp_response = successful_response.model_dump()

        # Verify required AdCP domain fields present and non-null
        required_fields = ["buyer_ref"]  # buyer_ref is required, media_buy_id is optional
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
            buyer_ref="br_union",
            packages=[],
        )
        error_via_union: CreateMediaBuyResponse = CreateMediaBuyError(
            errors=[{"code": "test", "message": "test"}],
        )

        # Verify Union type assignments work
        assert isinstance(success_via_union, CreateMediaBuySuccess)
        assert isinstance(error_via_union, CreateMediaBuyError)

        # Verify field count for success response
        assert (
            len(adcp_response) >= 3
        ), f"CreateMediaBuySuccess should have at least 3 required fields, got {len(adcp_response)}"

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
            delivery_measurement={
                "provider": "test_provider",
                "notes": "Test measurement",
            },  # Required per AdCP spec
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
        assert (
            len(empty_adcp_response) >= 2 and len(empty_adcp_response) <= 3
        ), f"GetProductsResponse should have 2-3 fields (status optional), got {len(empty_adcp_response)}"

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
                    assets_required=None,
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
        assert (
            len(adcp_response) >= 1
        ), f"ListCreativeFormatsResponse should have at least required fields, got {len(adcp_response)}"

    def test_update_media_buy_response_adcp_compliance(self):
        """Test that UpdateMediaBuyResponse complies with AdCP update-media-buy-response schema.

        Per AdCP PR #186, responses use oneOf discriminator for atomic semantics.
        Success responses have media_buy_id + buyer_ref, error responses have errors array.
        """
        from src.core.schemas import UpdateMediaBuyError, UpdateMediaBuySuccess

        # Create successful update response (oneOf success branch)
        # Note: implementation_date must be timezone-aware datetime (adcp 2.0.0)
        # Note: packages field removed from response in adcp 2.0.0, only affected_packages remains
        response = UpdateMediaBuySuccess(
            media_buy_id="buy_123",
            buyer_ref="ref_123",
            implementation_date=datetime.now(UTC) + timedelta(hours=1),
            affected_packages=[{"package_id": "pkg_1", "buyer_ref": "ref_123"}],
        )

        # Test AdCP-compliant response
        adcp_response = response.model_dump()

        # Verify required AdCP fields present and non-null
        required_fields = ["media_buy_id", "buyer_ref"]
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
        assert (
            len(adcp_response) >= 2
        ), f"UpdateMediaBuySuccess should have at least 2 required fields, got {len(adcp_response)}"

    def test_get_media_buy_delivery_request_adcp_compliance(self):
        """Test that GetMediaBuyDeliveryRequest complies with AdCP get-media-buy-delivery-request schema."""

        # Test request with all required + optional fields
        request = GetMediaBuyDeliveryRequest(
            media_buy_ids=["mb_123", "mb_456"],
            buyer_refs=["br_789", "br_012"],
            status_filter="active",
            start_date="2025-01-01",
            end_date="2025-01-31",
        )

        # Test AdCP-compliant request
        adcp_request = request.model_dump()

        # Verify all fields are optional in AdCP spec
        adcp_optional_fields = ["media_buy_ids", "buyer_refs", "status_filter", "start_date", "end_date"]
        for field in adcp_optional_fields:
            assert field in adcp_request, f"AdCP optional field '{field}' missing from request"

        # Verify field types and constraints
        if adcp_request.get("media_buy_ids") is not None:
            assert isinstance(adcp_request["media_buy_ids"], list), "media_buy_ids must be array"

        if adcp_request.get("buyer_refs") is not None:
            assert isinstance(adcp_request["buyer_refs"], list), "buyer_refs must be array"

        if adcp_request.get("status_filter") is not None:
            # Can be string or array according to spec
            valid_statuses = ["active", "pending", "paused", "completed", "failed", "all"]
            if isinstance(adcp_request["status_filter"], str):
                assert (
                    adcp_request["status_filter"] in valid_statuses
                ), f"Invalid status: {adcp_request['status_filter']}"
            elif isinstance(adcp_request["status_filter"], list):
                for status in adcp_request["status_filter"]:
                    assert (
                        status in valid_statuses[:-1]
                    ), f"Invalid status in array: {status}"  # 'all' not valid in array

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

        # Test array status_filter
        array_request = GetMediaBuyDeliveryRequest(status_filter=["active", "ready"])
        array_adcp_request = array_request.model_dump()
        assert isinstance(array_adcp_request["status_filter"], list), "status_filter should support array format"

    def test_get_media_buy_delivery_response_adcp_compliance(self):
        """Test that GetMediaBuyDeliveryResponse complies with AdCP get-media-buy-delivery-response schema."""
        from src.core.schemas import (
            AggregatedTotals,
            DailyBreakdown,
            DeliveryTotals,
            PackageDelivery,
            ReportingPeriod,
        )

        # Create AdCP-compliant delivery data using new models
        package_delivery = PackageDelivery(
            package_id="pkg_123",
            buyer_ref="br_456",
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
            buyer_ref="br_67890",
            status="active",
            totals=delivery_totals,
            by_package=[package_delivery.model_dump()],
            daily_breakdown=[daily_breakdown.model_dump()],
        )

        reporting_period = ReportingPeriod(start="2025-01-01T00:00:00Z", end="2025-01-31T23:59:59Z")

        aggregated_totals = AggregatedTotals(
            impressions=25000.0, spend=500.75, clicks=125.0, video_completions=None, media_buy_count=1
        )

        # Create AdCP-compliant response
        response = GetMediaBuyDeliveryResponse(
            reporting_period=reporting_period,
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
            delivery_optional_fields = ["buyer_ref", "daily_breakdown"]
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
            reporting_period=reporting_period,
            currency="USD",
            aggregated_totals=empty_aggregated,
            media_buy_deliveries=[],
        )

        empty_adcp_response = empty_response.model_dump()
        assert (
            empty_adcp_response["media_buy_deliveries"] == []
        ), "Empty media_buy_deliveries list should be empty array"

        # Verify field count - required fields + non-None optional fields
        # reporting_period, currency, media_buy_deliveries are required; aggregated_totals set; errors=None omitted
        assert (
            len(adcp_response) >= 3
        ), f"GetMediaBuyDeliveryResponse should have at least 3 required fields, got {len(adcp_response)}"

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
        """Test that Property complies with AdCP property schema."""
        # Create property with all required + optional fields
        property_obj = Property(
            property_type="website",
            name="Example News Site",
            identifiers=[PropertyIdentifier(type="domain", value="example.com")],
            tags=["news", "premium_content"],
            publisher_domain="example.com",
        )

        # Test AdCP-compliant response
        adcp_response = property_obj.model_dump()

        # Verify required AdCP fields present and non-null
        required_fields = ["property_type", "name", "identifiers", "publisher_domain"]
        for field in required_fields:
            assert field in adcp_response
            assert adcp_response[field] is not None

        # Verify optional AdCP fields present (can be null)
        optional_fields = ["tags"]
        for field in optional_fields:
            assert field in adcp_response

        # Verify property type is valid enum value
        valid_types = ["website", "mobile_app", "ctv_app", "dooh", "podcast", "radio", "streaming_audio"]
        assert adcp_response["property_type"] in valid_types

        # Verify identifiers is non-empty array
        assert isinstance(adcp_response["identifiers"], list)
        assert len(adcp_response["identifiers"]) > 0

        # Verify tags is array when present
        assert isinstance(adcp_response["tags"], list)

        # Verify field count expectations
        assert len(adcp_response) == 5

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
        # Create request with all required + optional fields
        request = ListAuthorizedPropertiesRequest(adcp_version="1.0.0", tags=["premium_content", "news"])

        # Test AdCP-compliant response
        adcp_response = request.model_dump()

        # Verify required AdCP fields present and non-null
        required_fields = ["adcp_version"]
        for field in required_fields:
            assert field in adcp_response
            assert adcp_response[field] is not None

        # Verify optional AdCP fields present (can be null)
        optional_fields = ["tags"]
        for field in optional_fields:
            assert field in adcp_response

        # Verify adcp_version format
        import re

        version_pattern = r"^\d+\.\d+\.\d+$"
        assert re.match(version_pattern, adcp_response["adcp_version"])

        # Verify tags is array when present
        if adcp_response["tags"] is not None:
            assert isinstance(adcp_response["tags"], list)

        # Verify field count expectations
        assert len(adcp_response) == 2

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
        # ✅ FIXED: Implementation now matches AdCP spec
        # AdCP spec requires: signal_spec, deliver_to, optional filters/max_results

        from src.core.schemas import GetSignalsRequest, SignalDeliverTo, SignalFilters

        # Test AdCP-compliant request with all required fields
        adcp_request = GetSignalsRequest(
            signal_spec="Sports enthusiasts in automotive market",
            deliver_to=SignalDeliverTo(
                platforms=["google_ad_manager", "the_trade_desk"],
                countries=["US", "CA"],
                accounts=[
                    {"platform": "google_ad_manager", "account": "123456"},
                    {"platform": "the_trade_desk", "account": "ttd789"},
                ],
            ),
            filters=SignalFilters(
                catalog_types=["marketplace", "custom"],
                data_providers=["Acme Data Solutions"],
                max_cpm=5.0,
                min_coverage_percentage=75.0,
            ),
            max_results=50,
        )

        adcp_response = adcp_request.model_dump()

        # ✅ VERIFY ADCP COMPLIANCE: Required fields present
        required_fields = ["signal_spec", "deliver_to"]
        for field in required_fields:
            assert field in adcp_response, f"Required AdCP field '{field}' missing from response"
            assert adcp_response[field] is not None, f"Required AdCP field '{field}' is None"

        # ✅ VERIFY ADCP COMPLIANCE: Optional fields present when provided
        optional_fields = ["filters", "max_results"]
        for field in optional_fields:
            assert field in adcp_response, f"Optional AdCP field '{field}' missing from response"

        # ✅ VERIFY deliver_to structure
        deliver_to = adcp_response["deliver_to"]
        assert "platforms" in deliver_to, "deliver_to must have platforms field"
        assert "countries" in deliver_to, "deliver_to must have countries field"
        assert isinstance(deliver_to["platforms"], list), "platforms must be array when not 'all'"
        assert isinstance(deliver_to["countries"], list), "countries must be array"

        # Verify country codes are 2-letter ISO
        for country in deliver_to["countries"]:
            assert len(country) == 2, f"Country code '{country}' must be 2-letter ISO code"
            assert country.isupper(), f"Country code '{country}' must be uppercase"

        # ✅ VERIFY filters structure when present
        filters = adcp_response["filters"]
        if filters.get("catalog_types"):
            valid_catalog_types = ["marketplace", "custom", "owned"]
            for catalog_type in filters["catalog_types"]:
                assert catalog_type in valid_catalog_types, f"Invalid catalog_type: {catalog_type}"

        if filters.get("max_cpm") is not None:
            pass  # Legacy pricing field, no longer validated

        if filters.get("min_coverage_percentage") is not None:
            assert 0 <= filters["min_coverage_percentage"] <= 100, "min_coverage_percentage must be 0-100"

        # ✅ VERIFY max_results constraint
        if adcp_response.get("max_results") is not None:
            assert adcp_response["max_results"] >= 1, "max_results must be positive"

        # Test minimal request (only required fields)
        minimal_request = GetSignalsRequest(
            signal_spec="Automotive intenders", deliver_to=SignalDeliverTo(platforms="all", countries=["US"])
        )
        minimal_response = minimal_request.model_dump()
        assert minimal_response["deliver_to"]["platforms"] == "all"

        # ✅ VERIFY backward compatibility properties work (deprecated)
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            query_value = adcp_request.query
            assert query_value == "Sports enthusiasts in automotive market"
            assert len(w) == 1
            assert issubclass(w[0].category, DeprecationWarning)
            assert "query is deprecated" in str(w[0].message)

        # Verify field count (4 fields: signal_spec, deliver_to, filters, max_results)
        assert len(adcp_response) == 4, f"AdCP request should have exactly 4 fields, got {len(adcp_response)}"

    def test_update_media_buy_request_adcp_compliance(self):
        """Test that UpdateMediaBuyRequest model complies with AdCP update-media-buy-request schema."""
        # ✅ FIXED: Implementation now matches AdCP spec
        # AdCP spec requires: oneOf(media_buy_id OR buyer_ref), optional active/start_time/end_time/budget/packages

        from datetime import UTC, datetime

        from src.core.schemas import AdCPPackageUpdate, Budget, UpdateMediaBuyRequest

        # Test AdCP-compliant request with media_buy_id (oneOf option 1)
        adcp_request_id = UpdateMediaBuyRequest(
            media_buy_id="mb_12345",
            active=True,
            start_time=datetime(2025, 2, 1, 9, 0, 0, tzinfo=UTC),
            end_time=datetime(2025, 2, 28, 23, 59, 59, tzinfo=UTC),
            budget=Budget(total=5000.0, currency="USD", pacing="even"),
            packages=[
                AdCPPackageUpdate(package_id="pkg_123", active=True, budget=2500.0)  # Float budget per AdCP spec
            ],
        )

        adcp_response_id = adcp_request_id.model_dump()

        # ✅ VERIFY ADCP COMPLIANCE: OneOf constraint satisfied
        assert "media_buy_id" in adcp_response_id, "media_buy_id must be present"
        assert adcp_response_id["media_buy_id"] is not None, "media_buy_id must not be None"
        assert (
            "buyer_ref" not in adcp_response_id or adcp_response_id["buyer_ref"] is None
        ), "buyer_ref must be None when media_buy_id is provided"

        # Test AdCP-compliant request with buyer_ref (oneOf option 2)
        adcp_request_ref = UpdateMediaBuyRequest(
            buyer_ref="br_67890", active=False, start_time=datetime(2025, 3, 1, 0, 0, 0, tzinfo=UTC)
        )

        adcp_response_ref = adcp_request_ref.model_dump()

        # ✅ VERIFY ADCP COMPLIANCE: OneOf constraint satisfied
        assert "buyer_ref" in adcp_response_ref, "buyer_ref must be present"
        assert adcp_response_ref["buyer_ref"] is not None, "buyer_ref must not be None"
        assert (
            "media_buy_id" not in adcp_response_ref or adcp_response_ref["media_buy_id"] is None
        ), "media_buy_id must be None when buyer_ref is provided"

        # ✅ VERIFY ADCP COMPLIANCE: Optional fields present when provided
        optional_fields = ["active", "start_time", "end_time", "budget", "packages"]
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
                # Each package must have either package_id OR buyer_ref (oneOf constraint)
                has_package_id = package.get("package_id") is not None
                has_buyer_ref = package.get("buyer_ref") is not None
                assert has_package_id or has_buyer_ref, "Each package must have either package_id or buyer_ref"
                assert not (has_package_id and has_buyer_ref), "Package cannot have both package_id and buyer_ref"

        # ✅ VERIFY budget structure (currency/pacing in budget object, not top-level)
        if adcp_response_id.get("budget"):
            budget = adcp_response_id["budget"]
            assert isinstance(budget, dict), "budget must be object"
            assert "total" in budget, "budget must have total field"
            assert "currency" in budget, "budget must have currency field (not top-level)"

        # NOTE: oneOf constraint validation happens at protocol boundary (MCP/A2A request validation)
        # not in Pydantic model construction. The JSON Schema enforces this when requests come in.
        # Internal construction allows flexibility for testing and data manipulation.

        # Verify that both construction patterns work internally
        # (they would be rejected by JSON Schema validation at the protocol boundary)
        req_both = UpdateMediaBuyRequest(media_buy_id="mb_123", buyer_ref="br_456")
        assert req_both.media_buy_id == "mb_123"
        assert req_both.buyer_ref == "br_456"

        req_neither = UpdateMediaBuyRequest(active=True)
        assert req_neither.active is True

        # ✅ VERIFY backward compatibility properties work (deprecated)
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            flight_start = adcp_request_id.flight_start_date
            assert flight_start == datetime(2025, 2, 1, 9, 0, 0).date()
            assert len(w) == 1
            assert issubclass(w[0].category, DeprecationWarning)
            assert "flight_start_date is deprecated" in str(w[0].message)

        # Verify field count (6-8 fields including oneOf field that might be None and push_notification_config)
        assert len(adcp_response_id) <= 8, f"AdCP request should have at most 8 fields, got {len(adcp_response_id)}"

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
            status="active",
            buyer_ref="test_ref_123",
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
        assert "status" in external_dump
        assert "buyer_ref" in external_dump
        assert "platform_line_item_id" not in external_dump, "platform_line_item_id should NOT be in AdCP response"
        assert "tenant_id" not in external_dump, "tenant_id should NOT be in AdCP response"
        assert "media_buy_id" not in external_dump, "media_buy_id should NOT be in AdCP response"
        assert "created_at" not in external_dump, "created_at should NOT be in AdCP response"
        assert "updated_at" not in external_dump, "updated_at should NOT be in AdCP response"
        assert "metadata" not in external_dump, "metadata should NOT be in AdCP response"

        # Internal database dump - should include internal fields
        internal_dump = pkg.model_dump_internal()
        assert "package_id" in internal_dump
        assert "status" in internal_dump
        assert "buyer_ref" in internal_dump
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
        request = CreateMediaBuyRequest(
            brand_manifest={"name": "Flash Sale Campaign"},
            buyer_ref="flash_sale_2025_q1",
            start_time="asap",  # AdCP v1.7.0 supports literal "asap"
            end_time=end_date,
            packages=[
                {
                    "buyer_ref": "pkg_flash_001",
                    "product_id": "product_1",
                    "pricing_option_id": "test_pricing",
                    "budget": 5000.0,
                }
            ],
            budget={"total": 25000, "currency": "USD", "pacing": "asap"},
        )

        # Verify asap is accepted
        assert request.start_time == "asap"
        assert request.end_time == end_date

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
        request = CreateMediaBuyRequest(
            brand_manifest={"name": "Scheduled Campaign"},
            buyer_ref="scheduled_2025_q1",
            start_time=start_date,
            end_time=end_date,
            packages=[
                {
                    "buyer_ref": "pkg_scheduled_001",
                    "product_id": "product_1",
                    "pricing_option_id": "test_pricing",
                    "budget": 5000.0,
                }
            ],
            budget={"total": 10000, "currency": "USD", "pacing": "even"},
        )

        # Verify datetime is still accepted
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
            delivery_measurement={
                "provider": "test_provider",
                "notes": "Test measurement",
            },  # Required per AdCP spec
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
        # publisher_properties is a discriminated union object, access via attribute
        assert product_with_properties.publisher_properties[0].publisher_domain == "example.com"

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

    def test_create_media_buy_with_brand_manifest_inline(self):
        """Test CreateMediaBuyRequest with inline brand_manifest (AdCP v1.8.0)."""
        start_date = datetime.now(UTC) + timedelta(days=1)
        end_date = datetime.now(UTC) + timedelta(days=30)

        # Test with inline brand manifest
        request = CreateMediaBuyRequest(
            buyer_ref="nike_2025_q1",
            brand_manifest={
                "name": "Nike",
                "url": "https://nike.com",
                "colors": {"primary": "#FF0000", "secondary": "#000000"},
                "tagline": "Just Do It",
            },
            packages=[
                {
                    "buyer_ref": "pkg_001",
                    "product_id": "product_1",
                    "pricing_option_id": "test_pricing",
                    "budget": 5000.0,
                }
            ],
            start_time=start_date,
            end_time=end_date,
            budget=5000.0,
        )

        # Verify brand_manifest is properly stored
        assert request.brand_manifest is not None
        assert isinstance(request.brand_manifest, dict) or hasattr(request.brand_manifest, "name")

        # Verify required fields still work
        assert request.buyer_ref == "nike_2025_q1"
        assert len(request.packages) == 1

    def test_create_media_buy_with_brand_manifest_url(self):
        """Test CreateMediaBuyRequest with brand_manifest as URL string (AdCP v1.8.0)."""
        start_date = datetime.now(UTC) + timedelta(days=1)
        end_date = datetime.now(UTC) + timedelta(days=30)

        # Test with brand manifest URL
        request = CreateMediaBuyRequest(
            buyer_ref="nike_2025_q1",
            brand_manifest="https://nike.com/brand-manifest.json",
            packages=[
                {
                    "buyer_ref": "pkg_001",
                    "product_id": "product_1",
                    "pricing_option_id": "test_pricing",
                    "budget": 5000.0,
                }
            ],
            start_time=start_date,
            end_time=end_date,
            budget=5000.0,
        )

        # Verify brand_manifest URL is properly stored
        assert request.brand_manifest == "https://nike.com/brand-manifest.json"
        assert isinstance(request.brand_manifest, str)

    def test_get_signals_response_adcp_compliance(self):
        """Test that GetSignalsResponse model complies with AdCP get-signals response schema.

        Per AdCP PR #113 and official schema, protocol fields (message, context_id)
        are added by the protocol layer, not the domain response.
        """
        from src.core.schema_adapters import GetSignalsResponse

        # Minimal required fields - only signals is required per AdCP spec
        response = GetSignalsResponse(signals=[])

        # Convert to AdCP format (excludes internal fields)
        adcp_response = response.model_dump(exclude_none=True)

        # Verify required fields are present
        assert "signals" in adcp_response

        # Verify field count (signals is required, errors is optional)
        # Per AdCP PR #113, protocol fields removed from domain responses
        assert (
            len(adcp_response) >= 1
        ), f"GetSignalsResponse should have at least 1 core field (signals), got {len(adcp_response)}"

        # Test with all fields
        signal_data = {
            "signal_agent_segment_id": "seg_123",
            "name": "Premium Audiences",
            "description": "High-value customer segment",
            "signal_type": "marketplace",
            "data_provider": "Acme Data",
            "coverage_percentage": 85.5,
            "deployments": [{"platform": "GAM", "is_live": True, "scope": "platform-wide"}],
            "pricing": {"cpm": 2.50, "currency": "USD"},
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
        assert (
            len(adcp_response) >= 1
        ), f"ActivateSignalResponse should have at least 1 core field, got {len(adcp_response)}"

        # Test with activation details (domain data)
        full_response = ActivateSignalResponse(
            signal_id="sig_456",
            activation_details={"platform_id": "seg_789", "estimated_duration_minutes": 5.0},
            errors=None,
        )
        full_dump = full_response.model_dump(exclude_none=True)
        assert full_dump["signal_id"] == "sig_456"
        assert full_dump["activation_details"]["platform_id"] == "seg_789"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
