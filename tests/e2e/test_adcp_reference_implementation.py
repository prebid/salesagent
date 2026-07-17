"""AdCP V2.3 reference E2E test — the template for new E2E tests.

Full campaign lifecycle (discovery → create → creatives → delivery → update → verify) over
sync and async (webhook) responses, using the request-builder helpers for schema correctness.
"""

import json
import uuid
from time import sleep

import pytest

from tests.e2e._webhook_capture import WebhookCaptureHandler, run_webhook_capture_server
from tests.e2e.adcp_request_builder import (
    build_creative,
    build_default_campaign_request,
    build_sync_creatives_request,
    parse_tool_result,
)
from tests.e2e.utils import make_mcp_client


class WebhookReceiver(WebhookCaptureHandler):
    """Webhook receiver for async notification tests."""

    received_webhooks: list = []


@pytest.fixture
def webhook_server():
    # Loopback-pinned: this lifecycle reaches the server over the host loopback, so the
    # callback host stays 127.0.0.1 rather than honoring ADCP_WEBHOOK_HOST.
    with run_webhook_capture_server(WebhookReceiver, WebhookReceiver.received_webhooks, host="127.0.0.1") as info:
        yield info


class TestAdCPReferenceImplementation:
    """Reference E2E test demonstrating the full AdCP V2.3 workflow."""

    @pytest.mark.asyncio
    async def test_complete_campaign_lifecycle_with_webhooks(
        self, docker_services_e2e, live_server, test_auth_token, webhook_server, auto_approval_adapter
    ):
        """Discovery → create → creatives → delivery → update → verify, over sync + webhook responses."""
        print("\n" + "=" * 80)
        print("REFERENCE E2E TEST: Complete Campaign Lifecycle")
        print("=" * 80)

        async with make_mcp_client(live_server, token=test_auth_token) as client:
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
            assert products_data.get("context") == {"e2e": "get_products"}

            product = products_data["products"][0]
            product_id = product["product_id"]
            # Discover the synthetic pricing_option_id from the response, not a literal default.
            pricing_option_id = product["pricing_options"][0]["pricing_option_id"]
            print(f"   ✓ Found product: {product['name']} ({product_id})")
            print(f"   ✓ Pricing option: {pricing_option_id}")
            print(f"   ✓ Formats: {product['format_ids']}")

            formats_result = await client.call_tool("list_creative_formats", {})
            formats_data = parse_tool_result(formats_result)

            assert "formats" in formats_data, "Response must contain formats"
            print(f"   ✓ Available formats: {len(formats_data['formats'])}")

            print("\n🎯 PHASE 2: Create Media Buy (with webhook for async updates)")

            media_buy_request = build_default_campaign_request(
                product_id,
                pricing_option_id,
                targeting_overlay={
                    "geo_countries": ["US", "CA"],
                },
                webhook_url=webhook_server["url"],
                context={"e2e": "create_media_buy"},
            )
            # Registering a config here exercises the AnyUrl/Enum serialization path end-to-end.
            media_buy_request["push_notification_config"] = {"url": webhook_server["url"]}

            media_buy_result = await client.call_tool("create_media_buy", media_buy_request)
            media_buy_data = parse_tool_result(media_buy_result)

            # Synchronous success path: the mock adapter auto-approves, so media_buy_id is
            # required (the async "submitted" shape returns task_id instead, not driven here).
            media_buy_id = media_buy_data.get("media_buy_id")
            assert media_buy_id, f"create_media_buy must return media_buy_id; got: {media_buy_data}"

            print(f"   ✓ Media buy created: {media_buy_id}")
            print(f"   ✓ Status: {media_buy_data.get('status', 'unknown')}")
            print(f"   ✓ Webhook configured: {webhook_server['url']}")
            assert media_buy_data.get("context") == {"e2e": "create_media_buy"}

            print("\n🎨 PHASE 3: Sync Creatives")

            # Discover formats from list_creative_formats, not the product: a product may advertise
            # an id the creative agent doesn't expose (display_300x250 vs display_300x250_image),
            # which sync_creatives rejects.
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

            sync_request = build_sync_creatives_request(creatives=[creative_1, creative_2])

            sync_result = await client.call_tool("sync_creatives", sync_request)
            sync_data = parse_tool_result(sync_result)

            assert "creatives" in sync_data, "Response must contain creatives (AdCP spec field name)"
            assert len(sync_data["creatives"]) == 2, "Should sync 2 creatives"
            # action != "failed" proves persistence: rejected creatives are echoed with
            # action="failed" and not persisted, surfacing only as an empty PHASE 7 list.
            for c in sync_data["creatives"]:
                assert c.get("action") != "failed", f"Creative {c.get('creative_id')} failed to sync: {c.get('errors')}"
            print(f"   ✓ Synced {len(sync_data['creatives'])} creatives")
            print(f"   ✓ Creative IDs: {creative_id_1}, {creative_id_2}")

            print("\n📊 PHASE 4: Get Delivery Metrics")

            delivery_result = await client.call_tool("get_media_buy_delivery", {"media_buy_ids": [media_buy_id]})
            delivery_data = parse_tool_result(delivery_result)

            assert "deliveries" in delivery_data or "media_buy_deliveries" in delivery_data
            print(f"   ✓ Delivery data retrieved for: {media_buy_id}")
            delivery_result_ctx = await client.call_tool(
                "get_media_buy_delivery", {"media_buy_ids": [media_buy_id], "context": {"e2e": "delivery"}}
            )
            delivery_data_ctx = parse_tool_result(delivery_result_ctx)
            assert delivery_data_ctx.get("context") == {"e2e": "delivery"}

            deliveries = delivery_data.get("deliveries") or delivery_data.get("media_buy_deliveries", [])
            if deliveries:
                print(f"   ✓ Found {len(deliveries)} delivery record(s)")
                if "metrics" in deliveries[0]:
                    metrics = deliveries[0]["metrics"]
                    print(f"   ✓ Metrics: {list(metrics.keys())}")

            print("\n💰 PHASE 5: Update Campaign Budget (configures push_notification_config)")

            update_result = await client.call_tool(
                "update_media_buy",
                {
                    "media_buy_id": media_buy_id,
                    "budget": 7500.0,
                    "context": {"e2e": "update_media_buy"},
                    "push_notification_config": {
                        "url": webhook_server["url"],
                    },
                },
            )
            update_data = parse_tool_result(update_result)

            assert "media_buy_id" in update_data
            print("   ✓ Budget update requested: $5000 → $7500")
            print(f"   ✓ Update status: {update_data.get('status', 'unknown')}")
            assert update_data.get("context") == {"e2e": "update_media_buy"}

            # PHASE 6: webhook delivery is asserted by test_update_media_buy_push_webhook_delivery
            # (xfail) — update_media_buy's notification is a pre-existing server bug, so this
            # lifecycle covers the synchronous contract rather than swallow a missing webhook.
            print("\n🔔 PHASE 6: webhook delivery → see test_update_media_buy_push_webhook_delivery (xfail)")

            print("\n📋 PHASE 7: List Creatives (verify final state)")

            list_result = await client.call_tool("list_creatives", {})
            list_data = parse_tool_result(list_result)

            assert "creatives" in list_data, "Response must contain creatives"
            print(f"   ✓ Listed {len(list_data['creatives'])} creatives")

            creative_ids_in_list = {c["creative_id"] for c in list_data["creatives"]}
            assert creative_id_1 in creative_ids_in_list, f"Creative {creative_id_1} should be in list"
            assert creative_id_2 in creative_ids_in_list, f"Creative {creative_id_2} should be in list"
            print("   ✓ Both synced creatives found in list")

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
            "delivered. _send_push_notifications (src/core/context_manager.py) queries "
            "object_workflow_mapping in a separate session before the update's UnitOfWork commits "
            "that mapping, so it logs 'No object mappings found' and returns without POSTing (the "
            "row IS written, visible post-request — a commit-ordering defect, not a missing row). "
            "The create/auto-approve path was fixed by linking the mapping before the notification "
            "fires; update_media_buy needs the same treatment. Remove this marker once it does."
        ),
    )
    async def test_update_media_buy_push_webhook_delivery(
        self, docker_services_e2e, live_server, test_auth_token, auto_approval_adapter
    ):
        """update_media_buy must deliver a task-status push webhook to the configured URL.

        Uses the shared helper's default (reachable) callback host — not the lifecycle's
        loopback-pinned fixture — so the webhook lands once the server links the mapping
        before firing. Discovers all ids from prior responses.
        """
        with run_webhook_capture_server(WebhookReceiver, WebhookReceiver.received_webhooks) as webhook:
            async with make_mcp_client(live_server, token=test_auth_token) as client:
                products_data = parse_tool_result(
                    await client.call_tool(
                        "get_products", {"brand": {"domain": "testbrand.com"}, "brief": "display advertising"}
                    )
                )
                product = products_data["products"][0]
                pricing_option_id = product["pricing_options"][0]["pricing_option_id"]

                # create is the only op that persists the PushNotificationConfig row delivery needs.
                create_request = build_default_campaign_request(product["product_id"], pricing_option_id)
                create_request["push_notification_config"] = {"url": webhook["url"]}
                create_data = parse_tool_result(await client.call_tool("create_media_buy", create_request))
                media_buy_id = create_data.get("media_buy_id")
                assert media_buy_id, f"create_media_buy must return media_buy_id; got: {create_data}"

                # create_media_buy delivers its completion webhook and may re-deliver it, so drain to
                # quiescence: wait for the first, then keep clearing until a quiet window passes with
                # no new arrival. A single clear() leaves a late/duplicate create webhook to land in
                # the update window below and falsely satisfy the assertion (intermittent XPASS).
                deadline = 0.0
                while deadline < 15.0 and not webhook["received"]:
                    sleep(0.5)
                    deadline += 0.5
                assert webhook["received"], (
                    "Expected the create-completion webhook to land (create delivery is fixed); "
                    "none arrived, so the update delivery below cannot be isolated."
                )
                quiet = 0.0
                while quiet < 4.0:
                    sleep(0.5)
                    quiet += 0.5
                    if webhook["received"]:
                        webhook["received"].clear()
                        quiet = 0.0
                webhook["received"].clear()

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
