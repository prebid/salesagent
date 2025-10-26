"""MCP Tool Roundtrip Tests with Minimal Parameters.

These tests verify that MCP tools work correctly when called with only required parameters,
catching issues like the datetime.combine() bug where optional fields defaulted to None
and caused errors.

Focus: Test parameter-to-schema mapping, not business logic.
"""

from datetime import date, datetime, timedelta

import pytest
from fastmcp.client import Client
from fastmcp.client.transports import StreamableHttpTransport


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
        result = await mcp_client.call_tool("get_products", {"brand_manifest": {"name": "sustainable products"}})

        assert result is not None
        # FastMCP call_tool returns structured_content
        content = result.structured_content if hasattr(result, "structured_content") else result
        assert "products" in content

    async def test_create_media_buy_minimal(self, mcp_client):
        """Test create_media_buy with minimal required parameters."""
        # Get a product first
        products_result = await mcp_client.call_tool(
            "get_products", {"brand_manifest": {"name": "test product"}, "brief": "test"}
        )

        products = (
            products_result.structured_content if hasattr(products_result, "structured_content") else products_result
        )
        if products and len(products.get("products", [])) > 0:
            product_id = products["products"][0]["product_id"]

            # Create media buy with minimal params
            result = await mcp_client.call_tool(
                "create_media_buy",
                {
                    "po_number": "TEST-001",
                    "product_ids": [product_id],
                    "total_budget": 1000.0,
                    "start_date": (date.today() + timedelta(days=1)).isoformat(),
                    "end_date": (date.today() + timedelta(days=30)).isoformat(),
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
            "get_products", {"brand_manifest": {"name": "test product"}, "brief": "test"}
        )

        products = (
            products_result.structured_content if hasattr(products_result, "structured_content") else products_result
        )
        if products and len(products.get("products", [])) > 0:
            product_id = products["products"][0]["product_id"]

            create_result = await mcp_client.call_tool(
                "create_media_buy",
                {
                    "po_number": "TEST-002",
                    "product_ids": [product_id],
                    "total_budget": 1000.0,
                    "start_date": (date.today() + timedelta(days=1)).isoformat(),
                    "end_date": (date.today() + timedelta(days=30)).isoformat(),
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
                        "active": False,  # This triggers datetime.combine at line 2711
                    },
                )

                assert update_result is not None
                update_content = (
                    update_result.structured_content if hasattr(update_result, "structured_content") else update_result
                )
                assert "status" in update_content
                # Should not get TypeError: combine() argument 1 must be datetime.date, not None

    async def test_get_media_buy_delivery_minimal(self, mcp_client):
        """Test get_media_buy_delivery with minimal parameters."""
        result = await mcp_client.call_tool("get_media_buy_delivery", {})  # All parameters are optional

        assert result is not None
        content = result.structured_content if hasattr(result, "structured_content") else result
        assert "deliveries" in content or "aggregated_totals" in content

    async def test_sync_creatives_minimal(self, mcp_client):
        """Test sync_creatives with minimal required parameters."""
        result = await mcp_client.call_tool(
            "sync_creatives",
            {
                "creatives": [
                    {
                        "creative_id": "test_creative_001",
                        "format_id": "display_300x250",
                        "preview_url": "https://example.com/preview.jpg",
                        "click_url": "https://example.com",
                        "status": "active",
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
        result = await mcp_client.call_tool("list_authorized_properties", {})  # req parameter is optional

        assert result is not None
        content = result.structured_content if hasattr(result, "structured_content") else result
        assert "properties" in content

    async def test_update_performance_index_minimal(self, mcp_client):
        """Test update_performance_index with required parameters."""
        result = await mcp_client.call_tool(
            "update_performance_index",
            {
                "media_buy_id": "test_buy_001",
                "performance_data": [{"metric": "ctr", "value": 0.05, "timestamp": datetime.now().isoformat()}],
            },
        )

        assert result is not None
        # May return error if media_buy doesn't exist, but should not crash
        # Just check we got some content back
        content = result.structured_content if hasattr(result, "structured_content") else result
        assert content is not None


@pytest.mark.unit  # Changed from integration - these don't require server
class TestSchemaConstructionValidation:
    """Test that schemas are constructed correctly from tool parameters."""

    def test_update_media_buy_request_construction(self):
        """Test that UpdateMediaBuyRequest can be constructed with minimal params."""
        from src.core.schemas import UpdateMediaBuyRequest

        # Test with only media_buy_id (required via oneOf constraint)
        req = UpdateMediaBuyRequest(media_buy_id="test_buy_123")

        assert req.media_buy_id == "test_buy_123"
        assert req.active is None
        assert req.today is None  # Should exist and be None, not raise AttributeError

        # Test that today field is accessible even though it's excluded from serialization
        assert hasattr(req, "today")
        assert "today" not in req.model_dump()  # Excluded from output

    def test_create_media_buy_request_with_deprecated_fields(self):
        """Test that deprecated fields don't break schema construction."""
        from src.core.schemas import CreateMediaBuyRequest

        # These deprecated fields should be handled by model_validator
        req = CreateMediaBuyRequest(
            buyer_ref="test_ref",
            brand_manifest={"name": "Nike Air Jordan 2025 basketball shoes"},
            po_number="TEST-003",
            product_ids=["prod_1"],
            start_date=date.today(),
            end_date=date.today() + timedelta(days=30),
            total_budget=5000.0,
        )

        assert req.po_number == "TEST-003"
        # start_date should be converted to start_time
        assert req.start_time is not None
        assert req.end_time is not None

    def test_all_request_schemas_have_optional_or_default_fields(self):
        """Verify that all request schemas can be constructed without all fields."""
        from src.core import schemas

        # Test schemas that should work with minimal params
        test_cases = [
            (schemas.GetProductsRequest, {"brand_manifest": {"name": "test"}}),
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
            "active": False,
        }

        # Create request with valid fields only
        req = UpdateMediaBuyRequest(**tool_params)

        # Valid fields should be set
        assert req.media_buy_id == "test_buy_123"
        assert req.active is False

        # start_time/end_time should be None since not provided
        assert req.start_time is None
        assert req.end_time is None

        # budget field should be None since not provided
        assert req.budget is None

    def test_create_media_buy_legacy_field_conversion(self):
        """Test that legacy fields are converted to new fields."""
        from src.core.schemas import CreateMediaBuyRequest

        req = CreateMediaBuyRequest(
            buyer_ref="test_ref",
            brand_manifest={"name": "Adidas UltraBoost 2025 running shoes"},
            po_number="TEST-004",
            product_ids=["prod_1", "prod_2"],
            start_date="2025-02-01",
            end_date="2025-02-28",
            total_budget=10000.0,
        )

        # Legacy fields should be converted
        assert req.packages is not None
        assert len(req.packages) == 2
        assert req.start_time is not None
        assert req.end_time is not None
        assert req.budget is not None
        assert req.budget.total == 10000.0
