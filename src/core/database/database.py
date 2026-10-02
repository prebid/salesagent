from datetime import UTC, datetime
from typing import cast

from sqlalchemy import func, select

from scripts.ops.migrate import run_migrations
from src.core.config import get_settings
from src.core.database.database_session import get_db_session
from src.core.database.models import (
    AdapterConfig,
    AuthorizedProperty,
    CurrencyLimit,
    Product,
    Tenant,
    TenantAuthConfig,
)
from src.core.database.repositories.principal import PrincipalRepository


def init_db(exit_on_error=False):
    """Initialize database with multi-tenant support.

    Args:
        exit_on_error: If True, exit process on migration error. If False, raise exception.
                      Default False for test compatibility.
    """
    provisioning = get_settings().provisioning
    if not provisioning.skip_migrations:
        # Run migrations first - this creates all tables
        print("Applying database migrations...")
        run_migrations(exit_on_error=exit_on_error)

    # A demo tenant is a fully configured tenant with the mock adapter; production
    # deployments start blank.
    create_demo_tenant = provisioning.create_demo_tenant

    # Check if we need to create a default tenant
    with get_db_session() as db_session:
        from sqlalchemy.exc import IntegrityError

        # Check if 'default' tenant already exists (safer than counting)
        stmt = select(Tenant).where(Tenant.tenant_id == "default")
        existing_tenant = db_session.scalars(stmt).first()

        if not existing_tenant:
            # See ensure_default_tenant: the deployment declares where it answers, once, and
            # the answer is stored. A production install declaring neither ADCP_AGENT_URL nor
            # SALES_AGENT_DOMAIN gets no default tenant rather than one holding a host that
            # looks configured and serves nothing.
            from src.core.agent_identity import deployment_virtual_host

            bootstrap_host = deployment_virtual_host()
            if bootstrap_host is None:
                print("⚠️  No default tenant created: set ADCP_AGENT_URL or SALES_AGENT_DOMAIN, then restart.")
                return

            if create_demo_tenant:
                # Demo mode: Create fully configured tenant with mock adapter
                new_tenant = Tenant(
                    tenant_id="default",
                    name="Demo Sales Agent",
                    subdomain="default",
                    virtual_host=bootstrap_host,
                    created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                    is_active=True,
                    billing_plan="standard",
                    ad_server="mock",
                    enable_axe_signals=True,
                    auto_approve_format_ids=[
                        "display_300x250",
                        "display_728x90",
                        "video_30s",
                    ],
                    human_review_required=False,
                    auth_setup_mode=False,  # Disable setup mode for demo (simulates SSO configured)
                )
            else:
                # Production mode: Create blank tenant requiring setup
                new_tenant = Tenant(
                    tenant_id="default",
                    name="My Sales Agent",
                    subdomain="default",
                    virtual_host=bootstrap_host,
                    created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                    is_active=True,
                    billing_plan="standard",
                    ad_server=None,  # No adapter - user must configure
                    enable_axe_signals=False,  # User should explicitly enable
                )

            db_session.add(new_tenant)

            try:
                db_session.flush()  # Try to write tenant first to catch duplicates
            except IntegrityError:  # structural-guard: integrity-narrowing - startup bootstrap of ONE known row; returns immediately, nothing else pending
                # Tenant was created by another process/thread - rollback and continue
                db_session.rollback()
                print("ℹ️  Default tenant already exists (created by concurrent process)")
                return  # Exit early since tenant exists

            if create_demo_tenant:
                # Demo mode: Add mock adapter config, test principal, currencies, etc.
                new_adapter = AdapterConfig(tenant_id="default", adapter_type="mock")
                db_session.add(new_adapter)

                # Create a CI test principal for E2E testing
                PrincipalRepository(db_session, "default").create_with_token(
                    # Fixed token for E2E tests; stored hashed like any other. A LITERAL
                    # COPY of scripts/setup/init_database_ci.py's CI_TEST_TOKEN, which
                    # owns the value: src/ cannot import from scripts/ (the dependency
                    # runs the other way), so this one is kept in step by hand. Every
                    # spelling under tests/ reads the constant.
                    "ci-test-token",
                    principal_id="ci-test-principal",
                    name="CI Test Principal",
                    platform_mappings={"mock": {"advertiser_id": "test-advertiser"}},
                )

                # Add currency limits for demo
                for currency in ["USD", "EUR", "GBP"]:
                    currency_limit = CurrencyLimit(
                        tenant_id="default",
                        currency_code=currency,
                        min_package_budget=0.0,
                        max_daily_package_spend=100000.0,
                    )
                    db_session.add(currency_limit)

                # Add authorized property for demo
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

                # Add SSO configuration for demo (simulates configured SSO)
                auth_config = TenantAuthConfig(
                    tenant_id="default",
                    oidc_enabled=True,
                    oidc_provider="google",
                    oidc_discovery_url="https://accounts.google.com/.well-known/openid-configuration",
                    oidc_client_id="demo-client-id",
                )
                db_session.add(auth_config)

            # Only create additional sample advertisers if this is a development environment
            if create_demo_tenant and provisioning.create_sample_data:
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
                    PrincipalRepository(db_session, "default").create_with_token(
                        p["access_token"],
                        principal_id=p["principal_id"],
                        name=p["name"],
                        platform_mappings=p["platform_mappings"],
                    )

            # Commit tenant, principals, and adapter config
            db_session.commit()

            # Print appropriate message based on mode
            if create_demo_tenant:
                print(
                    """
╔══════════════════════════════════════════════════════════════════╗
║              🎮 DEMO SALES AGENT INITIALIZED                     ║
╠══════════════════════════════════════════════════════════════════╣
║                                                                  ║
║  A demo tenant has been created with mock adapter:               ║
║                                                                  ║
║  🏢 Tenant: Demo Sales Agent                                     ║
║  🌐 Admin UI: http://localhost:8001/admin/                       ║
║  🔧 Adapter: Mock (for testing)                                  ║
║                                                                  ║
║  ✅ Pre-configured with:                                         ║
║     • Mock ad server adapter                                     ║
║     • USD/EUR/GBP currencies                                     ║
║     • Test principal (ci-test-token)                             ║
║     • Sample authorized property                                 ║
║     • SSO/OIDC configuration (demo mode)                         ║
║                                                                  ║
║  💡 Ready to test! No setup required.                            ║
║                                                                  ║
╚══════════════════════════════════════════════════════════════════╝
                """
                )
            else:
                print(
                    """
╔══════════════════════════════════════════════════════════════════╗
║              🚀 SALES AGENT INITIALIZED                          ║
╠══════════════════════════════════════════════════════════════════╣
║                                                                  ║
║  A blank tenant has been created for production setup:           ║
║                                                                  ║
║  🏢 Tenant: My Sales Agent                                       ║
║  🌐 Admin UI: http://localhost:8001/admin/                       ║
║                                                                  ║
║  ⚡ Next Steps (use Setup Checklist in Admin UI):                ║
║     1. Configure your ad server (GAM, Kevel, etc.)               ║
║     2. Set up currencies                                         ║
║     3. Create products                                           ║
║     4. Add advertisers (principals)                              ║
║     5. Configure access control                                  ║
║                                                                  ║
║  💡 For demo/testing, restart with CREATE_DEMO_TENANT=true       ║
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
        if provisioning.create_sample_data:
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
                                "agent_url": "https://creative.adcontextprotocol.org",
                                "id": "display_300x250",
                            }
                        ],
                        "targeting_template": {
                            "min_cpm": 5.0,
                            "max_frequency": 3,
                            "allow_adult_content": False,
                            "targeting": {"geo_countries": ["US", "CA"]},
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
                                "agent_url": "https://creative.adcontextprotocol.org",
                                "id": "display_728x90",
                            }
                        ],
                        "targeting_template": {
                            "targeting": {"geo_countries": ["US", "CA"]},
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
                        format_ids=p["formats"],
                        targeting_template=p["targeting_template"],
                        implementation_config=p.get("implementation_config"),
                        property_tags=p.get("property_tags"),
                        delivery_type="guaranteed" if is_fixed else "non_guaranteed",
                    )
                    db_session.add(new_product)
                    db_session.flush()

                    # Create pricing_option for this product (new system)
                    new_pricing_option = PricingOptionModel.create(
                        tenant_id="default",
                        product_id=cast("str", p["product_id"]),
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
