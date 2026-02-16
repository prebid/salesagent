#!/usr/bin/env python3
"""
Enhanced upstream MCP server showing run-of-site products with implementation details.

This demonstrates:
1. Standard run-of-site products for common formats
2. Using principal data for intelligent matching
3. Including implementation_config for ad server setup
"""

import os
from typing import Any

from fastmcp import FastMCP

# Initialize MCP server
mcp = FastMCP(name="EnhancedProductCatalog", description="Product catalog with implementation details")

# Standard run-of-site products that most publishers should offer
RUN_OF_SITE_PRODUCTS = [
    {
        "product_id": "ros_display_300x250",
        "name": "Run of Site - Medium Rectangle",
        "description": "Standard 300x250 display ads across all site content",
        "formats": [
            {
                "format_id": "display_300x250",
                "name": "Medium Rectangle",
                "type": "display",
                "specs": {"width": 300, "height": 250},
                "delivery_options": {"hosted": {}},
            }
        ],
        "targeting_template": {"geo_countries": ["US", "CA", "UK", "AU"]},
        "delivery_type": "non_guaranteed",
        "is_fixed_price": True,
        "cpm": 2.50,
        "implementation_config": {
            "ad_server": "google_ad_manager",
            "placement_ids": ["123456789", "123456790"],  # Multiple placements for ROS
            "ad_unit_path": "/network/run_of_site/display",
            "size_mapping": ["300x250", "320x250"],  # Include mobile size
            "frequency_caps": {"impressions": 10, "time_unit": "day", "time_count": 1},
        },
    },
    {
        "product_id": "ros_display_728x90",
        "name": "Run of Site - Leaderboard",
        "description": "Standard 728x90 leaderboard ads across all pages",
        "formats": [
            {
                "format_id": "display_728x90",
                "name": "Leaderboard",
                "type": "display",
                "specs": {"width": 728, "height": 90},
                "delivery_options": {"hosted": {}},
            }
        ],
        "targeting_template": {"geo_countries": ["US", "CA", "UK", "AU"]},
        "delivery_type": "non_guaranteed",
        "is_fixed_price": True,
        "cpm": 1.75,
        "implementation_config": {
            "ad_server": "google_ad_manager",
            "placement_ids": ["123456791"],
            "ad_unit_path": "/network/run_of_site/leaderboard",
            "size_mapping": ["728x90", "970x90", "320x50"],  # Desktop and mobile
            "position_targeting": "above_the_fold",
        },
    },
    {
        "product_id": "ros_display_300x600",
        "name": "Run of Site - Half Page",
        "description": "High-impact 300x600 display ads",
        "formats": [
            {
                "format_id": "display_300x600",
                "name": "Half Page",
                "type": "display",
                "specs": {"width": 300, "height": 600},
                "delivery_options": {"hosted": {}},
            }
        ],
        "targeting_template": {
            "geo_countries": ["US", "CA", "UK", "AU"],
            "device_type_any_of": ["desktop", "tablet"],  # Not great on mobile
        },
        "delivery_type": "non_guaranteed",
        "is_fixed_price": True,
        "cpm": 4.00,
        "implementation_config": {
            "ad_server": "google_ad_manager",
            "placement_ids": ["123456792"],
            "ad_unit_path": "/network/run_of_site/halfpage",
            "viewability_threshold": 50,  # High viewability placement
            "lazy_loading": True,
        },
    },
    {
        "product_id": "ros_video_preroll",
        "name": "Run of Site - Video Pre-roll",
        "description": "Standard pre-roll video ads on all video content",
        "formats": [
            {
                "format_id": "video_16x9",
                "name": "HD Video",
                "type": "video",
                "specs": {"aspect_ratio": "16:9", "min_duration": 15, "max_duration": 30, "skippable_after": 5},
                "delivery_options": {"hosted": {}},
            }
        ],
        "targeting_template": {"geo_countries": ["US", "CA", "UK", "AU"]},
        "delivery_type": "non_guaranteed",
        "is_fixed_price": False,
        "price_guidance": {"floor": 10.0, "p50": 15.0, "p75": 20.0},
        "implementation_config": {
            "ad_server": "google_ad_manager",
            "placement_ids": ["123456793"],
            "ad_unit_path": "/network/video/preroll",
            "player_size": ["640x360", "1280x720"],
            "max_pod_length": 60,  # seconds
            "companion_sizes": ["300x250", "728x90"],
        },
    },
]

# Premium products for specific content sections
PREMIUM_PRODUCTS = [
    {
        "product_id": "premium_sports_display",
        "name": "Sports Section - Premium Display",
        "description": "Premium display inventory on sports content with engaged audience",
        "formats": [
            {
                "format_id": "display_300x250",
                "name": "Medium Rectangle",
                "type": "display",
                "specs": {"width": 300, "height": 250},
                "delivery_options": {"hosted": {}},
            }
        ],
        "targeting_template": {"content_cat_any_of": ["sports"], "geo_countries": ["US"]},
        "delivery_type": "guaranteed",
        "is_fixed_price": False,
        "price_guidance": {"floor": 8.0, "p50": 12.0, "p75": 15.0},
        "implementation_config": {
            "ad_server": "google_ad_manager",
            "placement_ids": ["223456789"],  # Sports-specific placements
            "ad_unit_path": "/network/sports/display",
            "custom_targeting": {"section": "sports", "content_rating": "premium"},
            "competitive_exclusions": ["competing_sports_brands"],
            "roadblocking": True,  # All ads on page from same advertiser
        },
    }
]


@mcp.tool
async def get_products(
    brief: str,
    tenant_id: str = None,
    principal_id: str = None,
    principal_data: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """
    Get products matching the brief, with implementation details.

    Uses principal_data to:
    1. Verify advertiser has account on our ad server
    2. Apply advertiser-specific pricing
    3. Customize based on advertiser history/tier
    """
    print("\n📨 Enhanced Catalog Request:")
    print(f"   Brief: {brief}")
    print(f"   Principal: {principal_id}")
    if principal_data:
        print(f"   Ad Server IDs: {principal_data.get('platform_mappings', {})}")

    # Start with run-of-site products
    products = RUN_OF_SITE_PRODUCTS.copy()

    # Add premium products based on brief
    brief_lower = brief.lower()
    if any(word in brief_lower for word in ["sport", "game", "athletic"]):
        products.extend(PREMIUM_PRODUCTS)

    # All our products work on Google Ad Manager (our ad server)
    # Check if advertiser has a GAM account to work with us
    if principal_data and "platform_mappings" in principal_data:
        platform_mappings = principal_data["platform_mappings"]
        gam_config = platform_mappings.get("google_ad_manager", {})
        if not gam_config or not gam_config.get("advertiser_id"):
            # Advertiser doesn't have a GAM account
            print(f"   ⚠️  Note: Advertiser {principal_id} needs a GAM account")
            # Could return limited products or require account setup
            # For now, we'll still show products but flag this

    # Apply intelligent matching based on brief
    matched_products = []
    for product in products:
        score = calculate_relevance_score(product, brief)

        # Special handling for specific advertisers
        if principal_data:
            # Example: Give discounts to high-value advertisers
            if principal_id in ["premium_advertiser_001", "vip_brand"]:
                if product.get("cpm"):
                    product["cpm"] = product["cpm"] * 0.8  # 20% discount
                elif product.get("price_guidance"):
                    product["price_guidance"]["floor"] *= 0.8
                    product["price_guidance"]["p50"] *= 0.8
                    product["price_guidance"]["p75"] *= 0.8

        if score > 0:
            matched_products.append((score, product))

    # Sort by relevance and return top products
    matched_products.sort(key=lambda x: x[0], reverse=True)
    final_products = []

    # Remove targeting_template from products before returning
    for _score, product in matched_products[:5]:
        product_copy = product.copy()
        product_copy.pop("targeting_template", None)
        final_products.append(product_copy)

    print(f"   ✅ Returning {len(final_products)} products with implementation details")

    return {"products": final_products}


def calculate_relevance_score(product: dict[str, Any], brief: str) -> int:
    """Calculate how relevant a product is to the brief."""
    score = 0
    brief_lower = brief.lower()

    # Format matching
    if "video" in brief_lower and product.get("formats", [{}])[0].get("type") == "video":
        score += 10
    if "display" in brief_lower and product.get("formats", [{}])[0].get("type") == "display":
        score += 5

    # Size preferences
    if "high impact" in brief_lower and "300x600" in product.get("name", ""):
        score += 8
    if "standard" in brief_lower and any(size in product.get("name", "") for size in ["300x250", "728x90"]):
        score += 5

    # Budget hints
    if any(word in brief_lower for word in ["budget", "cost-effective", "efficient"]):
        if product.get("is_fixed_price") and product.get("cpm", 100) < 5:
            score += 10

    if any(word in brief_lower for word in ["premium", "high-quality", "engaged"]):
        if product.get("delivery_type") == "guaranteed":
            score += 8

    # Content matching
    product_desc = (product.get("name", "") + " " + product.get("description", "")).lower()
    content_keywords = ["sports", "news", "finance", "entertainment", "lifestyle"]
    for keyword in content_keywords:
        if keyword in brief_lower and keyword in product_desc:
            score += 15

    # Run of site is always somewhat relevant
    if "run of site" in product.get("name", "").lower():
        score += 2  # Base relevance

    return score


@mcp.tool
async def validate_implementation(product_id: str, principal_data: dict[str, Any]) -> dict[str, Any]:
    """
    Validate that a product can be implemented for a specific advertiser.
    Checks if advertiser has account on our ad server.
    """
    # Find the product
    all_products = RUN_OF_SITE_PRODUCTS + PREMIUM_PRODUCTS
    product = next((p for p in all_products if p["product_id"] == product_id), None)

    if not product:
        return {"valid": False, "reason": "Product not found"}

    # All our products use Google Ad Manager (our ad server)
    platform_mappings = principal_data.get("platform_mappings", {})
    gam_config = platform_mappings.get("google_ad_manager", {})

    # Check if advertiser has a GAM account
    if not gam_config or not gam_config.get("advertiser_id"):
        return {"valid": False, "reason": "Advertiser needs a Google Ad Manager account to buy from us"}

    return {
        "valid": True,
        "implementation_ready": True,
        "ad_server": "google_ad_manager",
        "advertiser_id": gam_config.get("advertiser_id"),
    }


if __name__ == "__main__":
    port = int(os.environ.get("UPSTREAM_PORT", "9000"))
    print(f"🚀 Starting Enhanced Product Catalog Server on port {port}")
    print(f"📍 Endpoint: http://localhost:{port}/mcp/")
    print("\n📦 Available Products:")
    print(f"  Run of Site ({len(RUN_OF_SITE_PRODUCTS)} products):")
    for p in RUN_OF_SITE_PRODUCTS:
        print(f"    - {p['name']} (CPM: ${p.get('cpm', 'variable')})")
    print(f"  Premium ({len(PREMIUM_PRODUCTS)} products):")
    for p in PREMIUM_PRODUCTS:
        print(f"    - {p['name']} (Type: {p['delivery_type']})")

    mcp.run(transport="http", host="0.0.0.0", port=port)
