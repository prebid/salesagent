"""Integration tests for _sync_governance (UC-030, #1329) with real PostgreSQL.

Verifies the seller-side governance-binding contract end-to-end against a real
DB: authority check (the normative MUST) -> url-only persistence (replace
semantics) on the accounts.governance_agents column -> synced/failed results,
plus a REST wire-path roundtrip.

Idempotency replay / IDEMPOTENCY_CONFLICT and the full UC-030 boundary matrix
are the richer BDD ledger (deferred follow-up); these tests pin the working
tool the capabilities honesty pass depends on.
"""

from __future__ import annotations

import pytest

from src.core.database.repositories.account import AccountRepository
from src.core.database.repositories.uow import AccountUoW
from src.core.schemas.account import SyncGovernanceRequest
from tests.factories import AccountFactory, AgentAccountAccessFactory
from tests.harness.governance_sync import GovernanceSyncEnv

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

GOV_URL = "https://governance.pinnacle-media.com"
GOV_URL_2 = "https://governance.new-buyer.com"
BEARER_CREDS = "x" * 64


def _agent(url: str = GOV_URL) -> dict:
    return {"url": url, "authentication": {"schemes": ["Bearer"], "credentials": BEARER_CREDS}}


def _request(
    account_ref: dict, url: str = GOV_URL, key: str = "uuid-v4-int-000000000000000001"
) -> SyncGovernanceRequest:
    return SyncGovernanceRequest(
        idempotency_key=key,
        accounts=[{"account": account_ref, "governance_agents": [_agent(url)]}],
    )


def _owned_account(env: GovernanceSyncEnv, tenant, principal, account_id: str):
    """Create an account owned by the env's agent (AgentAccountAccess grant)."""
    account = AccountFactory(tenant=tenant, account_id=account_id)
    AgentAccountAccessFactory(tenant=tenant, principal=principal, account=account)
    return account


def _persisted_agents(tenant_id: str, account_id: str) -> list:
    """Read the persisted governance_agents off the account row via the repository."""
    with AccountUoW(tenant_id) as uow:
        repo: AccountRepository = uow.accounts
        account = repo.get_by_id(account_id)
        return account.governance_agents if account else None


class TestSyncGovernancePersistence:
    """Authority-gated persistence: synced accounts store the binding url-only."""

    @pytest.mark.asyncio
    async def test_owned_account_synced_and_persisted_url_only(self, integration_db):
        with GovernanceSyncEnv(tenant_id="gov_t1", principal_id="gov_agent1") as env:
            tenant, principal = env.setup_default_data()
            _owned_account(env, tenant, principal, "acc_gov_1")

            resp = await env.call_impl_async(req=_request({"account_id": "acc_gov_1"}))

        assert resp.accounts[0].status == "synced"
        assert resp.accounts[0].governance_agents[0].url == GOV_URL + "/"
        # Persisted url-only (credentials are never stored — column model is url-only).
        persisted = _persisted_agents("gov_t1", "acc_gov_1")
        assert len(persisted) == 1
        assert str(persisted[0].url) == GOV_URL + "/"
        assert not hasattr(persisted[0], "authentication") or getattr(persisted[0], "authentication", None) is None

    @pytest.mark.asyncio
    async def test_replace_semantics_overwrites_prior_binding(self, integration_db):
        with GovernanceSyncEnv(tenant_id="gov_t2", principal_id="gov_agent2") as env:
            tenant, principal = env.setup_default_data()
            _owned_account(env, tenant, principal, "acc_gov_2")

            await env.call_impl_async(req=_request({"account_id": "acc_gov_2"}, url=GOV_URL))
            # Second sync with a different agent replaces the first.
            resp = await env.call_impl_async(
                req=_request({"account_id": "acc_gov_2"}, url=GOV_URL_2, key="uuid-v4-int-000000000000000002")
            )

        assert resp.accounts[0].status == "synced"
        persisted = _persisted_agents("gov_t2", "acc_gov_2")
        assert len(persisted) == 1
        assert str(persisted[0].url) == GOV_URL_2 + "/"


class TestSyncGovernanceAuthority:
    """The normative MUST: unknown/unowned accounts fail per-account, no persistence."""

    @pytest.mark.asyncio
    async def test_unknown_account_fails_account_not_found(self, integration_db):
        with GovernanceSyncEnv(tenant_id="gov_t3", principal_id="gov_agent3") as env:
            env.setup_default_data()

            resp = await env.call_impl_async(req=_request({"account_id": "acc_does_not_exist"}))

        assert resp.accounts[0].status == "failed"
        assert resp.accounts[0].errors[0].code == "ACCOUNT_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_existing_but_unowned_account_fails_unauthorized(self, integration_db):
        with GovernanceSyncEnv(tenant_id="gov_t4", principal_id="gov_agent4") as env:
            tenant, _principal = env.setup_default_data()
            # Account exists in the tenant but the agent has NO AgentAccountAccess grant.
            AccountFactory(tenant=tenant, account_id="acc_unowned")

            resp = await env.call_impl_async(req=_request({"account_id": "acc_unowned"}))

        assert resp.accounts[0].status == "failed"
        assert resp.accounts[0].errors[0].code == "UNAUTHORIZED"
        # No binding persisted on a failed account.
        assert _persisted_agents("gov_t4", "acc_unowned") in (None, [])


class TestSyncGovernanceRestWire:
    """REST wire-path roundtrip: the tool works across the transport boundary."""

    def test_rest_happy_path_synced(self, integration_db):
        with GovernanceSyncEnv(tenant_id="gov_t5", principal_id="gov_agent5") as env:
            tenant, principal = env.setup_default_data()
            _owned_account(env, tenant, principal, "acc_gov_5")

            resp = env.call_rest(req=_request({"account_id": "acc_gov_5"}))

        assert resp.accounts[0].status == "synced"
        assert resp.accounts[0].governance_agents[0].url == GOV_URL + "/"
