"""Integration test for custom targeting values endpoint.

This test validates that the /api/tenant/{id}/targeting/values/{key_id} endpoint
correctly queries GAM in real-time to fetch custom targeting values.
"""

from unittest.mock import ANY, patch

import pytest

from tests.factories import AdapterConfigFactory, GAMInventoryFactory, TenantFactory

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


@pytest.fixture
def gam_custom_targeting_key(bound_factory_session):
    """A GAM tenant with OAuth credentials and one synced custom targeting key."""
    tenant = TenantFactory(ad_server="google_ad_manager")
    AdapterConfigFactory(
        tenant=tenant,
        adapter_type="google_ad_manager",
        gam_network_code="123456",
        gam_refresh_token="test_refresh_token",
    )
    key = GAMInventoryFactory(
        tenant=tenant,
        inventory_type="custom_targeting_key",
        inventory_id="11001",
        name="sport",
    )
    return tenant, key


@pytest.fixture
def gam_discovery():
    """Stand-in for the GAM API: the inventory discovery class, answering no values by default."""
    with patch("src.adapters.gam_inventory_discovery.GAMInventoryDiscovery") as discovery_class:
        discovery_class.return_value.discover_custom_targeting_values_for_key.return_value = []
        yield discovery_class


def _values_url(tenant_id, key_id):
    return f"/api/tenant/{tenant_id}/targeting/values/{key_id}"


def test_get_targeting_values_endpoint(authenticated_admin_session, gam_custom_targeting_key, gam_discovery):
    """Test /api/tenant/{id}/targeting/values/{key_id} queries GAM and returns values."""
    from src.adapters.gam_inventory_discovery import CustomTargetingValue

    tenant, key = gam_custom_targeting_key
    discover = gam_discovery.return_value.discover_custom_targeting_values_for_key
    discover.return_value = [
        CustomTargetingValue(
            id="val_001",
            custom_targeting_key_id=key.inventory_id,
            name="basketball",
            display_name="Basketball",
            match_type="EXACT",
            status="ACTIVE",
        ),
        CustomTargetingValue(
            id="val_002",
            custom_targeting_key_id=key.inventory_id,
            name="football",
            display_name="Football",
            match_type="EXACT",
            status="ACTIVE",
        ),
        CustomTargetingValue(
            id="val_003",
            custom_targeting_key_id=key.inventory_id,
            name="soccer",
            display_name="Soccer",
            match_type="BROAD",
            status="INACTIVE",
        ),
    ]

    with patch("googleads.ad_manager.AdManagerClient") as mock_ad_manager_client:
        with patch("googleads.oauth2.GoogleRefreshTokenClient") as mock_oauth_client:
            response = authenticated_admin_session.get(_values_url(tenant.tenant_id, key.inventory_id))

    # The client is built from the tenant's stored credentials and network code.
    mock_oauth_client.assert_called_once_with(client_id=ANY, client_secret=ANY, refresh_token="test_refresh_token")
    mock_ad_manager_client.assert_called_once_with(
        mock_oauth_client.return_value, "Prebid Sales Agent", network_code="123456"
    )
    gam_discovery.assert_called_once_with(client=mock_ad_manager_client.return_value, tenant_id=tenant.tenant_id)
    discover.assert_called_once_with(key.inventory_id, max_values=1000)

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
    assert basketball["key_id"] == key.inventory_id
    assert basketball["key_name"] == "sport"

    # Check inactive value (soccer)
    soccer = next((v for v in values if v["name"] == "soccer"), None)
    assert soccer is not None
    assert soccer["status"] == "INACTIVE"
    assert soccer["match_type"] == "BROAD"


def test_get_targeting_values_empty_result(authenticated_admin_session, gam_custom_targeting_key, gam_discovery):
    """Test endpoint returns empty array when GAM returns no values for key."""
    tenant, key = gam_custom_targeting_key

    response = authenticated_admin_session.get(_values_url(tenant.tenant_id, key.inventory_id))

    assert response.status_code == 200
    data = response.json
    assert data["count"] == 0
    assert data["values"] == []


def test_get_targeting_values_is_never_cached(authenticated_admin_session, gam_custom_targeting_key, gam_discovery):
    """The values come from GAM live, so no cache may serve an earlier answer (#1045)."""
    tenant, key = gam_custom_targeting_key

    response = authenticated_admin_session.get(_values_url(tenant.tenant_id, key.inventory_id))

    assert response.status_code == 200, response.data
    assert response.headers.get("Cache-Control") == "no-store"


def test_get_targeting_values_tenant_isolation(authenticated_admin_session, bound_factory_session):
    """A tenant with no ad server credentials is refused, never answered from another tenant's GAM."""
    key = GAMInventoryFactory(inventory_type="custom_targeting_key")

    response = authenticated_admin_session.get(_values_url(key.tenant_id, key.inventory_id))

    assert response.status_code == 400
    assert response.json == {"error": "No adapter configured for this tenant"}


def test_get_targeting_values_requires_auth(admin_client, bound_factory_session):
    """An unauthenticated request is turned away even when the tenant and key exist."""
    key = GAMInventoryFactory(inventory_type="custom_targeting_key")

    response = admin_client.get(_values_url(key.tenant_id, key.inventory_id))

    # Should redirect to login or return 401/403
    assert response.status_code in [302, 401, 403]
