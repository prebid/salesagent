"""AccountSyncEnv — integration test environment for _sync_accounts_impl.

Patches: audit logger ONLY.
Real: get_db_session, AccountRepository, all upsert/deactivate logic (all hit real DB).

Requires: integration_db fixture (creates test PostgreSQL DB).

Usage::

    @pytest.mark.requires_db
    async def test_something(self, integration_db):
        with AccountSyncEnv() as env:
            tenant, principal = env.setup_default_data()

            response = await env.call_impl_async(
                accounts=[{"brand": {"domain": "acme.com"}, "operator": "acme.com", "billing": "operator"}]
            )
            assert len(response.accounts) == 1

Available mocks via env.mock:
    "audit_logger" -- get_audit_logger (module-level import)

beads: salesagent-7do
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock

from src.core.schemas.account import SyncAccountsResponse
from tests.harness._base import IntegrationEnv


class AccountSyncEnv(IntegrationEnv):
    """Integration test environment for _sync_accounts_impl.

    Only mocks the audit logger. Everything else is real:
    - Real get_db_session -> real DB queries
    - Real AccountRepository -> real DB writes
    - Real upsert, deactivate_missing, grant_access logic

    Both sync and async call patterns are supported:
    - call_impl() / call_a2a(): sync wrappers for BDD steps and dispatchers
    - call_impl_async() / call_a2a_async(): for @pytest.mark.asyncio tests

    Constructor accepts ``supported_billing`` to configure billing policy
    on the identity (BR-RULE-059).
    """

    EXTERNAL_PATCHES = {
        "audit_logger": "src.core.tools.accounts.get_audit_logger",
    }

    def __init__(
        self,
        supported_billing: list[str] | None = None,
        account_approval_mode: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._supported_billing = supported_billing
        self._account_approval_mode = account_approval_mode

    def _configure_mocks(self) -> None:
        """Set up happy-path defaults for audit logger."""
        mock_logger = MagicMock()
        self.mock["audit_logger"].return_value = mock_logger

    def set_billing_policy(self, supported: list[str]) -> None:
        """Configure which billing models this seller accepts (BR-RULE-059).

        Updates both the in-memory tenant overrides (for mock identity path)
        and the DB tenant record (for real MCP/A2A auth chain).
        """
        self._supported_billing = supported
        self._tenant_overrides["supported_billing"] = supported
        self._identity_cache.clear()

        if self._session:
            from src.core.database.models import Tenant

            tenant = self._session.get(Tenant, self._tenant_id)
            if tenant:
                tenant.supported_billing = supported
                self._session.commit()

    def set_approval_mode(self, mode: str) -> None:
        """Configure account approval mode (BR-RULE-060).

        Updates both the in-memory tenant overrides (for mock identity path)
        and the DB tenant record (for real MCP/A2A auth chain).
        """
        self._account_approval_mode = mode
        self._tenant_overrides["account_approval_mode"] = mode
        self._identity_cache.clear()

        if self._session:
            from src.core.database.models import Tenant

            tenant = self._session.get(Tenant, self._tenant_id)
            if tenant:
                # BR-RULE-060: account approval mode is a distinct field from creative
                # approval_mode (BR-RULE-037). Write to the correct column so the MCP
                # real-auth chain (which reads DB via config_loader.get_tenant_by_id)
                # sees the test-configured value.
                tenant.account_approval_mode = mode
                self._session.commit()

    def identity_for(self, transport: Any) -> Any:
        """Build identity with billing policy and approval mode on the tenant dict."""
        if self._supported_billing is not None:
            self._tenant_overrides["supported_billing"] = self._supported_billing
        if self._account_approval_mode is not None:
            self._tenant_overrides["account_approval_mode"] = self._account_approval_mode
        self._identity_cache.clear()
        return super().identity_for(transport)

    async def call_impl_async(self, **kwargs: Any) -> SyncAccountsResponse:
        """Call _sync_accounts_impl with real DB (async version).

        For use in @pytest.mark.asyncio tests with ``await``.
        """
        from src.core.tools.accounts import _sync_accounts_impl

        self._commit_factory_data()
        kwargs.setdefault("identity", self.identity)
        return await _sync_accounts_impl(**kwargs)

    def call_impl(self, **kwargs: Any) -> SyncAccountsResponse:
        """Call _sync_accounts_impl with real DB (sync wrapper).

        Bridges async _impl for sync callers (BDD steps, dispatchers).
        """
        return asyncio.run(self.call_impl_async(**kwargs))

    def call_a2a(self, **kwargs: Any) -> SyncAccountsResponse:
        """Call sync_accounts via real AdCPRequestHandler — full A2A pipeline."""
        return self._run_a2a_handler("sync_accounts", SyncAccountsResponse, **kwargs)

    def call_mcp(self, **kwargs: Any) -> SyncAccountsResponse:
        """Call sync_accounts via Client(mcp) — full pipeline dispatch."""
        return self._run_mcp_client("sync_accounts", SyncAccountsResponse, **kwargs)

    REST_ENDPOINT = "/api/v1/accounts/sync"

    def parse_rest_response(self, data: dict[str, Any]) -> SyncAccountsResponse:
        """Parse REST JSON into SyncAccountsResponse."""
        return SyncAccountsResponse(**data)
