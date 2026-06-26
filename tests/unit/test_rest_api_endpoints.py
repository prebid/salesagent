"""Tests for REST API /api/v1/* endpoints (all handlers except get_products).

Validates that each REST transport endpoint:
- Route exists (not 404)
- Returns 200 with valid mock data
- Auth-optional endpoints work without auth

beads: salesagent-b61l.15
"""

from unittest.mock import MagicMock, patch

from adcp.types import AccountReference as LibraryAccountReference
from starlette.testclient import TestClient

from src.app import app
from src.core.resolved_identity import ResolvedIdentity
from tests.helpers import assert_envelope_shape

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


# ---------------------------------------------------------------------------
# Auth-required endpoints
# ---------------------------------------------------------------------------


class TestCreateMediaBuyEndpoint:
    """Verify POST /api/v1/media-buys endpoint."""

    def test_requires_auth(self):
        """create_media_buy requires authentication."""
        response = client.post(
            "/api/v1/media-buys",
            json={"packages": []},
        )
        assert response.status_code == 401


class TestGetMediaBuyDeliveryEndpoint:
    """Verify POST /api/v1/media-buys/delivery endpoint."""

    @patch("src.core.resolved_identity.resolve_identity", return_value=_MOCK_IDENTITY)
    @patch("src.core.transport_helpers.enrich_identity_with_account")
    @patch("src.core.tools.media_buy_delivery.get_media_buy_delivery_raw")
    def test_account_is_coerced_before_enriching_identity(self, mock_impl, mock_enrich, mock_resolve):
        enriched_identity = _MOCK_IDENTITY.model_copy(update={"account_id": "acct-1"})
        mock_enrich.return_value = enriched_identity
        mock_impl.return_value = MagicMock(model_dump=lambda **kw: {"media_buys": []})

        response = client.post(
            "/api/v1/media-buys/delivery",
            json={
                "media_buy_ids": ["mb1"],
                "account": {"brand": {"domain": "example.com"}, "operator": "op-1", "sandbox": False},
            },
            headers={"Authorization": "Bearer test-token"},
        )

        assert response.status_code == 200
        expected_account = LibraryAccountReference.model_validate(
            {"brand": {"domain": "example.com"}, "operator": "op-1", "sandbox": False}
        )
        mock_enrich.assert_called_once_with(_MOCK_IDENTITY, expected_account)
        assert mock_impl.call_args.kwargs["identity"] is enriched_identity

    @patch("src.core.resolved_identity.resolve_identity", return_value=_MOCK_IDENTITY)
    @patch("src.core.transport_helpers.enrich_identity_with_account")
    @patch("src.core.tools.media_buy_delivery.get_media_buy_delivery_raw")
    def test_malformed_account_returns_validation_error(self, mock_impl, mock_enrich, mock_resolve):
        response = client.post(
            "/api/v1/media-buys/delivery",
            json={"media_buy_ids": ["mb1"], "account": {}},
            headers={"Authorization": "Bearer test-token"},
        )

        assert response.status_code == 400
        assert_envelope_shape(response.json(), "VALIDATION_ERROR", recovery="correctable")
        mock_enrich.assert_not_called()
        mock_impl.assert_not_called()
