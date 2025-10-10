import json
import os
import secrets
from datetime import datetime

from sqlalchemy import func, select

from scripts.ops.migrate import run_migrations
from src.core.database.database_session import get_db_session
from src.core.database.models import AdapterConfig, Principal, Product, Tenant


def init_db(exit_on_error=False):
    """Initialize database with multi-tenant support.

    Args:
        exit_on_error: If True, exit process on migration error. If False, raise exception.
                      Default False for test compatibility.
    """
    # Skip migrations if requested (for testing)
    if os.environ.get("SKIP_MIGRATIONS") != "true":
        # Run migrations first - this creates all tables
        print("Applying database migrations...")
        run_migrations(exit_on_error=exit_on_error)

    # Check if we need to create a default tenant
    with get_db_session() as db_session:
        from sqlalchemy.exc import IntegrityError

        # Check if 'default' tenant already exists (safer than counting)
        stmt = select(Tenant).where(Tenant.tenant_id == "default")
        existing_tenant = db_session.scalars(stmt).first()

        if not existing_tenant:
            # No tenants exist - create a default one for simple use case
            admin_token = secrets.token_urlsafe(32)

            # Create default tenant with proper columns (no config column after migration 007)
            # NOTE: max_daily_budget moved to currency_limits table
            new_tenant = Tenant(
                tenant_id="default",
                name="Default Publisher",
                subdomain="default",  # Proper subdomain routing
                created_at=datetime.now(),
                updated_at=datetime.now(),
                is_active=True,
                billing_plan="standard",
                ad_server="mock",
                enable_axe_signals=True,
                auto_approve_formats=json.dumps(
                    [
                        "display_300x250",
                        "display_728x90",
                        "video_30s",
                    ]
                ),
                human_review_required=False,
                admin_token=admin_token,
            )
            db_session.add(new_tenant)

            try:
                db_session.flush()  # Try to write tenant first to catch duplicates
            except IntegrityError:
                # Tenant was created by another process/thread - rollback and continue
                db_session.rollback()
                print("ℹ️  Default tenant already exists (created by concurrent process)")
                return  # Exit early since tenant exists

            # Create adapter_config for mock adapter
            new_adapter = AdapterConfig(tenant_id="default", adapter_type="mock", mock_dry_run=False)
            db_session.add(new_adapter)

            # Always create a CI test principal for E2E testing
            # This principal uses a fixed token that matches tests/e2e/conftest.py
            ci_test_principal = Principal(
                tenant_id="default",
                principal_id="ci-test-principal",
                name="CI Test Principal",
                platform_mappings=json.dumps({"mock": {"advertiser_id": "test-advertiser"}}),
                access_token="ci-test-token",  # Fixed token for E2E tests
            )
            db_session.add(ci_test_principal)

            # Only create additional sample advertisers if this is a development environment
            if os.environ.get("CREATE_SAMPLE_DATA", "false").lower() == "true":
                principals_data = [
                    {
                        "principal_id": "acme_corp",
                        "name": "Acme Corporation",
                        "platform_mappings": {
                            "gam_advertiser_id": 67890,
                            "kevel_advertiser_id": "acme-corporation",
                            "triton_advertiser_id": "ADV-ACM-002",
                            "mock_advertiser_id": "mock-acme",
                        },
                        "access_token": "acme_corp_token",
                    },
                    {
                        "principal_id": "purina",
                        "name": "Purina Pet Foods",
                        "platform_mappings": {
                            "gam_advertiser_id": 12345,
                            "kevel_advertiser_id": "purina-pet-foods",
                            "triton_advertiser_id": "ADV-PUR-001",
                            "mock_advertiser_id": "mock-purina",
                        },
                        "access_token": "purina_token",
                    },
                ]

                for p in principals_data:
                    new_principal = Principal(
                        tenant_id="default",
                        principal_id=p["principal_id"],
                        name=p["name"],
                        platform_mappings=json.dumps(p["platform_mappings"]),
                        access_token=p["access_token"],
                    )
                    db_session.add(new_principal)

            # Create sample products
            products_data = [
                {
                    "product_id": "prod_1",
                    "name": "Premium Display - News",
                    "description": "Premium news site display inventory",
                    "formats": [
                        {
                            "format_id": "display_300x250",
                            "name": "Medium Rectangle",
                            "type": "display",
                            "description": "Standard medium rectangle display ad",
                            "specs": {"width": 300, "height": 250},
                            "delivery_options": {"hosted": {}},
                        }
                    ],
                    "targeting_template": {
                        "content_cat_any_of": ["news", "politics"],
                        "geo_country_any_of": ["US"],
                    },
                    "delivery_type": "guaranteed",
                    "is_fixed_price": False,
                    "cpm": None,
                    "price_guidance": {"floor": 5.0, "p50": 8.0, "p75": 10.0},
                    "implementation_config": {
                        "placement_ids": ["news_300x250_atf", "news_300x250_btf"],
                        "ad_unit_path": "/1234/news/display",
                        "key_values": {"section": "news", "tier": "premium"},
                        "targeting": {
                            "content_cat_any_of": ["news", "politics"],
                            "geo_country_any_of": ["US"],
                        },
                    },
                },
                {
                    "product_id": "prod_2",
                    "name": "Run of Site Display",
                    "description": "Run of site display inventory",
                    "formats": [
                        {
                            "format_id": "display_728x90",
                            "name": "Leaderboard",
                            "type": "display",
                            "description": "Standard leaderboard display ad",
                            "specs": {"width": 728, "height": 90},
                            "delivery_options": {"hosted": {}},
                        }
                    ],
                    "targeting_template": {"geo_country_any_of": ["US", "CA"]},
                    "delivery_type": "non_guaranteed",
                    "is_fixed_price": True,
                    "cpm": 2.5,
                    "price_guidance": None,
                    "implementation_config": {
                        "placement_ids": ["ros_728x90_all"],
                        "ad_unit_path": "/1234/run_of_site/leaderboard",
                        "key_values": {"tier": "standard"},
                        "targeting": {"geo_country_any_of": ["US", "CA"]},
                    },
                },
            ]

            for p in products_data:
                new_product = Product(
                    tenant_id="default",
                    product_id=p["product_id"],
                    name=p["name"],
                    description=p["description"],
                    formats=p["formats"],  # No json.dumps needed for JSONB columns
                    targeting_template=p["targeting_template"],
                    delivery_type=p["delivery_type"],
                    is_fixed_price=p["is_fixed_price"],
                    cpm=p.get("cpm"),
                    price_guidance=p.get("price_guidance"),  # Direct dict assignment
                    implementation_config=p.get("implementation_config"),
                )
                db_session.add(new_product)

            # Commit all changes
            db_session.commit()

            # Update the print statement based on whether sample data was created
            if os.environ.get("CREATE_SAMPLE_DATA", "false").lower() == "true":
                print(
                    f"""
╔══════════════════════════════════════════════════════════════════╗
║                 🚀 ADCP SALES AGENT INITIALIZED                  ║
╠══════════════════════════════════════════════════════════════════╣
║                                                                  ║
║  A default tenant has been created for quick start:              ║
║                                                                  ║
║  🏢 Tenant: Default Publisher                                    ║
║  🌐 URL: http://default.localhost:8080                           ║
║                                                                  ║
║  🔑 Admin Token (x-adcp-auth header):                            ║
║     {admin_token}  ║
║                                                                  ║
║  👤 Sample Advertiser Tokens:                                    ║
║     • Acme Corp: acme_corp_token                                 ║
║     • Purina: purina_token                                       ║
║                                                                  ║
║  💡 To create additional tenants:                                ║
║     python scripts/setup/setup_tenant.py "Publisher Name"        ║
║                                                                  ║
║  📚 To use with a different tenant:                              ║
║     http://[subdomain].localhost:PORT                            ║
║                                                                  ║
╚══════════════════════════════════════════════════════════════════╝
                """
                )
            else:
                print(
                    f"""
╔══════════════════════════════════════════════════════════════════╗
║                 🚀 ADCP SALES AGENT INITIALIZED                  ║
╠══════════════════════════════════════════════════════════════════╣
║                                                                  ║
║  A default tenant has been created for quick start:              ║
║                                                                  ║
║  🏢 Tenant: Default Publisher                                    ║
║  🌐 Admin UI: http://localhost:8001/tenant/default/login         ║
║                                                                  ║
║  🔑 Admin Token (for legacy API access):                         ║
║     {admin_token}  ║
║                                                                  ║
║  ⚡ Next Steps:                                                  ║
║     1. Log in to the Admin UI                                    ║
║     2. Set up your ad server (Ad Server Setup tab)              ║
║     3. Create principals for your advertisers                    ║
║                                                                  ║
║  💡 To create additional tenants:                                ║
║     python scripts/setup/setup_tenant.py "Publisher Name"        ║
║                                                                  ║
╚══════════════════════════════════════════════════════════════════╝
                    """
                )
        else:
            # Count tenants for status message
            stmt_count = select(func.count()).select_from(Tenant)
            tenant_count = db_session.scalar(stmt_count)
            print(f"Database ready ({tenant_count} tenant(s) configured)")


if __name__ == "__main__":
    init_db(exit_on_error=True)
