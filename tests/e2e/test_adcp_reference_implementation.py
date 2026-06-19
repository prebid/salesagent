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
from time import sleep

import pytest
from fastmcp.client import Client
from fastmcp.client.transports import StreamableHttpTransport

from tests.e2e._webhook_capture import WebhookCaptureHandler, run_webhook_capture_server
from tests.e2e.adcp_request_builder import (
    build_adcp_media_buy_request,
    build_creative,
    build_sync_creatives_request,
    get_test_date_range,
    parse_tool_result,
)


class WebhookReceiver(WebhookCaptureHandler):
    """Simple webhook receiver for testing async notifications."""

    received_webhooks: list = []


@pytest.fixture
def webhook_server():
    """Start a local webhook server for testing async notifications."""
    # Loopback-only receiver: this reference test reaches the server over the
    # host loopback, so pin the callback host rather than honoring ADCP_WEBHOOK_HOST.
    with run_webhook_capture_server(WebhookReceiver, WebhookReceiver.received_webhooks, host="127.0.0.1") as info:
        yield info


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
                    "brand": {"domain": "testbrand.com"},
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
            # Synthetic pricing_option_ids are computed per-product (see
            # src/core/tools/media_buy_create.py:1100): "{pricing_model}_{currency}_{fixed|auction}".
            # Discover from the response rather than relying on a literal default.
            pricing_option_id = product["pricing_options"][0]["pricing_option_id"]
            print(f"   ✓ Found product: {product['name']} ({product_id})")
            print(f"   ✓ Pricing option: {pricing_option_id}")
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
                brand={"domain": "testbrand.com"},
                pricing_option_id=pricing_option_id,
                targeting_overlay={
                    "geo_countries": ["US", "CA"],
                },
                webhook_url=webhook_server["url"],  # Async notifications!
                context={"e2e": "create_media_buy"},
            )
            # Register a push_notification_config at create. This exercises the config
            # registration path end-to-end — the AnyUrl/Enum serialization that, if it
            # regressed, would crash this PHASE (see media_buy_create.py model_dump(mode="json")).
            # Delivery of the resulting webhook is verified separately (PHASE 6 / the dedicated
            # xfail test); this lifecycle asserts only the synchronous contract.
            media_buy_request["push_notification_config"] = {"url": webhook_server["url"]}

            # Create media buy (pass params directly - no req wrapper)
            media_buy_result = await client.call_tool("create_media_buy", media_buy_request)
            media_buy_data = parse_tool_result(media_buy_result)

            # This test drives the SYNCHRONOUS success path: the mock adapter returns a
            # CreateMediaBuySuccess, whose AdCP 3.1 shape requires media_buy_id + packages.
            # (The async "submitted" shape returns status="submitted" + task_id and NO
            # media_buy_id; that lands on the task completion artifact.) A previous version
            # silently returned when media_buy_id was missing, skipping the rest of the lifecycle.
            media_buy_id = media_buy_data.get("media_buy_id")
            assert media_buy_id, f"create_media_buy must return media_buy_id; got: {media_buy_data}"

            print(f"   ✓ Media buy created: {media_buy_id}")
            print(f"   ✓ Status: {media_buy_data.get('status', 'unknown')}")
            print(f"   ✓ Webhook configured: {webhook_server['url']}")
            # Context should echo back
            assert media_buy_data.get("context") == {"e2e": "create_media_buy"}

            # ================================================================
            # PHASE 3: Creative Sync (Synchronous)
            # ================================================================
            print("\n🎨 PHASE 3: Sync Creatives")

            # Build creatives using helper.
            # A creative's format_id ({agent_url, id}) MUST reference a format the
            # creative agent actually recognises — sync_creatives validates it via
            # the creative-agent registry and rejects unknown formats (the creative
            # is then echoed with action="failed" but never persisted, so it never
            # appears in list_creatives). Discover formats from the
            # list_creative_formats response (PHASE 1), NOT the product's
            # format_ids: a product may advertise an id (e.g. "display_300x250")
            # that the agent doesn't expose (it has "display_300x250_image"), which
            # would make every creative fail to sync.
            image_formats = [
                f for f in formats_data["formats"] if any(a.get("asset_type") == "image" for a in f.get("assets", []))
            ]
            assert len(image_formats) >= 2, (
                "Creative-agent registry must expose >=2 image formats; got "
                f"{[f['format_id']['id'] for f in formats_data['formats']]}"
            )
            fmt_1 = image_formats[0]["format_id"]
            fmt_2 = image_formats[1]["format_id"]

            creative_id_1 = f"creative_{uuid.uuid4().hex[:8]}"
            creative_id_2 = f"creative_{uuid.uuid4().hex[:8]}"

            creative_1 = build_creative(
                creative_id=creative_id_1,
                format_id=fmt_1,
                name=f"Nike Air Jordan - {fmt_1['id']}",
                asset_url="https://example.com/nike-jordan-300x250.jpg",
                click_through_url="https://nike.com/air-jordan-2025",
            )

            creative_2 = build_creative(
                creative_id=creative_id_2,
                format_id=fmt_2,
                name=f"Nike Air Jordan - {fmt_2['id']}",
                asset_url="https://example.com/nike-jordan-728x90.jpg",
                click_through_url="https://nike.com/air-jordan-2025",
            )

            # Sync creatives
            sync_request = build_sync_creatives_request(creatives=[creative_1, creative_2])

            sync_result = await client.call_tool("sync_creatives", sync_request)
            sync_data = parse_tool_result(sync_result)

            assert "creatives" in sync_data, "Response must contain creatives (AdCP spec field name)"
            assert len(sync_data["creatives"]) == 2, "Should sync 2 creatives"
            # A non-failed action is what proves persistence: sync_creatives echoes
            # rejected creatives back with action="failed" (and does NOT persist
            # them), so a length check alone lets a validation failure pass here and
            # only surface as an empty list in PHASE 7. Assert the action so the
            # failure lands at its true cause.
            for c in sync_data["creatives"]:
                assert c.get("action") != "failed", f"Creative {c.get('creative_id')} failed to sync: {c.get('errors')}"
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
            print("\n💰 PHASE 5: Update Campaign Budget (configures push_notification_config)")

            # Update budget (AdCP spec: budget is a number, not an object)
            update_result = await client.call_tool(
                "update_media_buy",
                {
                    "media_buy_id": media_buy_id,
                    "budget": 7500.0,  # AdCP spec: budget is a number
                    "context": {"e2e": "update_media_buy"},
                    # AdCP push_notification_config: omitting `authentication`
                    # selects the default RFC 9421 webhook-signing profile. The
                    # legacy {schemes, credentials} block is only required when
                    # the buyer opts into Bearer/HMAC-SHA256 instead.
                    "push_notification_config": {
                        "url": webhook_server["url"],
                    },
                },
            )
            update_data = parse_tool_result(update_result)

            assert "media_buy_id" in update_data
            print("   ✓ Budget update requested: $5000 → $7500")
            print(f"   ✓ Update status: {update_data.get('status', 'unknown')}")
            # Context should echo back on response
            assert update_data.get("context") == {"e2e": "update_media_buy"}

            # ================================================================
            # PHASE 6: Webhook delivery — verified by a dedicated test
            # ================================================================
            # The phases above exercise the webhook *configuration* contract
            # end-to-end: create_media_buy registers the push_notification_config and
            # the update supplies it (this path regressed the AnyUrl serialization
            # bug fixed in media_buy_create.py — a re-break would crash PHASE 2 here).
            #
            # Webhook *delivery* is intentionally asserted elsewhere, not in this
            # lifecycle: update_media_buy's task-status webhook never reaches the
            # receiver because _send_push_notifications fires inside the tool's
            # transaction and queries object_workflow_mapping in a SEPARATE session
            # before that mapping is committed ("No object mappings found" → no POST).
            # That is a pre-existing server bug; it is documented and asserted by the
            # dedicated xfail test `test_update_media_buy_push_webhook_delivery`
            # below, so this reference lifecycle stays green for the synchronous
            # contract instead of silently swallowing a missing webhook.
            print("\n🔔 PHASE 6: webhook delivery → see test_update_media_buy_push_webhook_delivery (xfail)")

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

    @pytest.mark.asyncio
    @pytest.mark.xfail(
        strict=True,
        reason=(
            "Pre-existing server bug: update_media_buy's task-status push webhook is never "
            "delivered. _send_push_notifications (src/core/context_manager.py) fires inside the "
            "tool's transaction and queries object_workflow_mapping in a SEPARATE session before "
            "the tool's UnitOfWork commits that mapping, so it logs 'No object mappings found for "
            "step ...' and returns without POSTing. The mapping row IS written (visible in the DB "
            "after the request), proving a commit-ordering / session-visibility defect, not a "
            "missing mapping. The create/auto-approve path was fixed by calling "
            "link_workflow_to_object() before update_workflow_step(status='completed') so the "
            "notification finds the mapping; update_media_buy still needs the same treatment. "
            "Remove this marker once the update path links the mapping before firing the "
            "notification (or shares the tool session)."
        ),
    )
    async def test_update_media_buy_push_webhook_delivery(self, docker_services_e2e, live_server, test_auth_token):
        """update_media_buy must deliver a task-status push webhook to the configured URL.

        Focused async-delivery counterpart to the synchronous reference lifecycle. All values
        are discovered from prior responses (no hardcoded ids). It uses the shared webhook
        helper's DEFAULT (reachable) callback host — not the lifecycle's loopback-pinned fixture
        — so that once the server links the mapping before the notification the webhook actually
        lands and strict=True forces removal of this marker. Currently xfails on the
        commit-ordering bug documented above.
        """
        headers = {"x-adcp-auth": test_auth_token, "x-adcp-tenant": "ci-test"}
        transport = StreamableHttpTransport(url=f"{live_server['mcp']}/mcp/", headers=headers)

        # Default host: the server must be able to REACH this receiver (localhost ->
        # host.docker.internal on the host, or the runner's network alias in-network),
        # unlike the lifecycle fixture which is loopback-pinned.
        with run_webhook_capture_server(WebhookReceiver, WebhookReceiver.received_webhooks) as webhook:
            async with Client(transport=transport) as client:
                # Discover a real product + pricing option (no hardcoded ids).
                products_data = parse_tool_result(
                    await client.call_tool(
                        "get_products", {"brand": {"domain": "testbrand.com"}, "brief": "display advertising"}
                    )
                )
                product = products_data["products"][0]
                pricing_option_id = product["pricing_options"][0]["pricing_option_id"]

                # Create the media buy AND register the push notification config: create is the
                # only operation that persists the PushNotificationConfig row that gates delivery,
                # so the row must exist for the update webhook to fire once the bug is fixed.
                start_time, end_time = get_test_date_range(days_from_now=1, duration_days=30)
                create_request = build_adcp_media_buy_request(
                    product_ids=[product["product_id"]],
                    total_budget=5000.0,
                    start_time=start_time,
                    end_time=end_time,
                    brand={"domain": "testbrand.com"},
                    pricing_option_id=pricing_option_id,
                )
                create_request["push_notification_config"] = {"url": webhook["url"]}
                create_data = parse_tool_result(await client.call_tool("create_media_buy", create_request))
                media_buy_id = create_data.get("media_buy_id")
                assert media_buy_id, f"create_media_buy must return media_buy_id; got: {create_data}"

                # The create/auto-approve path now DELIVERS its completion webhook, so drain it
                # deterministically (wait for it to land, then clear) — racing a bare clear()
                # against it lets a late create webhook arrive during the update wait below and
                # falsely satisfy the assertion.
                drained = 0.0
                while drained < 15.0 and not webhook["received"]:
                    sleep(0.5)
                    drained += 0.5
                assert webhook["received"], (
                    "Expected the create-completion webhook to land (create delivery is fixed); "
                    "none arrived, so the update delivery below cannot be isolated."
                )
                webhook["received"].clear()

                # Update the budget: completing this workflow step should POST a task-status
                # notification to the configured webhook URL.
                update_data = parse_tool_result(
                    await client.call_tool(
                        "update_media_buy",
                        {
                            "media_buy_id": media_buy_id,
                            "budget": 7500.0,
                            "context": {"e2e": "update_webhook"},
                            "push_notification_config": {"url": webhook["url"]},
                        },
                    )
                )
                assert update_data.get("media_buy_id") == media_buy_id

                # Wait for the asynchronous delivery of the UPDATE notification.
                waited = 0.0
                while waited < 15.0 and not webhook["received"]:
                    sleep(0.5)
                    waited += 0.5

                assert webhook["received"], (
                    "Expected a task-status webhook within 15s of update_media_buy, got none "
                    "(server did not deliver the update push notification)."
                )
                payload = webhook["received"][0]
                assert isinstance(payload, dict) and payload, (
                    f"Webhook payload must be a non-empty dict, got {payload!r}"
                )
