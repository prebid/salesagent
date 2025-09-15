#!/usr/bin/env python3
"""
Demo script showing HTML5 creative support in GAM adapter.

This script demonstrates:
1. HTML5 creative detection from file extensions and format strings
2. Html5Creative creation in GAM with proper structure
3. Support for backup images and delivery settings
4. Validation of HTML5 creatives against GAM limits
"""

from src.adapters.google_ad_manager import GoogleAdManager
from src.core.schemas import Principal


def main():
    """Demonstrate HTML5 creative support."""
    print("🎨 HTML5 Creative Support Demo for Google Ad Manager")
    print("=" * 60)

    # Create a mock principal
    principal = Principal(
        principal_id="test_principal",
        name="Test Advertiser",
        access_token="test_token",
        platform_mappings={"google_ad_manager": {"advertiser_id": "123456"}},
    )

    # GAM adapter configuration
    config = {
        "network_code": "123456",
        "service_account_key_file": "/path/to/key.json",
        "trafficker_id": "trafficker_123",
    }

    # Initialize GAM adapter in dry-run mode
    adapter = GoogleAdManager(config=config, principal=principal, dry_run=True)

    print("\n1. Testing HTML5 Creative Type Detection")
    print("-" * 40)

    # Test various HTML5 creative asset configurations
    test_assets = [
        {
            "name": "HTML5 Banner",
            "media_url": "https://example.com/banner.html",
            "format": "display_970x250",
        },
        {
            "name": "Interactive ZIP",
            "media_url": "https://example.com/interactive.zip",
            "format": "html5_interactive",
        },
        {
            "name": "Rich Media",
            "url": "https://example.com/rich.htm",
            "format": "rich_media_banner",
        },
        {
            "name": "HTML5 Mobile",
            "media_url": "https://example.com/mobile.html5",
            "format": "mobile_320x50",
        },
    ]

    for asset in test_assets:
        creative_type = adapter._get_creative_type(asset)
        print(f"✓ {asset['name']}: {creative_type}")
        if creative_type == "html5":
            print(f"  └─ Detected from: {asset.get('media_url') or asset.get('url')}")

    print("\n2. Testing HTML5 Creative Creation")
    print("-" * 40)

    # Create a comprehensive HTML5 creative asset
    html5_asset = {
        "creative_id": "html5_demo_1",
        "name": "Interactive HTML5 Banner",
        "format": "display_970x250",
        "media_url": "https://example.com/creative.zip",
        "click_url": "https://example.com/landing",
        "backup_image_url": "https://example.com/backup.jpg",
        "delivery_settings": {
            "interstitial": False,
            "override_size": False,
        },
        "package_assignments": ["demo_package"],
    }

    print(f"Creating GAM creative for: {html5_asset['name']}")
    creative_type = adapter._get_creative_type(html5_asset)
    print(f"Detected type: {creative_type}")

    if creative_type == "html5":
        # Create the GAM creative object
        base_creative = {
            "advertiserId": "123456",
            "name": html5_asset["name"],
            "destinationUrl": html5_asset["click_url"],
        }

        gam_creative = adapter._create_html5_creative(html5_asset, base_creative)

        print("✓ Created Html5Creative:")
        print(f"  └─ Type: {gam_creative['xsi_type']}")
        print(f"  └─ Size: {gam_creative['size']['width']}x{gam_creative['size']['height']}")
        print(f"  └─ HTML Source: {gam_creative['htmlAsset']['htmlSource'][:50]}...")
        print(f"  └─ Backup Image: {'✓' if 'backupImageAsset' in gam_creative else '✗'}")
        print(f"  └─ Interstitial: {gam_creative['isInterstitial']}")

    print("\n3. Testing HTML5 Creative Validation")
    print("-" * 40)

    # Test validation for HTML5 creatives
    valid_html5 = {
        "creative_id": "valid_html5",
        "name": "Valid HTML5",
        "media_url": "https://example.com/valid.html",
        "format": "display_300x250",
        "width": 300,
        "height": 250,
        "file_size": 1000000,  # 1MB - within 5MB limit
    }

    large_html5 = {
        "creative_id": "large_html5",
        "name": "Large HTML5 (No Size Limit)",
        "media_url": "https://example.com/large.zip",
        "format": "html5_interactive",
        "width": 970,
        "height": 250,
        "file_size": 10000000,  # 10MB - no client-side validation
    }

    oversized_dimensions_html5 = {
        "creative_id": "oversized_dimensions_html5",
        "name": "Oversized Dimensions HTML5",
        "media_url": "https://example.com/oversized.html",
        "format": "html5_interactive",
        "width": 2000,  # Exceeds 1800px limit
        "height": 250,
        "file_size": 1000000,  # 1MB - file size is fine
    }

    # Validate all assets
    for asset in [valid_html5, large_html5, oversized_dimensions_html5]:
        issues = adapter._validate_creative_for_gam(asset)
        status = "✓ PASS" if not issues else f"✗ FAIL ({len(issues)} issues)"
        print(f"{status}: {asset['name']}")
        for issue in issues:
            print(f"  └─ {issue}")

    print("\n4. End-to-End HTML5 Creative Processing")
    print("-" * 40)

    # Test the full creative processing pipeline
    from datetime import datetime

    demo_assets = [
        {
            "creative_id": "e2e_html5_1",
            "name": "E2E HTML5 Banner",
            "format": "display_728x90",
            "media_url": "https://example.com/banner.html",
            "click_url": "https://example.com/click",
            "package_assignments": ["demo_package"],
        },
        {
            "creative_id": "e2e_html5_2",
            "name": "E2E HTML5 ZIP",
            "format": "html5_interactive",
            "media_url": "https://example.com/interactive.zip",
            "click_url": "https://example.com/click",
            "backup_image_url": "https://example.com/backup.png",
            "package_assignments": ["demo_package"],
        },
    ]

    try:
        result = adapter.add_creative_assets("demo_media_buy", demo_assets, datetime.now())

        print(f"✓ Processed {len(result)} HTML5 creatives:")
        for asset_status in result:
            print(f"  └─ {asset_status.creative_id}: {asset_status.status}")

    except Exception as e:
        print(f"✗ Error processing creatives: {e}")

    print("\n" + "=" * 60)
    print("✅ HTML5 Creative Support Demo Complete!")
    print("\nSupported HTML5 features:")
    print("• HTML file uploads (.html, .htm, .html5)")
    print("• ZIP bundle uploads with multiple assets")
    print("• Backup image configuration")
    print("• Interstitial and size override settings")
    print("• Proper GAM Html5Creative generation")
    print("• Dimension validation (GAM API handles file size limits)")
    print("• Rich media format detection")


if __name__ == "__main__":
    main()
