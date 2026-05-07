"""MCP Tool Roundtrip Tests with Minimal Parameters.

These tests verify that MCP tools work correctly when called with only required parameters,
catching issues like the datetime.combine() bug where optional fields defaulted to None
and caused errors.

Focus: Test parameter-to-schema mapping, not business logic.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastmcp.client import Client
from fastmcp.client.transports import StreamableHttpTransport

# adcp 4.4 wire-required envelope on mutation tools — buyers must supply
# both. Match the pattern used by tests/e2e/adcp_request_builder.py so test
# inputs reflect real-buyer wire shape rather than the schema we'd prefer.
_WIRE_BRAND = {"domain": "testbrand.com"}
_WIRE_ACCOUNT = {"brand": _WIRE_BRAND, "operator": "testbrand.com"}


def _wire_envelope(prefix: str) -> dict:
    """Return ``account`` + ``idempotency_key`` for inclusion in mutation requests."""
    return {"account": _WIRE_ACCOUNT, "idempotency_key": f"{prefix}-{uuid.uuid4()}"}


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.requires_db
class TestMCPToolRoundtripMinimal:
    """Test MCP tools with minimal parameters to catch schema construction bugs.

    Uses the mcp_server fixture which starts a real MCP server with test database.
    """

    @pytest.fixture
    async def mcp_client(self, mcp_server, sample_tenant, sample_principal, sample_products):
        """Create MCP client for testing with test data."""
        # Use the mcp_server fixture which provides port and manages lifecycle
        headers = {"x-adcp-auth": sample_principal["access_token"]}
        transport = StreamableHttpTransport(url=f"http://localhost:{mcp_server.port}/mcp/", headers=headers)
        client = Client(transport=transport)

        async with client:
            yield client

    async def test_get_products_minimal(self, mcp_client):
        """Test get_products with only required parameter (promoted_offering)."""
        result = await mcp_client.call_tool("get_products", {"brand": {"domain": "testbrand.com"}})

        assert result is not None
        # FastMCP call_tool returns structured_content
        content = result.structured_content if hasattr(result, "structured_content") else result
        assert "products" in content

    async def test_create_media_buy_minimal(self, mcp_client):
        """Test create_media_buy with minimal required parameters."""
        # Get a product first
        products_result = await mcp_client.call_tool(
            "get_products", {"brand": {"domain": "testbrand.com"}, "brief": "test"}
        )

        products = (
            products_result.structured_content if hasattr(products_result, "structured_content") else products_result
        )
        if products and len(products.get("products", [])) > 0:
            product_id = products["products"][0]["product_id"]

            # Create media buy with minimal required AdCP params
            result = await mcp_client.call_tool(
                "create_media_buy",
                {
                    "brand": {"domain": "testbrand.com"},
                    "packages": [
                        {
                            "product_id": product_id,
                            "pricing_option_id": "cpm_usd_fixed",  # Format: {model}_{currency}_{fixed|auction}
                            "budget": 1000.0,
                        }
                    ],
                    "start_time": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
                    "end_time": (datetime.now(UTC) + timedelta(days=30)).isoformat(),
                },
            )

            assert result is not None
            content = result.structured_content if hasattr(result, "structured_content") else result
            assert "media_buy_id" in content or "status" in content

    async def test_update_media_buy_minimal(self, mcp_client):
        """Test update_media_buy with minimal parameters (no today field).

        This specifically tests the datetime.combine() bug fix where req.today
        was accessed but didn't exist in the schema.
        """
        # Create a media buy first
        products_result = await mcp_client.call_tool(
            "get_products", {"brand": {"domain": "testbrand.com"}, "brief": "test"}
        )

        products = (
            products_result.structured_content if hasattr(products_result, "structured_content") else products_result
        )
        if products and len(products.get("products", [])) > 0:
            product_id = products["products"][0]["product_id"]

            create_result = await mcp_client.call_tool(
                "create_media_buy",
                {
                    "brand": {"domain": "testbrand.com"},
                    "packages": [
                        {
                            "product_id": product_id,
                            "pricing_option_id": "cpm_usd_fixed",  # Format: {model}_{currency}_{fixed|auction}
                            "budget": 1000.0,
                        }
                    ],
                    "start_time": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
                    "end_time": (datetime.now(UTC) + timedelta(days=30)).isoformat(),
                },
            )

            create_content = (
                create_result.structured_content if hasattr(create_result, "structured_content") else create_result
            )
            if "media_buy_id" in create_content:
                # Now update it - this tests the datetime.combine code path
                update_result = await mcp_client.call_tool(
                    "update_media_buy",
                    {
                        "media_buy_id": create_content["media_buy_id"],
                        "paused": True,  # adcp 2.12.0+: paused=True means pause, paused=False means resume
                        **_wire_envelope("roundtrip-update"),
                    },
                )

                assert update_result is not None
                update_content = (
                    update_result.structured_content if hasattr(update_result, "structured_content") else update_result
                )
                assert "media_buy_id" in update_content
                # Should not get TypeError: combine() argument 1 must be datetime.date, not None

    async def test_get_media_buy_delivery_minimal(self, mcp_client):
        """Test get_media_buy_delivery with minimal parameters."""
        result = await mcp_client.call_tool("get_media_buy_delivery", {})  # All parameters are optional

        assert result is not None
        content = result.structured_content if hasattr(result, "structured_content") else result
        assert "deliveries" in content or "aggregated_totals" in content

    async def test_get_media_buy_delivery_invalid_date_range(self, mcp_client):
        """Test get_media_buy_delivery returns an error for invalid date ranges.

        This exercises the date range validation branch where start_date >= end_date
        should return an AdCP-compliant error response with zeroed totals.
        """
        # Use a start_date that is after end_date to trigger the validation error
        params = {
            "start_date": "2025-01-31",
            "end_date": "2025-01-01",
        }

        result = await mcp_client.call_tool("get_media_buy_delivery", params)

        assert result is not None
        content = result.structured_content if hasattr(result, "structured_content") else result

        # Errors array should be present with the invalid_date_range code
        assert "errors" in content
        assert isinstance(content["errors"], list)
        assert len(content["errors"]) >= 1
        assert content["errors"][0]["code"] == "invalid_date_range"

    async def test_sync_creatives_minimal(self, mcp_client):
        """Test sync_creatives with minimal required parameters.

        Uses AdCP-compliant CreativeAsset schema which requires:
        - creative_id: Unique identifier
        - name: Human-readable name
        - format_id: FormatId object (not just a string)
        - assets: CreativeAssets object with the actual asset data
        """
        result = await mcp_client.call_tool(
            "sync_creatives",
            {
                "creatives": [
                    {
                        "creative_id": "test_creative_001",
                        "name": "Test Display Creative",
                        "format_id": {
                            "agent_url": "https://creatives.adcontextprotocol.org",
                            "id": "display_static",
                            "width": 300,
                            "height": 250,
                        },
                        "assets": {
                            "image": {
                                "url": "https://example.com/preview.jpg",
                                "width": 300,
                                "height": 250,
                            },
                            "click_url": {"url": "https://example.com"},
                        },
                    }
                ]
            },
        )

        assert result is not None
        content = result.structured_content if hasattr(result, "structured_content") else result
        assert "creatives" in content or "status" in content

    async def test_list_creatives_minimal(self, mcp_client):
        """Test list_creatives with no parameters (all optional)."""
        result = await mcp_client.call_tool("list_creatives", {})  # All parameters are optional

        assert result is not None
        content = result.structured_content if hasattr(result, "structured_content") else result
        assert "creatives" in content

    async def test_list_authorized_properties_minimal(self, mcp_client):
        """Test list_authorized_properties with no req parameter."""
        try:
            result = await mcp_client.call_tool("list_authorized_properties", {})  # req parameter is optional

            assert result is not None
            content = result.structured_content if hasattr(result, "structured_content") else result
            # May return error if no properties configured - that's expected
            # Just check we got some content back
            assert content is not None
        except Exception as e:
            # Expected error when no properties configured
            error_msg = str(e).lower()
            assert "no_properties_configured" in error_msg or "properties" in error_msg

    # update_performance_index used to exist as an MCP tool but the adcp
    # library no longer exposes it on the wire (the tool was removed in
    # an earlier spec revision). The impl still lives at
    # src/core/tools/performance.py:_update_performance_index_impl and is
    # covered by unit tests:
    #   tests/unit/test_performance_index_behavioral.py    (behavioural)
    #   tests/unit/test_auth_requirements.py               (auth boundary)
    #   tests/unit/test_impl_resolved_identity.py          (identity param)
    #   tests/unit/test_transport_agnostic_impl.py         (no console)
    # Removing the MCP roundtrip test here — the wire path no longer
    # exists, and unit-level coverage is sufficient until/unless the tool
    # comes back to the spec.


@pytest.mark.unit  # Changed from integration - these don't require server
class TestSchemaConstructionValidation:
    """Test that schemas are constructed correctly from tool parameters."""

    def test_update_media_buy_request_construction(self):
        """Test that UpdateMediaBuyRequest can be constructed with minimal params."""
        from src.core.schemas import UpdateMediaBuyRequest

        # Test with only media_buy_id (required via oneOf constraint)
        req = UpdateMediaBuyRequest(media_buy_id="test_buy_123")

        assert req.media_buy_id == "test_buy_123"
        assert req.paused is None  # adcp 2.12.0+: replaced 'active' with 'paused'
        assert req.today is None  # Should exist and be None, not raise AttributeError

        # Test that today field is accessible even though it's excluded from serialization
        assert hasattr(req, "today")
        assert "today" not in req.model_dump()  # Excluded from output

    def test_all_request_schemas_have_optional_or_default_fields(self):
        """Verify that all request schemas can be constructed without all fields."""
        from src.core import schemas

        # Test schemas that should work with minimal params
        test_cases = [
            (schemas.GetProductsRequest, {"brand": {"domain": "testbrand.com"}}),
            (schemas.UpdateMediaBuyRequest, {"media_buy_id": "test"}),
            (schemas.GetMediaBuyDeliveryRequest, {}),
            (schemas.ListCreativesRequest, {}),
            (schemas.ListAuthorizedPropertiesRequest, {}),
        ]

        for schema_class, minimal_params in test_cases:
            try:
                instance = schema_class(**minimal_params)
                assert instance is not None, f"{schema_class.__name__} failed to construct with minimal params"
            except Exception as e:
                pytest.fail(f"{schema_class.__name__} raised {type(e).__name__}: {e}")


@pytest.mark.unit  # Changed from integration - these don't require server
class TestParameterToSchemaMapping:
    """Test that tool parameters map correctly to schema fields."""

    def test_update_media_buy_parameter_mapping(self):
        """Test that update_media_buy parameters map to UpdateMediaBuyRequest fields."""
        from src.core.schemas import UpdateMediaBuyRequest

        # Simulate what the tool does when constructing the request
        # Note: Tool should convert float to Budget object before passing
        # Updated: Only use valid AdCP fields (start_time/end_time, not flight_start_date/flight_end_date)
        tool_params = {
            "media_buy_id": "test_buy_123",
            "paused": True,  # adcp 2.12.0+: replaced 'active' with 'paused'
        }

        # Create request with valid fields only
        req = UpdateMediaBuyRequest(**tool_params)

        # Valid fields should be set
        assert req.media_buy_id == "test_buy_123"
        assert req.paused is True  # adcp 2.12.0+: paused=True means pause

        # start_time/end_time should be None since not provided
        assert req.start_time is None
        assert req.end_time is None

        # budget field should be None since not provided
        assert req.budget is None
