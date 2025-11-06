"""Unit tests for inventory sync status logic fix."""

from unittest.mock import MagicMock, patch


def test_inventory_sync_checks_gam_inventory_not_products():
    """Test that inventory sync status checks GAMInventory table, not Products."""
    from src.services.setup_checklist_service import SetupChecklistService

    tenant_id = "test_tenant"

    # Mock the database session
    with patch("src.services.setup_checklist_service.get_db_session") as mock_session:
        # Setup mock session context manager
        mock_db = MagicMock()
        mock_session.return_value.__enter__.return_value = mock_db

        # Mock tenant
        mock_tenant = MagicMock()
        mock_tenant.tenant_id = tenant_id
        mock_tenant.ad_server = "gam"
        mock_tenant.authorized_domains = []
        mock_tenant.authorized_emails = ["test@example.com"]
        mock_tenant.policy_settings = {}
        mock_tenant.human_review_required = None
        mock_tenant.auto_approve_formats = None
        mock_tenant.order_name_template = None
        mock_tenant.line_item_name_template = None
        mock_tenant.slack_webhook_url = None
        mock_tenant.enable_axe_signals = False
        mock_tenant.virtual_host = None

        # Mock scalars().first() for tenant query
        mock_db.scalars.return_value.first.return_value = mock_tenant

        # Mock scalar() calls for counts
        # Order of calls: CurrencyLimit, AuthorizedProperty, GAMInventory, Product, Principal, CurrencyLimit (budget)
        mock_db.scalar.side_effect = [
            1,  # CurrencyLimit count
            1,  # AuthorizedProperty count
            100,  # GAMInventory count (inventory synced!)
            0,  # Product count (no products yet)
            0,  # Principal count
            0,  # CurrencyLimit with budget controls count
            1,  # CurrencyLimit count again for optional tasks
        ]

        # Create service and get status
        with patch.dict("os.environ", {"GEMINI_API_KEY": "test_key"}):
            service = SetupChecklistService(tenant_id)
            status = service.get_setup_status()

        # Find the inventory_synced task
        critical = {task["key"]: task for task in status["critical"]}
        inventory_task = critical["inventory_synced"]

        # Verify inventory is marked as complete
        assert inventory_task["is_complete"], "Inventory should be complete when GAMInventory records exist"
        assert "100" in inventory_task["details"], "Details should show inventory count"

        # Verify products task is separate and incomplete
        products_task = critical["products_created"]
        assert not products_task["is_complete"], "Products should be incomplete when no products exist"


def test_inventory_sync_incomplete_when_no_gam_inventory():
    """Test that inventory sync status is incomplete when no GAMInventory records exist."""
    from src.services.setup_checklist_service import SetupChecklistService

    tenant_id = "test_tenant"

    # Mock the database session
    with patch("src.services.setup_checklist_service.get_db_session") as mock_session:
        # Setup mock session context manager
        mock_db = MagicMock()
        mock_session.return_value.__enter__.return_value = mock_db

        # Mock tenant
        mock_tenant = MagicMock()
        mock_tenant.tenant_id = tenant_id
        mock_tenant.ad_server = "gam"
        mock_tenant.authorized_domains = []
        mock_tenant.authorized_emails = ["test@example.com"]
        mock_tenant.policy_settings = {}
        mock_tenant.human_review_required = None
        mock_tenant.auto_approve_formats = None
        mock_tenant.order_name_template = None
        mock_tenant.line_item_name_template = None
        mock_tenant.slack_webhook_url = None
        mock_tenant.enable_axe_signals = False
        mock_tenant.virtual_host = None

        # Mock scalars().first() for tenant query
        mock_db.scalars.return_value.first.return_value = mock_tenant

        # Mock scalar() calls for counts - all zero (no inventory synced)
        mock_db.scalar.side_effect = [
            1,  # CurrencyLimit count
            1,  # AuthorizedProperty count
            0,  # GAMInventory count (no inventory!)
            0,  # Product count
            0,  # Principal count
            0,  # CurrencyLimit with budget controls count
            1,  # CurrencyLimit count again for optional tasks
        ]

        # Create service and get status
        with patch.dict("os.environ", {"GEMINI_API_KEY": "test_key"}):
            service = SetupChecklistService(tenant_id)
            status = service.get_setup_status()

        # Find the inventory_synced task
        critical = {task["key"]: task for task in status["critical"]}
        inventory_task = critical["inventory_synced"]

        # Verify inventory is marked as incomplete
        assert not inventory_task["is_complete"], "Inventory should be incomplete when no GAMInventory records exist"
        assert "No inventory synced" in inventory_task["details"], "Details should indicate no inventory"
