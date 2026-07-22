"""sync_governance tool implementation (UC-030, #1329 gap 14 dependency).

Binds a buyer-designated governance agent per account per AdCP 3.1.1
(account/sync-governance-request.json + accounts/tasks/sync_governance.mdx).

This is the SELLER side of the governance handshake: a buyer registers exactly
one governance agent per account, and the seller persists that binding
(replace semantics) so a governance-aware seller could later call it via
check_governance during the media-buy lifecycle. This sales agent registers the
binding but does NOT enforce it downstream — it deliberately does not declare
the ``governance-aware-seller`` specialism. Registering the binding is what the
``sales-non-guaranteed`` / ``sales-guaranteed`` specialism storyboards grade
(``accounts[0].status == "synced"`` + schema + context echo); enforcement
(check_governance) is a separate, un-declared capability.

Spec grounding (pinned AdCP 3.1.1 / adcp 6.6.0):
- Request: ``idempotency_key`` (required, ^[A-Za-z0-9_.:-]{16,255}$), ``accounts[]``
  (1..100), each ``{account: AccountReference, governance_agents[maxItems:1]}``,
  agent ``{url ^https://, authentication{schemes, credentials minLength:32}}``.
- Response: success variant carries ``accounts[]`` with per-item
  ``status in {synced, failed}``; synced echoes ``governance_agents[].url`` but
  NEVER credentials; envelope ``status: completed`` for the synchronous path.
- Normative MUST (sync_governance.mdx): "the seller MUST verify that the
  authenticated agent has authority over each referenced account before
  persisting." Unknown/unresolvable accounts return per-account ``status: failed``
  with ``ACCOUNT_NOT_FOUND``; an existing account the agent lacks authority over
  returns ``SCOPE_INSUFFICIENT`` (standard code from the pinned error-code enum
  that the graded BR-UC-030 storyboard checks; the .mdx prose's non-standard
  ``UNAUTHORIZED`` is flagged upstream — see ``_sync_one_account``).

Credentials are never persisted: the ``accounts.governance_agents`` column model
(core/account.json GovernanceAgent) is url-only by construction, so the binding
stores the durable agent identity (url) and nothing sensitive.

Idempotency replay + IDEMPOTENCY_CONFLICT (same key / different payload) are
graded only by the richer UC-030 BDD ledger (deferred follow-up), not by the
pinned 3.1.1 storyboards; because sync is a side-effect-free replace, persisting
without replay is already resource-idempotent. Mirrors the sync_accounts
precedent (accepts idempotency_key, no replay dedup yet).
"""

import logging

from adcp.types import AccountReference as LibraryAccountReference
from adcp.types import ContextObject, Error
from adcp.types.aliases import SyncGovernanceAccount as SyncGovernanceAccountInput
from fastmcp.server.context import Context
from fastmcp.tools.tool import ToolResult

from src.core.audit_logger import get_audit_logger
from src.core.auth import require_identity, require_principal_id, require_tenant
from src.core.database.repositories.account import AccountRepository
from src.core.database.repositories.uow import AccountUoW
from src.core.exceptions import (
    AdCPAccountAmbiguousError,
    AdCPAccountNotFoundError,
    AdCPAuthorizationError,
    AdCPError,
    AdCPValidationError,
)
from src.core.helpers.account_helpers import resolve_account, serialize_governance_agents
from src.core.resolved_identity import ResolvedIdentity
from src.core.schemas.account import (
    SyncedGovernanceAgent,
    SyncGovernanceRequest,
    SyncGovernanceResponse,
    SyncGovernanceResponseAccount,
)
from src.core.tool_context import ToolContext
from src.core.transport_helpers import resolve_identity_from_context
from src.core.validation_helpers import adcp_validation_boundary

logger = logging.getLogger(__name__)


def _failed_account_result(
    account_ref: LibraryAccountReference, code: str, exc: AdCPError
) -> SyncGovernanceResponseAccount:
    """Build a per-account ``failed`` result carrying a single per-account error.

    ``code`` is the spec-facing per-account error code (ACCOUNT_NOT_FOUND /
    SCOPE_INSUFFICIENT), set explicitly rather than read from the exception's wire
    code — AdCPAuthorizationError translates to AUTH_REQUIRED on the wire, but the
    per-account authority failure uses the standard ``SCOPE_INSUFFICIENT`` code
    (see ``_sync_one_account`` for the spec grounding).
    """
    return SyncGovernanceResponseAccount(
        account=account_ref,
        status="failed",
        errors=[
            Error(  # structural-guard: advisory per-account result in SyncGovernanceResponse.accounts[].errors[]
                code=code, message=str(exc), suggestion=getattr(exc, "suggestion", None)
            )
        ],
    )


def _sync_one_account(
    entry: SyncGovernanceAccountInput, identity: ResolvedIdentity, repo: AccountRepository
) -> SyncGovernanceResponseAccount:
    """Sync a single account's governance binding (authority check → persist → echo).

    Per-account failures (unknown/unowned account) are returned as ``failed``
    results, NOT raised — the overall response stays the success variant with a
    mix of synced/failed entries (partial-failure model).

    Per-account error codes are the standard AdCP vocabulary
    (``enums/error-code.json``, pinned 3.1.1) that the graded BR-UC-030 storyboard
    grades against: ``ACCOUNT_NOT_FOUND`` for an unknown/uniquely-unresolvable
    account (matches the sync-governance-response.json partial-failure example),
    ``SCOPE_INSUFFICIENT`` for an existing account the agent has no authority over.
    NOTE: the sync_governance.mdx prose table names a non-standard ``UNAUTHORIZED``
    here, which is absent from the pinned error-code enum and diverges from the
    graded storyboard — flagged upstream for reconciliation; we emit the standard,
    graded code.
    """
    try:
        account_id = resolve_account(entry.account, identity, repo)
    except AdCPAccountNotFoundError as e:
        return _failed_account_result(entry.account, "ACCOUNT_NOT_FOUND", e)
    except AdCPAuthorizationError as e:
        return _failed_account_result(entry.account, "SCOPE_INSUFFICIENT", e)
    except AdCPAccountAmbiguousError as e:
        # A natural key matching multiple accounts cannot be resolved to a single
        # binding target — surface as not-found (the account was not uniquely found).
        return _failed_account_result(entry.account, "ACCOUNT_NOT_FOUND", e)
    except AdCPError as e:
        # Account-status blocks (setup/suspended/payment): honest per-account
        # failure carrying the resolver's own code rather than a silent success.
        return _failed_account_result(entry.account, e.error_code, e)

    # Project request agents to the url-only DB-column shape ONCE (credentials
    # never persisted; serialize_governance_agents structurally strips them), then
    # persist and echo from that single list so the two can never disagree.
    # update_fields overwrites the prior binding (per-account replace semantics).
    agent_urls = serialize_governance_agents(entry.governance_agents) or []
    repo.update_fields(account_id, governance_agents=agent_urls)

    return SyncGovernanceResponseAccount(
        account=entry.account,
        status="synced",
        governance_agents=[SyncedGovernanceAgent(url=agent["url"]) for agent in agent_urls],
    )


async def _sync_governance_impl(
    req: SyncGovernanceRequest | None = None,
    identity: ResolvedIdentity | None = None,
) -> SyncGovernanceResponse:
    """Shared implementation for sync_governance.

    Args:
        req: Sync request with idempotency_key and per-account governance agents.
        identity: Resolved identity (must be authenticated).

    Returns:
        SyncGovernanceResponse with per-account synced/failed results.
    """
    if req is None:
        raise AdCPValidationError("sync_governance requires a request body with accounts and idempotency_key.")

    # Authentication is REQUIRED (write tool). require_principal_id first so the
    # canonical AUTH_REQUIRED message surfaces for a missing/anonymous token;
    # require_identity then narrows the type for the tenant lookup below.
    principal_id = require_principal_id(identity, context=req.context)
    identity = require_identity(identity, context=req.context)
    tenant = require_tenant(identity, context=req.context)
    tenant_id = tenant["tenant_id"]

    # A non-empty accounts array is guaranteed by the request schema (minItems: 1);
    # invalid requests are rejected at construction / the validation boundary.
    results: list[SyncGovernanceResponseAccount] = []
    with AccountUoW(tenant_id) as uow:
        assert uow.accounts is not None
        repo = uow.accounts
        for entry in req.accounts:
            results.append(_sync_one_account(entry, identity, repo))

    synced = sum(1 for r in results if r.status == "synced")
    audit_logger = get_audit_logger("sync_governance", tenant_id)
    audit_logger.log_info(f"sync_governance completed: {synced}/{len(results)} synced (principal={principal_id})")

    return SyncGovernanceResponse(accounts=results, context=req.context)


# ---------------------------------------------------------------------------
# MCP wrapper
# ---------------------------------------------------------------------------


async def sync_governance(
    idempotency_key: str | None = None,
    accounts: list[SyncGovernanceAccountInput] | None = None,
    context: ContextObject | None = None,
    ctx: Context | ToolContext | None = None,
) -> ToolResult:
    """Bind a governance agent per account (MCP tool).

    MCP wrapper that accepts individual parameters per AdCP spec and constructs a
    SyncGovernanceRequest for the shared implementation. ``idempotency_key`` is
    spec-required, but it is typed ``str | None`` here (not ``str``) so a missing
    key surfaces as an AdCP validation error at model construction — the same wire
    shape REST/A2A produce — rather than being rejected earlier by FastMCP's own
    parameter-schema layer with a different, non-AdCP error shape. The schema's
    required ``idempotency_key`` still rejects ``None`` (UC-030 grades that).

    Args:
        idempotency_key: Client-generated at-most-once key (spec-required; a
            missing key is rejected at request construction).
        accounts: Per-account governance agent bindings.
        context: Application-level context per AdCP spec (echoed back).
        ctx: FastMCP context for authentication.

    Returns:
        ToolResult with human-readable text and structured data.
    """
    # Wrap construction so a schema violation (missing/short idempotency_key,
    # non-https url, short credentials, agent cardinality) surfaces as the AdCP
    # VALIDATION_ERROR envelope — the same wire shape REST produces via its own
    # boundary — instead of a raw pydantic error or FastMCP's parameter-schema
    # rejection (UC-030 grades these on the wire).
    with adcp_validation_boundary(context="sync_governance request"):
        req = SyncGovernanceRequest(
            idempotency_key=idempotency_key,
            accounts=accounts or [],
            context=context,
        )
    identity = (await ctx.get_state("identity")) if isinstance(ctx, Context) else None
    response = await _sync_governance_impl(req, identity)
    return ToolResult(content=str(response), structured_content=response)


# ---------------------------------------------------------------------------
# A2A / REST raw wrapper
# ---------------------------------------------------------------------------


async def sync_governance_raw(
    req: SyncGovernanceRequest | None = None,
    ctx: Context | ToolContext | None = None,
    identity: ResolvedIdentity | None = None,
) -> SyncGovernanceResponse:
    """Bind a governance agent per account (raw function for A2A/REST).

    Args:
        req: Sync request with per-account governance agents.
        ctx: FastMCP context.
        identity: Pre-resolved identity (if available).

    Returns:
        SyncGovernanceResponse with per-account results.
    """
    if identity is None:
        identity = resolve_identity_from_context(ctx, require_valid_token=True)
    return await _sync_governance_impl(req, identity)
