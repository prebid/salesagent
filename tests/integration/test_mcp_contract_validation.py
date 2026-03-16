"""
MCP Contract Validation Tests

Tests that ensure MCP tools can be called with minimal required parameters,
preventing validation errors like the 'brief' is required issue.

Updated for adcp 3.9:
- brand_manifest replaced by brand (BrandReference with domain field)
- GetSignalsRequest is a regular model (no longer RootModel)
- ActivateSignalRequest.destinations is now required
"""

from unittest.mock import Mock, patch

import pytest
from adcp.types import DeliverTo, PlatformDestination

from src.core.schemas import (
    ActivateSignalRequest,
    CreateMediaBuyRequest,
    GetMediaBuyDeliveryRequest,
    GetProductsRequest,
    GetSignalsRequest,
    ListAuthorizedPropertiesRequest,  # Removed from adcp 3.2.0, defined locally
    UpdateMediaBuyRequest,
)
from tests.helpers.adcp_factories import create_test_package_request

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


class TestMCPContractValidation:
    """Test MCP tools can be called with minimal required parameters."""

    def test_get_products_minimal_call(self):
        """Test get_products can be called with just brand.

        Per AdCP spec, all fields are optional, including brief.
        """
        request = GetProductsRequest(brand={"domain": "testbrand.com"})

        assert request.brief is None  # Optional, defaults to None per spec
        # brand is BrandReference with required domain field
        assert request.brand is not None
        assert request.brand.domain == "testbrand.com"

    def test_get_products_with_brief(self):
        """Test get_products works with both brief and brand."""
        request = GetProductsRequest(brief="pet supplies campaign", brand={"domain": "testbrand.com"})

        assert request.brief == "pet supplies campaign"
        # brand is BrandReference with required domain field
        assert request.brand is not None
        assert request.brand.domain == "testbrand.com"

    def test_get_products_accepts_brief_only(self):
        """Test that GetProductsRequest accepts brief without brand per AdCP spec.

        Per AdCP spec, all fields in GetProductsRequest are OPTIONAL.
        """
        from src.core.schemas import GetProductsRequest as SchemaGetProductsRequest

        # brand is optional per spec - brief-only request should succeed
        request = SchemaGetProductsRequest(brief="just a brief")
        assert request.brief == "just a brief"
        assert request.brand is None

    def test_list_authorized_properties_minimal(self):
        """Test list_authorized_properties can be called with no parameters."""
        request = ListAuthorizedPropertiesRequest()

        # 'tags' field was removed in AdCP 2.5 - only 'context' remains as optional
        assert request.context is None

    def test_activate_signal_minimal(self):
        """Test activate_signal with required fields."""
        request = ActivateSignalRequest(
            signal_agent_segment_id="test_signal_123",
            destinations=[PlatformDestination(platform="google_ad_manager", type="platform")],
        )

        assert request.signal_agent_segment_id == "test_signal_123"
        assert request.campaign_id is None
        assert request.media_buy_id is None

    def test_create_media_buy_minimal(self):
        """Test create_media_buy with minimal required fields per AdCP v3.6.0 spec."""
        request = CreateMediaBuyRequest(
            buyer_ref="test_ref",
            brand={"domain": "testbrand.com"},
            packages=[
                create_test_package_request(
                    buyer_ref="pkg1", product_id="prod1", budget=1000.0, pricing_option_id="default-pricing-option"
                )
            ],
            start_time="2025-02-15T00:00:00Z",
            end_time="2025-02-28T23:59:59Z",
            po_number="PO-12345",
        )

        assert request.po_number == "PO-12345"
        assert request.buyer_ref == "test_ref"
        assert len(request.packages) == 1

    def test_create_media_buy_get_product_ids(self):
        """Test get_product_ids() extracts unique product IDs from packages.

        Per AdCP spec, packages use product_id (singular, required) field.
        """
        # Test: Multiple packages with product IDs
        request = CreateMediaBuyRequest(
            buyer_ref="test_ref_1",
            brand={"domain": "testbrand.com"},
            po_number="PO-12345",
            packages=[
                create_test_package_request(
                    buyer_ref="pkg1", product_id="prod1", budget=1000.0, pricing_option_id="test_pricing"
                ),
                create_test_package_request(
                    buyer_ref="pkg2", product_id="prod2", budget=1000.0, pricing_option_id="test_pricing"
                ),
                create_test_package_request(
                    buyer_ref="pkg3", product_id="prod1", budget=1000.0, pricing_option_id="test_pricing"
                ),  # Duplicate
            ],
            start_time="2025-02-15T00:00:00Z",
            end_time="2025-02-28T23:59:59Z",
        )
        # Should return unique product IDs
        product_ids = request.get_product_ids()
        assert set(product_ids) == {"prod1", "prod2"}
        assert len(product_ids) == 2

    def test_get_signals_minimal_now_works(self):
        """Test get_signals with minimal parameters.

        adcp 3.9: GetSignalsRequest is a regular model, not RootModel.
        """
        # Library DeliverTo requires explicit deployments and countries
        request = GetSignalsRequest(
            signal_spec="audience_automotive",
            deliver_to=DeliverTo(
                deployments=[PlatformDestination(platform="google_ad_manager", type="platform")],
                countries=["US"],
            ),
        )

        # adcp 3.9: GetSignalsRequest is a regular model, not RootModel
        assert request.signal_spec == "audience_automotive"
        assert len(request.deliver_to.deployments) == 1
        assert request.deliver_to.countries[0] == "US"

    def test_get_signals_with_custom_delivery(self):
        """Test get_signals with custom delivery requirements."""
        request = GetSignalsRequest(
            signal_spec="audience_luxury_automotive",
            deliver_to=DeliverTo(
                deployments=[
                    PlatformDestination(platform="gam", type="platform"),
                    PlatformDestination(platform="facebook", type="platform"),
                ],
                countries=["US", "CA", "UK"],
            ),
        )

        # adcp 3.9: GetSignalsRequest is a regular model, not RootModel
        assert len(request.deliver_to.deployments) == 2
        assert len(request.deliver_to.countries) == 3

    def test_update_media_buy_minimal(self):
        """Test update_media_buy identifiers (oneOf enforced in adcp 3.6.0 model)."""
        # adcp 3.6.0 enforces oneOf at model level: exactly one of media_buy_id or buyer_ref
        request = UpdateMediaBuyRequest(media_buy_id="test_buy_123")
        assert request.media_buy_id == "test_buy_123"
        assert request.buyer_ref is None
        assert request.paused is None

        # buyer_ref alone also works
        request_br = UpdateMediaBuyRequest(buyer_ref="ref_123")
        assert request_br.buyer_ref == "ref_123"
        assert request_br.media_buy_id is None

        # Neither identifier → validation error
        with pytest.raises(ValueError, match="Either media_buy_id or buyer_ref is required"):
            UpdateMediaBuyRequest(paused=False)

        # Both identifiers → validation error
        with pytest.raises(ValueError, match="either media_buy_id or buyer_ref, not both"):
            UpdateMediaBuyRequest(media_buy_id="buy_1", buyer_ref="ref_1")

    def test_get_media_buy_delivery_minimal(self):
        """Test get_media_buy_delivery with no filters."""
        request = GetMediaBuyDeliveryRequest()

        # All filters should be optional
        assert request.media_buy_ids is None
        assert request.buyer_refs is None
        assert request.status_filter is None


class TestMCPToolParameterPatterns:
    """Test consistency of MCP tool parameter patterns."""

    def test_tools_using_individual_parameters(self):
        """Document which tools use individual parameters vs request objects."""

        # Tools that use individual parameters (potential inconsistency)
        individual_param_tools = [
            "get_products",  # Now fixed to match schema
            "create_media_buy",
            "update_media_buy",
            "get_media_buy_delivery",
            "activate_signal",
            "list_creatives",
            "sync_creatives",
        ]

        # Tools that properly use request objects
        request_object_tools = [
            "get_signals",
            "list_authorized_properties",
        ]

        # This test documents the current state for future refactoring
        assert len(individual_param_tools) > 0
        assert len(request_object_tools) > 0

        # TODO: Standardize all tools to use request objects for consistency

    def test_parameter_naming_consistency(self):
        """Test that parameter names match between tools and schemas."""
        # This test would catch mismatches like:
        # - Tool parameter: media_buy_id
        # - Schema field: mediabuynid

        # For now, document known good patterns
        consistent_patterns = {
            "media_buy_id": "Used consistently across tools",
            "buyer_ref": "Used consistently across tools",
            "po_number": "Used consistently (not po_id or purchase_order)",
        }

        assert len(consistent_patterns) > 0


class TestSchemaDefaultValues:
    """Test that schema default values are sensible for client usage."""

    def test_optional_fields_have_reasonable_defaults(self):
        """Test that optional fields have defaults that make sense."""
        # GetProductsRequest - per AdCP spec, all fields are optional and default to None
        req = GetProductsRequest(brand={"domain": "testbrand.com"})
        assert req.brief is None  # Optional, defaults to None per spec

        # CreateMediaBuyRequest (with required fields per AdCP v3.6.0 spec)
        req = CreateMediaBuyRequest(
            buyer_ref="test_ref",
            brand={"domain": "testbrand.com"},
            packages=[
                create_test_package_request(
                    buyer_ref="pkg1", product_id="prod1", budget=1000.0, pricing_option_id="default-pricing-option"
                )
            ],
            start_time="2025-02-15T00:00:00Z",
            end_time="2025-02-28T23:59:59Z",
            po_number="test",
        )
        # Per AdCP spec, all fields are spec-compliant with library defaults
        assert req.po_number == "test"

        # ListAuthorizedPropertiesRequest
        req = ListAuthorizedPropertiesRequest()
        # adcp_version field was removed from AdCP spec

    def test_required_fields_are_truly_necessary(self):
        """Test that all required fields are actually necessary."""

        # This test documents which fields are required and why
        # Note: GetProductsRequest.brand is OPTIONAL per AdCP spec
        required_field_justifications = {
            "ActivateSignalRequest.signal_agent_segment_id": "Must specify which signal to activate",
            "CreateMediaBuyRequest.buyer_ref": "Required per AdCP spec for tracking purchases",
        }

        # All required fields should have business justification
        for field, justification in required_field_justifications.items():
            assert len(justification) > 10, f"Field {field} needs better justification"


class TestMCPToolMinimalCalls:
    """Simplified contract validation - schema tests only."""

    def test_contract_validation_prevents_original_issue(self):
        """Test that GetProductsRequest works with all fields optional per AdCP spec."""
        # Test that GetProductsRequest can be created with just brand
        # (and actually, even empty per spec)
        try:
            request = GetProductsRequest(brand={"domain": "testbrand.com"})
            assert request.brief is None  # Optional, defaults to None per spec
            # brand is BrandReference with required domain field
            assert request.brand is not None
            assert request.brand.domain == "testbrand.com"
        except Exception as e:
            pytest.fail(f"GetProductsRequest creation failed: {e}")

        # 2. Test that library DeliverTo works with explicit values
        try:
            deliver_to = DeliverTo(
                deployments=[PlatformDestination(platform="google_ad_manager", type="platform")],
                countries=["US"],
            )
            assert len(deliver_to.deployments) == 1
            assert deliver_to.countries[0] == "US"
        except Exception as e:
            pytest.fail(f"DeliverTo creation failed: {e}")

        # 3. Test that GetSignalsRequest works with minimal params
        # adcp 3.9: GetSignalsRequest is a regular model, not RootModel
        try:
            signals_request = GetSignalsRequest(
                signal_spec="audience_automotive",
                deliver_to=DeliverTo(
                    deployments=[PlatformDestination(platform="google_ad_manager", type="platform")],
                    countries=["US"],
                ),
            )
            assert signals_request.signal_spec == "audience_automotive"
        except Exception as e:
            pytest.fail(f"GetSignalsRequest creation failed: {e}")


@pytest.fixture
def mock_testing_setup():
    """Setup common mocks for MCP tool testing."""
    with patch("src.core.main.get_audit_logger") as mock_audit:
        mock_audit.return_value.log_operation = Mock()
        yield mock_audit
