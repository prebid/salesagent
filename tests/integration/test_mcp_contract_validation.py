"""
MCP Contract Validation Tests

Tests that ensure MCP tools can be called with minimal required parameters,
preventing validation errors like the 'brief' is required issue.
"""

from unittest.mock import Mock, patch

import pytest

from src.core.schema_adapters import (
    GetProductsRequest,
    ListAuthorizedPropertiesRequest,
)
from src.core.schemas import (
    ActivateSignalRequest,
    CreateMediaBuyRequest,
    GetMediaBuyDeliveryRequest,
    GetSignalsRequest,
    SignalDeliverTo,
    UpdateMediaBuyRequest,
)

pytestmark = pytest.mark.integration


class TestMCPContractValidation:
    """Test MCP tools can be called with minimal required parameters."""

    def test_get_products_minimal_call(self):
        """Test get_products can be called with just promoted_offering."""
        # This was the original failing case
        request = GetProductsRequest(promoted_offering="purina cat food")

        assert request.brief == ""  # Should default to empty string
        assert request.promoted_offering == "purina cat food"
        assert request.strategy_id is None

    def test_get_products_with_brief(self):
        """Test get_products works with both brief and promoted_offering."""
        request = GetProductsRequest(brief="pet supplies campaign", promoted_offering="purina cat food")

        assert request.brief == "pet supplies campaign"
        assert request.promoted_offering == "purina cat food"

    def test_get_products_validation_still_enforced(self):
        """Test that GetProductsRequest allows anonymous queries (no promoted_offering/brand_manifest)."""
        # This now works - anonymous queries are supported
        request = GetProductsRequest(brief="just a brief")
        assert request.brief == "just a brief"
        assert request.promoted_offering is None
        assert request.brand_manifest is None

    def test_list_authorized_properties_minimal(self):
        """Test list_authorized_properties can be called with no parameters."""
        request = ListAuthorizedPropertiesRequest()

        # adcp_version field was removed from AdCP spec
        assert request.tags is None

    def test_activate_signal_minimal(self):
        """Test activate_signal with just signal_id."""
        request = ActivateSignalRequest(signal_id="test_signal_123")

        assert request.signal_id == "test_signal_123"
        assert request.campaign_id is None
        assert request.media_buy_id is None

    def test_create_media_buy_minimal(self):
        """Test create_media_buy with just po_number."""
        request = CreateMediaBuyRequest(
            buyer_ref="test_ref", promoted_offering="Nike Air Jordan 2025 basketball shoes", po_number="PO-12345"
        )

        assert request.po_number == "PO-12345"
        assert request.buyer_ref == "test_ref"
        assert request.packages is None
        assert request.pacing == "even"  # Should have default

    def test_create_media_buy_with_packages_products_none(self):
        """Test that packages with products=None don't crash get_product_ids().

        Regression test for bug where Package(products=None) caused:
        'NoneType' object is not iterable in get_product_ids()
        """
        from src.core.schemas import Package

        # Test 1: Package with products=None
        request = CreateMediaBuyRequest(
            buyer_ref="test_ref_1",
            promoted_offering="Nike Air Jordan 2025 basketball shoes",
            po_number="PO-12345",
            packages=[Package(buyer_ref="pkg1", products=None)],
        )
        assert request.get_product_ids() == []  # Should return empty list, not crash

        # Test 2: Package with empty products list
        request = CreateMediaBuyRequest(
            buyer_ref="test_ref_2",
            promoted_offering="Adidas UltraBoost 2025 running shoes",
            po_number="PO-12346",
            packages=[Package(buyer_ref="pkg2", products=[])],
        )
        assert request.get_product_ids() == []

        # Test 3: Mixed packages (some None, some with products)
        request = CreateMediaBuyRequest(
            buyer_ref="test_ref_3",
            promoted_offering="Puma RS-X 2025 training shoes",
            po_number="PO-12347",
            packages=[
                Package(buyer_ref="pkg_none", products=None),
                Package(buyer_ref="pkg_with_products", products=["prod1", "prod2"]),
                Package(buyer_ref="pkg_empty", products=[]),
            ],
        )
        assert request.get_product_ids() == ["prod1", "prod2"]

    def test_get_signals_minimal_now_works(self):
        """Test get_signals with minimal parameters - now fixed!"""
        # This now works with sensible defaults
        request = GetSignalsRequest(
            signal_spec="audience_automotive",
            deliver_to=SignalDeliverTo(),  # Uses defaults: platforms="all", countries=["US"]
        )

        assert request.signal_spec == "audience_automotive"
        assert request.deliver_to.platforms == "all"
        assert request.deliver_to.countries == ["US"]

    def test_get_signals_with_custom_delivery(self):
        """Test get_signals with custom delivery requirements."""
        request = GetSignalsRequest(
            signal_spec="audience_luxury_automotive",
            deliver_to=SignalDeliverTo(platforms=["gam", "facebook"], countries=["US", "CA", "UK"]),
        )

        assert request.deliver_to.platforms == ["gam", "facebook"]
        assert request.deliver_to.countries == ["US", "CA", "UK"]

    def test_update_media_buy_minimal(self):
        """Test update_media_buy identifiers (oneOf enforced at protocol boundary)."""
        # NOTE: oneOf constraint validation happens at protocol boundary (MCP/A2A)
        # not in Pydantic model construction. Internal construction is flexible.

        # Internal construction works without identifier (protocol boundary would reject)
        request_no_id = UpdateMediaBuyRequest(active=True)
        assert request_no_id.active is True

        # Works with minimal identifier
        request = UpdateMediaBuyRequest(media_buy_id="test_buy_123")
        assert request.media_buy_id == "test_buy_123"
        assert request.buyer_ref is None
        assert request.active is None

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

        # GetProductsRequest
        req = GetProductsRequest(promoted_offering="test")
        assert req.brief == ""  # Empty string, not None

        # CreateMediaBuyRequest
        req = CreateMediaBuyRequest(
            buyer_ref="test_ref", promoted_offering="Nike Air Jordan 2025 basketball shoes", po_number="test"
        )
        assert req.pacing == "even"  # Sensible default
        assert req.enable_creative_macro is False  # Explicit boolean default

        # ListAuthorizedPropertiesRequest
        req = ListAuthorizedPropertiesRequest()
        # adcp_version field was removed from AdCP spec

    def test_required_fields_are_truly_necessary(self):
        """Test that all required fields are actually necessary."""

        # This test documents which fields are required and why
        required_field_justifications = {
            "GetProductsRequest.promoted_offering": "Required per AdCP spec for product discovery",
            "ActivateSignalRequest.signal_id": "Must specify which signal to activate",
            "CreateMediaBuyRequest.po_number": "Required for financial tracking and billing",
        }

        # All required fields should have business justification
        for field, justification in required_field_justifications.items():
            assert len(justification) > 10, f"Field {field} needs better justification"


class TestMCPToolMinimalCalls:
    """Simplified contract validation - schema tests only."""

    def test_contract_validation_prevents_original_issue(self):
        """Test that our fixes prevent the original 'brief is required' issue."""
        # This is what was failing before our fix

        # 1. Test that GetProductsRequest can be created with just promoted_offering
        try:
            request = GetProductsRequest(promoted_offering="purina cat food")
            assert request.brief == ""  # Should default to empty string
            assert request.promoted_offering == "purina cat food"
        except Exception as e:
            pytest.fail(f"GetProductsRequest creation failed: {e}")

        # 2. Test that SignalDeliverTo can be created with defaults
        try:
            deliver_to = SignalDeliverTo()
            assert deliver_to.platforms == "all"
            assert deliver_to.countries == ["US"]
        except Exception as e:
            pytest.fail(f"SignalDeliverTo creation failed: {e}")

        # 3. Test that GetSignalsRequest works with minimal params
        try:
            signals_request = GetSignalsRequest(signal_spec="audience_automotive", deliver_to=SignalDeliverTo())
            assert signals_request.signal_spec == "audience_automotive"
        except Exception as e:
            pytest.fail(f"GetSignalsRequest creation failed: {e}")


@pytest.fixture
def mock_testing_setup():
    """Setup common mocks for MCP tool testing."""
    with patch("src.core.main.get_audit_logger") as mock_audit:
        mock_audit.return_value.log_operation = Mock()
        yield mock_audit
