"""Integration tests for resolve_account() helper.

Verifies account resolution from AccountReference (by ID and by natural key)
with real PostgreSQL.

beads: salesagent-8n4
"""

import pytest
from adcp.types.generated_poc.core.account_ref import (
    AccountReference,
    AccountReference1,
    AccountReference2,
)

from src.core.exceptions import AdCPAccountNotFoundError, AdCPAuthorizationError
from tests.factories.account import AccountFactory, AgentAccountAccessFactory
from tests.harness.account_sync import AccountSyncEnv

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


class TestResolveAccountById:
    """Resolve AccountReference1 (by explicit account_id)."""

    def test_resolves_by_account_id(self, integration_db):
        """Valid account_id with agent access → returns account_id."""
        with AccountSyncEnv(tenant_id="resolve_t1", principal_id="agent_r1") as env:
            tenant, principal = env.setup_default_data()
            account = AccountFactory(tenant=tenant, account_id="acc_001")
            AgentAccountAccessFactory(tenant_id=tenant.tenant_id, principal=principal, account=account)
            env._commit_factory_data()

            from src.core.database.database_session import get_db_session
            from src.core.database.repositories.account import AccountRepository
            from src.core.helpers.account_helpers import resolve_account

            ref = AccountReference(AccountReference1(account_id="acc_001"))
            with get_db_session() as session:
                repo = AccountRepository(session, tenant.tenant_id)
                result = resolve_account(ref, env.identity, repo)

            assert result == "acc_001"

    def test_not_found_raises(self, integration_db):
        """Non-existent account_id → AdCPAccountNotFoundError."""
        with AccountSyncEnv(tenant_id="resolve_t2", principal_id="agent_r2") as env:
            env.setup_default_data()
            env._commit_factory_data()

            from src.core.database.database_session import get_db_session
            from src.core.database.repositories.account import AccountRepository
            from src.core.helpers.account_helpers import resolve_account

            ref = AccountReference(AccountReference1(account_id="nonexistent"))
            with get_db_session() as session:
                repo = AccountRepository(session, "resolve_t2")
                with pytest.raises(AdCPAccountNotFoundError):
                    resolve_account(ref, env.identity, repo)

    def test_no_access_raises(self, integration_db):
        """Account exists but agent has no access → AdCPAuthorizationError."""
        with AccountSyncEnv(tenant_id="resolve_t3", principal_id="agent_r3") as env:
            tenant, principal = env.setup_default_data()
            # Create account but DON'T grant access
            AccountFactory(tenant=tenant, account_id="acc_noaccess")
            env._commit_factory_data()

            from src.core.database.database_session import get_db_session
            from src.core.database.repositories.account import AccountRepository
            from src.core.helpers.account_helpers import resolve_account

            ref = AccountReference(AccountReference1(account_id="acc_noaccess"))
            with get_db_session() as session:
                repo = AccountRepository(session, tenant.tenant_id)
                with pytest.raises(AdCPAuthorizationError):
                    resolve_account(ref, env.identity, repo)


class TestResolveAccountByNaturalKey:
    """Resolve AccountReference2 (by brand + operator)."""

    def test_resolves_by_natural_key(self, integration_db):
        """Valid brand+operator → returns account_id."""
        with AccountSyncEnv(tenant_id="resolve_t4", principal_id="agent_r4") as env:
            tenant, principal = env.setup_default_data()
            account = AccountFactory(
                tenant=tenant,
                account_id="acc_nat",
                brand={"domain": "acme.com"},
                operator="acme.com",
            )
            AgentAccountAccessFactory(tenant_id=tenant.tenant_id, principal=principal, account=account)
            env._commit_factory_data()

            from src.core.database.database_session import get_db_session
            from src.core.database.repositories.account import AccountRepository
            from src.core.helpers.account_helpers import resolve_account

            ref = AccountReference(AccountReference2(brand={"domain": "acme.com"}, operator="acme.com"))
            with get_db_session() as session:
                repo = AccountRepository(session, tenant.tenant_id)
                result = resolve_account(ref, env.identity, repo)

            assert result == "acc_nat"

    def test_natural_key_not_found_raises(self, integration_db):
        """Non-existent brand+operator → AdCPAccountNotFoundError."""
        with AccountSyncEnv(tenant_id="resolve_t5", principal_id="agent_r5") as env:
            env.setup_default_data()
            env._commit_factory_data()

            from src.core.database.database_session import get_db_session
            from src.core.database.repositories.account import AccountRepository
            from src.core.helpers.account_helpers import resolve_account

            ref = AccountReference(AccountReference2(brand={"domain": "unknown.com"}, operator="unknown.com"))
            with get_db_session() as session:
                repo = AccountRepository(session, "resolve_t5")
                with pytest.raises(AdCPAccountNotFoundError):
                    resolve_account(ref, env.identity, repo)
