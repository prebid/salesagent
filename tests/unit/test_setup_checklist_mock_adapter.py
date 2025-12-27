"""Test that setup checklist correctly handles mock adapter inventory sync.

The mock adapter has built-in inventory and should not require inventory sync.
"""

from unittest.mock import MagicMock, patch

import pytest


class TestSetupChecklistMockAdapter:
    """Test setup checklist service handles mock adapter correctly."""

    def test_mock_adapter_inventory_sync_always_complete(self):
        """Mock adapter should have inventory sync marked as complete (no sync required)."""
        from src.services.setup_checklist_service import SetupChecklistService

        tenant_id = "test_tenant"

        with patch("src.services.setup_checklist_service.get_db_session") as mock_db:
            mock_session = MagicMock()
            mock_db.return_value.__enter__.return_value = mock_session

            # Create mock tenant with mock adapter
            mock_tenant = MagicMock()
            mock_tenant.tenant_id = tenant_id
            mock_tenant.name = "Test Tenant"
            mock_tenant.ad_server = "mock"
            mock_tenant.adapter_config = None  # Required for is_gam_tenant property
            mock_tenant.is_gam_tenant = False  # Mock the is_gam_tenant property
            mock_tenant.authorized_domains = []
            mock_tenant.authorized_emails = []
            mock_tenant.human_review_required = None
            mock_tenant.auto_approve_format_ids = None
            mock_tenant.order_name_template = None
            mock_tenant.line_item_name_template = None
            mock_tenant.slack_webhook_url = None
            mock_tenant.virtual_host = None
            mock_tenant.enable_axe_signals = False
            mock_tenant.policy_settings = {}

            # Mock database queries
            mock_session.scalars.return_value.first.return_value = mock_tenant
            mock_session.scalar.return_value = 0  # No inventory, products, etc.

            # Get setup status
            service = SetupChecklistService(tenant_id)
            status = service.get_setup_status()

            # Find inventory sync task in critical tasks
            inventory_task = next((task for task in status["critical"] if task["key"] == "inventory_synced"), None)

            # Verify inventory sync task exists and is marked complete for mock adapter
            assert inventory_task is not None, "Inventory sync task should exist"
            assert inventory_task["is_complete"] is True, "Mock adapter should have inventory sync complete"
            assert (
                "built-in inventory" in inventory_task["description"].lower()
            ), "Description should mention built-in inventory"

    def test_gam_adapter_inventory_sync_requires_database_records(self):
        """GAM adapter should require GAMInventory records to be synced."""
        from src.services.setup_checklist_service import SetupChecklistService

        tenant_id = "test_tenant"

        with patch("src.services.setup_checklist_service.get_db_session") as mock_db:
            mock_session = MagicMock()
            mock_db.return_value.__enter__.return_value = mock_session

            # Create mock tenant with GAM adapter
            mock_tenant = MagicMock()
            mock_tenant.tenant_id = tenant_id
            mock_tenant.name = "Test Tenant"
            mock_tenant.ad_server = "google_ad_manager"
            mock_tenant.adapter_config = None  # Required for is_gam_tenant property
            mock_tenant.is_gam_tenant = True  # Mock the is_gam_tenant property
            mock_tenant.authorized_domains = []
            mock_tenant.authorized_emails = []
            mock_tenant.human_review_required = None
            mock_tenant.auto_approve_format_ids = None
            mock_tenant.order_name_template = None
            mock_tenant.line_item_name_template = None
            mock_tenant.slack_webhook_url = None
            mock_tenant.virtual_host = None
            mock_tenant.enable_axe_signals = False
            mock_tenant.policy_settings = {}

            # Mock database queries - no inventory synced
            mock_session.scalars.return_value.first.return_value = mock_tenant
            mock_session.scalar.return_value = 0  # No GAMInventory records

            # Get setup status
            service = SetupChecklistService(tenant_id)
            status = service.get_setup_status()

            # Find inventory sync task in critical tasks
            inventory_task = next((task for task in status["critical"] if task["key"] == "inventory_synced"), None)

            # Verify inventory sync task exists and is marked incomplete for GAM
            assert inventory_task is not None, "Inventory sync task should exist"
            assert inventory_task["is_complete"] is False, "GAM adapter should require inventory sync"
            assert "Sync ad units and placements" in inventory_task["description"], "Description should mention syncing"

    def test_gam_adapter_inventory_sync_complete_with_records(self):
        """GAM adapter with synced inventory should have task marked complete."""
        from src.services.setup_checklist_service import SetupChecklistService

        tenant_id = "test_tenant"

        with patch("src.services.setup_checklist_service.get_db_session") as mock_db:
            mock_session = MagicMock()
            mock_db.return_value.__enter__.return_value = mock_session

            # Create mock tenant with GAM adapter
            mock_tenant = MagicMock()
            mock_tenant.tenant_id = tenant_id
            mock_tenant.name = "Test Tenant"
            mock_tenant.ad_server = "google_ad_manager"
            mock_tenant.adapter_config = None  # Required for is_gam_tenant property
            mock_tenant.is_gam_tenant = True  # Mock the is_gam_tenant property
            mock_tenant.authorized_domains = []
            mock_tenant.authorized_emails = []
            mock_tenant.human_review_required = None
            mock_tenant.auto_approve_format_ids = None
            mock_tenant.order_name_template = None
            mock_tenant.line_item_name_template = None
            mock_tenant.slack_webhook_url = None
            mock_tenant.virtual_host = None
            mock_tenant.enable_axe_signals = False
            mock_tenant.policy_settings = {}

            # Mock database queries - inventory synced (1000 records)
            def scalar_side_effect(stmt):
                # Return different values based on the query
                # This is a simplification - in reality we'd inspect the statement
                return 1000  # GAMInventory count

            mock_session.scalars.return_value.first.return_value = mock_tenant
            mock_session.scalar.side_effect = scalar_side_effect

            # Get setup status
            service = SetupChecklistService(tenant_id)
            status = service.get_setup_status()

            # Find inventory sync task in critical tasks
            inventory_task = next((task for task in status["critical"] if task["key"] == "inventory_synced"), None)

            # Verify inventory sync task is marked complete
            assert inventory_task is not None, "Inventory sync task should exist"
            assert inventory_task["is_complete"] is True, "GAM adapter with synced inventory should be complete"

    def test_no_adapter_selected_inventory_incomplete(self):
        """When no adapter is selected (None), inventory sync should be incomplete."""
        from src.services.setup_checklist_service import SetupChecklistService

        tenant_id = "test_tenant"

        with patch("src.services.setup_checklist_service.get_db_session") as mock_db:
            mock_session = MagicMock()
            mock_db.return_value.__enter__.return_value = mock_session

            # Create mock tenant with no adapter selected
            mock_tenant = MagicMock()
            mock_tenant.tenant_id = tenant_id
            mock_tenant.name = "Test Tenant"
            mock_tenant.ad_server = None  # No adapter selected
            mock_tenant.authorized_domains = []
            mock_tenant.authorized_emails = []
            mock_tenant.human_review_required = None
            mock_tenant.auto_approve_format_ids = None
            mock_tenant.order_name_template = None
            mock_tenant.line_item_name_template = None
            mock_tenant.slack_webhook_url = None
            mock_tenant.virtual_host = None
            mock_tenant.enable_axe_signals = False
            mock_tenant.policy_settings = {}

            # Mock database queries
            mock_session.scalars.return_value.first.return_value = mock_tenant
            mock_session.scalar.return_value = 0

            # Get setup status
            service = SetupChecklistService(tenant_id)
            status = service.get_setup_status()

            # Find inventory sync task
            inventory_task = next((task for task in status["critical"] if task["key"] == "inventory_synced"), None)

            # Verify inventory sync is incomplete when no adapter selected
            assert inventory_task is not None, "Inventory sync task should exist"
            assert (
                inventory_task["is_complete"] is False
            ), "Inventory sync should be incomplete when no adapter selected"
            assert "Configure ad server" in inventory_task["description"], "Should indicate need to configure ad server"

    def test_validate_setup_complete_allows_mock_adapter_without_inventory(self):
        """validate_setup_complete() should not raise error for mock adapter without GAMInventory."""
        from src.services.setup_checklist_service import SetupIncompleteError, validate_setup_complete

        tenant_id = "test_tenant"

        with patch("src.services.setup_checklist_service.get_db_session") as mock_db:
            mock_session = MagicMock()
            mock_db.return_value.__enter__.return_value = mock_session

            # Create mock tenant with mock adapter - all critical tasks complete
            mock_tenant = MagicMock()
            mock_tenant.tenant_id = tenant_id
            mock_tenant.name = "Test Tenant"
            mock_tenant.ad_server = "mock"  # Mock adapter
            mock_tenant.authorized_domains = ["example.com"]
            mock_tenant.authorized_emails = []
            mock_tenant.human_review_required = None
            mock_tenant.auto_approve_format_ids = None
            mock_tenant.order_name_template = None
            mock_tenant.line_item_name_template = None
            mock_tenant.slack_webhook_url = None
            mock_tenant.virtual_host = None
            mock_tenant.enable_axe_signals = False
            mock_tenant.policy_settings = {}

            # Mock database queries - all requirements met except GAMInventory
            def scalar_side_effect(stmt):
                # Return counts for different queries
                # In reality we'd inspect the statement, but for testing we'll return non-zero
                return 1  # At least 1 currency, 1 property, 1 product, 1 principal

            mock_session.scalars.return_value.first.return_value = mock_tenant
            mock_session.scalar.side_effect = scalar_side_effect

            # Mock GEMINI_API_KEY env var
            with patch("os.getenv") as mock_getenv:
                mock_getenv.return_value = "fake-api-key"

                # This should NOT raise SetupIncompleteError for mock adapter
                # (inventory sync is auto-complete for mock)
                try:
                    validate_setup_complete(tenant_id)
                    # If we get here, validation passed (expected for mock adapter)
                    assert True
                except SetupIncompleteError as e:
                    # Should not happen for mock adapter
                    pytest.fail(
                        f"validate_setup_complete raised error for mock adapter: {e.message}. "
                        f"Missing tasks: {e.missing_tasks}"
                    )
