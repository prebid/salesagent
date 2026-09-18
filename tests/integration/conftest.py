"""
Integration test specific fixtures.

These fixtures are for tests that require database and service integration.
"""

import os
import uuid
from contextlib import ExitStack
from datetime import UTC, date, datetime
from pathlib import Path

import psycopg2
import pytest
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
from sqlalchemy import create_engine, delete, select

from src.admin.app import create_app

# Registers the ci-test tenant/principal fixture for this package. Imported by
# name rather than star-imported: the one star import in tests/conftest.py is an
# allowlisted exception, not the pattern.
from tests.helpers.ledger import load_ledger_nodeids
from tests.integration.conftest_ci_seed import ci_test_principal  # noqa: F401

admin_app = create_app()
from src.core.database.database_session import get_db_session
from src.core.database.models import MediaBuy, MediaPackage, Principal, Tenant
from tests.fixtures import TenantFactory
from tests.helpers.local_http_origin import run_local_origin
from tests.helpers.local_mcp_origin import MCPOrigin, run_mcp_origin
from tests.helpers.test_tls_material import load_gen_test_tls, server_ssl_context
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
def local_origin():
    """A programmable HTTP origin on an ephemeral loopback port.

    Deliberately does NOT depend on ``integration_db``: outbound-egress tests
    need a real remote to talk to, not a database. Function-scoped and bound to
    port 0 because the integration suite runs under xdist.
    """
    with run_local_origin() as origin:
        yield origin


@pytest.fixture
def local_origin_tls(monkeypatch):
    """An https sibling of :func:`local_origin` (#1757).

    Serves real TLS off the same generated CA/leaf every other front in the
    repo reuses (never a second mechanism) — verification is ON, exactly like
    a production dial. ``SSL_CERT_FILE`` is set to the COMBINED bundle (system
    CA + our private CA), not the private CA alone: the bare-CA trap already
    broke `uv sync` against real pypi.org once (see docker-compose.e2e.yml's
    own comment on this), and this fixture's monkeypatch is function-scoped so
    the risk is contained regardless, but there is no reason to reintroduce it.
    Callers that need a plain-http origin (refusal tests, e.g. verifying a
    URL is never dialled) keep using :func:`local_origin` unchanged.
    """
    gen_test_tls = load_gen_test_tls()
    gen_test_tls.ensure_test_tls()
    monkeypatch.setenv("SSL_CERT_FILE", str(gen_test_tls.COMBINED_CERT))
    with run_local_origin(ssl_context=server_ssl_context(gen_test_tls)) as origin:
        yield origin


@pytest.fixture
def mcp_origin_tls(monkeypatch):
    """Start a real MCP origin over TLS, serving the tools a test hands it.

    The MCP sibling of :func:`local_origin_tls`: same generated CA/leaf, same
    ``SSL_CERT_FILE`` combined-bundle trust, but the origin speaks MCP, so a
    test can reach past the handshake and grade what a TOOL call does. Yielded
    as a factory rather than a started origin because the tools are the test's
    own — a fixture cannot know whether the tool under test succeeds, fails, or
    fails only the first time.
    """
    gen_test_tls = load_gen_test_tls()
    gen_test_tls.ensure_test_tls()
    monkeypatch.setenv("SSL_CERT_FILE", str(gen_test_tls.COMBINED_CERT))

    with ExitStack() as stack:

        def start(**tools) -> MCPOrigin:
            return stack.enter_context(
                run_mcp_origin(
                    tools=tools,
                    certfile=gen_test_tls.SERVER_CERT,
                    keyfile=gen_test_tls.SERVER_KEY,
                )
            )

        yield start


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
        # with_token, not access_token=: the row keeps sha256 plus a display prefix,
        # so the plaintext is an ARGUMENT to the constructor rather than a column.
        principal = Principal.with_token(
            f"{tenant_id}_token",
            tenant_id=tenant_id,
            principal_id=f"{tenant_id}_principal",
            name="Test Principal",
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
            # No admin_token: the tenant-level admin credential was dropped in 84a86e019
            # (migration e4b7c2a91f05). Principal tokens are hashed and shown once; there
            # is no tenant admin credential path left. No consumer read this key.
        }


@pytest.fixture
def sample_principal(integration_db, sample_tenant):
    """Create a sample principal with valid platform mappings."""
    from src.core.database.database_session import get_db_session
    from src.core.database.models import Principal

    with get_db_session() as session:
        from datetime import UTC, datetime

        now = datetime.now(UTC)
        token = "test_token_12345"
        principal = Principal.with_token(
            token,
            tenant_id=sample_tenant["tenant_id"],
            principal_id="test_principal",
            name="Test Advertiser",
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
            # The plaintext this fixture minted, not a column read: the row keeps
            # only sha256(token), so there is nothing on `principal` to read back.
            "access_token": token,
        }


@pytest.fixture
def sample_account(integration_db, factory_session, sample_tenant, sample_principal):
    """A real Account row the sample principal may act on.

    AdCP 3.1.1 makes `account` REQUIRED on sync-creatives-request and
    update-media-buy-request (/required), and production RESOLVES the reference against the
    database -- a fabricated id satisfies model construction and then earns
    ACCOUNT_NOT_FOUND at the wire. So a wire-level test needs a seeded account, not a
    plausible string. A test that only CONSTRUCTS models never reaches resolution and should
    keep using a literal.

    Built with factories, not session.add(): the repository-pattern guard forbids new inline
    session writes in tests, and it caught the first version of this fixture doing exactly
    that. The sibling sample_tenant/sample_principal fixtures predate that rule and are
    allowlisted; new code does not get to match them.

    Requests ``factory_session``, which binds the shared session onto every factory; the
    factories declare ``sqlalchemy_session = None`` and raise "No session provided" without it.

    Returns the AccountReference shape a request carries.
    """
    from tests.factories import AccountFactory, AgentAccountAccessFactory

    account = AccountFactory(tenant_id=sample_tenant["tenant_id"], account_id="acc_test_0001")
    # Resolution checks the calling agent's ACCESS, not merely that the row exists -- seeding
    # only the account fails in a way indistinguishable from not seeding at all.
    AgentAccountAccessFactory(
        tenant_id=sample_tenant["tenant_id"],
        principal_id=sample_principal["principal_id"],
        account_id=account.account_id,
    )
    return {"account_id": account.account_id}


@pytest.fixture
def sample_products(integration_db, sample_tenant):
    """Create sample products that comply with AdCP protocol."""
    from src.core.database.database_session import get_db_session
    from src.core.database.models import Product
    from tests.factories import PricingOptionFactory

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
        pricing_options = [
            PricingOptionFactory.build(
                tenant_id=sample_tenant["tenant_id"],
                product_id="guaranteed_display",
                pricing_model="cpm",
                rate=15.0,
                currency="USD",
                is_fixed=True,
                price_guidance=None,  # Not used for fixed pricing
            ),
            PricingOptionFactory.build(
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
    import shutil
    import socket
    import subprocess
    import sys
    import tempfile
    import time
    from pathlib import Path

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

    # The server's output goes to FILES, never PIPEs. A PIPE nobody drains caps
    # at 64KB; once server logging fills it (uvicorn access lines + app INFO +
    # rich console output), the server blocks on a log write INSIDE a request
    # handler and the calling test awaits forever — py-spy showed the server
    # MainThread parked in logging emit while create_media_buy hung for four
    # consecutive runs, and this wedged every full CI run at integration's quiet
    # tail until the >1h run reaper killed it (#1868 review). Files keep the
    # error-path diagnostics below without needing a drainer thread.
    output_dir = Path(tempfile.mkdtemp(prefix=f"mcp-server-{port}-"))
    stdout_path = output_dir / "stdout.log"
    stderr_path = output_dir / "stderr.log"
    stdout_f = stdout_path.open("wb")
    stderr_f = stderr_path.open("wb")
    try:
        process = subprocess.Popen(
            [sys.executable, "-c", server_script],
            env=env,
            stdout=stdout_f,
            stderr=stderr_f,
        )
    finally:
        # The child inherited the fds; the parent's handles are not needed.
        stdout_f.close()
        stderr_f.close()

    def _tail(path: Path, limit: int = 8000) -> str:
        """Last `limit` bytes of a log file — a chatty server writes far more than a failure message needs."""
        try:
            data = path.read_bytes()
        except OSError:
            return "N/A (log unreadable)"
        return data[-limit:].decode(errors="replace") if data else "N/A"

    def _server_output() -> str:
        return f"STDOUT: {_tail(stdout_path)}\nSTDERR: {_tail(stderr_path)}"

    # Wait for server to be ready.
    # Server startup is dominated by Python imports (fastmcp + adcp SDK + project)
    # plus FastAPI lifespan + DB pool warm-up. Under CI load this routinely takes
    # 20-40s; the prior 20s deadline produced flaky 'failed to start' errors even
    # though Uvicorn's own log showed it had started just past the threshold. Same
    # rationale as bf5fe3a66 (test-stack readiness deadline 120s -> 360s for
    # cold-boot).
    max_wait = 60  # seconds
    start_time = time.time()
    server_ready = False

    try:
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
                    raise RuntimeError(f"MCP server process died unexpectedly.\n{_server_output()}")
                time.sleep(0.3)

        if not server_ready:
            process.kill()
            process.wait(timeout=5)
            raise RuntimeError(f"MCP server failed to start on port {port} within {max_wait}s.\n{_server_output()}")
    except BaseException:
        # Both raise points above happen before yield, so pytest's generator-fixture
        # teardown (the code after yield, including shutil.rmtree(output_dir) below)
        # never runs for them -- clean up here instead, on every setup-failure path.
        shutil.rmtree(output_dir, ignore_errors=True)
        raise

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
    shutil.rmtree(output_dir, ignore_errors=True)

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
    client = test_admin_app.test_client()

    # The canonical admin session. It carries `is_super_admin` + `admin_email`, which
    # `is_super_admin()` short-circuits on — so recognising this caller costs no database
    # round trip. That matters here beyond speed: `get_db_session()` is scoped, so a read
    # inside the auth decorator nests within whatever session the TEST holds and its exit
    # detaches it, discarding rows the test flushed but had not committed.
    from tests.helpers.admin_session import admin_auth_session

    admin_auth_session(client, "default")

    yield client

    # Clean up test mode


# ``test_media_buy_workflow`` was here and is DELETED along with the dict
# ``CreativeFactory`` it seeded from (tests/fixtures/factories.py). It had zero consumers
# and could not have run for any of them: ``CreativeFactory.create_batch`` was never
# defined on that class, and the ORM ``Creative`` it then constructed has no ``format_id``
# or ``content`` column (they are ``format`` / ``agent_url`` / ``data``). Seed a media buy
# with ``MediaBuyFactory`` and creatives with ``CreativeFactory`` from tests/factories/.


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

    from src.core.database.models import Product
    from tests.factories import PricingOptionFactory

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
    pricing_option = PricingOptionFactory.build(
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
        principal = Principal.with_token(
            f"{tenant_id}_default_token",
            tenant_id=tenant_id,
            principal_id=f"{tenant_id}_default_principal",
            name="Default Test Principal",
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


def seed_error_test_tenant(
    *,
    tenant_id: str,
    principal_id: str,
    access_token: str,
    product_id: str,
    subdomain: str,
    tenant_name: str = "Error Test Tenant",
    advertiser_id: str = "mock_adv",
    protocol: str = "mcp",
) -> dict:
    """Seed a fully-configured tenant/principal/product for error-emission tests via factories.

    MUST be called inside an open ``IntegrationEnv`` (factory sessions are bound there).
    Composes factory-boy factories for the core entities and reuses
    ``add_required_setup_data`` for the setup-checklist scaffolding (SSO, authorized
    properties, etc.) so production's ``validate_setup_complete`` gate passes.
    ``PricingOptionFactory`` defaults (cpm/USD/fixed) derive the synthetic
    ``cpm_usd_fixed`` pricing option id the budget pins reference.

    Returns the seeded ``tenant`` / ``principal`` / ``product`` rows, the ``identity``
    (``ResolvedIdentity`` bound to the principal, carrying the tenant derived from its own
    row), and ``principal_id`` / ``access_token`` for callers that need them separately.
    """
    from src.core.tenant_context import TenantContext
    from tests.factories import (
        PricingOptionFactory,
        PrincipalFactory,
        ProductFactory,
        TenantFactory,
    )
    from tests.factories.principal import plaintext_token_for

    # Keyword arguments on the factory, not a dict. There was a ``tenant_dict`` here that got
    # splatted into the factory AND, separately, into ``make_tenant`` for the identity's
    # tenant -- two independent constructions of one tenant, free to drift, from a second
    # representation nothing owns. The factory owns the row's field values.
    tenant = TenantFactory(
        tenant_id=tenant_id,
        name=tenant_name,
        subdomain=subdomain,
        ad_server="mock",
        human_review_required=False,
        is_active=True,
    )
    product = ProductFactory(tenant=tenant, product_id=product_id, property_tags=["all_inventory"])
    PricingOptionFactory(product=product)
    # No access_token=: the row keeps sha256 plus a display prefix, and the factory
    # derives both from ``plaintext_token_for(principal_id)``. The token a caller
    # PRESENTS is therefore derived, not chosen — which is why the returned
    # ``access_token`` below is that derived value rather than the argument.
    principal = PrincipalFactory(
        tenant=tenant,
        principal_id=principal_id,
        platform_mappings={"mock": {"advertiser_id": advertiser_id}},
    )

    # Setup-checklist scaffolding (SSO, authorized properties, etc.) on the env-bound
    # session, so the production validate_setup_complete gate passes for create flows.
    session = TenantFactory._meta.sqlalchemy_session
    add_required_setup_data(session, tenant_id)
    session.commit()

    # No set_current_tenant. The ambient-tenant ContextVar and its setter were deleted with
    # the file-based config loader (76c2a96fb) -- tests/smoke/test_smoke_basic.py records it --
    # so this import raised at collection and took every test in this helper's one consumer
    # with it. The identity built below CARRIES the tenant, which is what production reads.

    # make_identity takes the principal and the tenant and nothing else. It refuses
    # unknown keywords by design rather than dropping them, so auth_token= and
    # protocol= are removed here instead of being silently ignored: the credential is
    # presented in a header the harness builds, and the transport is the harness's
    # choice, so neither is a property of the resolved identity.
    # The tenant context DERIVED from the row, through production's own constructor, so the
    # identity cannot describe a tenant the database disagrees with. It used to be built by
    # ``make_tenant(**tenant_dict)`` from the same dict that made the row -- a second
    # construction with its own defaults for every field the dict omitted.
    identity = PrincipalFactory.make_identity(
        principal_id=principal_id,
        tenant_id=tenant_id,
        tenant=TenantContext.from_orm_model(tenant),
    )
    return {
        "tenant": tenant,
        "principal": principal,
        "product": product,
        "identity": identity,
        "principal_id": principal_id,
        # The token that actually authenticates this principal, derived the one way
        # the resolver can match. The ``access_token`` parameter is kept in the
        # signature so existing callers still pass, but it cannot select the
        # credential any more — the hash in the row comes from the principal id.
        "access_token": plaintext_token_for(principal_id),
    }


@pytest.fixture
def live_media_buy_env(integration_db):
    """A ``MediaBuyDualEnv`` holding one ACTIVE, mid-flight media buy.

    Shared by the two honor-side graders
    (``test_spec_request_fields_accepted.py`` on the MCP path,
    ``test_raw_wrapper_spec_fields_accepted.py`` on the raw A2A/REST path), so
    the "a live buy that a cancellation must actually cancel" setup exists
    once rather than per file.

    Yields ``(env, media_buy)``. Callers that need the transport-generic
    dispatcher wrap the env in ``AdCPTestClient`` themselves: the env's own
    update dispatch flattens an ``UpdateMediaBuyRequest`` and pops
    ``canceled``/``cancellation_reason`` on the way to the wire
    (``tests/harness/media_buy_update.py`` ``_WRAPPER_UNSUPPORTED_FIELDS``),
    which is the harness accommodating the very defect under test.
    """
    from datetime import timedelta

    from tests.factories import MediaBuyFactory
    from tests.harness.media_buy_dual import MediaBuyDualEnv

    with MediaBuyDualEnv(tenant_id="honor-cancel", principal_id="test_principal") as env:
        tenant, principal, _product, _pricing_option = env.setup_media_buy_data()
        media_buy = MediaBuyFactory(
            tenant=tenant,
            principal=principal,
            status="active",
            currency="USD",
            start_time=datetime.now(UTC) - timedelta(days=1),
            end_time=datetime.now(UTC) + timedelta(days=30),
        )
        env._commit_factory_data()
        env._seeded_media_buy_id = media_buy.media_buy_id
        yield env, media_buy


@pytest.fixture
def bound_factory_session(integration_db):
    """A session bound to every factory for the duration of a test, then restored.

    Depends on ``integration_db`` for correctness, not convenience: that fixture
    creates a per-test database and REBINDS the global engine, and pytest
    instantiates same-scope fixtures in argument order. Without this dependency a
    test written as ``(bound_factory_session, integration_db)`` would bind factories
    to the pre-swap engine and write to the wrong database with nothing failing.
    The hand-rolled context manager this replaces could not have that bug — it was
    entered inside a fixture body that had already taken ``integration_db`` — so the
    ordering has to be declared here to keep the guarantee.

    The shared alternative to hand-rolling ``factory._meta.sqlalchemy_session = s``
    and nulling it afterwards. Nulling is only correct when the fixture is
    guaranteed outermost; ``bind_factories_to_session`` saves and restores the
    previous binding instead, which is correct either way.

    Owns the session as well as the binding: it creates it, binds it, and closes it,
    because a caller that only borrowed the binding would still have to manage the
    SASession itself and that is where the hand-rolled versions diverge.
    """
    from sqlalchemy.orm import Session as SASession

    from src.core.database.database_session import get_engine
    from tests.utils.database_helpers import bind_factories_to_session

    session = SASession(bind=get_engine())
    try:
        with bind_factories_to_session(session):
            yield session
    finally:
        session.close()


#: Integration tests ledgered pending rewrite — see ``known_failures.txt`` next to this
#: file for why each is listed and which issue owns it. Read through the SHARED loader the
#: bdd and storyboard ledgers use, so there is one parse of a nodeid ledger rather than a
#: third copy free to disagree with the other two about comments and blank lines.
_LEDGERED_NODEIDS: frozenset[str] = load_ledger_nodeids(Path(__file__).parent / "known_failures.txt")


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """xfail(strict=True) exactly the ledgered nodeids.

    STRICT, unlike the e2e_rest ledger. That one is deliberately non-strict because e2e
    dispatches over real HTTP to a separate server and "an environment-dependent xpass
    must not fail CI" (its own comment). These run in-process against the same real
    Postgres on every run, so there is no environment to be dependent on -- which means a
    ledgered test that starts passing must FAIL, or the ledger stops being a shrinking
    work-list and becomes a place where coverage goes quiet. That is the dormancy this
    repo has been bitten by often enough to have a graduation workflow for it
    (.claude/rules/workflows/xpass-graduation.md).

    An unmatched ledger entry is caught by ``tests/unit/test_integration_ledger_state.py``
    rather than here: this hook sees only the items the current selection collected, so a
    stale nodeid looks identical to one that was simply not selected.
    """
    for item in items:
        if item.nodeid in _LEDGERED_NODEIDS:
            item.add_marker(
                pytest.mark.xfail(
                    reason="ledgered pending rewrite (tests/integration/known_failures.txt)",
                    strict=True,
                )
            )
