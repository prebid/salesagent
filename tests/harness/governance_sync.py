"""GovernanceSyncEnv — integration test environment for _sync_governance_impl.

Patches: audit logger ONLY.
Real: get_db_session, AccountRepository, resolve_account, all persistence logic
(all hit real DB).

Requires: integration_db fixture (creates test PostgreSQL DB).

Mirrors AccountSyncEnv (sibling account-domain write tool). Used by the
sync_governance integration tests and, later, the UC-030 BDD ledger.

beads: #1329
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock

from src.core.schemas.account import SyncGovernanceResponse
from tests.harness._base import IntegrationEnv


class GovernanceSyncEnv(IntegrationEnv):
    """Integration test environment for _sync_governance_impl.

    Only mocks the audit logger. Everything else is real:
    - Real get_db_session -> real DB queries
    - Real AccountRepository + resolve_account -> real authority checks + writes
    - Real governance-agent persistence (url-only, replace semantics)
    """

    EXTERNAL_PATCHES = {
        "audit_logger": "src.core.tools.governance.get_audit_logger",
    }

    REST_ENDPOINT = "/api/v1/accounts/governance/sync"

    def _configure_mocks(self) -> None:
        """Set up happy-path defaults for the audit logger."""
        self.mock["audit_logger"].return_value = MagicMock()

    async def call_impl_async(self, **kwargs: Any) -> SyncGovernanceResponse:
        """Call _sync_governance_impl with real DB (async version)."""
        from src.core.tools.governance import _sync_governance_impl

        self._commit_factory_data()
        kwargs.setdefault("identity", self.identity)
        return await _sync_governance_impl(**kwargs)

    def call_impl(self, **kwargs: Any) -> SyncGovernanceResponse:
        """Call _sync_governance_impl with real DB (sync wrapper for BDD steps)."""
        return asyncio.run(self.call_impl_async(**kwargs))

    def call_a2a(self, **kwargs: Any) -> SyncGovernanceResponse:
        """Call sync_governance via real AdCPRequestHandler — full A2A pipeline."""
        return self._run_a2a_handler("sync_governance", SyncGovernanceResponse, **kwargs)

    def call_mcp(self, **kwargs: Any) -> SyncGovernanceResponse:
        """Call sync_governance via Client(mcp) — full pipeline dispatch."""
        return self._run_mcp_client("sync_governance", SyncGovernanceResponse, **kwargs)

    def build_rest_body(self, **kwargs: Any) -> dict[str, Any]:
        """Build the POST body from flat kwargs (idempotency_key, accounts, context).

        BDD steps dispatch raw kwargs (not a pre-built ``req``) so request
        validation fires at the transport boundary and yields a real wire
        envelope. A missing ``idempotency_key`` is intentionally omitted here so
        the boundary rejects it (UC-030 grades that).
        """
        body: dict[str, Any] = {}
        for field in ("idempotency_key", "accounts", "context"):
            if kwargs.get(field) is not None:
                body[field] = kwargs[field]
        return body

    def parse_rest_response(self, data: dict[str, Any]) -> SyncGovernanceResponse:
        """Parse REST JSON into SyncGovernanceResponse."""
        return SyncGovernanceResponse(**data)
