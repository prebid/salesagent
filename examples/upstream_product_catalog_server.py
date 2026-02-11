#!/usr/bin/env python3
"""
Example upstream MCP server for product catalog.

This demonstrates how a publisher (like Yahoo) could implement their own
intelligent product catalog server that the AdCP Sales Agent calls.
"""

import json
import os
from typing import Any

import google.generativeai as genai
from fastmcp import FastMCP

# Initialize MCP server
mcp = FastMCP(name="ProductCatalogAgent", description="Intelligent product catalog matching for advertising")

# Example product database (in production, this would be your actual inventory system)
PRODUCT_DATABASE = [
    {
        "product_id": "yahoo_sports_premium",
        "name": "Yahoo Sports - Premium Display",
        "description": "Premium display inventory on Yahoo Sports during live games",
        "formats": [
            {
                "format_id": "display_300x250",
                "name": "Medium Rectangle",
                "type": "display",
                "specs": {"width": 300, "height": 250},
            },
            {
                "format_id": "display_728x90",
                "name": "Leaderboard",
                "type": "display",
                "specs": {"width": 728, "height": 90},
            },
        ],
        "targeting_template": {
            "content_cat_any_of": ["sports", "basketball", "football"],
            "geo_countries": ["US", "CA"],
        },
        "delivery_type": "guaranteed",
        "is_fixed_price": False,
        "price_guidance": {"floor": 15.0, "p50": 25.0, "p75": 35.0},
        "availability": {"march_madness": True, "super_bowl": True, "world_series": True},
    },
    {
        "product_id": "yahoo_finance_targeted",
        "name": "Yahoo Finance - Targeted Display",
        "description": "Highly targeted display ads on Yahoo Finance",
        "formats": [
            {
                "format_id": "display_300x250",
                "name": "Medium Rectangle",
                "type": "display",
                "specs": {"width": 300, "height": 250},
            }
        ],
        "targeting_template": {
            "content_cat_any_of": ["finance", "business", "investing"],
            "geo_countries": ["US"],
        },
        "delivery_type": "guaranteed",
        "is_fixed_price": True,
        "cpm": 45.0,
        "audience_segments": ["investors", "high_net_worth", "business_decision_makers"],
    },
    {
        "product_id": "yahoo_news_video",
        "name": "Yahoo News - Premium Video",
        "description": "Pre-roll video ads on Yahoo News",
        "formats": [
            {
                "format_id": "video_16x9",
                "name": "HD Video",
                "type": "video",
                "specs": {"aspect_ratio": "16:9", "min_duration": 15, "max_duration": 30},
            }
        ],
        "targeting_template": {
            "content_cat_any_of": ["news", "politics", "world_news"],
            "geo_countries": ["US", "UK", "CA", "AU"],
        },
        "delivery_type": "non_guaranteed",
        "is_fixed_price": False,
        "price_guidance": {"floor": 20.0, "p50": 35.0, "p75": 50.0},
    },
    {
        "product_id": "yahoo_mail_native",
        "name": "Yahoo Mail - Native Ads",
        "description": "Native advertising within Yahoo Mail interface",
        "formats": [
            {
                "format_id": "native_feed",
                "name": "Native Feed Ad",
                "type": "native",
                "specs": {"title_length": 50, "description_length": 100},
            }
        ],
        "targeting_template": {"geo_countries": ["US", "CA", "UK"]},
        "delivery_type": "non_guaranteed",
        "is_fixed_price": True,
        "cpm": 8.0,
        "reach": "50M+ monthly active users",
    },
]


class ProductMatcher:
    """Intelligent product matching using AI."""

    def __init__(self):
        # Initialize Gemini if available
        self.ai_enabled = False
        api_key = os.environ.get("GEMINI_API_KEY")
        if api_key:
            genai.configure(api_key=api_key)
            self.model = genai.GenerativeModel("gemini-flash-latest")
            self.ai_enabled = True

    def analyze_brief(self, brief: str) -> dict[str, Any]:
        """Extract key requirements from the brief."""
        # Simple keyword extraction (in production, use NLP)
        brief_lower = brief.lower()

        analysis = {"topics": [], "timing": [], "formats": [], "audience": [], "budget_hints": []}

        # Topic detection
        if any(word in brief_lower for word in ["sport", "game", "march madness", "basketball"]):
            analysis["topics"].append("sports")
        if any(word in brief_lower for word in ["news", "current", "politics"]):
            analysis["topics"].append("news")
        if any(word in brief_lower for word in ["finance", "investor", "stock", "business"]):
            analysis["topics"].append("finance")

        # Timing detection
        if "march madness" in brief_lower:
            analysis["timing"].append("march_madness")
        if any(word in brief_lower for word in ["immediate", "urgent", "this week"]):
            analysis["timing"].append("immediate")

        # Format detection
        if any(word in brief_lower for word in ["video", "pre-roll", "sight and sound"]):
            analysis["formats"].append("video")
        if any(word in brief_lower for word in ["display", "banner"]):
            analysis["formats"].append("display")
        if "native" in brief_lower:
            analysis["formats"].append("native")

        # Audience detection
        if any(word in brief_lower for word in ["premium", "high-value", "affluent"]):
            analysis["audience"].append("premium")
        if any(word in brief_lower for word in ["mass", "broad", "awareness"]):
            analysis["audience"].append("mass_reach")

        return analysis

    async def match_products(self, brief: str, all_products: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Match products to the brief using AI or rules."""

        if self.ai_enabled:
            # Use AI for intelligent matching
            prompt = f"""Given this advertising brief: "{brief}"

And these available products:
{
                json.dumps(
                    [
                        {
                            "id": p["product_id"],
                            "name": p["name"],
                            "description": p["description"],
                            "formats": [f["type"] for f in p["formats"]],
                            "targeting": p.get("targeting_template", {}),
                            "price": p.get("cpm") or p.get("price_guidance", {}).get("p50", "variable"),
                            "special_features": p.get("availability", {}),
                        }
                        for p in all_products
                    ],
                    indent=2,
                )
            }

Select the most relevant products (up to 3) and return as JSON:
{{
  "selected_product_ids": ["id1", "id2"],
  "reasoning": {{
    "id1": "Why this product matches",
    "id2": "Why this product matches"
  }}
}}"""

            response = self.model.generate_content(
                prompt,
                generation_config=genai.types.GenerationConfig(temperature=0.3, response_mime_type="application/json"),
            )

            result = json.loads(response.text)
            selected_ids = result.get("selected_product_ids", [])

            # Filter products by AI selection
            return [p for p in all_products if p["product_id"] in selected_ids]

        else:
            # Fallback to rule-based matching
            analysis = self.analyze_brief(brief)
            scored_products = []

            for product in all_products:
                score = 0

                # Topic matching
                product_topics = product.get("targeting_template", {}).get("content_cat_any_of", [])
                for topic in analysis["topics"]:
                    if topic in product_topics:
                        score += 10

                # Format matching
                product_formats = [f["type"] for f in product.get("formats", [])]
                for format_type in analysis["formats"]:
                    if format_type in product_formats:
                        score += 5

                # Special timing matching
                if "march_madness" in analysis["timing"] and product.get("availability", {}).get("march_madness"):
                    score += 15

                # Audience matching
                if "premium" in analysis["audience"] and product.get("cpm", 0) > 20:
                    score += 5

                if score > 0:
                    scored_products.append((score, product))

            # Sort by score and return top products
            scored_products.sort(key=lambda x: x[0], reverse=True)
            return [p[1] for p in scored_products[:3]]


# Global matcher instance
matcher = ProductMatcher()


@mcp.tool
async def get_products(
    brief: str,
    tenant_id: str | None = None,
    principal_id: str | None = None,
    context: dict[str, Any] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """
    Get products that match the advertising brief.

    This is the tool that the AdCP Sales Agent will call.
    """
    print("\n📨 Received product request:")
    print(f"   Brief: {brief}")
    print(f"   Tenant: {tenant_id}")
    print(f"   Principal: {principal_id}")

    # Match products using our intelligent matcher
    matched_products = await matcher.match_products(brief, PRODUCT_DATABASE)

    print(f"   ✅ Matched {len(matched_products)} products")

    # Format products according to AdCP schema
    formatted_products = []
    for product in matched_products:
        # Convert to AdCP Product schema
        formatted = {
            "product_id": product["product_id"],
            "name": product["name"],
            "description": product["description"],
            "formats": product["formats"],
            "delivery_type": product["delivery_type"],
            "is_fixed_price": product["is_fixed_price"],
        }

        if product.get("cpm"):
            formatted["cpm"] = product["cpm"]
        if product.get("price_guidance"):
            formatted["price_guidance"] = product["price_guidance"]
        if product.get("implementation_config"):
            formatted["implementation_config"] = product["implementation_config"]

        formatted_products.append(formatted)

    return {"products": formatted_products}


@mcp.tool
async def get_product_availability(product_id: str, start_date: str, end_date: str) -> dict[str, Any]:
    """Check real-time availability for a specific product."""
    # In production, this would check actual inventory
    return {"product_id": product_id, "available": True, "estimated_impressions": 1000000, "fill_rate": 0.85}


if __name__ == "__main__":
    # Run the upstream server
    port = int(os.environ.get("UPSTREAM_PORT", "9000"))
    print(f"🚀 Starting Upstream Product Catalog Server on port {port}")
    print(f"📍 Endpoint: http://localhost:{port}/mcp/")
    print(f"🤖 AI Matching: {'Enabled' if matcher.ai_enabled else 'Disabled'}")
    print("\nExample products available:")
    for p in PRODUCT_DATABASE:
        print(f"  - {p['name']} ({p['product_id']})")

    mcp.run(transport="http", host="0.0.0.0", port=port)
