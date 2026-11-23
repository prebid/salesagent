"""Pasting a GAM service-account key sets ``gam_service_account_email``.

Graded through the real Flask routes against a real database, because the defect was a
split between two writers: the GCP auto-provision path (``gcp_service_account_service``)
set ``gam_service_account_email``, the paste path (``configure_gam``) stored the key and
left it NULL. ``tenant_status`` requires both before a GAM tenant counts as configured, so a
working pasted-key connection read as "Pending Configuration" on the landing page and
Settings showed no service-account email. The ``AdapterConfig.gam_service_account_json``
setter now keeps the pair in step for every writer. Obligations:

* a pasted key stores its ``client_email``, the email route reports it, and the tenant
  counts as configured;
* a network-code-only update (no key resent) keeps the stored email;
* switching to OAuth clears the key and the email with it;
* a key whose ``client_email`` is absent, empty or null is refused and nothing is stored;
* after a pasted key, auto-provisioning refuses (409) instead of creating a second account.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from src.core.database.models import AdapterConfig
from src.core.tenant_status import is_tenant_ad_server_configured
from tests.helpers.gam_credentials import service_account_key

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

TENANT_ID = "gam_pasted_key_tenant"
CLIENT_EMAIL = "adcp-sales@example-project.iam.gserviceaccount.com"
PASTED_KEY = json.dumps(service_account_key(project_id="example-project", client_email=CLIENT_EMAIL))


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
    return client.post(f"/tenant/{TENANT_ID}/gam/configure", json={"auth_method": "service_account", **fields})


def reported_email(client) -> str | None:
    resp = client.get(f"/tenant/{TENANT_ID}/gam/get-service-account-email")
    assert resp.status_code == 200, resp.data[:500]
    return resp.get_json()["service_account_email"]


def test_pasted_key_sets_email_and_configures_tenant(authenticated_admin_client, gam_env):
    resp = configure(authenticated_admin_client, service_account_json=PASTED_KEY, network_code="12345678")
    assert resp.status_code == 200, resp.data[:500]

    assert reported_email(authenticated_admin_client) == CLIENT_EMAIL
    config = stored_adapter_config(gam_env)
    assert config is not None
    assert config.gam_service_account_email == CLIENT_EMAIL
    assert is_tenant_ad_server_configured(TENANT_ID) is True


def test_network_code_only_update_keeps_email(authenticated_admin_client, gam_env):
    assert configure(authenticated_admin_client, service_account_json=PASTED_KEY).status_code == 200

    resp = configure(authenticated_admin_client, network_code="87654321")
    assert resp.status_code == 200, resp.data[:500]

    assert reported_email(authenticated_admin_client) == CLIENT_EMAIL
    config = stored_adapter_config(gam_env)
    assert config is not None
    assert config.gam_network_code == "87654321"
    assert config.gam_service_account_email == CLIENT_EMAIL


def test_switching_to_oauth_and_back_follows_the_key(authenticated_admin_client, gam_env):
    assert configure(authenticated_admin_client, service_account_json=PASTED_KEY).status_code == 200

    resp = authenticated_admin_client.post(
        f"/tenant/{TENANT_ID}/gam/configure", json={"auth_method": "oauth", "refresh_token": "1//a-refresh-token"}
    )
    assert resp.status_code == 200, resp.data[:500]
    assert reported_email(authenticated_admin_client) is None
    config = stored_adapter_config(gam_env)
    assert config is not None
    assert config.gam_service_account_email is None

    assert configure(authenticated_admin_client, service_account_json=PASTED_KEY).status_code == 200
    assert reported_email(authenticated_admin_client) == CLIENT_EMAIL


@pytest.mark.parametrize("client_email", ["absent", "", None])
def test_key_without_a_usable_client_email_is_refused(authenticated_admin_client, gam_env, client_email):
    key = json.loads(PASTED_KEY)
    if client_email == "absent":
        del key["client_email"]
    else:
        key["client_email"] = client_email

    resp = configure(authenticated_admin_client, service_account_json=json.dumps(key))
    assert resp.status_code == 400, resp.data[:500]
    issues = resp.get_json()["issues"]
    assert [issue["field"] for issue in issues] == ["service_account_json"]
    assert "client_email" in issues[0]["text"]
    assert stored_adapter_config(gam_env) is None


def test_pasted_key_counts_as_existing_for_auto_provision(authenticated_admin_client, gam_env):
    assert configure(authenticated_admin_client, service_account_json=PASTED_KEY).status_code == 200

    # The service refuses on the tenant's own state before it builds an IAM client.
    resp = authenticated_admin_client.post(f"/tenant/{TENANT_ID}/gam/create-service-account")
    assert resp.status_code == 409, resp.data[:500]
    assert reported_email(authenticated_admin_client) == CLIENT_EMAIL
