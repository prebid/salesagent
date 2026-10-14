"""Integration test for custom targeting values endpoint.

This test validates that the /api/tenant/{id}/targeting/values/{key_id} endpoint
correctly queries GAM in real-time to fetch custom targeting values.
"""

from unittest.mock import MagicMock, patch

import pytest

from src.core.database.database_session import get_db_session
from src.core.database.models import AdapterConfig, GAMInventory, Tenant

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


def test_get_targeting_values_endpoint(authenticated_admin_session, integration_db):
    """Test /api/tenant/{id}/targeting/values/{key_id} queries GAM and returns values."""
    # integration_db fixture sets up the database environment
    # Use get_db_session() to interact with it
    with get_db_session() as db_session:
        # Create test tenant with GAM configuration
        # Required (NOT NULL) and UNIQUE. Nothing routes to this host; it exists to satisfy the column.
        tenant = Tenant(
            tenant_id="test_tenant",
            name="Test Tenant",
            subdomain="test",
            virtual_host="test-tenant.adcp.test",
            ad_server="google_ad_manager",
        )
        db_session.add(tenant)
        db_session.flush()

        # Create adapter config
        adapter_config = AdapterConfig(
            tenant_id="test_tenant",
            adapter_type="google_ad_manager",
            gam_network_code="123456",
            gam_refresh_token="test_refresh_token",
        )
        db_session.add(adapter_config)

        # Create custom targeting key (synced from GAM)
        key_id = "123456"
        key = GAMInventory(
            tenant_id="test_tenant",
            inventory_type="custom_targeting_key",
            inventory_id=key_id,
            name="sport",
            status="ACTIVE",
            inventory_metadata={
                "display_name": "Sport",
                "type": "PREDEFINED",
                "reportable_type": "ON",
            },
        )
        db_session.add(key)
        db_session.commit()

    # Mock GAM client to return values
    from src.adapters.gam_inventory_discovery import CustomTargetingValue

    mock_values = [
        CustomTargetingValue(
            id="val_001",
            custom_targeting_key_id=key_id,
            name="basketball",
            display_name="Basketball",
            match_type="EXACT",
            status="ACTIVE",
        ),
        CustomTargetingValue(
            id="val_002",
            custom_targeting_key_id=key_id,
            name="football",
            display_name="Football",
            match_type="EXACT",
            status="ACTIVE",
        ),
        CustomTargetingValue(
            id="val_003",
            custom_targeting_key_id=key_id,
            name="soccer",
            display_name="Soccer",
            match_type="BROAD",
            status="INACTIVE",
        ),
    ]

    with patch("src.adapters.gam_inventory_discovery.GAMInventoryDiscovery") as mock_gam_class:
        with patch("googleads.ad_manager.AdManagerClient") as mock_ad_manager_client:
            with patch("googleads.oauth2.GoogleRefreshTokenClient") as mock_oauth_client:
                mock_gam_instance = MagicMock()
                mock_gam_instance.discover_custom_targeting_values_for_key.return_value = mock_values
                mock_gam_class.return_value = mock_gam_instance

                # Make request to API endpoint
                response = authenticated_admin_session.get(f"/api/tenant/test_tenant/targeting/values/{key_id}")

                # Validate OAuth client was created correctly
                mock_oauth_client.assert_called_once()
                oauth_call_kwargs = mock_oauth_client.call_args.kwargs
                assert oauth_call_kwargs["refresh_token"] == "test_refresh_token"

                # Validate AdManager client was created correctly
                mock_ad_manager_client.assert_called_once()
                ad_manager_call_args = mock_ad_manager_client.call_args
                assert ad_manager_call_args.kwargs["network_code"] == "123456"

                # Validate GAMInventoryDiscovery was instantiated with client and tenant_id
                mock_gam_class.assert_called_once()
                gam_call_kwargs = mock_gam_class.call_args.kwargs
                assert "client" in gam_call_kwargs
                assert gam_call_kwargs["tenant_id"] == "test_tenant"

                mock_gam_instance.discover_custom_targeting_values_for_key.assert_called_once_with(
                    key_id, max_values=1000
                )

    # Validate response
    assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.data}"

    data = response.json
    assert "values" in data
    assert "count" in data
    assert data["count"] == 3

    # Validate value structure
    values = data["values"]
    assert len(values) == 3

    # Check first value (basketball)
    basketball = next((v for v in values if v["name"] == "basketball"), None)
    assert basketball is not None
    assert basketball["id"] == "val_001"
    assert basketball["display_name"] == "Basketball"
    assert basketball["match_type"] == "EXACT"
    assert basketball["status"] == "ACTIVE"
    assert basketball["key_id"] == key_id
    assert basketball["key_name"] == "sport"

    # Check inactive value (soccer)
    soccer = next((v for v in values if v["name"] == "soccer"), None)
    assert soccer is not None
    assert soccer["status"] == "INACTIVE"
    assert soccer["match_type"] == "BROAD"


def test_get_targeting_values_empty_result(authenticated_admin_session, integration_db):
    """Test endpoint returns empty array when GAM returns no values for key."""
    with get_db_session() as db_session:
        # Create test tenant with GAM configuration
        tenant = Tenant(
            tenant_id="test_tenant_empty",
            name="Test Tenant Empty",
            subdomain="testempty",
            virtual_host="test-tenant-empty.adcp.test",
            ad_server="google_ad_manager",
        )
        db_session.add(tenant)
        db_session.flush()

        # Create adapter config
        adapter_config = AdapterConfig(
            tenant_id="test_tenant_empty",
            adapter_type="google_ad_manager",
            gam_network_code="123456",
            gam_refresh_token="test_refresh_token",
        )
        db_session.add(adapter_config)

        # Create custom targeting key with no values
        key_id = "999999"
        key = GAMInventory(
            tenant_id="test_tenant_empty",
            inventory_type="custom_targeting_key",
            inventory_id=key_id,
            name="empty_key",
            status="ACTIVE",
            inventory_metadata={"display_name": "Empty Key", "type": "FREEFORM"},
        )
        db_session.add(key)
        db_session.commit()

    # Mock GAM client to return no values
    with patch("src.adapters.gam_inventory_discovery.GAMInventoryDiscovery") as mock_gam_class:
        mock_gam_instance = MagicMock()
        mock_gam_instance.discover_custom_targeting_values_for_key.return_value = []
        mock_gam_class.return_value = mock_gam_instance

        # Make request to API endpoint
        response = authenticated_admin_session.get(f"/api/tenant/test_tenant_empty/targeting/values/{key_id}")

    # Validate response
    assert response.status_code == 200
    data = response.json
    assert data["count"] == 0
    assert data["values"] == []


def test_get_targeting_values_tenant_isolation(authenticated_admin_session, integration_db):
    """Test endpoint requires GAM configuration (validates tenant isolation at config level)."""
    with get_db_session() as db_session:
        # Create tenant without GAM configuration
        tenant = Tenant(
            tenant_id="tenant_a",
            name="Tenant A",
            subdomain="tenanta",
            virtual_host="tenant-a.adcp.test",
        )
        db_session.add(tenant)

        # Create key
        key_id = "shared_key_123"
        key = GAMInventory(
            tenant_id="tenant_a",
            inventory_type="custom_targeting_key",
            inventory_id=key_id,
            name="category",
            status="ACTIVE",
            inventory_metadata={"display_name": "Category", "type": "PREDEFINED"},
        )
        db_session.add(key)
        db_session.commit()

    # Request values without GAM configured - should fail
    response = authenticated_admin_session.get(f"/api/tenant/tenant_a/targeting/values/{key_id}")

    assert response.status_code == 400
    data = response.json
    assert "error" in data
    # Updated to match new, more specific error message
    assert "No adapter configured" in data["error"]


def test_get_targeting_values_requires_auth(admin_client, integration_db):
    """Test endpoint requires authentication."""
    with get_db_session() as db_session:
        # Create test tenant and key
        tenant = Tenant(
            tenant_id="auth_test",
            name="Auth Test",
            subdomain="authtest",
            virtual_host="auth-test.adcp.test",
        )
        db_session.add(tenant)
        db_session.commit()

    key_id = "auth_key_123"

    # Attempt to access without authentication
    response = admin_client.get(f"/api/tenant/auth_test/targeting/values/{key_id}")

    # Should redirect to login or return 401/403
    assert response.status_code in [302, 401, 403]
