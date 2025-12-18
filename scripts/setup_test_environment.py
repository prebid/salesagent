#!/usr/bin/env python3
"""
Setup script for test environment.
Creates test tenant, principals, and loads test data.
"""

import os
import sys
from pathlib import Path

# Add parent directories to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core.database.database_session import get_db_session
from src.core.database.models import Principal, Product, Tenant


def load_test_strategies():
    """Load predefined test strategies."""
    return {
        # Production strategies
        "conservative_pacing": {
            "name": "Conservative Pacing",
            "description": "Slow, steady delivery to ensure full flight completion",
            "config": {
                "pacing_rate": 0.8,
                "bid_adjustment": 0.9,
                "optimization_threshold": 0.15,
                "error_handling": "pause_and_alert",
            },
            "is_simulation": False,
        },
        "aggressive_scaling": {
            "name": "Aggressive Scaling",
            "description": "Fast delivery to maximize reach quickly",
            "config": {
                "pacing_rate": 1.3,
                "bid_adjustment": 1.2,
                "optimization_threshold": 0.25,
                "error_handling": "auto_recover",
            },
            "is_simulation": False,
        },
        # Simulation strategies
        "sim_happy_path": {
            "name": "Happy Path Simulation",
            "description": "Everything works perfectly simulation",
            "config": {
                "mode": "simulation",
                "time_progression": "accelerated",
                "scenario": "everything_works",
                "force_success": True,
            },
            "is_simulation": True,
        },
        "sim_creative_rejection": {
            "name": "Creative Rejection Simulation",
            "description": "Simulate creative policy violations",
            "config": {
                "mode": "simulation",
                "force_creative_rejection": True,
                "rejection_reason": "policy_violation",
                "rejection_stage": "review",
            },
            "is_simulation": True,
        },
        "sim_budget_exceeded": {
            "name": "Budget Exceeded Simulation",
            "description": "Simulate budget overspend scenarios",
            "config": {"mode": "simulation", "force_budget_exceeded": True, "overspend_percentage": 0.15},
            "is_simulation": True,
        },
    }


def load_test_products():
    """Load realistic test product catalog."""
    return [
        {
            "product_id": "test_guaranteed_homepage",
            "name": "Homepage Takeover - Guaranteed",
            "description": "Premium guaranteed placement on homepage",
            "delivery_type": "guaranteed",
            "formats": [
                {"agent_url": "https://creative.adcontextprotocol.org", "id": "display_970x250"},
                {"agent_url": "https://creative.adcontextprotocol.org", "id": "display_728x90"},
                {"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"},
            ],
            "pricing_model": "fixed_cpm",
            "cpm": 50.00,
            "min_spend": 10000,
            "countries": ["US", "CA"],
            "targeting_template": {
                "geo_country_any_of": ["US", "CA"],
                "device_type_any_of": ["desktop", "tablet"],
                "daypart_presets": ["prime_time"],
            },
            "implementation_config": {"mock_inventory_size": 100000, "mock_fill_rate": 0.95, "mock_viewability": 0.85},
        },
        {
            "product_id": "test_programmatic_video_sports",
            "name": "Sports Video - Programmatic",
            "description": "Video advertising within sports content",
            "delivery_type": "non_guaranteed",
            "formats": [
                {"agent_url": "https://creative.adcontextprotocol.org", "id": "video_16:9_1920x1080"},
                {"agent_url": "https://creative.adcontextprotocol.org", "id": "video_16:9_1280x720"},
            ],
            "pricing_model": "dynamic_cpm",
            "cpm_range": [15, 40],
            "countries": ["US"],
            "targeting_template": {
                "content_category_any_of": ["sports"],
                "media_type_any_of": ["video"],
                "device_type_any_of": ["mobile", "desktop", "ctv"],
            },
            "implementation_config": {"mock_inventory_size": 500000, "mock_fill_rate": 0.75, "mock_ctr": 0.025},
        },
        {
            "product_id": "test_audio_streaming",
            "name": "Audio Streaming - Targeted",
            "description": "Audio advertising in streaming content",
            "delivery_type": "non_guaranteed",
            "formats": [
                {"agent_url": "https://creative.adcontextprotocol.org", "id": "audio_companion_300x250"},
                {"agent_url": "https://creative.adcontextprotocol.org", "id": "audio_spot_30s"},
            ],
            "pricing_model": "dynamic_cpm",
            "cpm_range": [8, 20],
            "countries": ["US"],
            "targeting_template": {
                "media_type_any_of": ["audio"],
                "daypart_presets": ["drive_time", "work_day"],
                "audience_segment_any_of": ["music_streaming", "podcast_listeners"],
            },
            "implementation_config": {
                "mock_inventory_size": 200000,
                "mock_fill_rate": 0.80,
                "mock_completion_rate": 0.92,
            },
        },
        {
            "product_id": "test_ctv_premium",
            "name": "Premium CTV - Guaranteed",
            "description": "Premium Connected TV guaranteed inventory",
            "delivery_type": "guaranteed",
            "formats": [
                {"agent_url": "https://creative.adcontextprotocol.org", "id": "video_16:9_1920x1080"},
            ],
            "pricing_model": "fixed_cpm",
            "cpm": 65.00,
            "min_spend": 25000,
            "countries": ["US"],
            "targeting_template": {
                "device_type_any_of": ["ctv"],
                "content_rating_any_of": ["TV-G", "TV-PG", "TV-14"],
                "daypart_presets": ["prime_time", "late_night"],
            },
            "implementation_config": {"mock_inventory_size": 50000, "mock_fill_rate": 0.98, "mock_viewability": 0.95},
        },
        {
            "product_id": "test_mobile_interstitial",
            "name": "Mobile App Interstitials",
            "description": "Full-screen mobile app advertising",
            "delivery_type": "non_guaranteed",
            "formats": [
                {"agent_url": "https://creative.adcontextprotocol.org", "id": "display_320x480"},
                {"agent_url": "https://creative.adcontextprotocol.org", "id": "video_9:16_1080x1920"},
            ],
            "pricing_model": "dynamic_cpm",
            "cpm_range": [3, 12],
            "countries": ["US"],
            "targeting_template": {
                "device_type_any_of": ["mobile"],
                "app_categories": ["gaming", "social", "entertainment"],
                "os_any_of": ["iOS", "Android"],
            },
            "implementation_config": {"mock_inventory_size": 1000000, "mock_fill_rate": 0.65, "mock_ctr": 0.045},
        },
    ]


def setup_test_environment():
    """Set up complete test environment."""
    print("🧪 Setting up test environment...")

    with get_db_session() as session:
        # Create test tenant
        tenant_id = "test_tenant_1"
        tenant = session.query(Tenant).filter_by(tenant_id=tenant_id).first()

        if not tenant:
            print(f"📊 Creating test tenant: {tenant_id}")
            tenant = Tenant(tenant_id=tenant_id, name=os.getenv("TEST_TENANT_NAME", "Test Publisher"), subdomain="test")
            session.add(tenant)
            session.commit()
            print(f"✅ Created tenant: {tenant.name}")
        else:
            print(f"✅ Test tenant already exists: {tenant.name}")

        # Create test principal/advertiser
        principal_id = "test_advertiser_1"
        principal = session.query(Principal).filter_by(tenant_id=tenant_id, principal_id=principal_id).first()

        if not principal:
            print(f"👤 Creating test principal: {principal_id}")
            principal = Principal(
                tenant_id=tenant_id,
                principal_id=principal_id,
                name=os.getenv("TEST_PRINCIPAL_NAME", "Test Advertiser"),
                access_token=os.getenv("TEST_PRINCIPAL_TOKEN", "test_advertiser_456"),
                platform_mappings={"mock": {"advertiser_id": "mock_advertiser_123", "network_code": "test_network"}},
            )
            session.add(principal)
            session.commit()
            print(f"✅ Created principal: {principal.name}")
        else:
            print(f"✅ Test principal already exists: {principal.name}")

        # Load test products
        products = load_test_products()
        existing_product_ids = {p.product_id for p in session.query(Product).filter_by(tenant_id=tenant_id).all()}

        products_created = 0
        for product_data in products:
            if product_data["product_id"] not in existing_product_ids:
                print(f"📦 Creating product: {product_data['name']}")
                product = Product(
                    tenant_id=tenant_id,
                    product_id=product_data["product_id"],
                    name=product_data["name"],
                    description=product_data["description"],
                    format_ids=product_data["formats"],
                    countries=product_data.get("countries", ["US"]),
                    pricing_model=product_data["pricing_model"],
                    cpm=product_data.get("cpm"),
                    cpm_range=product_data.get("cpm_range"),
                    min_spend=product_data.get("min_spend"),
                    targeting_template=product_data["targeting_template"],
                    implementation_config=product_data.get("implementation_config", {}),
                )
                session.add(product)
                products_created += 1

        if products_created > 0:
            session.commit()
            print(f"✅ Created {products_created} test products")
        else:
            print("✅ Test products already exist")

        # Store strategy definitions (for reference)
        strategies = load_test_strategies()
        print(f"✅ Loaded {len(strategies)} strategy definitions")

    print("🎉 Test environment setup complete!")
    print(f"📍 Tenant ID: {tenant_id}")
    print(f"🔑 Admin Token: {os.getenv('TEST_ADMIN_TOKEN', 'test_admin_123')}")
    print(f"🔑 Principal Token: {os.getenv('TEST_PRINCIPAL_TOKEN', 'test_advertiser_456')}")
    print(f"🌐 MCP Server: http://localhost:{os.getenv('ADCP_SALES_PORT', 8080)}")
    print(f"🌐 A2A Server: http://localhost:{os.getenv('A2A_PORT', 8091)}")


if __name__ == "__main__":
    setup_test_environment()
