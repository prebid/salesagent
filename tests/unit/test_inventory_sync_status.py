"""Unit tests for inventory sync status logic fix."""

from unittest.mock import MagicMock, patch

from tests.utils.database_helpers import vhost_for


def test_inventory_sync_checks_gam_inventory_not_products(tenant_row, scalars_by_entity):
    """Test that inventory sync status checks GAMInventory table, not Products."""
    from src.services.setup_checklist_service import SetupChecklistService

    tenant_id = "test_tenant"

    # Mock the database session
    with patch("src.services.setup_checklist_service.get_db_session") as mock_session:
        # Setup mock session context manager
        mock_db = MagicMock()
        mock_session.return_value.__enter__.return_value = mock_db

        # The tenant, as the real ORM row (unset columns keep their model defaults)
        tenant = tenant_row(
            tenant_id=tenant_id,
            ad_server="google_ad_manager",
            authorized_emails=["test@example.com"],
            virtual_host=vhost_for(tenant_id),
        )

        # The tenant query answers with the row; any other scalars() query finds nothing
        mock_db.scalars.side_effect = scalars_by_entity(tenant)

        # Mock scalar() calls for counts
        # Order of calls: CurrencyLimit, GAMInventory, Product, Principal, CurrencyLimit (budget).
        # The authorized properties and publishers are a repository read (PublisherRepository), not a scalar.
        mock_db.scalar.side_effect = [
            1,  # CurrencyLimit count
            100,  # GAMInventory count (inventory synced!)
            0,  # Product count (no products yet)
            0,  # Principal count
            0,  # CurrencyLimit with budget controls count
            1,  # CurrencyLimit count again for optional tasks
        ]

        # Create service and get status
        status = SetupChecklistService(tenant_id).get_setup_status()

        # Find the inventory_synced task
        critical = {task["key"]: task for task in status["critical"]}
        inventory_task = critical["inventory_synced"]

        # Verify inventory is marked as complete
        assert inventory_task["is_complete"], "Inventory should be complete when GAMInventory records exist"
        assert "100" in inventory_task["details"], "Details should show inventory count"

        # Verify products task is separate and incomplete
        products_task = critical["products_created"]
        assert not products_task["is_complete"], "Products should be incomplete when no products exist"


def test_inventory_sync_incomplete_when_no_gam_inventory(tenant_row, scalars_by_entity):
    """Test that inventory sync status is incomplete when no GAMInventory records exist."""
    from src.services.setup_checklist_service import SetupChecklistService

    tenant_id = "test_tenant"

    # Mock the database session
    with patch("src.services.setup_checklist_service.get_db_session") as mock_session:
        # Setup mock session context manager
        mock_db = MagicMock()
        mock_session.return_value.__enter__.return_value = mock_db

        # The tenant, as the real ORM row (unset columns keep their model defaults)
        tenant = tenant_row(
            tenant_id=tenant_id,
            ad_server="google_ad_manager",
            authorized_emails=["test@example.com"],
            virtual_host=vhost_for(tenant_id),
        )

        # The tenant query answers with the row; any other scalars() query finds nothing
        mock_db.scalars.side_effect = scalars_by_entity(tenant)

        # Mock scalar() calls for counts - all zero (no inventory synced)
        # Order of calls: CurrencyLimit, GAMInventory, Product, Principal, CurrencyLimit (budget).
        # The authorized properties and publishers are a repository read (PublisherRepository), not a scalar.
        mock_db.scalar.side_effect = [
            1,  # CurrencyLimit count
            0,  # GAMInventory count (no inventory!)
            0,  # Product count
            0,  # Principal count
            0,  # CurrencyLimit with budget controls count
            1,  # CurrencyLimit count again for optional tasks
        ]

        # Create service and get status
        status = SetupChecklistService(tenant_id).get_setup_status()

        # Find the inventory_synced task
        critical = {task["key"]: task for task in status["critical"]}
        inventory_task = critical["inventory_synced"]

        # Verify inventory is marked as incomplete
        assert not inventory_task["is_complete"], "Inventory should be incomplete when no GAMInventory records exist"
        assert "No inventory synced" in inventory_task["details"], "Details should indicate no inventory"
