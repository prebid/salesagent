"""Integration tests for the resolver's account lookup, with real PostgreSQL.

Resolution from an ``AccountReference`` -- by id and by natural key -- with the access
check and the status check that go with it. The function is
``account_lookup.find_account``, which ``resolve_account`` became when account resolution
moved behind the boundary: the resolver calls it once per request and puts the account it
returns on the identity (``AccountIdentity.account``), so a tool reads
``identity.account`` and resolves nothing. It takes the repository, the reference and the
PRINCIPAL, and returns the Account ROW rather than an account_id string.
"""

import pytest
from adcp.types import (
    AccountReference,
    AccountReferenceById,
    AccountReferenceByNaturalKey,
)

from src.core.database.repositories.account_lookup import find_account
from src.core.database.repositories.uow import AccountUoW
from src.core.exceptions import AdCPAccountNotFoundError, AdCPAuthorizationError
from tests.factories.account import AccountFactory, AgentAccountAccessFactory
from tests.harness.account_sync import AccountSyncEnv

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


class TestResolveAccountById:
    """Resolve AccountReferenceById (by explicit account_id)."""

    def test_resolves_by_account_id(self, integration_db):
        """Valid account_id with agent access → returns that account's row."""
        with AccountSyncEnv(tenant_id="resolve_t1", principal_id="agent_r1") as env:
            tenant, principal = env.setup_default_data()
            account = AccountFactory(tenant=tenant, account_id="acc_001")
            AgentAccountAccessFactory(tenant_id=tenant.tenant_id, principal=principal, account=account)
            env._commit_factory_data()

            ref = AccountReference(AccountReferenceById(account_id="acc_001"))
            with AccountUoW(tenant.tenant_id) as uow:
                assert uow.accounts is not None
                # Read inside the unit of work: the row detaches when it closes, which is
                # why production projects it to the schema Account there (account_from_row).
                assert find_account(uow.accounts, ref, env.identity.principal).account_id == "acc_001"

    def test_not_found_raises(self, integration_db):
        """Non-existent account_id → AdCPAccountNotFoundError."""
        with AccountSyncEnv(tenant_id="resolve_t2", principal_id="agent_r2") as env:
            env.setup_default_data()
            env._commit_factory_data()

            ref = AccountReference(AccountReferenceById(account_id="nonexistent"))
            with AccountUoW("resolve_t2") as uow:
                assert uow.accounts is not None
                with pytest.raises(AdCPAccountNotFoundError):
                    find_account(uow.accounts, ref, env.identity.principal)

    def test_no_access_raises(self, integration_db):
        """Account exists but agent has no access → AdCPAuthorizationError."""
        with AccountSyncEnv(tenant_id="resolve_t3", principal_id="agent_r3") as env:
            tenant, _principal = env.setup_default_data()
            # Create account but DON'T grant access
            AccountFactory(tenant=tenant, account_id="acc_noaccess")
            env._commit_factory_data()

            ref = AccountReference(AccountReferenceById(account_id="acc_noaccess"))
            with AccountUoW(tenant.tenant_id) as uow:
                assert uow.accounts is not None
                with pytest.raises(AdCPAuthorizationError):
                    find_account(uow.accounts, ref, env.identity.principal)


class TestResolveAccountByNaturalKey:
    """Resolve AccountReferenceByNaturalKey (by brand + operator)."""

    def test_resolves_by_natural_key(self, integration_db):
        """Valid brand+operator → returns that account's row."""
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

            ref = AccountReference(AccountReferenceByNaturalKey(brand={"domain": "acme.com"}, operator="acme.com"))
            with AccountUoW(tenant.tenant_id) as uow:
                assert uow.accounts is not None
                assert find_account(uow.accounts, ref, env.identity.principal).account_id == "acc_nat"

    def test_natural_key_no_access_raises_not_found(self, integration_db):
        """Account exists but agent has no access → AdCPAccountNotFoundError.

        The natural-key lookup is scoped to the agent's accessible accounts
        (#1417), so an inaccessible account is never disclosed — it
        resolves as not-found, NOT as an authorization error. The explicit
        access check afterwards is defense-in-depth for the by-id parity path.
        """
        with AccountSyncEnv(tenant_id="resolve_t6", principal_id="agent_r6") as env:
            tenant, _principal = env.setup_default_data()
            # Create account but DON'T grant access
            AccountFactory(
                tenant=tenant,
                account_id="acc_nat_noaccess",
                brand={"domain": "hidden.com"},
                operator="hidden.com",
            )
            env._commit_factory_data()

            ref = AccountReference(AccountReferenceByNaturalKey(brand={"domain": "hidden.com"}, operator="hidden.com"))
            with AccountUoW(tenant.tenant_id) as uow:
                assert uow.accounts is not None
                with pytest.raises(AdCPAccountNotFoundError):
                    find_account(uow.accounts, ref, env.identity.principal)

    def test_natural_key_not_found_raises(self, integration_db):
        """Non-existent brand+operator → AdCPAccountNotFoundError."""
        with AccountSyncEnv(tenant_id="resolve_t5", principal_id="agent_r5") as env:
            env.setup_default_data()
            env._commit_factory_data()

            ref = AccountReference(
                AccountReferenceByNaturalKey(brand={"domain": "unknown.com"}, operator="unknown.com")
            )
            with AccountUoW("resolve_t5") as uow:
                assert uow.accounts is not None
                with pytest.raises(AdCPAccountNotFoundError):
                    find_account(uow.accounts, ref, env.identity.principal)
