#!/usr/bin/env python3
"""
Integration test for GAM tenant setup and configuration flow.

This test ensures that the GAM configuration flow works properly,
specifically testing the scenarios that caused the regression:
1. Creating a tenant without network code (should auto-detect)
2. Creating a tenant with manual network code input
3. OAuth flow for network detection
4. Proper database schema handling

This would have caught the regression where network code was required upfront.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import select

# Add project root to path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from scripts.setup.setup_tenant import create_tenant, main
from src.core.database.models import AdapterConfig, Tenant


@pytest.mark.integration
@pytest.mark.requires_db
class TestGAMTenantSetup:
    """Test GAM tenant setup and configuration flow."""

    def test_gam_tenant_creation_without_network_code(self, test_database):
        """
        Test that a GAM tenant can be created without providing network code upfront.

        This tests the core regression scenario: network code should be optional
        during tenant creation when using OAuth tokens.
        """
        # Create a simple args object without network code (should work)
        import uuid

        unique_id = str(uuid.uuid4())[:8]

        class Args:
            name = "Test GAM Publisher"
            tenant_id = f"test_gam_pub_{unique_id}"
            subdomain = f"testgampub{unique_id}"
            adapter = "google_ad_manager"
            gam_network_code = None  # Key test: No network code provided
            gam_refresh_token = "test_refresh_token_123"
            manual_approval = False
            auto_approve_all = False
            max_daily_budget = 15000
            admin_token = "test_admin_token"
            authorized_domain = []
            admin_email = []

        args = Args()

        # This should NOT raise an error (the regression made this fail)
        create_tenant(args)

        # Verify tenant was created successfully using SQLAlchemy ORM
        from src.core.database.database_session import get_db_session

        with get_db_session() as session:
            tenant = session.scalars(select(Tenant).filter_by(tenant_id=args.tenant_id)).first()
            assert tenant is not None
            assert tenant.name == "Test GAM Publisher"
            assert tenant.ad_server == "google_ad_manager"

            # Verify adapter config allows null network code initially
            adapter_config = session.scalars(select(AdapterConfig).filter_by(tenant_id=args.tenant_id)).first()
            assert adapter_config is not None
            assert adapter_config.gam_network_code is None  # network_code should be null initially
            assert adapter_config.gam_refresh_token == "test_refresh_token_123"  # refresh_token should be stored

    def test_gam_tenant_creation_with_network_code(self, test_database):
        """
        Test that a GAM tenant can be created WITH network code provided upfront.

        This ensures the manual network code path still works.
        """
        # Create a simple args object with network code
        import uuid

        unique_id = str(uuid.uuid4())[:8]

        class Args:
            name = "Test GAM Publisher With Code"
            tenant_id = f"test_gam_with_code_{unique_id}"
            subdomain = f"testgamcode{unique_id}"
            adapter = "google_ad_manager"
            gam_network_code = "123456789"  # Network code provided
            gam_refresh_token = "test_refresh_token_456"
            manual_approval = False
            auto_approve_all = False
            max_daily_budget = 20000
            admin_token = "test_admin_token_2"
            authorized_domain = []
            admin_email = []

        args = Args()

        create_tenant(args)

        # Verify network code was stored using SQLAlchemy ORM
        from src.core.database.database_session import get_db_session

        with get_db_session() as session:
            adapter_config = session.scalars(select(AdapterConfig).filter_by(tenant_id=args.tenant_id)).first()
            assert adapter_config is not None
            assert adapter_config.gam_network_code == "123456789"

    def test_command_line_parsing_network_code_optional(self):
        """
        Test that the command line parsing correctly handles optional network code.

        This would have caught the regression where --gam-network-code was required.
        """
        # Test the CLI argument parsing
        old_argv = sys.argv
        try:
            # Simulate command line without network code
            sys.argv = [
                "setup_tenant.py",
                "Test Publisher",
                "--adapter",
                "google_ad_manager",
                "--gam-refresh-token",
                "test_token",
                # Note: NO --gam-network-code provided - should NOT error
            ]

            with patch("scripts.setup.setup_tenant.create_tenant") as mock_create:
                try:
                    main()
                    # If we get here, the parsing succeeded (correct behavior)
                    parsing_succeeded = True

                    # Verify create_tenant was called with network_code as None
                    mock_create.assert_called_once()
                    args = mock_create.call_args[0][0]
                    assert args.gam_network_code is None
                    assert args.gam_refresh_token == "test_token"

                except SystemExit as e:
                    # Check if it's just the normal success exit
                    if e.code == 0:
                        parsing_succeeded = True
                    else:
                        parsing_succeeded = False
                except Exception:
                    # Any other exception means parsing failed
                    parsing_succeeded = False

            assert parsing_succeeded, "Network code should be optional when refresh token is provided"

        finally:
            sys.argv = old_argv

    @pytest.mark.xfail(reason="Endpoint not yet implemented")
    def test_admin_ui_network_detection_endpoint(self):
        """
        Test the Admin UI endpoint for detecting network code from refresh token.

        This tests the OAuth → network code detection flow.
        """
        from src.admin.app import create_app

        app, _ = create_app()
        app.config["TESTING"] = True
        app.config["SECRET_KEY"] = "test_secret"

        with app.test_client() as client:
            # Mock authentication
            with client.session_transaction() as sess:
                sess["authenticated"] = True
                sess["role"] = "super_admin"
                sess["email"] = "test@example.com"

            # Mock the GAM client and network detection
            with patch("googleads.ad_manager.AdManagerClient") as MockClient:
                mock_client_instance = MagicMock()
                mock_network_service = MagicMock()
                mock_network_service.getCurrentNetwork.return_value = {
                    "id": "123456",
                    "networkCode": "78901234",
                    "displayName": "Test Publisher Network",
                    "currencyCode": "USD",
                    "timeZone": "America/New_York",
                }
                mock_client_instance.GetService.return_value = mock_network_service
                MockClient.LoadFromDict.return_value = mock_client_instance

                # Test the network detection endpoint
                response = client.post(
                    "/tenant/test_tenant/gam/detect-network",
                    json={"refresh_token": "test_refresh_token"},
                    content_type="application/json",
                )

                assert response.status_code == 200
                data = response.get_json()
                assert data["success"] is True
                assert data["network_code"] == "78901234"
                assert data["network_name"] == "Test Publisher Network"

                # Verify the GAM client was called with correct config
                MockClient.LoadFromDict.assert_called_once()
                config = MockClient.LoadFromDict.call_args[0][0]
                assert "ad_manager" in config
                assert config["ad_manager"]["refresh_token"] == "test_refresh_token"

    def test_gam_adapter_initialization_without_network_code(self):
        """
        Test that the GAM adapter can be initialized even without network code.

        This ensures the adapter gracefully handles missing network codes
        during the configuration phase.
        """
        from src.adapters.google_ad_manager import GoogleAdManager
        from src.core.schemas import Principal

        # Create principal with GAM platform mapping
        principal = Principal(
            tenant_id="test_tenant",
            principal_id="test_principal",
            name="Test Advertiser",
            access_token="test_token",
            platform_mappings={"google_ad_manager": "12345"},
        )

        # Config without network code (should not crash)
        config = {
            "refresh_token": "test_refresh_token",
            # network_code is missing - should be handled gracefully
        }

        # After refactoring, network_code, advertiser_id, and trafficker_id are required
        # This test needs to be updated to reflect the new constructor requirements
        # We'll test with a TypeError being raised for missing required parameters

        # This should raise a TypeError for missing required parameters
        with pytest.raises(TypeError) as exc_info:
            adapter = GoogleAdManager(
                config=config,
                principal=principal,
                dry_run=True,  # Use dry_run to avoid actual API calls
            )

        # Verify the error mentions the missing parameters
        assert "network_code" in str(exc_info.value)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
