"""
AdCP Full Lifecycle E2E Test (Minimal)

Exercises the core 4-phase lifecycle:
1. get_products - Product discovery
2. create_media_buy - Campaign creation (with force-approval)
3. sync_creatives - Creative sync
4. get_media_buy_delivery - Delivery metrics

Modeled after test_adcp_reference_implementation.py but kept minimal.
Does NOT test webhooks or budget updates.
"""

import uuid

import pytest
from fastmcp.client import Client
from fastmcp.client.transports import StreamableHttpTransport

from tests.e2e.adcp_request_builder import (
    build_adcp_media_buy_request,
    build_creative,
    build_sync_creatives_request,
    get_test_date_range,
    parse_tool_result,
)
from tests.e2e.utils import force_approve_media_buy_in_db


class TestAdCPFullLifecycle:
    """Minimal E2E test for the core AdCP 4-phase lifecycle."""

    @pytest.mark.asyncio
    async def test_four_phase_lifecycle(self, docker_services_e2e, live_server, test_auth_token):
        """
        Minimal lifecycle: get_products -> create_media_buy -> sync_creatives -> get_media_buy_delivery.

        This test uses its own Client (not e2e_client) to avoid X-Dry-Run:true.
        """
        # Setup MCP client without dry-run
        headers = {
            "x-adcp-auth": test_auth_token,
            "x-adcp-tenant": "ci-test",
        }
        transport = StreamableHttpTransport(url=f"{live_server['mcp']}/mcp/", headers=headers)

        async with Client(transport=transport) as client:
            # ============================================================
            # PHASE 1: Product Discovery
            # ============================================================
            products_result = await client.call_tool(
                "get_products",
                {
                    "brand_manifest": {"name": "Test Brand"},
                    "brief": "display advertising",
                    "context": {"e2e": "full_lifecycle"},
                },
            )
            products_data = parse_tool_result(products_result)

            assert "products" in products_data, "Response must contain products"
            assert len(products_data["products"]) > 0, "Must have at least one product"

            product = products_data["products"][0]
            product_id = product["product_id"]

            # Extract pricing_option_id from the actual product response
            pricing_options = product.get("pricing_options", [])
            assert len(pricing_options) > 0, f"Product {product_id} must have at least one pricing option"
            pricing_option_id = pricing_options[0]["pricing_option_id"]

            # Extract a valid format_id from the product
            format_ids = product.get("format_ids", [])
            assert len(format_ids) > 0, f"Product {product_id} must have at least one format_id"
            format_id = format_ids[0]

            # ============================================================
            # PHASE 2: Create Media Buy
            # ============================================================
            start_time, end_time = get_test_date_range(days_from_now=1, duration_days=30)

            media_buy_request = build_adcp_media_buy_request(
                product_ids=[product_id],
                total_budget=5000.0,
                start_time=start_time,
                end_time=end_time,
                brand_manifest={"name": "Lifecycle Test Brand"},
                pricing_option_id=pricing_option_id,
                context={"e2e": "full_lifecycle_create"},
            )

            media_buy_result = await client.call_tool("create_media_buy", media_buy_request)
            media_buy_data = parse_tool_result(media_buy_result)

            media_buy_id = media_buy_data.get("media_buy_id")
            assert media_buy_id, f"create_media_buy must return media_buy_id, got: {list(media_buy_data.keys())}"

            # Force-approve the media buy so delivery works
            force_approve_media_buy_in_db(live_server, media_buy_id)

            # ============================================================
            # PHASE 3: Sync Creatives
            # ============================================================
            creative_id = f"creative_{uuid.uuid4().hex[:8]}"

            creative = build_creative(
                creative_id=creative_id,
                format_id=format_id,
                name="Lifecycle Test Creative",
                asset_url="https://example.com/test-creative.jpg",
                click_through_url="https://example.com/landing",
            )

            sync_request = build_sync_creatives_request(creatives=[creative])

            sync_result = await client.call_tool("sync_creatives", sync_request)
            sync_data = parse_tool_result(sync_result)

            assert "creatives" in sync_data, "Response must contain creatives"
            assert len(sync_data["creatives"]) == 1, "Should sync exactly 1 creative"

            # ============================================================
            # PHASE 4: Get Delivery Metrics
            # ============================================================
            delivery_result = await client.call_tool(
                "get_media_buy_delivery",
                {"media_buy_ids": [media_buy_id]},
            )
            delivery_data = parse_tool_result(delivery_result)

            assert "deliveries" in delivery_data or "media_buy_deliveries" in delivery_data, (
                f"Response must contain deliveries, got: {list(delivery_data.keys())}"
            )

            # Delivery may be empty for freshly created media buys (mock adapter
            # delivery simulator needs a cycle to generate metrics). Verify structure only.
            deliveries = delivery_data.get("deliveries") or delivery_data.get("media_buy_deliveries", [])
            assert isinstance(deliveries, list), "deliveries must be a list"
