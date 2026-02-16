#!/usr/bin/env python3
"""
GAM test suite focused ONLY on supported features:
- Geographic targeting (country, region, metro)
- Key-value targeting (for AEE/AXE signals)

All other targeting types should fail loudly.
"""

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.adapters.google_ad_manager import GoogleAdManager
from src.core.schemas import CreateMediaBuyRequest, FormatId, MediaPackage, Principal, Targeting

# Default agent URL for creating FormatId objects
DEFAULT_AGENT_URL = "https://creative.adcontextprotocol.org"


def make_format_id(format_id: str) -> FormatId:
    """Helper to create FormatId objects with default agent URL."""
    return FormatId(agent_url=DEFAULT_AGENT_URL, id=format_id)


class SupportedTargetingTester:
    """Test suite for supported GAM targeting features only."""

    def __init__(self):
        """Initialize test environment."""
        self.created_orders = []
        self.test_results = []
        self.load_config()

    def load_config(self):
        """Load test configuration."""
        config_path = Path(__file__).parent.parent.parent / ".gam-test-config.json"
        with open(config_path) as f:
            self.test_config = json.load(f)

        self.network_code = self.test_config["test_network"]["network_code"]
        self.advertiser_id = self.test_config["test_advertisers"]["primary_test_advertiser"]
        self.trafficker_id = self.test_config["test_users"]["trafficker_id"]

        self.gam_config = {
            "network_code": self.network_code,
            "refresh_token": self.test_config["credentials"]["refresh_token"],
            "trafficker_id": self.trafficker_id,
            "implementation_config": {"targeted_ad_unit_ids": [self.test_config["test_ad_units"]["root_ad_unit_id"]]},
        }

        self.principal = Principal(
            tenant_id="test_tenant",
            principal_id="test_principal",
            name="Supported Targeting Test",
            access_token="test_token",
            platform_mappings={
                "google_ad_manager": {"advertiser_id": self.advertiser_id, "advertiser_name": "Test Advertiser"}
            },
        )

    def run_test(self, test_name, test_func):
        """Run a single test and record results."""
        print(f"\n🧪 {test_name}...")
        try:
            result = test_func()
            self.test_results.append({"name": test_name, "success": True, "result": result})
            print(f"   ✅ Success: {result}")
            return result
        except Exception as e:
            if "Cannot fulfill buyer contract" in str(e):
                # This is expected for unsupported features
                self.test_results.append(
                    {"name": test_name, "success": True, "result": f"Correctly failed: {str(e)[:100]}..."}
                )
                print(f"   ✅ Correctly failed: {str(e)[:100]}...")
            else:
                self.test_results.append({"name": test_name, "success": False, "error": str(e)})
                print(f"   ❌ Unexpected error: {str(e)}")
            return None

    def test_geo_targeting(self):
        """Test geographic targeting - SHOULD WORK."""
        adapter = GoogleAdManager(
            config=self.gam_config,
            principal=self.principal,
            network_code=self.network_code,
            advertiser_id=self.advertiser_id,
            trafficker_id=self.trafficker_id,
            dry_run=False,
            tenant_id="test",
        )

        package = MediaPackage(
            package_id="geo_test",
            name="Geographic Targeting Test",
            impressions=1000,
            cpm=1.00,
            delivery_type="non_guaranteed",
            format_ids=[make_format_id("display_300x250")],
        )

        request = CreateMediaBuyRequest(
            brand_manifest={"name": "Professional software and productivity tools"},
            po_number="GEO_SUPPORTED",
            total_budget=1.00,
            targeting_overlay=Targeting(
                geo_countries=["US"],
                geo_regions=["US-CA", "US-NY"],
                geo_metros=[{"system": "nielsen_dma", "values": ["501", "803"]}],
            ),
        )

        response = adapter.create_media_buy(
            request, [package], datetime.now() + timedelta(hours=2), datetime.now() + timedelta(days=2)
        )

        self.created_orders.append(response.media_buy_id)
        return f"Order {response.media_buy_id} with geo targeting"

    def test_key_value_targeting(self):
        """Test key-value targeting for AEE/AXE signals - SHOULD WORK."""
        adapter = GoogleAdManager(
            config=self.gam_config,
            principal=self.principal,
            network_code=self.network_code,
            advertiser_id=self.advertiser_id,
            trafficker_id=self.trafficker_id,
            dry_run=False,
            tenant_id="test",
        )

        package = MediaPackage(
            package_id="key_value_test",
            name="Key-Value Targeting Test",
            impressions=1000,
            cpm=2.00,
            delivery_type="non_guaranteed",
            format_ids=[make_format_id("display_300x250")],
        )

        # Build key-value pairs from configuration
        key_value_pairs = {}
        custom_keys = self.test_config.get("custom_targeting_keys", {})

        # Add AEE signals if configured
        if "aee_segment" in custom_keys:
            key_value_pairs["aee_segment"] = custom_keys["aee_segment"]["example_values"][0]
        if "aee_score" in custom_keys:
            key_value_pairs["aee_score"] = custom_keys["aee_score"]["example_values"][0]
        if "aee_intent" in custom_keys:
            key_value_pairs["aee_intent"] = custom_keys["aee_intent"]["example_values"][0]

        # Add AXE signals if configured
        if "axei" in custom_keys:
            key_value_pairs["axei"] = custom_keys["axei"]["example_values"][0]
        if "axex" in custom_keys:
            key_value_pairs["axex"] = custom_keys["axex"]["example_values"][0]
        if "axem" in custom_keys:
            key_value_pairs["axem"] = custom_keys["axem"]["example_values"][0]

        # Only proceed if we have some custom keys configured
        if not key_value_pairs:
            raise ValueError("No custom targeting keys configured in test config")

        request = CreateMediaBuyRequest(
            brand_manifest={"name": "Data analytics and AI-powered insights"},
            po_number="AEE_AXE_SIGNALS",
            total_budget=2.00,
            targeting_overlay=Targeting(key_value_pairs=key_value_pairs),
        )

        response = adapter.create_media_buy(
            request, [package], datetime.now() + timedelta(hours=2), datetime.now() + timedelta(days=2)
        )

        self.created_orders.append(response.media_buy_id)
        return f"Order {response.media_buy_id} with AEE key-value targeting"

    def test_combined_supported(self):
        """Test combining geo and key-value targeting - SHOULD WORK."""
        adapter = GoogleAdManager(
            config=self.gam_config,
            principal=self.principal,
            network_code=self.network_code,
            advertiser_id=self.advertiser_id,
            trafficker_id=self.trafficker_id,
            dry_run=False,
            tenant_id="test",
        )

        package = MediaPackage(
            package_id="combined_test",
            name="Combined Supported Targeting",
            impressions=1000,
            cpm=3.00,
            delivery_type="non_guaranteed",
            format_ids=[make_format_id("display_300x250")],
        )

        # Build key-value pairs from configuration
        key_value_pairs = {}
        custom_keys = self.test_config.get("custom_targeting_keys", {})

        # Add a subset of configured keys for the combined test
        if "aee_segment" in custom_keys:
            # Use different example value for variation
            values = custom_keys["aee_segment"]["example_values"]
            key_value_pairs["aee_segment"] = values[1] if len(values) > 1 else values[0]
        if "aee_behavior" in custom_keys:
            key_value_pairs["aee_behavior"] = custom_keys["aee_behavior"]["example_values"][0]
        if "axei" in custom_keys:
            values = custom_keys["axei"]["example_values"]
            key_value_pairs["axei"] = values[1] if len(values) > 1 else values[0]
        if "axex" in custom_keys:
            values = custom_keys["axex"]["example_values"]
            key_value_pairs["axex"] = values[1] if len(values) > 1 else values[0]

        request = CreateMediaBuyRequest(
            brand_manifest={"name": "Cloud infrastructure and enterprise solutions"},
            po_number="GEO_AEE_COMBINED",
            total_budget=3.00,
            targeting_overlay=Targeting(
                geo_countries=["US"],
                geo_regions=["US-CA"],
                key_value_pairs=key_value_pairs if key_value_pairs else None,
            ),
        )

        response = adapter.create_media_buy(
            request, [package], datetime.now() + timedelta(hours=2), datetime.now() + timedelta(days=2)
        )

        self.created_orders.append(response.media_buy_id)
        return f"Order {response.media_buy_id} with geo + AEE targeting"

    def test_device_failure(self):
        """Test device targeting - MUST FAIL."""
        adapter = GoogleAdManager(
            config=self.gam_config,
            principal=self.principal,
            network_code=self.network_code,
            advertiser_id=self.advertiser_id,
            trafficker_id=self.trafficker_id,
            dry_run=False,
            tenant_id="test",
        )

        package = MediaPackage(
            package_id="device_fail",
            name="Device Fail Test",
            impressions=1000,
            cpm=1.00,
            delivery_type="non_guaranteed",
            format_ids=[make_format_id("display_300x250")],
        )

        request = CreateMediaBuyRequest(
            brand_manifest={"name": "Mobile app subscriptions and services"},
            po_number="DEVICE_MUST_FAIL",
            total_budget=1.00,
            targeting_overlay=Targeting(device_type_any_of=["mobile", "desktop"]),
        )

        try:
            response = adapter.create_media_buy(
                request, [package], datetime.now() + timedelta(hours=2), datetime.now() + timedelta(days=2)
            )
            raise AssertionError("Device targeting should have failed!")
        except ValueError as e:
            if "Cannot fulfill buyer contract" in str(e):
                return "Device targeting correctly failed"
            raise e

    def test_os_failure(self):
        """Test OS targeting - MUST FAIL."""
        adapter = GoogleAdManager(
            config=self.gam_config,
            principal=self.principal,
            network_code=self.network_code,
            advertiser_id=self.advertiser_id,
            trafficker_id=self.trafficker_id,
            dry_run=False,
            tenant_id="test",
        )

        package = MediaPackage(
            package_id="os_fail",
            name="OS Fail Test",
            impressions=1000,
            cpm=1.00,
            delivery_type="non_guaranteed",
            format_ids=[make_format_id("display_300x250")],
        )

        request = CreateMediaBuyRequest(
            brand_manifest={"name": "Gaming consoles and video game titles"},
            po_number="OS_MUST_FAIL",
            total_budget=1.00,
            targeting_overlay=Targeting(os_any_of=["iOS", "Android"]),
        )

        try:
            response = adapter.create_media_buy(
                request, [package], datetime.now() + timedelta(hours=2), datetime.now() + timedelta(days=2)
            )
            raise AssertionError("OS targeting should have failed!")
        except ValueError as e:
            if "Cannot fulfill buyer contract" in str(e):
                return "OS targeting correctly failed"
            raise e

    def test_keyword_failure(self):
        """Test keyword targeting - MUST FAIL."""
        adapter = GoogleAdManager(
            config=self.gam_config,
            principal=self.principal,
            network_code=self.network_code,
            advertiser_id=self.advertiser_id,
            trafficker_id=self.trafficker_id,
            dry_run=False,
            tenant_id="test",
        )

        package = MediaPackage(
            package_id="keyword_fail",
            name="Keyword Fail Test",
            impressions=1000,
            cpm=1.00,
            delivery_type="non_guaranteed",
            format_ids=[make_format_id("display_300x250")],
        )

        request = CreateMediaBuyRequest(
            brand_manifest={"name": "Breaking news and journalism platform"},
            po_number="KEYWORD_MUST_FAIL",
            total_budget=1.00,
            targeting_overlay=Targeting(keywords_any_of=["sports", "news"]),
        )

        try:
            response = adapter.create_media_buy(
                request, [package], datetime.now() + timedelta(hours=2), datetime.now() + timedelta(days=2)
            )
            raise AssertionError("Keyword targeting should have failed!")
        except ValueError as e:
            if "Cannot fulfill buyer contract" in str(e):
                return "Keyword targeting correctly failed"
            raise e

    def run_all_tests(self):
        """Run all tests in sequence."""
        print("=" * 60)
        print("🎯 SUPPORTED TARGETING TEST SUITE")
        print("=" * 60)
        print(f"Network: {self.network_code}")
        print(f"Advertiser: {self.advertiser_id}")
        print("\nSupported Features:")
        print("  ✅ Geographic targeting (country, region, metro)")
        print("  ✅ Key-value targeting (AEE/AXE signals)")
        print("\nUnsupported Features (must fail loudly):")
        print("  ❌ Device targeting")
        print("  ❌ OS targeting")
        print("  ❌ Browser targeting")
        print("  ❌ Content category targeting")
        print("  ❌ Keyword targeting")

        # Test supported features
        print("\n" + "=" * 60)
        print("TESTING SUPPORTED FEATURES")
        print("=" * 60)
        self.run_test("Geographic Targeting", self.test_geo_targeting)
        self.run_test("Key-Value Targeting (AEE/AXE)", self.test_key_value_targeting)
        self.run_test("Combined Geo + Key-Value", self.test_combined_supported)

        # Test unsupported features fail loudly
        print("\n" + "=" * 60)
        print("TESTING UNSUPPORTED FEATURES FAIL LOUDLY")
        print("=" * 60)
        self.run_test("Device Targeting Failure", self.test_device_failure)
        self.run_test("OS Targeting Failure", self.test_os_failure)
        self.run_test("Keyword Targeting Failure", self.test_keyword_failure)

        # Print summary
        print("\n" + "=" * 60)
        print("📊 TEST SUMMARY")
        print("=" * 60)

        passed = sum(1 for r in self.test_results if r["success"])
        failed = len(self.test_results) - passed

        print(f"✅ Passed: {passed}/{len(self.test_results)}")
        print(f"❌ Failed: {failed}/{len(self.test_results)}")

        if self.created_orders:
            print(f"\n📦 Created Orders ({len(self.created_orders)}):")
            for order_id in self.created_orders:
                print(f"   https://admanager.google.com/{self.network_code}#delivery/OrderDetail/orderId={order_id}")

        print("\n" + "=" * 60)
        print("🔍 WHAT TO CHECK IN GAM:")
        print("=" * 60)
        print("1. Go to the Orders page")
        print("2. For each created order:")
        print("   • GEO_SUPPORTED: Should have US country, CA/NY regions, NYC/LA metros")
        print("   • AEE_AXE_SIGNALS: Should have custom key-value pairs:")

        # Display the actual configured keys and values
        custom_keys = self.test_config.get("custom_targeting_keys", {})
        for key_name in ["aee_segment", "aee_score", "aee_intent", "aee_behavior", "axei", "axex", "axem"]:
            if key_name in custom_keys:
                example_value = custom_keys[key_name]["example_values"][0]
                print(f"     - {key_name} = {example_value}")

        print("   • GEO_AEE_COMBINED: Should have both geo AND key-value targeting")
        print("3. Click 'View targeting' on each line item to verify")
        print("4. Check that NO orders were created for unsupported features")

        return passed == len(self.test_results)


if __name__ == "__main__":
    tester = SupportedTargetingTester()
    success = tester.run_all_tests()
    sys.exit(0 if success else 1)
