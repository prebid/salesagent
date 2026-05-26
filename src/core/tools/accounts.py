"""Account tool implementations (list + sync).

Handles account management per AdCP spec (UC-011):
- Agent-scoped results (BR-RULE-054)
- Authenticated list — INV-3 of BR-RULE-055: unauthenticated callers raise
  AUTH_TOKEN_INVALID (consistent with sync_accounts; see _list_accounts_impl)
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

from adcp.types import PaginationRequest, PaginationResponse

from src.core.audit_logger import get_audit_logger
from src.core.database.models import Account as DBAccount
from src.core.database.repositories.push_notification import (
    PushNotificationConfigRepository,
    PushNotificationConfigSnapshot,
)
from src.core.database.repositories.uow import AccountUoW
from src.core.exceptions import AdCPAuthenticationError, AdCPValidationError
from src.core.resolved_identity import ResolvedIdentity
from src.core.schemas.account import (
    Account,
    ListAccountsRequest,
    ListAccountsResponse,
    SyncAccountsRequest,
    SyncAccountsResponse,
    SyncResponseAccount,
)
from src.core.tracing import traced
from src.services.protocol_change_webhooks import notify_account_status_changed_async
from src.services.push_notification_registration import (
    normalize_push_notification_config,
    register_account_notification_configs_in_repo,
    register_push_notification_config_in_repo,
)

logger = logging.getLogger(__name__)


# Internal status values that the wire spec doesn't model. Mapped to spec
# enums at every wire-emit boundary so SyncResponseAccount / Account
# Pydantic validation accepts them. Internal logic (routing in
# media_buy_create, provisioning in account_provisioning) still reads the
# raw DB column to distinguish the two.
#
# #332: ``pending_provision`` collapses to ``pending_approval`` — both mean
# "buyer can't use this account yet, operator-side work pending"; the AdCP
# Account.status enum doesn't distinguish "waiting for human approval"
# from "waiting for technical provisioning" at the buyer-visible level.
_INTERNAL_TO_WIRE_STATUS = {
    "pending_provision": "pending_approval",
}


def _wire_status(status: str | None) -> str | None:
    """Translate internal lifecycle status to a spec-valid AccountStatus.

    Spec enum (``adcp.types.AccountStatus``):
        active, pending_approval, rejected, payment_required, suspended, closed

    Anything not in the translation map passes through unchanged — only
    internal-only states need rewriting.
    """
    if status is None:
        return None
    return _INTERNAL_TO_WIRE_STATUS.get(status, status)


def _db_account_to_schema(db_account: DBAccount, notification_configs: list[dict[str, Any]] | None = None) -> Account:
    """Convert ORM Account to Pydantic schema Account."""
    return Account(
        account_id=db_account.account_id,
        name=db_account.name,
        status=_wire_status(db_account.status),
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
        notification_configs=notification_configs,
        ext=db_account.ext,
    )


def _notification_configs_to_wire(snapshots: list[PushNotificationConfigSnapshot]) -> list[dict[str, Any]] | None:
    configs = [
        {
            "subscriber_id": snapshot.subscriber_id or snapshot.principal_id,
            "url": snapshot.url,
            "event_types": snapshot.event_types or [],
            "active": snapshot.is_active,
        }
        for snapshot in snapshots
        if snapshot.account_id is not None and snapshot.event_types
    ]
    return sorted(configs, key=lambda config: config["subscriber_id"]) or None


def _account_notification_configs_for_response(
    repo: PushNotificationConfigRepository,
    *,
    principal_id: str,
    account_id: str,
) -> list[dict[str, Any]] | None:
    return _notification_configs_to_wire(
        repo.list_current_snapshots(
            principal_id=principal_id,
            purpose="catalog_changes",
            account_id=account_id,
        )
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
    )


@traced
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

        assert uow.push_notifications is not None
        # Convert ORM models to schema models while session is alive
        schema_accounts = [
            _db_account_to_schema(
                account,
                _account_notification_configs_for_response(
                    uow.push_notifications,
                    principal_id=principal_id,
                    account_id=account.account_id,
                ),
            )
            for account in db_accounts
        ]

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
    """Convert GovernanceAgent models to JSON-serializable dicts for DB storage.

    Both dict and model inputs are normalized through model_dump(mode="json")
    to ensure consistent comparison (e.g., AnyUrl → str).
    """
    from adcp.types.generated_poc.core.account import GovernanceAgent

    if agents is None:
        return None
    result: list[dict[str, Any]] = []
    for g in agents:
        if isinstance(g, dict):
            # Validate through model to normalize types (AnyUrl → str, etc.)
            result.append(GovernanceAgent.model_validate(g).model_dump(mode="json"))
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
    incoming_gov = _serialize_governance_agents(getattr(entry, "governance_agents", None))
    db_gov = _serialize_governance_agents(db_account.governance_agents)
    if db_gov != incoming_gov:
        changes["governance_agents"] = incoming_gov

    return changes


def _sync_existing_action(changes: dict[str, Any], access_changed: bool) -> str:
    """Return the sync action for an existing account upsert."""
    return "updated" if changes or access_changed else "unchanged"


def _build_sync_result(
    *,
    brand: Any,
    operator: str,
    action: str,
    status: str,
    account_id: str | None = None,
    name: str | None = None,
    billing: str | None = None,
    sandbox: bool | None = None,
    errors: list[Any] | None = None,
    setup: Any | None = None,
    notification_configs: list[dict[str, Any]] | None = None,
) -> SyncResponseAccount:
    """Build an AdCP sync response Account object.

    Per ``sync-accounts-response.json`` (3.0.6+), ``account_id`` is
    optional on each entry — it's omitted on rejected / failed entries
    where no account was provisioned. Library's typed ``Account`` model
    declares ``account_id: str | None = None`` matching the spec; the
    wire-side ``exclude_none=True`` projection drops the field cleanly
    when callers pass ``None``. Don't substitute a sentinel string
    (e.g. ``"unassigned"``) — buyers may roundtrip it as a real
    seller-assigned ID into ``create_media_buy``.
    """
    return SyncResponseAccount(
        account_id=account_id,
        brand=brand,
        operator=operator,
        action=action,
        status=_wire_status(status),
        name=name,
        billing=billing,
        sandbox=sandbox,
        errors=errors,
        setup=setup,
        notification_configs=notification_configs,
    )


def _build_setup_for_approval(mode: str, tenant_id: str) -> Any:
    """Build a Setup object based on the approval mode.

    Returns Setup for pending_approval modes, None for auto-approve.
    """
    from datetime import datetime, timedelta

    from adcp.types import Setup

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


def _read_principal_billing_enabled_sync(tenant_id: str, principal_id: str) -> bool:
    """Read ``principals.billing_enabled`` once per sync_accounts call.

    Pulled out of the per-entry hot path so a 1000-account sync doesn't
    open 1000 sessions, and so a concurrent operator flip can only land
    before or after the entire batch — not mid-batch (BR-RULE-061
    consistency). ``None`` (principal vanished post-auth) is treated as
    disabled — fail closed.
    """
    from sqlalchemy import select

    from src.core.database.database_session import get_db_session
    from src.core.database.models import Principal

    with get_db_session() as session:
        value = session.scalars(
            select(Principal.billing_enabled).filter_by(
                tenant_id=tenant_id,
                principal_id=principal_id,
            )
        ).first()
    return bool(value)


def _check_billing_policy(
    billing_val: str | None,
    identity: ResolvedIdentity,
    *,
    principal_billing_enabled: bool,
) -> list[Any] | None:
    """Check if the billing model is supported by the seller AND the calling
    principal is allowed to be billed under that model.

    Two gates:

    * **BR-RULE-059** (tenant-level) — billing must be in the tenant's
      ``supported_billing`` list. Reject with ``BILLING_NOT_SUPPORTED``.
    * **BR-RULE-061** (principal-level, slice 4) — when ``billing="agent"``,
      the calling principal must have ``billing_enabled=True``. Operators
      mark internal/free-tier/test agents as ``billing_enabled=False`` so
      they can't be set as the billing party for any Account.
      Reject with ``BILLING_NOT_PERMITTED_FOR_AGENT`` (recovery="correctable":
      buyer can retry with ``billing="operator"``).

    ``principal_billing_enabled`` is read once at the top of
    ``_sync_accounts_impl`` and passed in explicitly; we don't re-read per
    entry to avoid (a) write race against operator flip mid-batch and (b)
    N+1 sessions on a large batch. No default — every caller must reason
    about which value to pass so a future call site can't silently get the
    permissive path.

    Returns a list of Error objects if rejected, None if accepted.
    """
    from adcp.types import Error

    # Read billing policy from tenant configuration (not identity).
    # Both dict and TenantContext expose .get() identically, so no branching needed.
    tenant = identity.tenant if identity else None
    supported = tenant.get("supported_billing") if tenant else None
    if supported is not None and billing_val not in supported:
        return [
            Error(
                code="BILLING_NOT_SUPPORTED",
                message=f"Billing model '{billing_val}' is not supported by this seller. "
                f"Supported models: {', '.join(supported)}.",
                suggestion=f"Use one of the supported billing models: {', '.join(supported)}.",
            )
        ]

    # Per-principal billing-capability gate (BR-RULE-061).
    if billing_val == "agent" and not principal_billing_enabled:
        return [
            Error(
                code="BILLING_NOT_PERMITTED_FOR_AGENT",
                message="This buyer agent is not permitted to be the billing party on this seller.",
                suggestion="Use billing='operator'.",
                recovery="correctable",
                field="billing",
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


@traced
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
        # idempotency_key is library-required (adcp 4.4) but the no-op empty
        # request handled below doesn't actually round-trip the field.
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

    # Read the principal's billing_enabled flag once for the whole batch
    # (BR-RULE-061). Holding the value constant for the duration of the
    # sync prevents an operator flip mid-batch from producing a partial
    # result where some entries land billable and others reject. Auth
    # guard above already proved tenant_id + principal_id are non-None.
    principal_billing_enabled = _read_principal_billing_enabled_sync(tenant_id, principal_id)

    results: list[SyncResponseAccount] = []
    # Track natural keys in the payload for delete_missing
    seen_account_ids: set[str] = set()
    status_changes: list[tuple[str, str, str]] = []
    webhook_registration = normalize_push_notification_config(req.push_notification_config)

    with AccountUoW(tenant_id) as uow:
        assert uow.accounts is not None
        assert uow.push_notifications is not None
        repo = uow.accounts

        if webhook_registration is not None and not dry_run:
            register_push_notification_config_in_repo(
                uow.push_notifications,
                principal_id=principal_id,
                registration=webhook_registration,
            )

        for entry in req.accounts:
            brand_domain, brand_id, operator, sandbox = _extract_natural_key(entry)
            billing_val = _enum_to_str(entry.billing)

            # BR-RULE-059 + BR-RULE-061: check tenant + per-principal billing
            billing_errors = _check_billing_policy(
                billing_val,
                identity,
                principal_billing_enabled=principal_billing_enabled,
            )
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

            # Look up existing account by natural key.
            #
            # For billing=agent the natural key includes the calling
            # principal — different buyer agents on the same (operator,
            # brand) pair produce distinct Accounts (different commercial
            # relationships, different rate cards, separate GAM
            # advertisers). See docs/design/sync-accounts-advertiser-
            # mapping.md § Granularity decision.
            agent_scoped = billing_val == "agent"
            existing = repo.get_by_natural_key(
                operator=operator,
                brand_domain=brand_domain,
                brand_id=brand_id,
                sandbox=sandbox,
                billing=billing_val if agent_scoped else None,
                principal_id=principal_id if agent_scoped else None,
            )

            if existing is not None:
                seen_account_ids.add(existing.account_id)
                account_notification_configs = getattr(entry, "notification_configs", None)

                if dry_run:
                    # Check if fields would change
                    changes = _account_fields_changed(existing, entry)
                    access_would_change = not repo.has_access(principal_id, existing.account_id)
                    action = _sync_existing_action(changes, access_would_change)
                    results.append(
                        _build_sync_result(
                            account_id=existing.account_id,
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
                access_granted = repo.ensure_access(principal_id, existing.account_id)
                if changes:
                    repo.update_fields(existing.account_id, **changes)
                action = _sync_existing_action(changes, access_granted)

                if account_notification_configs is not None:
                    register_account_notification_configs_in_repo(
                        uow.push_notifications,
                        principal_id=principal_id,
                        account_id=existing.account_id,
                        configs=account_notification_configs,
                    )
                results.append(
                    _build_sync_result(
                        account_id=existing.account_id,
                        brand=entry.brand,
                        operator=operator,
                        action=action,
                        status=existing.status,
                        name=existing.name,
                        billing=existing.billing,
                        sandbox=existing.sandbox,
                        notification_configs=_account_notification_configs_for_response(
                            uow.push_notifications,
                            principal_id=principal_id,
                            account_id=existing.account_id,
                        ),
                    )
                )
            else:
                # Create new account
                billing_val = _enum_to_str(entry.billing)
                payment_terms_val = _enum_to_str(entry.payment_terms)
                governance_agents_val = _serialize_governance_agents(getattr(entry, "governance_agents", None))

                account_id = _generate_account_id()
                account_name = _generate_account_name(brand_domain, operator, brand_id)

                # BR-RULE-060: determine approval status from tenant config.
                # account_approval_mode is a distinct field from creative approval_mode
                # (BR-RULE-037) — do NOT fall back to approval_mode.
                # Resolved BEFORE the dry_run branch so previews reflect what a real
                # create would return (BR-RULE-062).
                tenant = identity.tenant if identity else None
                approval_mode = tenant.get("account_approval_mode") if tenant else None
                setup = _build_setup_for_approval(approval_mode or "auto", tenant_id)
                # Status precedence (sprint 1.6 § Lifecycle):
                #   pending_approval — manual approval gate (BR-RULE-060)
                #   pending_provision — auto-approved + GAM tenant + not sandbox + no
                #                       pre-mapped advertiser → wait for provision-on-
                #                       first-buy or manual mapping via Tenant Mgmt API
                #   active — sandbox accounts (sandbox advertiser wired at first-buy),
                #            non-GAM tenants (mock adapter, no provisioning concept),
                #            and rows the management API pre-creates with platform_mappings
                tenant_ad_server = (tenant or {}).get("ad_server")
                needs_provision = setup is None and tenant_ad_server == "google_ad_manager" and not sandbox
                if setup:
                    initial_status = "pending_approval"
                elif needs_provision:
                    initial_status = "pending_provision"
                else:
                    initial_status = "active"

                if dry_run:
                    results.append(
                        _build_sync_result(
                            account_id=account_id,
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
                    continue

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
                account_notification_configs = getattr(entry, "notification_configs", None)
                if account_notification_configs is not None:
                    register_account_notification_configs_in_repo(
                        uow.push_notifications,
                        principal_id=principal_id,
                        account_id=account_id,
                        configs=account_notification_configs,
                    )

                results.append(
                    _build_sync_result(
                        account_id=account_id,
                        brand=entry.brand,
                        operator=operator,
                        action="created",
                        status=initial_status,
                        name=account_name,
                        billing=billing_val,
                        sandbox=sandbox,
                        setup=setup,
                        notification_configs=_account_notification_configs_for_response(
                            uow.push_notifications,
                            principal_id=principal_id,
                            account_id=account_id,
                        ),
                    )
                )

        # BR-RULE-061: delete_missing — close accounts not in payload
        if delete_missing and not dry_run:
            agent_accounts = repo.list_by_principal(principal_id)
            for db_acct in agent_accounts:
                if db_acct.account_id not in seen_account_ids:
                    old_status = db_acct.status
                    repo.update_status(db_acct.account_id, "closed")
                    status_changes.append((db_acct.account_id, old_status, "closed"))
                    results.append(
                        _build_sync_result(
                            account_id=db_acct.account_id,
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

    for account_id, from_status, to_status in status_changes:
        await notify_account_status_changed_async(
            tenant_id=tenant_id,
            account_id=account_id,
            from_status=from_status,
            to_status=to_status,
            principal_id=principal_id,
        )

    return SyncAccountsResponse(
        accounts=results,
        dry_run=dry_run if dry_run else None,
        context=req.context,
    )


# ---------------------------------------------------------------------------
# sync_accounts MCP wrapper
# ---------------------------------------------------------------------------
