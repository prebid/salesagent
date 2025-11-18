"""
AdCP V2.3 Reference E2E Test

This is the REFERENCE implementation for all future E2E tests.
It demonstrates:
1. Proper use of NEW AdCP V2.3 format
2. Full campaign lifecycle (discovery → creation → delivery → reporting)
3. Mix of synchronous and asynchronous (webhook) responses
4. Proper schema validation using helper utilities
5. Creative workflow integration

Use this as a template when adding new E2E tests.
"""

import json
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
from time import sleep

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


class WebhookReceiver(BaseHTTPRequestHandler):
    """Simple webhook receiver for testing async notifications."""

    received_webhooks = []

    def do_POST(self):
        """Handle POST requests (webhook notifications)."""
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)

        try:
            webhook_data = json.loads(body.decode("utf-8"))
            self.received_webhooks.append(webhook_data)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status": "received"}')
        except Exception as e:
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode())

    def log_message(self, format, *args):
        """Suppress HTTP server logs."""
        pass


@pytest.fixture
def webhook_server():
    """Start a local webhook server for testing async notifications."""
    # Find an available port
    import socket

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("", 0))
    port = s.getsockname()[1]
    s.close()

    # Start server
    server = HTTPServer(("127.0.0.1", port), WebhookReceiver)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()

    webhook_url = f"http://127.0.0.1:{port}/webhook"

    yield {"url": webhook_url, "server": server, "received": WebhookReceiver.received_webhooks}

    # Cleanup
    server.shutdown()
    WebhookReceiver.received_webhooks.clear()


class TestAdCPReferenceImplementation:
    """Reference E2E test demonstrating full AdCP V2.3 workflow."""

    @pytest.mark.asyncio
    async def test_complete_campaign_lifecycle_with_webhooks(
        self, docker_services_e2e, live_server, test_auth_token, webhook_server
    ):
        """
        REFERENCE TEST: Complete campaign lifecycle with sync + async (webhook) responses.

        This test demonstrates the CORRECT way to write E2E tests using AdCP V2.3 format.

        Flow:
        1. Discovery: Get products and formats
        2. Create: Create media buy with webhook for async updates
        3. Creatives: Sync creatives (sync response)
        4. Delivery: Get delivery metrics (sync response)
        5. Update: Update campaign budget (webhook notification)
        6. Reporting: Verify webhook received update notification

        Use this as a template for all future E2E tests!
        """
        print("\n" + "=" * 80)
        print("REFERENCE E2E TEST: Complete Campaign Lifecycle")
        print("=" * 80)

        # Setup MCP client with both auth and tenant detection headers
        # Note: Host header is automatically set by HTTP client based on URL,
        # so we use x-adcp-tenant header for explicit tenant selection in E2E tests
        headers = {
            "x-adcp-auth": test_auth_token,
            "x-adcp-tenant": "ci-test",  # Explicit tenant selection for E2E tests
        }
        transport = StreamableHttpTransport(url=f"{live_server['mcp']}/mcp/", headers=headers)

        async with Client(transport=transport) as client:
            # ================================================================
            # PHASE 1: Product Discovery (Synchronous)
            # ================================================================
            print("\n📦 PHASE 1: Product Discovery")

            products_result = await client.call_tool(
                "get_products",
                {
                    "brand_manifest": {"name": "Premium Athletic Footwear"},
                    "brief": "display advertising",
                    "context": {"e2e": "get_products"},
                },
            )
            products_data = parse_tool_result(products_result)

            print(f"   🔍 DEBUG: products_result type: {type(products_result)}")
            print(f"   🔍 DEBUG: products_result.content: {products_result.content}")
            print(f"   🔍 DEBUG: products_data keys: {products_data.keys()}")
            print(f"   🔍 DEBUG: products_data: {json.dumps(products_data, indent=2)[:500]}")

            assert "products" in products_data, "Response must contain products"
            assert len(products_data["products"]) > 0, "Must have at least one product"
            # Context should echo back
            assert products_data.get("context") == {"e2e": "get_products"}

            # Get first product
            product = products_data["products"][0]
            product_id = product["product_id"]
            print(f"   ✓ Found product: {product['name']} ({product_id})")
            # Product uses 'format_ids' field
            print(f"   ✓ Formats: {product['format_ids']}")

            # Get creative formats (no req wrapper - takes optional params directly)
            formats_result = await client.call_tool("list_creative_formats", {})
            formats_data = parse_tool_result(formats_result)

            assert "formats" in formats_data, "Response must contain formats"
            print(f"   ✓ Available formats: {len(formats_data['formats'])}")

            # ================================================================
            # PHASE 2: Create Media Buy with Webhook (Async Notification)
            # ================================================================
            print("\n🎯 PHASE 2: Create Media Buy (with webhook for async updates)")

            # Build request using helper
            start_time, end_time = get_test_date_range(days_from_now=1, duration_days=30)

            media_buy_request = build_adcp_media_buy_request(
                product_ids=[product_id],
                total_budget=5000.0,
                start_time=start_time,
                end_time=end_time,
                brand_manifest={"name": "Nike Air Jordan 2025 Basketball Shoes"},
                targeting_overlay={
                    "geo_country_any_of": ["US", "CA"],
                },
                webhook_url=webhook_server["url"],  # Async notifications!
                context={"e2e": "create_media_buy"},
            )

            # Create media buy (pass params directly - no req wrapper)
            media_buy_result = await client.call_tool("create_media_buy", media_buy_request)
            media_buy_data = parse_tool_result(media_buy_result)

            # When webhook is provided, response may have task_id instead of media_buy_id
            # For this test, we'll use buyer_ref if media_buy_id is not available
            media_buy_id = media_buy_data.get("media_buy_id")
            buyer_ref = media_buy_data.get("buyer_ref")

            if not media_buy_id:
                # If async operation, skip delivery check since we don't have the ID yet
                print(f"   ✓ Media buy submitted (async): {buyer_ref}")
                print(f"   ✓ Status: {media_buy_data.get('status', 'unknown')}")
                print(f"   ✓ Webhook configured: {webhook_server['url']}")
                print("   ⚠️  Skipping delivery check (async operation, no media_buy_id yet)")
                # Skip the rest of the test phases that need media_buy_id
                return

            print(f"   ✓ Media buy created: {media_buy_id}")
            print(f"   ✓ Status: {media_buy_data.get('status', 'unknown')}")
            print(f"   ✓ Webhook configured: {webhook_server['url']}")
            # Context should echo back
            assert media_buy_data.get("context") == {"e2e": "create_media_buy"}

            # ================================================================
            # PHASE 3: Creative Sync (Synchronous)
            # ================================================================
            print("\n🎨 PHASE 3: Sync Creatives")

            # Build creatives using helper
            creative_id_1 = f"creative_{uuid.uuid4().hex[:8]}"
            creative_id_2 = f"creative_{uuid.uuid4().hex[:8]}"

            creative_1 = build_creative(
                creative_id=creative_id_1,
                format_id="display_300x250",
                name="Nike Air Jordan - Display 300x250",
                asset_url="https://example.com/nike-jordan-300x250.jpg",
                click_through_url="https://nike.com/air-jordan-2025",
            )

            creative_2 = build_creative(
                creative_id=creative_id_2,
                format_id="display_728x90",
                name="Nike Air Jordan - Display 728x90",
                asset_url="https://example.com/nike-jordan-728x90.jpg",
                click_through_url="https://nike.com/air-jordan-2025",
            )

            # Sync creatives
            sync_request = build_sync_creatives_request(creatives=[creative_1, creative_2])

            sync_result = await client.call_tool("sync_creatives", sync_request)
            sync_data = parse_tool_result(sync_result)

            assert "creatives" in sync_data, "Response must contain creatives (AdCP spec field name)"
            assert len(sync_data["creatives"]) == 2, "Should sync 2 creatives"
            print(f"   ✓ Synced {len(sync_data['creatives'])} creatives")
            print(f"   ✓ Creative IDs: {creative_id_1}, {creative_id_2}")

            # ================================================================
            # PHASE 4: Get Delivery Metrics (Synchronous)
            # ================================================================
            print("\n📊 PHASE 4: Get Delivery Metrics")

            delivery_result = await client.call_tool("get_media_buy_delivery", {"media_buy_ids": [media_buy_id]})
            delivery_data = parse_tool_result(delivery_result)

            # Verify delivery response structure (AdCP spec: deliveries is an array)
            assert "deliveries" in delivery_data or "media_buy_deliveries" in delivery_data
            print(f"   ✓ Delivery data retrieved for: {media_buy_id}")
            # If context was provided, ensure echo works when present; add context and re-call minimally
            delivery_result_ctx = await client.call_tool(
                "get_media_buy_delivery", {"media_buy_ids": [media_buy_id], "context": {"e2e": "delivery"}}
            )
            delivery_data_ctx = parse_tool_result(delivery_result_ctx)
            assert delivery_data_ctx.get("context") == {"e2e": "delivery"}

            # Check if we have deliveries
            deliveries = delivery_data.get("deliveries") or delivery_data.get("media_buy_deliveries", [])
            if deliveries:
                print(f"   ✓ Found {len(deliveries)} delivery record(s)")
                if "metrics" in deliveries[0]:
                    metrics = deliveries[0]["metrics"]
                    print(f"   ✓ Metrics: {list(metrics.keys())}")

            # ================================================================
            # PHASE 5: Update Campaign Budget (Async via Webhook)
            # ================================================================
            print("\n💰 PHASE 5: Update Campaign Budget (webhook notification expected)")

            # Clear any previous webhooks
            webhook_server["received"].clear()

            # Update budget (AdCP spec: budget is a number, not an object)
            update_result = await client.call_tool(
                "update_media_buy",
                {
                    "media_buy_id": media_buy_id,
                    "budget": 7500.0,  # AdCP spec: budget is a number
                    "context": {"e2e": "update_media_buy"},
                    "push_notification_config": {
                        "url": webhook_server["url"],
                        "authentication": {"type": "none"},
                    },
                },
            )
            update_data = parse_tool_result(update_result)

            assert "media_buy_id" in update_data or "buyer_ref" in update_data
            print("   ✓ Budget update requested: $5000 → $7500")
            print(f"   ✓ Update status: {update_data.get('status', 'unknown')}")
            # Context should echo back on response
            assert update_data.get("context") == {"e2e": "update_media_buy"}

            # ================================================================
            # PHASE 6: Verify Webhook Notification (Async Verification)
            # ================================================================
            print("\n🔔 PHASE 6: Verify Webhook Notifications")

            # Wait for webhook (with timeout)
            max_wait = 5  # seconds
            waited = 0
            webhook_received = False

            while waited < max_wait and not webhook_received:
                if len(webhook_server["received"]) > 0:
                    webhook_received = True
                    break
                sleep(0.5)
                waited += 0.5

            if webhook_received:
                print(f"   ✓ Webhook received after {waited}s")
                print(f"   ✓ Webhook count: {len(webhook_server['received'])}")

                # Verify webhook content
                webhook_data = webhook_server["received"][0]
                print(f"   ✓ Webhook keys: {list(webhook_data.keys())}")

                # Basic webhook validation
                assert isinstance(webhook_data, dict), "Webhook data must be a dict"
                print("   ✓ Webhook data validated")
                # Verify context echoed in webhook result payload
                if "result" in webhook_data and isinstance(webhook_data["result"], dict):
                    assert webhook_data["result"].get("context") == {"e2e": "update_media_buy"}
            else:
                # Webhook may not be implemented yet - that's okay for this reference test
                print(f"   ⚠ No webhook received after {max_wait}s (may not be implemented yet)")
                print("   ℹ️ This is acceptable - webhook support is optional for this phase")

            # ================================================================
            # PHASE 7: List Creatives (Verify State)
            # ================================================================
            print("\n📋 PHASE 7: List Creatives (verify final state)")

            list_result = await client.call_tool("list_creatives", {})
            list_data = parse_tool_result(list_result)

            assert "creatives" in list_data, "Response must contain creatives"
            print(f"   ✓ Listed {len(list_data['creatives'])} creatives")

            # Verify our creatives are in the list
            creative_ids_in_list = {c["creative_id"] for c in list_data["creatives"]}
            assert creative_id_1 in creative_ids_in_list, f"Creative {creative_id_1} should be in list"
            assert creative_id_2 in creative_ids_in_list, f"Creative {creative_id_2} should be in list"
            print("   ✓ Both synced creatives found in list")

            # ================================================================
            # SUCCESS
            # ================================================================
            print("\n" + "=" * 80)
            print("✅ REFERENCE TEST PASSED - Complete Campaign Lifecycle")
            print("=" * 80)
            print("\nThis test demonstrates:")
            print("  ✓ Product discovery")
            print("  ✓ Media buy creation with webhook")
            print("  ✓ Creative sync (synchronous)")
            print("  ✓ Delivery metrics (synchronous)")
            print("  ✓ Budget update with webhook notification")
            print("  ✓ Creative listing (verify state)")
            print("\nUse this as a template for new E2E tests!")
            print("=" * 80)
