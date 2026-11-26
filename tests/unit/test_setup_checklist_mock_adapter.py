"""Test that setup checklist correctly handles mock adapter.

The mock adapter is for testing/development only - it's NOT considered a fully configured
production ad server. This means:
1. SSO is still required (regardless of ad server)
2. Currency limits, inventory sync, products, and principals tasks don't appear
   until a real ad server (like GAM) is configured
"""

from unittest.mock import MagicMock, patch

from src.core.database.repositories.publisher import AuthorizedCounts
from tests.utils.database_helpers import vhost_for


class TestSetupChecklistMockAdapter:
    """Test setup checklist service handles mock adapter correctly."""

    def test_mock_adapter_not_fully_configured_in_production(self, tenant_row, scalars_by_entity):
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

                # A tenant row with mock adapter
                tenant = tenant_row(
                    tenant_id=tenant_id, ad_server="mock", auth_setup_mode=True, virtual_host=vhost_for(tenant_id)
                )

                # Mock database queries
                mock_session.scalars.side_effect = scalars_by_entity(tenant)
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

    def test_gam_adapter_inventory_sync_requires_database_records(self, tenant_row, scalars_by_entity):
        """GAM adapter should require GAMInventory records to be synced."""
        from src.services.setup_checklist_service import SetupChecklistService

        tenant_id = "test_tenant"

        with patch("src.services.setup_checklist_service.get_db_session") as mock_db:
            mock_session = MagicMock()
            mock_db.return_value.__enter__.return_value = mock_session

            # A tenant row with GAM adapter
            tenant = tenant_row(
                tenant_id=tenant_id,
                ad_server="google_ad_manager",
                auth_setup_mode=True,
                virtual_host=vhost_for(tenant_id),
            )

            # Mock database queries - no inventory synced
            mock_session.scalars.side_effect = scalars_by_entity(tenant)
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

    def test_gam_adapter_inventory_sync_complete_with_records(self, tenant_row, scalars_by_entity):
        """GAM adapter with synced inventory should have task marked complete."""
        from src.services.setup_checklist_service import SetupChecklistService

        tenant_id = "test_tenant"

        with patch("src.services.setup_checklist_service.get_db_session") as mock_db:
            mock_session = MagicMock()
            mock_db.return_value.__enter__.return_value = mock_session

            # A tenant row with GAM adapter
            tenant = tenant_row(
                tenant_id=tenant_id,
                ad_server="google_ad_manager",
                auth_setup_mode=True,
                virtual_host=vhost_for(tenant_id),
            )

            # Mock database queries - inventory synced (1000 records)
            def scalar_side_effect(stmt):
                # Return different values based on the query
                # This is a simplification - in reality we'd inspect the statement
                return 1000  # GAMInventory count

            mock_session.scalars.side_effect = scalars_by_entity(tenant)
            mock_session.scalar.side_effect = scalar_side_effect

            # Get setup status
            service = SetupChecklistService(tenant_id)
            status = service.get_setup_status()

            # Find inventory sync task in critical tasks
            inventory_task = next((task for task in status["critical"] if task["key"] == "inventory_synced"), None)

            # Verify inventory sync task is marked complete
            assert inventory_task is not None, "Inventory sync task should exist"
            assert inventory_task["is_complete"] is True, "GAM adapter with synced inventory should be complete"

    def test_no_adapter_selected_shows_minimal_tasks(self, tenant_row, scalars_by_entity):
        """When no adapter is selected (None), only essential tasks appear."""
        from src.services.setup_checklist_service import SetupChecklistService

        tenant_id = "test_tenant"

        # Test in single-tenant mode (default) where SSO is critical
        with patch.dict("os.environ", {"ADCP_MULTI_TENANT": "false"}, clear=False):
            with patch("src.services.setup_checklist_service.get_db_session") as mock_db:
                mock_session = MagicMock()
                mock_db.return_value.__enter__.return_value = mock_session

                # A tenant row with no adapter selected
                tenant = tenant_row(
                    tenant_id=tenant_id, ad_server=None, auth_setup_mode=True, virtual_host=vhost_for(tenant_id)
                )

                # Mock database queries
                mock_session.scalars.side_effect = scalars_by_entity(tenant)
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

    def test_sso_optional_in_multi_tenant_mode(self, tenant_row, scalars_by_entity):
        """In multi-tenant mode, SSO is optional."""
        from src.services.setup_checklist_service import SetupChecklistService

        tenant_id = "test_tenant"

        # Test in multi-tenant mode where SSO is optional
        with patch.dict("os.environ", {"ADCP_MULTI_TENANT": "true"}, clear=False):
            with patch("src.services.setup_checklist_service.get_db_session") as mock_db:
                mock_session = MagicMock()
                mock_db.return_value.__enter__.return_value = mock_session

                # A tenant row
                tenant = tenant_row(
                    tenant_id=tenant_id, ad_server="mock", auth_setup_mode=True, virtual_host=vhost_for(tenant_id)
                )

                # Mock database queries
                mock_session.scalars.side_effect = scalars_by_entity(tenant)
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

    def test_validate_setup_complete_with_all_requirements(self, tenant_row, scalars_by_entity):
        """validate_setup_complete() passes when all requirements are met (in multi-tenant mode)."""
        from src.services.setup_checklist_service import validate_setup_complete

        tenant_id = "test_tenant"

        # Test in multi-tenant mode where SSO is optional
        with patch.dict("os.environ", {"ADCP_MULTI_TENANT": "true"}, clear=False):
            with patch("src.services.setup_checklist_service.get_db_session") as mock_db:
                mock_session = MagicMock()
                mock_db.return_value.__enter__.return_value = mock_session

                # A tenant row with GAM adapter - all critical tasks complete
                tenant = tenant_row(
                    tenant_id=tenant_id,
                    ad_server="google_ad_manager",
                    auth_setup_mode=True,
                    authorized_domains=["example.com"],
                    virtual_host=vhost_for(tenant_id),
                )

                # SSO is optional in multi-tenant mode, so no need for mock auth config

                def scalar_side_effect(stmt):
                    # Return counts for different queries (non-zero = requirements met)
                    return 10  # At least 1 currency, property, product, principal, inventory

                mock_session.scalars.side_effect = scalars_by_entity(tenant)
                mock_session.scalar.side_effect = scalar_side_effect

                # The authorized properties are a repository read (one authorizing publisher,
                # two properties), not a counted scalar.
                with patch(
                    "src.services.setup_checklist_service.PublisherRepository.count_authorized",
                    return_value=AuthorizedCounts(publishers=1, properties=2, stale=0),
                ):
                    # Returns without raising when every requirement is met.
                    validate_setup_complete(tenant_id)
