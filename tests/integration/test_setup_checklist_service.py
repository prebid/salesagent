"""Tests for setup checklist service."""

import os
from unittest.mock import patch

import pytest

from src.core.database.models import (
    AuthorizedProperty,
    CurrencyLimit,
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

    from sqlalchemy import select

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

    from sqlalchemy import delete, select

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
            auto_approve_formats=["display_300x250"],
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
            formats=["display"],
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

        db_session.commit()

    yield tenant

    # Cleanup after test
    with get_db_session() as db_session:
        db_session.execute(delete(Principal).where(Principal.tenant_id == test_tenant_id))
        db_session.execute(delete(Product).where(Product.tenant_id == test_tenant_id))
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
        with patch.dict(os.environ, {}, clear=True):  # No GEMINI_API_KEY
            service = SetupChecklistService(test_tenant_id)
            status = service.get_setup_status()

            # Should have low progress
            assert status["progress_percent"] < 50
            assert not status["ready_for_orders"]

            # Check critical tasks are incomplete
            critical = {task["key"]: task for task in status["critical"]}
            assert not critical["gemini_api_key"]["is_complete"]
            assert not critical["currency_limits"]["is_complete"]
            assert not critical["ad_server_connected"]["is_complete"]
            assert not critical["authorized_properties"]["is_complete"]
            assert not critical["inventory_synced"]["is_complete"]
            assert not critical["products_created"]["is_complete"]
            assert not critical["principals_created"]["is_complete"]

    def test_complete_tenant_ready_for_orders(self, integration_db, setup_complete_tenant, test_tenant_id):
        """Test that fully configured tenant shows all critical tasks complete."""
        with patch.dict(os.environ, {"GEMINI_API_KEY": "test_key"}):
            service = SetupChecklistService(test_tenant_id)
            status = service.get_setup_status()

            # Should have 100% critical complete
            assert status["ready_for_orders"]
            critical_complete = all(task["is_complete"] for task in status["critical"])
            assert critical_complete

            # Check specific critical tasks
            critical = {task["key"]: task for task in status["critical"]}
            assert critical["gemini_api_key"]["is_complete"]
            assert critical["currency_limits"]["is_complete"]
            assert critical["ad_server_connected"]["is_complete"]
            assert critical["authorized_properties"]["is_complete"]
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
        """Test GEMINI_API_KEY environment variable detection."""
        # Without key
        with patch.dict(os.environ, {}, clear=True):
            service = SetupChecklistService(test_tenant_id)
            status = service.get_setup_status()
            gemini_task = next(t for t in status["critical"] if t["key"] == "gemini_api_key")
            assert not gemini_task["is_complete"]

        # With key
        with patch.dict(os.environ, {"GEMINI_API_KEY": "test_key"}):
            service = SetupChecklistService(test_tenant_id)
            status = service.get_setup_status()
            gemini_task = next(t for t in status["critical"] if t["key"] == "gemini_api_key")
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
