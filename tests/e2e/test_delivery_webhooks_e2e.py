"""End-to-end blueprint for delivery webhook flow.

This follows the reference E2E patterns and calls real MCP tools:

1. get_products
2. create_media_buy (with reporting_webhook and inline creatives)
3. get_media_buy_delivery for an explicit period
4. Wait for scheduled delivery_report webhook and inspect payload

All TODOs are left for you to fill in assertions and any spec-specific checks.
"""

import uuid
from time import sleep
from typing import Any

import pytest

from tests.e2e._webhook_capture import WebhookCaptureHandler, run_webhook_capture_server
from tests.e2e.adcp_request_builder import (
    build_adcp_media_buy_request,
    build_creative,
    get_test_date_range,
    parse_tool_result,
)
from tests.e2e.utils import (
    force_approve_media_buy_in_db,
    force_complete_media_buy_in_db,
    make_mcp_client,
    set_live_adapter_behavior,
    wait_for_server_readiness,
)


class DeliveryWebhookReceiver(WebhookCaptureHandler):
    """Simple webhook receiver to capture delivery_report notifications."""

    received_webhooks: list[Any] = []


@pytest.fixture
def delivery_webhook_server():
    """Start a local HTTP server to receive delivery_report webhooks."""
    with run_webhook_capture_server(DeliveryWebhookReceiver, DeliveryWebhookReceiver.received_webhooks) as info:
        yield info


class TestDailyDeliveryWebhookFlow:
    """Blueprint E2E test for daily delivery webhooks."""

    async def discover_product(self, client):
        """Phase 1: Product discovery (get_products)."""
        products_result = await client.call_tool(
            "get_products",
            {
                "brand": {"domain": "testbrand.com"},
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
        format_ids = product["format_ids"]

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

    async def create_media_buy(self, client, product_id, pricing_option_id, delivery_webhook_server):
        """Phase 3: Create media buy with reporting_webhook."""
        _, end_time = get_test_date_range(days_from_now=0, duration_days=7)
        start_time = "asap"

        media_buy_request = build_adcp_media_buy_request(
            product_ids=[product_id],
            total_budget=2000.0,
            start_time=start_time,
            end_time=end_time,
            brand={"domain": "testbrand.com"},
            webhook_url=delivery_webhook_server["url"],
            reporting_frequency="daily",
            context={"e2e": "delivery_webhook_create_media_buy"},
            pricing_option_id=pricing_option_id,
        )

        create_result = await client.call_tool("create_media_buy", media_buy_request)
        create_data = parse_tool_result(create_result)

        assert "media_buy_id" in create_data

        # Verify context echo
        assert create_data.get("context", {}).get("e2e") == "delivery_webhook_create_media_buy"

        media_buy_id = create_data.get("media_buy_id")

        assert media_buy_id  # Blueprint sanity check

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
        set_live_adapter_behavior(live_server, manual_approval_required=False)

        # Wait for server readiness
        wait_for_server_readiness(live_server["mcp"])

        async with make_mcp_client(live_server, token=test_auth_token) as client:
            # 1. Discover Product
            product_id, pricing_option_id, format_ids = await self.discover_product(client)

            # 2. Create Media Buy
            # Use approved creatives from init_database_ci.py
            media_buy_id, start_time, end_time = await self.create_media_buy(
                client, product_id, pricing_option_id, delivery_webhook_server
            )

            # 3. Force Approve Media Buy
            self.force_approve_media_buy(live_server, media_buy_id)

            # 4. Explicit Delivery Check
            start_date_str = start_time
            if start_time == "asap":
                from datetime import UTC, datetime

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

            assert received, (
                "Expected at least one delivery report webhook. Check connectivity and DELIVERY_WEBHOOK_INTERVAL."
            )

            if received:
                webhook_payload = received[0]

                # Verify webhook payload structure (MCP webhook format)
                assert webhook_payload.get("status") == "completed", (
                    f"Expected status 'completed', got {webhook_payload.get('status')}"
                )
                assert webhook_payload.get("task_id") == media_buy_id, (
                    f"Expected task_id '{media_buy_id}', got {webhook_payload.get('task_id')}"
                )
                assert "timestamp" in webhook_payload, "Missing timestamp in webhook payload"

                result = webhook_payload.get("result") or {}

                # Verify delivery data
                media_buy_deliveries = result.get("media_buy_deliveries")
                assert media_buy_deliveries is not None, "Missing media_buy_deliveries in result"
                assert len(media_buy_deliveries) > 0, "Expected at least one media_buy_delivery"
                assert media_buy_deliveries[0]["media_buy_id"] == media_buy_id

                # Verify scheduling metadata
                assert result.get("notification_type") == "scheduled", (
                    f"Expected notification_type 'scheduled', got {result.get('notification_type')}"
                )
                assert "next_expected_at" in result, "Missing next_expected_at in result"
                # partial_data is on the wire (hardcoded False today) — pin it so a future
                # partial-data change can't silently put a spec-divergent shape on the webhook.
                assert result.get("partial_data") is False, (
                    f"Expected partial_data False on the scheduled webhook, got {result.get('partial_data')!r}"
                )

            # 6. Final webhook on completion — grade the `final` derivation on the REAL wire.
            # Drive the buy to terminal `completed` (what the status scheduler writes when a
            # flight ends). The next scheduler batch resolves it to canonical `completed` and
            # sends the FINAL notification. The prior `scheduled` send does NOT dedup this:
            # the final gate keys only on a prior SUCCESSFUL `final` (has_successful_final),
            # of which there is none. AdCP 3.1.1 requires `next_expected_at` be OMITTED (not
            # null) for `final`, so this asserts real-wire omission — the headline behavior.
            force_complete_media_buy_in_db(live_server, media_buy_id)

            def _final_webhook():
                for wh in received:
                    if (wh.get("result") or {}).get("notification_type") == "final":
                        return wh
                return None

            elapsed = 0
            while elapsed < timeout_seconds and _final_webhook() is None:
                sleep(poll_interval)
                elapsed += poll_interval

            final_webhook = _final_webhook()
            assert final_webhook is not None, (
                "Expected a FINAL delivery webhook after the buy completed. "
                f"Captured notification_types: {[(w.get('result') or {}).get('notification_type') for w in received]}"
            )
            assert final_webhook.get("task_id") == media_buy_id
            final_result = final_webhook.get("result") or {}
            assert "next_expected_at" not in final_result, (
                "AdCP 3.1.1: next_expected_at must be OMITTED for a final notification; "
                f"got {final_result.get('next_expected_at')!r} on the real webhook wire"
            )
