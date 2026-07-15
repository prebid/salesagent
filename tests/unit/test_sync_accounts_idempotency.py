"""idempotency_key forwarding + required-field enforcement for sync_accounts (#1512, 3.1.1).

AdCP 3.1.1 (SDK 6.6) makes ``idempotency_key`` REQUIRED on sync_accounts (16-255,
pattern ``^[A-Za-z0-9_.:-]{16,255}$``). These tests pin that:

- the client's key is forwarded VERBATIM to the SyncAccountsRequest on every
  transport (no strip, no re-generation), and
- a missing / malformed key is REJECTED as VALIDATION_ERROR at the boundary — the
  transport never synthesizes a UUID to paper over the buyer's omission.

Per-transport wire-envelope assertions for missing/malformed keys (and the full
replay/conflict/ceiling matrix) live in the integration suite
``test_sync_accounts_idempotency_replay.py`` (needs the harness + real DB); these
unit tests exercise the wrapper's request construction directly.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.exceptions import AdCPValidationError
from tests.factories import PrincipalFactory

# A pattern-valid key (>=16 chars, charset [A-Za-z0-9_.:-]).
VALID_KEY = "client-key-1234567890"


class TestSyncAccountsIdempotencyKeyPreserved:
    """The client's idempotency_key survives verbatim to the SyncAccountsRequest."""

    @pytest.mark.asyncio
    async def test_mcp_wrapper_forwards_client_idempotency_key(self):
        from src.core.tools import accounts

        with patch.object(accounts, "_sync_accounts_impl", new_callable=AsyncMock, return_value={}) as mock_impl:
            await accounts.sync_accounts(accounts=[], idempotency_key=VALID_KEY, ctx=None)

        req = mock_impl.call_args.args[0]
        assert req.idempotency_key == VALID_KEY

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
            await handler._handle_sync_accounts_skill({"accounts": [], "idempotency_key": VALID_KEY}, identity)

        req = mock_tool.call_args.kwargs["req"]
        assert req.idempotency_key == VALID_KEY

    @pytest.mark.asyncio
    async def test_rest_route_forwards_client_idempotency_key(self):
        from src.routes import api_v1

        identity = PrincipalFactory.make_identity(
            principal_id="p1", tenant_id="t1", tenant={"tenant_id": "t1"}, protocol="rest"
        )
        body = api_v1.SyncAccountsBody(accounts=[], idempotency_key=VALID_KEY)

        with patch.object(
            api_v1.accounts_module, "sync_accounts_raw", new_callable=AsyncMock, return_value=MagicMock()
        ) as mock_raw:
            await api_v1.sync_accounts(body, identity, raw_wire_payload={})

        req = mock_raw.call_args.kwargs["req"]
        assert req.idempotency_key == VALID_KEY


class TestSyncAccountsMissingKeyRejected:
    """A missing/malformed idempotency_key rejects as VALIDATION_ERROR — never synthesized.

    The boundary translates the required-field / constraint Pydantic ValidationError
    into a typed AdCPValidationError (VALIDATION_ERROR / correctable) BEFORE the impl
    is reached, so no UUID is fabricated and the impl is never called.
    """

    @pytest.mark.asyncio
    async def test_mcp_wrapper_rejects_missing_key(self):
        from src.core.tools import accounts

        with patch.object(accounts, "_sync_accounts_impl", new_callable=AsyncMock) as mock_impl:
            with pytest.raises(AdCPValidationError) as exc:
                await accounts.sync_accounts(accounts=[], ctx=None)

        assert exc.value.error_code == "VALIDATION_ERROR"
        assert exc.value.recovery == "correctable"
        mock_impl.assert_not_called()

    @pytest.mark.asyncio
    async def test_mcp_wrapper_rejects_short_key(self):
        from src.core.tools import accounts

        with patch.object(accounts, "_sync_accounts_impl", new_callable=AsyncMock) as mock_impl:
            with pytest.raises(AdCPValidationError) as exc:
                await accounts.sync_accounts(accounts=[], idempotency_key="tooshort", ctx=None)

        assert exc.value.error_code == "VALIDATION_ERROR"
        assert exc.value.recovery == "correctable"
        mock_impl.assert_not_called()

    @pytest.mark.asyncio
    async def test_a2a_handler_rejects_missing_key(self):
        from src.a2a_server.adcp_a2a_server import AdCPRequestHandler

        handler = AdCPRequestHandler()
        identity = PrincipalFactory.make_identity(
            principal_id="p1", tenant_id="t1", tenant={"tenant_id": "t1"}, protocol="a2a"
        )

        with patch("src.a2a_server.adcp_a2a_server.core_sync_accounts_tool", new_callable=AsyncMock) as mock_tool:
            with pytest.raises(AdCPValidationError) as exc:
                await handler._handle_sync_accounts_skill({"accounts": []}, identity)

        assert exc.value.error_code == "VALIDATION_ERROR"
        assert exc.value.recovery == "correctable"
        mock_tool.assert_not_called()

    @pytest.mark.asyncio
    async def test_rest_route_rejects_missing_key(self):
        from src.routes import api_v1

        identity = PrincipalFactory.make_identity(
            principal_id="p1", tenant_id="t1", tenant={"tenant_id": "t1"}, protocol="rest"
        )
        body = api_v1.SyncAccountsBody(accounts=[])

        with patch.object(api_v1.accounts_module, "sync_accounts_raw", new_callable=AsyncMock) as mock_raw:
            with pytest.raises(AdCPValidationError) as exc:
                await api_v1.sync_accounts(body, identity, raw_wire_payload={})

        assert exc.value.error_code == "VALIDATION_ERROR"
        assert exc.value.recovery == "correctable"
        mock_raw.assert_not_called()
