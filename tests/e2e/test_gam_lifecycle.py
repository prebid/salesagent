"""
Real GAM lifecycle E2E tests that create and manage orders via the GAM API.

These tests require:
1. Valid GAM credentials (service account) — set GAM_SERVICE_ACCOUNT_JSON or GAM_SERVICE_ACCOUNT_KEY_FILE
2. PostgreSQL database — set DATABASE_URL

They are gated behind @pytest.mark.requires_gam and skip when either is absent.

Test network: 23341594478 (XFP sandbox property)
Service account: salesagent-e2e@salesagenttest.iam.gserviceaccount.com

WARNING: These tests create real GAM orders. Cleanup happens in fixture teardown,
but if the process is killed, orphaned orders may remain in the test network.

Run with:
    DATABASE_URL=postgresql://... uv run pytest tests/e2e/test_gam_lifecycle.py -v
"""

import os
import re
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from tests.e2e.conftest import (
    GAM_TEST_AD_UNIT_IDS,
    GAM_TEST_ADVERTISER_ID,
    GAM_TEST_NETWORK_CODE,
)

GAM_LIFECYCLE_TENANT_ID = "gam_lifecycle_test"

# Module-level engine reference (set by gam_lifecycle_db, restored by _preserve_db)
_lifecycle_engine = None
_lifecycle_db_url = None


@pytest.fixture(autouse=True)
def _preserve_db(request):
    """Counteract the root conftest test_environment fixture.

    The root test_environment (function-scoped, autouse) deletes DATABASE_URL
    for non-integration tests and calls reset_engine() on teardown. This breaks
    our module-scoped gam_lifecycle_db fixture. Re-establish the engine connection
    before each test and prevent teardown from wiping it.
    """
    if _lifecycle_engine is None:
        return  # DB not set up yet — let test_environment do its thing

    import src.core.database.database_session as db_mod

    os.environ["DATABASE_URL"] = _lifecycle_db_url
    os.environ["DB_TYPE"] = "postgresql"

    # Restore engine if it was reset by previous test's teardown
    if db_mod._engine is None:
        from sqlalchemy.orm import scoped_session, sessionmaker

        db_mod._engine = _lifecycle_engine
        db_mod._session_factory = sessionmaker(autocommit=False, autoflush=False, bind=_lifecycle_engine)
        db_mod._scoped_session = scoped_session(db_mod._session_factory)

    yield

    # After test: prevent reset_engine from nuking the shared engine
    # The root conftest calls reset_engine() in its teardown, which runs AFTER ours.
    # We can't prevent that, but we restore on next test's setup above.


# ---------------------------------------------------------------------------
# Database fixtures — create a fresh PostgreSQL DB with test products
# ---------------------------------------------------------------------------


def _parse_database_url(url: str) -> dict:
    """Parse DATABASE_URL into connection params."""
    pattern = r"postgresql://([^:]+):([^@]+)@([^:]+):(\d+)/(.+)"
    match = re.match(pattern, url)
    if not match:
        return {}
    user, password, host, port, db = match.groups()
    return {"user": user, "password": password, "host": host, "port": int(port), "database": db}


@pytest.fixture(scope="module")
def gam_lifecycle_db(gam_service_account_json):
    """Create an isolated PostgreSQL database for GAM lifecycle tests.

    Requires DATABASE_URL pointing to a PostgreSQL server.
    Creates a unique database, sets up tables, seeds test data, and cleans up after.
    """
    import psycopg2
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

    postgres_url = os.environ.get("DATABASE_URL")
    if not postgres_url or not postgres_url.startswith("postgresql://"):
        pytest.skip("GAM lifecycle tests require PostgreSQL DATABASE_URL")

    params = _parse_database_url(postgres_url)
    if not params:
        pytest.skip(f"Failed to parse DATABASE_URL: {postgres_url}")

    unique_db_name = f"gam_lifecycle_{uuid.uuid4().hex[:8]}"

    # Save original env
    original_url = os.environ.get("DATABASE_URL")
    original_db_type = os.environ.get("DB_TYPE")

    # Create the test database
    conn_params = {
        "host": params["host"],
        "port": params["port"],
        "user": params["user"],
        "password": params["password"],
        "database": "postgres",
    }

    conn = psycopg2.connect(**conn_params)
    conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    cur = conn.cursor()
    try:
        cur.execute(f'CREATE DATABASE "{unique_db_name}"')
    finally:
        cur.close()
        conn.close()

    test_url = f"postgresql://{params['user']}:{params['password']}@{params['host']}:{params['port']}/{unique_db_name}"
    os.environ["DATABASE_URL"] = test_url
    os.environ["DB_TYPE"] = "postgresql"

    # Create tables and seed data
    from sqlalchemy import create_engine
    from sqlalchemy.orm import scoped_session, sessionmaker

    import src.core.database.models as all_models  # noqa: F401
    from src.core.database.database_session import _pydantic_json_serializer, reset_engine
    from src.core.database.models import Base

    engine = create_engine(test_url, echo=False, json_serializer=_pydantic_json_serializer)
    Base.metadata.create_all(bind=engine, checkfirst=True)

    # Point the app's DB session at our test database
    reset_engine()
    import src.core.database.database_session as db_session_module

    db_session_module._engine = engine
    db_session_module._session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db_session_module._scoped_session = scoped_session(db_session_module._session_factory)

    import src.core.context_manager

    src.core.context_manager._context_manager_instance = None

    # Save module-level references for _preserve_db fixture
    global _lifecycle_engine, _lifecycle_db_url
    _lifecycle_engine = engine
    _lifecycle_db_url = test_url

    # Seed test data
    _seed_lifecycle_test_data()

    yield unique_db_name

    _lifecycle_engine = None
    _lifecycle_db_url = None

    # Teardown
    reset_engine()
    src.core.context_manager._context_manager_instance = None
    engine.dispose()

    # Restore original env
    if original_url:
        os.environ["DATABASE_URL"] = original_url
    else:
        os.environ.pop("DATABASE_URL", None)
    if original_db_type:
        os.environ["DB_TYPE"] = original_db_type
    else:
        os.environ.pop("DB_TYPE", None)

    # Drop the test database
    try:
        conn = psycopg2.connect(**conn_params)
        conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        cur = conn.cursor()
        cur.execute(
            f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            f"WHERE datname = '{unique_db_name}' AND pid <> pg_backend_pid()"
        )
        cur.execute(f'DROP DATABASE IF EXISTS "{unique_db_name}"')
        cur.close()
        conn.close()
    except Exception:
        pass


def _seed_lifecycle_test_data():
    """Seed the test database with tenant, principal, products, and inventory mappings."""
    from src.core.database.database_session import get_db_session
    from src.core.database.models import (
        GAMInventory,
        Product,
        ProductInventoryMapping,
        Tenant,
    )
    from src.core.database.models import Principal as PrincipalModel

    with get_db_session() as session:
        # Create tenant
        tenant = Tenant(
            tenant_id=GAM_LIFECYCLE_TENANT_ID,
            name="GAM Lifecycle Test Tenant",
            subdomain="gam-lifecycle-test",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            creative_auto_approve_threshold=0.8,
            creative_auto_reject_threshold=0.2,
            brand_manifest_policy="flexible",
            auth_setup_mode=True,
        )
        session.add(tenant)

        # Create principal (required by MediaBuy FK)
        principal = PrincipalModel(
            tenant_id=GAM_LIFECYCLE_TENANT_ID,
            principal_id="e2e_lifecycle_test",
            name="E2E Lifecycle Test Principal",
            platform_mappings={
                "google_ad_manager": {"advertiser_id": GAM_TEST_ADVERTISER_ID},
            },
            access_token=f"e2e_test_token_{uuid.uuid4().hex[:8]}",
        )
        session.add(principal)

        # Create GAM inventory records for test ad units
        for ad_unit_id in GAM_TEST_AD_UNIT_IDS:
            inv = GAMInventory(
                tenant_id=GAM_LIFECYCLE_TENANT_ID,
                inventory_type="AD_UNIT",
                inventory_id=ad_unit_id,
                name=f"Test Ad Unit {ad_unit_id}",
                status="ACTIVE",
                last_synced=datetime.now(UTC),
            )
            session.add(inv)

        # Non-guaranteed product (CPM → PRICE_PRIORITY line item)
        product_non_guaranteed = Product(
            tenant_id=GAM_LIFECYCLE_TENANT_ID,
            product_id="gam_e2e_non_guaranteed",
            name="E2E Non-Guaranteed CPM",
            format_ids=[],
            targeting_template={},
            delivery_type="non_guaranteed",
            property_tags=["all_inventory"],
            implementation_config={
                "order_name_template": "E2E-NONGUAR-{po_number}-{timestamp}",
                "line_item_type": "PRICE_PRIORITY",
                "priority": 12,
                "cost_type": "CPM",
                "creative_rotation_type": "EVEN",
                "delivery_rate_type": "EVENLY",
                "primary_goal_type": "LIFETIME",
                "primary_goal_unit_type": "IMPRESSIONS",
                "creative_placeholders": [{"width": 300, "height": 250, "expected_creative_count": 1}],
                "targeted_ad_unit_ids": [str(ad_id) for ad_id in GAM_TEST_AD_UNIT_IDS],
            },
        )
        session.add(product_non_guaranteed)

        # Guaranteed product (CPM + guaranteed → STANDARD line item)
        product_guaranteed = Product(
            tenant_id=GAM_LIFECYCLE_TENANT_ID,
            product_id="gam_e2e_guaranteed",
            name="E2E Guaranteed CPM",
            format_ids=[],
            targeting_template={},
            delivery_type="guaranteed",
            property_tags=["all_inventory"],
            implementation_config={
                "order_name_template": "E2E-GUAR-{po_number}-{timestamp}",
                "line_item_type": "STANDARD",
                "priority": 8,
                "cost_type": "CPM",
                "creative_rotation_type": "EVEN",
                "delivery_rate_type": "EVENLY",
                "primary_goal_type": "LIFETIME",
                "primary_goal_unit_type": "IMPRESSIONS",
                "creative_placeholders": [{"width": 300, "height": 250, "expected_creative_count": 1}],
                "targeted_ad_unit_ids": [str(ad_id) for ad_id in GAM_TEST_AD_UNIT_IDS],
            },
        )
        session.add(product_guaranteed)

        # HOUSE product for archive lifecycle test
        product_house = Product(
            tenant_id=GAM_LIFECYCLE_TENANT_ID,
            product_id="gam_e2e_house",
            name="E2E House for Archive",
            format_ids=[],
            targeting_template={},
            delivery_type="non_guaranteed",
            property_tags=["all_inventory"],
            implementation_config={
                "order_name_template": "E2E-HOUSE-{po_number}-{timestamp}",
                "line_item_type": "HOUSE",
                "priority": 16,
                "cost_type": "CPM",
                "creative_rotation_type": "EVEN",
                "delivery_rate_type": "EVENLY",
                "primary_goal_type": "LIFETIME",
                "primary_goal_unit_type": "IMPRESSIONS",
                "creative_placeholders": [{"width": 300, "height": 250, "expected_creative_count": 1}],
                "targeted_ad_unit_ids": [str(ad_id) for ad_id in GAM_TEST_AD_UNIT_IDS],
            },
        )
        session.add(product_house)

        session.flush()

        # Create inventory mappings for all products
        for product_id in ["gam_e2e_non_guaranteed", "gam_e2e_guaranteed", "gam_e2e_house"]:
            for ad_unit_id in GAM_TEST_AD_UNIT_IDS:
                mapping = ProductInventoryMapping(
                    tenant_id=GAM_LIFECYCLE_TENANT_ID,
                    product_id=product_id,
                    inventory_type="AD_UNIT",
                    inventory_id=ad_unit_id,
                )
                session.add(mapping)

        session.commit()


# ---------------------------------------------------------------------------
# Adapter fixture — GoogleAdManager instance with service account
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def gam_adapter(gam_lifecycle_db, gam_service_account_json):
    """Create a GoogleAdManager adapter connected to the test network with DB access."""
    from src.adapters.google_ad_manager import GoogleAdManager

    config = {
        "service_account_json": gam_service_account_json,
        "network_code": GAM_TEST_NETWORK_CODE,
    }

    principal = MagicMock()
    principal.tenant_id = GAM_LIFECYCLE_TENANT_ID
    principal.principal_id = "e2e_lifecycle_test"
    principal.platform_mappings = {"gam_advertiser_id": GAM_TEST_ADVERTISER_ID}

    adapter = GoogleAdManager(
        config=config,
        principal=principal,
        network_code=GAM_TEST_NETWORK_CODE,
        advertiser_id=GAM_TEST_ADVERTISER_ID,
        dry_run=False,
        tenant_id=GAM_LIFECYCLE_TENANT_ID,
    )

    return adapter


# ---------------------------------------------------------------------------
# Order cleanup fixture — archives GAM orders even on test failure
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def gam_order_cleanup(gam_adapter):
    """Track and clean up GAM orders created during tests.

    Yields a list. Tests append order IDs to it.
    Teardown archives all tracked orders via direct GAM API.
    """
    created_orders: list[str] = []

    yield created_orders

    # Teardown: archive all created orders via direct API
    # (orders_manager.archive_order has a SOAP .get() compatibility issue)
    from googleads import ad_manager as gam_module

    try:
        order_service = gam_adapter.client_manager.get_service("OrderService")
    except Exception:
        return

    for order_id in created_orders:
        try:
            archive_action = {"xsi_type": "ArchiveOrders"}
            sb = gam_module.StatementBuilder()
            sb.Where("id = :orderId").WithBindVariable("orderId", int(order_id))
            order_service.performOrderAction(archive_action, sb.ToStatement())
        except Exception as e:
            print(f"Warning: Failed to archive order {order_id}: {e}")


# ---------------------------------------------------------------------------
# Helper to build a CreateMediaBuyRequest
# ---------------------------------------------------------------------------


def _make_create_request(product_id: str, po_number: str, delivery_type: str = "non_guaranteed"):
    """Build a CreateMediaBuyRequest, MediaPackage, and pricing info for a given product."""
    from src.core.schemas import CreateMediaBuyRequest, MediaPackage

    start_time = datetime.now(UTC) + timedelta(hours=1)
    end_time = start_time + timedelta(days=7)

    pkg_id = f"pkg_{product_id}"
    package = MediaPackage(
        package_id=pkg_id,
        product_id=product_id,
        name=f"E2E Test Package ({product_id})",
        delivery_type=delivery_type,
        impressions=1000,
        cpm=1.00,
        format_ids=[],
    )

    request = CreateMediaBuyRequest(
        brand_manifest={"name": "E2E Lifecycle Test"},
        buyer_ref=f"e2e_{po_number}",
        po_number=po_number,
        start_time=start_time,
        end_time=end_time,
    )

    # Pricing info that _create_media_buy_impl normally resolves from PricingOption DB records
    pricing_info = {
        pkg_id: {
            "pricing_model": "cpm",
            "rate": 1.00,
            "currency": "USD",
            "is_fixed": True,
        }
    }

    return request, [package], start_time, end_time, pricing_info


def _persist_media_buy(response, request, packages, start_time, end_time):
    """Persist a media buy to the DB after GAM order creation.

    This is normally done by media_buy_create.py. The lifecycle tests need
    DB records so update_media_buy can find the order's packages and line items.
    """
    from src.core.database.database_session import get_db_session
    from src.core.database.models import MediaBuy as MediaBuyModel
    from src.core.database.models import MediaPackage as MediaPackageModel

    with get_db_session() as session:
        media_buy = MediaBuyModel(
            media_buy_id=response.media_buy_id,
            tenant_id=GAM_LIFECYCLE_TENANT_ID,
            principal_id="e2e_lifecycle_test",
            buyer_ref=request.buyer_ref,
            order_name=f"E2E Order {response.media_buy_id}",
            advertiser_name="E2E Test Advertiser",
            budget=10.00,
            currency="USD",
            start_date=start_time.date(),
            end_date=end_time.date(),
            start_time=start_time,
            end_time=end_time,
            status="approved",
            raw_request={"brand_manifest": {"name": "E2E Lifecycle Test"}, "buyer_ref": request.buyer_ref},
        )
        session.add(media_buy)
        session.flush()

        # Get platform_line_item_ids from the response
        platform_ids = getattr(response, "_platform_line_item_ids", {})

        for pkg in packages:
            media_package = MediaPackageModel(
                media_buy_id=response.media_buy_id,
                package_id=pkg.package_id,
                package_config={
                    "package_id": pkg.package_id,
                    "product_id": pkg.product_id,
                    "name": pkg.name,
                    "platform_line_item_id": platform_ids.get(pkg.package_id),
                },
            )
            session.add(media_package)

        session.commit()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.requires_gam
class TestGAMOrderCreation:
    """Test creating real GAM orders via the adapter."""

    def test_create_non_guaranteed_order(self, gam_adapter, gam_order_cleanup):
        """Non-guaranteed product with CPM creates a PRICE_PRIORITY order in GAM."""
        from src.core.schemas import CreateMediaBuySuccess

        request, packages, start_time, end_time, pricing_info = _make_create_request(
            "gam_e2e_non_guaranteed", f"E2E-NG-{uuid.uuid4().hex[:6]}"
        )

        response = gam_adapter.create_media_buy(request, packages, start_time, end_time, pricing_info)

        assert isinstance(response, CreateMediaBuySuccess), f"Expected success, got: {response}"
        assert response.media_buy_id is not None, "Order ID should be set"
        assert len(response.packages) == 1

        gam_order_cleanup.append(response.media_buy_id)

        # Verify the order exists in GAM via direct API (orders_manager.get_order_status
        # has a SOAP response compatibility issue)
        from googleads import ad_manager as gam_module

        from src.adapters.gam.utils.constants import GAM_API_VERSION

        order_service = gam_adapter.client_manager.get_service("OrderService")
        sb = gam_module.StatementBuilder(version=GAM_API_VERSION)
        sb.Where("id = :id").WithBindVariable("id", int(response.media_buy_id))
        result = order_service.getOrdersByStatement(sb.ToStatement())

        assert "results" in result and len(result["results"]) > 0
        order = result["results"][0]
        assert order["status"] in ("APPROVED", "READY", "DELIVERING"), f"Unexpected order status: {order['status']}"

    def test_create_guaranteed_order(self, gam_adapter, gam_order_cleanup):
        """Guaranteed product creates a STANDARD order that needs activation approval."""
        from src.core.schemas import CreateMediaBuySuccess

        request, packages, start_time, end_time, pricing_info = _make_create_request(
            "gam_e2e_guaranteed", f"E2E-GR-{uuid.uuid4().hex[:6]}", delivery_type="guaranteed"
        )

        response = gam_adapter.create_media_buy(request, packages, start_time, end_time, pricing_info)

        assert isinstance(response, CreateMediaBuySuccess), f"Expected success, got: {response}"
        assert response.media_buy_id is not None, "Order ID should be set"

        gam_order_cleanup.append(response.media_buy_id)

        # Guaranteed orders should have line items of type STANDARD
        from googleads import ad_manager as gam_module

        lineitem_service = gam_adapter.client_manager.get_service("LineItemService")
        sb = gam_module.StatementBuilder()
        sb.Where("orderId = :orderId").WithBindVariable("orderId", int(response.media_buy_id))
        result = lineitem_service.getLineItemsByStatement(sb.ToStatement())

        line_items = result["results"] if "results" in result else []
        assert len(line_items) > 0, "Order should have line items"
        for li in line_items:
            assert li["lineItemType"] == "STANDARD", f"Expected STANDARD, got {li['lineItemType']}"

    def test_order_has_correct_advertiser(self, gam_adapter, gam_order_cleanup):
        """Created orders belong to the configured advertiser."""
        from src.core.schemas import CreateMediaBuySuccess

        request, packages, start_time, end_time, pricing_info = _make_create_request(
            "gam_e2e_non_guaranteed", f"E2E-ADV-{uuid.uuid4().hex[:6]}"
        )

        response = gam_adapter.create_media_buy(request, packages, start_time, end_time, pricing_info)

        assert isinstance(response, CreateMediaBuySuccess)
        gam_order_cleanup.append(response.media_buy_id)

        # Verify advertiser via direct GAM API
        from googleads import ad_manager

        from src.adapters.gam.utils.constants import GAM_API_VERSION

        order_service = gam_adapter.client_manager.get_service("OrderService")
        sb = ad_manager.StatementBuilder(version=GAM_API_VERSION)
        sb.Where("id = :id").WithBindVariable("id", int(response.media_buy_id))
        result = order_service.getOrdersByStatement(sb.ToStatement())

        assert "results" in result and len(result["results"]) > 0
        order = result["results"][0]
        assert str(order["advertiserId"]) == GAM_TEST_ADVERTISER_ID


@pytest.mark.requires_gam
class TestGAMLifecycle:
    """Test GAM order lifecycle operations (pause, archive)."""

    def test_pause_media_buy(self, gam_adapter, gam_order_cleanup):
        """Can pause an approved non-guaranteed order via update_media_buy.

        Mirrors manual script's pause step in test_lifecycle_archive_order.
        Uses adapter's update_media_buy which requires DB persistence.
        """
        from src.core.schemas import CreateMediaBuySuccess, UpdateMediaBuySuccess

        request, packages, start_time, end_time, pricing_info = _make_create_request(
            "gam_e2e_non_guaranteed", f"E2E-PAUSE-{uuid.uuid4().hex[:6]}"
        )

        response = gam_adapter.create_media_buy(request, packages, start_time, end_time, pricing_info)
        assert isinstance(response, CreateMediaBuySuccess)
        order_id = response.media_buy_id
        gam_order_cleanup.append(order_id)

        # Persist the media buy to DB (update_media_buy needs it)
        _persist_media_buy(response, request, packages, start_time, end_time)

        # Pause the order via adapter's update_media_buy
        pause_response = gam_adapter.update_media_buy(
            media_buy_id=order_id,
            buyer_ref=request.buyer_ref,
            action="pause_media_buy",
            package_id=None,
            budget=None,
            today=datetime.now(UTC),
        )

        assert isinstance(pause_response, UpdateMediaBuySuccess), f"Expected success, got: {pause_response}"

    def test_pause_then_archive_order(self, gam_adapter, gam_order_cleanup):
        """Can create, pause, and archive a HOUSE order.

        Mirrors manual script's test_lifecycle_archive_order: create → pause → archive.
        """
        from googleads import ad_manager as gam_module

        from src.adapters.gam.utils.constants import GAM_API_VERSION
        from src.core.schemas import CreateMediaBuySuccess, UpdateMediaBuySuccess

        request, packages, start_time, end_time, pricing_info = _make_create_request(
            "gam_e2e_house", f"E2E-ARCH-{uuid.uuid4().hex[:6]}"
        )

        response = gam_adapter.create_media_buy(request, packages, start_time, end_time, pricing_info)
        assert isinstance(response, CreateMediaBuySuccess)
        assert response.media_buy_id is not None
        order_id = response.media_buy_id

        # Persist the media buy to DB (update_media_buy needs it)
        _persist_media_buy(response, request, packages, start_time, end_time)

        # Step 1: Pause the order (like the manual script does before archiving)
        pause_response = gam_adapter.update_media_buy(
            media_buy_id=order_id,
            buyer_ref=request.buyer_ref,
            action="pause_media_buy",
            package_id=None,
            budget=None,
            today=datetime.now(UTC),
        )
        assert isinstance(pause_response, UpdateMediaBuySuccess), f"Pause should succeed, got: {pause_response}"

        # Step 2: Archive the order via direct GAM API
        # (adapter's archive_order has the .get() zeep bug — salesagent-mzpq)
        order_service = gam_adapter.client_manager.get_service("OrderService")
        archive_action = {"xsi_type": "ArchiveOrders"}
        sb = gam_module.StatementBuilder()
        sb.Where("id = :orderId").WithBindVariable("orderId", int(order_id))
        result = order_service.performOrderAction(archive_action, sb.ToStatement())

        assert result is not None, "Archive action should return a result"

        # Verify the order is archived
        sb2 = gam_module.StatementBuilder(version=GAM_API_VERSION)
        sb2.Where("id = :id").WithBindVariable("id", int(order_id))
        check = order_service.getOrdersByStatement(sb2.ToStatement())

        assert "results" in check and len(check["results"]) > 0
        assert check["results"][0]["isArchived"] is True, "Order should be archived"
        # Don't add to cleanup — already archived

    def test_activate_guaranteed_requires_workflow(self, gam_adapter, gam_order_cleanup):
        """activate_order on a guaranteed order creates a workflow step instead of direct activation."""
        from src.core.schemas import CreateMediaBuySuccess, UpdateMediaBuySuccess

        request, packages, start_time, end_time, pricing_info = _make_create_request(
            "gam_e2e_guaranteed", f"E2E-ACT-{uuid.uuid4().hex[:6]}", delivery_type="guaranteed"
        )

        create_response = gam_adapter.create_media_buy(request, packages, start_time, end_time, pricing_info)
        assert isinstance(create_response, CreateMediaBuySuccess)
        order_id = create_response.media_buy_id
        gam_order_cleanup.append(order_id)

        # Try to activate — should be blocked because it has guaranteed items
        update_response = gam_adapter.update_media_buy(
            media_buy_id=order_id,
            buyer_ref="e2e_test",
            action="activate_order",
            package_id=None,
            budget=None,
            today=datetime.now(UTC),
        )

        # For guaranteed orders, activate_order should create a workflow step
        # (either success with workflow_step_id, or error about guaranteed items)
        if isinstance(update_response, UpdateMediaBuySuccess):
            # Success path: workflow step created for activation approval
            assert update_response.workflow_step_id is not None, "Guaranteed activation should produce a workflow step"
        else:
            # Error path: activation blocked because of guaranteed items
            assert any("guaranteed" in str(e.message).lower() for e in update_response.errors), (
                f"Error should mention guaranteed items: {update_response.errors}"
            )
