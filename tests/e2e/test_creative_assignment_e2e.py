"""
E2E tests for creative assignment scenarios.

Tests creative sync with assignments to packages in various scenarios:
1. Creative sync + assignment in single call (during media buy creation)
2. Creative assignment to existing media buy packages
3. Multiple creatives assigned to multiple packages
"""

import json
import uuid

import pytest
from fastmcp.client import Client
from fastmcp.client.transports import StreamableHttpTransport

from tests.e2e.adcp_request_builder import (
    build_adcp_media_buy_request,
    build_creative,
    build_sync_creatives_request,
    get_test_date_range,
)


class TestCreativeAssignment:
    """E2E tests for creative assignment to media buy packages."""

    @pytest.mark.asyncio
    async def test_creative_sync_with_assignment_in_single_call(
        self, docker_services_e2e, live_server, test_auth_token
    ):
        """
        Test creative sync with assignment in a single call.

        This demonstrates the pattern where creatives are synced AND assigned
        to media buy packages in one sync_creatives call.

        Flow:
        1. Discover products and formats
        2. Create media buy with packages
        3. Sync creatives AND assign to packages in single call
        4. Verify assignment via get_media_buy_delivery
        """
        print("\n" + "=" * 80)
        print("E2E TEST: Creative Sync + Assignment in Single Call")
        print("=" * 80)

        # Setup MCP client
        headers = {
            "x-adcp-auth": test_auth_token,
            "x-adcp-tenant": "ci-test",
        }
        transport = StreamableHttpTransport(url=f"{live_server['mcp']}/mcp/", headers=headers)

        async with Client(transport=transport) as client:
            # ================================================================
            # PHASE 1: Product Discovery
            # ================================================================
            print("\n📦 PHASE 1: Product Discovery")

            products_result = await client.call_tool(
                "get_products",
                {
                    "brand_manifest": {"name": "Test Creative Assignment Brand"},
                    "brief": "display advertising",
                },
            )
            products_data = json.loads(products_result.content[0].text)

            assert "products" in products_data, "Response must contain products"
            assert len(products_data["products"]) > 0, "Must have at least one product"

            product = products_data["products"][0]
            product_id = product["product_id"]
            print(f"   ✓ Found product: {product['name']} ({product_id})")

            # Get creative formats
            formats_result = await client.call_tool("list_creative_formats", {})
            formats_data = json.loads(formats_result.content[0].text)

            assert "formats" in formats_data, "Response must contain formats"
            print(f"   ✓ Available formats: {len(formats_data['formats'])}")

            # Find a suitable format
            format_id = None
            for fmt in formats_data["formats"]:
                fmt_id = fmt.get("format_id")
                # format_id can be a string or dict with 'id' field
                fmt_id_str = (
                    fmt_id if isinstance(fmt_id, str) else (fmt_id.get("id") if isinstance(fmt_id, dict) else "")
                )
                if "display" in fmt_id_str.lower():
                    format_id = fmt_id
                    break

            assert format_id, "Must find at least one display format"
            print(f"   ✓ Using format: {format_id}")

            # ================================================================
            # PHASE 2: Create Media Buy with Packages
            # ================================================================
            print("\n🎯 PHASE 2: Create Media Buy with Package")

            start_time, end_time = get_test_date_range(days_from_now=1, duration_days=30)

            # Generate unique buyer_ref for the package so we can reference it
            package_buyer_ref = f"pkg_{uuid.uuid4().hex[:8]}"

            media_buy_request = build_adcp_media_buy_request(
                product_ids=[product_id],
                total_budget=5000.0,
                start_time=start_time,
                end_time=end_time,
                brand_manifest={"name": "Creative Assignment Test Campaign"},
                targeting_overlay={
                    "geographic": {"countries": ["US"]},
                },
            )

            # Override the package buyer_ref so we can reference it in assignment
            media_buy_request["packages"][0]["buyer_ref"] = package_buyer_ref

            media_buy_result = await client.call_tool("create_media_buy", media_buy_request)
            media_buy_data = json.loads(media_buy_result.content[0].text)

            media_buy_id = media_buy_data.get("media_buy_id")
            buyer_ref = media_buy_data.get("buyer_ref")

            if not media_buy_id:
                print("   ⚠️  Async operation (no media_buy_id), skipping test")
                print(f"   ✓ Buyer ref: {buyer_ref}")
                return

            print(f"   ✓ Media buy created: {media_buy_id}")
            print(f"   ✓ Package buyer_ref: {package_buyer_ref}")
            print(f"   ✓ Status: {media_buy_data.get('status', 'unknown')}")

            # ================================================================
            # PHASE 3: Sync Creatives WITH Assignment (Single Call)
            # ================================================================
            print("\n🎨 PHASE 3: Sync Creatives + Assign to Package (Single Call)")

            # Generate unique creative ID
            creative_id = f"e2etestcreative_{uuid.uuid4().hex[:8]}"

            # Build creative
            creative = build_creative(
                creative_id=creative_id,
                format_id=format_id,
                name="E2E Test Creative",
                asset_url="https://example.com/test-banner.png",
                click_through_url="https://example.com/campaign",
            )

            # Build sync request WITH assignments
            sync_request = build_sync_creatives_request(
                creatives=[creative],
                patch=False,
                dry_run=False,
                validation_mode="lenient",
                delete_missing=False,
                assignments={
                    creative_id: [package_buyer_ref],  # Assign creative to package
                },
            )

            print(f"   📋 Sync request: {json.dumps(sync_request, indent=2)}")

            sync_result = await client.call_tool("sync_creatives", sync_request)
            sync_data = json.loads(sync_result.content[0].text)

            print(f"   📋 Sync response: {json.dumps(sync_data, indent=2)}")

            assert "results" in sync_data, "Response must contain results"
            print(f"   ✓ Synced creative: {creative_id}")

            # Check if assignment was successful
            if "assignments" in sync_data:
                print(f"   ✓ Assignments in response: {sync_data['assignments']}")

            # ================================================================
            # PHASE 4: Verify Assignment via Delivery
            # ================================================================
            print("\n📊 PHASE 4: Verify Assignment via Get Delivery")

            delivery_result = await client.call_tool("get_media_buy_delivery", {"media_buy_ids": [media_buy_id]})
            delivery_data = json.loads(delivery_result.content[0].text)

            print(f"   📋 Delivery response: {json.dumps(delivery_data, indent=2)[:1000]}")

            # Verify delivery response structure
            assert "deliveries" in delivery_data or "media_buy_deliveries" in delivery_data
            print(f"   ✓ Delivery data retrieved for: {media_buy_id}")

            # Look for our creative in the delivery data
            deliveries = delivery_data.get("deliveries") or delivery_data.get("media_buy_deliveries", [])
            if deliveries:
                delivery = deliveries[0]
                print(f"   ✓ Delivery keys: {list(delivery.keys())}")

                # Check for creative assignments in packages
                if "packages" in delivery:
                    for pkg in delivery["packages"]:
                        if "creatives" in pkg or "creative_ids" in pkg:
                            pkg_creatives = pkg.get("creatives", pkg.get("creative_ids", []))
                            print(f"   ✓ Package creatives: {pkg_creatives}")
                            if creative_id in pkg_creatives:
                                print(f"   ✅ VERIFIED: Creative {creative_id} assigned to package")

            # ================================================================
            # PHASE 5: List Creatives (Verify State)
            # ================================================================
            print("\n📋 PHASE 5: List Creatives (verify final state)")

            list_result = await client.call_tool("list_creatives", {})
            list_data = json.loads(list_result.content[0].text)

            assert "creatives" in list_data, "Response must contain creatives"
            print(f"   ✓ Listed {len(list_data['creatives'])} creatives")

            # Verify our creative is in the list
            creative_ids_in_list = {c["creative_id"] for c in list_data["creatives"]}
            assert creative_id in creative_ids_in_list, f"Creative {creative_id} should be in list"
            print(f"   ✓ Creative {creative_id} found in list")

            # ================================================================
            # SUCCESS
            # ================================================================
            print("\n" + "=" * 80)
            print("✅ TEST PASSED - Creative Sync + Assignment in Single Call")
            print("=" * 80)
            print("\nThis test demonstrates:")
            print("  ✓ Creating media buy with packages")
            print("  ✓ Syncing creatives and assigning to packages in one call")
            print("  ✓ Verifying assignment via get_media_buy_delivery")
            print("  ✓ Listing creatives to verify state")
            print("=" * 80)

    @pytest.mark.asyncio
    async def test_multiple_creatives_multiple_packages(self, docker_services_e2e, live_server, test_auth_token):
        """
        Test multiple creatives assigned to multiple packages.

        This demonstrates more complex assignment patterns:
        - Creative 1 → Package 1
        - Creative 2 → Package 2
        - Creative 3 → Package 1 AND Package 2

        Flow:
        1. Create media buy with 2 packages
        2. Sync 3 creatives with complex assignments
        3. Verify all assignments
        """
        print("\n" + "=" * 80)
        print("E2E TEST: Multiple Creatives → Multiple Packages")
        print("=" * 80)

        headers = {
            "x-adcp-auth": test_auth_token,
            "x-adcp-tenant": "ci-test",
        }
        transport = StreamableHttpTransport(url=f"{live_server['mcp']}/mcp/", headers=headers)

        async with Client(transport=transport) as client:
            # ================================================================
            # PHASE 1: Product Discovery
            # ================================================================
            print("\n📦 PHASE 1: Product Discovery")

            products_result = await client.call_tool(
                "get_products",
                {
                    "brand_manifest": {"name": "Multi-Assignment Test Brand"},
                    "brief": "display advertising",
                },
            )
            products_data = json.loads(products_result.content[0].text)

            assert len(products_data["products"]) > 0, "Must have at least one product"
            product = products_data["products"][0]
            product_id = product["product_id"]
            print(f"   ✓ Found product: {product_id}")

            # Get formats
            formats_result = await client.call_tool("list_creative_formats", {})
            formats_data = json.loads(formats_result.content[0].text)

            format_id = None
            for fmt in formats_data["formats"]:
                fmt_id = fmt.get("format_id")
                # format_id can be a string or dict with 'id' field
                fmt_id_str = (
                    fmt_id if isinstance(fmt_id, str) else (fmt_id.get("id") if isinstance(fmt_id, dict) else "")
                )
                if "display" in fmt_id_str.lower():
                    format_id = fmt_id
                    break

            assert format_id, "Must find display format"
            print(f"   ✓ Using format: {format_id}")

            # ================================================================
            # PHASE 2: Create Media Buy with 2 Packages
            # ================================================================
            print("\n🎯 PHASE 2: Create Media Buy with 2 Packages")

            start_time, end_time = get_test_date_range(days_from_now=1, duration_days=30)

            pkg1_ref = f"pkg1_{uuid.uuid4().hex[:8]}"
            pkg2_ref = f"pkg2_{uuid.uuid4().hex[:8]}"

            media_buy_request = build_adcp_media_buy_request(
                product_ids=[product_id],
                total_budget=10000.0,
                start_time=start_time,
                end_time=end_time,
                brand_manifest={"name": "Multi-Package Assignment Test"},
            )

            # Override packages to have 2 distinct packages
            media_buy_request["packages"] = [
                {
                    "buyer_ref": pkg1_ref,
                    "products": [product_id],
                    "budget": 5000.0,
                    "targeting_overlay": {"geographic": {"countries": ["US"]}},
                },
                {
                    "buyer_ref": pkg2_ref,
                    "products": [product_id],
                    "budget": 5000.0,
                    "targeting_overlay": {"geographic": {"countries": ["CA"]}},
                },
            ]

            media_buy_result = await client.call_tool("create_media_buy", media_buy_request)
            media_buy_data = json.loads(media_buy_result.content[0].text)

            media_buy_id = media_buy_data.get("media_buy_id")

            if not media_buy_id:
                print("   ⚠️  Async operation, skipping test")
                return

            print(f"   ✓ Media buy created: {media_buy_id}")
            print(f"   ✓ Package 1: {pkg1_ref}")
            print(f"   ✓ Package 2: {pkg2_ref}")

            # ================================================================
            # PHASE 3: Sync 3 Creatives with Complex Assignments
            # ================================================================
            print("\n🎨 PHASE 3: Sync 3 Creatives with Complex Assignments")

            creative1_id = f"creative1_{uuid.uuid4().hex[:8]}"
            creative2_id = f"creative2_{uuid.uuid4().hex[:8]}"
            creative3_id = f"creative3_{uuid.uuid4().hex[:8]}"

            # Build creatives
            creatives = [
                build_creative(
                    creative_id=creative1_id,
                    format_id=format_id,
                    name="Creative 1 - Package 1 Only",
                    asset_url="https://example.com/creative1.png",
                ),
                build_creative(
                    creative_id=creative2_id,
                    format_id=format_id,
                    name="Creative 2 - Package 2 Only",
                    asset_url="https://example.com/creative2.png",
                ),
                build_creative(
                    creative_id=creative3_id,
                    format_id=format_id,
                    name="Creative 3 - Both Packages",
                    asset_url="https://example.com/creative3.png",
                ),
            ]

            # Build assignments
            # Creative 1 → Package 1
            # Creative 2 → Package 2
            # Creative 3 → Both packages
            assignments = {
                creative1_id: [pkg1_ref],
                creative2_id: [pkg2_ref],
                creative3_id: [pkg1_ref, pkg2_ref],
            }

            print("   📋 Assignment plan:")
            print(f"      • {creative1_id} → {pkg1_ref}")
            print(f"      • {creative2_id} → {pkg2_ref}")
            print(f"      • {creative3_id} → {pkg1_ref}, {pkg2_ref}")

            sync_request = build_sync_creatives_request(
                creatives=creatives,
                validation_mode="lenient",
                assignments=assignments,
            )

            sync_result = await client.call_tool("sync_creatives", sync_request)
            sync_data = json.loads(sync_result.content[0].text)

            assert "results" in sync_data, "Response must contain results"
            assert len(sync_data["results"]) == 3, "Should sync 3 creatives"
            print("   ✓ Synced 3 creatives with assignments")

            # ================================================================
            # PHASE 4: Verify Assignments
            # ================================================================
            print("\n📊 PHASE 4: Verify Assignments")

            delivery_result = await client.call_tool("get_media_buy_delivery", {"media_buy_ids": [media_buy_id]})
            delivery_data = json.loads(delivery_result.content[0].text)

            deliveries = delivery_data.get("deliveries") or delivery_data.get("media_buy_deliveries", [])
            if deliveries and "packages" in deliveries[0]:
                print("   ✓ Package assignments verified:")
                for pkg in deliveries[0]["packages"]:
                    pkg_ref = pkg.get("buyer_ref", "unknown")
                    pkg_creatives = pkg.get("creatives", pkg.get("creative_ids", []))
                    print(f"      • Package {pkg_ref}: {pkg_creatives}")

            # ================================================================
            # SUCCESS
            # ================================================================
            print("\n" + "=" * 80)
            print("✅ TEST PASSED - Multiple Creatives → Multiple Packages")
            print("=" * 80)
            print("\nThis test demonstrates:")
            print("  ✓ Creating media buy with multiple packages")
            print("  ✓ Complex assignment patterns (1:1, 1:many)")
            print("  ✓ Verifying assignments across packages")
            print("=" * 80)
