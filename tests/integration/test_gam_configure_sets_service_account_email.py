"""Pasting a GAM service-account key sets ``gam_service_account_email``.

Graded through the real Flask route against a real database, because the defect was a
split between two writers: the GCP auto-provision path (``gcp_service_account_service``)
sets ``gam_service_account_email``, the paste path (``configure_gam``) stored the key and
left it NULL. ``tenant_status`` requires both before a GAM tenant counts as configured, so a
working pasted-key connection read as "Pending Configuration" on the landing page and
Settings showed no service-account email. Four obligations:

* a pasted key stores its ``client_email`` and the tenant counts as configured;
* a network-code-only update (no key resent) keeps the stored email;
* a key without ``client_email`` is rejected and nothing is stored;
* after a pasted key, auto-provisioning reports an existing service account instead of
  creating a second one (the same guard the auto-provision path already applies to itself).

The first fails on upstream ``main`` on both assertions; the fourth fails there because the
email is NULL, so the guard never fires.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from sqlalchemy import select

from src.core.database.models import AdapterConfig
from src.core.tenant_status import is_tenant_ad_server_configured

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

TENANT_ID = "gam_pasted_key_tenant"
CLIENT_EMAIL = "adcp-sales@example-project.iam.gserviceaccount.com"


def fake_key(**overrides) -> dict:
    """A syntactically complete service-account key; nothing here is ever used to sign."""
    key = {
        "type": "service_account",
        "project_id": "example-project",
        "private_key_id": "0123456789abcdef",
        "private_key": "-----BEGIN PRIVATE KEY-----\nnot-a-real-key\n-----END PRIVATE KEY-----\n",
        "client_email": CLIENT_EMAIL,
    }
    key.update(overrides)
    return key


@pytest.fixture
def gam_env(integration_db):
    """A committed tenant with no adapter config yet; ``configure_gam`` creates it."""
    from tests.factories import TenantFactory
    from tests.harness._base import IntegrationEnv

    with IntegrationEnv() as env:
        TenantFactory(tenant_id=TENANT_ID, name="Pasted Key Publisher", subdomain="pastedkey")
        yield env


def stored_adapter_config(env) -> AdapterConfig | None:
    """The adapter-config row, read fresh after whatever the handler's session committed."""
    session = env.get_session()
    session.rollback()
    return session.scalars(select(AdapterConfig).filter_by(tenant_id=TENANT_ID)).first()


def configure(client, **fields):
    return client.post(
        f"/tenant/{TENANT_ID}/gam/configure",
        json={"auth_method": "service_account", **fields},
    )


def test_pasted_key_sets_email_and_configures_tenant(authenticated_admin_client, gam_env):
    resp = configure(authenticated_admin_client, service_account_json=json.dumps(fake_key()), network_code="12345678")
    assert resp.status_code == 200, resp.data[:500]

    config = stored_adapter_config(gam_env)
    assert config is not None
    assert config.gam_service_account_email == CLIENT_EMAIL
    assert is_tenant_ad_server_configured(TENANT_ID) is True


def test_network_code_only_update_keeps_email(authenticated_admin_client, gam_env):
    assert configure(authenticated_admin_client, service_account_json=json.dumps(fake_key())).status_code == 200

    resp = configure(authenticated_admin_client, network_code="87654321")
    assert resp.status_code == 200, resp.data[:500]

    config = stored_adapter_config(gam_env)
    assert config is not None
    assert config.gam_network_code == "87654321"
    assert config.gam_service_account_email == CLIENT_EMAIL
    assert is_tenant_ad_server_configured(TENANT_ID) is True


def test_key_without_client_email_is_rejected(authenticated_admin_client, gam_env):
    key = fake_key()
    del key["client_email"]

    resp = configure(authenticated_admin_client, service_account_json=json.dumps(key))
    assert resp.status_code == 400, resp.data[:500]
    assert any("client_email" in error for error in resp.get_json()["errors"])
    assert stored_adapter_config(gam_env) is None
    assert is_tenant_ad_server_configured(TENANT_ID) is False


def test_pasted_key_counts_as_existing_for_auto_provision(authenticated_admin_client, gam_env):
    from src.services.gcp_service_account_service import GCPServiceAccountService

    assert configure(authenticated_admin_client, service_account_json=json.dumps(fake_key())).status_code == 200

    # The IAM client is built in __init__; the guard under test runs before any GCP call.
    with patch("src.services.gcp_service_account_service.iam_admin_v1.IAMClient"):
        service = GCPServiceAccountService("example-project")
        with pytest.raises(ValueError, match="already has a service account"):
            service.create_service_account_for_tenant(TENANT_ID)
