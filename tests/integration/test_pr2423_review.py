"""Review repros for prebid/salesagent#2423. Each test asserts the behavior we want; on 3819200 all fail."""

import pytest
from sqlalchemy import select

import src.admin.blueprints.principals as principals_module
from src.admin.services.business_activity_service import get_business_activities
from src.core.audit_logger import get_audit_logger
from src.core.database.models import Account, AgentAccountAccess, AuditLog, Principal
from src.core.database.repositories.principal import PrincipalRepository
from src.core.schemas.account import SyncAccountsRequest
from tests.factories import AgentAccountAccessFactory, PrincipalFactory
from tests.factories.request import fresh_idempotency_key
from tests.harness._base import BareIntegrationEnv
from tests.harness.account_sync import AccountSyncEnv
from tests.helpers import concurrent_commit_in_write_window, operator_answer
from tests.helpers.admin_session import admin_auth_session

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


@pytest.fixture
def creation_env(integration_db, admin_client):
    with BareIntegrationEnv() as env:
        tenant, _ = env.setup_default_data()
        admin_auth_session(admin_client, tenant.tenant_id)
        yield env, admin_client, tenant


def create(client, tenant_id, **fields):
    return client.post(
        f"/tenant/{tenant_id}/principals/create",
        data={"name": "Created Advertiser", "enable_mock": "1", **fields},
    )


# Finding 1: the race loser gets a different answer than the pre-check.
def test_race_loser_gets_the_precheck_answer(creation_env):
    env, client, tenant = creation_env
    with concurrent_commit_in_write_window(
        principals_module, lambda: PrincipalFactory(tenant=tenant, principal_id="buyer")
    ):
        loser = operator_answer(client, create(client, tenant.tenant_id, principal_id="buyer"))
    winner = operator_answer(client, create(client, tenant.tenant_id, principal_id="buyer"))
    assert winner[2] == [("error", "Advertiser ID is already in use")]
    assert loser == winner


# Finding 2: refused creates are audited as successes.
@pytest.mark.parametrize("principal_id", ["buyer", "two words"])
def test_refused_create_is_audited_as_failure(creation_env, principal_id):
    env, client, tenant = creation_env
    PrincipalFactory(tenant=tenant, principal_id="buyer")
    env.get_session()
    create(client, tenant.tenant_id, principal_id=principal_id)
    env._session.expire_all()
    (row,) = env._session.scalars(
        select(AuditLog).filter_by(tenant_id=tenant.tenant_id, operation="AdminUI.create_principal")
    ).all()
    assert row.success is False


# Finding 3: an advertiser can take an ID that audit rows use for unauthenticated callers.
@pytest.mark.parametrize("sentinel", ["anonymous", "unknown"])
def test_audit_sentinel_ids_are_refused(creation_env, sentinel):
    env, client, tenant = creation_env
    create(client, tenant.tenant_id, principal_id=sentinel, name="Acme Advertiser")
    # Exactly what products.py:753-757 writes for an unauthenticated get_products.
    get_audit_logger("AdCP", tenant.tenant_id).log_operation(
        operation="get_products",
        principal_name=sentinel,
        principal_id=sentinel,
        adapter_id="mcp_server",
        success=True,
        details={"product_count": 3},
    )
    titles = [a["title"] for a in get_business_activities(tenant.tenant_id)]
    env._session.expire_all()
    row = env._session.scalar(select(Principal).filter_by(tenant_id=tenant.tenant_id, principal_id=sentinel))
    assert row is None, f"dashboard now reads: {titles}"


# Finding 4: a reused ID inherits the deleted principal's accounts and can close shared ones.
async def test_reused_id_does_not_close_a_shared_account_it_never_created(integration_db):
    tid = "reuse_t"
    with AccountSyncEnv(tenant_id=tid, principal_id="nike") as env:
        tenant, _ = env.setup_default_data()
        env._commit_factory_data()
        created = await env.call_impl_async(
            req=SyncAccountsRequest(
                idempotency_key=fresh_idempotency_key(),
                accounts=[{"brand": {"domain": "nike.com"}, "operator": "nike.com", "billing": "operator"}],
            )
        )
        account_id = created.accounts[0].account_id
        partner = PrincipalFactory(tenant=tenant, principal_id="partner_agency")
        account = env._session.scalar(select(Account).filter_by(tenant_id=tid, account_id=account_id))
        AgentAccountAccessFactory(tenant=tenant, principal=partner, account=account)
        env._commit_factory_data()
        # What delete_principal does (principals.py:869-870).
        env._session.delete(PrincipalRepository(env._session, tid).get("nike"))
        env._session.commit()

    with AccountSyncEnv(tenant_id=tid, principal_id="nike") as env:
        env.setup_default_data()  # a NEW principal row reusing the id, as the form now allows
        env._commit_factory_data()
        await env.call_impl_async(
            req=SyncAccountsRequest(
                idempotency_key=fresh_idempotency_key(),
                accounts=[{"brand": {"domain": "other.com"}, "operator": "other.com", "billing": "operator"}],
                delete_missing=True,
            )
        )
        env._session.expire_all()
        status = env._session.scalar(select(Account.status).filter_by(tenant_id=tid, account_id=account_id))
        holders = env._session.scalars(
            select(AgentAccountAccess.principal_id).filter_by(tenant_id=tid, account_id=account_id)
        ).all()
    assert status == "active", f"{account_id} is {status}; still held by {holders}"
