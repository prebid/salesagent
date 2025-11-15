"""Tests for setup checklist service."""

import json
import os
from unittest.mock import patch

import pytest
from sqlalchemy import select

from src.core.database.models import (
    AdapterConfig,
    AuthorizedProperty,
    CurrencyLimit,
    GAMInventory,
    Principal,
    Product,
    Tenant,
)
from src.services.setup_checklist_service import (
    SetupChecklistService,
    SetupIncompleteError,
    get_incomplete_critical_tasks,
    validate_setup_complete,
)

pytestmark = pytest.mark.requires_db


@pytest.fixture
def test_tenant_id():
    """Test tenant ID."""
    return "test_tenant"


@pytest.fixture
def setup_minimal_tenant(integration_db, test_tenant_id):
    """Create minimal tenant for testing (incomplete setup)."""
    from datetime import UTC, datetime

    from src.core.database.database_session import get_db_session

    with get_db_session() as db_session:
        # Check if tenant already exists and delete it
        stmt = select(Tenant).filter_by(tenant_id=test_tenant_id)
        existing = db_session.scalars(stmt).first()
        if existing:
            db_session.delete(existing)
            db_session.commit()

        now = datetime.now(UTC)
        tenant = Tenant(
            tenant_id=test_tenant_id,
            name="Test Tenant",
            subdomain="test",
            ad_server=None,  # Not configured
            created_at=now,
            updated_at=now,
            is_active=True,
        )
        db_session.add(tenant)
        db_session.commit()

    yield tenant

    # Cleanup after test
    with get_db_session() as db_session:
        stmt = select(Tenant).filter_by(tenant_id=test_tenant_id)
        tenant = db_session.scalars(stmt).first()
        if tenant:
            db_session.delete(tenant)
            db_session.commit()


@pytest.fixture
def setup_complete_tenant(integration_db, test_tenant_id):
    """Create fully configured tenant for testing."""
    from datetime import UTC, datetime

    from sqlalchemy import delete

    from src.core.database.database_session import get_db_session

    with get_db_session() as db_session:
        # Check if tenant already exists and delete it (and related records)
        stmt = select(Tenant).filter_by(tenant_id=test_tenant_id)
        existing = db_session.scalars(stmt).first()
        if existing:
            # Delete related records first (due to foreign keys)
            db_session.execute(delete(Principal).where(Principal.tenant_id == test_tenant_id))
            db_session.execute(delete(Product).where(Product.tenant_id == test_tenant_id))
            db_session.execute(delete(AuthorizedProperty).where(AuthorizedProperty.tenant_id == test_tenant_id))
            db_session.execute(delete(CurrencyLimit).where(CurrencyLimit.tenant_id == test_tenant_id))
            db_session.delete(existing)
            db_session.commit()

        now = datetime.now(UTC)

        # Create tenant
        tenant = Tenant(
            tenant_id=test_tenant_id,
            name="Complete Tenant",
            subdomain="complete",
            ad_server="google_ad_manager",
            human_review_required=True,
            auto_approve_format_ids=["display_300x250"],
            slack_webhook_url="https://hooks.slack.com/test",
            enable_axe_signals=True,
            authorized_emails=["test@example.com"],  # Required for access control
            created_at=now,
            updated_at=now,
            is_active=True,
        )
        db_session.add(tenant)

        # Add currency
        currency = CurrencyLimit(
            tenant_id=test_tenant_id, currency_code="USD", min_package_budget=0.0, max_daily_package_spend=10000.0
        )
        db_session.add(currency)

        # Add authorized property
        prop = AuthorizedProperty(
            tenant_id=test_tenant_id,
            property_id="prop_1",
            property_type="website",
            name="Test Property",
            publisher_domain="test.com",
            identifiers=[{"type": "domain", "value": "test.com"}],
        )
        db_session.add(prop)

        # Add product
        product = Product(
            tenant_id=test_tenant_id,
            product_id="prod_1",
            name="Test Product",
            description="Test",
            format_ids=["display"],
            targeting_template={},
            delivery_type="guaranteed",
            property_tags=["all_inventory"],
        )
        db_session.add(product)

        # Add principal
        principal = Principal(
            tenant_id=test_tenant_id,
            principal_id="principal_1",
            name="Test Advertiser",
            access_token="test_token",
            platform_mappings={"google_ad_manager": {"advertiser_id": "12345"}},
        )
        db_session.add(principal)

        # Add GAM inventory (ad units, placements, targeting)
        # This simulates that inventory has been synced from the ad server
        inventory_items = [
            GAMInventory(
                tenant_id=test_tenant_id,
                inventory_type="ad_unit",
                inventory_id="ad_unit_1",
                name="Test Ad Unit 1",
                path=["root", "test"],
                status="active",
                inventory_metadata={"size": "300x250"},
            ),
            GAMInventory(
                tenant_id=test_tenant_id,
                inventory_type="placement",
                inventory_id="placement_1",
                name="Test Placement 1",
                path=["root"],
                status="active",
                inventory_metadata={},
            ),
            GAMInventory(
                tenant_id=test_tenant_id,
                inventory_type="targeting_key",
                inventory_id="key_1",
                name="Test Key",
                path=[],
                status="active",
                inventory_metadata={"type": "predefined"},
            ),
        ]
        for item in inventory_items:
            db_session.add(item)

        db_session.commit()

    yield tenant

    # Cleanup after test
    with get_db_session() as db_session:
        db_session.execute(delete(Principal).where(Principal.tenant_id == test_tenant_id))
        db_session.execute(delete(Product).where(Product.tenant_id == test_tenant_id))
        db_session.execute(delete(GAMInventory).where(GAMInventory.tenant_id == test_tenant_id))
        db_session.execute(delete(AuthorizedProperty).where(AuthorizedProperty.tenant_id == test_tenant_id))
        db_session.execute(delete(CurrencyLimit).where(CurrencyLimit.tenant_id == test_tenant_id))
        stmt = select(Tenant).filter_by(tenant_id=test_tenant_id)
        tenant = db_session.scalars(stmt).first()
        if tenant:
            db_session.delete(tenant)
        db_session.commit()


class TestSetupChecklistService:
    """Tests for SetupChecklistService."""

    def test_minimal_tenant_incomplete_setup(self, integration_db, setup_minimal_tenant, test_tenant_id):
        """Test that minimal tenant shows all critical tasks incomplete."""
        service = SetupChecklistService(test_tenant_id)
        status = service.get_setup_status()

        # Should have low progress
        assert status["progress_percent"] < 50
        assert not status["ready_for_orders"]

        # Check critical tasks are incomplete (Gemini moved to optional)
        critical = {task["key"]: task for task in status["critical"]}
        assert not critical["currency_limits"]["is_complete"]
        assert not critical["ad_server_connected"]["is_complete"]
        assert not critical["authorized_properties"]["is_complete"]
        assert not critical["inventory_synced"]["is_complete"]
        assert not critical["products_created"]["is_complete"]
        assert not critical["principals_created"]["is_complete"]

    def test_complete_tenant_ready_for_orders(self, integration_db, setup_complete_tenant, test_tenant_id):
        """Test that fully configured tenant shows all critical tasks complete."""
        service = SetupChecklistService(test_tenant_id)
        status = service.get_setup_status()

        # Should have 100% critical complete
        assert status["ready_for_orders"]
        critical_complete = all(task["is_complete"] for task in status["critical"])
        assert critical_complete

        # Check specific critical tasks (Gemini moved to optional)
        critical = {task["key"]: task for task in status["critical"]}
        assert critical["currency_limits"]["is_complete"]
        assert critical["ad_server_connected"]["is_complete"]
        assert critical["authorized_properties"]["is_complete"]
        assert critical["inventory_synced"]["is_complete"]
        assert critical["products_created"]["is_complete"]
        assert critical["principals_created"]["is_complete"]

    def test_recommended_tasks_tracked(self, integration_db, setup_complete_tenant, test_tenant_id):
        """Test that recommended tasks are properly tracked."""
        with patch.dict(os.environ, {"GEMINI_API_KEY": "test_key"}):
            service = SetupChecklistService(test_tenant_id)
            status = service.get_setup_status()

            # Check recommended tasks exist
            assert len(status["recommended"]) > 0
            recommended = {task["key"]: task for task in status["recommended"]}

            # This tenant has everything configured
            assert recommended["creative_approval_guidelines"]["is_complete"]
            assert recommended["naming_conventions"]["is_complete"]
            assert recommended["budget_controls"]["is_complete"]
            assert recommended["slack_integration"]["is_complete"]

    def test_optional_tasks_tracked(self, integration_db, setup_complete_tenant, test_tenant_id):
        """Test that optional tasks are properly tracked."""
        with patch.dict(os.environ, {"GEMINI_API_KEY": "test_key"}):
            service = SetupChecklistService(test_tenant_id)
            status = service.get_setup_status()

            # Check optional tasks exist
            assert len(status["optional"]) > 0
            optional = {task["key"]: task for task in status["optional"]}

            # Complete tenant has AXE signals enabled
            assert optional["signals_agent"]["is_complete"]

    def test_progress_calculation(self, integration_db, test_tenant_id):
        """Test progress percentage calculation."""
        from datetime import UTC, datetime

        from src.core.database.database_session import get_db_session

        now = datetime.now(UTC)

        with get_db_session() as db_session:
            # Create tenant with partial setup (50% of critical tasks)
            tenant = Tenant(
                tenant_id=test_tenant_id,
                name="Partial Tenant",
                subdomain="partial",
                ad_server="mock",
                created_at=now,
                updated_at=now,
                is_active=True,
            )
            db_session.add(tenant)

            # Add 2 out of 4 critical items (currency + property)
            currency = CurrencyLimit(
                tenant_id=test_tenant_id, currency_code="USD", min_package_budget=0.0, max_daily_package_spend=10000.0
            )
            db_session.add(currency)

            prop = AuthorizedProperty(
                tenant_id=test_tenant_id,
                property_id="prop_1",
                property_type="website",
                name="Test",
                publisher_domain="test.com",
                identifiers=[],
            )
            db_session.add(prop)
            db_session.commit()

        with patch.dict(os.environ, {"GEMINI_API_KEY": "test_key"}):
            service = SetupChecklistService(test_tenant_id)
            status = service.get_setup_status()

            # Should show partial progress
            assert 0 < status["progress_percent"] < 100
            assert status["completed_count"] < status["total_count"]

    def test_action_urls_provided(self, integration_db, setup_minimal_tenant, test_tenant_id):
        """Test that action URLs are provided for incomplete tasks."""
        with patch.dict(os.environ, {}, clear=True):
            service = SetupChecklistService(test_tenant_id)
            status = service.get_setup_status()

            # Check that incomplete tasks have action URLs (except environment variables)
            for task in status["critical"]:
                if not task["is_complete"] and task["key"] != "gemini_api_key":
                    assert task["action_url"] is not None
                    assert f"/tenant/{test_tenant_id}" in task["action_url"]

    def test_get_next_steps(self, integration_db, setup_minimal_tenant, test_tenant_id):
        """Test get_next_steps returns prioritized actions."""
        with patch.dict(os.environ, {}, clear=True):
            service = SetupChecklistService(test_tenant_id)
            next_steps = service.get_next_steps()

            # Should return max 3 steps
            assert len(next_steps) <= 3

            # All should be critical priority (since critical tasks incomplete)
            assert all(step["priority"] == "critical" for step in next_steps)

            # Each step should have required fields
            for step in next_steps:
                assert "title" in step
                assert "description" in step
                assert "action_url" in step
                assert "priority" in step

    def test_bulk_setup_status_for_multiple_tenants(self, integration_db):
        """Test bulk setup status calculation for multiple tenants efficiently."""
        from datetime import UTC, datetime

        from src.core.database.database_session import get_db_session

        # Create 3 test tenants with varying setup levels
        tenant_ids = ["bulk_tenant_1", "bulk_tenant_2", "bulk_tenant_3"]

        with get_db_session() as db_session:
            now = datetime.now(UTC)

            # Tenant 1: Minimal setup (no ad server, no products)
            tenant1 = Tenant(
                tenant_id=tenant_ids[0],
                name="Bulk Test Tenant 1",
                subdomain="bulk1",
                ad_server=None,
                created_at=now,
                updated_at=now,
                is_active=True,
            )
            db_session.add(tenant1)

            # Tenant 2: Partial setup (ad server configured, has products)
            tenant2 = Tenant(
                tenant_id=tenant_ids[1],
                name="Bulk Test Tenant 2",
                subdomain="bulk2",
                ad_server="mock",
                created_at=now,
                updated_at=now,
                is_active=True,
            )
            db_session.add(tenant2)

            # Add currency and product for tenant 2
            from tests.fixtures.factories import PrincipalFactory, ProductFactory

            currency2 = CurrencyLimit(
                tenant_id=tenant_ids[1], currency_code="USD", min_package_budget=0.0, max_daily_package_spend=10000.0
            )
            db_session.add(currency2)

            # Use ProductFactory to create product with all required fields
            product2_data = ProductFactory.create(
                tenant_id=tenant_ids[1], product_id="bulk_product_2", name="Test Product", format_ids=[]
            )
            product2 = Product(
                tenant_id=product2_data["tenant_id"],
                product_id=product2_data["product_id"],
                name=product2_data["name"],
                description=product2_data["description"],
                format_ids=product2_data["format_ids"] if isinstance(product2_data["format_ids"], list) else [],
                targeting_template=(
                    product2_data["targeting_template"] if isinstance(product2_data["targeting_template"], dict) else {}
                ),
                delivery_type=product2_data["delivery_type"],
                property_tags=["all_inventory"],  # Required by ck_product_properties_xor constraint
            )
            db_session.add(product2)

            # Tenant 3: Complete setup
            tenant3 = Tenant(
                tenant_id=tenant_ids[2],
                name="Bulk Test Tenant 3",
                subdomain="bulk3",
                ad_server="mock",
                created_at=now,
                updated_at=now,
                is_active=True,
                authorized_domains=["example.com"],
            )
            db_session.add(tenant3)

            # Add complete setup for tenant 3
            currency3 = CurrencyLimit(
                tenant_id=tenant_ids[2], currency_code="USD", min_package_budget=0.0, max_daily_package_spend=10000.0
            )
            db_session.add(currency3)

            # Add adapter config with AXE keys for tenant 3 (recommended for complete setup)
            adapter_config3 = AdapterConfig(
                tenant_id=tenant_ids[2],
                adapter_type="mock",
                axe_include_key="axe_include_segment",
                axe_exclude_key="axe_exclude_segment",
                axe_macro_key="axe_macro_segment",
            )
            db_session.add(adapter_config3)

            property3 = AuthorizedProperty(
                property_id="prop_bulk_3",
                tenant_id=tenant_ids[2],
                property_type="website",
                name="Test Property",
                identifiers=[{"type": "domain", "value": "example.com"}],
                publisher_domain="example.com",
                verification_status="verified",
            )
            db_session.add(property3)

            # Use ProductFactory for product3 as well
            product3_data = ProductFactory.create(
                tenant_id=tenant_ids[2], product_id="bulk_product_3", name="Test Product", format_ids=[]
            )
            product3 = Product(
                tenant_id=product3_data["tenant_id"],
                product_id=product3_data["product_id"],
                name=product3_data["name"],
                description=product3_data["description"],
                format_ids=product3_data["format_ids"] if isinstance(product3_data["format_ids"], list) else [],
                targeting_template=(
                    product3_data["targeting_template"] if isinstance(product3_data["targeting_template"], dict) else {}
                ),
                delivery_type=product3_data["delivery_type"],
                property_tags=["all_inventory"],  # Required by ck_product_properties_xor constraint
            )
            db_session.add(product3)

            # Use PrincipalFactory to create principal with valid platform_mappings
            principal3_data = PrincipalFactory.create(
                tenant_id=tenant_ids[2],
                principal_id="bulk_principal_3",
                name="Test Principal",
                access_token="test_token_bulk_3",
            )
            principal3 = Principal(
                tenant_id=principal3_data["tenant_id"],
                principal_id=principal3_data["principal_id"],
                name=principal3_data["name"],
                access_token=principal3_data["access_token"],
                platform_mappings=(
                    json.loads(principal3_data["platform_mappings"])
                    if isinstance(principal3_data["platform_mappings"], str)
                    else principal3_data["platform_mappings"]
                ),
            )
            db_session.add(principal3)

            db_session.commit()

        # Call bulk setup status method
        statuses = SetupChecklistService.get_bulk_setup_status(tenant_ids)

        # Verify all tenants returned
        assert len(statuses) == 3
        assert set(statuses.keys()) == set(tenant_ids)

        # Verify tenant 1 has low progress (minimal setup)
        status1 = statuses[tenant_ids[0]]
        assert status1["progress_percent"] < 30
        assert not status1["ready_for_orders"]

        # Verify tenant 2 has medium progress
        status2 = statuses[tenant_ids[1]]
        assert 30 <= status2["progress_percent"] < 80

        # Verify tenant 3 has high progress (near complete)
        status3 = statuses[tenant_ids[2]]
        assert status3["progress_percent"] > 70

        # Verify structure matches single-tenant query
        for status in statuses.values():
            assert "progress_percent" in status
            assert "completed_count" in status
            assert "total_count" in status
            assert "ready_for_orders" in status
            assert "critical" in status
            assert "recommended" in status
            assert "optional" in status

        # Cleanup - Delete child objects first to avoid foreign key violations
        with get_db_session() as db_session:
            for tenant_id in tenant_ids:
                # Delete child objects (CASCADE will handle some, but explicit is clearer)
                # Delete principals
                stmt = select(Principal).filter_by(tenant_id=tenant_id)
                principals = db_session.scalars(stmt).all()
                for principal in principals:
                    db_session.delete(principal)

                # Delete products
                stmt = select(Product).filter_by(tenant_id=tenant_id)
                products = db_session.scalars(stmt).all()
                for product in products:
                    db_session.delete(product)

                # Delete authorized properties
                stmt = select(AuthorizedProperty).filter_by(tenant_id=tenant_id)
                properties = db_session.scalars(stmt).all()
                for prop in properties:
                    db_session.delete(prop)

                # Delete currency limits
                stmt = select(CurrencyLimit).filter_by(tenant_id=tenant_id)
                currencies = db_session.scalars(stmt).all()
                for currency in currencies:
                    db_session.delete(currency)

                # Finally delete tenant
                stmt = select(Tenant).filter_by(tenant_id=tenant_id)
                tenant = db_session.scalars(stmt).first()
                if tenant:
                    db_session.delete(tenant)

            db_session.commit()

    def test_bulk_setup_status_with_empty_list(self, integration_db):
        """Test bulk setup status handles empty tenant list."""
        statuses = SetupChecklistService.get_bulk_setup_status([])
        assert statuses == {}

    def test_bulk_setup_status_matches_single_query(self, integration_db, setup_complete_tenant, test_tenant_id):
        """Test that bulk query produces same results as single-tenant query."""
        # Get status via single query
        single_service = SetupChecklistService(test_tenant_id)
        single_status = single_service.get_setup_status()

        # Get status via bulk query
        bulk_statuses = SetupChecklistService.get_bulk_setup_status([test_tenant_id])
        bulk_status = bulk_statuses[test_tenant_id]

        # Compare key metrics
        assert single_status["progress_percent"] == bulk_status["progress_percent"]
        assert single_status["completed_count"] == bulk_status["completed_count"]
        assert single_status["total_count"] == bulk_status["total_count"]
        assert single_status["ready_for_orders"] == bulk_status["ready_for_orders"]

        # Compare task counts
        assert len(single_status["critical"]) == len(bulk_status["critical"])
        assert len(single_status["recommended"]) == len(bulk_status["recommended"])
        assert len(single_status["optional"]) == len(bulk_status["optional"])


class TestSetupValidation:
    """Tests for setup validation functions."""

    def test_get_incomplete_critical_tasks(self, integration_db, setup_minimal_tenant, test_tenant_id):
        """Test getting list of incomplete critical tasks."""
        with patch.dict(os.environ, {}, clear=True):
            incomplete = get_incomplete_critical_tasks(test_tenant_id)

            # Should have multiple incomplete tasks
            assert len(incomplete) > 0

            # Each task should have required fields
            for task in incomplete:
                assert "key" in task
                assert "name" in task
                assert "description" in task
                assert task["is_complete"] is False

    def test_validate_setup_complete_fails_for_incomplete(self, integration_db, setup_minimal_tenant, test_tenant_id):
        """Test that validation fails for incomplete setup."""
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(SetupIncompleteError) as exc_info:
                validate_setup_complete(test_tenant_id)

            # Check error details
            error = exc_info.value
            assert len(error.missing_tasks) > 0
            assert "Complete required setup tasks" in error.message

    def test_validate_setup_complete_passes_for_complete(self, integration_db, setup_complete_tenant, test_tenant_id):
        """Test that validation passes for complete setup."""
        with patch.dict(os.environ, {"GEMINI_API_KEY": "test_key"}):
            # Should not raise exception
            validate_setup_complete(test_tenant_id)

    def test_setup_incomplete_error_details(self, integration_db, setup_minimal_tenant, test_tenant_id):
        """Test that SetupIncompleteError provides useful details."""
        with patch.dict(os.environ, {}, clear=True):
            try:
                validate_setup_complete(test_tenant_id)
            except SetupIncompleteError as e:
                # Check error structure
                assert hasattr(e, "message")
                assert hasattr(e, "missing_tasks")
                assert isinstance(e.missing_tasks, list)
                assert len(e.missing_tasks) > 0

                # Check task structure
                task = e.missing_tasks[0]
                assert "key" in task
                assert "name" in task
                assert "description" in task


class TestTaskDetails:
    """Tests for individual task checking logic."""

    def test_gemini_api_key_detection(self, integration_db, setup_minimal_tenant, test_tenant_id):
        """Test tenant-specific Gemini API key detection (moved to optional tasks)."""
        from src.core.database.database_session import get_db_session

        # Without key (tenant.gemini_api_key is None)
        service = SetupChecklistService(test_tenant_id)
        status = service.get_setup_status()
        gemini_task = next(t for t in status["optional"] if t["key"] == "gemini_api_key")
        assert not gemini_task["is_complete"]

        # With tenant-specific key
        with get_db_session() as db_session:
            tenant = db_session.scalars(select(Tenant).filter_by(tenant_id=test_tenant_id)).first()
            tenant.gemini_api_key = "test_tenant_key"  # Set tenant-specific key
            db_session.commit()

        service = SetupChecklistService(test_tenant_id)
        status = service.get_setup_status()
        gemini_task = next(t for t in status["optional"] if t["key"] == "gemini_api_key")
        assert gemini_task["is_complete"]

    def test_currency_count_in_details(self, integration_db, test_tenant_id):
        """Test that currency count is shown in task details."""
        from datetime import UTC, datetime

        from src.core.database.database_session import get_db_session

        now = datetime.now(UTC)

        with get_db_session() as db_session:
            tenant = Tenant(
                tenant_id=test_tenant_id,
                name="Test",
                subdomain="test",
                ad_server="mock",
                created_at=now,
                updated_at=now,
                is_active=True,
            )
            db_session.add(tenant)

            # Add 2 currencies
            for currency_code in ["USD", "EUR"]:
                currency = CurrencyLimit(
                    tenant_id=test_tenant_id,
                    currency_code=currency_code,
                    min_package_budget=0.0,
                    max_daily_package_spend=10000.0,
                )
                db_session.add(currency)
            db_session.commit()

        with patch.dict(os.environ, {"GEMINI_API_KEY": "test_key"}):
            service = SetupChecklistService(test_tenant_id)
            status = service.get_setup_status()

            currency_task = next(t for t in status["critical"] if t["key"] == "currency_limits")
            assert "2 currencies" in currency_task["details"]

    def test_tenant_not_found_error(self, integration_db):
        """Test that service raises error for non-existent tenant."""
        service = SetupChecklistService("nonexistent_tenant")

        with pytest.raises(ValueError, match="Tenant nonexistent_tenant not found"):
            service.get_setup_status()
