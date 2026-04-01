"""
Integration test specific fixtures.

These fixtures are for tests that require database and service integration.
"""

import os
import uuid
from datetime import UTC, date, datetime

import psycopg2
import pytest
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
from sqlalchemy import create_engine, delete, select

from src.admin.app import create_app

admin_app = create_app()
from src.core.database.database_session import get_db_session
from src.core.database.models import MediaBuy, MediaPackage, Principal, Tenant
from tests.fixtures import TenantFactory
from tests.integration.migration_helpers import parse_postgres_url

# ---------------------------------------------------------------------------
# Shared test helpers for media buy repository tests
# ---------------------------------------------------------------------------

# integration_db fixture moved to tests/conftest_db.py (visible to all test suites including tests/bdd/)
# It is available here via tests/conftest.py -> from tests.conftest_db import *


def cleanup_tenant(tenant_id: str) -> None:
    """Delete tenant and all dependent data (correct FK order)."""
    with get_db_session() as session:
        mb_ids = session.scalars(select(MediaBuy.media_buy_id).where(MediaBuy.tenant_id == tenant_id)).all()
        if mb_ids:
            session.execute(delete(MediaPackage).where(MediaPackage.media_buy_id.in_(mb_ids)))
        session.execute(delete(MediaBuy).where(MediaBuy.tenant_id == tenant_id))
        session.execute(delete(Principal).where(Principal.tenant_id == tenant_id))
        session.execute(delete(Tenant).where(Tenant.tenant_id == tenant_id))
        session.commit()


def make_media_buy(tenant_id: str, principal_id: str, media_buy_id: str, **kwargs) -> MediaBuy:
    """Helper to construct a MediaBuy ORM object with required fields."""
    defaults = {
        "order_name": f"Order {media_buy_id}",
        "advertiser_name": "Test Advertiser",
        "start_date": date(2026, 1, 1),
        "end_date": date(2026, 12, 31),
        "status": "draft",
        "raw_request": {"test": True},
    }
    defaults.update(kwargs)
    return MediaBuy(
        media_buy_id=media_buy_id,
        tenant_id=tenant_id,
        principal_id=principal_id,
        **defaults,
    )


def make_package(media_buy_id: str, package_id: str, **kwargs) -> MediaPackage:
    """Helper to construct a MediaPackage ORM object."""
    defaults = {
        "package_config": {"name": f"Package {package_id}", "test": True},
    }
    defaults.update(kwargs)
    return MediaPackage(
        media_buy_id=media_buy_id,
        package_id=package_id,
        **defaults,
    )


@pytest.fixture
def admin_client(integration_db):
    """Create test client for admin UI with proper configuration."""
    admin_app.config["TESTING"] = True
    admin_app.config["SECRET_KEY"] = "test-secret-key"
    admin_app.config["PROPAGATE_EXCEPTIONS"] = True  # Critical for catching template errors
    admin_app.config["SESSION_COOKIE_PATH"] = "/"  # Allow session cookies for all paths in tests
    admin_app.config["SESSION_COOKIE_HTTPONLY"] = False  # Allow test client to access cookies
    admin_app.config["SESSION_COOKIE_SECURE"] = False  # Allow HTTP in tests
    admin_app.config["WTF_CSRF_ENABLED"] = False  # Disable CSRF for tests
    with admin_app.test_client() as client:
        yield client


@pytest.fixture
def authenticated_admin_session(admin_client, integration_db):
    """Create an authenticated session for admin UI testing."""
    # Set up super admin configuration in database
    from src.core.database.database_session import get_db_session
    from src.core.database.models import TenantManagementConfig

    with get_db_session() as db_session:
        # Add tenant management admin email configuration
        email_config = TenantManagementConfig(config_key="super_admin_emails", config_value="test@example.com")
        db_session.add(email_config)
        db_session.commit()

    # Enable test mode for authentication
    os.environ["ADCP_AUTH_TEST_MODE"] = "true"

    with admin_client.session_transaction() as sess:
        sess["authenticated"] = True
        sess["role"] = "super_admin"
        sess["email"] = "test@example.com"
        sess["user"] = {"email": "test@example.com", "role": "super_admin"}  # Required by require_auth decorator
        sess["is_super_admin"] = True  # Blueprint sets this
        # Test mode session keys for require_tenant_access() decorator
        sess["test_user"] = "test@example.com"
        sess["test_user_role"] = "super_admin"
        sess["test_user_name"] = "Test Admin"

    yield admin_client

    # Clean up test mode
    if "ADCP_AUTH_TEST_MODE" in os.environ:
        del os.environ["ADCP_AUTH_TEST_MODE"]


@pytest.fixture
def test_tenant_with_data(integration_db):
    """Create a test tenant in the database with proper configuration and all required setup data."""
    from src.core.database.models import (
        AuthorizedProperty,
        CurrencyLimit,
        GAMInventory,
        Principal,
        PropertyTag,
        TenantAuthConfig,
    )

    tenant_data = TenantFactory.create()
    now = datetime.now(UTC)

    with get_db_session() as db_session:
        tenant = Tenant(
            tenant_id=tenant_data["tenant_id"],
            name=tenant_data["name"],
            subdomain=tenant_data["subdomain"],
            is_active=tenant_data["is_active"],
            ad_server="mock",  # Mock adapter is accepted in test environments (ADCP_TESTING=true)
            auth_setup_mode=False,  # Disable setup mode for production-ready auth
            auto_approve_format_ids=[],  # JSONType expects list, not json.dumps()
            human_review_required=False,
            policy_settings={},  # JSONType expects dict, not json.dumps()
            authorized_emails=["test@example.com"],  # Required for access control
            created_at=now,
            updated_at=now,
        )
        db_session.add(tenant)
        db_session.flush()

        # Add all required setup data for tests to pass setup checklist validation
        tenant_id = tenant_data["tenant_id"]

        # CurrencyLimit (required for budget validation)
        currency_limit = CurrencyLimit(
            tenant_id=tenant_id,
            currency_code="USD",
            min_package_budget=1.00,
            max_daily_package_spend=100000.00,
        )
        db_session.add(currency_limit)

        # PropertyTag (required for product property_tags)
        property_tag = PropertyTag(
            tenant_id=tenant_id,
            tag_id="all_inventory",
            name="All Inventory",
            description="All available inventory",
        )
        db_session.add(property_tag)

        # AuthorizedProperty (required for setup validation)
        auth_property = AuthorizedProperty(
            tenant_id=tenant_id,
            property_id=f"{tenant_id}_property_1",
            property_type="website",
            name="Fixture Default Property",  # Unique name to avoid conflicts with test assertions
            identifiers=[{"type": "domain", "value": "fixture-default.example.com"}],
            publisher_domain="fixture-default.example.com",
            verification_status="verified",
        )
        db_session.add(auth_property)

        # Principal (required for setup completion)
        # Include both kevel and mock mappings to support ad_server="kevel" (which is production-ready)
        principal = Principal(
            tenant_id=tenant_id,
            principal_id=f"{tenant_id}_principal",
            name="Test Principal",
            access_token=f"{tenant_id}_token",
            platform_mappings={
                "kevel": {"advertiser_id": f"kevel_adv_{tenant_id}"},
                "mock": {"advertiser_id": f"mock_adv_{tenant_id}"},
            },
        )
        db_session.add(principal)

        # GAMInventory (required for inventory sync status)
        inventory_items = [
            GAMInventory(
                tenant_id=tenant_id,
                inventory_type="ad_unit",
                inventory_id=f"{tenant_id}_ad_unit_1",
                name="Test Ad Unit",
                path=["root", "test"],
                status="active",
                inventory_metadata={"sizes": ["300x250"]},
            ),
            GAMInventory(
                tenant_id=tenant_id,
                inventory_type="placement",
                inventory_id=f"{tenant_id}_placement_1",
                name="Test Placement",
                path=["root"],
                status="active",
                inventory_metadata={},
            ),
        ]
        for item in inventory_items:
            db_session.add(item)

        # TenantAuthConfig with SSO enabled (required for setup validation)
        auth_config = TenantAuthConfig(
            tenant_id=tenant_id,
            oidc_enabled=True,
            oidc_provider="google",
            oidc_discovery_url="https://accounts.google.com/.well-known/openid-configuration",
            oidc_client_id="test_client_id_for_fixtures",
            oidc_scopes="openid email profile",
        )
        db_session.add(auth_config)

        db_session.commit()

    return tenant_data


@pytest.fixture
def populated_db(integration_db):
    """Provide a database populated with test data."""
    from tests.fixtures import PrincipalFactory, ProductFactory, TenantFactory

    # Create test data
    tenant_data = TenantFactory.create()
    PrincipalFactory.create(tenant_id=tenant_data["tenant_id"])
    ProductFactory.create_batch(3, tenant_id=tenant_data["tenant_id"])


@pytest.fixture
def sample_tenant(integration_db):
    """Create a sample tenant for testing with required currency and property configuration."""
    from datetime import UTC, datetime

    from src.core.database.database_session import get_db_session
    from src.core.database.models import (
        AuthorizedProperty,
        CurrencyLimit,
        GAMInventory,
        PropertyTag,
        Tenant,
        TenantAuthConfig,
    )

    now = datetime.now(UTC)
    with get_db_session() as session:
        tenant = Tenant(
            tenant_id="test_tenant",
            name="Test Tenant",
            subdomain="test",
            is_active=True,
            ad_server="mock",  # Mock adapter is accepted in test environments (ADCP_TESTING=true)
            auth_setup_mode=False,  # Disable setup mode for production-ready auth
            enable_axe_signals=True,
            authorized_emails=["test@example.com"],
            authorized_domains=["example.com"],
            auto_approve_format_ids=["display_300x250"],
            human_review_required=False,
            admin_token="test_admin_token",
            created_at=now,
            updated_at=now,
        )
        session.add(tenant)
        session.commit()

        # Add required CurrencyLimit (required for media buys)
        currency_limit = CurrencyLimit(
            tenant_id=tenant.tenant_id,
            currency_code="USD",
            max_daily_package_spend=10000.0,
            min_package_budget=100.0,
        )
        session.add(currency_limit)

        # Add required PropertyTag (required for product property_tags references)
        property_tag = PropertyTag(
            tenant_id=tenant.tenant_id,
            tag_id="all_inventory",
            name="All Inventory",
            description="All available ad inventory",
        )
        session.add(property_tag)

        # Add required AuthorizedProperty (required for setup checklist)
        auth_property = AuthorizedProperty(
            tenant_id=tenant.tenant_id,
            property_id="example_property",
            property_type="website",
            name="Example Property",
            identifiers=[{"type": "domain", "value": "example.com"}],
            publisher_domain="example.com",
            verification_status="verified",
        )
        session.add(auth_property)

        # Add GAMInventory records (required for inventory sync status in setup checklist)
        inventory_items = [
            GAMInventory(
                tenant_id=tenant.tenant_id,
                inventory_type="ad_unit",
                inventory_id="test_ad_unit_1",
                name="Test Ad Unit - Homepage",
                path=["root", "website", "homepage"],
                status="active",
                inventory_metadata={"sizes": ["300x250", "728x90"]},
            ),
            GAMInventory(
                tenant_id=tenant.tenant_id,
                inventory_type="placement",
                inventory_id="test_placement_1",
                name="Test Placement - Premium",
                path=["root"],
                status="active",
                inventory_metadata={"description": "Premium placement"},
            ),
        ]
        for item in inventory_items:
            session.add(item)

        # TenantAuthConfig with SSO enabled (required for setup validation)
        auth_config = TenantAuthConfig(
            tenant_id=tenant.tenant_id,
            oidc_enabled=True,
            oidc_provider="google",
            oidc_discovery_url="https://accounts.google.com/.well-known/openid-configuration",
            oidc_client_id="test_client_id_for_fixtures",
            oidc_scopes="openid email profile",
        )
        session.add(auth_config)

        session.commit()

        return {
            "tenant_id": tenant.tenant_id,
            "name": tenant.name,
            "subdomain": tenant.subdomain,
            "admin_token": tenant.admin_token,
        }


@pytest.fixture
def sample_principal(integration_db, sample_tenant):
    """Create a sample principal with valid platform mappings."""
    from src.core.database.database_session import get_db_session
    from src.core.database.models import Principal

    with get_db_session() as session:
        from datetime import UTC, datetime

        now = datetime.now(UTC)
        principal = Principal(
            tenant_id=sample_tenant["tenant_id"],
            principal_id="test_principal",
            name="Test Advertiser",
            access_token="test_token_12345",
            # Include both kevel and mock mappings for compatibility
            platform_mappings={
                "kevel": {"advertiser_id": "test_advertiser"},
                "mock": {"id": "test_advertiser"},
            },
            created_at=now,
        )
        session.add(principal)
        session.commit()

        return {
            "principal_id": principal.principal_id,
            "name": principal.name,
            "access_token": principal.access_token,
        }


@pytest.fixture
def sample_products(integration_db, sample_tenant):
    """Create sample products that comply with AdCP protocol."""
    from src.core.database.database_session import get_db_session
    from src.core.database.models import PricingOption as PricingOptionModel
    from src.core.database.models import Product

    with get_db_session() as session:
        products = [
            Product(
                tenant_id=sample_tenant["tenant_id"],
                product_id="guaranteed_display",
                name="Guaranteed Display Ads",
                description="Premium guaranteed display advertising",
                format_ids=[
                    {"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"},
                    {"agent_url": "https://creative.adcontextprotocol.org", "id": "display_728x90"},
                ],
                targeting_template={"geo_country": {"values": ["US"], "required": False}},
                delivery_type="guaranteed",
                property_tags=["all_inventory"],  # Required per AdCP spec
                is_custom=False,
                countries=["US"],
                measurement=None,
                creative_policy=None,
                price_guidance=None,
                implementation_config=None,
                properties=None,
                # Placements for placement-targeting validation (adcp#208)
                placements=[
                    {
                        "placement_id": "homepage_atf",
                        "name": "Homepage Above the Fold",
                        "description": "Premium above-the-fold placement on homepage",
                        "format_ids": [
                            {"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"},
                        ],
                    },
                    {
                        "placement_id": "sidebar",
                        "name": "Sidebar",
                        "description": "Standard sidebar placement",
                        "format_ids": [
                            {"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"},
                        ],
                    },
                    {
                        "placement_id": "article_inline",
                        "name": "Article Inline",
                        "description": "Inline placement within article content",
                        "format_ids": [
                            {"agent_url": "https://creative.adcontextprotocol.org", "id": "display_728x90"},
                        ],
                    },
                ],
            ),
            Product(
                tenant_id=sample_tenant["tenant_id"],
                product_id="non_guaranteed_video",
                name="Non-Guaranteed Video",
                description="Programmatic video advertising",
                format_ids=[
                    {"agent_url": "https://creative.adcontextprotocol.org", "id": "video_15s"},
                    {"agent_url": "https://creative.adcontextprotocol.org", "id": "video_30s"},
                ],
                targeting_template={},
                delivery_type="non_guaranteed",
                property_tags=["all_inventory"],  # Required per AdCP spec
                price_guidance={"floor": 10.0, "p50": 20.0, "p75": 30.0, "p90": 40.0},
                is_custom=False,
                countries=["US", "CA"],
                measurement=None,
                creative_policy=None,
                implementation_config=None,
                properties=None,
            ),
        ]

        for product in products:
            session.add(product)
        session.commit()

        # Create pricing_options for each product (required per AdCP PR #88)
        # Note: Database model uses auto-increment 'id', not 'pricing_option_id'
        pricing_options = [
            PricingOptionModel(
                tenant_id=sample_tenant["tenant_id"],
                product_id="guaranteed_display",
                pricing_model="cpm",
                rate=15.0,
                currency="USD",
                is_fixed=True,
                price_guidance=None,  # Not used for fixed pricing
            ),
            PricingOptionModel(
                tenant_id=sample_tenant["tenant_id"],
                product_id="non_guaranteed_video",
                pricing_model="cpm",
                rate=None,  # Auction-based pricing has no fixed rate
                currency="USD",
                is_fixed=False,
                price_guidance={"floor": 10.0, "p50": 20.0, "p75": 30.0, "p90": 40.0},
            ),
        ]

        for pricing_option in pricing_options:
            session.add(pricing_option)
        session.commit()

        return [p.product_id for p in products]


@pytest.fixture(scope="function")
def mcp_server(integration_db):
    """Start a real MCP server for integration testing using the test database."""
    import socket
    import subprocess
    import sys
    import time

    # Find an available port
    def get_free_port():
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("", 0))
            s.listen(1)
            port = s.getsockname()[1]
        return port

    port = get_free_port()

    # Use the integration_db (PostgreSQL database name already created by the integration_db fixture)
    db_name = integration_db

    # IMPORTANT: Close any existing connections to ensure the database is fully committed
    # This is necessary because the server subprocess will create a new connection
    # Note: SQLAlchemy 2.0 uses get_db_session() context manager, no global db_session to close

    # Set up environment for the server (use PostgreSQL, not SQLite)
    # Get PostgreSQL connection details from current DATABASE_URL
    postgres_url = os.environ.get("DATABASE_URL", "")
    if not postgres_url or not postgres_url.startswith("postgresql://"):
        raise RuntimeError("mcp_server fixture requires PostgreSQL DATABASE_URL")

    import re

    pattern = r"postgresql://([^:]+):([^@]+)@([^:]+):(\d+)/(.+)"
    match = re.match(pattern, postgres_url)
    if match:
        user, password, host, port_str, _ = match.groups()
        postgres_port = int(port_str)
        server_db_url = f"postgresql://{user}:{password}@{host}:{postgres_port}/{db_name}"
    else:
        raise RuntimeError(f"Failed to parse DATABASE_URL: {postgres_url}")

    env = os.environ.copy()
    env["ADCP_SALES_PORT"] = str(port)
    env["DATABASE_URL"] = server_db_url
    env["DB_TYPE"] = "postgresql"
    env["ADCP_TESTING"] = "true"
    env["PYTHONUNBUFFERED"] = "1"  # Force unbuffered output for better debugging

    # Start the server process using mcp.run() instead of uvicorn directly
    server_script = f"""
import sys
sys.path.insert(0, '.')
from src.core.main import mcp
mcp.run(transport='http', host='0.0.0.0', port={port})
"""

    process = subprocess.Popen(
        [sys.executable, "-c", server_script],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=1,  # Line buffered
    )

    # Wait for server to be ready
    max_wait = 20  # seconds (increased for server initialization)
    start_time = time.time()
    server_ready = False

    while time.time() - start_time < max_wait:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(1)
                s.connect(("localhost", port))
                server_ready = True
                break
        except (ConnectionRefusedError, OSError):
            # Check if process has died
            if process.poll() is not None:
                stdout, stderr = process.communicate()
                raise RuntimeError(
                    f"MCP server process died unexpectedly.\n"
                    f"STDOUT: {stdout.decode() if stdout else 'N/A'}\n"
                    f"STDERR: {stderr.decode() if stderr else 'N/A'}"
                )
            time.sleep(0.3)

    if not server_ready:
        # Capture output for debugging
        try:
            stdout, stderr = process.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate()

        process.terminate()
        process.wait(timeout=5)
        raise RuntimeError(
            f"MCP server failed to start on port {port} within {max_wait}s.\n"
            f"STDOUT: {stdout.decode() if stdout else 'N/A'}\n"
            f"STDERR: {stderr.decode() if stderr else 'N/A'}"
        )

    # Return server info
    class ServerInfo:
        def __init__(self, port, process, db_name):
            self.port = port
            self.process = process
            self.db_name = db_name

    server = ServerInfo(port, process, db_name)

    yield server

    # Cleanup
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
    if process.stdout:
        process.stdout.close()
    if process.stderr:
        process.stderr.close()

    # Don't remove db_name - the PostgreSQL database is managed by integration_db fixture


@pytest.fixture
def test_admin_app(integration_db):
    """Provide a test Admin UI app with real database."""
    # integration_db ensures database tables are created
    from src.admin.app import create_app

    app = create_app()
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test-secret-key"
    app.config["WTF_CSRF_ENABLED"] = False
    app.config["SESSION_COOKIE_PATH"] = "/"  # Allow session cookies for all paths in tests
    app.config["SESSION_COOKIE_HTTPONLY"] = False  # Allow test client to access cookies
    app.config["SESSION_COOKIE_SECURE"] = False  # Allow HTTP in tests

    yield app


@pytest.fixture
def authenticated_admin_client(test_admin_app):
    """Provide authenticated admin client with database."""
    # Enable test mode for authentication
    os.environ["ADCP_AUTH_TEST_MODE"] = "true"

    client = test_admin_app.test_client()

    with client.session_transaction() as sess:
        sess["user"] = {"email": "admin@example.com", "name": "Admin User", "role": "super_admin"}
        sess["authenticated"] = True
        sess["role"] = "super_admin"
        sess["email"] = "admin@example.com"
        # Add test mode session keys for require_tenant_access() decorator
        sess["test_user"] = "admin@example.com"
        sess["test_user_role"] = "super_admin"
        sess["test_user_name"] = "Admin User"

    yield client

    # Clean up test mode
    if "ADCP_AUTH_TEST_MODE" in os.environ:
        del os.environ["ADCP_AUTH_TEST_MODE"]


@pytest.fixture
def test_media_buy_workflow(populated_db):
    """Provide complete media buy workflow test setup."""
    from src.core.database.database_session import get_db_session
    from src.core.database.models import Creative, MediaBuy
    from tests.fixtures import CreativeFactory, MediaBuyFactory

    data = populated_db

    # Create media buy
    media_buy_data = MediaBuyFactory.create(
        tenant_id=data["tenant"]["tenant_id"],
        principal_id=data["principal"]["principal_id"],
        status="draft",
    )

    # Create creatives
    creatives_data = CreativeFactory.create_batch(
        2,
        tenant_id=data["tenant"]["tenant_id"],
        principal_id=data["principal"]["principal_id"],
    )

    # Insert into database using ORM
    with get_db_session() as db_session:
        media_buy = MediaBuy(
            tenant_id=media_buy_data["tenant_id"],
            media_buy_id=media_buy_data["media_buy_id"],
            principal_id=media_buy_data["principal_id"],
            status=media_buy_data["status"],
            config=media_buy_data["config"],
            total_budget=media_buy_data["total_budget"],
        )
        db_session.add(media_buy)

        for creative_data in creatives_data:
            creative = Creative(
                tenant_id=creative_data["tenant_id"],
                creative_id=creative_data["creative_id"],
                principal_id=creative_data["principal_id"],
                format_id=creative_data["format_id"],
                status=creative_data["status"],
                content=creative_data["content"],
            )
            db_session.add(creative)

        db_session.commit()

    return {**data, "media_buy": media_buy_data, "creatives": creatives_data}


@pytest.fixture
def test_audit_logger(integration_db):
    """Provide test audit logger with database."""
    from src.core.audit_logger import AuditLogger

    logger = AuditLogger("test_tenant")

    yield logger


@pytest.fixture(scope="module")
def migration_db():
    """Create an isolated PostgreSQL database for migration testing.

    Yields (engine, db_url) and cleans up the database after the test module.
    Uses Alembic for schema management -- does NOT use Base.metadata.create_all().
    """
    parsed = parse_postgres_url()
    if not parsed:
        pytest.skip("Requires PostgreSQL DATABASE_URL")

    user, password, host, port = parsed
    db_name = f"test_migration_{uuid.uuid4().hex[:8]}"

    conn_params = {
        "host": host,
        "port": port,
        "user": user,
        "password": password,
        "database": "postgres",
    }

    conn = psycopg2.connect(**conn_params)
    conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    cur = conn.cursor()
    cur.execute(f'CREATE DATABASE "{db_name}"')
    cur.close()
    conn.close()

    db_url = f"postgresql://{user}:{password}@{host}:{port}/{db_name}"
    engine = create_engine(db_url, echo=False)

    yield engine, db_url

    engine.dispose()
    try:
        conn = psycopg2.connect(**conn_params)
        conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        cur = conn.cursor()
        cur.execute(
            f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            f"WHERE datname = '{db_name}' AND pid <> pg_backend_pid()"
        )
        cur.execute(f'DROP DATABASE IF EXISTS "{db_name}"')
        cur.close()
        conn.close()
    except Exception:
        pass


@pytest.fixture
def mock_identity(sample_tenant, sample_principal):
    """Build a ResolvedIdentity from real test DB fixtures.

    Use this with: patch("src.core.resolved_identity.resolve_identity", return_value=mock_identity)

    Uses LazyTenantContext so the tenant dict is always read from the DB,
    matching production behavior (where resolve_identity → LazyTenantContext
    defers DB load until a field is accessed).
    """
    from src.core.resolved_identity import ResolvedIdentity
    from src.core.tenant_context import LazyTenantContext

    return ResolvedIdentity(
        principal_id=sample_principal["principal_id"],
        tenant_id=sample_tenant["tenant_id"],
        tenant=LazyTenantContext(sample_tenant["tenant_id"]),
        protocol="a2a",
    )


# ============================================================================
# Pricing Helper Functions (merged from integration_v2)
# ============================================================================
# These helpers provide a consistent API for creating Products with
# pricing_options. They will break if Product/PricingOption schema changes
# (intentional - ensures tests stay up to date with migrations).


def get_pricing_option_id(product, currency: str = "USD") -> str:
    """Get the pricing_option_id for a given product and currency.

    Args:
        product: Product instance (from create_test_product_with_pricing)
        currency: Currency code to find (default: USD)

    Returns:
        String pricing_option_id in format {pricing_model}_{currency}_{fixed|auction}

    Raises:
        ValueError: If no pricing option found for currency
    """
    for pricing_option in product.pricing_options:
        if pricing_option.currency == currency:
            fixed_str = "fixed" if pricing_option.is_fixed else "auction"
            return f"{pricing_option.pricing_model}_{pricing_option.currency.lower()}_{fixed_str}"
    raise ValueError(f"No pricing option found for currency {currency} on product {product.product_id}")


def create_test_product_with_pricing(
    session,
    tenant_id: str,
    product_id: str | None = None,
    name: str = "Test Product",
    pricing_model: str = "CPM",
    rate="15.00",
    is_fixed: bool = True,
    currency: str = "USD",
    min_spend_per_package=None,
    price_guidance: dict | None = None,
    format_ids: list[dict[str, str]] | None = None,
    targeting_template: dict | None = None,
    delivery_type: str = "guaranteed_impressions",
    property_tags: list[str] | None = None,
    **product_kwargs,
):
    """Create a Product with pricing_options using the new pricing model."""
    import uuid
    from decimal import Decimal

    from src.core.database.models import PricingOption, Product

    if product_id is None:
        product_id = f"test_product_{uuid.uuid4().hex[:8]}"

    if format_ids is None:
        format_ids = [{"agent_url": "https://test.com", "id": "300x250"}]

    if targeting_template is None:
        targeting_template = {}

    # Convert rate to Decimal
    if isinstance(rate, str):
        rate_decimal = Decimal(rate)
    elif isinstance(rate, float):
        rate_decimal = Decimal(str(rate))
    else:
        rate_decimal = rate

    # Convert min_spend to Decimal if provided
    min_spend_decimal = None
    if min_spend_per_package is not None:
        if isinstance(min_spend_per_package, str):
            min_spend_decimal = Decimal(min_spend_per_package)
        elif isinstance(min_spend_per_package, float):
            min_spend_decimal = Decimal(str(min_spend_per_package))
        else:
            min_spend_decimal = min_spend_per_package

    # AdCP Library Schema Compliance defaults
    if "delivery_measurement" not in product_kwargs:
        product_kwargs["delivery_measurement"] = {
            "provider": "Google Ad Manager",
            "notes": "MRC-accredited viewability",
        }

    if property_tags is None and "properties" not in product_kwargs:
        property_tags = ["all_inventory"]

    # Fix measurement - remove invalid fields
    if "measurement" in product_kwargs and isinstance(product_kwargs["measurement"], dict):
        valid_measurement_fields = {"available_metrics", "reporting_frequency", "reporting_delay_hours"}
        product_kwargs["measurement"] = {
            k: v for k, v in product_kwargs["measurement"].items() if k in valid_measurement_fields
        }
        if not product_kwargs["measurement"]:
            del product_kwargs["measurement"]

    # Fix creative_policy - remove invalid fields
    if "creative_policy" in product_kwargs and isinstance(product_kwargs["creative_policy"], dict):
        valid_creative_policy_fields = {"co_branding", "landing_page", "templates_available"}
        filtered_policy = {
            k: v for k, v in product_kwargs["creative_policy"].items() if k in valid_creative_policy_fields
        }
        if "co_branding" not in filtered_policy:
            filtered_policy["co_branding"] = "optional"
        if "landing_page" not in filtered_policy:
            filtered_policy["landing_page"] = "any"
        if "templates_available" not in filtered_policy:
            filtered_policy["templates_available"] = False
        product_kwargs["creative_policy"] = filtered_policy
    elif "creative_policy" not in product_kwargs:
        product_kwargs["creative_policy"] = {
            "co_branding": "optional",
            "landing_page": "any",
            "templates_available": False,
        }

    product = Product(
        tenant_id=tenant_id,
        product_id=product_id,
        name=name,
        format_ids=format_ids,
        targeting_template=targeting_template,
        delivery_type=delivery_type,
        property_tags=property_tags,
        **product_kwargs,
    )
    session.add(product)
    session.flush()

    pricing_model_lower = pricing_model.lower() if isinstance(pricing_model, str) else pricing_model
    pricing_option = PricingOption(
        tenant_id=tenant_id,
        product_id=product_id,
        pricing_model=pricing_model_lower,
        rate=rate_decimal,
        currency=currency,
        is_fixed=is_fixed,
        price_guidance=price_guidance,
        min_spend_per_package=min_spend_decimal,
    )
    session.add(pricing_option)
    session.flush()

    session.refresh(product)
    return product


def create_auction_product(
    session,
    tenant_id: str,
    product_id: str | None = None,
    name: str = "Auction Product",
    pricing_model: str = "CPM",
    floor_cpm="1.00",
    currency: str = "USD",
    **kwargs,
):
    """Create a Product with auction pricing (is_fixed=False)."""
    if "price_guidance" not in kwargs:
        floor_value = float(floor_cpm)
        kwargs["price_guidance"] = {
            "floor": floor_value,
            "p50": floor_value * 1.5,
            "p75": floor_value * 2.0,
            "p90": floor_value * 2.5,
        }

    return create_test_product_with_pricing(
        session=session,
        tenant_id=tenant_id,
        product_id=product_id,
        name=name,
        pricing_model=pricing_model,
        rate=floor_cpm,
        is_fixed=False,
        currency=currency,
        **kwargs,
    )


def create_flat_rate_product(
    session,
    tenant_id: str,
    product_id: str | None = None,
    name: str = "Flat Rate Product",
    rate="10000.00",
    currency: str = "USD",
    **kwargs,
):
    """Create a Product with flat-rate pricing."""
    return create_test_product_with_pricing(
        session=session,
        tenant_id=tenant_id,
        product_id=product_id,
        name=name,
        pricing_model="FLAT_RATE",
        rate=rate,
        is_fixed=True,
        currency=currency,
        delivery_type="sponsorship",
        **kwargs,
    )


def add_required_setup_data(session, tenant_id: str):
    """Add required setup data for a tenant to pass setup validation.

    This helper ensures tenants have all required relationships for
    tests to pass setup checklist validation.
    """
    from decimal import Decimal

    from sqlalchemy import select
    from sqlalchemy.orm import attributes

    from src.core.database.models import (
        AuthorizedProperty,
        CurrencyLimit,
        GAMInventory,
        Principal,
        PropertyTag,
        PublisherPartner,
        Tenant,
        TenantAuthConfig,
    )

    stmt = select(Tenant).filter_by(tenant_id=tenant_id)
    tenant = session.scalars(stmt).first()
    if tenant:
        if not tenant.authorized_emails:
            tenant.authorized_emails = ["test@example.com"]
            attributes.flag_modified(tenant, "authorized_emails")
        tenant.auth_setup_mode = False
        if tenant.ad_server is None:
            tenant.ad_server = "mock"
        session.flush()

    # TenantAuthConfig
    stmt_auth_config = select(TenantAuthConfig).filter_by(tenant_id=tenant_id)
    if not session.scalars(stmt_auth_config).first():
        auth_config = TenantAuthConfig(
            tenant_id=tenant_id,
            oidc_enabled=True,
            oidc_provider="google",
            oidc_discovery_url="https://accounts.google.com/.well-known/openid-configuration",
            oidc_client_id="test_client_id_for_fixtures",
            oidc_scopes="openid email profile",
        )
        session.add(auth_config)

    # PublisherPartner
    stmt_publisher = select(PublisherPartner).filter_by(
        tenant_id=tenant_id, publisher_domain="fixture-default.example.com"
    )
    if not session.scalars(stmt_publisher).first():
        publisher_partner = PublisherPartner(
            tenant_id=tenant_id,
            publisher_domain="fixture-default.example.com",
            display_name="Fixture Default Publisher",
            is_verified=True,
            sync_status="success",
        )
        session.add(publisher_partner)

    # AuthorizedProperty
    stmt_property = select(AuthorizedProperty).filter_by(tenant_id=tenant_id)
    if not session.scalars(stmt_property).first():
        authorized_property = AuthorizedProperty(
            tenant_id=tenant_id,
            property_id=f"{tenant_id}_property_1",
            property_type="website",
            name="Fixture Default Property",
            identifiers=[{"type": "domain", "value": "fixture-default.example.com"}],
            publisher_domain="fixture-default.example.com",
            verification_status="verified",
        )
        session.add(authorized_property)

    # CurrencyLimit
    stmt_currency = select(CurrencyLimit).filter_by(tenant_id=tenant_id, currency_code="USD")
    if not session.scalars(stmt_currency).first():
        currency_limit = CurrencyLimit(
            tenant_id=tenant_id,
            currency_code="USD",
            min_package_budget=Decimal("1.00"),
            max_daily_package_spend=Decimal("100000.00"),
        )
        session.add(currency_limit)

    # PropertyTag
    stmt_tag = select(PropertyTag).filter_by(tenant_id=tenant_id, tag_id="all_inventory")
    if not session.scalars(stmt_tag).first():
        property_tag = PropertyTag(
            tenant_id=tenant_id,
            tag_id="all_inventory",
            name="All Inventory",
            description="All available inventory",
        )
        session.add(property_tag)

    # Principal
    stmt_principal = select(Principal).filter_by(tenant_id=tenant_id)
    existing_principal = session.scalars(stmt_principal).first()
    if not existing_principal:
        principal = Principal(
            tenant_id=tenant_id,
            principal_id=f"{tenant_id}_default_principal",
            name="Default Test Principal",
            access_token=f"{tenant_id}_default_token",
            platform_mappings={
                "kevel": {"advertiser_id": f"kevel_adv_{tenant_id}"},
                "mock": {"advertiser_id": f"mock_adv_{tenant_id}"},
            },
        )
        session.add(principal)
    elif existing_principal.platform_mappings and "kevel" not in existing_principal.platform_mappings:
        existing_principal.platform_mappings["kevel"] = {
            "advertiser_id": f"kevel_adv_{existing_principal.principal_id}"
        }
        attributes.flag_modified(existing_principal, "platform_mappings")

    # GAMInventory
    stmt_inventory = select(GAMInventory).filter_by(tenant_id=tenant_id)
    if not session.scalars(stmt_inventory).first():
        inventory_items = [
            GAMInventory(
                tenant_id=tenant_id,
                inventory_type="ad_unit",
                inventory_id=f"{tenant_id}_ad_unit_1",
                name="Test Ad Unit",
                path=["root", "test"],
                status="active",
                inventory_metadata={"sizes": ["300x250"]},
            ),
            GAMInventory(
                tenant_id=tenant_id,
                inventory_type="placement",
                inventory_id=f"{tenant_id}_placement_1",
                name="Test Placement",
                path=["root"],
                status="active",
                inventory_metadata={},
            ),
        ]
        for item in inventory_items:
            session.add(item)
