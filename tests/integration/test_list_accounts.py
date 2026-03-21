"""Integration tests for _list_accounts_impl.

Verifies agent-scoped account listing with real PostgreSQL.

beads: salesagent-hl0
Business rules: BR-RULE-054 (agent scoping), BR-RULE-055 (auth optional)
"""

import pytest

from tests.harness._base import IntegrationEnv

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


class _AccountListEnv(IntegrationEnv):
    """Bare integration env for list_accounts tests — no external patches."""

    EXTERNAL_PATCHES: dict[str, str] = {}

    def get_session(self):
        self._commit_factory_data()
        return self._session


class TestListAccountsAgentScoping:
    """BR-RULE-054: list_accounts returns only accessible accounts."""

    def test_returns_accessible_accounts(self, integration_db):
        from src.core.tools.accounts import _list_accounts_impl
        from tests.factories import (
            AccountFactory,
            AgentAccountAccessFactory,
            PrincipalFactory,
            TenantFactory,
        )

        with _AccountListEnv(tenant_id="la_scope_t1", principal_id="agent_la") as env:
            tenant = TenantFactory(tenant_id="la_scope_t1")
            principal = PrincipalFactory(tenant=tenant, principal_id="agent_la")
            acc1 = AccountFactory(tenant=tenant, account_id="acc_la_1", name="Visible")
            AccountFactory(tenant=tenant, account_id="acc_la_2", name="Not Visible")
            AgentAccountAccessFactory(tenant_id=tenant.tenant_id, principal=principal, account=acc1)
            env._commit_factory_data()

            response = _list_accounts_impl(identity=env.identity)

        assert len(response.accounts) == 1
        assert response.accounts[0].account_id == "acc_la_1"

    def test_returns_empty_when_no_access(self, integration_db):
        from src.core.tools.accounts import _list_accounts_impl
        from tests.factories import (
            AccountFactory,
            PrincipalFactory,
            TenantFactory,
        )

        with _AccountListEnv(tenant_id="la_empty_t1", principal_id="agent_no_acc") as env:
            tenant = TenantFactory(tenant_id="la_empty_t1")
            PrincipalFactory(tenant=tenant, principal_id="agent_no_acc")
            AccountFactory(tenant=tenant, account_id="acc_no_access")
            env._commit_factory_data()

            response = _list_accounts_impl(identity=env.identity)

        assert len(response.accounts) == 0


class TestListAccountsUnauthenticated:
    """BR-RULE-055 INV-3: unauthenticated list_accounts raises AUTH_TOKEN_INVALID."""

    def test_unauthenticated_returns_auth_error(self, integration_db):
        import pytest

        from src.core.exceptions import AdCPAuthenticationError
        from src.core.tools.accounts import _list_accounts_impl

        with pytest.raises(AdCPAuthenticationError, match="Authentication required"):
            _list_accounts_impl(identity=None)
