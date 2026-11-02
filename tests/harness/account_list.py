"""AccountListEnv — integration test environment for _list_accounts_impl.

Patches: audit logger ONLY.
Real: get_db_session, AccountRepository, all query building (all hit real DB).

Requires: integration_db fixture (creates test PostgreSQL DB).

Usage::

    @pytest.mark.requires_db
    def test_something(self, integration_db):
        with AccountListEnv() as env:
            tenant, principal = env.setup_default_data()
            account = AccountFactory(tenant=tenant, account_id="acc_1")
            AgentAccountAccessFactory(
                tenant_id=tenant.tenant_id, principal=principal, account=account
            )

            response = env.call_impl()
            assert len(response.accounts) == 1

Available mocks via env.mock:
    "audit_logger" -- get_audit_logger (module-level import)

"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from src.core.schemas.account import ListAccountsResponse
from tests.harness._base import IntegrationEnv
from tests.harness._mixins import AccountListDispatchMixin


class AccountListEnv(AccountListDispatchMixin, IntegrationEnv):
    """Integration test environment for _list_accounts_impl.

    Only mocks the audit logger. Everything else is real:
    - Real get_db_session -> real DB queries
    - Real AccountRepository -> real DB reads
    - Real query building, filtering, pagination
    """

    # Dispatch declaration: the base owns call_mcp/call_a2a, routing both through
    # the ONE AdCPTestClient core (BaseTestEnv._deliver_via_client), so the wire
    # rides back on DeliverResult.wire_response / the raised error's
    # wire_error_envelope instead of a per-env stash.
    # test_architecture_harness_single_dispatch names this env in the converted
    # set: an override here would be a new violation, not an allowlist row.
    MCP_TOOL = "list_accounts"
    A2A_SKILL = "list_accounts"
    RESPONSE_MODEL = ListAccountsResponse

    EXTERNAL_PATCHES = {
        "audit_logger": "src.core.tools.accounts.get_audit_logger",
    }

    def _configure_mocks(self) -> None:
        """Set up happy-path defaults for audit logger."""
        mock_logger = MagicMock()
        self.mock["audit_logger"].return_value = mock_logger

    # The non-transport halves of the list verb — the _impl call, the REST path
    # and the REST parser — stay in AccountListDispatchMixin because AccountSyncEnv
    # dispatches list_accounts as its SECOND verb and must grade the same
    # production call the same way. Its MCP/A2A halves are what the base's
    # declaration above replaces here; the dual-verb env still needs a
    # request-content discriminator, so it keeps overriding deliver_* itself.

    def call_impl(self, **kwargs: Any) -> ListAccountsResponse:
        """Call _list_accounts_impl with real DB.

        Accepts all _list_accounts_impl kwargs. The 'identity' kwarg
        defaults to self.identity if not provided.
        """
        return self._call_list_impl(**kwargs)

    REST_ENDPOINT = AccountListDispatchMixin.LIST_REST_ENDPOINT

    def parse_rest_response(self, data: dict[str, Any]) -> ListAccountsResponse:
        """Parse REST JSON into ListAccountsResponse."""
        return self._parse_list_rest_response(data)
