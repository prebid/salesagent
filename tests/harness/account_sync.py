"""AccountSyncEnv — integration test environment for _sync_accounts_impl.

Patches: audit logger ONLY.
Real: get_db_session, AccountRepository, all upsert/deactivate logic (all hit real DB).

Requires: integration_db fixture (creates test PostgreSQL DB).

Usage::

    @pytest.mark.requires_db
    async def test_something(self, integration_db):
        with AccountSyncEnv() as env:
            tenant, principal = env.setup_default_data()

            response = await env.call_impl(
                accounts=[{"brand": {"domain": "acme.com"}, "operator": "acme.com", "billing": "operator"}]
            )
            assert len(response.accounts) == 1

Available mocks via env.mock:
    "audit_logger" -- get_audit_logger (module-level import)

beads: salesagent-7do
"""

from __future__ import annotations

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
    """

    EXTERNAL_PATCHES = {
        "audit_logger": "src.core.tools.accounts.get_audit_logger",
    }

    def _configure_mocks(self) -> None:
        """Set up happy-path defaults for audit logger."""
        mock_logger = MagicMock()
        self.mock["audit_logger"].return_value = mock_logger

    async def call_impl(self, **kwargs: Any) -> SyncAccountsResponse:
        """Call _sync_accounts_impl with real DB.

        Accepts all _sync_accounts_impl kwargs. The 'identity' kwarg
        defaults to self.identity if not provided.
        """
        from src.core.tools.accounts import _sync_accounts_impl

        self._commit_factory_data()
        kwargs.setdefault("identity", self.identity)
        return await _sync_accounts_impl(**kwargs)

    async def call_a2a(self, **kwargs: Any) -> SyncAccountsResponse:
        """Call sync_accounts_raw (A2A wrapper) with real DB."""
        from src.core.tools.accounts import sync_accounts_raw

        self._commit_factory_data()
        kwargs.setdefault("identity", self.identity)
        return await sync_accounts_raw(**kwargs)
