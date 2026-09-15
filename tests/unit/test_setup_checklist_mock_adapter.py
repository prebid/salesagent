"""Test that setup checklist correctly handles mock adapter.

The mock adapter is for testing/development only - it's NOT considered a fully configured
production ad server. This means:
1. SSO is still required (regardless of ad server)
2. Currency limits, inventory sync, products, and principals tasks don't appear
   until a real ad server (like GAM) is configured
"""

from unittest.mock import MagicMock, patch

import pytest


class TestSetupChecklistMockAdapter:
    """Test setup checklist service handles mock adapter correctly."""

    def test_mock_adapter_not_fully_configured_in_production(self):
        """Mock adapter is not considered fully configured for production (ADCP_TESTING not set).

        When using mock adapter in non-test environments, only the first critical tasks appear:
        - Ad server configuration (incomplete - mock is not production-ready)
        - SSO configuration (always required)
        - Authorized properties (always shown)

        Currency limits, inventory sync, products, and principals tasks
        only appear after a real ad server is configured.
        """
        from src.services.setup_checklist_service import SetupChecklistService

        tenant_id = "test_tenant"

        # Ensure ADCP_TESTING is NOT set for this test (tests production behavior)
        # In single-tenant mode (default), SSO is critical
        with patch.dict("os.environ", {"ADCP_TESTING": "false", "ADCP_MULTI_TENANT": "false"}, clear=False):
            with patch("src.services.setup_checklist_service.get_db_session") as mock_db:
                mock_session = MagicMock()
                mock_db.return_value.__enter__.return_value = mock_session

                # Create mock tenant with mock adapter
                mock_tenant = MagicMock()
                # TenantAIConfig.from_tenant reads these two fields for the optional AI task and
                # refuses a non-dict (a bare MagicMock attribute) by design.
                mock_tenant.ai_config = None
                mock_tenant.gemini_api_key = None
                mock_tenant.tenant_id = tenant_id
                mock_tenant.name = "Test Tenant"
                mock_tenant.ad_server = "mock"
                mock_tenant.adapter_config = None
                mock_tenant.is_gam_tenant = False
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
                mock_tenant.auth_setup_mode = True  # Setup mode active

                # Mock database queries
                mock_session.scalars.return_value.first.return_value = mock_tenant
                mock_session.scalar.return_value = 0

                # Get setup status
                service = SetupChecklistService(tenant_id)
                status = service.get_setup_status()

                # Find critical tasks
                critical_keys = {task["key"] for task in status["critical"]}

                # In single-tenant mode, SSO is critical
                assert "ad_server_connected" in critical_keys, "Ad server task should exist"
                assert "sso_configuration" in critical_keys, "SSO is critical in single-tenant mode"
                assert "authorized_properties" in critical_keys, "Properties task should exist"

                # SSO should NOT be in optional tasks in single-tenant mode
                optional_keys = {task["key"] for task in status["optional"]}
                assert "sso_configuration" not in optional_keys, "SSO should not be optional in single-tenant mode"

                # Inventory sync, currency_limits, products, principals should NOT be present
                # (these only appear after ad server is fully configured)
                assert "inventory_synced" not in critical_keys, "Inventory task should not appear for mock adapter"
                assert "currency_limits" not in critical_keys, "Currency task should not appear for mock adapter"

                # Ad server should be marked incomplete for mock
                ad_server_task = next(t for t in status["critical"] if t["key"] == "ad_server_connected")
                assert ad_server_task["is_complete"] is False, "Mock adapter should not be considered fully configured"

    def test_gam_adapter_inventory_sync_requires_database_records(self):
        """GAM adapter should require GAMInventory records to be synced."""
        from src.services.setup_checklist_service import SetupChecklistService

        tenant_id = "test_tenant"

        with patch("src.services.setup_checklist_service.get_db_session") as mock_db:
            mock_session = MagicMock()
            mock_db.return_value.__enter__.return_value = mock_session

            # Create mock tenant with GAM adapter
            mock_tenant = MagicMock()
            # TenantAIConfig.from_tenant reads these two fields for the optional AI task and
            # refuses a non-dict (a bare MagicMock attribute) by design.
            mock_tenant.ai_config = None
            mock_tenant.gemini_api_key = None
            mock_tenant.tenant_id = tenant_id
            mock_tenant.name = "Test Tenant"
            mock_tenant.ad_server = "google_ad_manager"
            mock_tenant.adapter_config = MagicMock()  # Has adapter config (authenticated)
            mock_tenant.adapter_config.gam_oauth_complete = True
            mock_tenant.is_gam_tenant = True
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
            mock_tenant.auth_setup_mode = True  # Setup mode active

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
            # TenantAIConfig.from_tenant reads these two fields for the optional AI task and
            # refuses a non-dict (a bare MagicMock attribute) by design.
            mock_tenant.ai_config = None
            mock_tenant.gemini_api_key = None
            mock_tenant.tenant_id = tenant_id
            mock_tenant.name = "Test Tenant"
            mock_tenant.ad_server = "google_ad_manager"
            mock_tenant.adapter_config = MagicMock()  # Has adapter config (authenticated)
            mock_tenant.adapter_config.gam_oauth_complete = True
            mock_tenant.is_gam_tenant = True
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
            mock_tenant.auth_setup_mode = True  # Setup mode active

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

    def test_no_adapter_selected_shows_minimal_tasks(self):
        """When no adapter is selected (None), only essential tasks appear."""
        from src.services.setup_checklist_service import SetupChecklistService

        tenant_id = "test_tenant"

        # Test in single-tenant mode (default) where SSO is critical
        with patch.dict("os.environ", {"ADCP_MULTI_TENANT": "false"}, clear=False):
            with patch("src.services.setup_checklist_service.get_db_session") as mock_db:
                mock_session = MagicMock()
                mock_db.return_value.__enter__.return_value = mock_session

                # Create mock tenant with no adapter selected
                mock_tenant = MagicMock()
                # TenantAIConfig.from_tenant reads these two fields for the optional AI task and
                # refuses a non-dict (a bare MagicMock attribute) by design.
                mock_tenant.ai_config = None
                mock_tenant.gemini_api_key = None
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
                mock_tenant.auth_setup_mode = True  # Setup mode active

                # Mock database queries
                mock_session.scalars.return_value.first.return_value = mock_tenant
                mock_session.scalar.return_value = 0

                # Get setup status
                service = SetupChecklistService(tenant_id)
                status = service.get_setup_status()

                # Find critical tasks
                critical_keys = {task["key"] for task in status["critical"]}

                # In single-tenant mode, SSO is critical
                assert "ad_server_connected" in critical_keys, "Ad server task should exist"
                assert "sso_configuration" in critical_keys, "SSO is critical in single-tenant mode"
                assert "authorized_properties" in critical_keys, "Properties task should exist"

                # SSO should NOT be in optional tasks in single-tenant mode
                optional_keys = {task["key"] for task in status["optional"]}
                assert "sso_configuration" not in optional_keys, "SSO should not be optional in single-tenant mode"

                # Inventory sync, currency_limits should NOT be present
                # (these only appear after ad server is fully configured)
                assert "inventory_synced" not in critical_keys, "Inventory task should not appear without ad server"
                assert "currency_limits" not in critical_keys, "Currency task should not appear without ad server"

                # Ad server should be marked incomplete
                ad_server_task = next(t for t in status["critical"] if t["key"] == "ad_server_connected")
                assert ad_server_task["is_complete"] is False, "Ad server should be incomplete when None"

    def test_sso_optional_in_multi_tenant_mode(self):
        """In multi-tenant mode, SSO is optional."""
        from src.services.setup_checklist_service import SetupChecklistService

        tenant_id = "test_tenant"

        # Test in multi-tenant mode where SSO is optional
        with patch.dict("os.environ", {"ADCP_MULTI_TENANT": "true"}, clear=False):
            with patch("src.services.setup_checklist_service.get_db_session") as mock_db:
                mock_session = MagicMock()
                mock_db.return_value.__enter__.return_value = mock_session

                # Create mock tenant
                mock_tenant = MagicMock()
                # TenantAIConfig.from_tenant reads these two fields for the optional AI task and
                # refuses a non-dict (a bare MagicMock attribute) by design.
                mock_tenant.ai_config = None
                mock_tenant.gemini_api_key = None
                mock_tenant.tenant_id = tenant_id
                mock_tenant.name = "Test Tenant"
                mock_tenant.ad_server = "mock"
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
                mock_tenant.auth_setup_mode = True  # Setup mode active

                # Mock database queries
                mock_session.scalars.return_value.first.return_value = mock_tenant
                mock_session.scalar.return_value = 0

                # Get setup status
                service = SetupChecklistService(tenant_id)
                status = service.get_setup_status()

                # Find critical tasks
                critical_keys = {task["key"] for task in status["critical"]}

                # In multi-tenant mode, SSO is NOT critical
                assert "sso_configuration" not in critical_keys, "SSO should not be critical in multi-tenant mode"

                # SSO should be in optional tasks
                optional_keys = {task["key"] for task in status["optional"]}
                assert "sso_configuration" in optional_keys, "SSO should be optional in multi-tenant mode"

    def test_validate_setup_complete_with_all_requirements(self):
        """validate_setup_complete() passes when all requirements are met (in multi-tenant mode)."""
        from src.services.setup_checklist_service import SetupIncompleteError, validate_setup_complete

        tenant_id = "test_tenant"

        # Test in multi-tenant mode where SSO is optional
        with patch.dict("os.environ", {"ADCP_MULTI_TENANT": "true"}, clear=False):
            with patch("src.services.setup_checklist_service.get_db_session") as mock_db:
                mock_session = MagicMock()
                mock_db.return_value.__enter__.return_value = mock_session

                # Create mock tenant with GAM adapter - all critical tasks complete
                mock_tenant = MagicMock()
                # TenantAIConfig.from_tenant reads these two fields for the optional AI task and
                # refuses a non-dict (a bare MagicMock attribute) by design.
                mock_tenant.ai_config = None
                mock_tenant.gemini_api_key = None
                mock_tenant.tenant_id = tenant_id
                mock_tenant.name = "Test Tenant"
                mock_tenant.ad_server = "google_ad_manager"  # Real ad server
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
                mock_tenant.auth_setup_mode = True  # Setup mode still active (SSO is optional in multi-tenant)
                mock_tenant.adapter_config = MagicMock()  # Has adapter config (authenticated)
                mock_tenant.adapter_config.gam_oauth_complete = True

                # SSO is optional in multi-tenant mode, so no need for mock auth config

                # Track which query is being called and return appropriate mock
                def scalars_side_effect(stmt):
                    result = MagicMock()
                    # Return tenant for all queries
                    result.first.return_value = mock_tenant
                    result.all.return_value = []
                    return result

                def scalar_side_effect(stmt):
                    # Return counts for different queries (non-zero = requirements met)
                    return 10  # At least 1 currency, property, product, principal, inventory

                mock_session.scalars.side_effect = scalars_side_effect
                mock_session.scalar.side_effect = scalar_side_effect

                # Mock GEMINI_API_KEY env var
                with patch("os.getenv") as mock_getenv:
                    mock_getenv.return_value = "fake-api-key"

                    # This should NOT raise SetupIncompleteError when all requirements met
                    try:
                        validate_setup_complete(tenant_id)
                        # If we get here, validation passed (expected)
                        assert True
                    except SetupIncompleteError as e:
                        # Should not happen when all requirements met
                        pytest.fail(
                            f"validate_setup_complete raised error unexpectedly: {e.message}. "
                            f"Missing tasks: {e.missing_tasks}"
                        )
