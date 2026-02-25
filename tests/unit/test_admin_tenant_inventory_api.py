"""Tests for the tenant admin inventory & properties API router.

Uses TestClient against the FastAPI app to verify:
- Authorized property CRUD endpoints
- Inventory discovery endpoints
- Inventory profile endpoints
"""

from unittest.mock import MagicMock, patch

import pytest
from starlette.testclient import TestClient

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

TENANT_ID = "tenant_test123"


@pytest.fixture
def mock_tenant():
    tenant = MagicMock()
    tenant.tenant_id = TENANT_ID
    tenant.admin_token = "valid-token"
    return tenant


@pytest.fixture
def client():
    from src.app import app

    yield TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def auth_header():
    return {"Authorization": "Bearer valid-token"}


# ---------------------------------------------------------------------------
# Authorized Property Tests
# ---------------------------------------------------------------------------


class TestListProperties:
    @patch("src.routes.admin_tenant._property_svc")
    @patch("src.routes.admin_tenant.require_tenant_admin")
    def test_returns_properties(self, mock_auth, mock_svc, client, auth_header):
        mock_svc.list_properties.return_value = {
            "properties": [
                {"property_id": "p1", "name": "Homepage", "publisher_domain": "example.com"},
            ],
            "count": 1,
            "status_counts": {"verified": 0, "pending": 1, "failed": 0},
        }
        response = client.get(f"/api/v1/admin/{TENANT_ID}/properties", headers=auth_header)
        assert response.status_code == 200
        assert response.json()["count"] == 1


class TestCreateProperty:
    @patch("src.routes.admin_tenant._property_svc")
    @patch("src.routes.admin_tenant.require_tenant_admin")
    def test_creates_property(self, mock_auth, mock_svc, client, auth_header):
        mock_svc.create_property.return_value = {
            "property_id": "prop_abc",
            "tenant_id": TENANT_ID,
            "name": "Homepage",
            "publisher_domain": "example.com",
        }
        response = client.post(
            f"/api/v1/admin/{TENANT_ID}/properties",
            json={"name": "Homepage", "publisher_domain": "example.com"},
            headers=auth_header,
        )
        assert response.status_code == 201
        assert response.json()["property_id"] == "prop_abc"


class TestUpdateProperty:
    @patch("src.routes.admin_tenant._property_svc")
    @patch("src.routes.admin_tenant.require_tenant_admin")
    def test_updates_property(self, mock_auth, mock_svc, client, auth_header):
        mock_svc.update_property.return_value = {
            "property_id": "p1",
            "name": "Updated",
            "verification_status": "pending",
        }
        response = client.put(
            f"/api/v1/admin/{TENANT_ID}/properties/p1",
            json={"name": "Updated"},
            headers=auth_header,
        )
        assert response.status_code == 200


class TestDeleteProperty:
    @patch("src.routes.admin_tenant._property_svc")
    @patch("src.routes.admin_tenant.require_tenant_admin")
    def test_deletes_property(self, mock_auth, mock_svc, client, auth_header):
        mock_svc.delete_property.return_value = {"message": "Deleted", "property_id": "p1"}
        response = client.delete(f"/api/v1/admin/{TENANT_ID}/properties/p1", headers=auth_header)
        assert response.status_code == 200


class TestBulkUpload:
    @patch("src.routes.admin_tenant._property_svc")
    @patch("src.routes.admin_tenant.require_tenant_admin")
    def test_bulk_upload(self, mock_auth, mock_svc, client, auth_header):
        mock_svc.bulk_upload.return_value = {"success_count": 2, "error_count": 0, "errors": []}
        response = client.post(
            f"/api/v1/admin/{TENANT_ID}/properties/bulk",
            json={
                "properties": [
                    {"name": "Site A", "publisher_domain": "a.com"},
                    {"name": "Site B", "publisher_domain": "b.com"},
                ]
            },
            headers=auth_header,
        )
        assert response.status_code == 200
        assert response.json()["success_count"] == 2


class TestVerifyProperties:
    @patch("src.routes.admin_tenant._property_svc")
    @patch("src.routes.admin_tenant.require_tenant_admin")
    def test_triggers_verification(self, mock_auth, mock_svc, client, auth_header):
        mock_svc.verify_properties.return_value = {"total_checked": 5, "verified": 3, "failed": 2}
        response = client.post(f"/api/v1/admin/{TENANT_ID}/properties/verify", headers=auth_header)
        assert response.status_code == 200
        assert response.json()["total_checked"] == 5


# ---------------------------------------------------------------------------
# Inventory Discovery Tests
# ---------------------------------------------------------------------------


class TestGetInventory:
    @patch("src.routes.admin_tenant._inventory_svc")
    @patch("src.routes.admin_tenant.require_tenant_admin")
    def test_returns_inventory(self, mock_auth, mock_svc, client, auth_header):
        mock_svc.get_inventory.return_value = {
            "items": [
                {"inventory_id": "123", "name": "Homepage Banner", "inventory_type": "ad_unit"},
            ],
            "count": 1,
        }
        response = client.get(f"/api/v1/admin/{TENANT_ID}/inventory", headers=auth_header)
        assert response.status_code == 200
        assert response.json()["count"] == 1

    @patch("src.routes.admin_tenant._inventory_svc")
    @patch("src.routes.admin_tenant.require_tenant_admin")
    def test_filters_by_type(self, mock_auth, mock_svc, client, auth_header):
        mock_svc.get_inventory.return_value = {"items": [], "count": 0}
        response = client.get(
            f"/api/v1/admin/{TENANT_ID}/inventory?inventory_type=placement",
            headers=auth_header,
        )
        assert response.status_code == 200
        mock_svc.get_inventory.assert_called_once_with(
            TENANT_ID, inventory_type="placement", status=None, search=None, limit=500
        )


class TestGetSizes:
    @patch("src.routes.admin_tenant._inventory_svc")
    @patch("src.routes.admin_tenant.require_tenant_admin")
    def test_returns_sizes(self, mock_auth, mock_svc, client, auth_header):
        mock_svc.get_sizes.return_value = {"sizes": ["300x250", "728x90"], "count": 2}
        response = client.get(f"/api/v1/admin/{TENANT_ID}/inventory/sizes", headers=auth_header)
        assert response.status_code == 200
        assert response.json()["count"] == 2


class TestGetTargeting:
    @patch("src.routes.admin_tenant._inventory_svc")
    @patch("src.routes.admin_tenant.require_tenant_admin")
    def test_returns_targeting_data(self, mock_auth, mock_svc, client, auth_header):
        mock_svc.get_targeting.return_value = {
            "custom_targeting_keys": [{"name": "category"}],
            "audience_segments": [],
            "labels": [],
        }
        response = client.get(f"/api/v1/admin/{TENANT_ID}/targeting", headers=auth_header)
        assert response.status_code == 200
        assert len(response.json()["custom_targeting_keys"]) == 1


class TestGetTargetingValues:
    @patch("src.routes.admin_tenant._inventory_svc")
    @patch("src.routes.admin_tenant.require_tenant_admin")
    def test_returns_values(self, mock_auth, mock_svc, client, auth_header):
        mock_svc.get_targeting_values.return_value = {
            "key_id": "123",
            "key_name": "category",
            "values": ["sports", "news"],
            "count": 2,
        }
        response = client.get(f"/api/v1/admin/{TENANT_ID}/targeting/123/values", headers=auth_header)
        assert response.status_code == 200
        assert response.json()["count"] == 2

    @patch("src.routes.admin_tenant._inventory_svc")
    @patch("src.routes.admin_tenant.require_tenant_admin")
    def test_not_found_returns_404(self, mock_auth, mock_svc, client, auth_header):
        from src.core.exceptions import AdCPNotFoundError

        mock_svc.get_targeting_values.side_effect = AdCPNotFoundError("Not found")
        response = client.get(f"/api/v1/admin/{TENANT_ID}/targeting/999/values", headers=auth_header)
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Inventory Profile Tests
# ---------------------------------------------------------------------------


class TestListProfiles:
    @patch("src.routes.admin_tenant._profile_svc")
    @patch("src.routes.admin_tenant.require_tenant_admin")
    def test_returns_profiles(self, mock_auth, mock_svc, client, auth_header):
        mock_svc.list_profiles.return_value = {
            "profiles": [
                {"profile_id": "homepage_takeover", "name": "Homepage Takeover", "product_count": 2},
            ],
            "count": 1,
        }
        response = client.get(f"/api/v1/admin/{TENANT_ID}/inventory-profiles", headers=auth_header)
        assert response.status_code == 200
        assert response.json()["count"] == 1


class TestCreateProfile:
    @patch("src.routes.admin_tenant._profile_svc")
    @patch("src.routes.admin_tenant.require_tenant_admin")
    def test_creates_profile(self, mock_auth, mock_svc, client, auth_header):
        mock_svc.create_profile.return_value = {
            "id": 1,
            "profile_id": "homepage_takeover",
            "tenant_id": TENANT_ID,
            "name": "Homepage Takeover",
        }
        response = client.post(
            f"/api/v1/admin/{TENANT_ID}/inventory-profiles",
            json={"name": "Homepage Takeover", "inventory_config": {"ad_units": ["123"]}},
            headers=auth_header,
        )
        assert response.status_code == 201
        assert response.json()["profile_id"] == "homepage_takeover"


class TestUpdateProfile:
    @patch("src.routes.admin_tenant._profile_svc")
    @patch("src.routes.admin_tenant.require_tenant_admin")
    def test_updates_profile(self, mock_auth, mock_svc, client, auth_header):
        mock_svc.update_profile.return_value = {
            "profile_id": "homepage_takeover",
            "name": "Updated Profile",
        }
        response = client.put(
            f"/api/v1/admin/{TENANT_ID}/inventory-profiles/homepage_takeover",
            json={"name": "Updated Profile"},
            headers=auth_header,
        )
        assert response.status_code == 200


class TestDeleteProfile:
    @patch("src.routes.admin_tenant._profile_svc")
    @patch("src.routes.admin_tenant.require_tenant_admin")
    def test_deletes_profile(self, mock_auth, mock_svc, client, auth_header):
        mock_svc.delete_profile.return_value = {"message": "Deleted", "profile_id": "homepage_takeover"}
        response = client.delete(
            f"/api/v1/admin/{TENANT_ID}/inventory-profiles/homepage_takeover",
            headers=auth_header,
        )
        assert response.status_code == 200

    @patch("src.routes.admin_tenant._profile_svc")
    @patch("src.routes.admin_tenant.require_tenant_admin")
    def test_cannot_delete_referenced_profile(self, mock_auth, mock_svc, client, auth_header):
        from src.core.exceptions import AdCPValidationError

        mock_svc.delete_profile.side_effect = AdCPValidationError("Referenced by products")
        response = client.delete(
            f"/api/v1/admin/{TENANT_ID}/inventory-profiles/homepage_takeover",
            headers=auth_header,
        )
        assert response.status_code == 400


# ---------------------------------------------------------------------------
# OpenAPI Surface Test
# ---------------------------------------------------------------------------


class TestPhase3OpenAPISurface:
    """Verify all Phase 3 endpoints appear in the OpenAPI spec."""

    def test_all_inventory_endpoints_registered(self, client):
        response = client.get("/openapi.json")
        assert response.status_code == 200
        paths = response.json()["paths"]

        expected_paths = [
            "/api/v1/admin/{tenant_id}/properties",
            "/api/v1/admin/{tenant_id}/properties/{property_id}",
            "/api/v1/admin/{tenant_id}/properties/bulk",
            "/api/v1/admin/{tenant_id}/properties/verify",
            "/api/v1/admin/{tenant_id}/inventory",
            "/api/v1/admin/{tenant_id}/inventory/sizes",
            "/api/v1/admin/{tenant_id}/targeting",
            "/api/v1/admin/{tenant_id}/targeting/{key_id}/values",
            "/api/v1/admin/{tenant_id}/inventory-profiles",
            "/api/v1/admin/{tenant_id}/inventory-profiles/{profile_id}",
        ]

        for path in expected_paths:
            assert path in paths, f"Missing endpoint in OpenAPI spec: {path}"
