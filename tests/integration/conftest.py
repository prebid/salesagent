"""
Integration test specific fixtures.

These fixtures are for tests that require database and service integration.
"""

import os
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from src.admin.app import create_app

admin_app, _ = create_app()
from src.core.database.database_session import get_db_session
from src.core.database.models import Tenant
from tests.fixtures import TenantFactory


@pytest.fixture(scope="function")  # Changed to function scope for better isolation
def integration_db():
    """Provide an isolated PostgreSQL database for each integration test.

    REQUIRES: PostgreSQL container running (via run_all_tests.sh ci or GitHub Actions)
    - Uses DATABASE_URL to get PostgreSQL connection info (host, port, user, password)
    - Database name in URL is ignored - creates a unique database per test (e.g., test_a3f8d92c)
    - Matches production environment exactly
    - Better multi-process support (fixes mcp_server tests)
    - Consistent JSONB behavior
    """
    import uuid

    # Save original DATABASE_URL
    original_url = os.environ.get("DATABASE_URL")
    original_db_type = os.environ.get("DB_TYPE")

    # Require PostgreSQL - no SQLite fallback
    postgres_url = os.environ.get("DATABASE_URL")
    if not postgres_url or not postgres_url.startswith("postgresql://"):
        pytest.skip(
            "Integration tests require PostgreSQL DATABASE_URL (e.g., postgresql://user:pass@localhost:5432/any_db)"
        )

    # PostgreSQL mode - create unique database per test
    unique_db_name = f"test_{uuid.uuid4().hex[:8]}"

    # Create the test database
    # Parse port from postgres_url (set by run_all_tests.sh or environment)
    import re

    import psycopg2
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

    pattern = r"postgresql://([^:]+):([^@]+)@([^:]+):(\d+)/(.+)"
    match = re.match(pattern, postgres_url)
    if match:
        user, password, host, port_str, _ = match.groups()
        postgres_port = int(port_str)
    else:
        # Fallback to defaults if URL parsing fails
        pytest.fail(
            f"Failed to parse DATABASE_URL: {postgres_url}\nExpected format: postgresql://user:pass@host:port/dbname"
        )
        user, password, host, postgres_port = "adcp_user", "test_password", "localhost", 5432

    conn_params = {
        "host": host,
        "port": postgres_port,
        "user": user,
        "password": password,
        "database": "postgres",  # Connect to default db first
    }

    conn = psycopg2.connect(**conn_params)
    conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    cur = conn.cursor()

    try:
        cur.execute(f'CREATE DATABASE "{unique_db_name}"')
    finally:
        cur.close()
        conn.close()

    os.environ["DATABASE_URL"] = f"postgresql://{user}:{password}@{host}:{postgres_port}/{unique_db_name}"
    os.environ["DB_TYPE"] = "postgresql"
    db_path = unique_db_name  # For cleanup reference

    # Create the database without running migrations
    # (migrations are for production, tests create tables directly)
    from sqlalchemy import create_engine
    from sqlalchemy.orm import scoped_session, sessionmaker

    # Import ALL models first, BEFORE using Base
    # This ensures all tables are registered in Base.metadata
    import src.core.database.models as all_models  # noqa: F401
    from src.core.database.models import Base, Context, ObjectWorkflowMapping, WorkflowStep  # noqa: F401

    # Explicitly ensure Context and workflow models are registered
    # (in case the module import doesn't trigger class definition)
    _ = (Context, WorkflowStep, ObjectWorkflowMapping)

    engine = create_engine(f"postgresql://{user}:{password}@{host}:{postgres_port}/{unique_db_name}", echo=False)

    # Ensure all model classes are imported and registered with Base.metadata
    # Import order matters - some models may not be registered if imported too early
    from src.core.database.models import (
        AdapterConfig,
        AuditLog,
        AuthorizedProperty,
        Creative,
        CreativeAssignment,
        FormatPerformanceMetrics,
        GAMInventory,
        GAMLineItem,
        GAMOrder,
        MediaBuy,
        Principal,
        Product,
        ProductInventoryMapping,
        PropertyTag,
        PushNotificationConfig,
        Strategy,
        StrategyState,
        SyncJob,
        Tenant,
        TenantManagementConfig,
        User,
    )

    # Ensure workflow models are loaded (force evaluation)
    _ = (
        Context,
        WorkflowStep,
        ObjectWorkflowMapping,
        Tenant,
        Principal,
        Product,
        MediaBuy,
        Creative,
        AuthorizedProperty,
        Strategy,
        AuditLog,
        CreativeAssignment,
        TenantManagementConfig,
        PushNotificationConfig,
        User,
        AdapterConfig,
        GAMInventory,
        ProductInventoryMapping,
        FormatPerformanceMetrics,
        GAMOrder,
        GAMLineItem,
        SyncJob,
        StrategyState,
        PropertyTag,
    )

    # Create all tables directly (no migrations)
    Base.metadata.create_all(bind=engine, checkfirst=True)

    # Reset engine and update globals to point to the test database
    from src.core.database.database_session import reset_engine

    reset_engine()

    # Now update the globals to use our test engine
    import src.core.database.database_session as db_session_module

    db_session_module._engine = engine
    db_session_module._session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db_session_module._scoped_session = scoped_session(db_session_module._session_factory)

    # Reset context manager singleton so it uses the new database session
    # This is critical because ContextManager caches a session reference
    import src.core.context_manager

    src.core.context_manager._context_manager_instance = None

    yield db_path

    # Reset engine to clean up test database connections
    reset_engine()

    # Reset context manager singleton again to avoid stale references
    src.core.context_manager._context_manager_instance = None

    # Cleanup
    engine.dispose()

    # Restore original environment
    if original_url:
        os.environ["DATABASE_URL"] = original_url
    else:
        del os.environ["DATABASE_URL"]

    if original_db_type:
        os.environ["DB_TYPE"] = original_db_type
    elif "DB_TYPE" in os.environ:
        del os.environ["DB_TYPE"]

    # Drop PostgreSQL test database
    try:
        conn = psycopg2.connect(**conn_params)
        conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        cur = conn.cursor()
        # Terminate connections to the test database
        cur.execute(
            f"""
            SELECT pg_terminate_backend(pg_stat_activity.pid)
            FROM pg_stat_activity
            WHERE pg_stat_activity.datname = '{db_path}'
            AND pid <> pg_backend_pid()
            """
        )
        cur.execute(f'DROP DATABASE IF EXISTS "{db_path}"')
        cur.close()
        conn.close()
    except Exception:
        pass  # Ignore cleanup errors


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
    """Create a test tenant in the database with proper configuration."""
    tenant_data = TenantFactory.create()
    now = datetime.now(UTC)

    with get_db_session() as db_session:
        tenant = Tenant(
            tenant_id=tenant_data["tenant_id"],
            name=tenant_data["name"],
            subdomain=tenant_data["subdomain"],
            is_active=tenant_data["is_active"],
            ad_server="mock",
            auto_approve_formats=[],  # JSONType expects list, not json.dumps()
            human_review_required=False,
            policy_settings={},  # JSONType expects dict, not json.dumps()
            created_at=now,
            updated_at=now,
        )
        db_session.add(tenant)
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
    """Create a sample tenant for testing."""
    from datetime import UTC, datetime

    from src.core.database.database_session import get_db_session
    from src.core.database.models import Tenant

    now = datetime.now(UTC)
    with get_db_session() as session:
        tenant = Tenant(
            tenant_id="test_tenant",
            name="Test Tenant",
            subdomain="test",
            is_active=True,
            ad_server="mock",
            enable_axe_signals=True,
            authorized_emails=["test@example.com"],
            authorized_domains=["example.com"],
            auto_approve_formats=["display_300x250"],
            human_review_required=False,
            admin_token="test_admin_token",
            created_at=now,
            updated_at=now,
        )
        session.add(tenant)
        session.commit()

        return {
            "tenant_id": tenant.tenant_id,
            "name": tenant.name,
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
            platform_mappings={"mock": {"id": "test_advertiser"}},
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
    from src.core.database.models import Product

    with get_db_session() as session:
        products = [
            Product(
                tenant_id=sample_tenant["tenant_id"],
                product_id="guaranteed_display",
                name="Guaranteed Display Ads",
                description="Premium guaranteed display advertising",
                formats=["display_300x250", "display_728x90"],  # Format IDs only, not objects
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
            ),
            Product(
                tenant_id=sample_tenant["tenant_id"],
                product_id="non_guaranteed_video",
                name="Non-Guaranteed Video",
                description="Programmatic video advertising",
                formats=["video_15s", "video_30s"],  # Format IDs only, not objects
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

        return [p.product_id for p in products]


@pytest.fixture
def mock_external_apis():
    """Mock external APIs but allow database access."""
    with patch("requests.post") as mock_post:
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {"status": "ok"}

        with patch("google.generativeai.configure"):
            with patch("google.generativeai.GenerativeModel") as mock_model:
                mock_instance = MagicMock()
                mock_instance.generate_content.return_value.text = "AI generated content"
                mock_model.return_value = mock_instance

                yield {"requests": mock_post, "gemini": mock_instance}


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

    # Don't remove db_name - the PostgreSQL database is managed by integration_db fixture


@pytest.fixture
def test_admin_app(integration_db):
    """Provide a test Admin UI app with real database."""
    # integration_db ensures database tables are created
    from src.admin.app import create_app

    app, _ = create_app()
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
