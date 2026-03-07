"""Tests for REST API /api/v1/* endpoints (all handlers except get_products).

Validates that each REST transport endpoint:
- Route exists (not 404)
- Returns 200 with valid mock data
- Auth-optional endpoints work without auth

beads: salesagent-b61l.15
"""

from unittest.mock import MagicMock, patch

from starlette.testclient import TestClient

from src.app import app
from src.core.resolved_identity import ResolvedIdentity

client = TestClient(app)

_MOCK_IDENTITY = ResolvedIdentity(
    principal_id="test-principal",
    tenant_id="default",
    tenant={"tenant_id": "default"},
    auth_token="test-token",
    protocol="rest",
)


# ---------------------------------------------------------------------------
# Discovery endpoints (auth-optional)
# ---------------------------------------------------------------------------


class TestCapabilitiesEndpoint:
    """Verify GET /api/v1/capabilities endpoint."""

    @patch("src.core.tools.capabilities.get_adcp_capabilities_raw")
    def test_returns_200(self, mock_impl):
        mock_impl.return_value = MagicMock(model_dump=lambda **kw: {"supported_protocols": []})
        response = client.get("/api/v1/capabilities")
        assert response.status_code == 200

    @patch("src.core.tools.capabilities.get_adcp_capabilities_raw")
    def test_works_without_auth(self, mock_impl):
        mock_impl.return_value = MagicMock(model_dump=lambda **kw: {"supported_protocols": []})
        response = client.get("/api/v1/capabilities")
        assert response.status_code == 200, "Discovery skill should work without auth"


class TestCreativeFormatsEndpoint:
    """Verify POST /api/v1/creative-formats endpoint."""

    @patch("src.core.tools.creative_formats.list_creative_formats_raw")
    def test_returns_200(self, mock_impl):
        mock_impl.return_value = MagicMock(model_dump=lambda **kw: {"formats": []})
        response = client.post("/api/v1/creative-formats", json={})
        assert response.status_code == 200

    @patch("src.core.tools.creative_formats.list_creative_formats_raw")
    def test_works_without_auth(self, mock_impl):
        mock_impl.return_value = MagicMock(model_dump=lambda **kw: {"formats": []})
        response = client.post("/api/v1/creative-formats", json={})
        assert response.status_code == 200, "Discovery skill should work without auth"


class TestAuthorizedPropertiesEndpoint:
    """Verify POST /api/v1/authorized-properties endpoint."""

    @patch("src.core.tools.properties.list_authorized_properties_raw")
    def test_returns_200(self, mock_impl):
        mock_impl.return_value = MagicMock(model_dump=lambda **kw: {"properties": []})
        response = client.post("/api/v1/authorized-properties", json={})
        assert response.status_code == 200

    @patch("src.core.tools.properties.list_authorized_properties_raw")
    def test_works_without_auth(self, mock_impl):
        mock_impl.return_value = MagicMock(model_dump=lambda **kw: {"properties": []})
        response = client.post("/api/v1/authorized-properties", json={})
        assert response.status_code == 200, "Discovery skill should work without auth"


# ---------------------------------------------------------------------------
# Auth-required endpoints
# ---------------------------------------------------------------------------


class TestCreateMediaBuyEndpoint:
    """Verify POST /api/v1/media-buys endpoint."""

    @patch("src.core.resolved_identity.resolve_identity", return_value=_MOCK_IDENTITY)
    @patch("src.core.tools.media_buy_create.create_media_buy_raw")
    def test_returns_200(self, mock_impl, mock_resolve):
        mock_impl.return_value = MagicMock(model_dump=lambda **kw: {"media_buy_id": "mb1"})
        response = client.post(
            "/api/v1/media-buys",
            json={
                "buyer_ref": "buyer1",
                "brand": {"domain": "testbrand.com"},
                "packages": [{"product_id": "p1", "budget": {"amount": 100, "currency": "USD"}}],
                "start_time": "2026-04-01T00:00:00Z",
                "end_time": "2026-04-30T00:00:00Z",
            },
            headers={"Authorization": "Bearer test-token"},
        )
        assert response.status_code == 200

    def test_requires_auth(self):
        """create_media_buy requires authentication."""
        response = client.post(
            "/api/v1/media-buys",
            json={"buyer_ref": "buyer1", "packages": []},
        )
        assert response.status_code == 401


class TestUpdateMediaBuyEndpoint:
    """Verify PUT /api/v1/media-buys/{id} endpoint."""

    @patch("src.core.resolved_identity.resolve_identity", return_value=_MOCK_IDENTITY)
    @patch("src.core.tools.media_buy_update.update_media_buy_raw")
    def test_returns_200(self, mock_impl, mock_resolve):
        mock_impl.return_value = MagicMock(model_dump=lambda **kw: {"media_buy_id": "mb1"})
        response = client.put(
            "/api/v1/media-buys/mb1",
            json={"paused": True},
            headers={"Authorization": "Bearer test-token"},
        )
        assert response.status_code == 200


class TestGetMediaBuyDeliveryEndpoint:
    """Verify POST /api/v1/media-buys/delivery endpoint."""

    @patch("src.core.resolved_identity.resolve_identity", return_value=_MOCK_IDENTITY)
    @patch("src.core.tools.media_buy_delivery.get_media_buy_delivery_raw")
    def test_returns_200(self, mock_impl, mock_resolve):
        mock_impl.return_value = MagicMock(model_dump=lambda **kw: {"media_buys": []})
        response = client.post(
            "/api/v1/media-buys/delivery",
            json={"media_buy_ids": ["mb1"]},
            headers={"Authorization": "Bearer test-token"},
        )
        assert response.status_code == 200


class TestSyncCreativesEndpoint:
    """Verify POST /api/v1/creatives/sync endpoint."""

    @patch("src.core.resolved_identity.resolve_identity", return_value=_MOCK_IDENTITY)
    @patch("src.core.tools.creatives.sync_wrappers.sync_creatives_raw")
    def test_returns_200(self, mock_impl, mock_resolve):
        mock_impl.return_value = MagicMock(model_dump=lambda **kw: {"creatives": []})
        response = client.post(
            "/api/v1/creatives/sync",
            json={"creatives": []},
            headers={"Authorization": "Bearer test-token"},
        )
        assert response.status_code == 200


class TestListCreativesEndpoint:
    """Verify POST /api/v1/creatives endpoint."""

    @patch("src.core.resolved_identity.resolve_identity", return_value=_MOCK_IDENTITY)
    @patch("src.core.tools.creatives.listing.list_creatives_raw")
    def test_returns_200(self, mock_impl, mock_resolve):
        mock_impl.return_value = MagicMock(model_dump=lambda **kw: {"creatives": []})
        response = client.post(
            "/api/v1/creatives",
            json={},
            headers={"Authorization": "Bearer test-token"},
        )
        assert response.status_code == 200


class TestUpdatePerformanceIndexEndpoint:
    """Verify POST /api/v1/performance-index endpoint."""

    @patch("src.core.resolved_identity.resolve_identity", return_value=_MOCK_IDENTITY)
    @patch("src.core.tools.performance.update_performance_index_raw")
    def test_returns_200(self, mock_impl, mock_resolve):
        mock_impl.return_value = MagicMock(model_dump=lambda **kw: {"status": "ok"})
        response = client.post(
            "/api/v1/performance-index",
            json={"media_buy_id": "mb1", "performance_data": []},
            headers={"Authorization": "Bearer test-token"},
        )
        assert response.status_code == 200
