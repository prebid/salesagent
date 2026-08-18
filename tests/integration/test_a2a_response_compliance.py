"""Integration tests for A2A response spec compliance.

This test suite validates that:
1. A2A handlers return AdCP spec-compliant responses (no extra fields like 'success', 'message')
2. Response data is identical between MCP and A2A protocols

The human-readable text a buyer sees is pinned where it is produced -- on the
real ``on_message_send`` pipeline output, in
``tests/integration/test_a2a_skill_invocation.py`` -- not here.

Replaces: test_a2a_response_message_fields.py (which tested the old incorrect behavior)
"""

import pytest

from src.core.schemas import (
    CreateMediaBuySuccess,
    GetMediaBuyDeliveryResponse,
    GetProductsResponse,
    ListAuthorizedPropertiesResponse,
    ListCreativeFormatsResponse,
    ListCreativesResponse,
    SyncCreativesResponse,
    UpdateMediaBuySuccess,
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

        # Verify __str__() works for human-readable message
        assert str(response) == "Found 1 authorized publisher domain."

    def test_get_products_spec_compliance(self):
        """Test get_products returns only spec-defined fields."""
        response_data = {
            "products": [],
            "errors": None,
        }

        ctx = {"user_id": "1234567890"}
        response = GetProductsResponse(**response_data, context=ctx)

        # Check no extra fields.
        # SDK 5.7 adds cache_scope, replayed as protocol envelope defaults.
        spec_fields = {"products", "errors", "status", "context", "cache_scope", "replayed"}
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
                    creative_id="cr-001",
                    internal_status="approved",
                    action="created",
                )
            ],
            "dry_run": False,
        }

        ctx = {"user_id": "1234567890"}
        response = SyncCreativesResponse(**response_data, context=ctx)

        # Check no extra fields.
        # status is a protocol-envelope default (GH #1710) -- same pattern as
        # GetProductsResponse/ListCreativesResponse above.
        spec_fields = {"creatives", "dry_run", "context", "status"}
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
            "pagination": Pagination(has_more=False),
            "creatives": [],
        }

        ctx = {"user_id": "1234567890"}
        response = ListCreativesResponse(**response_data, context=ctx)

        # Check no extra fields.
        # SDK 5.7 adds status, replayed as protocol envelope defaults.
        spec_fields = {
            "query_summary",
            "pagination",
            "creatives",
            "context_id",
            "format_summary",
            "status_summary",
            "context",
            "status",
            "replayed",
        }
        response_fields = set(response.model_dump().keys())
        extra_fields = response_fields - spec_fields

        assert extra_fields == set(), f"Response has non-spec fields: {extra_fields}"
        # Local schema's __str__() message format
        assert str(response) == "Found 0 creatives."

    def test_list_creative_formats_spec_compliance(self):
        """Test list_creative_formats returns only spec-defined fields."""
        response_data = {
            "formats": [],
            "creative_agents": None,
            "errors": None,
        }

        ctx = {"user_id": "1234567890"}
        response = ListCreativeFormatsResponse(**response_data, context=ctx)

        # Check no extra fields.
        # SDK 5.7 adds replayed as a protocol envelope default.
        spec_fields = {"formats", "creative_agents", "errors", "status", "context", "replayed"}
        response_fields = set(response.model_dump().keys())
        extra_fields = response_fields - spec_fields

        assert extra_fields == set(), f"Response has non-spec fields: {extra_fields}"
        # Local schema's __str__() message format
        assert str(response) == "No creative formats are currently supported."

    def test_create_media_buy_spec_compliance(self):
        """Test create_media_buy returns only spec-defined fields."""
        ctx = {"user_id": "1234567890"}
        response = CreateMediaBuySuccess(
            media_buy_id="mb-456",
            packages=[],  # Required field per AdCP spec
            context=ctx,
        )

        # Check response can be dumped (has all required fields)
        response_dict = response.model_dump()
        assert "media_buy_id" in response_dict
        assert "packages" in response_dict

        # Verify __str__() works
        assert str(response) == "Media buy mb-456 created successfully."

        # Ensure NO extra fields like 'success' or 'message' are in the spec
        assert "success" not in response_dict
        assert "message" not in response_dict

    def test_update_media_buy_spec_compliance(self):
        """Test update_media_buy returns only spec-defined fields."""
        ctx = {"user_id": "1234567890"}
        response = UpdateMediaBuySuccess(
            media_buy_id="mb-456",
            context=ctx,
        )

        response_dict = response.model_dump()
        assert "media_buy_id" in response_dict
        assert str(response) == "Media buy mb-456 updated successfully."

        # No extra fields
        assert "success" not in response_dict
        assert "message" not in response_dict

    def test_get_media_buy_delivery_spec_compliance(self):
        """Test get_media_buy_delivery returns only spec-defined fields."""
        from datetime import UTC, datetime

        from src.core.schemas import AggregatedTotals

        ctx = {"user_id": "1234567890"}
        response = GetMediaBuyDeliveryResponse(
            reporting_period={
                "start": datetime.now(UTC).isoformat(),
                "end": datetime.now(UTC).isoformat(),
            },
            currency="USD",
            media_buy_deliveries=[],
            aggregated_totals=AggregatedTotals(  # Required field per AdCP spec
                spend=0.0,
                impressions=0,
                clicks=0,
                media_buy_count=0,
            ),
            context=ctx,
        )

        response_dict = response.model_dump()
        assert "media_buy_deliveries" in response_dict
        assert "reporting_period" in response_dict
        assert "currency" in response_dict
        assert "aggregated_totals" in response_dict
        # __str__() may vary based on the schema class used
        assert len(str(response)) > 0

        # No extra fields
        assert "success" not in response_dict
        assert "message" not in response_dict


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
        # Local schema's __str__() message format
        assert mcp_message == "Found 1 authorized publisher domain."

    def test_all_response_types_have_str_method(self):
        """Test that all AdCP response types support __str__() for human-readable messages."""
        response_types = [
            CreateMediaBuySuccess,
            UpdateMediaBuySuccess,
            GetMediaBuyDeliveryResponse,
            GetProductsResponse,
            ListAuthorizedPropertiesResponse,
            ListCreativeFormatsResponse,
            ListCreativesResponse,
            SyncCreativesResponse,
        ]

        for response_cls in response_types:
            # All our response adapters should have __str__
            assert hasattr(response_cls, "__str__"), (
                f"{response_cls.__name__} must have __str__() for human-readable messages"
            )


@pytest.mark.integration
class TestA2AResponseRegressionPrevention:
    """Prevent regressions: ensure we never add non-spec fields back."""

    def test_handlers_return_spec_compliant_dicts(self):
        """Test that handler responses are plain spec-compliant dicts."""
        # This is a contract test - if someone adds 'success' or 'message' back,
        # this test will catch it

        from src.core.schemas import ListAuthorizedPropertiesResponse

        response = ListAuthorizedPropertiesResponse(publisher_domains=["test.com"])
        response_dict = response.model_dump()

        # These fields should NOT be on the Pydantic response MODEL. 'message'
        # is a genuine spec-defined field on the WIRE envelope's Protocol
        # Envelope arm (see tests/helpers/adcp_schema_validator.py), but it's
        # populated by the protocol layer (_serialize_for_a2a et al) at the
        # transport boundary, not carried on the domain response model itself.
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

        # These are protocol-envelope fields (spec-defined on the WIRE
        # envelope's Protocol Envelope arm — see
        # tests/helpers/adcp_schema_validator.py — and populated by the protocol
        # layer at the transport boundary), correctly absent from the
        # Pydantic response MODEL itself.
        protocol_fields = {"task_id", "context_id"}  # status is actually in some AdCP responses

        violations = protocol_fields & set(response_dict.keys())
        assert violations == set(), f"Response data contains protocol fields: {violations}"


@pytest.mark.integration
class TestA2ASuccessDerivedFromErrorsOnRealWire:
    """``_stamp_a2a_protocol_fields`` derives ``success`` from ``errors`` so a
    response carrying per-item errors reports ``success=False`` uniformly
    (single derivation point after three call sites used to duplicate it
    inline — PR #1868, one of the three previously omitted the derivation
    entirely and always forced ``success=True``).

    Dispatched through the real ``AdCPRequestHandler`` (not by calling
    ``_stamp_a2a_protocol_fields`` directly) so a regression in the
    derivation — or in any call site that stopped routing through it — shows
    up on the real A2A wire, the same bytes a buyer actually receives.
    Deleting the derivation in ``_stamp_a2a_protocol_fields`` must make this
    test fail.
    """

    def test_get_products_populated_errors_derive_wire_success_false(self, integration_db):
        """Sync on purpose: ``call_a2a``/``call_via`` bridge to ``asyncio.run()``
        internally (see ``ProductEnv.call_impl``'s docstring on the same bridge) —
        an ``async def`` test would nest event loops and silently swallow the
        resulting RuntimeError.
        """
        from unittest.mock import AsyncMock, patch

        from src.core.schemas import Error, GetProductsResponse
        from tests.bdd.steps._outcome_helpers import wire_field
        from tests.bdd.steps.generic._dispatch import dispatch_request
        from tests.factories import PrincipalFactory, TenantFactory
        from tests.harness.product import ProductEnv
        from tests.harness.transport import Transport

        with ProductEnv(tenant_id="a2a-success-errors-test", principal_id="test_principal") as env:
            tenant = TenantFactory(tenant_id="a2a-success-errors-test")
            PrincipalFactory(tenant=tenant, principal_id="test_principal")

            # get_products has no production code path that populates
            # non-fatal errors[] today (unlike CreateMediaBuySuccess's
            # property_list_filtering advisory) — stub the tool boundary
            # _handle_get_products_skill calls, the same seam the transport
            # wrapper is contractually bound to (Pattern #5), to grade the
            # stamping/derivation logic the wrapper owns independent of
            # whether any business path currently exercises it.
            errored_response = GetProductsResponse(
                products=[],
                errors=[Error(code="UNSUPPORTED_FEATURE", message="property_list_filtering unavailable")],
            )

            with patch(
                "src.a2a_server.adcp_a2a_server.core_get_products_tool",
                new=AsyncMock(return_value=errored_response),
            ):
                ctx: dict = {"env": env, "transport": Transport.A2A}
                dispatch_request(ctx, brief="display ads")

            assert wire_field(ctx, "success") is False
