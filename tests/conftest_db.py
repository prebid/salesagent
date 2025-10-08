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
        CreativeFormat,
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
    from scripts.setup.init_database import init_db

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
    from scripts.setup.init_database import init_db

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
        formats=["display_300x250"],
        targeting_template={},
        delivery_type="guaranteed",
        is_fixed_price=True,
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


# Import inspect only when needed
def inspect(engine):
    """Lazy import of Inspector."""
    from sqlalchemy import inspect as sqlalchemy_inspect

    return sqlalchemy_inspect(engine)
