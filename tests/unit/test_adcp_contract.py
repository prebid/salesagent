"""Contract tests to ensure database models match AdCP protocol schemas.

These tests verify that:
1. Database models have all required fields for AdCP schemas
2. Field types are compatible
3. Data can be correctly transformed between models and schemas
4. AdCP protocol requirements are met
"""

from datetime import datetime, timedelta
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
    Property,
    PropertyIdentifier,
    PropertyTagMetadata,
    Signal,
    SignalDeployment,
    SignalPricing,
    SyncCreativesRequest,
    SyncCreativesResponse,
    Targeting,
    TaskStatus,
    UpdateMediaBuyResponse,
)
from src.core.schemas import (
    Principal as PrincipalSchema,
)
from src.core.schemas import (
    Product as ProductSchema,
)


class TestAdCPContract:
    """Test that models and schemas align with AdCP protocol requirements."""

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
            is_fixed_price=True,
            cpm=Decimal("10.50"),
            price_guidance=None,
            is_custom=False,
            expires_at=None,
            countries=["US", "CA"],
            implementation_config={"internal": "config"},
        )

        # Convert to dict (simulating database retrieval and conversion)
        # The validator now ensures formats are stored as strings
        model_dict = {
            "product_id": model.product_id,
            "name": model.name,
            "description": model.description,
            "formats": model.formats,  # Now guaranteed to be strings by validator
            "delivery_type": model.delivery_type,
            "is_fixed_price": model.is_fixed_price,
            "cpm": float(model.cpm) if model.cpm else None,
            "price_guidance": model.price_guidance,
            "is_custom": model.is_custom,
            "expires_at": model.expires_at,
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
            is_fixed_price=False,
            cpm=None,
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
            "is_fixed_price": model.is_fixed_price,
            "cpm": None,
            "is_custom": model.is_custom,
            "expires_at": model.expires_at,
        }

        schema = ProductSchema(**model_dict)

        # AdCP spec: non_guaranteed products use auction-based pricing (no price_guidance)
        assert schema.delivery_type == "non_guaranteed"
        assert schema.is_fixed_price is False
        assert schema.cpm is None  # No fixed CPM for non-guaranteed

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
        # AdCP requires both brief and promoted_offering
        request = GetProductsRequest(
            brief="Looking for display ads on news sites",
            promoted_offering="B2B SaaS company selling analytics software",
        )

        assert request.brief is not None
        assert request.promoted_offering is not None

        # Should fail without promoted_offering (AdCP requirement)
        with pytest.raises(ValueError):
            GetProductsRequest(brief="Just a brief")

    def test_adcp_create_media_buy_request(self):
        """Test AdCP create_media_buy request structure."""
        start_date = datetime.now() + timedelta(days=1)
        end_date = datetime.now() + timedelta(days=30)

        request = CreateMediaBuyRequest(
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
            "format_id": "native_feed",
            "name": "Native Feed Ad",
            "type": "native",
            "is_standard": True,
            "iab_specification": "IAB Native Ad Specification",
            "requirements": {"width": 300, "height": 250},
            # assets_required follows new AdCP spec structure
            "assets_required": [{"asset_type": "image", "quantity": 1, "requirements": {"width": 300, "height": 250}}],
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
            "is_fixed_price": "is_fixed_price",
            "cpm": "cpm",
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
            is_fixed_price=True,
            cpm=10.0,
            price_guidance=None,
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
                is_fixed_price=True,
                cpm=10.0,
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
                is_fixed_price=True,
                cpm=10.0,
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
                is_fixed_price=True,
                cpm=10.0,
                implementation_config={"internal": "data"},  # Should be excluded
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
        assert adcp_response["format"] == "display_300x250", "Format should use AdCP field name"

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
        assert "cpm" in adcp_response["pricing"], "pricing must have cpm field"
        assert "currency" in adcp_response["pricing"], "pricing must have currency field"
        assert adcp_response["pricing"]["cpm"] >= 0, "cpm must be non-negative"
        assert len(adcp_response["pricing"]["currency"]) == 3, "currency must be 3-letter code"

        # Test backward compatibility properties
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

        # Verify field count (Budget is simple, count should be stable)
        assert len(adcp_response) == 4, f"Budget response should have exactly 4 fields, got {len(adcp_response)}"

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
            override_start_date=datetime.now(),
            override_end_date=datetime.now() + timedelta(days=7),
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
        """Test that SyncCreativesRequest model complies with AdCP sync-creatives schema."""
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

        request = SyncCreativesRequest(
            creatives=[creative],
            media_buy_id="mb_456",
            buyer_ref="buyer_789",
            assign_to_packages=["pkg_1", "pkg_2"],
            upsert=True,
        )

        # Test model_dump (SyncCreativesRequest doesn't have internal fields)
        adcp_response = request.model_dump()

        # Verify required AdCP fields are present
        adcp_required_fields = ["creatives"]
        for field in adcp_required_fields:
            assert field in adcp_response, f"Required AdCP field '{field}' missing from response"
            assert adcp_response[field] is not None, f"Required AdCP field '{field}' is None"

        # Verify AdCP optional fields are present
        adcp_optional_fields = ["media_buy_id", "buyer_ref", "assign_to_packages", "upsert"]
        for field in adcp_optional_fields:
            assert field in adcp_response, f"AdCP optional field '{field}' missing from response"

        # Verify creatives array structure
        assert isinstance(adcp_response["creatives"], list), "Creatives must be an array"
        assert len(adcp_response["creatives"]) > 0, "Creatives array must not be empty"

        # Test creative object structure
        creative_obj = adcp_response["creatives"][0]
        creative_required_fields = ["creative_id", "name", "format", "url"]  # AdCP spec field names
        for field in creative_required_fields:
            assert field in creative_obj, f"Creative required field '{field}' missing"
            assert creative_obj[field] is not None, f"Creative required field '{field}' is None"

        # Verify field count (flexible due to optional fields)
        assert len(adcp_response) >= 1, f"SyncCreativesRequest should have at least 1 field, got {len(adcp_response)}"

    def test_sync_creatives_response_adcp_compliance(self):
        """Test that SyncCreativesResponse model complies with AdCP sync-creatives response schema."""
        synced_creative1 = Creative(
            creative_id="creative_123",
            name="Synced Creative 1",
            format_id="display_300x250",
            content_uri="https://example.com/creative1.jpg",
            principal_id="principal_1",
            status="approved",
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )

        synced_creative2 = Creative(
            creative_id="creative_456",
            name="Synced Creative 2",
            format_id="video_720p",
            content_uri="https://example.com/creative2.mp4",
            principal_id="principal_1",
            status="pending_review",
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )

        response = SyncCreativesResponse(
            success=True,
            message="Successfully synced 2 creatives",
            synced_creatives=[synced_creative1, synced_creative2],
            failed_creatives=[{"creative_id": "creative_789", "name": "Failed Creative", "error": "Invalid format"}],
        )

        # Test model_dump
        adcp_response = response.model_dump()

        # Verify required AdCP fields are present
        adcp_required_fields = ["synced_creatives"]
        for field in adcp_required_fields:
            assert field in adcp_response, f"Required AdCP field '{field}' missing from response"
            assert adcp_response[field] is not None, f"Required AdCP field '{field}' is None"

        # Verify AdCP optional fields are present
        adcp_optional_fields = ["failed_creatives", "assignments", "message"]
        for field in adcp_optional_fields:
            assert field in adcp_response, f"AdCP optional field '{field}' missing from response"

        # Verify response structure requirements
        assert isinstance(adcp_response["synced_creatives"], list), "Synced creatives must be array"
        assert isinstance(adcp_response["failed_creatives"], list), "Failed creatives must be array"
        assert isinstance(adcp_response["assignments"], list), "Assignments must be array"

        # Verify field count (flexible due to optional fields)
        assert len(adcp_response) >= 1, f"SyncCreativesResponse should have at least 1 field, got {len(adcp_response)}"

    def test_list_creatives_request_adcp_compliance(self):
        """Test that ListCreativesRequest model complies with AdCP list-creatives schema."""
        request = ListCreativesRequest(
            media_buy_id="mb_123",
            buyer_ref="buyer_456",
            status="approved",
            format="display_300x250",  # Uses format, not format_id
            tags=["sports", "premium"],
            created_after=datetime.now() - timedelta(days=30),
            created_before=datetime.now(),
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
            format_id="video_720p",
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
            total_count=2,
            page=1,  # Required field
            limit=50,  # Required field
            has_more=False,
            message="Found 2 creatives",  # Optional field
        )

        # Test model_dump
        adcp_response = response.model_dump()

        # Verify required AdCP fields are present
        adcp_required_fields = ["creatives", "total_count", "page", "limit", "has_more"]
        for field in adcp_required_fields:
            assert field in adcp_response, f"Required AdCP field '{field}' missing from response"
            assert adcp_response[field] is not None, f"Required AdCP field '{field}' is None"

        # Verify AdCP optional fields are present
        adcp_optional_fields = ["message"]
        for field in adcp_optional_fields:
            assert field in adcp_response, f"AdCP optional field '{field}' missing from response"

        # Verify response structure requirements
        assert isinstance(adcp_response["creatives"], list), "Creatives must be array"
        assert isinstance(adcp_response["total_count"], int), "Total count must be integer"
        assert adcp_response["total_count"] >= 0, "Total count must be non-negative"

        # Test creative object structure in response
        if len(adcp_response["creatives"]) > 0:
            creative = adcp_response["creatives"][0]
            creative_required_fields = ["creative_id", "name", "format", "status"]
            for field in creative_required_fields:
                assert field in creative, f"Creative required field '{field}' missing"
                assert creative[field] is not None, f"Creative required field '{field}' is None"

        # Verify field count
        assert len(adcp_response) == 6, f"ListCreativesResponse should have exactly 6 fields, got {len(adcp_response)}"

    def test_create_media_buy_response_adcp_compliance(self):
        """Test that CreateMediaBuyResponse complies with AdCP create-media-buy-response schema."""

        # Create response with all fields (success case)
        successful_response = CreateMediaBuyResponse(
            media_buy_id="mb_12345",
            buyer_ref="br_67890",
            status="active",
            detail="Media buy created successfully",
            message="Campaign is ready to launch",
            packages=[{"package_id": "pkg_1", "product_id": "prod_1", "budget": 5000.0, "targeting": {}}],
            creative_deadline=datetime.now() + timedelta(days=7),
            errors=None,
        )

        # Test successful response AdCP compliance
        adcp_response = successful_response.model_dump()

        # Verify required AdCP fields present and non-null
        required_fields = ["media_buy_id"]
        for field in required_fields:
            assert field in adcp_response, f"Required AdCP field '{field}' missing from response"
            assert adcp_response[field] is not None, f"Required AdCP field '{field}' is None"

        # Verify optional AdCP fields present (can be null)
        optional_fields = ["buyer_ref", "status", "detail", "message", "packages", "creative_deadline", "errors"]
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
            buyer_ref=None,
            status="failed",
            detail="Budget validation failed",
            message="Insufficient budget for requested targeting",
            packages=[],
            creative_deadline=None,
            errors=[Error(code="budget_insufficient", message="Minimum budget of $1000 required")],
        )

        error_adcp_response = error_response.model_dump()

        # Verify error response structure
        assert error_adcp_response["status"] == "failed"
        assert error_adcp_response["errors"] is not None
        assert len(error_adcp_response["errors"]) > 0
        assert isinstance(error_adcp_response["errors"][0], dict)
        assert "code" in error_adcp_response["errors"][0]
        assert "message" in error_adcp_response["errors"][0]

        # Verify field count (8 fields total)
        assert len(adcp_response) == 8, f"CreateMediaBuyResponse should have exactly 8 fields, got {len(adcp_response)}"

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
            is_fixed_price=True,
            cpm=12.50,
            min_spend=1000.00,
            measurement=None,
            creative_policy=None,
            is_custom=False,
        )

        # Create response with products
        response = GetProductsResponse(
            products=[product],
            message="Found 1 matching product",
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
        optional_fields = ["message", "errors"]
        for field in optional_fields:
            assert field in adcp_response, f"Optional AdCP field '{field}' missing from response"

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
        empty_response = GetProductsResponse(products=[], message="No products match your criteria", errors=[])

        empty_adcp_response = empty_response.model_dump()
        assert empty_adcp_response["products"] == [], "Empty products list should be empty array"
        # Allow 3 or 4 fields (status is optional and may not be present)
        assert (
            len(empty_adcp_response) >= 3 and len(empty_adcp_response) <= 4
        ), f"GetProductsResponse should have 3-4 fields (status optional), got {len(empty_adcp_response)}"

    def test_list_creative_formats_response_adcp_compliance(self):
        """Test that ListCreativeFormatsResponse complies with AdCP list-creative-formats-response schema."""

        # Create response with formats using actual Format schema
        response = ListCreativeFormatsResponse(
            formats=[
                Format(
                    format_id="display_300x250",
                    name="Medium Rectangle",
                    type="display",
                    is_standard=True,
                    iab_specification="IAB Display",
                    requirements={"width": 300, "height": 250, "file_types": ["jpg", "png", "gif"]},
                    assets_required=None,
                )
            ],
            message="Found 1 creative format",
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
        optional_fields = ["message", "errors"]
        for field in optional_fields:
            assert field in adcp_response, f"Optional AdCP field '{field}' missing from response"

        # Verify specific field types and constraints
        assert isinstance(adcp_response["formats"], list), "formats must be array"

        # Verify format structure (using actual Format schema fields)
        if len(adcp_response["formats"]) > 0:
            format_obj = adcp_response["formats"][0]
            assert "format_id" in format_obj, "format must have format_id"
            assert "name" in format_obj, "format must have name"
            assert "type" in format_obj, "format must have type"
            # Note: width/height are in requirements dict, not direct fields

        # Verify field count (at least 3 fields - some optional fields might be excluded)
        assert (
            len(adcp_response) >= 3
        ), f"ListCreativeFormatsResponse should have at least 3 fields, got {len(adcp_response)}"

    def test_update_media_buy_response_adcp_compliance(self):
        """Test that UpdateMediaBuyResponse complies with AdCP update-media-buy-response schema."""

        # Create successful update response
        response = UpdateMediaBuyResponse(
            status="accepted",
            implementation_date=datetime.now() + timedelta(hours=1),
            detail="Budget update scheduled for implementation",
            reason=None,
        )

        # Test AdCP-compliant response
        adcp_response = response.model_dump()

        # Verify required AdCP fields present and non-null
        required_fields = ["status"]
        for field in required_fields:
            assert field in adcp_response, f"Required AdCP field '{field}' missing from response"
            assert adcp_response[field] is not None, f"Required AdCP field '{field}' is None"

        # Verify optional AdCP fields present (can be null)
        optional_fields = ["implementation_date", "detail", "reason"]
        for field in optional_fields:
            assert field in adcp_response, f"Optional AdCP field '{field}' missing from response"

        # Verify specific field types and constraints
        assert isinstance(adcp_response["status"], str), "status must be string"
        assert adcp_response["status"] in ["accepted", "rejected", "pending"], "status must be valid value"

        # Test error response case
        error_response = UpdateMediaBuyResponse(
            status="rejected",
            implementation_date=None,
            detail="Invalid budget amount",
            reason="Budget must be positive",
        )

        error_adcp_response = error_response.model_dump()
        assert error_adcp_response["status"] == "rejected"
        assert error_adcp_response["reason"] == "Budget must be positive"

        # Verify field count (4 fields total - only non-None fields included)
        assert len(adcp_response) <= 4, f"UpdateMediaBuyResponse should have at most 4 fields, got {len(adcp_response)}"

    def test_get_media_buy_delivery_response_adcp_compliance(self):
        """Test that GetMediaBuyDeliveryResponse complies with AdCP get-media-buy-delivery-response schema."""

        # Create delivery data with correct structure using MediaBuyDeliveryData
        delivery_data = MediaBuyDeliveryData(
            media_buy_id="mb_12345",
            buyer_ref="br_67890",
            status="active",
            spend=Budget(total=2500.50, currency="USD"),
            impressions=125000,
            pacing="even",
            days_elapsed=15,
            total_days=30,
        )

        # Create delivery response with metrics
        response = GetMediaBuyDeliveryResponse(
            deliveries=[delivery_data],
            total_spend=2500.50,
            total_impressions=125000,
            active_count=1,
            summary_date=datetime.now().date(),
        )

        # Test AdCP-compliant response
        adcp_response = response.model_dump()

        # Verify required AdCP fields present and non-null
        required_fields = ["deliveries", "total_spend", "total_impressions", "active_count", "summary_date"]
        for field in required_fields:
            assert field in adcp_response, f"Required AdCP field '{field}' missing from response"
            assert adcp_response[field] is not None, f"Required AdCP field '{field}' is None"

        # Verify specific field types and constraints
        assert isinstance(adcp_response["deliveries"], list), "deliveries must be array"

        # Verify delivery structure (MediaBuyDeliveryData fields)
        if len(adcp_response["deliveries"]) > 0:
            delivery = adcp_response["deliveries"][0]
            assert "media_buy_id" in delivery, "delivery must have media_buy_id"
            assert "buyer_ref" in delivery, "delivery must have buyer_ref"
            assert "status" in delivery, "delivery must have status"
            assert "spend" in delivery, "delivery must have spend (Budget object)"
            assert "impressions" in delivery, "delivery must have impressions"
            assert "pacing" in delivery, "delivery must have pacing"
            assert "days_elapsed" in delivery, "delivery must have days_elapsed"
            assert "total_days" in delivery, "delivery must have total_days"

            # Verify Budget structure within spend
            spend = delivery["spend"]
            assert "total" in spend, "spend must have total"
            assert "currency" in spend, "spend must have currency"

        # Test empty response case
        empty_response = GetMediaBuyDeliveryResponse(
            deliveries=[], total_spend=0.0, total_impressions=0, active_count=0, summary_date=datetime.now().date()
        )

        empty_adcp_response = empty_response.model_dump()
        assert empty_adcp_response["deliveries"] == [], "Empty deliveries list should be empty array"

        # Verify field count (5 fields total)
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
        """Test that ListAuthorizedPropertiesResponse complies with AdCP list-authorized-properties-response schema."""
        # Create response with all required + optional fields
        property_obj = Property(
            property_type="website",
            name="Example Site",
            identifiers=[PropertyIdentifier(type="domain", value="example.com")],
            tags=["premium_content"],
            publisher_domain="example.com",
        )

        tag_metadata = PropertyTagMetadata(name="Premium Content", description="High-quality content properties")

        response = ListAuthorizedPropertiesResponse(
            adcp_version="1.0.0", properties=[property_obj], tags={"premium_content": tag_metadata}, errors=[]
        )

        # Test AdCP-compliant response
        adcp_response = response.model_dump()

        # Verify required AdCP fields present and non-null
        required_fields = ["adcp_version", "properties"]
        for field in required_fields:
            assert field in adcp_response
            assert adcp_response[field] is not None

        # Verify optional AdCP fields present (can be null)
        optional_fields = ["tags", "errors"]
        for field in optional_fields:
            assert field in adcp_response

        # Verify adcp_version format
        import re

        version_pattern = r"^\d+\.\d+\.\d+$"
        assert re.match(version_pattern, adcp_response["adcp_version"])

        # Verify properties is array
        assert isinstance(adcp_response["properties"], list)

        # Verify tags is object when present
        if adcp_response["tags"] is not None:
            assert isinstance(adcp_response["tags"], dict)

        # Verify errors is array when present
        if adcp_response["errors"] is not None:
            assert isinstance(adcp_response["errors"], list)

        # Verify field count expectations
        assert len(adcp_response) == 4

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

        # Test response schemas with status field
        response = GetProductsResponse(products=[], message="Test with status", status=TaskStatus.COMPLETED)

        data = response.model_dump()
        assert "status" in data
        assert data["status"] == TaskStatus.COMPLETED

        # Test backward compatibility (no status field)
        response_no_status = GetProductsResponse(products=[], message="Test without status")

        data_no_status = response_no_status.model_dump()
        assert "status" not in data_no_status  # Should be excluded when None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
