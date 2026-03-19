"""List Accounts tool implementation.

Handles account discovery per AdCP spec (UC-011):
- Agent-scoped results (BR-RULE-054)
- Auth-optional with empty fallback (BR-RULE-055)
- Status filtering
- Cursor pagination

beads: salesagent-hl0
"""

import logging
from typing import Any, cast

from adcp.types.generated_poc.core.context import ContextObject
from fastmcp.server.context import Context
from fastmcp.tools.tool import ToolResult

from src.core.database.models import Account as DBAccount
from src.core.database.repositories.uow import AccountUoW
from src.core.resolved_identity import ResolvedIdentity
from src.core.schemas.account import Account, ListAccountsRequest, ListAccountsResponse
from src.core.tool_context import ToolContext

logger = logging.getLogger(__name__)


def _db_account_to_schema(db_account: DBAccount) -> Account:
    """Convert ORM Account to Pydantic schema Account."""
    return Account(
        account_id=db_account.account_id,
        name=db_account.name,
        status=db_account.status,
        advertiser=db_account.advertiser,
        billing_proxy=db_account.billing_proxy,
        brand=db_account.brand,
        operator=db_account.operator,
        billing=db_account.billing,
        rate_card=db_account.rate_card,
        payment_terms=db_account.payment_terms,
        credit_limit=db_account.credit_limit,
        setup=db_account.setup,
        account_scope=db_account.account_scope,
        governance_agents=db_account.governance_agents,
        sandbox=db_account.sandbox,
        ext=db_account.ext,
    )


def _list_accounts_impl(
    req: ListAccountsRequest | None = None,
    identity: ResolvedIdentity | None = None,
) -> ListAccountsResponse:
    """List accounts accessible to the authenticated agent.

    Per BR-RULE-055: works without auth, returns empty array for unauthenticated.
    Per BR-RULE-054: returns only accounts accessible to the agent.

    Args:
        req: Optional request with status filter and pagination.
        identity: Resolved identity for authentication.

    Returns:
        ListAccountsResponse with scoped account list.
    """
    if req is None:
        req = ListAccountsRequest()

    # BR-RULE-055: unauthenticated → empty response
    if identity is None or identity.tenant_id is None:
        return ListAccountsResponse(
            accounts=[],
            context=req.context,
        )

    tenant_id = identity.tenant_id
    principal_id = identity.principal_id
    assert tenant_id is not None  # guarded above
    assert principal_id is not None  # guarded above

    with AccountUoW(tenant_id) as uow:
        assert uow.accounts is not None
        # BR-RULE-054: agent-scoped results
        db_accounts = uow.accounts.list_for_agent(principal_id)

        # Apply status filter if requested
        status_filter = getattr(req, "status", None)
        if status_filter is not None:
            db_accounts = [a for a in db_accounts if a.status == status_filter]

        # Apply sandbox filter if requested
        sandbox_filter = getattr(req, "sandbox", None)
        if sandbox_filter is not None:
            db_accounts = [a for a in db_accounts if a.sandbox == sandbox_filter]

        # Convert ORM models to schema models while session is alive
        schema_accounts = [_db_account_to_schema(a) for a in db_accounts]

    return ListAccountsResponse(
        accounts=schema_accounts,
        context=req.context,
    )


# ---------------------------------------------------------------------------
# MCP wrapper
# ---------------------------------------------------------------------------


async def list_accounts(
    req: ListAccountsRequest | None = None,
    ctx: Context | ToolContext | None = None,
    context: ContextObject | None = None,
) -> Any:
    """List accounts accessible to the authenticated agent (MCP tool).

    MCP wrapper that delegates to the shared implementation.

    Args:
        req: Optional request with status filter and pagination.
        context: Application-level context per AdCP spec.
        ctx: FastMCP context for authentication.

    Returns:
        ToolResult with human-readable text and structured data.
    """
    if context is not None:
        if req is None:
            req = ListAccountsRequest(context=context)
        else:
            req = cast(ListAccountsRequest, req)
            req.context = context

    identity = (await ctx.get_state("identity")) if isinstance(ctx, Context) else None
    response = _list_accounts_impl(cast(ListAccountsRequest | None, req), identity)

    return ToolResult(content=str(response), structured_content=response)


# ---------------------------------------------------------------------------
# A2A raw wrapper
# ---------------------------------------------------------------------------


def list_accounts_raw(
    req: ListAccountsRequest | None = None,
    ctx: Context | ToolContext | None = None,
    identity: ResolvedIdentity | None = None,
) -> ListAccountsResponse:
    """List accounts accessible to the authenticated agent (raw function for A2A).

    Args:
        req: Optional request with filter parameters.
        ctx: FastMCP context.
        identity: Pre-resolved identity (if available).

    Returns:
        ListAccountsResponse with accessible accounts.
    """
    if identity is None:
        from src.core.transport_helpers import resolve_identity_from_context

        identity = resolve_identity_from_context(ctx, require_valid_token=False)
    return _list_accounts_impl(req, identity)
