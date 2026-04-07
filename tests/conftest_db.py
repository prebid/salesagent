"""Database setup for tests - ensures proper initialization."""

import os
from datetime import UTC
from pathlib import Path

import pytest
from sqlalchemy import text

# Set test mode before any imports
os.environ["PYTEST_CURRENT_TEST"] = "true"


@pytest.fixture(scope="session")
def test_database_url():
    """Get PostgreSQL test database URL.

    REQUIRES: PostgreSQL container running (via run_all_tests.sh ci)
    """
    # Use TEST_DATABASE_URL if set, otherwise DATABASE_URL (for CI)
    url = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")

    if not url:
        pytest.skip("Tests require PostgreSQL. Run: ./run_all_tests.sh ci")

    if "postgresql" not in url:
        pytest.skip(f"Tests require PostgreSQL, got: {url.split('://')[0]}. Run: ./run_all_tests.sh ci")

    return url


@pytest.fixture(scope="session")
def test_database(test_database_url):
    """Create and initialize test database once per session."""
    # Set the database URL for the application
    os.environ["DATABASE_URL"] = test_database_url
    os.environ["DB_TYPE"] = "postgresql"

    # Import all models FIRST
    from sqlalchemy import create_engine
    from sqlalchemy.orm import scoped_session, sessionmaker

    from src.core.database.models import (  # noqa: F401
        AdapterConfig,
        AuditLog,
        AuthorizedProperty,
        Base,
        Context,
        Creative,
        CreativeAssignment,
        # CreativeFormat removed - table dropped in migration f2addf453200
        FormatPerformanceMetrics,
        GAMInventory,
        GAMLineItem,
        GAMOrder,
        MediaBuy,
        ObjectWorkflowMapping,
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
        WorkflowStep,
    )

    # Create a new engine for the test database (don't use get_engine())
    # This ensures we use the correct DATABASE_URL set above
    engine = create_engine(test_database_url, echo=False)

    # Run migrations for PostgreSQL
    import subprocess

    result = subprocess.run(
        ["python3", "scripts/ops/migrate.py"], capture_output=True, text=True, cwd=Path(__file__).parent.parent
    )
    if result.returncode != 0:
        pytest.skip(f"Migration failed: {result.stderr}")

    # Reset any existing engine and force initialization with test database
    from src.core.database.database_session import reset_engine

    reset_engine()

    # Now update the globals to use our test engine
    import src.core.database.database_session as db_session_module

    db_session_module._engine = engine
    db_session_module._session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db_session_module._scoped_session = scoped_session(db_session_module._session_factory)

    # Initialize with test data
    from src.core.database.database import init_db

    init_db(exit_on_error=False)

    yield test_database_url

    # Cleanup is automatic for in-memory database


@pytest.fixture(scope="function")
def db_session(test_database):
    """Provide a database session for tests."""
    from src.core.database.database_session import get_db_session

    with get_db_session() as session:
        yield session
        session.rollback()  # Rollback any changes made during test


@pytest.fixture(scope="function")
def clean_db(test_database):
    """Provide a clean database for each test."""
    from src.core.database.database_session import get_engine

    engine = get_engine()

    # Clear all data but keep schema
    with engine.connect() as conn:
        # Define deletion order to handle foreign key constraints properly
        # Tables with foreign keys should be deleted before their referenced tables
        deletion_order = [
            # Tables that reference other tables via foreign keys
            "strategy_states",
            "object_workflow_mapping",
            "workflow_steps",
            "contexts",
            "sync_jobs",
            "gam_line_items",
            "gam_orders",
            "product_inventory_mappings",
            "gam_inventory",
            "adapter_config",
            "audit_logs",
            "media_buys",
            "creative_assignments",
            "creatives",
            "users",
            "principals",
            "products",
            "creative_formats",
            "strategies",
            # Base tables with no dependencies
            "tenants",
            "superadmin_config",
        ]

        # Get all existing table names
        inspector = inspect(engine)
        existing_tables = set(inspector.get_table_names())

        # Delete data from tables in proper order
        for table in deletion_order:
            if table in existing_tables and table != "alembic_version":
                try:
                    conn.execute(text(f"DELETE FROM {table}"))
                except Exception as e:
                    # Log but continue - some tables might not exist in all test scenarios
                    logger.debug(f"Could not delete from {table}: {e}")

        # Delete any remaining tables not in our explicit order
        for table in existing_tables:
            if table not in deletion_order and table != "alembic_version":
                try:
                    conn.execute(text(f"DELETE FROM {table}"))
                except Exception as e:
                    logger.debug(f"Could not delete from remaining table {table}: {e}")

        conn.commit()

    # Re-initialize with test data
    from src.core.database.database import init_db

    init_db(exit_on_error=False)

    yield

    # Cleanup happens automatically at function scope


@pytest.fixture
def test_tenant(db_session):
    """Create a test tenant."""
    import uuid
    from datetime import datetime

    from src.core.database.models import Tenant

    # Generate unique tenant data for each test
    unique_id = str(uuid.uuid4())[:8]

    # Explicitly set created_at and updated_at to avoid database constraint violations
    now = datetime.now(UTC)
    tenant = Tenant(
        tenant_id=f"test_tenant_{unique_id}",
        name=f"Test Tenant {unique_id}",
        subdomain=f"test_{unique_id}",
        is_active=True,
        ad_server="mock",
        created_at=now,
        updated_at=now,
        # Set default measurement provider (Publisher Ad Server)
        measurement_providers={"providers": ["Publisher Ad Server"], "default": "Publisher Ad Server"},
    )
    db_session.add(tenant)
    db_session.commit()

    return tenant


@pytest.fixture
def test_principal(db_session, test_tenant):
    """Create a test principal."""
    import uuid

    from src.core.database.models import Principal

    unique_id = str(uuid.uuid4())[:8]

    principal = Principal(
        tenant_id=test_tenant.tenant_id,
        principal_id=f"test_principal_{unique_id}",
        name=f"Test Principal {unique_id}",
        access_token=f"test_token_{unique_id}",
        platform_mappings={"mock": {"advertiser_id": f"test_advertiser_{unique_id}"}},
    )
    db_session.add(principal)
    db_session.commit()

    return principal


@pytest.fixture
def test_product(db_session, test_tenant):
    """Create a test product."""
    import uuid

    from src.core.database.models import Product

    unique_id = str(uuid.uuid4())[:8]

    product = Product(
        product_id=f"test_product_{unique_id}",
        tenant_id=test_tenant.tenant_id,
        name=f"Test Product {unique_id}",
        format_ids=[
            {"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"},
        ],
        targeting_template={},
        delivery_type="guaranteed",
        property_tags=["all_inventory"],  # Required: products must have properties OR property_tags
    )
    db_session.add(product)
    db_session.commit()

    return product


@pytest.fixture
def test_audit_log(db_session, test_tenant, test_principal):
    """Create a test audit log entry."""
    from datetime import UTC, datetime

    from src.core.database.models import AuditLog

    # Create a minimal audit log without strategy_id (which may not exist in all test environments)
    audit_log = AuditLog(
        tenant_id=test_tenant.tenant_id,
        principal_id=test_principal.principal_id,
        principal_name=test_principal.name,
        operation="get_products",
        timestamp=datetime.now(UTC),
        success=True,
        details={"product_count": 3, "brief": "Test query"},
        # Note: Omitting strategy_id as it may not exist in all test database schemas
    )
    db_session.add(audit_log)
    db_session.commit()

    return audit_log


@pytest.fixture
def test_media_buy(db_session, test_tenant, test_principal, test_product):
    """Create a test media buy."""
    import uuid
    from datetime import datetime, timedelta

    from src.core.database.models import MediaBuy

    unique_id = str(uuid.uuid4())[:8]
    now = datetime.now(UTC)
    media_buy = MediaBuy(
        media_buy_id=f"test_media_buy_{unique_id}",
        tenant_id=test_tenant.tenant_id,
        principal_id=test_principal.principal_id,
        order_name=f"Test Order {unique_id}",
        advertiser_name=f"Test Advertiser {unique_id}",
        budget=1000.00,
        start_date=(now + timedelta(days=1)).date(),
        end_date=(now + timedelta(days=8)).date(),
        status="active",
        raw_request={"test": "data"},  # Required field
    )
    db_session.add(media_buy)
    db_session.commit()

    return media_buy


@pytest.fixture
def auth_headers(test_principal):
    """Get auth headers for testing."""
    return {"x-adcp-auth": test_principal.access_token}


def _parse_postgres_url(url: str) -> dict:
    """Parse PostgreSQL URL into connection components."""
    import re

    pattern = r"postgresql://([^:]+):([^@]+)@([^:]+):(\d+)/(.+)"
    match = re.match(pattern, url)
    if not match:
        raise ValueError(f"Cannot parse DATABASE_URL: {url}")
    user, password, host, port_str, dbname = match.groups()
    return {"user": user, "password": password, "host": host, "port": int(port_str), "dbname": dbname}


def _pg_admin_conn(parsed: dict):
    """Open an AUTOCOMMIT connection to the 'postgres' admin database."""
    import psycopg2
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

    conn = psycopg2.connect(
        host=parsed["host"], port=parsed["port"], user=parsed["user"], password=parsed["password"], database="postgres"
    )
    conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    return conn


def _build_url(parsed: dict, dbname: str) -> str:
    """Reconstruct a PostgreSQL URL with a different database name."""
    return f"postgresql://{parsed['user']}:{parsed['password']}@{parsed['host']}:{parsed['port']}/{dbname}"


def _drop_database(parsed: dict, dbname: str) -> None:
    """Terminate connections and drop a PostgreSQL database (best-effort)."""
    try:
        conn = _pg_admin_conn(parsed)
        cur = conn.cursor()
        cur.execute(
            f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            f"WHERE datname = '{dbname}' AND pid <> pg_backend_pid()"
        )
        cur.execute(f'DROP DATABASE IF EXISTS "{dbname}"')
        cur.close()
        conn.close()
    except Exception:
        pass


@pytest.fixture(scope="session")
def migration_template():
    """Create a session-scoped template database with Alembic migrations applied.

    Run once per test session. Per-test databases clone from this template
    via ``CREATE DATABASE ... TEMPLATE``, which is near-instant (filesystem
    copy, no SQL replay).

    This ensures integration tests use the **exact same schema** as
    production (Alembic migrations), eliminating schema drift from
    ``Base.metadata.create_all()``.
    """
    import uuid

    postgres_url = os.environ.get("DATABASE_URL")
    if not postgres_url or not postgres_url.startswith("postgresql://"):
        pytest.skip("Integration tests require PostgreSQL DATABASE_URL")

    parsed = _parse_postgres_url(postgres_url)
    template_name = f"tpl_{uuid.uuid4().hex[:8]}"

    # 1. Create the template database
    conn = _pg_admin_conn(parsed)
    cur = conn.cursor()
    try:
        cur.execute(f'CREATE DATABASE "{template_name}"')
    finally:
        cur.close()
        conn.close()

    # 2. Run Alembic migrations against the template
    template_url = _build_url(parsed, template_name)
    original_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = template_url
    try:
        from alembic.config import Config

        from alembic import command

        alembic_cfg = Config("alembic.ini")
        alembic_cfg.set_main_option("sqlalchemy.url", template_url)
        command.upgrade(alembic_cfg, "head")
    finally:
        if original_url:
            os.environ["DATABASE_URL"] = original_url

    yield {"parsed": parsed, "template_name": template_name}

    # 3. Drop the template database at session end
    _drop_database(parsed, template_name)


@pytest.fixture(scope="function")
def integration_db(migration_template):
    """Provide an isolated PostgreSQL database for each integration test.

    Clones the session-scoped Alembic template database (near-instant)
    instead of running ``Base.metadata.create_all()`` or re-running
    migrations per test. Schema matches production exactly.

    REQUIRES: PostgreSQL container running (via run_all_tests.sh ci or GitHub Actions)
    """
    import uuid

    parsed = migration_template["parsed"]
    template_name = migration_template["template_name"]
    unique_db_name = f"test_{uuid.uuid4().hex[:8]}"

    # Save original env
    original_url = os.environ.get("DATABASE_URL")
    original_db_type = os.environ.get("DB_TYPE")

    # Clone from template (near-instant filesystem copy)
    conn = _pg_admin_conn(parsed)
    cur = conn.cursor()
    try:
        cur.execute(f'CREATE DATABASE "{unique_db_name}" TEMPLATE "{template_name}"')
    finally:
        cur.close()
        conn.close()

    test_url = _build_url(parsed, unique_db_name)
    os.environ["DATABASE_URL"] = test_url
    os.environ["DB_TYPE"] = "postgresql"

    from sqlalchemy import create_engine
    from sqlalchemy.orm import scoped_session, sessionmaker

    from src.core.database.database_session import _pydantic_json_serializer, reset_engine

    engine = create_engine(test_url, echo=False, json_serializer=_pydantic_json_serializer)

    reset_engine()

    import src.core.database.database_session as db_session_module

    db_session_module._engine = engine
    db_session_module._session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db_session_module._scoped_session = scoped_session(db_session_module._session_factory)

    import src.core.context_manager

    src.core.context_manager._context_manager_instance = None

    yield unique_db_name

    # Cleanup
    reset_engine()
    src.core.context_manager._context_manager_instance = None
    engine.dispose()

    # Restore original environment
    if original_url:
        os.environ["DATABASE_URL"] = original_url
    else:
        os.environ.pop("DATABASE_URL", None)
    if original_db_type:
        os.environ["DB_TYPE"] = original_db_type
    else:
        os.environ.pop("DB_TYPE", None)

    # Drop the per-test database
    _drop_database(parsed, unique_db_name)


# Import inspect only when needed
def inspect(engine):
    """Lazy import of Inspector."""
    from sqlalchemy import inspect as sqlalchemy_inspect

    return sqlalchemy_inspect(engine)
