"""Minimal database initialization for CI/CD testing."""

import os
import sys
from pathlib import Path

# Add the project root directory to Python path to ensure imports work
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))


def init_db_ci():
    """Initialize database with migrations only for CI testing."""
    try:
        # Import here to ensure path is set up first
        import uuid
        from datetime import UTC, datetime

        from sqlalchemy import select

        from scripts.ops.migrate import run_migrations
        from src.core.database.database_session import get_db_session
        from src.core.database.models import (
            AuthorizedProperty,
            CurrencyLimit,
            PricingOption,
            Principal,
            Product,
            PropertyTag,
            Tenant,
        )

        print("Applying database migrations for CI...")
        run_migrations()
        print("Database migrations applied successfully")

        # Create a default tenant for CI tests (handle race condition between multiple containers)
        print("Creating default tenant for CI...")
        with get_db_session() as session:
            # First, check if CI test tenant already exists
            # Note: In Docker Compose, both adcp-server and admin-ui may run this simultaneously
            stmt = select(Tenant).filter_by(subdomain="ci-test")
            existing_tenant = session.scalars(stmt).first()

            if existing_tenant:
                print(f"CI test tenant already exists (ID: {existing_tenant.tenant_id})")
                tenant_id = existing_tenant.tenant_id

                # Check if principal exists GLOBALLY by access_token (it's unique across all tenants)
                stmt_principal = select(Principal).filter_by(access_token="ci-test-token")
                existing_principal = session.scalars(stmt_principal).first()
                if not existing_principal:
                    # Create principal if it doesn't exist
                    principal_id = str(uuid.uuid4())
                    principal = Principal(
                        principal_id=principal_id,
                        tenant_id=tenant_id,
                        name="CI Test Principal",
                        access_token="ci-test-token",
                        platform_mappings={"mock": {"advertiser_id": "test-advertiser"}},
                    )
                    session.add(principal)
                    print(f"Created principal (ID: {principal_id}) for existing tenant")
                elif existing_principal.tenant_id != tenant_id:
                    # Principal exists but for different tenant - update it to point to new tenant
                    print(
                        f"⚠️  Warning: Principal with token 'ci-test-token' exists for different tenant ({existing_principal.tenant_id})"
                    )
                    print(f"   Updating principal to point to new tenant: {tenant_id}")
                    existing_principal.tenant_id = tenant_id
                    session.flush()
                    print("   ✓ Principal updated to new tenant")
                else:
                    print(f"Principal already exists (ID: {existing_principal.principal_id})")

                # Check if currency limit exists for this tenant
                stmt_currency = select(CurrencyLimit).filter_by(tenant_id=tenant_id, currency_code="USD")
                existing_currency = session.scalars(stmt_currency).first()
                if not existing_currency:
                    # Create currency limit if it doesn't exist
                    currency_limit = CurrencyLimit(
                        tenant_id=tenant_id,
                        currency_code="USD",
                        min_package_budget=1000.0,
                        max_daily_package_spend=10000.0,
                    )
                    session.add(currency_limit)
                    print("Created currency limit for existing tenant")

                # Check if property tag exists for this tenant
                stmt_tag = select(PropertyTag).filter_by(tenant_id=tenant_id, tag_id="all_inventory")
                existing_tag = session.scalars(stmt_tag).first()
                if not existing_tag:
                    # Create property tag if it doesn't exist
                    from datetime import UTC, datetime

                    now = datetime.now(UTC)
                    property_tag = PropertyTag(
                        tag_id="all_inventory",
                        tenant_id=tenant_id,
                        name="All Inventory",
                        description="Default tag for all inventory",
                        created_at=now,
                        updated_at=now,
                    )
                    session.add(property_tag)
                    print("Created property tag for existing tenant")

                session.commit()  # Commit before creating products to avoid autoflush
            else:
                tenant_id = str(uuid.uuid4())
                principal_id = str(uuid.uuid4())

                # CRITICAL: Create tenant FIRST in separate transaction to avoid rollback cascade
                # If we create tenant + principal together, UniqueViolation on principal rolls back BOTH
                now = datetime.now(UTC)
                tenant = Tenant(
                    tenant_id=tenant_id,
                    name="CI Test Tenant",
                    subdomain="ci-test",
                    billing_plan="test",
                    ad_server="mock",
                    enable_axe_signals=True,
                    is_active=True,  # Explicitly set for auth lookup
                    authorized_emails=None,  # SQL NULL (satisfies constraint)
                    authorized_domains=None,  # SQL NULL (satisfies constraint)
                    policy_settings=None,  # SQL NULL
                    signals_agent_config=None,  # SQL NULL
                    ai_policy=None,  # SQL NULL
                    auto_approve_formats=["display_300x250", "display_728x90"],
                    human_review_required=False,
                    created_at=now,
                    updated_at=now,
                )
                session.add(tenant)

                try:
                    session.commit()  # Commit tenant FIRST
                    print(f"Created tenant (ID: {tenant_id})")

                    # Verify tenant was created with is_active=True
                    stmt_verify = select(Tenant).filter_by(tenant_id=tenant_id)
                    created_tenant = session.scalars(stmt_verify).first()
                    if created_tenant:
                        print(f"   ✓ Tenant verified: is_active={created_tenant.is_active}")

                        # CRITICAL: Verify tenant is findable with EXACT auth query
                        stmt_auth_test = select(Tenant).filter_by(tenant_id=tenant_id, is_active=True)
                        auth_findable_tenant = session.scalars(stmt_auth_test).first()
                        if auth_findable_tenant:
                            print("   ✓ Tenant findable with auth query (is_active=True filter)")
                        else:
                            print("   ❌ ERROR: Tenant NOT findable with auth query! This will cause auth to fail!")
                    else:
                        print("   ⚠️  Warning: Could not verify tenant creation")
                except Exception as e:
                    # Handle race: another container created tenant already
                    session.rollback()
                    print(f"⚠️  Tenant already exists (race condition): {e}")
                    stmt_tenant = select(Tenant).filter_by(subdomain="ci-test")
                    existing_tenant = session.scalars(stmt_tenant).first()
                    if existing_tenant:
                        tenant_id = existing_tenant.tenant_id
                        print(f"   Using existing tenant (ID: {tenant_id})")
                    else:
                        raise ValueError("Failed to create or find CI test tenant")

                # Now create principal + dependencies in separate transaction
                # Query again for principal (may have been created by other container)
                stmt_principal = select(Principal).filter_by(access_token="ci-test-token")
                existing_principal = session.scalars(stmt_principal).first()

                if not existing_principal:
                    principal = Principal(
                        principal_id=principal_id,
                        tenant_id=tenant_id,
                        name="CI Test Principal",
                        access_token="ci-test-token",
                        platform_mappings={"mock": {"advertiser_id": "test-advertiser"}},
                    )
                    session.add(principal)

                    try:
                        session.commit()
                        print(f"Created principal (ID: {principal_id})")
                    except Exception as e:
                        session.rollback()
                        print(f"⚠️  Principal already exists (race condition): {e}")
                        # Re-query for principal created by other container
                        stmt_principal = select(Principal).filter_by(access_token="ci-test-token")
                        existing_principal = session.scalars(stmt_principal).first()
                        if existing_principal:
                            principal_id = existing_principal.principal_id
                            print(f"   Using existing principal (ID: {principal_id})")
                else:
                    principal_id = existing_principal.principal_id
                    if existing_principal.tenant_id != tenant_id:
                        # Principal exists but for different tenant - update it
                        print(
                            f"⚠️  Warning: Principal with token 'ci-test-token' exists for different tenant ({existing_principal.tenant_id})"
                        )
                        print(f"   Updating principal to point to new tenant: {tenant_id}")
                        existing_principal.tenant_id = tenant_id
                        session.commit()
                        print("   ✓ Principal updated to new tenant")
                    else:
                        print(f"Principal already exists (ID: {principal_id})")

                # Create currency limit and property tag with race condition handling
                stmt_currency = select(CurrencyLimit).filter_by(tenant_id=tenant_id, currency_code="USD")
                existing_currency = session.scalars(stmt_currency).first()
                if not existing_currency:
                    currency_limit = CurrencyLimit(
                        tenant_id=tenant_id,
                        currency_code="USD",
                        min_package_budget=1000.0,
                        max_daily_package_spend=10000.0,
                    )
                    session.add(currency_limit)

                stmt_tag = select(PropertyTag).filter_by(tenant_id=tenant_id, tag_id="all_inventory")
                existing_tag = session.scalars(stmt_tag).first()
                if not existing_tag:
                    property_tag = PropertyTag(
                        tag_id="all_inventory",
                        tenant_id=tenant_id,
                        name="All Inventory",
                        description="Default tag for all inventory",
                        created_at=now,
                        updated_at=now,
                    )
                    session.add(property_tag)

                try:
                    session.commit()
                    print("Created currency limit and property tag")
                except Exception as e:
                    session.rollback()
                    print(f"⚠️  Currency limit or property tag already exists (race condition): {e}")
                    # Re-query to ensure we have references
                    stmt_currency = select(CurrencyLimit).filter_by(tenant_id=tenant_id, currency_code="USD")
                    existing_currency = session.scalars(stmt_currency).first()
                    stmt_tag = select(PropertyTag).filter_by(tenant_id=tenant_id, tag_id="all_inventory")
                    existing_tag = session.scalars(stmt_tag).first()
                    print("   Using existing currency limit and property tag")

            # Validate prerequisites before creating products
            print("Validating prerequisites for product creation...")

            # 1. Check CurrencyLimit exists (required for media buy budget validation)
            stmt_currency = select(CurrencyLimit).filter_by(tenant_id=tenant_id, currency_code="USD")
            currency_limit = session.scalars(stmt_currency).first()
            if not currency_limit:
                raise ValueError(
                    f"Cannot create products: CurrencyLimit (USD) not found for tenant {tenant_id}. "
                    "Products require currency limits for budget validation."
                )
            print(
                f"  ✓ CurrencyLimit exists: USD (min={currency_limit.min_package_budget}, max={currency_limit.max_daily_package_spend})"
            )

            # 2. Check PropertyTag exists (required for property_tags array references)
            stmt_tag = select(PropertyTag).filter_by(tenant_id=tenant_id, tag_id="all_inventory")
            property_tag = session.scalars(stmt_tag).first()
            if not property_tag:
                raise ValueError(
                    f"Cannot create products: PropertyTag 'all_inventory' not found for tenant {tenant_id}. "
                    "Products require at least one property tag per AdCP spec."
                )
            print(f"  ✓ PropertyTag exists: {property_tag.name} (tag_id={property_tag.tag_id})")

            print("All prerequisites validated successfully")

            # Create default products for testing
            print(f"Creating default products for CI on tenant: {tenant_id}...")
            products_data = [
                {
                    "product_id": "prod_display_premium",
                    "name": "Premium Display Advertising",
                    "description": "High-impact display ads across premium content",
                    "formats": ["display_300x250", "display_728x90", "display_160x600"],
                    "targeting_template": {"geo": ["US"], "device_type": "any"},
                    "delivery_type": "guaranteed",
                    "is_fixed_price": True,
                    "cpm": 15.0,
                },
                {
                    "product_id": "prod_video_premium",
                    "name": "Premium Video Advertising",
                    "description": "Pre-roll video ads with guaranteed completion rates",
                    "formats": ["video_15s", "video_30s"],
                    "targeting_template": {"geo": ["US"], "device_type": "any"},
                    "delivery_type": "guaranteed",
                    "is_fixed_price": True,
                    "cpm": 25.0,
                },
            ]

            for p in products_data:
                # Check if product already exists
                stmt = select(Product).filter_by(tenant_id=tenant_id, product_id=p["product_id"])
                existing_product = session.scalars(stmt).first()

                if not existing_product:
                    product = Product(
                        tenant_id=tenant_id,
                        product_id=p["product_id"],
                        name=p["name"],
                        description=p["description"],
                        formats=p["formats"],
                        targeting_template=p["targeting_template"],
                        delivery_type=p["delivery_type"],
                        is_fixed_price=p["is_fixed_price"],
                        cpm=p.get("cpm"),
                        property_tags=["all_inventory"],  # Required per AdCP spec
                        # Explicitly set all JSONB fields to None (SQL NULL) to satisfy constraints
                        measurement=None,
                        creative_policy=None,
                        price_guidance=None,
                        countries=None,
                        implementation_config=None,
                        properties=None,  # Using property_tags instead
                    )
                    session.add(product)
                    print(f"  ✓ Created product: {p['name']} (property_tags=['all_inventory'])")

                    # Create corresponding pricing_option (required for pricing display)
                    pricing_option = PricingOption(
                        tenant_id=tenant_id,
                        product_id=p["product_id"],
                        pricing_model="cpm",
                        rate=p.get("cpm"),
                        currency="USD",
                        is_fixed=p["is_fixed_price"],
                        price_guidance=None,  # Not used for fixed price products
                    )
                    session.add(pricing_option)
                    print(f"  ✓ Created pricing_option for: {p['name']}")
                else:
                    print(f"  ℹ️  Product already exists: {p['name']}")

            session.commit()

            # Create authorized property for setup checklist completion
            print("\nCreating authorized property for setup checklist...")
            stmt_check_property = select(AuthorizedProperty).filter_by(tenant_id=tenant_id, property_id="example_com")
            existing_property = session.scalars(stmt_check_property).first()

            if not existing_property:
                authorized_prop = AuthorizedProperty(
                    tenant_id=tenant_id,
                    property_id="example_com",
                    property_type="website",
                    name="Example Website",
                    identifiers=[{"type": "domain", "value": "example.com"}],
                    publisher_domain="example.com",
                    verification_status="verified",
                )
                session.add(authorized_prop)
                session.commit()
                print("  ✓ Created authorized property: example.com")
            else:
                print("  ℹ️  Authorized property already exists: example.com")

            # Verify products were actually saved
            stmt_verify = select(Product).filter_by(tenant_id=tenant_id)
            saved_products = session.scalars(stmt_verify).all()
            print(f"\n✅ Verification: {len(saved_products)} products found in database for tenant {tenant_id}")
            for prod in saved_products:
                print(f"   - {prod.product_id}: {prod.name}, property_tags={prod.property_tags}")

            # Also verify pricing_options exist
            from src.core.database.models import PricingOption as PricingOptionModel

            stmt_pricing = select(PricingOptionModel).filter_by(tenant_id=tenant_id)
            saved_pricing = session.scalars(stmt_pricing).all()
            print(f"✅ Verification: {len(saved_pricing)} pricing_options found for tenant {tenant_id}")

            # CRITICAL: Fail if no products were created
            if len(saved_products) == 0:
                print("\n❌ CRITICAL ERROR: No products found in database after initialization!")
                print(f"   Expected {len(products_data)} products but found 0")
                print(f"   Tenant ID: {tenant_id}")
                sys.exit(1)

            # CRITICAL: Fail if products missing property authorization
            for prod in saved_products:
                if not prod.property_tags and not prod.properties:
                    print(f"\n❌ CRITICAL ERROR: Product {prod.product_id} missing property authorization!")
                    print(f"   property_tags: {prod.property_tags}")
                    print(f"   properties: {prod.properties}")
                    print("   Products must have either property_tags or properties per AdCP spec")
                    sys.exit(1)

            print("\n✅ Database initialization complete:")
            print(f"   - Tenant ID: {tenant_id}")
            print(f"   - Principal ID: {principal_id}")
            print(f"   - Products created: {len(products_data)}")
            print(f"   - Products verified: {len(saved_products)}")

        print("✅ Database initialized successfully with all validations passed")
    except ImportError as e:
        print(f"Import error: {e}")
        print(f"Python path: {sys.path}")
        print(f"Current directory: {os.getcwd()}")
        sys.exit(1)
    except Exception as e:
        print(f"Error during initialization: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    init_db_ci()
