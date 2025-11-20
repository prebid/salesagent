"""End-to-end blueprint for delivery webhook flow.

This follows the reference E2E patterns and calls real MCP tools:

1. get_products
2. create_media_buy (with reporting_webhook and inline creatives)
3. get_media_buy_delivery for an explicit period
4. Wait for scheduled delivery_report webhook and inspect payload

All TODOs are left for you to fill in assertions and any spec-specific checks.
"""

import json
import socket
import uuid
import psycopg2
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
from time import sleep
from typing import Any
import pytest
from fastmcp.client import Client
from fastmcp.client.transports import StreamableHttpTransport

from tests.e2e.utils import wait_for_server_readiness, force_approve_media_buy_in_db
from tests.e2e.adcp_request_builder import (
    build_adcp_media_buy_request,
    get_test_date_range,
    parse_tool_result,
    build_creative,
)


class DeliveryWebhookReceiver(BaseHTTPRequestHandler):
    """Simple webhook receiver to capture delivery_report notifications."""

    received_webhooks: list[Any] = []

    def do_POST(self):
        """Handle POST requests (webhook notifications)."""
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)

        try:
            payload = json.loads(body.decode("utf-8"))
            self.received_webhooks.append(payload)
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
        """Silence HTTP server logs during tests."""
        pass


@pytest.fixture
def delivery_webhook_server():
    """Start a local HTTP server to receive delivery_report webhooks."""
    
    # Find an available port
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()

    # Start server on all interfaces so it's reachable
    server = HTTPServer(("127.0.0.1", port), DeliveryWebhookReceiver)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()

    webhook_url = f"http://localhost:{port}/webhook"

    yield {
        "url": webhook_url,
        "server": server,
        "received": DeliveryWebhookReceiver.received_webhooks,
    }

    server.shutdown()
    DeliveryWebhookReceiver.received_webhooks.clear()


class TestDailyDeliveryWebhookFlow:
    """Blueprint E2E test for daily delivery webhooks."""

    def setup_adapter_config(self, live_server):
        """Configure adapter for auto-approval (needs active media buy for delivery scheduler)."""
        try:
            conn = psycopg2.connect(live_server["postgres"])
            cursor = conn.cursor()
            
            # Ensure ci-test tenant has mock manual approval disabled
            cursor.execute("SELECT tenant_id FROM tenants WHERE subdomain = 'ci-test'")
            tenant_row = cursor.fetchone()
            if tenant_row:
                tenant_id = tenant_row[0]
                cursor.execute("""
                    INSERT INTO adapter_config (tenant_id, adapter_type, mock_manual_approval_required)
                    VALUES (%s, 'mock', false)
                    ON CONFLICT (tenant_id) 
                    DO UPDATE SET mock_manual_approval_required = false, adapter_type = 'mock'
                """, (tenant_id,))
                conn.commit()
                print(f"Updated adapter config for tenant {tenant_id}: manual_approval=False")
            else:
                print("Warning: ci-test tenant not found for adapter config update")
            
            cursor.close()
            conn.close()
        except Exception as e:
            print(f"Failed to update adapter config: {e}")

    async def discover_product(self, client):
        """Phase 1: Product discovery (get_products)."""
        products_result = await client.call_tool(
            "get_products",
            {
                "brand_manifest": {"name": "Daily Delivery Webhook Test"},
                "brief": "display advertising",
                "context": {"e2e": "delivery_webhook_get_products"},
            },
        )
        products_data = parse_tool_result(products_result)

        assert "products" in products_data
        assert isinstance(products_data["products"], list)
        assert len(products_data["products"]) > 0

        # Verify context echo
        assert products_data.get("context", {}).get("e2e") == "delivery_webhook_get_products"

        # Pick first product
        product = products_data["products"][0]
        product_id = product["product_id"]
        pricing_option_id = product["pricing_options"][0]["pricing_option_id"]
        
        # Pick formats_ids
        format_ids = product['format_ids']

        return product_id, pricing_option_id, format_ids 

    async def build_inline_creative(self, format_id: dict[str, Any]) -> dict[str, Any]:
        """Phase 2: Build inline creative for testing (no external sync)."""
        creative = build_creative(
            creative_id="cr_" + uuid.uuid4().hex[:8],
            format_id=format_id,
            name="Delivery Test Creative",
            asset_url="https://via.placeholder.com/300x250.png",
        )
        return creative

    async def create_media_buy(
        self, 
        client, 
        product_id, 
        pricing_option_id, 
        delivery_webhook_server
    ):
        """Phase 3: Create media buy with reporting_webhook."""
        _, end_time = get_test_date_range(days_from_now=0, duration_days=7)
        start_time = "asap"

        media_buy_request = build_adcp_media_buy_request(
            product_ids=[product_id],
            total_budget=2000.0,
            start_time=start_time,
            end_time=end_time,
            brand_manifest={"name": "Daily Delivery Webhook Test"},
            webhook_url=delivery_webhook_server["url"],
            reporting_frequency="daily",
            context={"e2e": "delivery_webhook_create_media_buy"},
            pricing_option_id=pricing_option_id
        )

        create_result = await client.call_tool("create_media_buy", media_buy_request)
        create_data = parse_tool_result(create_result)

        assert "media_buy_id" in create_data
        
        # Verify context echo
        assert create_data.get("context", {}).get("e2e") == "delivery_webhook_create_media_buy"

        media_buy_id = create_data.get("media_buy_id")
        buyer_ref = create_data.get("buyer_ref")

        assert media_buy_id or buyer_ref  # Blueprint sanity check
        
        return media_buy_id, start_time, end_time

    def force_approve_media_buy(self, live_server, media_buy_id):
        """Force approve media buy in database to bypass approval workflow."""
        force_approve_media_buy_in_db(live_server, media_buy_id)

    @pytest.mark.asyncio
    async def test_daily_delivery_webhook_end_to_end(
        self,
        docker_services_e2e,
        live_server,
        test_auth_token,
        delivery_webhook_server,
    ):
        """
        End-to-end blueprint:

        1. Discover a product (get_products)
        2. Create media buy with reporting_webhook.frequency = "daily"
        3. Get delivery metrics explicitly via get_media_buy_delivery
        4. Wait for scheduled delivery_report webhook and inspect payload
        """
        self.setup_adapter_config(live_server)

        headers = {
            "x-adcp-auth": test_auth_token,
            "x-adcp-tenant": "ci-test",  # Explicit tenant selection for E2E
        }
        print("live_server")
        print(live_server)
        transport = StreamableHttpTransport(url=f"{live_server['mcp']}/mcp/", headers=headers)

        # Wait for server readiness
        wait_for_server_readiness(live_server['mcp'])

        async with Client(transport=transport) as client:
            # 1. Discover Product
            product_id, pricing_option_id, format_ids = await self.discover_product(client)

            # 2. Create Media Buy
            # Use approved creatives from init_database_ci.py
            media_buy_id, start_time, end_time = await self.create_media_buy(
                client, 
                product_id, 
                pricing_option_id, 
                delivery_webhook_server
            )

            # 3. Force Approve Media Buy
            self.force_approve_media_buy(live_server, media_buy_id)

            # 4. Explicit Delivery Check
            start_date_str = start_time
            if start_time == "asap":
                from datetime import datetime, UTC
                start_date_str = datetime.now(UTC).date().isoformat()
            else:
                start_date_str = start_time.split("T")[0]
            
            delivery_period = {
                "start_date": start_date_str,
                "end_date": end_time.split("T")[0],
            }

            delivery_result = await client.call_tool(
                "get_media_buy_delivery",
                {
                    "media_buy_ids": [media_buy_id],
                    **delivery_period,
                    "context": {"e2e": "delivery_webhook_get_media_buy_delivery"},
                },
            )

            delivery_data = parse_tool_result(delivery_result)

            assert "media_buy_deliveries" in delivery_data
            assert len(delivery_data["media_buy_deliveries"]) > 0
            assert delivery_data["media_buy_deliveries"][0]["totals"]["impressions"] > 0
            assert delivery_data.get("context", {}).get("e2e") == "delivery_webhook_get_media_buy_delivery"


            # 5. Wait for Webhook
            # The scheduler runs inside the container. 
            # We configured DELIVERY_WEBHOOK_INTERVAL=5 in conftest.py for E2E tests.
            # It should trigger in 5 seconds.

            received = delivery_webhook_server["received"]

            # Wait for webhook
            timeout_seconds = 30
            poll_interval = 1

            elapsed = 0
            while elapsed < timeout_seconds and not received:
                sleep(poll_interval)
                elapsed += poll_interval

            assert received, "Expected at least one delivery report webhook. Check connectivity and DELIVERY_WEBHOOK_INTERVAL."

            if received:
                webhook_payload = received[0]
                
                # # Verify webhook payload structure
                # # The scheduler uses task_type="media_buy_delivery"
                assert webhook_payload.get("task_type") == "media_buy_delivery"
                assert webhook_payload.get("status") == "completed"
                
                result = webhook_payload.get("result") or {}

                media_buy_deliveries = result.get("media_buy_deliveries")
                assert len(media_buy_deliveries) > 0
                assert media_buy_deliveries[0]['media_buy_id'] == media_buy_id
                
                # # Verify scheduling metadata
                assert "next_expected_at" in result
                assert result.get("frequency") == "daily"
                assert "sequence_number" in result

