"""GovernanceSyncEnv — integration test environment for _sync_governance_impl.

Patches: audit logger ONLY.
Real: get_db_session, AccountRepository, resolve_account, all persistence logic
(all hit real DB).

Requires: integration_db fixture (creates test PostgreSQL DB).

Mirrors AccountSyncEnv (sibling account-domain write tool). Used by the
sync_governance integration tests and the UC-030 BDD ledger.

#1329
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

    # Dispatch declaration: the base owns call_mcp/call_a2a via the client core.
    # sync_governance is a single tool registered on every transport, so it
    # delegates to the base rather than overriding deliver_* (single-dispatch guard).
    MCP_TOOL = "sync_governance"
    A2A_SKILL = "sync_governance"
    RESPONSE_MODEL = SyncGovernanceResponse

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

    def build_rest_body(self, **kwargs: Any) -> dict[str, Any]:
        """Build the POST body from a ``req`` object OR flat kwargs.

        Integration tests pass a pre-built ``req`` (delegated to the base, which
        serializes the Pydantic model). BDD steps dispatch raw kwargs
        (idempotency_key/accounts/context, no ``req``) so request validation fires
        at the transport boundary and yields a real wire envelope — a missing
        ``idempotency_key`` is intentionally omitted so the boundary rejects it
        (UC-030 grades that).
        """
        if kwargs.get("req") is not None:
            return super().build_rest_body(**kwargs)
        body: dict[str, Any] = {}
        # ``ext`` is a SyncGovernanceRequest field (SyncGovernanceBody exposes it on the HTTP
        # body); include it so the REST leg forwards it, matching the A2A/MCP wrappers — the
        # round-8 ext fix would otherwise be unreachable on REST (#1329 R9-D3).
        for field in ("idempotency_key", "accounts", "context", "ext"):
            if kwargs.get(field) is not None:
                body[field] = kwargs[field]
        return body

    def parse_rest_response(self, data: dict[str, Any]) -> SyncGovernanceResponse:
        """Parse REST JSON into SyncGovernanceResponse."""
        return SyncGovernanceResponse(**data)
