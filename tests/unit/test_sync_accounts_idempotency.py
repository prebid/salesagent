"""Idempotency-key preservation for sync_accounts across MCP and A2A (#1512).

The envelope-tolerance layer stripped the buyer's ``idempotency_key`` (MCP) or
the wrapper dropped it (A2A), and both paths then synthesized a fresh UUID — so
two retries carrying the SAME client key were treated as distinct requests. These
tests pin that the client's key is forwarded verbatim, and that a fresh key is
synthesized only when the client omits one.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from tests.factories import PrincipalFactory


class TestSyncAccountsIdempotencyKeyPreserved:
    """The client's idempotency_key survives to the SyncAccountsRequest."""

    @pytest.mark.asyncio
    async def test_mcp_wrapper_forwards_client_idempotency_key(self):
        from src.core.tools import accounts

        with patch.object(accounts, "_sync_accounts_impl", new_callable=AsyncMock, return_value={}) as mock_impl:
            await accounts.sync_accounts(accounts=[], idempotency_key="client-key-123", ctx=None)

        req = mock_impl.call_args.args[0]
        assert req.idempotency_key == "client-key-123"

    @pytest.mark.asyncio
    async def test_mcp_wrapper_synthesizes_key_only_when_omitted(self):
        from src.core.tools import accounts

        with patch.object(accounts, "_sync_accounts_impl", new_callable=AsyncMock, return_value={}) as mock_impl:
            await accounts.sync_accounts(accounts=[], ctx=None)

        req = mock_impl.call_args.args[0]
        assert req.idempotency_key is not None
        # A synthesized fallback is a valid UUID v4 string.
        uuid.UUID(req.idempotency_key)

    @pytest.mark.asyncio
    async def test_a2a_handler_forwards_client_idempotency_key(self):
        from src.a2a_server.adcp_a2a_server import AdCPRequestHandler

        handler = AdCPRequestHandler()
        identity = PrincipalFactory.make_identity(
            principal_id="p1", tenant_id="t1", tenant={"tenant_id": "t1"}, protocol="a2a"
        )

        with patch(
            "src.a2a_server.adcp_a2a_server.core_sync_accounts_tool", new_callable=AsyncMock, return_value={}
        ) as mock_tool:
            await handler._handle_sync_accounts_skill({"accounts": [], "idempotency_key": "client-key-a2a"}, identity)

        req = mock_tool.call_args.kwargs["req"]
        assert req.idempotency_key == "client-key-a2a"
