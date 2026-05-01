"""Integration tests for account REST routes (list_accounts + sync_accounts).

Verifies REST transport parity with IMPL/A2A/MCP transports.
These routes don't exist yet — tests should FAIL until implemented.

beads: salesagent-4ud
"""

from __future__ import annotations

import pytest

from tests.factories.account import AccountFactory, AgentAccountAccessFactory


@pytest.mark.requires_db
class TestListAccountsRestRoute:
    """REST /api/v1/accounts route should call list_accounts_raw."""

    def test_list_accounts_returns_accounts(self, integration_db):
        """GET accounts via REST returns same data as IMPL."""
        from tests.harness.account_list import AccountListEnv

        with AccountListEnv() as env:
            tenant, principal = env.setup_default_data()
            acc = AccountFactory(tenant=tenant, status="active")
            AgentAccountAccessFactory(
                tenant_id=tenant.tenant_id,
                principal=principal,
                account=acc,
            )

            # IMPL baseline
            impl_response = env.call_impl()
            assert len(impl_response.accounts) >= 1

            # REST should return the same (uses harness auth headers)
            rest_response = env.call_rest()
            assert len(rest_response.accounts) == len(impl_response.accounts)


@pytest.mark.requires_db
class TestSyncAccountsRestRoute:
    """REST /api/v1/accounts/sync route should call sync_accounts_raw."""

    def test_sync_accounts_creates_account(self, integration_db):
        """POST sync via REST creates account same as IMPL."""
        from tests.harness.account_sync import AccountSyncEnv

        with AccountSyncEnv() as env:
            env.setup_default_data()

            rest_response = env.call_rest(
                accounts=[
                    {
                        "brand": {"domain": "rest-test.com"},
                        "operator": "rest-test.com",
                        "billing": "operator",
                    }
                ],
            )
            assert len(rest_response.accounts) == 1
            assert rest_response.accounts[0].brand.domain == "rest-test.com"
