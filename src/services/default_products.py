#!/usr/bin/env python3
"""Default product templates for new tenants.

This module provides standard advertising products that are automatically
created for new tenants to make onboarding easier.
"""

import json
from datetime import datetime
from typing import Any


def get_default_products() -> list[dict[str, Any]]:
    """Get a list of default products that should be created for new tenants."""

    return [
        {
            "product_id": "run_of_site_display",
            "name": "Run of Site - Display",
            "description": "Non-guaranteed display advertising across all available inventory. Supports standard IAB display formats.",
            "delivery_type": "non_guaranteed",
            "is_fixed_price": False,
            "cpm": None,
            "min_spend": 100.0,  # Use AdCP-compliant field
            "formats": [
                "display_300x250",  # Medium Rectangle
                "display_728x90",  # Leaderboard
                "display_320x50",  # Mobile Banner
                "display_300x600",  # Half Page Ad
            ],
            "measurement": {
                "type": "viewability_measurement",
                "attribution": "probabilistic",
                "reporting": "weekly_dashboard",
            },
            "creative_policy": {"co_branding": "optional", "landing_page": "any", "templates_available": False},
            "countries": None,  # All countries
            "targeting_template": {"device_targets": {"device_types": ["desktop", "mobile", "tablet"]}},
            "implementation_config": {"is_run_of_site": True, "priority": "low"},
        },
        {
            "product_id": "homepage_takeover",
            "name": "Homepage Takeover",
            "description": "Premium guaranteed placement on homepage with high viewability. Desktop and mobile optimized.",
            "delivery_type": "guaranteed",
            "is_fixed_price": True,
            "cpm": 25.0,
            "min_spend": 1000.0,
            "formats": [
                "display_970x250",  # Billboard
                "display_728x90",  # Leaderboard
                "display_320x50",  # Mobile Banner
            ],
            "measurement": {
                "type": "brand_lift",
                "attribution": "deterministic_purchase",
                "reporting": "real_time_api",
            },
            "creative_policy": {"co_branding": "required", "landing_page": "any", "templates_available": True},
            "countries": ["US", "CA", "GB"],
            "targeting_template": {
                "placement_targets": {"ad_unit_ids": ["homepage_top", "homepage_leaderboard"]},
                "device_targets": {"device_types": ["desktop", "mobile"]},
            },
            "implementation_config": {"is_premium": True, "priority": "high", "viewability_threshold": 70},
        },
        {
            "product_id": "mobile_interstitial",
            "name": "Mobile Interstitial",
            "description": "Full-screen mobile interstitial ads with frequency capping. High engagement rates.",
            "delivery_type": "guaranteed",
            "is_fixed_price": True,
            "cpm": 15.0,
            "min_spend": 500.0,
            "formats": [
                "display_320x480",  # Mobile Interstitial
                "display_300x250",  # Medium Rectangle
            ],
            "measurement": {"type": "engagement_rate", "attribution": "probabilistic", "reporting": "weekly_dashboard"},
            "creative_policy": {"co_branding": "optional", "landing_page": "any", "templates_available": False},
            "countries": None,
            "targeting_template": {
                "device_targets": {"device_types": ["mobile"]},
                "frequency_cap": {"impressions": 3, "time_unit": "day"},
            },
            "implementation_config": {"is_interstitial": True, "priority": "medium"},
        },
        {
            "product_id": "video_preroll",
            "name": "Video Pre-Roll",
            "description": "Standard pre-roll video advertising. VAST 4.0 compliant with viewability measurement.",
            "delivery_type": "non_guaranteed",
            "is_fixed_price": False,
            "cpm": None,
            "min_spend": 1000.0,
            "formats": [
                "video_vast",  # VAST Video
            ],
            "measurement": {
                "type": "video_completion_rate",
                "attribution": "probabilistic",
                "reporting": "real_time_api",
            },
            "creative_policy": {"co_branding": "optional", "landing_page": "any", "templates_available": False},
            "countries": None,
            "targeting_template": {"device_targets": {"device_types": ["desktop", "mobile", "tablet", "ctv"]}},
            "implementation_config": {
                "video_settings": {"skippable_after_seconds": 5, "max_duration_seconds": 30, "vast_version": "4.0"},
                "priority": "medium",
            },
        },
        {
            "product_id": "native_infeed",
            "name": "Native In-Feed",
            "description": "Native advertising that matches your site's look and feel. Appears within content feeds.",
            "delivery_type": "non_guaranteed",
            "is_fixed_price": False,
            "cpm": None,
            "min_spend": 200.0,
            "formats": [
                "native_infeed",  # Native In-Feed
            ],
            "measurement": {"type": "engagement_rate", "attribution": "probabilistic", "reporting": "weekly_dashboard"},
            "creative_policy": {
                "co_branding": "optional",
                "landing_page": "must_include_retailer",
                "templates_available": True,
            },
            "countries": None,
            "targeting_template": {"device_targets": {"device_types": ["desktop", "mobile", "tablet"]}},
            "implementation_config": {
                "native_settings": {
                    "template_type": "standard",
                    "required_assets": ["headline", "description", "image", "logo"],
                },
                "priority": "medium",
            },
        },
        {
            "product_id": "contextual_display",
            "name": "Contextual Display",
            "description": "Display advertising with contextual targeting based on page content. No cookies required.",
            "delivery_type": "non_guaranteed",
            "is_fixed_price": False,
            "cpm": None,
            "min_spend": 200.0,
            "formats": [
                "display_300x250",  # Medium Rectangle
                "display_728x90",  # Leaderboard
                "display_160x600",  # Wide Skyscraper
            ],
            "measurement": {
                "type": "viewability_measurement",
                "attribution": "probabilistic",
                "reporting": "weekly_dashboard",
            },
            "creative_policy": {"co_branding": "optional", "landing_page": "any", "templates_available": False},
            "countries": None,
            "targeting_template": {
                "device_targets": {"device_types": ["desktop", "mobile", "tablet"]},
                "contextual_targets": {"categories": ["auto", "finance", "health", "technology", "travel"]},
            },
            "implementation_config": {"privacy_compliant": True, "no_cookies": True, "priority": "medium"},
        },
    ]


def create_default_products_for_tenant(conn, tenant_id: str, industry: str = None) -> list[str]:
    """Create default products for a new tenant.

    Args:
        conn: Database connection
        tenant_id: The tenant to create products for

    Returns:
        List of created product IDs
    """
    created_products = []

    # Get products based on industry if specified
    products_to_create = get_industry_specific_products(industry) if industry else get_default_products()

    for product_template in products_to_create:
        try:
            # Check if product already exists
            cursor = conn.execute(
                "SELECT product_id FROM products WHERE tenant_id = ? AND product_id = ?",
                (tenant_id, product_template["product_id"]),
            )
            if cursor.fetchone():
                continue  # Skip if already exists

            # Insert the product
            conn.execute(
                """
                INSERT INTO products (
                    product_id, tenant_id, name, description,
                    creative_formats, delivery_type, cpm,
                    price_guidance_min, price_guidance_max,
                    countries, targeting_template, implementation_config,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    product_template["product_id"],
                    tenant_id,
                    product_template["name"],
                    product_template["description"],
                    json.dumps(product_template["formats"]),
                    product_template["delivery_type"],
                    product_template["cpm"],
                    (
                        product_template.get("price_guidance", {}).get("min")
                        if product_template.get("price_guidance")
                        else None
                    ),
                    (
                        product_template.get("price_guidance", {}).get("max")
                        if product_template.get("price_guidance")
                        else None
                    ),
                    json.dumps(product_template["countries"]) if product_template["countries"] else None,
                    json.dumps(product_template["targeting_template"]),
                    json.dumps(product_template["implementation_config"]),
                    datetime.now().isoformat(),
                    datetime.now().isoformat(),
                ),
            )

            created_products.append(product_template["product_id"])

        except Exception as e:
            print(f"Warning: Failed to create default product {product_template['product_id']}: {e}")
            continue

    conn.commit()
    return created_products


def get_industry_specific_products(industry: str) -> list[dict[str, Any]]:
    """Get product templates specific to an industry.

    Args:
        industry: Industry type (e.g., 'news', 'sports', 'entertainment', 'ecommerce')

    Returns:
        List of product templates for the industry
    """
    industry_products: dict[str, list[dict[str, Any]]] = {
        "news": [
            {
                "product_id": "breaking_news_alert",
                "name": "Breaking News Alert Sponsorship",
                "description": "Sponsor breaking news alerts with guaranteed placement at top of article pages.",
                "delivery_type": "guaranteed",
                "cpm": 30.0,
                "price_guidance": None,
                "formats": ["display_728x90", "display_300x250"],
                "countries": None,
                "targeting_template": {
                    "content_targets": {"categories": ["breaking_news"]},
                    "device_targets": {"device_types": ["desktop", "mobile"]},
                },
                "implementation_config": {},
            }
        ],
        "sports": [
            {
                "product_id": "game_day_takeover",
                "name": "Game Day Takeover",
                "description": "Own the site on game days with synchronized creative across all placements.",
                "delivery_type": "guaranteed",
                "cpm": 40.0,
                "price_guidance": None,
                "formats": ["display_970x250", "display_300x600", "video_vast"],
                "countries": None,
                "targeting_template": {
                    "date_targets": {"day_of_week": ["saturday", "sunday"]},
                    "content_targets": {"categories": ["live_games"]},
                },
                "implementation_config": {},
            }
        ],
        "entertainment": [
            {
                "product_id": "video_companion",
                "name": "Video Companion Display",
                "description": "Display ads that appear alongside video content for maximum engagement.",
                "delivery_type": "non_guaranteed",
                "cpm": None,
                "price_guidance": {"min": 5.0, "max": 20.0},
                "formats": ["display_300x250", "display_300x600"],
                "countries": None,
                "targeting_template": {
                    "placement_targets": {"placement_types": ["video_companion"]},
                    "content_targets": {"content_types": ["video"]},
                },
                "implementation_config": {},
            }
        ],
        "ecommerce": [
            {
                "product_id": "product_retargeting",
                "name": "Product Retargeting Display",
                "description": "Dynamic product ads for retargeting shoppers who viewed specific products.",
                "delivery_type": "non_guaranteed",
                "cpm": None,
                "price_guidance": {"min": 3.0, "max": 15.0},
                "formats": ["display_300x250", "display_728x90", "native_infeed"],
                "countries": None,
                "targeting_template": {
                    "audience_targets": {"segments": ["cart_abandoners", "product_viewers"]},
                    "frequency_cap": {"impressions": 10, "time_unit": "day"},
                },
                "implementation_config": {},
            }
        ],
    }

    # Return industry-specific products plus standard ones
    base_products = get_default_products()
    if industry in industry_products:
        return base_products + industry_products[industry]
    return base_products
