"""The advertiser form's ID field persists within the addressed tenant.

The contract is templates/create_principal.html. These are real Flask and
PostgreSQL tests because the protocol BDD harness does not drive admin forms.
"""

import pytest
from sqlalchemy import select

from src.core.database.models import Principal
from tests.factories import TenantFactory
from tests.harness._base import BareIntegrationEnv
from tests.helpers.admin_session import admin_auth_session

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


@pytest.fixture
def creation_env(integration_db, admin_client):
    with BareIntegrationEnv() as env:
        tenant, _ = env.setup_default_data()
        admin_auth_session(admin_client, tenant.tenant_id)
        yield env, admin_client, tenant


def create(client, tenant_id, *, script_name="", **fields):
    return client.post(
        f"/tenant/{tenant_id}/principals/create",
        data={"name": "Created Advertiser", "enable_mock": "1", **fields},
        environ_overrides={"SCRIPT_NAME": script_name},
    )


def created_rows(env, tenant_id):
    env._session.expire_all()
    return env._session.scalars(select(Principal).filter_by(tenant_id=tenant_id, name="Created Advertiser")).all()


@pytest.mark.parametrize("principal_id", ["Buyer_123-brand", "a" * 50])
def test_supplied_id_is_preserved(creation_env, principal_id):
    env, client, tenant = creation_env
    response = create(client, tenant.tenant_id, principal_id=principal_id)
    assert response.status_code == 302
    assert response.headers["Location"] == f"/tenant/{tenant.tenant_id}/settings/advertisers"
    (row,) = created_rows(env, tenant.tenant_id)
    assert row.principal_id == principal_id
    assert row.platform_mappings["mock"]["advertiser_id"] == f"mock_{principal_id}"


@pytest.mark.parametrize("principal_id", ["", "   ", "two words", "a/b", "☃", "a" * 51])
@pytest.mark.parametrize("script_name", ["", "/console"])
def test_invalid_id_cannot_create_a_principal(creation_env, principal_id, script_name):
    env, client, tenant = creation_env
    response = create(client, tenant.tenant_id, principal_id=principal_id, script_name=script_name)
    assert response.status_code == 302
    assert response.headers["Location"] == f"{script_name}/tenant/{tenant.tenant_id}/principals/create"
    assert created_rows(env, tenant.tenant_id) == []


@pytest.mark.parametrize("script_name", ["", "/console"])
def test_existing_id_does_not_change_or_duplicate_a_principal(creation_env, script_name):
    env, client, tenant = creation_env
    create(client, tenant.tenant_id, principal_id="buyer")
    (original,) = created_rows(env, tenant.tenant_id)
    original_hash = original.token_hash
    response = create(client, tenant.tenant_id, principal_id="buyer", name="Replacement", script_name=script_name)
    assert response.status_code == 302
    assert response.headers["Location"] == f"{script_name}/tenant/{tenant.tenant_id}/principals/create"
    (row,) = created_rows(env, tenant.tenant_id)
    assert (row.principal_id, row.token_hash) == ("buyer", original_hash)
    assert env._session.scalar(select(Principal).filter_by(tenant_id=tenant.tenant_id, name="Replacement")) is None


def test_same_id_is_available_in_another_tenant(creation_env):
    env, client, tenant = creation_env
    other = TenantFactory()
    env.get_session()
    for tenant_id in [tenant.tenant_id, other.tenant_id]:
        admin_auth_session(client, tenant_id)
        response = create(client, tenant_id, principal_id="buyer")
        assert response.status_code == 302
        (row,) = created_rows(env, tenant_id)
        assert row.principal_id == "buyer"


def test_legacy_post_without_an_id_keeps_automatic_generation(creation_env):
    env, client, tenant = creation_env
    response = create(client, tenant.tenant_id)
    assert response.status_code == 302
    (row,) = created_rows(env, tenant.tenant_id)
    assert row.principal_id.startswith("prin_")


def test_anonymous_post_cannot_create_a_principal(creation_env):
    env, client, tenant = creation_env
    with client.session_transaction() as session:
        session.clear()
    response = create(client, tenant.tenant_id, principal_id="buyer")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]
    assert created_rows(env, tenant.tenant_id) == []
