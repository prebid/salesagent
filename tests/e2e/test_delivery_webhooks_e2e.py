"""End-to-end blueprint for delivery webhook flow.

This follows the reference E2E patterns and calls real MCP tools:

1. get_products
2. create_media_buy (with reporting_webhook)
3. get_media_buy_delivery for an explicit period
4. Wait for scheduled delivery_report webhook and inspect payload

All TODOs are left for you to fill in assertions and any spec-specific checks.
"""

import json
import socket
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
from time import sleep
from typing import Any
import pytest
from fastmcp.client import Client
from fastmcp.client.transports import StreamableHttpTransport

from tests.e2e.utils import wait_for_server_readiness
from tests.e2e.adcp_request_builder import (
    build_adcp_media_buy_request,
    get_test_date_range,
    parse_tool_result,
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
    s.bind(("0.0.0.0", 0))
    port = s.getsockname()[1]
    s.close()

    # Start server on all interfaces so it's reachable
    server = HTTPServer(("0.0.0.0", port), DeliveryWebhookReceiver)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()

    # The webhook URL passed to the container must use a hostname reachable 
    # from inside the Docker container. 
    # On macOS/Windows with Docker Desktop, 'host.docker.internal' works.
    # On Linux, it might vary (often 172.17.0.1), but for now we assume 
    # host.docker.internal or that the user configures extra_hosts.
    webhook_host = "host.docker.internal"
    
    # Fallback for non-Docker environments (running locally)
    # If running tests against localhost directly (no docker), localhost is fine.
    # But the e2e tests use docker_services_e2e, so we assume Docker.
    
    webhook_url = f"http://{webhook_host}:{port}/webhook"

    yield {
        "url": webhook_url,
        "server": server,
        "received": DeliveryWebhookReceiver.received_webhooks,
    }

    server.shutdown()
    DeliveryWebhookReceiver.received_webhooks.clear()


class TestDailyDeliveryWebhookFlow:
    """Blueprint E2E test for daily delivery webhooks."""

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
        # ------------------------------------------------------------------
        # MCP client setup (same pattern as reference implementation)
        # ------------------------------------------------------------------
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
            # ==============================================================
            # PHASE 1: Product discovery (get_products)
            # ==============================================================
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

            # ==============================================================
            # PHASE 2: Create media buy with reporting_webhook (create_media_buy)
            # ==============================================================
            start_time, end_time = get_test_date_range(days_from_now=1, duration_days=7)

            media_buy_request = build_adcp_media_buy_request(
                product_ids=[product_id],
                total_budget=2000.0,
                start_time=start_time,
                end_time=end_time,
                brand_manifest={"name": "Daily Delivery Webhook Test"},
                webhook_url=delivery_webhook_server["url"],
                # Explicitly configure daily reporting frequency
                reporting_webhook_extra={
                    "frequency": "daily",
                },
                context={"e2e": "delivery_webhook_create_media_buy"},
            )

            create_result = await client.call_tool("create_media_buy", media_buy_request)
            create_data = parse_tool_result(create_result)

            assert "media_buy_id" in create_data
            assert create_data.get("status") in ["pending", "approved", "active"]
            
            # Verify context echo
            assert create_data.get("context", {}).get("e2e") == "delivery_webhook_create_media_buy"

            media_buy_id = create_data.get("media_buy_id")
            buyer_ref = create_data.get("buyer_ref")

            assert media_buy_id or buyer_ref  # Blueprint sanity check

            # ==============================================================
            # PHASE 3: Explicit delivery request (get_media_buy_delivery)
            # ==============================================================
            # Use media_buy_id if available; otherwise you might use buyer_ref-based lookup
            if media_buy_id:
                delivery_period = {
                    "start_date": start_time.split("T")[0],
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

                # Delivery might be empty if campaign hasn't started, but structure should valid
                assert "media_buy_deliveries" in delivery_data or "deliveries" in delivery_data
                assert delivery_data.get("context", {}).get("e2e") == "delivery_webhook_get_media_buy_delivery"

            # ==============================================================
            # PHASE 4: Scheduled delivery webhook (delivery_report)
            # ==============================================================
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
                
                # Verify webhook payload structure
                # The scheduler uses task_type="media_buy_delivery"
                assert webhook_payload.get("task_type") == "media_buy_delivery"
                assert webhook_payload.get("status") == "completed"
                
                result = webhook_payload.get("result") or {}
                assert result.get("media_buy_id") == media_buy_id
                
                # Verify scheduling metadata
                assert "next_expected_at" in result
                assert result.get("frequency") == "daily"
                assert "sequence_number" in result

