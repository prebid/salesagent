"""Integration tests for AccountRepository.

Verifies that the repository correctly queries Account and AgentAccountAccess
models with tenant scoping against real PostgreSQL.

beads: salesagent-m44
"""

import pytest
from sqlalchemy import select

from tests.harness._base import IntegrationEnv

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


class _RepoEnv(IntegrationEnv):
    """Bare integration env for repository tests — no external patches."""

    EXTERNAL_PATCHES: dict[str, str] = {}

    def get_session(self):
        """Expose session for direct repository construction."""
        self._commit_factory_data()
        return self._session


class TestAccountRepositoryGetById:
    """get_by_id returns the account or None."""

    def test_returns_account(self, integration_db):
        from src.core.database.repositories.account import AccountRepository
        from tests.factories import AccountFactory, TenantFactory

        with _RepoEnv() as env:
            tenant = TenantFactory(tenant_id="repo_get_t1")
            AccountFactory(tenant=tenant, account_id="acc_find", name="Findable")
            session = env.get_session()
            repo = AccountRepository(session, "repo_get_t1")
            result = repo.get_by_id("acc_find")

        assert result is not None
        assert result.name == "Findable"

    def test_returns_none_for_missing(self, integration_db):
        from src.core.database.repositories.account import AccountRepository
        from tests.factories import TenantFactory

        with _RepoEnv() as env:
            TenantFactory(tenant_id="repo_get_t2")
            session = env.get_session()
            repo = AccountRepository(session, "repo_get_t2")
            result = repo.get_by_id("nonexistent")

        assert result is None

    def test_tenant_isolation(self, integration_db):
        from src.core.database.repositories.account import AccountRepository
        from tests.factories import AccountFactory, TenantFactory

        with _RepoEnv() as env:
            t1 = TenantFactory(tenant_id="repo_iso_t1")
            t2 = TenantFactory(tenant_id="repo_iso_t2")
            AccountFactory(tenant=t1, account_id="acc_iso", name="T1 Account")
            AccountFactory(tenant=t2, account_id="acc_iso", name="T2 Account")
            session = env.get_session()

            repo_t1 = AccountRepository(session, "repo_iso_t1")
            repo_t2 = AccountRepository(session, "repo_iso_t2")

        assert repo_t1.get_by_id("acc_iso").name == "T1 Account"
        assert repo_t2.get_by_id("acc_iso").name == "T2 Account"


class TestAccountRepositoryListForAgent:
    """list_for_agent returns accounts accessible to a principal."""

    def test_returns_accessible_accounts(self, integration_db):
        from src.core.database.repositories.account import AccountRepository
        from tests.factories import (
            AccountFactory,
            AgentAccountAccessFactory,
            PrincipalFactory,
            TenantFactory,
        )

        with _RepoEnv() as env:
            tenant = TenantFactory(tenant_id="repo_list_t1")
            principal = PrincipalFactory(tenant=tenant, principal_id="agent_list")
            acc1 = AccountFactory(tenant=tenant, account_id="acc_vis_1", name="Visible 1")
            acc2 = AccountFactory(tenant=tenant, account_id="acc_vis_2", name="Visible 2")
            AccountFactory(tenant=tenant, account_id="acc_invis", name="Not Accessible")
            AgentAccountAccessFactory(tenant_id=tenant.tenant_id, principal=principal, account=acc1)
            AgentAccountAccessFactory(tenant_id=tenant.tenant_id, principal=principal, account=acc2)
            session = env.get_session()
            repo = AccountRepository(session, "repo_list_t1")
            results = repo.list_for_agent("agent_list")

        assert len(results) == 2
        names = {a.name for a in results}
        assert names == {"Visible 1", "Visible 2"}


class TestAccountRepositoryAccessMethods:
    """grant_access, has_access, revoke_access for AgentAccountAccess."""

    def test_grant_and_check_access(self, integration_db):
        from src.core.database.repositories.account import AccountRepository
        from tests.factories import AccountFactory, PrincipalFactory, TenantFactory

        with _RepoEnv() as env:
            tenant = TenantFactory(tenant_id="repo_acc_t1")
            PrincipalFactory(tenant=tenant, principal_id="agent_grant")
            AccountFactory(tenant=tenant, account_id="acc_grant")
            session = env.get_session()
            repo = AccountRepository(session, "repo_acc_t1")

            assert not repo.has_access("agent_grant", "acc_grant")
            repo.grant_access("agent_grant", "acc_grant")
            session.flush()
            assert repo.has_access("agent_grant", "acc_grant")


class TestAccountUoW:
    """AccountUoW session lifecycle."""

    def test_uow_commits_on_clean_exit(self, integration_db):
        from src.core.database.models import Account
        from src.core.database.repositories.uow import AccountUoW
        from tests.factories import TenantFactory

        # Create tenant first
        with _RepoEnv() as env:
            TenantFactory(tenant_id="uow_test")
            env.get_session()

        # Use UoW to create account
        with AccountUoW("uow_test") as uow:
            account = Account(
                account_id="uow_acc",
                tenant_id="uow_test",
                name="UoW Created",
                status="active",
            )
            uow.accounts.create(account)

        # Verify in fresh session
        with _RepoEnv() as env:
            session = env.get_session()
            result = session.scalars(select(Account).filter_by(tenant_id="uow_test", account_id="uow_acc")).first()

        assert result is not None
        assert result.name == "UoW Created"
