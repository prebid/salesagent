"""The stored GAM refresh token is write-only from the admin UI.

The settings page used to render the token into the page (a hidden input and the
manual-entry field's value), so anyone who could open the page could copy a credential
to the publisher's ad server. Now the page never receives it, and the GAM endpoints use
the stored token when the page sends a blank one. These cases go through the real routes
against a real database. Only Google's client library is patched: it is an external
system a test cannot call.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import select

from src.core.config import get_settings
from src.core.database.models import AdapterConfig

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

TENANT_ID = "gam_write_only"
STORED_TOKEN = "1//stored-refresh-token-must-never-reach-the-page"
PRESENTED_TOKEN = "1//freshly-pasted-refresh-token"
NETWORK = {
    "id": "123456",
    "networkCode": "78901234",
    "displayName": "Write-only Test Network",
    "currencyCode": "USD",
    "timeZone": "America/New_York",
}


@pytest.fixture
def gam_tenant(integration_db):
    """A tenant whose GAM adapter has a stored OAuth refresh token."""
    from tests.factories import AdapterConfigFactory, TenantFactory
    from tests.harness._base import IntegrationEnv

    with IntegrationEnv() as env:
        tenant = TenantFactory(tenant_id=TENANT_ID, name="GAM Write Only", subdomain="gamwriteonly")
        AdapterConfigFactory(
            tenant=tenant,
            adapter_type="google_ad_manager",
            gam_auth_method="oauth",
            gam_refresh_token=STORED_TOKEN,
            gam_network_code=NETWORK["networkCode"],
        )
        yield env


@pytest.fixture
def google(gam_tenant):
    """Google's OAuth and Ad Manager clients, with the seller's OAuth app configured."""
    network_service = MagicMock()
    network_service.getAllNetworks.return_value = [NETWORK]
    network_service.getCurrentNetwork.return_value = NETWORK
    user_service = MagicMock()
    user_service.getCurrentUser.return_value = {"id": 99999, "name": "Test User"}
    ad_manager_client = MagicMock()
    ad_manager_client.GetService.side_effect = lambda name, **kw: (
        network_service if name == "NetworkService" else user_service
    )
    gam_auth = get_settings().auth
    with (
        patch.object(gam_auth, "gam_oauth_client_id", "fake-id.apps.googleusercontent.com"),
        patch.object(gam_auth, "gam_oauth_client_secret", "GOCSPX-fake-secret"),
        patch("googleads.oauth2.GoogleRefreshTokenClient") as oauth_client,
        patch("googleads.ad_manager.AdManagerClient", return_value=ad_manager_client),
    ):
        yield oauth_client


def _token_google_was_given(oauth_client: MagicMock) -> str:
    return oauth_client.call_args.kwargs["refresh_token"]


def test_the_settings_page_never_contains_the_stored_token(gam_tenant, authenticated_admin_client):
    response = authenticated_admin_client.get(f"/tenant/{TENANT_ID}/settings")
    assert response.status_code == 200
    assert STORED_TOKEN.encode() not in response.data


def test_detect_network_with_a_blank_token_uses_the_stored_one(google, authenticated_admin_client):
    response = authenticated_admin_client.post(f"/tenant/{TENANT_ID}/gam/detect-network", json={"refresh_token": ""})
    assert response.status_code == 200, response.get_json()
    assert _token_google_was_given(google) == STORED_TOKEN


def test_detect_network_prefers_a_presented_token(google, authenticated_admin_client):
    response = authenticated_admin_client.post(
        f"/tenant/{TENANT_ID}/gam/detect-network", json={"refresh_token": PRESENTED_TOKEN}
    )
    assert response.status_code == 200, response.get_json()
    assert _token_google_was_given(google) == PRESENTED_TOKEN


def test_configure_with_a_blank_token_keeps_the_stored_one(gam_tenant, google, authenticated_admin_client):
    response = authenticated_admin_client.post(
        f"/tenant/{TENANT_ID}/gam/configure",
        json={"auth_method": "oauth", "network_code": NETWORK["networkCode"], "refresh_token": ""},
    )
    assert response.status_code == 200, response.get_json()
    session = gam_tenant.get_session()
    session.expire_all()
    stored = session.scalars(select(AdapterConfig).filter_by(tenant_id=TENANT_ID)).one()
    assert stored.gam_refresh_token == STORED_TOKEN
