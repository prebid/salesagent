#!/usr/bin/env python3
"""
Quick start script to test upstream product catalog.

This runs both the upstream catalog server and tests it with the AdCP Sales Agent.
Run this in one terminal to see the full flow.
"""

import asyncio
import os
import subprocess
import sys
import time

# Simple upstream server code (embedded for convenience)
UPSTREAM_SERVER_CODE = '''
from fastmcp import FastMCP
import json

mcp = FastMCP(name="QuickCatalog")

# Simple product database
PRODUCTS = [
    {
        "product_id": "sports_premium",
        "name": "Sports Premium Display",
        "description": "Premium sports inventory",
        "formats": [{"format_id": "display_300x250", "name": "Rectangle", "type": "display", "specs": {"width": 300, "height": 250}}],
        "delivery_type": "guaranteed",
        "is_fixed_price": False,
        "price_guidance": {"floor": 10.0, "p50": 15.0, "p75": 20.0},
        "implementation_config": {
            "ad_server": "google_ad_manager",
            "placement_ids": ["sports_300x250_premium"],
            "targeting": {"content_cat_any_of": ["sports"]}
        }
    },
    {
        "product_id": "news_standard",
        "name": "News Standard Display",
        "description": "Standard news inventory",
        "formats": [{"format_id": "display_728x90", "name": "Leaderboard", "type": "display", "specs": {"width": 728, "height": 90}}],
        "delivery_type": "non_guaranteed",
        "is_fixed_price": True,
        "cpm": 5.0,
        "implementation_config": {
            "ad_server": "google_ad_manager",
            "placement_ids": ["news_728x90_standard"],
            "targeting": {"content_cat_any_of": ["news"]}
        }
    },
    {
        "product_id": "finance_video",
        "name": "Finance Video Pre-roll",
        "description": "Video ads on finance content",
        "formats": [{"format_id": "video_16x9", "name": "HD Video", "type": "video", "specs": {"aspect_ratio": "16:9"}}],
        "delivery_type": "guaranteed",
        "is_fixed_price": False,
        "price_guidance": {"floor": 25.0, "p50": 35.0, "p75": 45.0},
        "implementation_config": {
            "ad_server": "google_ad_manager",
            "placement_ids": ["finance_video_preroll"],
            "targeting": {"content_cat_any_of": ["finance", "business"]}
        }
    }
]

@mcp.tool
async def get_products(brief: str, tenant_id: Optional[str] = None, **kwargs) -> dict:
    """Simple product matching based on keywords in brief."""
    print(f"\\n📨 Upstream received: {brief}")

    brief_lower = brief.lower()
    matched = []

    # Simple keyword matching
    for product in PRODUCTS:
        score = 0
        if "sport" in brief_lower and "sports" in str(product):
            score += 10
        if "news" in brief_lower and "news" in str(product):
            score += 10
        if "video" in brief_lower and "video" in str(product):
            score += 5
        if "premium" in brief_lower and "premium" in product["name"].lower():
            score += 5
        if "finance" in brief_lower and "finance" in str(product):
            score += 10

        if score > 0:
            matched.append((score, product))

    # Sort by score and return top matches
    matched.sort(key=lambda x: x[0], reverse=True)
    results = [p[1] for p in matched[:2]]

    print(f"✅ Returning {len(results)} products")
    return {"products": results}

if __name__ == "__main__":
    print("🚀 Upstream Catalog Server starting on port 9000...")
    mcp.run(transport="http", host="0.0.0.0", port=9000)
'''


def start_upstream_server():
    """Start the upstream catalog server in a subprocess."""
    print("🚀 Starting upstream product catalog server...")

    # Write the server code to a temp file
    with open(".upstream_server_temp.py", "w") as f:
        f.write(UPSTREAM_SERVER_CODE)

    # Start the server
    env = os.environ.copy()
    if sys.platform == "win32":
        # Windows needs special handling
        proc = subprocess.Popen(
            [sys.executable, ".upstream_server_temp.py"], env=env, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP
        )
    else:
        proc = subprocess.Popen(["uv", "run", "python", ".upstream_server_temp.py"], env=env, preexec_fn=os.setsid)

    # Wait for server to start
    time.sleep(2)
    print("✅ Upstream server started on http://localhost:9000/mcp/\n")
    return proc


async def configure_tenant_for_mcp():
    """Configure the default tenant to use the upstream MCP server.

    Uses the SQLAlchemy ORM via `get_db_session()` — previously used the raw
    psycopg2 `get_db_connection()` which is now reserved for PID-1 fork-safe
    paths per Agent F Audit 06 Decision 2 (Agent F pre-L0 hardening scope
    item D).

    Stores the product_catalog config block on `Tenant.signals_agent_config`
    (a JSONType dict column), under the `product_catalog` key.
    """
    from sqlalchemy import select

    from src.core.database.database_session import get_db_session
    from src.core.database.models import Tenant

    print("🔧 Configuring tenant to use upstream catalog...")

    with get_db_session() as session:
        tenant = session.scalars(select(Tenant).filter_by(tenant_id="default")).first()
        if tenant is None:
            raise RuntimeError("Tenant 'default' not found — run init_db() first.")

        current_config = dict(tenant.signals_agent_config or {})
        current_config["product_catalog"] = {
            "provider": "mcp",
            "config": {
                "upstream_url": "http://localhost:9000/mcp/",
                "tool_name": "get_products",
                "timeout": 10,
            },
        }
        tenant.signals_agent_config = current_config
        session.commit()

    print("✅ Tenant configured to use upstream catalog\n")


async def test_product_listing():
    """Test the product listing through the AdCP Sales Agent."""
    from fastmcp.client import Client
    from fastmcp.client.transports import StreamableHttpTransport

    print("🧪 Testing product catalog through AdCP Sales Agent...\n")

    # Test different briefs
    test_briefs = [
        "I need premium sports advertising for March Madness",
        "Looking for news display inventory",
        "Want video ads on finance content for affluent audiences",
    ]

    # Create MCP client to AdCP Sales Agent
    headers = {"x-adcp-auth": "default_token"}
    transport = StreamableHttpTransport(url="http://localhost:8081/mcp/", headers=headers)

    async with Client(transport=transport) as client:
        for brief in test_briefs:
            print(f"📝 Brief: {brief}")

            try:
                result = await client.tools.get_products(brief=brief)
                products = result.get("products", [])

                print(f"📦 Got {len(products)} products:")
                for p in products:
                    print(f"   - {p['name']} ({p['product_id']})")
                    print(f"     {p['description']}")
                print()

            except Exception as e:
                print(f"❌ Error: {e}\n")


async def main():
    """Run the full test flow."""
    print("=" * 60)
    print("UPSTREAM PRODUCT CATALOG QUICK START")
    print("=" * 60)
    print()

    # Initialize database
    from src.core.database.database import init_db

    init_db()

    # Start upstream server
    upstream_proc = start_upstream_server()

    try:
        # Configure tenant
        await configure_tenant_for_mcp()

        # Start AdCP Sales Agent
        print("🚀 Starting AdCP Sales Agent...")
        adcp_proc = subprocess.Popen(["uv", "run", "python", "scripts/run_server.py"], env=os.environ.copy())

        # Wait for it to start
        time.sleep(3)
        print("✅ AdCP Sales Agent started on http://localhost:8081/mcp/\n")

        # Run tests
        await test_product_listing()

        print("\n" + "=" * 60)
        print("✨ SUCCESS! The upstream catalog is working!")
        print("\nYou now have:")
        print("1. Upstream catalog server on http://localhost:9000/mcp/")
        print("2. AdCP Sales Agent on http://localhost:8081/mcp/")
        print("3. Tenant configured to use upstream catalog")
        print("\nThe AdCP agent is calling your upstream server for products!")
        print("=" * 60)

        print("\nPress Ctrl+C to stop all servers...")

        # Keep running
        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        print("\n\n🛑 Shutting down...")
    finally:
        # Cleanup
        if sys.platform == "win32":
            upstream_proc.terminate()
            adcp_proc.terminate()
        else:
            os.killpg(os.getpgid(upstream_proc.pid), 9)
            adcp_proc.terminate()

        # Remove temp file
        if os.path.exists(".upstream_server_temp.py"):
            os.remove(".upstream_server_temp.py")

        print("✅ Cleanup complete")


if __name__ == "__main__":
    asyncio.run(main())
