import json
import os
import secrets
from datetime import datetime

from sqlalchemy import func, select

from scripts.ops.migrate import run_migrations
from src.core.database.database_session import get_db_session
from src.core.database.models import AdapterConfig, AuthorizedProperty, CurrencyLimit, Principal, Product, Tenant


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

            # Add required setup checklist items (currency limits and authorized property)
            # These are required for the setup checklist validation
            for currency in ["USD", "EUR", "GBP"]:
                currency_limit = CurrencyLimit(
                    tenant_id="default",
                    currency_code=currency,
                    min_package_budget=0.0,
                    max_daily_package_spend=100000.0,
                )
                db_session.add(currency_limit)

            # Add authorized property for setup checklist
            authorized_property = AuthorizedProperty(
                tenant_id="default",
                property_id="default-property",
                property_type="website",
                name="Default Property",
                identifiers=[{"type": "domain", "value": "example.com"}],
                tags=["default"],
                publisher_domain="example.com",
                verification_status="verified",
            )
            db_session.add(authorized_property)

            # Only create additional sample advertisers if this is a development environment
            if os.environ.get("CREATE_SAMPLE_DATA", "false").lower() == "true":
                principals_data = [
                    {
                        "principal_id": "acme_corp",
                        "name": "Acme Corporation",
                        "platform_mappings": {
                            "mock": {"advertiser_id": "mock-acme"},
                        },
                        "access_token": "acme_corp_token",
                    },
                    {
                        "principal_id": "purina",
                        "name": "Purina Pet Foods",
                        "platform_mappings": {
                            "mock": {"advertiser_id": "mock-purina"},
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

            # Commit tenant, principals, and adapter config
            # Products will be created later (outside this block) if CREATE_SAMPLE_DATA is set
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

        # Create sample products if CREATE_SAMPLE_DATA is set and products don't exist
        # This runs regardless of whether tenant was just created or already existed
        if os.environ.get("CREATE_SAMPLE_DATA", "false").lower() == "true":
            # Check if products already exist
            stmt_products = select(func.count()).select_from(Product).where(Product.tenant_id == "default")
            existing_products_count = db_session.scalar(stmt_products)

            if existing_products_count == 0:
                print("Creating sample products for testing...")
                from src.core.database.models import PricingOption as PricingOptionModel

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
                                "width": 300,
                                "height": 250,
                            }
                        ],
                        "targeting_template": {
                            "min_cpm": 5.0,
                            "max_frequency": 3,
                            "allow_adult_content": False,
                            "targeting": {"geo_country_any_of": ["US", "CA"]},
                        },
                        "property_tags": ["all_inventory"],  # Required per AdCP spec
                        "pricing_option": {
                            "pricing_model": "cpm",
                            "currency": "USD",
                            "is_fixed": False,
                            "price_guidance": {"floor": 5.0, "p50": 8.0, "p75": 10.0},
                        },
                    },
                    {
                        "product_id": "prod_2",
                        "name": "Run of Site Display",
                        "description": "General display inventory across all properties",
                        "formats": [
                            {
                                "format_id": "display_728x90",
                                "name": "Leaderboard",
                                "type": "display",
                                "width": 728,
                                "height": 90,
                            }
                        ],
                        "targeting_template": {
                            "targeting": {"geo_country_any_of": ["US", "CA"]},
                        },
                        "property_tags": ["all_inventory"],  # Required per AdCP spec
                        "pricing_option": {
                            "pricing_model": "cpm",
                            "rate": 2.5,
                            "currency": "USD",
                            "is_fixed": True,
                        },
                    },
                ]

                for p in products_data:
                    # Extract pricing info to populate legacy fields (still required by schema)
                    pricing_opt_data = p["pricing_option"]
                    is_fixed = pricing_opt_data["is_fixed"]

                    new_product = Product(
                        tenant_id="default",
                        product_id=p["product_id"],
                        name=p["name"],
                        description=p["description"],
                        formats=p["formats"],
                        targeting_template=p["targeting_template"],
                        implementation_config=p.get("implementation_config"),
                        property_tags=p.get("property_tags"),
                        # Legacy pricing fields (still in schema, will be removed later)
                        is_fixed_price=is_fixed,
                        delivery_type="guaranteed" if is_fixed else "non_guaranteed",
                        currency=pricing_opt_data["currency"],
                        cpm=pricing_opt_data.get("rate"),
                        price_guidance=pricing_opt_data.get("price_guidance"),
                    )
                    db_session.add(new_product)
                    db_session.flush()

                    # Create pricing_option for this product (new system)
                    new_pricing_option = PricingOptionModel(
                        tenant_id="default",
                        product_id=p["product_id"],
                        pricing_model=pricing_opt_data["pricing_model"],
                        rate=pricing_opt_data.get("rate"),
                        currency=pricing_opt_data["currency"],
                        is_fixed=pricing_opt_data["is_fixed"],
                        price_guidance=pricing_opt_data.get("price_guidance"),
                    )
                    db_session.add(new_pricing_option)

                db_session.commit()
                print(f"✅ Created {len(products_data)} sample products")


if __name__ == "__main__":
    init_db(exit_on_error=True)
