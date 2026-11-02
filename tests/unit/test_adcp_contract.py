"""Contract tests to ensure database models match AdCP protocol schemas.

These tests verify that:
1. Database models have all required fields for AdCP schemas
2. Field types are compatible
3. Data can be correctly transformed between models and schemas
4. AdCP protocol requirements are met
"""

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from adcp.types import CreativePolicy

from src.core.database.models import (
    Principal as PrincipalModel,
)  # Need both for contract test
from src.core.database.models import Product as ProductModel
from src.core.product_conversion import default_reporting_capabilities
from src.core.schemas import (
    Budget,
    CreateMediaBuyRequest,
    Creative,
    CreativeApprovalStatus,
    CreativeAssignment,
    Format,
    FormatId,
    GetMediaBuyDeliveryRequest,
    GetMediaBuyDeliveryResponse,
    GetProductsRequest,
    GetProductsResponse,
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
from tests.factories.creative_asset import build_assets, image_spec, url_spec, video_spec
from tests.factories.media_buy import package_pricing_fields
from tests.factories.principal import plaintext_token_for


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
        from src.core.schemas import (
            ListCreativeFormatsRequest as LocalListCreativeFormatsRequest,
        )
        from src.core.schemas import (
            ListCreativesRequest as LocalListCreativesRequest,
        )
        from src.core.schemas import (
            SyncCreativesRequest as LocalSyncCreativesRequest,
        )

        # GetProductsRequest - local declares no field the library does not
        # push_notification_config — a real library field on GetProductsWholesaleRequest
        #   (adcp 6.6 / spec 3.1.1); inherited, present in both sets
        # buying_mode and account are in the library (adcp 3.9) but overridden locally
        # (buying_mode widened to str|None, account made optional)
        lib_fields = set(LibGetProductsRequest.model_fields.keys())
        local_fields = set(GetProductsRequest.model_fields.keys())
        assert lib_fields == local_fields, f"GetProductsRequest drift: lib={lib_fields}, local={local_fields}"

        # GetMediaBuyDeliveryRequest - local now matches library exactly
        # (SDK 5.7 provides time_granularity, include_window_breakdown,
        # include_package_daily_breakdown — no local extensions needed)
        lib_fields = set(LibGetMediaBuyDeliveryRequest.model_fields.keys())
        local_fields = set(LocalGetMediaBuyDeliveryRequest.model_fields.keys())
        assert lib_fields == local_fields, f"GetMediaBuyDeliveryRequest drift: lib={lib_fields}, local={local_fields}"

        # Document known drift for other schemas (to be fixed)
        # These assertions document the current state and will fail when fixed

        # CreateMediaBuyRequest - has many non-spec convenience fields
        # CreateMediaBuyRequest - now extends library, should match
        lib_fields = set(LibCreateMediaBuyRequest.model_fields.keys())
        local_fields = set(LocalCreateMediaBuyRequest.model_fields.keys())
        assert lib_fields == local_fields, f"CreateMediaBuyRequest drift: lib={lib_fields}, local={local_fields}"

        # ListCreativesRequest - the buyer shape, matching the library exactly. The
        # reader's two internal knobs (format, page) are on ListCreativesRequest, which
        # subclasses this and is what the builder and the _impl are typed to.
        lib_fields = set(LibListCreativesRequest.model_fields.keys())
        local_fields = set(LocalListCreativesRequest.model_fields.keys())
        assert lib_fields == local_fields, f"ListCreativesRequest drift: lib={lib_fields}, local={local_fields}"

        # ListCreativeFormatsRequest - now extends library, should match
        lib_fields = set(LibListCreativeFormatsRequest.model_fields.keys())
        local_fields = set(LocalListCreativeFormatsRequest.model_fields.keys())
        assert lib_fields == local_fields, f"ListCreativeFormatsRequest drift: lib={lib_fields}, local={local_fields}"

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
            LibraryCreateMediaBuyRequest()

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

    @pytest.mark.parametrize(
        "field_name",
        ["account", "sandbox", "creative_deadline", "valid_actions", "context"],
    )
    def test_create_media_buy_success_inherits_parent_typed_annotations(self, field_name):
        """CreateMediaBuySuccess must inherit the adcp 6.6 parent's TYPED annotations.

        Regression test for PR #1567 round-3 (GH #1620): _base.py carried
        'SDK 5.7 removed these from parent' redeclarations that are stale under
        adcp 6.6 — the parent re-added all five fields, typed. Two of the local
        redeclarations WEAKEN the parent's types (account: Any | None vs the
        parent's Account | None; creative_deadline: datetime | None vs the
        parent's AwareDatetime | None). The subclass annotation must be exactly
        the parent's annotation — the library parent is the source of truth,
        which also catches any future drift on an SDK bump.
        """
        from adcp.types.aliases import (
            CreateMediaBuySuccessResponse as LibraryCreateMediaBuySuccess,
        )

        from src.core.schemas import CreateMediaBuySuccess as LocalCreateMediaBuySuccess

        parent_annotation = LibraryCreateMediaBuySuccess.model_fields[field_name].annotation
        local_annotation = LocalCreateMediaBuySuccess.model_fields[field_name].annotation
        assert local_annotation == parent_annotation, (
            f"CreateMediaBuySuccess.{field_name} drifts from the adcp parent: "
            f"local={local_annotation!r} vs parent={parent_annotation!r} — "
            f"delete the stale local redeclaration and inherit the parent's typed field"
        )


def _as_type_list(json_type: object) -> list:
    """JSON Schema `type` as a list — it is either a string or a list of strings."""
    if json_type is None:
        return []
    return list(json_type) if isinstance(json_type, list) else [json_type]


class TestGetMediaBuysAlwaysIncludeNullFields:
    """Every required+nullable field of the pinned item must survive `exclude_none`."""

    def test_required_nullable_item_fields_are_declared_always_include(self):
        """`required` n `nullable` on the pinned get-media-buys item == {confirmed_at}.

        The library base dumps with `exclude_none=True`, which is right for OPTIONAL
        fields (AdCP omits rather than nulls them) and wrong for a field the schema
        lists in `required` while typing it nullable. Dropping such a field produces an
        item that fails validation for every not-yet-confirmed buy.

        This pin is DECLARATION-only by design — the behavioural net is the wire-reading
        UC-019 step `then_media_buy_confirmed_at_is_null`, graded by
        `@T-UC-019-confirmed-at-null-survives-exclude-none` on a2a+mcp. That step, not
        `then_media_buy_includes_confirmed_at`, is the net for THIS pin: the presence-only
        step is reached exclusively by scenarios seeding a NON-null confirmed_at, where the
        key survives because it has a value — so it cannot fail when retention breaks.
        What this pin adds is the reverse direction: a pin bump that makes a SECOND field
        required+nullable reddens here, and the only sane way to green it is to grow the
        declared set.
        """
        from src.core.schemas._base import GetMediaBuysMediaBuy
        from tests.helpers import pinned_schema

        item = pinned_schema.load_canonicalized("media-buy/get-media-buys-response.json")["properties"]["media_buys"][
            "items"
        ]
        required = set(item["required"])
        properties = item["properties"]

        # Nullability is read off the `type` list, the only spelling the pinned item
        # uses. A required property that acquired a combinator could hide a null branch
        # from that rule, so refuse rather than under-report.
        combinator_required = sorted(name for name in required if {"anyOf", "oneOf"} & set(properties.get(name, {})))
        assert not combinator_required, (
            f"Pinned get-media-buys item now spells required properties {combinator_required} with "
            f"anyOf/oneOf; this test's nullability rule reads only `type`. Extend the rule before "
            f"trusting the result."
        )

        nullable = {name for name, spec in properties.items() if "null" in _as_type_list(spec.get("type"))}
        required_nullable = required & nullable
        assert required_nullable, (
            "The pinned get-media-buys item declares no required+nullable property, so this test "
            "would pass without exercising anything. The pin moved — check what replaced it."
        )

        # Assert the WIRE, not the declaration. The set is derived from this same pin
        # (_PINNED_SCHEMA_REF), so comparing it against the pin would be a tautology;
        # what is worth grading is that a null value for such a field actually survives
        # `exclude_none` and reaches the buyer as an explicit null.
        blank = dict.fromkeys(required_nullable)
        buy = GetMediaBuysMediaBuy(
            media_buy_id="mb-1",
            status="active",
            currency="USD",
            total_budget=1000.0,
            packages=[],
            revision=1,
            **blank,
        )
        dumped = buy.model_dump(mode="json")
        missing = sorted(name for name in required_nullable if name not in dumped)
        assert not missing, (
            f"{missing} are required+nullable in the pinned get-media-buys item but were dropped "
            f"by exclude_none, so a buy whose value is null would fail item-level validation at "
            f"the buyer. Emitted keys: {sorted(dumped)}"
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
            "reporting_capabilities": default_reporting_capabilities(),  # Required per AdCP 3.1.1
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
            "reporting_capabilities": default_reporting_capabilities(),
        }

        schema = ProductSchema(**model_dict)

        # AdCP spec: non_guaranteed products use auction-based pricing (no price_guidance)
        assert str(schema.delivery_type.value) == "non_guaranteed"  # Enum value

    def test_principal_model_to_schema(self):
        """Test that Principal model matches AdCP authentication requirements."""
        model = PrincipalModel.with_token(
            plaintext_token_for("test_principal"),
            tenant_id="test_tenant",
            principal_id="test_principal",
            name="Test Advertiser",
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
            ],  # Required per AdCP spec,
            # reporting_capabilities is required on the pinned Product (AdCP 3.1.1) and
            # carries no default, so a construction that omits it cannot validate.
            reporting_capabilities=default_reporting_capabilities(),
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
            ],  # Required per AdCP spec,
            # reporting_capabilities is required on the pinned Product (AdCP 3.1.1) and
            # carries no default, so a construction that omits it cannot validate.
            reporting_capabilities=default_reporting_capabilities(),
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
                    # V3 auction shape: floor at top level, percentiles in guidance
                    "floor_price": 1.0,
                    "price_guidance": {"p50": 5.0},
                }
            ],
            # reporting_capabilities is required on the pinned Product (AdCP 3.1.1) and
            # carries no default, so a construction that omits it cannot validate.
            reporting_capabilities=default_reporting_capabilities(),
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
                # reporting_capabilities is required on the pinned Product (AdCP 3.1.1) and
                # carries no default, so a construction that omits it cannot validate.
                reporting_capabilities=default_reporting_capabilities(),
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
            account={"account_id": "acct_test"},
            brand={"domain": "nike.com"},  # Required in adcp 3.6.0 (was brand_manifest)
            # Required per AdCP spec
            packages=[
                {"product_id": "product_1", "budget": 2500.0, "pricing_option_id": "opt_1"},
                {"product_id": "product_2", "budget": 2500.0, "pricing_option_id": "opt_2"},
            ],
            start_time=start_time,
            end_time=end_time,
            po_number="PO-12345",  # Optional per spec
            idempotency_key="unit-test-key-adcp-create-mb",
        )

        # Verify AdCP requirements
        assert len(request.get_product_ids()) == 2
        assert request.get_total_budget() == 5000.0
        assert request.flight_end_date > request.flight_start_date

        # Verify spec-compliant fields are present
        assert request.brand is not None
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

    def test_format_canonical_parameters_round_trip(self):
        """canonical_parameters keeps format_kind and params (3.1.1 product-format-declaration.json
        requires both). The SDK's generated declaration has neither field and ignores extras, so a
        creative agent's catalog came back as {"canonical_formats_only": false, "experimental": false}."""
        from tests.helpers.adcp_factories import create_test_format_id

        declaration = {"format_kind": "image", "params": {"width": 300, "height": 250, "pixel_ratios": [2]}}
        format_obj = Format.model_validate(
            {
                "format_id": create_test_format_id("display_300x250_image_2x"),
                "name": "Medium Rectangle - Image (2x)",
                "canonical": {"kind": "image"},
                "canonical_parameters": declaration,
            }
        )

        dumped = format_obj.model_dump(mode="json", exclude_none=True)["canonical_parameters"]
        assert dumped == {**declaration, "canonical_formats_only": False, "experimental": False}

    def test_list_creative_formats_response_round_trips_the_reference_catalog(self):
        """A response re-parsed from its own wire serializes to the same wire, as a buyer reads it.

        The response inherited the library ``formats`` type, so the re-parse rebuilt each entry as
        the library Format and dropped canonical_parameters.format_kind and .params."""
        from src.core.format_cache import load_reference_formats

        wire = ListCreativeFormatsResponse(formats=list(load_reference_formats())).model_dump(mode="json")
        # The catalog must carry declarations, or the round-trip below has nothing to lose.
        assert any("format_kind" in (f.get("canonical_parameters") or {}) for f in wire["formats"])

        assert ListCreativeFormatsResponse.model_validate(wire).model_dump(mode="json") == wire

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
                # reporting_capabilities is required on the pinned Product (AdCP 3.1.1) and
                # carries no default, so a construction that omits it cannot validate.
                reporting_capabilities=default_reporting_capabilities(),
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
                # reporting_capabilities is required on the pinned Product (AdCP 3.1.1) and
                # carries no default, so a construction that omits it cannot validate.
                reporting_capabilities=default_reporting_capabilities(),
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
                # reporting_capabilities is required on the pinned Product (AdCP 3.1.1) and
                # carries no default, so a construction that omits it cannot validate.
                reporting_capabilities=default_reporting_capabilities(),
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
        )

        # Verify signals are supported in Targeting schema
        assert hasattr(targeting, "signals")
        assert targeting.signals == [
            "sports_enthusiasts",
            "auto_intenders_q1_2025",
            "high_income_households",
        ]
        # ``key_value_pairs`` was seeded and asserted here. Targeting no longer declares it
        # (src/core/schemas/_base.py — the pin declares no managed-only field), and the
        # subject of this test is signal support, which the assertion above grades.

    def test_creative_adcp_compliance(self):
        """Test that Creative model complies with AdCP listing Creative schema.

        The Creative extends the listing Creative (list_creatives_response.Creative):
        - Public model_dump() contains: creative_id, format_id, name, status,
          created_date, updated_date, assets, tags (listing schema fields)
        - Internal fields (principal_id) are excluded from model_dump()
          but carried on the model as attributes
        """

        # Test creating a Creative with all fields (some public, some internal)
        creative = Creative(
            creative_id="test_creative_123",
            name="Test AdCP Creative",
            format_id=FormatId(agent_url="https://creative.adcontextprotocol.org", id="display_300x250"),
            assets=build_assets(
                image_spec("banner_image", url="https://example.com/creative.jpg"),
                url_spec("click_url", url="https://example.com/landing", url_type="clickthrough"),
            ),
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

        # The internal half of the split: principal_id is CARRIED on the model, and the
        # attribute is what existing means for a Field(exclude=True) field. There is no
        # second dump shape to read it out of (CLAUDE.md pattern 4 — one serializer seat).
        assert creative.principal_id == "test_principal", "principal_id must be carried on the model"
        # status is on BOTH: a spec field, so the attribute and the wire agree.
        assert creative.status == adcp_response["status"]

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
            signal_id={
                "source": "catalog",
                "data_provider_domain": "acmedata.com",
                "id": "signal_auto_intenders_q1_2025",
            },
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

        # The internal half of the split: every Field(exclude=True) field is CARRIED on the
        # model, and the attribute is what existing means. There is no second dump shape to
        # read them out of (CLAUDE.md pattern 4 — one serializer seat).
        for field in internal_fields:
            assert getattr(signal, field) is not None, f"Internal field '{field}' not carried on the model"
        assert signal.deployments[0].scope == "account-specific", "deployment scope not carried on the model"

        # Verify field count expectations (flexible to allow AdCP spec evolution)
        assert len(adcp_response) >= 8, f"AdCP response should have at least 8 core fields, got {len(adcp_response)}"

        # Every internal name is off the wire while the attribute above holds it — the split
        # itself, asserted with the two mechanisms that survive.
        assert not set(internal_fields) & set(adcp_response), "an internal field reached the wire"

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

        # There is no second, internal dump any more, and no internal field for one to
        # carry. This block used to call a model_dump_internal() helper and assert each
        # internal name WAS present in it, then assert the internal dump had at least three
        # keys the AdCP dump lacked -- a two-dump design where the wire shape was produced by
        # stripping. Both the helper and the seven internal declarations on Package are gone:
        # the model declares what the pin declares, and extra="ignore" means a value handed
        # in under one of the removed names is not kept at all. That is what makes the
        # absence assertions above a guarantee rather than a coincidence, so it is asserted
        # here instead of the old helper's behaviour.
        assert type(package).model_config.get("extra") == "ignore", (
            "Package must refuse to keep undeclared keys; under the library parent's "
            "extra='allow' every name above would be stored and then serialized"
        )
        for field in internal_fields:
            assert field not in type(package).model_fields, f"'{field}' must not be declared on the wire model"
        assert not package.model_extra, f"undeclared input was retained: {package.model_extra}"

        # Verify field count expectations (flexible to allow AdCP spec evolution)
        # Package has 1 required field (package_id) + any optional fields that are set
        # We set several optional fields above, so expect at least 1 field
        assert len(adcp_response) >= 1, f"AdCP response should have at least required fields, got {len(adcp_response)}"

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

        # Targeting declares NO internal field, so it has no internal/wire split to grade.
        # key_value_pairs, tenant_id, created_at, updated_at and metadata were all removed
        # from the model (src/core/schemas/_base.py — the pinned core/targeting.json
        # declares no managed-only field, and a seller-side value that must persist belongs
        # on a repository-owned carrier). An undeclared key is now refused on construction
        # in dev, so the wire shape is exactly what the fields declare.
        assert not set(adcp_response) - set(Targeting.model_fields), "Targeting dumped a field it does not declare"

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

        # Verify field count expectations (flexible - targeting has many optional fields)
        assert len(adcp_response) >= 9, f"AdCP response should have at least 9 fields, got {len(adcp_response)}"

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
                    internal_status="approved",
                ),
                SyncCreativeResult(
                    creative_id="creative_456",
                    action="updated",
                    internal_status="pending_review",
                    changes=["url", "name"],
                ),
                SyncCreativeResult(
                    creative_id="creative_789",
                    action="failed",
                    errors=[{"code": "invalid_format", "message": "Invalid format"}],
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
        from adcp.types import PaginationRequest
        from adcp.types.generated_poc.creative.list_creatives_request import (
            Sort as LibrarySort,
        )  # TODO: different Sort from adcp.types.Sort

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

        # Verify all spec fields are present (per adcp 5.7 library schema)
        spec_fields = {
            "account",
            "adcp_major_version",
            "adcp_version",
            "context",
            "ext",
            "fields",
            "filters",
            "include_assignments",
            "include_items",
            "include_pricing",
            "include_purged",
            "include_snapshot",
            "include_variables",
            "include_webhook_activity",
            "pagination",
            "sort",
            "webhook_activity_limit",
        }
        assert set(adcp_response.keys()) == spec_fields, f"Fields should match spec: {set(adcp_response.keys())}"

    def test_list_creatives_response_adcp_compliance(self):
        """Test that ListCreativesResponse model complies with AdCP list-creatives response schema."""
        creative1 = Creative(
            creative_id="creative_123",
            name="Test Creative 1",
            format_id=FormatId(agent_url="https://creative.adcontextprotocol.org", id="display_300x250"),
            assets=build_assets(
                image_spec("banner_image", url="https://example.com/creative1.jpg", width=300, height=250)
            ),
            tags=["sports"],
            # Internal fields
            principal_id="principal_1",
            status="approved",
            created_date=datetime.now(tz=UTC),
            updated_date=datetime.now(tz=UTC),
        )

        creative2 = Creative(
            creative_id="creative_456",
            name="Test Creative 2",
            format_id=FormatId(agent_url="https://creative.adcontextprotocol.org", id="video_1280x720"),
            assets=build_assets(
                video_spec("video_file", url="https://example.com/creative2.mp4", width=1280, height=720)
            ),
            tags=["premium"],
            # Internal fields
            principal_id="principal_1",
            status="pending_review",
            created_date=datetime.now(tz=UTC),
            updated_date=datetime.now(tz=UTC),
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

    # REMOVED: test_create_media_buy_response_adcp_compliance and
    # test_update_media_buy_response_adcp_compliance. Both were field-PRESENCE contracts
    # over fields this model INHERITS -- media_buy_id, packages, creative_deadline,
    # affected_packages and revision are all declared by the adcp parent, not redeclared
    # here -- so they asserted that Python inheritance works. CLAUDE.md states the rule:
    # "There is deliberately no suite comparing a model's field set to the pinned schema."
    #
    # The create case had already rotted past the point of grading anything: it read
    # ``required_fields = []`` and then looped over it, so both of its carefully-worded
    # assertions ("Required AdCP field 'X' missing from response") had never once
    # executed. Its remaining checks were presence/type plus ``len(adcp_response) >= 3``,
    # a floor nothing can trip.
    #
    # Where the obligations live now:
    #   - the parent's TYPED annotations (account, sandbox, creative_deadline,
    #     valid_actions, context):
    #     TestSchemaMatchesLibrary::test_create_media_buy_success_inherits_parent_typed_annotations
    #     -- derived from the library parent per field, so it grades drift rather than presence;
    #   - the success branch carrying no ``errors`` key:
    #     tests/unit/test_property_list_unsupported_advisory.py::TestSuccessEnvelopeErrorsField
    #     (``errors`` is OUR field, absent from the library parent, so that one can fail);
    #   - no key outside the pin on the wire:
    #     tests/integration/test_a2a_response_compliance.py (the pinned create/update roots
    #     leave additionalProperties unset, so schema validation cannot see a stray key --
    #     that oracle is the only thing that can).

    def test_get_media_buy_delivery_request_adcp_compliance(self):
        """Test that GetMediaBuyDeliveryRequest complies with AdCP get-media-buy-delivery-request schema."""

        # Test request with all required + optional fields
        request = GetMediaBuyDeliveryRequest(
            media_buy_ids=["mb_123", "mb_456"],
            status_filter="active",
            start_date="2025-01-01",
            end_date="2025-01-31",
        )

        # Test AdCP-compliant request
        adcp_request = request.model_dump()

        # Verify all fields are optional in AdCP spec
        adcp_optional_fields = ["media_buy_ids", "status_filter", "start_date", "end_date"]
        for field in adcp_optional_fields:
            assert field in adcp_request, f"AdCP optional field '{field}' missing from request"

        # Verify field types and constraints
        if adcp_request.get("media_buy_ids") is not None:
            assert isinstance(adcp_request["media_buy_ids"], list), "media_buy_ids must be array"

        if adcp_request.get("status_filter") is not None:
            # Can be string or array according to AdCP spec
            # AdCP MediaBuyStatus enum: pending_creatives, pending_start, active,
            # paused, completed, rejected, canceled
            valid_statuses = [
                "pending_creatives",
                "pending_start",
                "active",
                "paused",
                "completed",
                "rejected",
                "canceled",
            ]
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

        # Create AdCP-compliant delivery data using new models.
        # pricing_model/rate/currency are in the by_package item's `required` set and are
        # non-nullable, so a compliance fixture that omits them is not AdCP-compliant.
        package_delivery = PackageDelivery(
            package_id="pkg_123",
            impressions=25000.0,
            spend=500.75,
            clicks=125.0,
            completed_views=None,
            pacing_index=1.0,
            **package_pricing_fields(rate=20.03),
        )

        daily_breakdown = DailyBreakdown(date="2025-01-15", impressions=1250.0, spend=25.05)

        delivery_totals = DeliveryTotals(
            impressions=25000.0, spend=500.75, clicks=125.0, ctr=0.005, completed_views=None, completion_rate=None
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
            impressions=25000.0, spend=500.75, clicks=125.0, completed_views=None, media_buy_count=1
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
            delivery_optional_fields = ["daily_breakdown"]
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

    def test_get_signals_request_adcp_compliance(self):
        """Test that GetSignalsRequest model complies with AdCP get-signals-request schema."""
        # adcp 3.9: GetSignalsRequest is a regular model (not RootModel).
        # deliver_to replaced with top-level destinations + countries fields.

        from adcp.types.generated_poc.core.destination import Destination1  # TODO: no stable alias in adcp.types

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

        # SDK 5.7: status is part of the protocol envelope (always present as default)
        # Per AdCP PR #113, status was moved to protocol envelope — SDK 5.7 includes it
        response = GetProductsResponse(products=[])
        data = response.model_dump()
        assert "products" in data  # Domain field present

    def test_package_excludes_internal_fields(self):
        """An internal name handed to the response Package reaches no serialization path.

        The names below were once declared on this class as Field(exclude=True), and the
        class also had a model_dump_internal() helper, so this test asserted they were
        absent from the AdCP dump and present in the internal one. Both halves are obsolete:
        the declarations are deleted because the pin does not declare them, and the helper is
        deleted because a model has one serializer.

        What replaced the old guarantee is not "the field is excluded" but "the class does
        not keep what it did not declare", and that distinction is the whole point of this
        test. The library parent sets extra="allow", so with the declarations removed and
        nothing else changed, each name below became an EXTRA -- stored on the model and
        then serialized, on model_dump, model_dump_json and to_wire alike. Deleting an
        internal field from a wire model made it MORE exposed, not less. extra="ignore" on
        the class is what closes it, and this test fails if that setting is lost.

        All three paths are checked because this class is what the buyer receives: it is
        built into response_packages in the create tool and into CreateMediaBuySuccess for
        the admin approval path, which reaches the buyer and the webhook.
        """
        from src.core.tools._wire import to_wire

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

        internal_names = [
            "platform_line_item_id",
            "tenant_id",
            "media_buy_id",
            "created_at",
            "updated_at",
            "metadata",
        ]

        # Not retained on the model in the first place. This is the assertion that fails
        # under the parent's extra="allow", and it fails BEFORE any dump, which is why it
        # comes first: every path below is clean only because nothing was kept.
        assert not pkg.model_extra, f"undeclared input was retained on the model: {pkg.model_extra}"
        assert type(pkg).model_config.get("extra") == "ignore"

        # All three serialization paths, not just model_dump. A field can be absent from one
        # and present in another when a per-class hook shapes only that one; nothing shapes
        # these, and that is the claim.
        wire = to_wire(pkg)
        paths = {
            "model_dump": pkg.model_dump(),
            "model_dump_json": json.loads(pkg.model_dump_json()),
            "to_wire": wire,
        }
        for path_name, payload in paths.items():
            assert "package_id" in payload, f"package_id missing from {path_name}"
            for name in internal_names:
                assert name not in payload, f"internal name '{name}' reached the buyer via {path_name}"

        # And none of them is declared, so there is no exclude=True to rely on.
        for name in internal_names:
            assert name not in type(pkg).model_fields, f"'{name}' must not be declared on the wire model"

    def test_create_media_buy_asap_start_time(self):
        """Test that CreateMediaBuyRequest accepts 'asap' as start_time per AdCP v1.7.0."""
        end_date = datetime.now(UTC) + timedelta(days=30)

        # Test with 'asap' start_time
        # Per AdCP spec, budget is at package level, not request level
        # adcp 3.6.0: brand_manifest replaced by brand (BrandReference with required domain)
        request = CreateMediaBuyRequest(
            account={"account_id": "acct_test"},
            brand={"domain": "flashsale.com"},
            start_time="asap",  # AdCP v1.7.0 supports literal "asap"
            end_time=end_date,
            packages=[{"product_id": "product_1", "pricing_option_id": "test_pricing", "budget": 5000.0}],
            idempotency_key="unit-test-key-asap-start-time",
        )

        # Verify asap is accepted (library wraps in StartTiming on some SDK versions)
        if hasattr(request.start_time, "root"):  # noqa: rootmodel — SDK-version polymorphism
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
            account={"account_id": "acct_test"},
            idempotency_key="test-idem-key-0001",
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
            account={"account_id": "acct_test"},
            brand={"domain": "scheduled.com"},
            start_time=start_date,
            end_time=end_date,
            packages=[{"product_id": "product_1", "pricing_option_id": "test_pricing", "budget": 5000.0}],
            idempotency_key="unit-test-key-datetime-start",
        )

        # Verify datetime is still accepted (library wraps in StartTiming on some SDK versions)
        if hasattr(request.start_time, "root"):  # noqa: rootmodel — SDK-version polymorphism
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
            # reporting_capabilities is required on the pinned Product (AdCP 3.1.1) and
            # carries no default, so a construction that omits it cannot validate.
            reporting_capabilities=default_reporting_capabilities(),
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
                # reporting_capabilities is required on the pinned Product (AdCP 3.1.1) and
                # carries no default, so a construction that omits it cannot validate.
                reporting_capabilities=default_reporting_capabilities(),
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
            account={"account_id": "acct_test"},
            brand={"domain": "nike.com"},
            packages=[{"product_id": "product_1", "pricing_option_id": "test_pricing", "budget": 5000.0}],
            start_time=start_date,
            end_time=end_date,
            idempotency_key="unit-test-key-brand-inline",
        )

        # Verify brand is properly stored
        assert request.brand is not None
        assert request.brand.domain == "nike.com"

        # Verify fields still work
        assert len(request.packages) == 1

    def test_create_media_buy_with_brand_and_brand_id(self):
        """Test CreateMediaBuyRequest with brand reference including brand_id (adcp 3.6.0)."""
        start_date = datetime.now(UTC) + timedelta(days=1)
        end_date = datetime.now(UTC) + timedelta(days=30)

        # Test with brand reference + optional brand_id
        request = CreateMediaBuyRequest(
            account={"account_id": "acct_test"},
            brand={"domain": "nike.com", "brand_id": "brand_nike_001"},
            packages=[{"product_id": "product_1", "pricing_option_id": "test_pricing", "budget": 5000.0}],
            start_time=start_date,
            end_time=end_date,
            idempotency_key="unit-test-key-brand-and-brand-id",
        )

        # Verify brand fields
        assert request.brand.domain == "nike.com"
        # brand_id is wrapped in a BrandId RootModel on some SDK versions
        brand_id = request.brand.brand_id
        # noqa applied on the hasattr line for the rootmodel guard.
        has_root = hasattr(brand_id, "root")  # noqa: rootmodel — SDK-version polymorphism
        brand_id_val = brand_id.root if has_root else brand_id
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
            "signal_id": {"id": "seg_123", "source": "agent", "agent_url": "https://salesagent.adcontextprotocol.org"},
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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
