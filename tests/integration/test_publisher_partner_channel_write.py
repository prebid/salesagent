"""Admin write-boundary tests for PublisherPartner.supported_channels.

Valid lists persist as canonical MediaChannel values. Malformed input is
rejected with 422 and does not overwrite stored channels. Updates are
tenant-scoped.

Uses factory-boy + the integration harness — no get_db_session() in test bodies.
"""

from __future__ import annotations

import pytest

from src.admin.app import create_app
from src.core.database.repositories.tenant_config import TenantConfigRepository
from tests.factories import PublisherPartnerFactory, TenantFactory
from tests.harness._base import IntegrationEnv

app = create_app()

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


class _WriteEnv(IntegrationEnv):
    EXTERNAL_PATCHES: dict[str, str] = {}

    def get_session(self):
        self._commit_factory_data()
        return self._session


@pytest.fixture
def client():
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    app.config["SESSION_COOKIE_PATH"] = "/"
    with app.test_client() as client:
        yield client


def _stored_channels(tenant_id: str, partner_id: int) -> list[str] | None:
    with _WriteEnv() as env:
        repo = TenantConfigRepository(env.get_session(), tenant_id)
        partner = repo.get_publisher_partner(partner_id)
        assert partner is not None
        return partner.supported_channels


class TestPublisherPartnerChannelWrite:
    def test_post_persists_canonical_channels(self, client, integration_db):
        with _WriteEnv() as env:
            tenant = TenantFactory(tenant_id="ppc_post")
            env._commit_factory_data()
            tenant_id = tenant.tenant_id

        response = client.post(
            f"/tenant/{tenant_id}/publisher-partners",
            json={
                "publisher_domain": "news.example",
                "display_name": "News",
                "supported_channels": ["video", "ctv"],
            },
        )

        assert response.status_code == 201
        body = response.get_json()
        assert body["supported_channels"] == ["ctv", "olv"]
        assert _stored_channels(tenant_id, body["id"]) == ["ctv", "olv"]

    def test_get_includes_supported_channels(self, client, integration_db):
        with _WriteEnv() as env:
            tenant = TenantFactory(tenant_id="ppc_get")
            PublisherPartnerFactory(
                tenant=tenant,
                publisher_domain="get.example",
                supported_channels=["display", "ctv"],
            )
            env._commit_factory_data()
            tenant_id = tenant.tenant_id

        response = client.get(f"/tenant/{tenant_id}/publisher-partners")

        assert response.status_code == 200
        partners = response.get_json()["partners"]
        assert len(partners) == 1
        assert partners[0]["supported_channels"] == ["display", "ctv"]

    def test_patch_persists_canonical_channels(self, client, integration_db):
        with _WriteEnv() as env:
            tenant = TenantFactory(tenant_id="ppc_patch")
            partner = PublisherPartnerFactory(
                tenant=tenant,
                publisher_domain="patch.example",
                display_name="Old Name",
                supported_channels=["display"],
            )
            env._commit_factory_data()
            tenant_id = tenant.tenant_id
            partner_id = partner.id

        response = client.patch(
            f"/tenant/{tenant_id}/publisher-partners/{partner_id}",
            json={"display_name": "New Name", "supported_channels": ["video", "ctv"]},
        )

        assert response.status_code == 200
        body = response.get_json()
        assert body["display_name"] == "New Name"
        assert body["supported_channels"] == ["ctv", "olv"]
        assert _stored_channels(tenant_id, partner_id) == ["ctv", "olv"]

    def test_malformed_patch_does_not_erase_channels(self, client, integration_db):
        with _WriteEnv() as env:
            tenant = TenantFactory(tenant_id="ppc_bad")
            partner = PublisherPartnerFactory(
                tenant=tenant,
                publisher_domain="bad.example",
                supported_channels=["display"],
            )
            env._commit_factory_data()
            tenant_id = tenant.tenant_id
            partner_id = partner.id

        response = client.patch(
            f"/tenant/{tenant_id}/publisher-partners/{partner_id}",
            json={"supported_channels": "display,ctv"},
        )

        assert response.status_code == 422
        assert "list of strings" in response.get_json()["error"]
        assert _stored_channels(tenant_id, partner_id) == ["display"]

    def test_unknown_channel_patch_does_not_erase_channels(self, client, integration_db):
        with _WriteEnv() as env:
            tenant = TenantFactory(tenant_id="ppc_unk")
            partner = PublisherPartnerFactory(
                tenant=tenant,
                publisher_domain="unk.example",
                supported_channels=["display"],
            )
            env._commit_factory_data()
            tenant_id = tenant.tenant_id
            partner_id = partner.id

        response = client.patch(
            f"/tenant/{tenant_id}/publisher-partners/{partner_id}",
            json={"supported_channels": ["display", "native"]},
        )

        assert response.status_code == 422
        assert "native" in response.get_json()["error"]
        assert _stored_channels(tenant_id, partner_id) == ["display"]

    def test_patch_is_tenant_isolated(self, client, integration_db):
        with _WriteEnv() as env:
            tenant_a = TenantFactory(tenant_id="ppc_iso_a")
            TenantFactory(tenant_id="ppc_iso_b")
            partner = PublisherPartnerFactory(
                tenant=tenant_a,
                publisher_domain="iso.example",
                supported_channels=["display"],
            )
            env._commit_factory_data()
            partner_id = partner.id

        response = client.patch(
            f"/tenant/ppc_iso_b/publisher-partners/{partner_id}",
            json={"supported_channels": ["ctv"]},
        )

        assert response.status_code == 404
        assert _stored_channels("ppc_iso_a", partner_id) == ["display"]
