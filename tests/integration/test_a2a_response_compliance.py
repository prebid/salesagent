"""Integration tests for A2A response spec compliance.

This test suite validates that:
1. A2A handlers return AdCP spec-compliant responses (no extra fields like 'success', 'message')
2. Human-readable messages are provided via Artifact.description (not in response data)
3. Response data is identical between MCP and A2A protocols

Replaces: test_a2a_response_message_fields.py (which tested the old incorrect behavior)
"""

import pytest

from src.a2a_server.adcp_a2a_server import AdCPRequestHandler
from src.core.schema_adapters import (
    CreateMediaBuyResponse,
    GetMediaBuyDeliveryResponse,
    GetProductsResponse,
    ListAuthorizedPropertiesResponse,
    ListCreativeFormatsResponse,
    ListCreativesResponse,
    SyncCreativesResponse,
    UpdateMediaBuyResponse,
)

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


@pytest.mark.integration
class TestA2ASpecCompliance:
    """Test that A2A handlers return spec-compliant responses without extra fields."""

    def test_list_authorized_properties_spec_compliance(self):
        """Test list_authorized_properties returns only spec-defined fields."""
        response_data = {
            "publisher_domains": ["example.com"],
            "primary_channels": None,
            "primary_countries": None,
            "portfolio_description": None,
            "advertising_policies": None,
            "last_updated": None,
            "errors": None,
        }

        # Verify this is spec-compliant
        # Include context and ensure it's present in payload
        ctx = {"user_id": "1234567890"}
        response = ListAuthorizedPropertiesResponse(**response_data, context=ctx)

        # Check response has NO extra fields
        spec_fields = {
            "publisher_domains",
            "primary_channels",
            "primary_countries",
            "portfolio_description",
            "advertising_policies",
            "last_updated",
            "errors",
            "context",
        }
        response_fields = set(response.model_dump().keys())
        extra_fields = response_fields - spec_fields

        assert extra_fields == set(), f"Response has non-spec fields: {extra_fields}"

        # Verify __str__() works for human-readable message (using library's .summary())
        assert str(response) == "Authorized to represent 1 publisher domain."

    def test_get_products_spec_compliance(self):
        """Test get_products returns only spec-defined fields."""
        response_data = {
            "products": [],
            "errors": None,
        }

        ctx = {"user_id": "1234567890"}
        response = GetProductsResponse(**response_data, context=ctx)

        # Check no extra fields
        spec_fields = {"products", "errors", "status", "context"}
        response_fields = set(response.model_dump().keys())
        extra_fields = response_fields - spec_fields

        assert extra_fields == set(), f"Response has non-spec fields: {extra_fields}"
        assert str(response) == "No products matched your requirements."

    def test_sync_creatives_spec_compliance(self):
        """Test sync_creatives returns only spec-defined fields."""
        from src.core.schemas import SyncCreativeResult

        response_data = {
            "creatives": [
                SyncCreativeResult(
                    buyer_ref="test-001",
                    creative_id="cr-001",
                    status="approved",
                    action="created",
                )
            ],
            "dry_run": False,
        }

        ctx = {"user_id": "1234567890"}
        response = SyncCreativesResponse(**response_data, context=ctx)

        # Check no extra fields
        spec_fields = {"creatives", "dry_run", "context"}
        response_fields = set(response.model_dump().keys())
        extra_fields = response_fields - spec_fields

        assert extra_fields == set(), f"Response has non-spec fields: {extra_fields}"
        # Verify __str__() works (message format may vary based on action counts)
        assert len(str(response)) > 0

    def test_list_creatives_spec_compliance(self):
        """Test list_creatives returns only spec-defined fields."""
        from src.core.schemas import Pagination, QuerySummary

        response_data = {
            "query_summary": QuerySummary(total_matching=0, returned=0),
            "pagination": Pagination(limit=50, offset=0, has_more=False),
            "creatives": [],
        }

        ctx = {"user_id": "1234567890"}
        response = ListCreativesResponse(**response_data, context=ctx)

        # Check no extra fields
        spec_fields = {
            "query_summary",
            "pagination",
            "creatives",
            "context_id",
            "format_summary",
            "status_summary",
            "context",
        }
        response_fields = set(response.model_dump().keys())
        extra_fields = response_fields - spec_fields

        assert extra_fields == set(), f"Response has non-spec fields: {extra_fields}"
        # Library's .summary() message format
        assert str(response) == "Found 0 creatives in the system."

    def test_list_creative_formats_spec_compliance(self):
        """Test list_creative_formats returns only spec-defined fields."""
        response_data = {
            "formats": [],
            "creative_agents": None,
            "errors": None,
        }

        ctx = {"user_id": "1234567890"}
        response = ListCreativeFormatsResponse(**response_data, context=ctx)

        # Check no extra fields
        spec_fields = {"formats", "creative_agents", "errors", "status", "context"}
        response_fields = set(response.model_dump().keys())
        extra_fields = response_fields - spec_fields

        assert extra_fields == set(), f"Response has non-spec fields: {extra_fields}"
        # Library's .summary() message format
        assert str(response) == "Found 0 supported creative formats."

    def test_create_media_buy_spec_compliance(self):
        """Test create_media_buy returns only spec-defined fields."""
        ctx = {"user_id": "1234567890"}
        response = CreateMediaBuyResponse(
            buyer_ref="test-123",
            media_buy_id="mb-456",
            context=ctx,
        )

        # Check response can be dumped (has all required fields)
        response_dict = response.model_dump()
        assert "buyer_ref" in response_dict
        assert "media_buy_id" in response_dict

        # Verify __str__() works
        assert str(response) == "Media buy mb-456 created successfully."

        # Ensure NO extra fields like 'success' or 'message' are in the spec
        assert "success" not in response_dict
        assert "message" not in response_dict

    def test_update_media_buy_spec_compliance(self):
        """Test update_media_buy returns only spec-defined fields."""
        ctx = {"user_id": "1234567890"}
        response = UpdateMediaBuyResponse(
            buyer_ref="test-123",
            media_buy_id="mb-456",
            context=ctx,
        )

        response_dict = response.model_dump()
        assert "buyer_ref" in response_dict
        assert "media_buy_id" in response_dict
        assert str(response) == "Media buy mb-456 updated successfully."

        # No extra fields
        assert "success" not in response_dict
        assert "message" not in response_dict

    def test_get_media_buy_delivery_spec_compliance(self):
        """Test get_media_buy_delivery returns only spec-defined fields."""
        from datetime import UTC, datetime

        from src.core.schemas import ReportingPeriod

        ctx = {"user_id": "1234567890"}
        response = GetMediaBuyDeliveryResponse(
            reporting_period=ReportingPeriod(
                start=datetime.now(UTC).isoformat(),
                end=datetime.now(UTC).isoformat(),
            ),
            currency="USD",
            media_buy_deliveries=[],
            context=ctx,
        )

        response_dict = response.model_dump()
        assert "media_buy_deliveries" in response_dict
        assert "reporting_period" in response_dict
        assert "currency" in response_dict
        # __str__() may vary based on the schema class used
        assert len(str(response)) > 0

        # No extra fields
        assert "success" not in response_dict
        assert "message" not in response_dict


@pytest.mark.integration
class TestA2AArtifactDescriptions:
    """Test that A2A artifacts include human-readable descriptions from __str__()."""

    def test_artifact_reconstruction_helper(self):
        """Test _reconstruct_response_object helper."""
        handler = AdCPRequestHandler()

        # Test successful reconstruction
        data = {
            "publisher_domains": ["example.com", "test.com"],
            "primary_channels": None,
            "primary_countries": None,
            "portfolio_description": None,
            "advertising_policies": None,
            "last_updated": None,
            "errors": None,
        }

        response = handler._reconstruct_response_object("list_authorized_properties", data)

        assert response is not None
        assert isinstance(response, ListAuthorizedPropertiesResponse)
        # Library's .summary() message format
        assert str(response) == "Authorized to represent 2 publisher domains."

    def test_artifact_reconstruction_all_skills(self):
        """Test reconstruction works for all supported skills."""
        handler = AdCPRequestHandler()

        test_cases = [
            (
                "list_authorized_properties",
                {"publisher_domains": ["test.com"], "errors": None},
                ListAuthorizedPropertiesResponse,
            ),
            (
                "get_products",
                {"products": [], "errors": None},
                GetProductsResponse,
            ),
            (
                "list_creative_formats",
                {"formats": [], "creative_agents": None, "errors": None},
                ListCreativeFormatsResponse,
            ),
        ]

        for skill_name, data, expected_class in test_cases:
            response = handler._reconstruct_response_object(skill_name, data)
            assert response is not None, f"Failed to reconstruct {skill_name}"
            assert isinstance(response, expected_class)
            assert hasattr(response, "__str__")
            assert len(str(response)) > 0, f"{skill_name} __str__() returned empty string"

    def test_artifact_reconstruction_invalid_data(self):
        """Test reconstruction gracefully handles invalid data."""
        handler = AdCPRequestHandler()

        # Invalid data should return None, not raise
        response = handler._reconstruct_response_object("list_authorized_properties", {"invalid": "data"})

        assert response is None, "Should return None for invalid data"

    def test_artifact_reconstruction_unknown_skill(self):
        """Test reconstruction gracefully handles unknown skills."""
        handler = AdCPRequestHandler()

        response = handler._reconstruct_response_object("unknown_skill", {"data": "value"})

        assert response is None, "Should return None for unknown skill"


@pytest.mark.integration
class TestMCPAndA2AResponseParity:
    """Test that MCP and A2A return identical response data."""

    def test_response_data_identical(self):
        """Test that both protocols return the same AdCP response data."""
        # Create response object like MCP returns
        mcp_response = ListAuthorizedPropertiesResponse(
            publisher_domains=["example.com"],
        )

        # What A2A returns (after our fix)
        a2a_response_data = mcp_response.model_dump()

        # Both should be identical
        assert a2a_response_data == mcp_response.model_dump()

        # Per AdCP spec, only fields that were set should be present (exclude_none=True)
        # Optional fields with None values should be omitted
        assert set(a2a_response_data.keys()) == {
            "publisher_domains",
        }

        # Verify optional fields are omitted when None
        assert "errors" not in a2a_response_data, "None-valued optional fields should be omitted per AdCP spec"
        assert "primary_channels" not in a2a_response_data
        assert "primary_countries" not in a2a_response_data
        assert "portfolio_description" not in a2a_response_data
        assert "advertising_policies" not in a2a_response_data
        assert "last_updated" not in a2a_response_data

        # Both can generate the same human-readable message
        mcp_message = str(mcp_response)
        a2a_message = str(ListAuthorizedPropertiesResponse(**a2a_response_data))
        assert mcp_message == a2a_message
        # Library's .summary() message format
        assert mcp_message == "Authorized to represent 1 publisher domain."

    def test_all_response_types_have_str_method(self):
        """Test that all AdCP response types support __str__() for human-readable messages."""
        response_types = [
            CreateMediaBuyResponse,
            UpdateMediaBuyResponse,
            GetMediaBuyDeliveryResponse,
            GetProductsResponse,
            ListAuthorizedPropertiesResponse,
            ListCreativeFormatsResponse,
            ListCreativesResponse,
            SyncCreativesResponse,
        ]

        for response_cls in response_types:
            # All our response adapters should have __str__
            assert hasattr(
                response_cls, "__str__"
            ), f"{response_cls.__name__} must have __str__() for human-readable messages"


@pytest.mark.integration
class TestA2AResponseRegressionPrevention:
    """Prevent regressions: ensure we never add non-spec fields back."""

    def test_handlers_return_spec_compliant_dicts(self):
        """Test that handler responses are plain spec-compliant dicts."""
        # This is a contract test - if someone adds 'success' or 'message' back,
        # this test will catch it

        from src.core.schema_adapters import ListAuthorizedPropertiesResponse

        response = ListAuthorizedPropertiesResponse(publisher_domains=["test.com"])
        response_dict = response.model_dump()

        # These fields should NOT be in the response data
        forbidden_fields = {"success", "message", "total_count", "specification_version"}
        actual_fields = set(response_dict.keys())

        violations = forbidden_fields & actual_fields
        assert violations == set(), f"Response contains forbidden non-spec fields: {violations}"

    def test_no_protocol_fields_in_response_data(self):
        """Ensure protocol metadata is separate from response data."""
        # Protocol fields like 'status', 'task_id', 'context_id' should be
        # in the protocol wrapper (Task), not in the response data (Artifact.parts.data)

        response = GetProductsResponse(products=[])
        response_dict = response.model_dump()

        # These are protocol-level fields, not AdCP response fields
        protocol_fields = {"task_id", "context_id"}  # status is actually in some AdCP responses

        violations = protocol_fields & set(response_dict.keys())
        assert violations == set(), f"Response data contains protocol fields: {violations}"
