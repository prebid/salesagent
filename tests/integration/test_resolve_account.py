"""Integration tests for resolve_account() helper.

Verifies account resolution from AccountReference (by ID and by natural key)
with real PostgreSQL.

"""

import pytest
from adcp.types import (
    AccountReference,
    AccountReferenceById,
    AccountReferenceByNaturalKey,
)

from src.core.exceptions import AdCPAccountNotFoundError, AdCPAuthorizationError
from tests.factories.account import AccountFactory, AgentAccountAccessFactory
from tests.harness.account_sync import AccountSyncEnv

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


class TestResolveAccountById:
    """Resolve AccountReferenceById (by explicit account_id)."""

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

            ref = AccountReference(AccountReferenceById(account_id="acc_001"))
            with get_db_session() as session:
                repo = AccountRepository(session, tenant.tenant_id)
                result = resolve_account(ref, env.identity, repo)

            assert result.account_id == "acc_001"
            assert result.sandbox is False, "a non-sandbox account must not claim sandbox mode"

    def test_not_found_raises(self, integration_db):
        """Non-existent account_id → AdCPAccountNotFoundError."""
        with AccountSyncEnv(tenant_id="resolve_t2", principal_id="agent_r2") as env:
            env.setup_default_data()
            env._commit_factory_data()

            from src.core.database.database_session import get_db_session
            from src.core.database.repositories.account import AccountRepository
            from src.core.helpers.account_helpers import resolve_account

            ref = AccountReference(AccountReferenceById(account_id="nonexistent"))
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

            ref = AccountReference(AccountReferenceById(account_id="acc_noaccess"))
            with get_db_session() as session:
                repo = AccountRepository(session, tenant.tenant_id)
                with pytest.raises(AdCPAuthorizationError):
                    resolve_account(ref, env.identity, repo)


class TestResolveAccountByNaturalKey:
    """Resolve AccountReferenceByNaturalKey (by brand + operator)."""

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

            ref = AccountReference(AccountReferenceByNaturalKey(brand={"domain": "acme.com"}, operator="acme.com"))
            with get_db_session() as session:
                repo = AccountRepository(session, tenant.tenant_id)
                result = resolve_account(ref, env.identity, repo)

            assert result.account_id == "acc_nat"
            assert result.sandbox is False, "a non-sandbox account must not claim sandbox mode"

    def test_natural_key_no_access_raises_not_found(self, integration_db):
        """Account exists but agent has no access → AdCPAccountNotFoundError.

        The natural-key lookup is scoped to the agent's accessible accounts
        (#1417), so an inaccessible account is never disclosed — it
        resolves as not-found, NOT as an authorization error. The explicit
        _require_account_access afterwards is defense-in-depth for the by-id
        parity path.
        """
        with AccountSyncEnv(tenant_id="resolve_t6", principal_id="agent_r6") as env:
            tenant, principal = env.setup_default_data()
            # Create account but DON'T grant access
            AccountFactory(
                tenant=tenant,
                account_id="acc_nat_noaccess",
                brand={"domain": "hidden.com"},
                operator="hidden.com",
            )
            env._commit_factory_data()

            from src.core.database.repositories.uow import AccountUoW
            from src.core.helpers.account_helpers import resolve_account

            ref = AccountReference(AccountReferenceByNaturalKey(brand={"domain": "hidden.com"}, operator="hidden.com"))
            with AccountUoW(tenant.tenant_id) as uow:
                with pytest.raises(AdCPAccountNotFoundError):
                    resolve_account(ref, env.identity, uow.accounts)

    def test_natural_key_not_found_raises(self, integration_db):
        """Non-existent brand+operator → AdCPAccountNotFoundError."""
        with AccountSyncEnv(tenant_id="resolve_t5", principal_id="agent_r5") as env:
            env.setup_default_data()
            env._commit_factory_data()

            from src.core.database.database_session import get_db_session
            from src.core.database.repositories.account import AccountRepository
            from src.core.helpers.account_helpers import resolve_account

            ref = AccountReference(
                AccountReferenceByNaturalKey(brand={"domain": "unknown.com"}, operator="unknown.com")
            )
            with get_db_session() as session:
                repo = AccountRepository(session, "resolve_t5")
                with pytest.raises(AdCPAccountNotFoundError):
                    resolve_account(ref, env.identity, repo)


class TestResolveAccountCarriesSandboxMode:
    """The mode a sandbox account is resolved WITH — the defect this PR opens on.

    Both pre-existing assertions read ``result.sandbox is False`` against non-sandbox
    accounts, so they hold under the original bug (a hardcoded ``False``) and under the
    fix alike. Nothing drove the True side: every ``identity.sandbox`` in the suite was
    an assignment to False, a comment, or an ``is False`` assert, so restoring
    ``ResolvedAccount(account.account_id, False)`` at both production sites left 155
    sandbox/account tests green.

    Driven through ``MediaBuyAccountEnv`` rather than a hand-held session: its
    ``call_impl`` runs production ``resolve_account`` inside an ``AccountUoW`` and
    records the resolved mode, so these need no ``get_db_session()`` in the test body
    (CLAUDE.md §Test Fixtures, enforced by test_architecture_repository_pattern). That
    recorded mode had no reader before this class existed.
    """

    @staticmethod
    def _resolved_sandbox(*, account_sandbox: bool | None, by_natural_key: bool = False) -> bool:
        from adcp.types import AccountReference, AccountReferenceById, AccountReferenceByNaturalKey

        from tests.harness.media_buy_account import MediaBuyAccountEnv

        with MediaBuyAccountEnv() as env:
            tenant, principal = env.setup_default_data()
            AccountFactory(
                tenant=tenant,
                account_id="acc_mode",
                sandbox=account_sandbox,
                brand={"domain": "mode.example.com"},
                operator="mode.example.com",
            )
            # Resolution enforces agent access; without the grant it fails
            # authorization before the mode is ever read off the row.
            AgentAccountAccessFactory(tenant_id=tenant.tenant_id, principal=principal, account_id="acc_mode")

            if by_natural_key:
                # sandbox is PART of the natural key — _resolve_by_natural_key passes
                # ref.sandbox into the lookup — so the buyer selects the sandbox account
                # explicitly; a live account with the same brand+operator is a different
                # account, not the same one in another mode.
                inner = AccountReferenceByNaturalKey(
                    brand={"domain": "mode.example.com"},
                    operator="mode.example.com",
                    sandbox=bool(account_sandbox),
                )
            else:
                inner = AccountReferenceById(account_id="acc_mode")

            env.call_impl(account_ref=AccountReference(inner))
            return env.last_resolved_sandbox

    def test_sandbox_account_resolves_as_sandbox_by_id(self, integration_db):
        assert self._resolved_sandbox(account_sandbox=True) is True, (
            "a sandbox account resolved to LIVE mode — the defect this PR exists to fix, "
            "and what routes a sandbox buyer to the real ad-server adapter"
        )

    def test_sandbox_account_resolves_as_sandbox_by_natural_key(self, integration_db):
        """The by-natural-key path builds its own ResolvedAccount, so it fails independently."""
        assert self._resolved_sandbox(account_sandbox=True, by_natural_key=True) is True, (
            "the natural-key resolution path dropped the sandbox flag"
        )

    def test_null_sandbox_column_resolves_as_live(self, integration_db):
        """The negative side, driven through production rather than computed in the test.

        The previous namesake asserted ``ResolvedAccount("acc_1", bool(None)).sandbox is
        False``, which evaluates ``bool()`` in the test body and reads a field back —
        production's ``bool(account.sandbox)`` never ran.
        """
        assert self._resolved_sandbox(account_sandbox=None) is False, (
            "a NULL sandbox column must coerce to LIVE, not to None or True"
        )
