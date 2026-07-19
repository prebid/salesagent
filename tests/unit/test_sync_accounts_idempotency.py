"""Required idempotency-key preservation for sync_accounts transports.

The envelope-tolerance layer stripped the buyer's ``idempotency_key`` (MCP), the
wrapper dropped it (A2A), or the REST body model discarded it via ``extra="ignore"``;
each path then synthesized a fresh UUID. AdCP 3.1.1 requires the buyer's key;
these tests pin verbatim forwarding and fail-loud omission on every transport.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.factories import PrincipalFactory


class TestSyncAccountsIdempotencyKeyPreserved:
    """The client's idempotency_key survives to the SyncAccountsRequest."""

    @pytest.mark.asyncio
    async def test_mcp_wrapper_forwards_client_idempotency_key(self):
        from src.core.tools import accounts

        with patch.object(accounts, "_sync_accounts_impl", new_callable=AsyncMock, return_value={}) as mock_impl:
            await accounts.sync_accounts(accounts=[], idempotency_key="client-key-00001", ctx=None)

        req = mock_impl.call_args.args[0]
        assert req.idempotency_key == "client-key-00001"

    @pytest.mark.asyncio
    async def test_mcp_wrapper_rejects_explicit_null_key(self):
        from src.core.exceptions import AdCPValidationError
        from src.core.tools import accounts

        with pytest.raises(AdCPValidationError, match="idempotency_key is required"):
            await accounts.sync_accounts(idempotency_key=None, accounts=[], ctx=None)

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
            await handler._handle_sync_accounts_skill(
                {"accounts": [], "idempotency_key": "client-key-a2a-0001"}, identity
            )

        req = mock_tool.call_args.kwargs["req"]
        assert req.idempotency_key == "client-key-a2a-0001"

    @pytest.mark.asyncio
    async def test_a2a_handler_rejects_omitted_key(self):
        """The real A2A skill boundary rejects before dispatching the core tool."""
        from src.a2a_server.adcp_a2a_server import AdCPRequestHandler
        from src.core.exceptions import AdCPValidationError

        handler = AdCPRequestHandler()
        identity = PrincipalFactory.make_identity(
            principal_id="p1", tenant_id="t1", tenant={"tenant_id": "t1"}, protocol="a2a"
        )

        with pytest.raises(AdCPValidationError, match="idempotency_key is required"):
            await handler._handle_sync_accounts_skill({"accounts": []}, identity)

    @pytest.mark.asyncio
    async def test_rest_route_forwards_client_idempotency_key(self):
        from src.routes import api_v1

        identity = PrincipalFactory.make_identity(
            principal_id="p1", tenant_id="t1", tenant={"tenant_id": "t1"}, protocol="rest"
        )
        body = api_v1.SyncAccountsBody(accounts=[], idempotency_key="client-key-rest-0001")

        with patch.object(
            api_v1.accounts_module, "sync_accounts_raw", new_callable=AsyncMock, return_value=MagicMock()
        ) as mock_raw:
            await api_v1.sync_accounts(body, identity)

        req = mock_raw.call_args.kwargs["req"]
        assert req.idempotency_key == "client-key-rest-0001"

    @pytest.mark.asyncio
    async def test_rest_route_rejects_omitted_key(self):
        from src.core.exceptions import AdCPValidationError
        from src.routes import api_v1

        identity = PrincipalFactory.make_identity(
            principal_id="p1", tenant_id="t1", tenant={"tenant_id": "t1"}, protocol="rest"
        )
        # Explicit None reaches the shared route guard. An actually omitted
        # field is rejected earlier by FastAPI and covered by the REST wire
        # matrix in test_rest_api_endpoints.py.
        body = api_v1.SyncAccountsBody(accounts=[], idempotency_key=None)

        with pytest.raises(AdCPValidationError, match="idempotency_key is required"):
            await api_v1.sync_accounts(body, identity)
