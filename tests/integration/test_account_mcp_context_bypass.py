"""Integration tests for MCP wrapper context parameter bypass (salesagent-rhp).

The MCP wrappers in accounts.py accept ``context`` as a separate kwarg
(for FastMCP tool dispatch). In production, FastMCP passes tool parameters
as individual kwargs: ``list_accounts(req=..., ctx=..., context=ContextObject(...))``.

The test harness ``_run_mcp_wrapper`` passes ``req`` to the wrapper with
context inside it (``req.context``), but never extracts ``context`` and
passes it as a separate kwarg. This means the MCP wrappers' ``if context
is not None:`` branch (lines 226-231 for list, 689-694 for sync) is never
exercised through the normal harness path.

Additionally, the BDD step ``when_request_with_context`` for list_accounts
calls ``_list_accounts_impl`` directly, bypassing transport dispatch entirely.

beads: salesagent-rhp
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from adcp.types.generated_poc.core.context import ContextObject
from fastmcp.server.context import Context

from src.core.schemas.account import (
    ListAccountsRequest,
    ListAccountsResponse,
    SyncAccountsResponse,
)
from tests.harness.account_list import AccountListEnv
from tests.harness.account_sync import AccountSyncEnv

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


class TestMCPContextParamBypass:
    """MCP wrappers accept ``context`` as separate kwarg, but harness never passes it that way."""

    def test_harness_mcp_exercises_context_merge_via_separate_kwarg(self, integration_db):
        """The harness should exercise the MCP wrapper's context merge branch.

        When the harness passes req with req.context set, the _run_mcp_wrapper
        should extract context from req and also pass it as a separate kwarg,
        mimicking production FastMCP behavior. This ensures the MCP wrapper's
        ``if context is not None:`` merge branch is exercised.

        BUG: _run_mcp_wrapper passes req as-is. The MCP wrapper receives
        context=None (the default for the separate kwarg parameter) and
        skips the merge branch entirely. The response still includes context
        because _impl echoes req.context, but the wrapper's own merge code
        is never tested.

        This test instruments the MCP wrapper to detect whether the merge
        branch fires, then calls through the harness.
        """
        from tests.factories import (
            AccountFactory,
            AgentAccountAccessFactory,
            PrincipalFactory,
            TenantFactory,
        )

        with AccountListEnv(tenant_id="merge_t1", principal_id="merge_agent") as env:
            tenant = TenantFactory(tenant_id="merge_t1")
            principal = PrincipalFactory(tenant=tenant, principal_id="merge_agent")
            acc = AccountFactory(tenant=tenant, account_id="acc_merge_1")
            AgentAccountAccessFactory(tenant_id=tenant.tenant_id, principal=principal, account=acc)

            context_obj = ContextObject.model_validate({"channel": "merge-test"})
            req = ListAccountsRequest(context=context_obj)

            # Instrument: track whether the MCP wrapper's context merge fires
            merge_branch_entered = False
            original_wrapper = None

            # Import the real wrapper to wrap it
            from src.core.tools import accounts as accounts_mod

            original_list_accounts = accounts_mod.list_accounts

            async def instrumented_list_accounts(ctx=None, context=None, **rest):
                nonlocal merge_branch_entered
                if context is not None:
                    merge_branch_entered = True
                return await original_list_accounts(ctx=ctx, context=context, **rest)

            # Patch at the module level where call_mcp imports it
            with patch.object(accounts_mod, "list_accounts", instrumented_list_accounts):
                # Also need to patch where the harness imports from
                import tests.harness.account_list as al_mod

                old_call_mcp = al_mod.AccountListEnv.call_mcp

                def patched_call_mcp(self, **kwargs):
                    return self._run_mcp_wrapper(instrumented_list_accounts, ListAccountsResponse, **kwargs)

                al_mod.AccountListEnv.call_mcp = patched_call_mcp
                try:
                    response = env.call_mcp(req=req)
                finally:
                    al_mod.AccountListEnv.call_mcp = old_call_mcp

        # Response has context (echoed from req.context by _impl) — this works
        assert response.context is not None
        assert response.context.channel == "merge-test"

        # But did the MCP wrapper's context merge branch fire?
        assert merge_branch_entered, (
            "BUG: The MCP wrapper's 'if context is not None' merge branch "
            "(accounts.py:226-231) was NOT entered when calling through the "
            "harness. _run_mcp_wrapper does not extract context from req and "
            "pass it as a separate kwarg."
        )


class TestMCPContextDirectCalls:
    """Verify MCP wrappers work correctly when called the production way (direct calls)."""

    def test_list_accounts_mcp_context_as_separate_kwarg(self, integration_db):
        """MCP list_accounts forwards context when passed as separate kwarg.

        Calls the wrapper directly with context as a separate kwarg,
        exercising lines 226-231 in accounts.py.
        """
        from src.core.tools.accounts import list_accounts
        from tests.factories import (
            AccountFactory,
            AgentAccountAccessFactory,
            PrincipalFactory,
            TenantFactory,
        )

        with AccountListEnv(tenant_id="mcp_ctx_t1", principal_id="mcp_ctx_agent") as env:
            tenant = TenantFactory(tenant_id="mcp_ctx_t1")
            principal = PrincipalFactory(tenant=tenant, principal_id="mcp_ctx_agent")
            acc = AccountFactory(tenant=tenant, account_id="acc_mcp_ctx_1", name="Ctx Test")
            AgentAccountAccessFactory(tenant_id=tenant.tenant_id, principal=principal, account=acc)
            env._commit_factory_data()

            context_obj = ContextObject.model_validate({"channel": "mcp-test"})

            from tests.harness.transport import Transport

            mcp_identity = env.identity_for(Transport.MCP)
            mock_ctx = MagicMock(spec=Context)
            mock_ctx.get_state = AsyncMock(return_value=mcp_identity)

            tool_result = asyncio.run(list_accounts(ctx=mock_ctx, context=context_obj))
            response = ListAccountsResponse(**tool_result.structured_content)

        assert response.context is not None
        assert response.context.channel == "mcp-test"

    def test_sync_accounts_mcp_context_as_separate_kwarg(self, integration_db):
        """MCP sync_accounts forwards context when passed as separate kwarg.

        Exercises lines 689-694 in accounts.py.
        """
        from src.core.tools.accounts import sync_accounts

        with AccountSyncEnv(tenant_id="mcp_sync_ctx_t1", principal_id="mcp_sync_ctx_agent") as env:
            env.setup_default_data()

            context_obj = ContextObject.model_validate({"channel": "sync-mcp-test"})

            from tests.harness.transport import Transport

            mcp_identity = env.identity_for(Transport.MCP)
            mock_ctx = MagicMock(spec=Context)
            mock_ctx.get_state = AsyncMock(return_value=mcp_identity)

            tool_result = asyncio.run(
                sync_accounts(
                    accounts=[{"brand": {"domain": "ctx-sync.com"}, "operator": "ctx-sync.com", "billing": "operator"}],
                    ctx=mock_ctx,
                    context=context_obj,
                )
            )
            response = SyncAccountsResponse(**tool_result.structured_content)

        assert response.context is not None
        assert response.context.channel == "sync-mcp-test"


class TestBDDTransportBypass:
    """Demonstrate that BDD step when_request_with_context bypasses transport for list_accounts."""

    def test_list_accounts_context_through_dispatch(self, integration_db):
        """list_accounts with context should go through dispatch_request, not _impl directly.

        The BDD step when_request_with_context calls _list_accounts_impl
        directly for list_accounts, bypassing transport dispatch. This means
        the MCP/A2A/REST transports are never tested for list_accounts context echo.

        This test verifies that dispatch_request works correctly for list_accounts
        with context, proving the BDD step SHOULD use it.
        """
        from tests.bdd.steps.generic._dispatch import dispatch_request
        from tests.factories import (
            AccountFactory,
            AgentAccountAccessFactory,
            PrincipalFactory,
            TenantFactory,
        )

        with AccountListEnv(tenant_id="bdd_disp_t1", principal_id="bdd_disp_agent") as env:
            tenant = TenantFactory(tenant_id="bdd_disp_t1")
            principal = PrincipalFactory(tenant=tenant, principal_id="bdd_disp_agent")
            acc = AccountFactory(tenant=tenant, account_id="acc_disp_1")
            AgentAccountAccessFactory(tenant_id=tenant.tenant_id, principal=principal, account=acc)

            context_obj = ContextObject.model_validate({"channel": "dispatch-test"})
            req = ListAccountsRequest(context=context_obj)

            # Simulate what the BDD step SHOULD do (but doesn't for list_accounts)
            bdd_ctx = {"env": env, "transport": "IMPL"}
            dispatch_request(bdd_ctx, req=req)

            response = bdd_ctx["response"]

        assert response.context is not None
        assert response.context.channel == "dispatch-test"
