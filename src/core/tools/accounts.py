"""Account tool implementations (list + sync).

Handles account management per AdCP spec (UC-011):
- Agent-scoped results (BR-RULE-054)
- Auth-optional list with empty fallback (BR-RULE-055)
- Upsert by natural key (BR-RULE-056)
- Atomic XOR response (BR-RULE-057)
- Brand echo (BR-RULE-058)
- Approval workflow (BR-RULE-060)
- delete_missing (BR-RULE-061)
- dry_run (BR-RULE-062)

beads: salesagent-hl0, salesagent-619
"""

import base64
import logging
import uuid
from datetime import UTC
from typing import Any

from adcp.types.generated_poc.account.list_accounts_request import (
    Status as AccountStatus,
)
from adcp.types.generated_poc.account.sync_accounts_request import (
    Account as SyncAccountInput,
)
from adcp.types.generated_poc.account.sync_accounts_response import (
    Account as SyncResponseAccount,
)
from adcp.types.generated_poc.core.context import ContextObject
from adcp.types.generated_poc.core.pagination_request import PaginationRequest
from adcp.types.generated_poc.core.pagination_response import PaginationResponse
from fastmcp.server.context import Context
from fastmcp.tools.tool import ToolResult

from src.core.audit_logger import get_audit_logger
from src.core.database.models import Account as DBAccount
from src.core.database.repositories.uow import AccountUoW
from src.core.exceptions import AdCPAuthenticationError, AdCPValidationError
from src.core.resolved_identity import ResolvedIdentity
from src.core.schemas.account import (
    Account,
    ListAccountsRequest,
    ListAccountsResponse,
    SyncAccountsRequest,
    SyncAccountsResponse,
)
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


def _encode_cursor(offset: int) -> str:
    """Encode an offset as a base64 cursor string."""
    return base64.b64encode(str(offset).encode()).decode()


def _decode_cursor(cursor: str) -> int:
    """Decode a base64 cursor string to an offset. Returns 0 for invalid cursors."""
    try:
        return int(base64.b64decode(cursor).decode())
    except (ValueError, Exception):
        return 0


def _apply_pagination(
    accounts: list[Account],
    pagination: PaginationRequest | None,
) -> tuple[list[Account], PaginationResponse | None]:
    """Apply cursor-based pagination to an account list.

    Returns (paginated_accounts, pagination_response_or_None).
    """
    if pagination is None:
        return accounts, None

    max_results = pagination.max_results or 50
    offset = _decode_cursor(pagination.cursor) if pagination.cursor else 0

    paginated = accounts[offset : offset + max_results]
    has_more = (offset + max_results) < len(accounts)

    return paginated, PaginationResponse(
        has_more=has_more,
        cursor=_encode_cursor(offset + max_results) if has_more else None,
        total_count=len(accounts),
    )


def _list_accounts_impl(
    req: ListAccountsRequest | None = None,
    identity: ResolvedIdentity | None = None,
) -> ListAccountsResponse:
    """List accounts accessible to the authenticated agent.

    Per BR-RULE-055: requires authentication, raises AUTH_TOKEN_INVALID if missing.
    Per BR-RULE-054: returns only accounts accessible to the agent.

    Args:
        req: Optional request with status filter and pagination.
        identity: Resolved identity for authentication.

    Returns:
        ListAccountsResponse with scoped account list.
    """
    if req is None:
        req = ListAccountsRequest()

    # BR-RULE-055 INV-3: unauthenticated → auth error (consistent with sync_accounts)
    if identity is None or identity.principal_id is None or identity.tenant_id is None:
        from src.core.exceptions import AdCPAuthenticationError

        raise AdCPAuthenticationError("Authentication required for list_accounts")

    tenant_id = identity.tenant_id
    principal_id = identity.principal_id

    with AccountUoW(tenant_id) as uow:
        assert uow.accounts is not None
        # BR-RULE-054: agent-scoped results
        db_accounts = uow.accounts.list_for_agent(principal_id)

        # Apply status filter if requested
        status_filter = getattr(req, "status", None)
        if status_filter is not None:
            status_str = status_filter.value if hasattr(status_filter, "value") else str(status_filter)
            db_accounts = [a for a in db_accounts if a.status == status_str]

        # Apply sandbox filter if requested
        sandbox_filter = getattr(req, "sandbox", None)
        if sandbox_filter is not None:
            db_accounts = [a for a in db_accounts if a.sandbox == sandbox_filter]

        # Sort for deterministic pagination
        db_accounts.sort(key=lambda a: a.account_id)

        # Convert ORM models to schema models while session is alive
        schema_accounts = [_db_account_to_schema(a) for a in db_accounts]

    # Apply pagination after conversion
    paginated, pagination_resp = _apply_pagination(schema_accounts, getattr(req, "pagination", None))

    return ListAccountsResponse(
        accounts=paginated,
        pagination=pagination_resp,
        context=req.context,
    )


# ---------------------------------------------------------------------------
# MCP wrapper
# ---------------------------------------------------------------------------


async def list_accounts(
    status: AccountStatus | None = None,
    pagination: PaginationRequest | None = None,
    sandbox: bool | None = None,
    context: ContextObject | None = None,
    ctx: Context | ToolContext | None = None,
) -> Any:
    """List accounts accessible to the authenticated agent (MCP tool).

    MCP wrapper that delegates to the shared implementation.
    FastMCP automatically validates and coerces JSON inputs to Pydantic models.

    Args:
        status: Filter accounts by status (active, closed, etc.).
        pagination: Pagination parameters (max_results, cursor).
        sandbox: Filter by sandbox flag.
        context: Application-level context per AdCP spec.
        ctx: FastMCP context for authentication.

    Returns:
        ToolResult with human-readable text and structured data.
    """
    req = ListAccountsRequest(
        status=status,
        pagination=pagination,
        sandbox=sandbox,
        context=context,
    )

    identity = (await ctx.get_state("identity")) if isinstance(ctx, Context) else None
    response = _list_accounts_impl(req, identity)

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


# ===========================================================================
# sync_accounts — upsert accounts by natural key (BR-RULE-056..062)
# ===========================================================================


def _generate_account_id() -> str:
    """Generate a unique account ID."""
    return f"acc_{uuid.uuid4().hex[:12]}"


def _generate_account_name(brand_domain: str, operator: str, brand_id: str | None = None) -> str:
    """Generate a human-readable account name from brand + operator."""
    brand_part = f"{brand_domain}:{brand_id}" if brand_id else brand_domain
    return f"{brand_part} c/o {operator}"


def _enum_to_str(val: Any) -> str | None:
    """Extract string value from an enum or return as-is. Returns None for None."""
    if val is None:
        return None
    return val.value if hasattr(val, "value") else str(val)


def _serialize_governance_agents(agents: Any) -> list[dict[str, Any]] | None:
    """Convert GovernanceAgent models to JSON-serializable dicts for DB storage."""
    if agents is None:
        return None
    result: list[dict[str, Any]] = []
    for g in agents:
        if isinstance(g, dict):
            result.append(g)
        elif hasattr(g, "model_dump"):
            result.append(g.model_dump(mode="json"))
        else:
            result.append(dict(g))
    return result


def _account_fields_changed(db_account: DBAccount, entry: Any) -> dict[str, Any]:
    """Compare incoming sync entry fields against existing DB account.

    Returns a dict of fields that changed (key → new value).
    Only compares mutable fields that can be updated via sync.
    """
    changes: dict[str, Any] = {}

    billing_val = _enum_to_str(entry.billing)
    if db_account.billing != billing_val:
        changes["billing"] = billing_val

    payment_terms_val = _enum_to_str(entry.payment_terms)
    if db_account.payment_terms != payment_terms_val:
        changes["payment_terms"] = payment_terms_val

    # Normalize: None and False are equivalent for sandbox (DB defaults to False)
    sandbox_val = entry.sandbox or False
    db_sandbox = db_account.sandbox or False
    if db_sandbox != sandbox_val:
        changes["sandbox"] = entry.sandbox

    # Compare governance_agents (JSON field)
    # Both sides must be serialized to dicts for comparison — db_account.governance_agents
    # is hydrated to list[GovernanceAgent] by JSONType, while incoming is already serialized.
    incoming_gov = _serialize_governance_agents(entry.governance_agents)
    db_gov = _serialize_governance_agents(db_account.governance_agents)
    if db_gov != incoming_gov:
        changes["governance_agents"] = incoming_gov

    return changes


def _build_sync_result(
    *,
    brand: Any,
    operator: str,
    action: str,
    status: str,
    name: str | None = None,
    billing: str | None = None,
    sandbox: bool | None = None,
    errors: list[Any] | None = None,
    setup: Any | None = None,
) -> SyncResponseAccount:
    """Build an AdCP sync response Account object."""
    return SyncResponseAccount(
        brand=brand,
        operator=operator,
        action=action,
        status=status,
        name=name,
        billing=billing,
        sandbox=sandbox,
        errors=errors,
        setup=setup,
    )


def _build_setup_for_approval(mode: str, tenant_id: str) -> Any:
    """Build a Setup object based on the approval mode.

    Returns Setup for pending_approval modes, None for auto-approve.
    """
    from datetime import datetime, timedelta

    from adcp.types.generated_poc.account.sync_accounts_response import Setup

    if mode == "credit_review":
        return Setup(
            message="Account requires credit review before activation. Please complete the credit application.",
            url=f"https://seller.example.com/accounts/review?tenant={tenant_id}",
            expires_at=datetime.now(tz=UTC) + timedelta(days=7),
        )
    if mode == "legal_review":
        return Setup(
            message="Account requires legal review before activation. Our team will review your application.",
        )
    return None


def _check_domain_validity(brand_domain: str) -> list[Any] | None:
    """Check if the brand domain is valid for account provisioning.

    Returns a list of Error objects if invalid, None if valid.
    Reserved TLDs (.test, .invalid, .example, .localhost) are rejected.
    """
    from adcp.types.generated_poc.core.error import Error

    reserved_tlds = {".test", ".invalid", ".example", ".localhost"}
    for tld in reserved_tlds:
        if brand_domain.endswith(tld):
            return [
                Error(
                    code="INVALID_DOMAIN",
                    message=f"Domain '{brand_domain}' uses reserved TLD '{tld}' "
                    f"and cannot be used for account provisioning.",
                    suggestion="Use a real domain name for production accounts.",
                    field="brand.domain",
                )
            ]
    return None


def _check_billing_policy(
    billing_val: str | None,
    identity: ResolvedIdentity,
) -> list[Any] | None:
    """Check if the billing model is supported by the seller.

    Returns a list of Error objects if rejected, None if accepted.
    Per BR-RULE-059: unsupported billing → BILLING_NOT_SUPPORTED.
    """
    from adcp.types.generated_poc.core.error import Error

    supported = getattr(identity, "supported_billing", None)
    if supported is None:
        return None  # No policy configured → accept all

    if billing_val not in supported:
        return [
            Error(
                code="BILLING_NOT_SUPPORTED",
                message=f"Billing model '{billing_val}' is not supported by this seller. "
                f"Supported models: {', '.join(supported)}.",
                suggestion=f"Use one of the supported billing models: {', '.join(supported)}.",
            )
        ]
    return None


def _extract_natural_key(entry: Any) -> tuple[str, str | None, str, bool | None]:
    """Extract natural key components from a sync request account entry.

    Returns (brand_domain, brand_id, operator, sandbox).
    """
    brand = entry.brand
    brand_domain = brand.domain
    brand_id = None
    if hasattr(brand, "brand_id") and brand.brand_id is not None:
        brand_id = str(brand.brand_id)
    operator = entry.operator
    sandbox = entry.sandbox
    return brand_domain, brand_id, operator, sandbox


async def _sync_accounts_impl(
    req: SyncAccountsRequest | None = None,
    identity: ResolvedIdentity | None = None,
) -> SyncAccountsResponse:
    """Sync accounts by natural key — upsert, delete_missing, dry_run.

    Per AdCP spec (BR-RULE-055..062):
    - Auth required (BR-RULE-055)
    - Upsert by natural key: brand.domain + brand.brand_id + operator + sandbox (BR-RULE-056)
    - Atomic XOR: success accounts[] or error errors[], never both (BR-RULE-057)
    - Brand echoed from request (BR-RULE-058)
    - New accounts get status=active (BR-RULE-060, auto-approve for now)
    - delete_missing closes absent accounts scoped to agent (BR-RULE-061)
    - dry_run previews without persisting (BR-RULE-062)

    Args:
        req: Sync request with accounts list and options.
        identity: Resolved identity (must be authenticated).

    Returns:
        SyncAccountsResponse with per-account action results.
    """
    if req is None:
        req = SyncAccountsRequest(accounts=[])

    # BR-RULE-055: sync requires auth
    if identity is None or identity.principal_id is None or identity.tenant_id is None:
        raise AdCPAuthenticationError("Authentication required: sync_accounts requires a valid auth token.")

    # Validate non-empty accounts array
    if not req.accounts:
        raise AdCPValidationError("accounts array must not be empty — at least one account is required.")

    tenant_id = identity.tenant_id
    principal_id = identity.principal_id
    dry_run = bool(req.dry_run)
    delete_missing = bool(req.delete_missing)

    results: list[SyncResponseAccount] = []
    # Track natural keys in the payload for delete_missing
    seen_account_ids: set[str] = set()

    with AccountUoW(tenant_id) as uow:
        assert uow.accounts is not None
        repo = uow.accounts

        for entry in req.accounts:
            brand_domain, brand_id, operator, sandbox = _extract_natural_key(entry)
            billing_val = _enum_to_str(entry.billing)

            # Domain validation: reject reserved TLDs
            domain_errors = _check_domain_validity(brand_domain)
            if domain_errors is not None:
                results.append(
                    _build_sync_result(
                        brand=entry.brand,
                        operator=operator,
                        action="failed",
                        status="rejected",
                        billing=billing_val,
                        sandbox=sandbox,
                        errors=domain_errors,
                    )
                )
                continue

            # BR-RULE-059: check billing policy before processing
            billing_errors = _check_billing_policy(billing_val, identity)
            if billing_errors is not None:
                results.append(
                    _build_sync_result(
                        brand=entry.brand,
                        operator=operator,
                        action="failed",
                        status="rejected",
                        billing=billing_val,
                        sandbox=sandbox,
                        errors=billing_errors,
                    )
                )
                continue

            # Look up existing account by natural key
            existing = repo.get_by_natural_key(
                operator=operator,
                brand_domain=brand_domain,
                brand_id=brand_id,
                sandbox=sandbox,
            )

            if existing is not None:
                seen_account_ids.add(existing.account_id)

                if dry_run:
                    # Check if fields would change
                    changes = _account_fields_changed(existing, entry)
                    action = "updated" if changes else "unchanged"
                    results.append(
                        _build_sync_result(
                            brand=entry.brand,
                            operator=operator,
                            action=action,
                            status=existing.status,
                            name=existing.name,
                            billing=existing.billing,
                            sandbox=existing.sandbox,
                        )
                    )
                    continue

                # Check for field changes and update if needed
                changes = _account_fields_changed(existing, entry)
                if changes:
                    repo.update_fields(existing.account_id, **changes)
                    action = "updated"
                else:
                    action = "unchanged"

                results.append(
                    _build_sync_result(
                        brand=entry.brand,
                        operator=operator,
                        action=action,
                        status=existing.status,
                        name=existing.name,
                        billing=existing.billing,
                        sandbox=existing.sandbox,
                    )
                )
            else:
                # Create new account
                billing_val = _enum_to_str(entry.billing)
                payment_terms_val = _enum_to_str(entry.payment_terms)
                governance_agents_val = _serialize_governance_agents(entry.governance_agents)

                account_id = _generate_account_id()
                account_name = _generate_account_name(brand_domain, operator, brand_id)

                if dry_run:
                    results.append(
                        _build_sync_result(
                            brand=entry.brand,
                            operator=operator,
                            action="created",
                            status="active",
                            name=account_name,
                            billing=billing_val,
                            sandbox=sandbox,
                        )
                    )
                    continue

                # BR-RULE-060: determine approval status
                approval_mode = getattr(identity, "account_approval_mode", None)
                setup = _build_setup_for_approval(approval_mode or "auto", tenant_id)
                initial_status = "pending_approval" if setup else "active"

                new_account = DBAccount(
                    tenant_id=tenant_id,
                    account_id=account_id,
                    name=account_name,
                    status=initial_status,
                    brand={"domain": brand_domain, **({"brand_id": brand_id} if brand_id else {})},
                    operator=operator,
                    billing=billing_val,
                    payment_terms=payment_terms_val,
                    sandbox=sandbox,
                    governance_agents=governance_agents_val,
                    principal_id=principal_id,
                )
                repo.create(new_account)
                seen_account_ids.add(account_id)

                # Grant agent access to the new account
                repo.grant_access(principal_id, account_id)

                results.append(
                    _build_sync_result(
                        brand=entry.brand,
                        operator=operator,
                        action="created",
                        status=initial_status,
                        name=account_name,
                        billing=billing_val,
                        sandbox=sandbox,
                        setup=setup,
                    )
                )

        # BR-RULE-061: delete_missing — close accounts not in payload
        if delete_missing and not dry_run:
            agent_accounts = repo.list_by_principal(principal_id)
            for db_acct in agent_accounts:
                if db_acct.account_id not in seen_account_ids:
                    repo.update_status(db_acct.account_id, "closed")
                    results.append(
                        _build_sync_result(
                            brand=db_acct.brand,
                            operator=db_acct.operator or "",
                            action="updated",
                            status="closed",
                            name=db_acct.name,
                            billing=db_acct.billing,
                            sandbox=db_acct.sandbox,
                        )
                    )

    # Audit log
    audit_logger = get_audit_logger("sync_accounts", tenant_id)
    action_counts: dict[str, int] = {}
    for r in results:
        act = _enum_to_str(r.action) or "unknown"
        action_counts[act] = action_counts.get(act, 0) + 1
    audit_logger.log_info(f"sync_accounts completed: {action_counts} (dry_run={dry_run}, principal={principal_id})")

    return SyncAccountsResponse(
        accounts=results,
        dry_run=dry_run if dry_run else None,
        context=req.context,
    )


# ---------------------------------------------------------------------------
# sync_accounts MCP wrapper
# ---------------------------------------------------------------------------


async def sync_accounts(
    accounts: list[SyncAccountInput] | None = None,
    delete_missing: bool | None = None,
    dry_run: bool | None = None,
    context: ContextObject | None = None,
    ctx: Context | ToolContext | None = None,
) -> Any:
    """Sync accounts by natural key (MCP tool).

    MCP wrapper that accepts individual parameters per AdCP spec and
    constructs a SyncAccountsRequest for the shared implementation.

    Args:
        accounts: List of accounts to upsert.
        delete_missing: Deactivate accounts not in the list.
        dry_run: Preview changes without persisting.
        context: Application-level context per AdCP spec.
        ctx: FastMCP context for authentication.

    Returns:
        ToolResult with human-readable text and structured data.
    """
    req = SyncAccountsRequest(
        accounts=accounts or [],
        delete_missing=delete_missing,
        dry_run=dry_run,
        context=context,
    )
    identity = (await ctx.get_state("identity")) if isinstance(ctx, Context) else None
    response = await _sync_accounts_impl(req, identity)

    return ToolResult(content=str(response), structured_content=response)


# ---------------------------------------------------------------------------
# sync_accounts A2A raw wrapper
# ---------------------------------------------------------------------------


async def sync_accounts_raw(
    req: SyncAccountsRequest | None = None,
    ctx: Context | ToolContext | None = None,
    identity: ResolvedIdentity | None = None,
) -> SyncAccountsResponse:
    """Sync accounts by natural key (raw function for A2A).

    Args:
        req: Sync request with accounts to upsert.
        ctx: FastMCP context.
        identity: Pre-resolved identity (if available).

    Returns:
        SyncAccountsResponse with per-account action results.
    """
    if identity is None:
        from src.core.transport_helpers import resolve_identity_from_context

        identity = resolve_identity_from_context(ctx, require_valid_token=True)
    return await _sync_accounts_impl(req, identity)
