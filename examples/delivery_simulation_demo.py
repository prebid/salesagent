#!/usr/bin/env python3
"""Demo script for mock adapter delivery simulation with webhooks.

This script demonstrates how to:
1. Configure a mock adapter product with delivery simulation
2. Set up a webhook endpoint to receive delivery updates
3. Create a media buy and watch delivery progress in real-time
4. Process delivery updates as they arrive

Perfect for testing AI agent responses to campaign delivery!
"""

import asyncio
import json
from datetime import UTC, datetime, timedelta

from aiohttp import web

# Track received webhooks
webhooks_received = []
simulation_complete = asyncio.Event()


async def webhook_handler(request):
    """Handle incoming webhook notifications from delivery simulation."""
    try:
        payload = await request.json()

        # Store webhook
        webhooks_received.append(payload)

        # Extract delivery data
        data = payload.get("data", {})
        progress = data.get("progress", {})
        delivery = data.get("delivery", {})
        status = data.get("status", "unknown")

        # Print progress update
        print(f"\n📊 Delivery Update #{len(webhooks_received)}")
        print(f"   Status: {status}")
        print(f"   Progress: {progress.get('elapsed_hours', 0):.1f}h / {progress.get('total_hours', 0):.1f}h")
        print(f"   Completion: {progress.get('progress_percentage', 0):.1f}%")
        print(f"   Impressions: {delivery.get('impressions', 0):,}")
        print(f"   Spend: ${delivery.get('spend', 0):,.2f} / ${delivery.get('total_budget', 0):,.2f}")
        print(f"   Pacing: {delivery.get('pacing_percentage', 0):.1f}%")

        # Check if campaign completed
        if status == "completed":
            print("\n🎉 Campaign simulation completed!")
            simulation_complete.set()

        return web.Response(text="OK", status=200)

    except Exception as e:
        print(f"❌ Error handling webhook: {e}")
        return web.Response(text="Error handling webhook", status=400)


async def start_webhook_server(port=8888):
    """Start local webhook server to receive notifications."""
    app = web.Application()
    app.router.add_post("/webhook", webhook_handler)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "localhost", port)
    await site.start()

    print(f"✅ Webhook server started on http://localhost:{port}/webhook")
    return runner


async def configure_mock_product(tenant_id: str, product_id: str):
    """Configure mock adapter product with delivery simulation.

    In practice, you'd do this via the Admin UI or API.
    This is just for demonstration.
    """
    print("\n📋 Configuration Steps (do this in Admin UI):")
    print(f"   1. Navigate to Products for tenant '{tenant_id}'")
    print(f"   2. Click Configure on product '{product_id}'")
    print("   3. Scroll to 'Delivery Simulation' section")
    print("   4. Check 'Enable Delivery Simulation'")
    print("   5. Set Time Acceleration: 3600 (1 sec = 1 hour)")
    print("   6. Set Update Interval: 1.0 seconds")
    print("   7. Save configuration")
    print("\nOR update the database directly:")
    print(
        f"   UPDATE products SET implementation_config = '{json.dumps({'delivery_simulation': {'enabled': True, 'time_acceleration': 3600, 'update_interval_seconds': 1.0}})}' WHERE product_id = '{product_id}';"
    )


async def register_webhook(tenant_id: str, principal_id: str, webhook_url: str):
    """Register webhook for principal.

    In practice, you'd do this via the Admin UI.
    """
    print("\n🔗 Webhook Registration Steps (do this in Admin UI):")
    print(f"   1. Navigate to Principals for tenant '{tenant_id}'")
    print(f"   2. Select principal '{principal_id}'")
    print("   3. Click 'Manage Webhooks'")
    print(f"   4. Add webhook URL: {webhook_url}")
    print("   5. Set authentication (if needed)")
    print("   6. Test webhook delivery")
    print("   7. Save")


async def create_media_buy_via_mcp():
    """Create a media buy via MCP client.

    This would trigger the delivery simulation.
    """
    print("\n🚀 Creating Media Buy (example MCP code):")
    print(
        """
    from fastmcp.client import Client
    from fastmcp.client.transports import StreamableHttpTransport

    headers = {"x-adcp-auth": "your_token"}
    transport = StreamableHttpTransport(
        url="http://localhost:8080/mcp/",
        headers=headers
    )

    async with Client(transport=transport) as client:
        result = await client.tools.create_media_buy(
            promoted_offering="Delivery Simulation Test",
            product_ids=["prod_mock_1"],
            total_budget=5000.0,
            flight_start_date="2025-10-08",
            flight_end_date="2025-10-15"  # 7-day campaign
        )

        print(f"Media Buy Created: {result.media_buy_id}")
        # Delivery simulation starts automatically!
    """
    )


async def demo_simulation():
    """Run the full demonstration."""
    print("=" * 70)
    print("Mock Adapter Delivery Simulation Demo")
    print("=" * 70)

    # Configuration
    TENANT_ID = "tenant_demo"
    PRODUCT_ID = "prod_mock_1"
    PRINCIPAL_ID = "principal_demo"
    WEBHOOK_PORT = 8888
    WEBHOOK_URL = f"http://localhost:{WEBHOOK_PORT}/webhook"

    # Step 1: Start webhook server
    print("\n" + "=" * 70)
    print("Step 1: Starting Webhook Server")
    print("=" * 70)
    runner = await start_webhook_server(WEBHOOK_PORT)

    try:
        # Step 2: Configure product
        print("\n" + "=" * 70)
        print("Step 2: Configure Mock Product")
        print("=" * 70)
        await configure_mock_product(TENANT_ID, PRODUCT_ID)

        # Step 3: Register webhook
        print("\n" + "=" * 70)
        print("Step 3: Register Webhook")
        print("=" * 70)
        await register_webhook(TENANT_ID, PRINCIPAL_ID, WEBHOOK_URL)

        # Step 4: Create media buy
        print("\n" + "=" * 70)
        print("Step 4: Create Media Buy")
        print("=" * 70)
        await create_media_buy_via_mcp()

        # Step 5: Wait for webhooks
        print("\n" + "=" * 70)
        print("Step 5: Receiving Delivery Updates")
        print("=" * 70)
        print("\n🔄 Waiting for delivery webhooks...")
        print("   (In real scenario, webhooks would arrive here)")
        print("   Campaign duration: 7 days")
        print("   Simulated duration: 168 seconds (~2.8 minutes)")
        print("   Webhook interval: Every 1 second = 1 hour progress")

        # For demo purposes, simulate some example webhooks
        print("\n📨 Example Webhook Sequence:")
        example_times = [0, 24, 72, 120, 168]  # hours
        for hours in example_times:
            progress_pct = (hours / 168) * 100
            impressions = int(50000 * (hours / 168))
            spend = 5000 * (hours / 168)

            status = "started" if hours == 0 else "delivering" if hours < 168 else "completed"

            example_payload = {
                "task_id": "buy_demo123",
                "status": status,
                "timestamp": datetime.now(UTC).isoformat(),
                "data": {
                    "event_type": "delivery_update",
                    "media_buy_id": "buy_demo123",
                    "status": status,
                    "simulated_time": (datetime.now(UTC) + timedelta(hours=hours)).isoformat(),
                    "progress": {"elapsed_hours": hours, "total_hours": 168, "progress_percentage": progress_pct},
                    "delivery": {
                        "impressions": impressions,
                        "spend": spend,
                        "total_budget": 5000.0,
                        "pacing_percentage": (spend / 5000) * 100,
                    },
                    "metrics": {"cpm": 10.0, "clicks": int(impressions * 0.01), "ctr": 0.01},
                },
            }

            # Simulate webhook arrival
            webhooks_received.append(example_payload)

            # Create mock request with proper closure (bind payload to avoid loop variable issue)
            def create_handler(payload):
                async def make_json():
                    return payload

                return make_json

            mock_request = type("Request", (), {"json": create_handler(example_payload)})()
            await webhook_handler(mock_request)

            if status != "completed":
                await asyncio.sleep(1)  # Simulate time between webhooks

        # Summary
        print("\n" + "=" * 70)
        print("Summary")
        print("=" * 70)
        print(f"✅ Received {len(webhooks_received)} delivery updates")
        print("✅ Campaign progressed from 0% to 100%")
        print(f"✅ Total impressions delivered: {webhooks_received[-1]['data']['delivery']['impressions']:,}")
        print(f"✅ Total spend: ${webhooks_received[-1]['data']['delivery']['spend']:,.2f}")
        print("\n🎯 AI Agent Integration Points:")
        print("   • Monitor pacing vs. progress")
        print("   • Adjust bids if under/over-delivering")
        print("   • Pause campaign if performance issues")
        print("   • Generate reports on completion")
        print("   • Optimize targeting based on delivery")

    finally:
        # Cleanup
        await runner.cleanup()
        print("\n👋 Demo complete!")


def main():
    """Main entry point."""
    try:
        asyncio.run(demo_simulation())
    except KeyboardInterrupt:
        print("\n\n⚠️ Demo interrupted by user")
    except Exception as e:
        print(f"\n\n❌ Demo failed: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    main()
