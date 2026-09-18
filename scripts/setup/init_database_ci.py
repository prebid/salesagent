"""Minimal database initialization for CI/CD testing."""

import os
import sys
from pathlib import Path

# Add the project root directory to Python path to ensure imports work
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))


#: Referenced by tests/e2e/adcp_request_builder.py's CI_TEST_ACCOUNT. Both must agree;
#: the E2E builders send this id and this script is what makes it resolvable.
CI_TEST_ACCOUNT_ID = "ci-test-account"

#: The subdomain the CI tenant is reachable at, and the value a caller puts in
#: ``x-adcp-tenant`` to address it. Owned HERE, and imported by every test that names it
#: (tests/e2e/utils.py, tests/integration/conftest_ci_seed.py,
#: tests/storyboard/test_storyboard_conformance.py), because this script is what makes it
#: true in the database: the tenant_id is a fresh uuid4 per seed, so the subdomain is the
#: only stable spelling of "the CI tenant" and a second literal of it is a silent 401 the
#: day one of them changes. The dependency runs tests -> scripts only; a script cannot
#: import from tests/ (see scripts/ci/migration_helpers.py).
CI_TEST_SUBDOMAIN = "ci-test"

#: The credential presented to that tenant: the plaintext token this script hashes into
#: the CI principal's row. Owned here for the same reason as the subdomain above -- this
#: script is what makes it resolvable -- and read by tests/integration/conftest_ci_seed.py
#: and tests/storyboard/test_storyboard_conformance.py. ``tox.ini``'s
#: ``STORYBOARD_AUTH_TOKEN`` default cannot import a Python constant and carries a literal
#: that must be kept in step with this one; its comment says so.
CI_TEST_TOKEN = "ci-test-token"


def init_db_ci():
    """Initialize database with migrations only for CI testing."""
    try:
        # Import here to ensure path is set up first
        import uuid
        from datetime import UTC, datetime

        from adcp.types import BrandReference
        from sqlalchemy import select

        from scripts.ops.migrate import run_migrations
        from src.core.credentials import hash_token
        from src.core.database.database_session import get_db_session
        from src.core.database.models import (
            Account,
            AgentAccountAccess,
            AuthorizedProperty,
            CurrencyLimit,
            GAMInventory,
            PricingOption,
            Product,
            PropertyTag,
            Tenant,
            TenantAuthConfig,
        )
        from src.core.database.repositories.principal import PrincipalRepository
        from src.core.database.repositories.principal_lookup import find_principal_by_token_hash

        print("Applying database migrations for CI...")
        run_migrations()
        print("Database migrations applied successfully")

        # Create a default tenant for CI tests (handle race condition between multiple containers)
        print("Creating default tenant for CI...")
        with get_db_session() as session:
            # First, check if CI test tenant already exists
            # Note: In Docker Compose, both adcp-server and admin-ui may run this simultaneously
            stmt = select(Tenant).filter_by(subdomain=CI_TEST_SUBDOMAIN)
            existing_tenant = session.scalars(stmt).first()

            if existing_tenant:
                print(f"CI test tenant already exists (ID: {existing_tenant.tenant_id})")
                tenant_id = existing_tenant.tenant_id

                # Backfill access control if missing (required by setup checklist)
                needs_access_control = not existing_tenant.authorized_emails and not existing_tenant.authorized_domains
                if needs_access_control:
                    print("Backfilling access control for existing tenant...")
                    existing_tenant.authorized_emails = ["ci-test@example.com"]
                    session.flush()
                    print("   ✓ Access control configured")

                # Check if principal exists GLOBALLY by token hash (it's unique across all tenants)
                existing_principal = find_principal_by_token_hash(session, hash_token(CI_TEST_TOKEN))
                if not existing_principal:
                    # Create principal if it doesn't exist
                    principal_id = str(uuid.uuid4())
                    PrincipalRepository(session, tenant_id).create_with_token(
                        CI_TEST_TOKEN,
                        principal_id=principal_id,
                        name="CI Test Principal",
                        platform_mappings={"mock": {"advertiser_id": "test-advertiser"}},
                    )
                    print(f"Created principal (ID: {principal_id}) for existing tenant")
                elif existing_principal.tenant_id != tenant_id:
                    principal_id = existing_principal.principal_id
                    # Principal exists but for different tenant - update it to point to new tenant
                    print(
                        f"⚠️  Warning: Principal with token '{CI_TEST_TOKEN}' exists for different tenant ({existing_principal.tenant_id})"
                    )
                    print(f"   Updating principal to point to new tenant: {tenant_id}")
                    existing_principal.tenant_id = tenant_id
                    session.flush()
                    print("   ✓ Principal updated to new tenant")
                else:
                    principal_id = existing_principal.principal_id
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
                    subdomain=CI_TEST_SUBDOMAIN,
                    billing_plan="test",
                    ad_server="mock",
                    enable_axe_signals=True,
                    is_active=True,  # Explicitly set for auth lookup
                    authorized_emails=["ci-test@example.com"],  # Required by setup checklist
                    authorized_domains=None,  # Optional when emails are set
                    policy_settings=None,  # SQL NULL
                    signals_agent_config=None,  # SQL NULL
                    ai_policy=None,  # SQL NULL
                    auto_approve_format_ids=["display_300x250", "display_728x90"],
                    human_review_required=False,
                    auth_setup_mode=False,  # Disable setup mode for CI (simulates SSO configured)
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

                        # Create SSO config (simulates configured SSO)
                        stmt_auth_config = select(TenantAuthConfig).filter_by(tenant_id=tenant_id)
                        existing_auth_config = session.scalars(stmt_auth_config).first()
                        if not existing_auth_config:
                            auth_config = TenantAuthConfig(
                                tenant_id=tenant_id,
                                oidc_enabled=True,
                                oidc_provider="google",
                                oidc_discovery_url="https://accounts.google.com/.well-known/openid-configuration",
                                oidc_client_id="ci-test-client-id",
                            )
                            session.add(auth_config)
                            session.commit()
                            print("   ✓ SSO configuration created")
                        else:
                            print("   ✓ SSO configuration already exists")
                    else:
                        print("   ⚠️  Warning: Could not verify tenant creation")
                except Exception as e:
                    # Handle race: another container created tenant already
                    session.rollback()
                    print(f"⚠️  Tenant already exists (race condition): {e}")
                    stmt_tenant = select(Tenant).filter_by(subdomain=CI_TEST_SUBDOMAIN)
                    existing_tenant = session.scalars(stmt_tenant).first()
                    if existing_tenant:
                        tenant_id = existing_tenant.tenant_id
                        print(f"   Using existing tenant (ID: {tenant_id})")
                    else:
                        raise ValueError("Failed to create or find CI test tenant")

                # Now create principal + dependencies in separate transaction
                # Query again for principal (may have been created by other container)
                existing_principal = find_principal_by_token_hash(session, hash_token(CI_TEST_TOKEN))

                if not existing_principal:
                    PrincipalRepository(session, tenant_id).create_with_token(
                        CI_TEST_TOKEN,
                        principal_id=principal_id,
                        name="CI Test Principal",
                        platform_mappings={"mock": {"advertiser_id": "test-advertiser"}},
                    )

                    try:
                        session.commit()
                        print(f"Created principal (ID: {principal_id})")
                    except Exception as e:
                        session.rollback()
                        print(f"⚠️  Principal already exists (race condition): {e}")
                        # Re-query for principal created by other container
                        existing_principal = find_principal_by_token_hash(session, hash_token(CI_TEST_TOKEN))
                        if existing_principal:
                            principal_id = existing_principal.principal_id
                            print(f"   Using existing principal (ID: {principal_id})")
                else:
                    principal_id = existing_principal.principal_id
                    if existing_principal.tenant_id != tenant_id:
                        # Principal exists but for different tenant - update it
                        print(
                            f"⚠️  Warning: Principal with token '{CI_TEST_TOKEN}' exists for different tenant ({existing_principal.tenant_id})"
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

            # Account for the CI principal — placed HERE, after both the existing-tenant
            # and new-tenant branches, because this is where tenant_id and principal_id are
            # finally settled. `account` is in /required on sync-creatives-request.json,
            # create-media-buy-request.json and update-media-buy-request.json, so without
            # this row every E2E call to those tools is refused with INVALID_REQUEST before
            # reaching the behavior under test.
            #
            # It belongs to the CI tenant, NOT to "default": this script MOVES the
            # ci-test-token principal into a fresh tenant when it finds it elsewhere, so a
            # grant pinned to "default" would block that very move with a
            # ForeignKeyViolation and take down initialization.
            stmt_account = select(Account).filter_by(tenant_id=tenant_id, account_id=CI_TEST_ACCOUNT_ID)
            if not session.scalars(stmt_account).first():
                session.add(
                    Account(
                        tenant_id=tenant_id,
                        account_id=CI_TEST_ACCOUNT_ID,
                        name="CI Test Account",
                        status="active",
                        operator="testbrand.example",
                        brand=BrandReference(domain="testbrand.example"),
                    )
                )
                # Both parents must be ON the database before the association row: its FKs
                # are composite and AgentAccountAccess declares no ORM relationship to
                # either, so the unit of work has nothing to order the INSERTs by.
                session.flush()

            # Resolution is gated on the GRANT, not on the account row alone —
            # _require_account_access rejects a principal with no grant, so the account
            # without this would resolve to AUTHORIZATION_ERROR rather than succeed.
            stmt_grant = select(AgentAccountAccess).filter_by(
                tenant_id=tenant_id, principal_id=principal_id, account_id=CI_TEST_ACCOUNT_ID
            )
            if not session.scalars(stmt_grant).first():
                session.add(
                    AgentAccountAccess(tenant_id=tenant_id, principal_id=principal_id, account_id=CI_TEST_ACCOUNT_ID)
                )
            session.commit()
            print(f"  ✓ CI account ready: {CI_TEST_ACCOUNT_ID} (granted to {principal_id})")

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

            # Create default products for testing.
            # KEEP IN SYNC: tests/e2e/test_tenant_isolation.py CI_TEST_PRODUCT_IDS
            # asserts the ci-test tenant sees EXACTLY this product-id set. If you
            # add/remove/rename a ci-test product here, update that set too.
            print(f"Creating default products for CI on tenant: {tenant_id}...")
            products_data = [
                {
                    "product_id": "prod_display_premium",
                    "name": "Premium Display Advertising",
                    "description": "High-impact display ads across premium content",
                    "formats": [
                        {"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"},
                        {"agent_url": "https://creative.adcontextprotocol.org", "id": "display_728x90"},
                        {"agent_url": "https://creative.adcontextprotocol.org", "id": "display_160x600"},
                    ],
                    "targeting_template": {"geo": ["US"], "device_type": "any"},
                    "delivery_type": "guaranteed",
                    "pricing": {"model": "cpm", "rate": 15.0, "is_fixed": True},
                },
                {
                    "product_id": "prod_video_premium",
                    "name": "Premium Video Advertising",
                    "description": "Pre-roll video ads with guaranteed completion rates",
                    "formats": [
                        {"agent_url": "https://creative.adcontextprotocol.org", "id": "video_15s"},
                        {"agent_url": "https://creative.adcontextprotocol.org", "id": "video_30s"},
                    ],
                    "targeting_template": {"geo": ["US"], "device_type": "any"},
                    "delivery_type": "guaranteed",
                    "pricing": {"model": "cpm", "rate": 25.0, "is_fixed": True},
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
                        format_ids=p["formats"],
                        targeting_template=p["targeting_template"],
                        delivery_type=p["delivery_type"],
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
                    pricing = p["pricing"]
                    pricing_option = PricingOption.create(
                        tenant_id=tenant_id,
                        product_id=p["product_id"],
                        pricing_model=pricing["model"],
                        rate=pricing["rate"],
                        currency="USD",
                        is_fixed=pricing["is_fixed"],
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

            # Create GAM inventory for setup checklist completion (inventory sync)
            print("\nCreating GAM inventory for setup checklist...")
            stmt_check_inventory = select(GAMInventory).filter_by(tenant_id=tenant_id)
            existing_inventory_count = len(session.scalars(stmt_check_inventory).all())

            if existing_inventory_count == 0:
                # Create sample inventory items to satisfy setup checklist
                inventory_items = [
                    GAMInventory(
                        tenant_id=tenant_id,
                        inventory_type="ad_unit",
                        inventory_id="ci_test_ad_unit_1",
                        name="CI Test Ad Unit - Homepage",
                        path=["root", "website", "homepage"],
                        status="active",
                        inventory_metadata={"sizes": ["300x250", "728x90"]},
                    ),
                    GAMInventory(
                        tenant_id=tenant_id,
                        inventory_type="ad_unit",
                        inventory_id="ci_test_ad_unit_2",
                        name="CI Test Ad Unit - Article",
                        path=["root", "website", "article"],
                        status="active",
                        inventory_metadata={"sizes": ["300x600", "970x250"]},
                    ),
                    GAMInventory(
                        tenant_id=tenant_id,
                        inventory_type="placement",
                        inventory_id="ci_test_placement_1",
                        name="CI Test Placement - Premium",
                        path=["root"],
                        status="active",
                        inventory_metadata={"description": "Premium placement for CI tests"},
                    ),
                    GAMInventory(
                        tenant_id=tenant_id,
                        inventory_type="targeting_key",
                        inventory_id="ci_test_targeting_key_1",
                        name="CI Test Key - Category",
                        path=[],
                        status="active",
                        inventory_metadata={"type": "predefined", "values": ["news", "sports", "entertainment"]},
                    ),
                ]
                for item in inventory_items:
                    session.add(item)

                session.commit()
                print(f"  ✓ Created {len(inventory_items)} inventory items (ad units, placements, targeting)")
            else:
                print(f"  ℹ️  Inventory already exists: {existing_inventory_count} items")

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

        # ==================================================================
        # Create isolation test tenant (iso-test) for multi-tenant e2e tests
        # ==================================================================
        print("\n" + "=" * 60)
        print("Creating isolation test tenant (iso-test) for e2e tests...")
        print("=" * 60)

        with get_db_session() as iso_session:
            stmt_iso = select(Tenant).filter_by(subdomain="iso-test")
            existing_iso = iso_session.scalars(stmt_iso).first()

            if existing_iso:
                iso_tenant_id = existing_iso.tenant_id
                print(f"Isolation tenant already exists (ID: {iso_tenant_id})")
            else:
                iso_tenant_id = str(uuid.uuid4())
                now_iso = datetime.now(UTC)
                iso_tenant = Tenant(
                    tenant_id=iso_tenant_id,
                    name="Isolation Test Tenant",
                    subdomain="iso-test",
                    billing_plan="test",
                    ad_server="mock",
                    enable_axe_signals=False,
                    is_active=True,
                    authorized_emails=["iso-test@example.com"],
                    authorized_domains=None,
                    policy_settings=None,
                    signals_agent_config=None,
                    ai_policy=None,
                    auto_approve_format_ids=["display_300x250"],
                    human_review_required=False,
                    auth_setup_mode=False,
                    created_at=now_iso,
                    updated_at=now_iso,
                )
                iso_session.add(iso_tenant)
                try:
                    iso_session.commit()
                    print(f"  ✓ Created isolation tenant (ID: {iso_tenant_id})")
                except Exception as e:
                    iso_session.rollback()
                    print(f"  ⚠️  Isolation tenant race condition: {e}")
                    stmt_iso = select(Tenant).filter_by(subdomain="iso-test")
                    existing_iso = iso_session.scalars(stmt_iso).first()
                    if existing_iso:
                        iso_tenant_id = existing_iso.tenant_id
                    else:
                        raise ValueError("Failed to create or find isolation tenant")

            # Create principal for isolation tenant
            existing_iso_principal = find_principal_by_token_hash(iso_session, hash_token("iso-test-token"))
            if not existing_iso_principal:
                iso_principal_id = str(uuid.uuid4())
                PrincipalRepository(iso_session, iso_tenant_id).create_with_token(
                    "iso-test-token",
                    principal_id=iso_principal_id,
                    name="Isolation Test Principal",
                    platform_mappings={"mock": {"advertiser_id": "iso-test-advertiser"}},
                )
                try:
                    iso_session.commit()
                    print(f"  ✓ Created isolation principal (ID: {iso_principal_id})")
                except Exception as e:
                    iso_session.rollback()
                    print(f"  ⚠️  Isolation principal race condition: {e}")
            else:
                iso_principal_id = existing_iso_principal.principal_id
                print(f"  ✓ Isolation principal already exists (ID: {iso_principal_id})")

            # Create currency limit for isolation tenant
            stmt_iso_currency = select(CurrencyLimit).filter_by(tenant_id=iso_tenant_id, currency_code="USD")
            if not iso_session.scalars(stmt_iso_currency).first():
                iso_session.add(
                    CurrencyLimit(
                        tenant_id=iso_tenant_id,
                        currency_code="USD",
                        min_package_budget=500.0,
                        max_daily_package_spend=5000.0,
                    )
                )

            # Create property tag for isolation tenant
            now_iso = datetime.now(UTC)
            stmt_iso_tag = select(PropertyTag).filter_by(tenant_id=iso_tenant_id, tag_id="all_inventory")
            if not iso_session.scalars(stmt_iso_tag).first():
                iso_session.add(
                    PropertyTag(
                        tag_id="all_inventory",
                        tenant_id=iso_tenant_id,
                        name="All Inventory",
                        description="Default tag for isolation tenant",
                        created_at=now_iso,
                        updated_at=now_iso,
                    )
                )

            try:
                iso_session.commit()
                print("  ✓ Created currency limit and property tag for isolation tenant")
            except Exception as e:
                iso_session.rollback()
                print(f"  ⚠️  Isolation prerequisites race condition: {e}")

            # Create products for isolation tenant (iso_ prefix for clear identification).
            # KEEP IN SYNC: tests/e2e/test_tenant_isolation.py ISO_TEST_PRODUCT_IDS
            # asserts the iso-test tenant sees EXACTLY this product-id set. If you
            # add/remove/rename an iso-test product here, update that set too.
            iso_products_data = [
                {
                    "product_id": "iso_display_standard",
                    "name": "ISO Standard Display",
                    "description": "Standard display ads for isolation testing",
                    "formats": [
                        {"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"},
                    ],
                    "targeting_template": {"geo": ["UK"], "device_type": "any"},
                    "delivery_type": "guaranteed",
                    "pricing": {"model": "cpm", "rate": 10.0, "is_fixed": True},
                },
            ]

            for p in iso_products_data:
                stmt_prod = select(Product).filter_by(tenant_id=iso_tenant_id, product_id=p["product_id"])
                if not iso_session.scalars(stmt_prod).first():
                    product = Product(
                        tenant_id=iso_tenant_id,
                        product_id=p["product_id"],
                        name=p["name"],
                        description=p["description"],
                        format_ids=p["formats"],
                        targeting_template=p["targeting_template"],
                        delivery_type=p["delivery_type"],
                        property_tags=["all_inventory"],
                        measurement=None,
                        creative_policy=None,
                        price_guidance=None,
                        countries=None,
                        implementation_config=None,
                        properties=None,
                    )
                    iso_session.add(product)
                    print(f"  ✓ Created isolation product: {p['name']}")

                    pricing = p["pricing"]
                    iso_session.add(
                        PricingOption.create(
                            tenant_id=iso_tenant_id,
                            product_id=p["product_id"],
                            pricing_model=pricing["model"],
                            rate=pricing["rate"],
                            currency="USD",
                            is_fixed=pricing["is_fixed"],
                            price_guidance=None,
                        )
                    )
                    print(f"  ✓ Created pricing_option for: {p['name']}")
                else:
                    print(f"  ℹ️  Isolation product already exists: {p['name']}")

            iso_session.commit()

            # Verify isolation tenant products
            stmt_iso_verify = select(Product).filter_by(tenant_id=iso_tenant_id)
            iso_products = iso_session.scalars(stmt_iso_verify).all()
            print(f"\n✅ Isolation tenant verification: {len(iso_products)} products")
            for prod in iso_products:
                print(f"   - {prod.product_id}: {prod.name}")

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
