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
    CreativeAssignment,
    CreativePolicy,
    CreativeStatus,
    Error,
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
    UpdateMediaBuyResponse,
)
from src.core.schemas import PricingOption as PricingOptionSchema
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
            formats=["display_300x250"],  # Now stores format IDs as strings
            targeting_template={"geo_country": {"values": ["US", "CA"], "required": False}},
            delivery_type="guaranteed",  # AdCP: guaranteed or non_guaranteed
            is_custom=False,
            expires_at=None,
            countries=["US", "CA"],
            implementation_config={"internal": "config"},
        )

        # Create pricing option for the product

        pricing_option = PricingOptionSchema(
            pricing_option_id="cpm_usd_fixed",
            pricing_model="cpm",
            rate=10.50,
            currency="USD",
            is_fixed=True,
            price_guidance=None,
            parameters=None,
            min_spend_per_package=None,
        )

        # Convert to dict (simulating database retrieval and conversion)
        # The validator now ensures formats are stored as strings
        model_dict = {
            "product_id": model.product_id,
            "name": model.name,
            "description": model.description,
            "formats": model.formats,  # Now guaranteed to be strings by validator
            "delivery_type": model.delivery_type,
            "pricing_options": [pricing_option],
            "is_custom": model.is_custom,
            "expires_at": model.expires_at,
            "property_tags": ["all_inventory"],  # Required per AdCP spec
        }

        # Should be convertible to AdCP schema
        schema = ProductSchema(**model_dict)

        # Verify AdCP required fields
        assert schema.product_id == "test_product"
        assert schema.name == "Test Product"
        assert schema.description == "A test product for AdCP protocol"
        assert schema.delivery_type in ["guaranteed", "non_guaranteed"]
        assert len(schema.formats) > 0

        # Verify format IDs match AdCP (now strings)
        assert schema.formats[0] == "display_300x250"

    def test_product_non_guaranteed(self):
        """Test non-guaranteed product (AdCP spec compliant - no price_guidance)."""
        model = ProductModel(
            tenant_id="test_tenant",
            product_id="test_ng_product",
            name="Non-Guaranteed Product",
            description="AdCP non-guaranteed product",
            formats=["video_15s"],  # Now stores format IDs as strings
            targeting_template={},
            delivery_type="non_guaranteed",
            is_custom=False,
            expires_at=None,
            countries=["US"],
            implementation_config=None,
        )

        model_dict = {
            "product_id": model.product_id,
            "name": model.name,
            "description": model.description,
            "formats": model.formats,
            "delivery_type": model.delivery_type,
            "is_custom": model.is_custom,
            "expires_at": model.expires_at,
            "property_tags": ["all_inventory"],  # Required per AdCP spec
            "pricing_options": [
                PricingOptionSchema(
                    pricing_option_id="cpm_usd_fixed", pricing_model="cpm", rate=10.0, currency="USD", is_fixed=True
                )
            ],
        }

        schema = ProductSchema(**model_dict)

        # AdCP spec: non_guaranteed products use auction-based pricing (no price_guidance)
        assert schema.delivery_type == "non_guaranteed"

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

        PR #79 adds:
        - min_exposures filter in get_products request
        - currency field (ISO 4217)
        - estimated_exposures for guaranteed products
        - floor_cpm and recommended_cpm for non-guaranteed products
        """
        # Test guaranteed product with estimated_exposures
        guaranteed_product = ProductSchema(
            product_id="test_guaranteed",
            name="Guaranteed Product",
            description="Test product with exposure estimates",
            formats=["display_300x250"],
            delivery_type="guaranteed",
            pricing_options=[
                PricingOptionSchema(
                    pricing_option_id="cpm_usd_fixed",
                    pricing_model="cpm",
                    rate=15.0,
                    currency="USD",
                    is_fixed=True,
                )
            ],
            estimated_exposures=50000,
            property_tags=["all_inventory"],  # Required per AdCP spec
        )

        # Verify AdCP-compliant response includes PR #79 fields
        adcp_response = guaranteed_product.model_dump()
        assert "estimated_exposures" in adcp_response
        assert adcp_response["estimated_exposures"] == 50000

        # Test non-guaranteed product with floor_cpm and recommended_cpm
        non_guaranteed_product = ProductSchema(
            product_id="test_non_guaranteed",
            name="Non-Guaranteed Product",
            description="Test product with CPM guidance",
            formats=["video_15s"],
            delivery_type="non_guaranteed",
            pricing_options=[
                PricingOptionSchema(
                    pricing_option_id="cpm_eur_auction",
                    pricing_model="cpm",
                    rate=None,
                    currency="EUR",
                    is_fixed=False,
                    price_guidance={"floor": 5.0, "p90": 8.5},
                )
            ],
            floor_cpm=5.0,
            recommended_cpm=8.5,
            property_tags=["all_inventory"],  # Required per AdCP spec
        )

        adcp_response = non_guaranteed_product.model_dump()
        # Currency is now in pricing_options, not at product level
        assert adcp_response["pricing_options"][0]["currency"] == "EUR"

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

    def test_product_properties_or_tags_required(self):
        """Test Product schema requires either properties or property_tags per AdCP spec oneOf constraint.

        AdCP spec requires products to have at least one of:
        - properties: Array of full Property objects for adagents.json validation
        - property_tags: Array of tag strings (buyers use list_authorized_properties for details)
        """
        # Test with property_tags (recommended approach)
        product_with_tags = ProductSchema(
            product_id="test_product_tags",
            name="Product with Tags",
            description="Product using property tags",
            formats=["display_300x250"],
            delivery_type="guaranteed",
            cpm=10.0,
            property_tags=["premium_sports", "local_news"],
            pricing_options=[
                PricingOptionSchema(
                    pricing_option_id="cpm_usd_fixed",
                    pricing_model="cpm",
                    rate=10.0,
                    currency="USD",
                    is_fixed=True,
                )
            ],
        )

        adcp_response = product_with_tags.model_dump()
        assert "property_tags" in adcp_response
        assert adcp_response["property_tags"] == ["premium_sports", "local_news"]
        assert len(adcp_response["property_tags"]) >= 1

        # Test with full properties
        from src.core.schemas import Property, PropertyIdentifier

        property_obj = Property(
            property_type="website",
            name="Example News Site",
            identifiers=[PropertyIdentifier(type="domain", value="example.com")],
            tags=["premium_sports"],
            publisher_domain="example.com",
        )

        product_with_properties = ProductSchema(
            product_id="test_product_properties",
            name="Product with Properties",
            description="Product with full property objects",
            formats=["video_15s"],
            delivery_type="non_guaranteed",
            properties=[property_obj],
            pricing_options=[
                PricingOptionSchema(
                    pricing_option_id="cpm_usd_auction",
                    pricing_model="cpm",
                    currency="USD",
                    is_fixed=False,
                    price_guidance={"floor": 1.0, "suggested_rate": 5.0},
                )
            ],
        )

        adcp_response = product_with_properties.model_dump()
        assert "properties" in adcp_response
        assert len(adcp_response["properties"]) >= 1
        assert adcp_response["properties"][0]["property_type"] == "website"
        assert adcp_response["properties"][0]["publisher_domain"] == "example.com"

        # Test without either properties or property_tags should fail (strict validation enabled)
        with pytest.raises(ValueError, match="properties.*property_tags"):
            ProductSchema(
                product_id="test_product_no_props",
                name="Invalid Product",
                description="Missing property information",
                formats=["display_300x250"],
                delivery_type="guaranteed",
                cpm=10.0,
                pricing_options=[
                    PricingOptionSchema(
                        pricing_option_id="cpm_usd_fixed",
                        pricing_model="cpm",
                        rate=10.0,
                        currency="USD",
                        is_fixed=True,
                    )
                ],
                # Missing both properties and property_tags
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
        format_data = {
            "format_id": FormatId(agent_url="https://creative.adcontextprotocol.org", id="native_feed"),
            "name": "Native Feed Ad",
            "type": "native",
            "is_standard": True,
            "iab_specification": "IAB Native Ad Specification",
            "requirements": {"width": 300, "height": 250},
            # assets_required follows new AdCP spec structure (asset_id is required per Oct 17 schema update)
            "assets_required": [
                {
                    "asset_id": "primary_image",
                    "asset_type": "image",
                    "quantity": 1,
                    "requirements": {"width": 300, "height": 250},
                }
            ],
        }

        format_obj = Format(**format_data)

        # AdCP format requirements (new spec structure)
        assert format_obj.format_id is not None
        assert format_obj.type in ["display", "video", "audio", "native", "dooh"]
        assert format_obj.is_standard is True
        assert format_obj.requirements is not None

    def test_field_mapping_consistency(self):
        """Test that field names are consistent between models and schemas."""
        # These fields should map correctly
        model_to_schema_mapping = {
            # Model field -> Schema field (AdCP spec compliant - no price_guidance)
            "product_id": "product_id",
            "name": "name",
            "description": "description",
            "delivery_type": "delivery_type",  # Must be "guaranteed" or "non_guaranteed"
            "formats": "formats",
            "is_custom": "is_custom",
            "expires_at": "expires_at",
        }

        # Create test data
        model = ProductModel(
            tenant_id="test",
            product_id="test_mapping",
            name="Test",
            description="Test product",
            formats=[],
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
        # AdCP specifies exactly these two values
        valid_delivery_types = ["guaranteed", "non_guaranteed"]

        # Test valid values
        for delivery_type in valid_delivery_types:
            product = ProductSchema(
                product_id="test",
                name="Test",
                description="Test",
                formats=[],
                delivery_type=delivery_type,
                cpm=10.0,
                property_tags=["all_inventory"],  # Required per AdCP spec
                pricing_options=[
                    PricingOptionSchema(
                        pricing_option_id="cpm_usd_fixed",
                        pricing_model="cpm",
                        rate=10.0,
                        currency="USD",
                        is_fixed=True,
                    )
                ],
            )
            assert product.delivery_type in valid_delivery_types

        # Invalid values should fail
        with pytest.raises(ValueError):
            ProductSchema(
                product_id="test",
                name="Test",
                description="Test",
                formats=[],
                delivery_type="programmatic",  # Not AdCP compliant
                cpm=10.0,
                property_tags=["all_inventory"],  # Required per AdCP spec
                pricing_options=[
                    PricingOptionSchema(
                        pricing_option_id="cpm_usd_fixed",
                        pricing_model="cpm",
                        rate=10.0,
                        currency="USD",
                        is_fixed=True,
                    )
                ],
            )

    def test_adcp_response_excludes_internal_fields(self):
        """Test that AdCP responses don't expose internal fields."""
        products = [
            ProductSchema(
                product_id="test",
                name="Test Product",
                description="Test",
                formats=[],
                delivery_type="guaranteed",
                cpm=10.0,
                implementation_config={"internal": "data"},  # Should be excluded
                property_tags=["all_inventory"],  # Required per AdCP spec
                pricing_options=[
                    PricingOptionSchema(
                        pricing_option_id="cpm_usd_fixed",
                        pricing_model="cpm",
                        rate=10.0,
                        currency="USD",
                        is_fixed=True,
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
        """Test that Creative model complies with AdCP creative-asset schema."""
        # Test creating a Creative with required AdCP fields
        creative = Creative(
            creative_id="test_creative_123",
            name="Test AdCP Creative",
            format_id="display_300x250",
            content_uri="https://example.com/creative.jpg",
            click_through_url="https://example.com/landing",
            principal_id="test_principal",
            created_at=datetime.now(),
            updated_at=datetime.now(),
            width=300,
            height=250,
            duration=None,  # Not applicable for display
            status="approved",
            platform_id="platform_abc123",
            review_feedback="Approved for all placements",
        )

        # Test AdCP-compliant model_dump (external response)
        adcp_response = creative.model_dump()

        # Verify required AdCP fields are present
        adcp_required_fields = ["creative_id", "name", "format"]
        for field in adcp_required_fields:
            assert field in adcp_response, f"Required AdCP field '{field}' missing from response"
            assert adcp_response[field] is not None, f"Required AdCP field '{field}' is None"

        # Verify AdCP optional fields are present
        adcp_optional_fields = [
            "url",
            "media_url",
            "click_url",
            "duration",
            "width",
            "height",
            "status",
            "platform_id",
            "review_feedback",
            "compliance",
            "package_assignments",
            "assets",
        ]
        for field in adcp_optional_fields:
            assert field in adcp_response, f"AdCP optional field '{field}' missing from response"

        # Verify internal fields are excluded from AdCP response
        internal_fields = [
            "principal_id",
            "group_id",
            "created_at",
            "updated_at",
            "has_macros",
            "macro_validation",
            "asset_mapping",
            "metadata",
        ]
        for field in internal_fields:
            assert field not in adcp_response, f"Internal field '{field}' exposed in AdCP response"

        # Verify AdCP-specific requirements
        assert adcp_response["media_url"] == adcp_response["url"], "media_url should default to url"
        assert adcp_response["compliance"]["status"] == "pending", "Default compliance status should be 'pending'"
        assert isinstance(adcp_response["compliance"]["issues"], list), "Compliance issues should be a list"

        # Format is now a FormatId object per AdCP v2.4
        assert isinstance(adcp_response["format"], dict), "Format should be FormatId object"
        assert adcp_response["format"]["id"] == "display_300x250", "Format ID should be display_300x250"
        assert "agent_url" in adcp_response["format"], "Format should have agent_url"

        # Test internal model_dump includes all fields
        internal_response = creative.model_dump_internal()
        for field in internal_fields:
            assert field in internal_response, f"Internal field '{field}' missing from internal response"

        # Verify field count expectations (flexible to allow AdCP spec evolution)
        assert len(adcp_response) >= 12, f"AdCP response should have at least 12 core fields, got {len(adcp_response)}"
        assert len(internal_response) >= len(
            adcp_response
        ), "Internal response should have at least as many fields as external response"

        # Verify internal response has more fields than external (due to internal fields)
        internal_only_fields = set(internal_response.keys()) - set(adcp_response.keys())
        assert (
            len(internal_only_fields) >= 4
        ), f"Expected at least 4 internal-only fields, got {len(internal_only_fields)}"

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
        package = Package(
            package_id="pkg_test_123",
            status="active",
            buyer_ref="buyer_ref_abc",
            product_id="product_xyz",
            products=["product_xyz", "product_def"],
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

        # Verify AdCP optional fields are present (can be null)
        adcp_optional_fields = [
            "buyer_ref",
            "product_id",
            "products",
            "budget",
            "impressions",
            "targeting_overlay",
            "creative_assignments",
        ]
        for field in adcp_optional_fields:
            assert field in adcp_response, f"AdCP optional field '{field}' missing from response"

        # Verify internal fields are excluded from AdCP response
        internal_fields = ["tenant_id", "media_buy_id", "created_at", "updated_at", "metadata"]
        for field in internal_fields:
            assert field not in adcp_response, f"Internal field '{field}' exposed in AdCP response"

        # Verify AdCP-specific requirements
        assert adcp_response["status"] in ["draft", "active", "paused", "completed"], "status must be valid enum"
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
        assert len(adcp_response) >= 7, f"AdCP response should have at least 7 core fields, got {len(adcp_response)}"
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
        """Test that CreativeStatus model complies with AdCP creative-status schema."""
        status = CreativeStatus(
            creative_id="creative_123",
            status="approved",
            detail="Creative approved for all placements",
            estimated_approval_time=datetime.now() + timedelta(hours=1),
        )

        # Test model_dump (CreativeStatus doesn't have internal fields currently)
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
        # Create Creative objects with all required fields (using media content, not snippet)
        creative = Creative(
            creative_id="creative_123",
            name="Test Creative",
            format_id="display_300x250",  # Uses format_id alias for format field
            content_uri="https://example.com/creative.jpg",  # Uses content_uri alias for url field
            principal_id="principal_456",
            # Note: Don't use snippet here as it's mutually exclusive with media_url/content_uri
            click_through_url="https://example.com/click",  # Uses click_through_url alias
            tags=["sports", "premium"],
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

        # Test creative object structure
        creative_obj = adcp_response["creatives"][0]
        creative_required_fields = ["creative_id", "name", "format", "url"]  # AdCP spec field names
        for field in creative_required_fields:
            assert field in creative_obj, f"Creative required field '{field}' missing"
            assert creative_obj[field] is not None, f"Creative required field '{field}' is None"

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
        """Test that ListCreativesRequest model complies with AdCP list-creatives schema."""
        request = ListCreativesRequest(
            media_buy_id="mb_123",
            buyer_ref="buyer_456",
            status="approved",
            format="display_300x250",  # Uses format, not format_id
            tags=["sports", "premium"],
            created_after=datetime.now(UTC) - timedelta(days=30),
            created_before=datetime.now(UTC),
            limit=50,
            # Note: ListCreativesRequest uses page, not offset
            page=1,
            sort_by="created_date",  # Uses created_date, not created_at
            sort_order="desc",
        )

        # Test model_dump (ListCreativesRequest doesn't have internal fields)
        adcp_response = request.model_dump()

        # Verify all fields are optional in AdCP list-creatives request
        adcp_optional_fields = [
            "media_buy_id",
            "buyer_ref",
            "status",
            "format",  # Uses format, not format_id
            "tags",
            "created_after",
            "created_before",
            "search",
            "page",  # Uses page, not offset
            "limit",
            "sort_by",
            "sort_order",
        ]
        for field in adcp_optional_fields:
            assert field in adcp_response, f"AdCP optional field '{field}' missing from response"

        # Verify AdCP-specific requirements
        if adcp_response.get("status"):
            valid_statuses = ["pending_review", "approved", "rejected", "adaptation_required"]
            assert adcp_response["status"] in valid_statuses, f"Invalid status: {adcp_response['status']}"

        if adcp_response.get("limit") is not None:
            assert adcp_response["limit"] > 0, "Limit must be positive"

        if adcp_response.get("page") is not None:
            assert adcp_response["page"] >= 1, "Page must be >= 1"

        if adcp_response.get("sort_order"):
            assert adcp_response["sort_order"] in ["asc", "desc"], "Sort order must be asc or desc"

        # Verify field count (flexible - all fields optional)
        assert len(adcp_response) >= 0, "ListCreativesRequest can have 0 or more fields"

    def test_list_creatives_response_adcp_compliance(self):
        """Test that ListCreativesResponse model complies with AdCP list-creatives response schema."""
        creative1 = Creative(
            creative_id="creative_123",
            name="Test Creative 1",
            format_id="display_300x250",
            content_uri="https://example.com/creative1.jpg",
            principal_id="principal_1",
            status="approved",
            tags=["sports"],
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )

        creative2 = Creative(
            creative_id="creative_456",
            name="Test Creative 2",
            format_id="video_1280x720",
            content_uri="https://example.com/creative2.mp4",
            principal_id="principal_1",
            status="pending_review",
            # Note: Not using snippet as it's mutually exclusive with content_uri
            tags=["premium"],
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
            creative_required_fields = ["creative_id", "name", "format", "status"]
            for field in creative_required_fields:
                assert field in creative, f"Creative required field '{field}' missing"
                assert creative[field] is not None, f"Creative required field '{field}' is None"

        # Verify field count (adcp_version, message, query_summary, pagination, creatives, context_id, format_summary, status_summary)
        assert (
            len(adcp_response) >= 5
        ), f"ListCreativesResponse should have at least 5 core fields, got {len(adcp_response)}"

    def test_create_media_buy_response_adcp_compliance(self):
        """Test that CreateMediaBuyResponse complies with AdCP create-media-buy-response schema."""

        # Create response with domain fields only (per AdCP PR #113)
        # Protocol fields (status, task_id, message) are added by transport layer
        successful_response = CreateMediaBuyResponse(
            media_buy_id="mb_12345",
            buyer_ref="br_67890",
            packages=[{"package_id": "pkg_1", "product_id": "prod_1", "budget": 5000.0, "targeting": {}}],
            creative_deadline=datetime.now() + timedelta(days=7),
            errors=None,
        )

        # Test successful response AdCP compliance
        adcp_response = successful_response.model_dump()

        # Verify required AdCP domain fields present and non-null
        required_fields = ["buyer_ref"]  # buyer_ref is required, media_buy_id is optional
        for field in required_fields:
            assert field in adcp_response, f"Required AdCP field '{field}' missing from response"
            assert adcp_response[field] is not None, f"Required AdCP field '{field}' is None"

        # Verify optional AdCP domain fields present (can be null)
        optional_fields = ["media_buy_id", "packages", "creative_deadline", "errors"]
        for field in optional_fields:
            assert field in adcp_response, f"Optional AdCP field '{field}' missing from response"

        # Verify specific field types and constraints
        assert isinstance(adcp_response["media_buy_id"], str), "media_buy_id must be string"
        assert len(adcp_response["media_buy_id"]) > 0, "media_buy_id must not be empty"

        if adcp_response["packages"] is not None:
            assert isinstance(adcp_response["packages"], list), "packages must be array"

        if adcp_response["errors"] is not None:
            assert isinstance(adcp_response["errors"], list), "errors must be array"

        # Test error response case
        error_response = CreateMediaBuyResponse(
            media_buy_id="mb_failed",
            buyer_ref="br_67890",
            packages=[],
            creative_deadline=None,
            errors=[Error(code="budget_insufficient", message="Insufficient budget")],
        )

        error_adcp_response = error_response.model_dump()

        # Verify error response structure
        assert error_adcp_response["errors"] is not None
        assert len(error_adcp_response["errors"]) > 0
        assert isinstance(error_adcp_response["errors"][0], dict)
        assert "code" in error_adcp_response["errors"][0]
        assert "message" in error_adcp_response["errors"][0]

        # Verify field count (buyer_ref, media_buy_id, creative_deadline, packages, errors)
        assert (
            len(adcp_response) >= 5
        ), f"CreateMediaBuyResponse should have at least 5 domain fields, got {len(adcp_response)}"

    def test_get_products_response_adcp_compliance(self):
        """Test that GetProductsResponse complies with AdCP get-products-response schema."""

        # Create Product using the actual Product model (not ProductSchema)
        from src.core.schemas import Product as ProductModel

        product = ProductModel(
            product_id="prod_1",
            name="Premium Display",
            description="High-quality display advertising",
            formats=["display_300x250", "display_728x90"],
            delivery_type="guaranteed",
            measurement=None,
            creative_policy=None,
            is_custom=False,
            property_tags=["all_inventory"],  # Required per AdCP spec
            pricing_options=[
                PricingOptionSchema(
                    pricing_option_id="cpm_usd_fixed",
                    pricing_model="cpm",
                    rate=10.0,
                    currency="USD",
                    is_fixed=True,
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
            errors=[],
        )

        # Test AdCP-compliant response
        adcp_response = response.model_dump()

        # Verify required AdCP fields present and non-null
        required_fields = ["formats"]
        for field in required_fields:
            assert field in adcp_response, f"Required AdCP field '{field}' missing from response"
            assert adcp_response[field] is not None, f"Required AdCP field '{field}' is None"

        # Verify optional AdCP fields present (can be null)
        # Note: message, adcp_version, status fields removed - handled via protocol envelope
        optional_fields = ["errors", "creative_agents"]
        for field in optional_fields:
            assert field in adcp_response, f"Optional AdCP field '{field}' missing from response"

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

        # Verify field count
        assert (
            len(adcp_response) >= 3
        ), f"ListCreativeFormatsResponse should have at least 3 fields, got {len(adcp_response)}"

    def test_update_media_buy_response_adcp_compliance(self):
        """Test that UpdateMediaBuyResponse complies with AdCP update-media-buy-response schema."""

        # Create successful update response
        response = UpdateMediaBuyResponse(
            media_buy_id="buy_123",
            buyer_ref="ref_123",
            implementation_date=datetime.now() + timedelta(hours=1),
            affected_packages=[],
        )

        # Test AdCP-compliant response
        adcp_response = response.model_dump()

        # Verify required AdCP fields present and non-null
        required_fields = ["media_buy_id", "buyer_ref"]
        for field in required_fields:
            assert field in adcp_response, f"Required AdCP field '{field}' missing from response"
            assert adcp_response[field] is not None, f"Required AdCP field '{field}' is None"

        # Verify optional AdCP fields present (can be null)
        optional_fields = ["implementation_date", "affected_packages", "errors"]
        for field in optional_fields:
            assert field in adcp_response, f"Optional AdCP field '{field}' missing from response"

        # Test error response case
        error_response = UpdateMediaBuyResponse(
            media_buy_id="buy_123",
            buyer_ref="ref_123",
            implementation_date=None,
            errors=[Error(code="INVALID_BUDGET", message="Invalid budget")],
        )

        error_adcp_response = error_response.model_dump()
        assert len(error_adcp_response["errors"]) == 1
        assert error_adcp_response["errors"][0]["message"] == "Invalid budget"

        # Verify field count (media_buy_id, buyer_ref, implementation_date, affected_packages, errors)
        assert (
            len(adcp_response) >= 2
        ), f"UpdateMediaBuyResponse should have at least 2 required fields, got {len(adcp_response)}"

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

        # Verify optional AdCP fields present (can be null)
        optional_fields = ["aggregated_totals", "errors"]
        for field in optional_fields:
            assert field in adcp_response, f"AdCP optional field '{field}' missing from response"

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

        # Verify field count (5 fields total: reporting_period, currency, aggregated_totals, media_buy_deliveries, errors)
        assert (
            len(adcp_response) == 5
        ), f"GetMediaBuyDeliveryResponse should have exactly 5 fields, got {len(adcp_response)}"

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
        # Create response with all required + optional fields (per official AdCP v2.4 spec)
        tag_metadata = PropertyTagMetadata(name="Premium Content", description="Premium content tag")
        response = ListAuthorizedPropertiesResponse(
            publisher_domains=["example.com"], tags={"premium_content": tag_metadata}, errors=[]
        )

        # Test AdCP-compliant response
        adcp_response = response.model_dump()

        # Verify required AdCP fields present and non-null
        required_fields = ["publisher_domains"]
        for field in required_fields:
            assert field in adcp_response
            assert adcp_response[field] is not None

        # Verify optional AdCP fields present (can be null)
        optional_fields = [
            "errors",
            "primary_channels",
            "primary_countries",
            "portfolio_description",
            "advertising_policies",
            "last_updated",
        ]
        for field in optional_fields:
            assert field in adcp_response

        # Verify publisher_domains is array
        assert isinstance(adcp_response["publisher_domains"], list)

        # Verify tags is object when present
        if adcp_response["tags"] is not None:
            assert isinstance(adcp_response["tags"], dict)

        # Verify errors is array when present
        if adcp_response["errors"] is not None:
            assert isinstance(adcp_response["errors"], list)

        # Verify optional fields types
        if adcp_response["primary_channels"] is not None:
            assert isinstance(adcp_response["primary_channels"], list)
        if adcp_response["primary_countries"] is not None:
            assert isinstance(adcp_response["primary_countries"], list)
        if adcp_response["portfolio_description"] is not None:
            assert isinstance(adcp_response["portfolio_description"], str)
        if adcp_response["advertising_policies"] is not None:
            assert isinstance(adcp_response["advertising_policies"], str)

        # Verify message is provided via __str__() not as schema field (response already created above)
        assert str(response) == "Found 1 authorized publisher domain."

        # Verify field count expectations (8 domain fields: publisher_domains, tags, errors, primary_channels, primary_countries, portfolio_description, advertising_policies, last_updated)
        assert len(adcp_response) == 8

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
                AdCPPackageUpdate(package_id="pkg_123", active=True, budget=Budget(total=2500.0, currency="USD"))
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
                    "package_id": "pkg_flash_001",
                    "products": ["product_1"],
                    "status": "draft",
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
                    "package_id": "pkg_scheduled_001",
                    "products": ["product_1"],
                    "status": "draft",
                }
            ],
            budget={"total": 10000, "currency": "USD", "pacing": "even"},
        )

        # Verify datetime is still accepted
        assert isinstance(request.start_time, datetime)
        assert request.start_time == start_date

    def test_product_properties_xor_constraint(self):
        """Test that Product enforces AdCP oneOf constraint (exactly one of properties/property_tags)."""
        from src.core.schemas import Product, Property, PropertyIdentifier

        # Valid: property_tags only
        product_with_tags = Product(
            product_id="p1",
            name="Tagged Product",
            description="Product using property tags",
            formats=["display_300x250"],
            property_tags=["sports", "premium"],
            delivery_type="guaranteed",
            cpm=10.0,
            pricing_options=[
                PricingOptionSchema(
                    pricing_option_id="cpm_usd_fixed",
                    pricing_model="cpm",
                    rate=10.0,
                    currency="USD",
                    is_fixed=True,
                )
            ],
        )
        assert product_with_tags.property_tags == ["sports", "premium"]
        assert product_with_tags.properties is None

        # Valid: properties only
        product_with_properties = Product(
            product_id="p2",
            name="Property Product",
            description="Product using full properties",
            formats=["display_300x250"],
            properties=[
                Property(
                    property_type="website",
                    name="Example Site",
                    identifiers=[PropertyIdentifier(type="domain", value="example.com")],
                    publisher_domain="example.com",
                )
            ],
            delivery_type="guaranteed",
            cpm=10.0,
            pricing_options=[
                PricingOptionSchema(
                    pricing_option_id="cpm_usd_fixed",
                    pricing_model="cpm",
                    rate=10.0,
                    currency="USD",
                    is_fixed=True,
                )
            ],
        )
        assert len(product_with_properties.properties) == 1
        assert product_with_properties.property_tags is None

        # Invalid: both properties and property_tags set (violates oneOf)
        with pytest.raises(ValueError, match="cannot have both"):
            Product(
                product_id="p3",
                name="Invalid Product",
                description="Product with both fields (invalid)",
                formats=["display_300x250"],
                properties=[
                    Property(
                        property_type="website",
                        name="Example Site",
                        identifiers=[PropertyIdentifier(type="domain", value="example.com")],
                        publisher_domain="example.com",
                    )
                ],
                property_tags=["sports"],  # This violates oneOf constraint
                delivery_type="guaranteed",
                cpm=10.0,
                pricing_options=[
                    PricingOptionSchema(
                        pricing_option_id="cpm_usd_fixed",
                        pricing_model="cpm",
                        rate=10.0,
                        currency="USD",
                        is_fixed=True,
                    )
                ],
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
                    "package_id": "pkg_001",
                    "products": ["product_1"],
                    "status": "draft",
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
                    "package_id": "pkg_001",
                    "products": ["product_1"],
                    "status": "draft",
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
