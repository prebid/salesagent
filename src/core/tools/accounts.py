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
import time
import uuid
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC
from typing import TYPE_CHECKING, Annotated, cast

from adcp.types import AccountReference as LibraryAccountReference
from adcp.types import BrandReference as LibraryBrandReference
from adcp.types import ContextObject, NotificationConfig, PaginationRequest, PaginationResponse
from adcp.types.generated_poc.account.list_accounts_request import (
    Status as AccountStatus,
)
from adcp.types.generated_poc.account.sync_accounts_request import (
    Accounts as SyncAccountInput,  # SDK 5.7: Account → Accounts
)
from adcp.types.generated_poc.account.sync_accounts_request import (
    Accounts1 as SettingsUpdateAccountInput,  # the account-reference / settings-update arm
)
from adcp.types.generated_poc.core.account_ref import AccountReference1, AccountReference2
from adcp.types.generated_poc.core.business_entity import BusinessEntity
from fastmcp.server.context import Context
from fastmcp.tools.tool import ToolResult
from pydantic import BaseModel, Field

from src.core.audit_logger import get_audit_logger
from src.core.auth import require_identity, require_principal_id, require_tenant
from src.core.database.models import Account as DBAccount
from src.core.database.repositories.account import AccountRepository, NaturalKeyConflict
from src.core.database.repositories.account_serialization import (
    serialize_business_entity,
    serialize_governance_agents,
    serialize_notification_configs,
)
from src.core.database.repositories.uow import AccountUoW
from src.core.exceptions import AdCPValidationError
from src.core.helpers import enum_value
from src.core.helpers.brand_key import brand_key_parts
from src.core.resolved_identity import ResolvedIdentity
from src.core.schemas.account import (
    Account,
    ListAccountsRequest,
    ListAccountsResponse,
    SyncAccountsRequest,
    SyncAccountsResponse,
    SyncResponseAccount,
)
from src.core.tool_context import ToolContext
from src.core.tools._mcp_boundary import build_tool_result
from src.core.transport_helpers import NOT_PROVIDED, IdentityOrNotProvided, resolve_identity_if_not_provided
from src.core.validation_helpers import adcp_validation_boundary
from src.services.notification_proof_service import NotificationProofService, get_notification_proof_service

if TYPE_CHECKING:
    from adcp.types import Error, Setup

logger = logging.getLogger(__name__)

#: Either sync_accounts entry shape: the provisioning trio (brand/operator/
#: billing) or the account-reference settings-update arm. Typed here (not
#: Any) so a resolver written for one arm cannot silently accept the other's
#: entry -- exactly the class of bug _FIELD_POLICY exists to prevent.
SyncEntry = SyncAccountInput | SettingsUpdateAccountInput

#: Either account-reference shape a settings-update entry's ``account`` field
#: carries: the seller-assigned handle (AccountReference1) or the natural key
#: (AccountReference2).
AccountRef = AccountReference1 | AccountReference2


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
        # Same scrub as the sync echo: list_accounts must not reflect write-only
        # credentials either, and the read-back leg of the register scenario goes
        # through here.
        notification_configs=_scrub_notification_credentials(db_account.notification_configs),
        # Same scrub rationale as notification_configs: `bank` is write-only, and
        # list_accounts is an echo path too.
        billing_entity=_scrub_business_entity(db_account.billing_entity),
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


def _matches_account_ref(db_account: DBAccount, ref: AccountRef) -> bool:
    """Whether *db_account* matches an AccountReference (account_id XOR natural key).

    AccountReference1 carries account_id; AccountReference2 carries the natural
    key (brand + operator, optionally sandbox) -- mirrors the RootModel
    discrimination in _process_settings_update_entry (salesagent-5g8e).
    """
    if isinstance(ref, AccountReference1):
        return bool(db_account.account_id == ref.account_id)
    brand_domain = ref.brand.domain if ref.brand else None
    if db_account.operator != ref.operator:
        return False
    # brand is Mapped[BrandReference | None] (JSONType(model=BrandReference),
    # models.py:828) -- hydrated as the typed model, not a plain dict.
    domain = db_account.brand.domain if db_account.brand else None
    if domain != brand_domain:
        return False
    if ref.sandbox is not None and db_account.sandbox != ref.sandbox:
        return False
    return True


def _apply_list_account_filters(db_accounts: list[DBAccount], req: ListAccountsRequest) -> list[DBAccount]:
    """Apply every list_accounts predicate filter in one place (DRY --
    salesagent-tm97 disease scan; status/sandbox/account were 3 near-identical
    inline list-comprehension filters before this extraction).
    """
    status_filter = getattr(req, "status", None)
    if status_filter is not None:
        status_str = enum_value(status_filter)
        db_accounts = [a for a in db_accounts if a.status == status_str]

    sandbox_filter = getattr(req, "sandbox", None)
    if sandbox_filter is not None:
        db_accounts = [a for a in db_accounts if a.sandbox == sandbox_filter]

    account_filter = getattr(req, "account", None)
    if account_filter is not None:
        # account_filter is always AccountReference (a RootModel) when present.
        db_accounts = [a for a in db_accounts if _matches_account_ref(a, account_filter.root)]

    return db_accounts


def _list_accounts_impl(
    req: ListAccountsRequest | None = None,
    identity: ResolvedIdentity | None = None,
) -> ListAccountsResponse:
    """List accounts accessible to the authenticated agent.

    Per BR-RULE-055: requires authentication, raises AUTH_MISSING if missing.
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
    principal_id = require_principal_id(identity, context=req.context)
    tenant = require_tenant(identity, context=req.context)
    tenant_id = tenant["tenant_id"]

    with AccountUoW(tenant_id) as uow:
        assert uow.accounts is not None
        # BR-RULE-054: agent-scoped results
        db_accounts = uow.accounts.list_for_agent(principal_id)
        db_accounts = _apply_list_account_filters(db_accounts, req)

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
# Shared request builder
# ---------------------------------------------------------------------------


def build_list_accounts_request(
    *,
    account: LibraryAccountReference | None = None,
    status: AccountStatus | None = None,
    pagination: PaginationRequest | None = None,
    sandbox: bool | None = None,
    idempotency_key: str | None = None,
    ext: dict | None = None,
    context: ContextObject | None = None,
    adcp_version: str | None = None,
    adcp_major_version: int | None = None,
) -> ListAccountsRequest:
    """Build the shared list_accounts request for transport wrappers.

    Mirrors build_get_adcp_capabilities_request (capabilities.py:160) -- the single
    seam every transport constructs the typed request through, so a future request
    field lands here once instead of in wrapper lockstep.

    ``idempotency_key`` is threaded verbatim and never generated: on a read tool it is
    tolerance (list-accounts-request.json 3.1.1 declares no such property and
    ``additionalProperties: true``), so the only correct handling is to accept whatever
    the buyer sent and let it have no effect.
    """
    return ListAccountsRequest(
        account=account,
        status=status,
        pagination=pagination,
        sandbox=sandbox,
        idempotency_key=idempotency_key,
        ext=ext,
        context=context,
        adcp_version=adcp_version,
        adcp_major_version=adcp_major_version,
    )


# ---------------------------------------------------------------------------
# MCP wrapper
# ---------------------------------------------------------------------------


async def list_accounts(
    account: LibraryAccountReference | None = None,
    status: AccountStatus | None = None,
    pagination: PaginationRequest | None = None,
    sandbox: Annotated[bool | None, Field(description="When true, return only sandbox/test accounts")] = None,
    idempotency_key: Annotated[
        str | None, Field(description="Read-tool idempotency tolerance per v3.1.1 -- accepted, has no effect")
    ] = None,
    ext: Annotated[dict | None, Field(description="AdCP extension object -- accepted, has no effect")] = None,
    context: ContextObject | None = None,
    ctx: Context | ToolContext | None = None,
) -> ToolResult:
    """List accounts accessible to the authenticated agent (MCP tool).

    MCP wrapper that delegates to the shared implementation.
    FastMCP automatically validates and coerces JSON inputs to Pydantic models.

    Args:
        account: Exact account filter (account_id, or natural key brand+operator[+sandbox]).
        status: Filter accounts by status (active, closed, etc.).
        pagination: Pagination parameters (max_results, cursor).
        sandbox: Filter by sandbox flag.
        idempotency_key: Read-tool idempotency tolerance (accepted, no effect on a read).
        ext: AdCP extension object (accepted, no effect).
        context: Application-level context per AdCP spec.
        ctx: FastMCP context for authentication.

    Returns:
        ToolResult with human-readable text and structured data.
    """
    with adcp_validation_boundary(context="list_accounts request"):
        req = build_list_accounts_request(
            account=account,
            status=status,
            pagination=pagination,
            sandbox=sandbox,
            idempotency_key=idempotency_key,
            ext=ext,
            context=context,
        )

    identity = (await ctx.get_state("identity")) if isinstance(ctx, Context) else None
    response = _list_accounts_impl(req, identity)

    return build_tool_result(str(response), response)


# ---------------------------------------------------------------------------
# A2A raw wrapper
# ---------------------------------------------------------------------------


def list_accounts_raw(
    req: ListAccountsRequest | None = None,
    ctx: Context | ToolContext | None = None,
    identity: IdentityOrNotProvided = NOT_PROVIDED,
) -> ListAccountsResponse:
    """List accounts accessible to the authenticated agent (raw function for A2A).

    Args:
        req: Optional request with filter parameters.
        ctx: FastMCP context.
        identity: Pre-resolved identity (if available).

    Returns:
        ListAccountsResponse with accessible accounts.
    """
    identity = resolve_identity_if_not_provided(identity, ctx, require_valid_token=False)
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


def _enum_to_str(val: object) -> str | None:
    """Extract string value from an enum or return as-is. Returns None for None."""
    return enum_value(val)


def _scrub_notification_credentials(
    configs: Iterable[BaseModel | Mapping[str, object]] | None,
) -> list[NotificationConfig] | None:
    """Strip write-only ``authentication.credentials`` from an echoed subscriber set.

    ``credentials`` is ``minLength: 32`` and documented write-only: the seller
    stores it to authenticate its own outbound calls and MUST NOT reflect it.
    Called from ``_build_sync_result`` and ``_db_account_to_schema`` — the two
    places a persisted config becomes a response object — rather than at each
    call site, so a future echo path cannot forget it.

    Returns ``None`` for ``None`` and ``[]`` for ``[]``: "never configured" and
    "explicitly cleared" are different states to the buyer.
    """
    if configs is None:
        return None
    scrubbed: list[NotificationConfig] = []
    for config in configs:
        data = config.model_dump(mode="json") if hasattr(config, "model_dump") else dict(config)
        auth = data.get("authentication")
        if isinstance(auth, dict) and "credentials" in auth:
            auth = {k: v for k, v in auth.items() if k != "credentials"}
            data["authentication"] = auth
        scrubbed.append(NotificationConfig.model_validate(data))
    return scrubbed


def _scrub_business_entity(entity: BusinessEntity | Mapping[str, object] | None) -> BusinessEntity | None:
    """Strip write-only ``bank`` from an echoed ``billing_entity``.

    The response account item documents ``billing_entity`` as "echoed from the
    request ... **Bank details are omitted (write-only)**" (v3.1.1
    sync-accounts-response.json). Called from ``_build_sync_result`` and
    ``_db_account_to_schema`` — the two places a persisted entity becomes a
    response object — rather than at each call site, the same placement
    rationale as :func:`_scrub_notification_credentials`, so a future echo path
    cannot leak by forgetting a call.
    """
    from adcp.types.generated_poc.core.business_entity import BusinessEntity

    if entity is None:
        return None
    data = entity.model_dump(mode="json", exclude_none=True) if hasattr(entity, "model_dump") else dict(entity)
    data.pop("bank", None)
    return BusinessEntity.model_validate(data)


def _persisted_value(db_account: DBAccount, field: str) -> object:
    """The persisted value of ``field``, serialized to compare against a resolved one."""
    current = getattr(db_account, field, None)
    if field == "notification_configs":
        return serialize_notification_configs(current)
    if field == "governance_agents":
        return serialize_governance_agents(current)
    if field == "billing_entity":
        return serialize_business_entity(current)
    return current


def _resolve_notification_configs(
    entry: SyncEntry, persisted: list[dict[str, object]] | None
) -> tuple[bool, list[dict[str, object]] | None]:
    """Apply declarative-replace semantics for ``notification_configs``.

    Unlike its sibling resolvers, ``persisted`` is the field's ALREADY-SERIALIZED
    value (the caller wires it via ``serialize_notification_configs(getattr(
    existing, "notification_configs", None))``), not the whole ``DBAccount`` --
    the wiring lambda in ``_FIELD_POLICY`` does that adaptation.

    Returns ``(changed, value)``:
      - field omitted (``None``) -> ``(False, persisted)``: omission is NOT clearance
      - ``[]``                   -> ``(True, [])``: explicit clear, persisted as an
        empty array rather than NULL so the echo can carry it
      - non-empty                -> ``(True, <full array>)``: the submitted array
        REPLACES the persisted set wholesale; a re-sent ``subscriber_id`` replaces
        in place and paused entries survive only if re-included. Never merged.

    Note the ``is None`` test: ``[]`` is falsy, so a truthiness check here would
    silently turn "clear" into "leave unchanged".
    """
    submitted = getattr(entry, "notification_configs", None)
    if submitted is None:
        return False, persisted
    return True, serialize_notification_configs(submitted) or []


def _resolve_scalar(entry: SyncEntry, existing: DBAccount | None, field: str) -> tuple[bool, object]:
    """Omission-preserves resolver for a scalar enum/string field.

    ``None`` means "not submitted", not "clear it": the request schema gives the
    buyer no way to null a scalar, so an omitted field can only mean "leave it".
    This is the same semantic ``notification_configs`` documents, applied
    uniformly — a re-sync that mentions only ``payment_terms`` must not wipe
    every other field it stayed silent about.
    """
    incoming = _enum_to_str(getattr(entry, field, None))
    if incoming is None:
        return False, getattr(existing, field, None)
    return True, incoming


def _resolve_governance_agents(
    entry: SyncEntry, existing: DBAccount | None
) -> tuple[bool, list[dict[str, object]] | None]:
    """Omission-preserves resolver for ``governance_agents``.

    Deliberately the SAME semantic as ``_resolve_notification_configs`` rather
    than a second copy of it: before salesagent-gcze this field was compared
    ``serialize(incoming) != serialize(persisted)``, so a provisioning re-sync
    that merely OMITTED it produced ``changes["governance_agents"] = None`` and
    WIPED the binding. ``check_governance`` keys off that binding, which makes an
    omission-wipe a governance BYPASS — the buyer re-syncs ``payment_terms`` and
    silently loses the approval gate, with a success response.
    """
    submitted = getattr(entry, "governance_agents", None)
    if submitted is None:
        return False, serialize_governance_agents(getattr(existing, "governance_agents", None))
    return True, serialize_governance_agents(submitted)


def _resolve_billing_entity(entry: SyncEntry, existing: DBAccount | None) -> tuple[bool, dict[str, object] | None]:
    """Omission-preserves resolver for ``billing_entity`` (whole-object replace).

    "Permitted in both provisioning and settings-update modes — sellers MAY
    accept refinements in settings-update mode (e.g., updated bank details)"
    (v3.1.1 sync-accounts-request.json
    #/properties/accounts/items/properties/billing_entity/description), and the
    response item echoes it back with bank details stripped (write-only).
    """
    from adcp.types.generated_poc.core.business_entity import BusinessEntity

    submitted = getattr(entry, "billing_entity", None)
    if submitted is None:
        return False, serialize_business_entity(getattr(existing, "billing_entity", None))
    if isinstance(submitted, dict):
        submitted = BusinessEntity.model_validate(submitted)
    return True, serialize_business_entity(submitted)


def _resolve_sandbox(entry: SyncEntry, existing: DBAccount | None) -> tuple[bool, bool | None]:
    """``sandbox`` is applied at CREATE only — it is part of the natural key.

    On an existing account this resolver is inert BY COUPLING, not as a local
    property: both provisioning call sites reach here with
    ``existing = repo.get_by_natural_key(..., sandbox=_extract_natural_key(entry).sandbox)``,
    and ``get_by_natural_key`` filters exactly on it (``is not None`` -> equality;
    otherwise ``IS NULL OR = false``). All cases normalize equal under
    ``x or False``, so a matched account can never disagree with the submitted
    value. If that lookup ever stops filtering on sandbox (e.g. to detect
    ambiguous matches), this becomes a LIVE re-key and must be revisited here —
    the settings-update arm already rejects it for exactly that hazard.
    """
    if existing is not None:
        return False, existing.sandbox
    return True, getattr(entry, "sandbox", None)


class _Disposition:
    """What a sync_accounts entry field DOES in one entry mode.

    ``applied`` is the only disposition that needs no citation: it means the
    buyer's value reaches persistence. Every other value tells the buyer their
    field will not take effect, which under the project's no-quiet-failure rule
    is acceptable only as a DECLARED decision traceable to the pinned spec — so
    the citation is required by construction, not by review discipline.
    """

    __slots__ = ("kind", "citation")

    def __init__(self, kind: str, citation: str = "") -> None:
        self.kind = kind
        self.citation = citation

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return f"_Disposition({self.kind!r}, {self.citation!r})"


class _FieldPolicy:
    """One row of :data:`_FIELD_POLICY`: a disposition per mode + how to apply it."""

    __slots__ = ("provisioning", "settings_update", "resolve")

    def __init__(
        self,
        *,
        provisioning: _Disposition,
        settings_update: _Disposition,
        resolve: Callable[[SyncEntry, DBAccount | None], tuple[bool, object]] | None = None,
    ) -> None:
        self.provisioning = provisioning
        self.settings_update = settings_update
        self.resolve = resolve


_APPLIED = _Disposition("applied")

#: Why ``preferred_reporting_protocol`` is a DECLARED no-op rather than a
#: rejection. Named because both modes cite it identically.
_PREFERRED_PROTOCOL_CITATION = (
    "v3.1.1 sync-accounts-request.json"
    "#/properties/accounts/items/properties/preferred_reporting_protocol/description — hint language "
    "('if supported'; 'when omitted, the seller chooses from its supported offline_delivery_protocols'), "
    "not echoed by the response item, and the per-account errors array is 'only present when action is "
    "failed', so the protocol offers NO channel to advise on a successful account. Rejecting would fail a "
    "spec-legal request over an advisory hint. Non-support stays discoverable via get_adcp_capabilities "
    "(offline_delivery_protocols declared unbacked, #1291). FIXME(#1291): revisit when offline delivery lands."
)

#: THE record of what every ``sync_accounts`` entry field does, per entry mode.
#:
#: This table replaces two hand-maintained allowlists (``_KNOWN_ASYMMETRIC`` and
#: ``_KNOWN_DROPPED_BY_BOTH`` in the deleted handler-symmetry guard). Those
#: encoded "undecided", which is what let a field sit in debt indefinitely and
#: let ``billing``/``sandbox``/``governance_agents`` be honored on one arm and
#: silently discarded on the other. Here every in-scope field — declared on the
#: request arms OR arriving through ``extra="allow"`` — carries an explicit
#: disposition in BOTH modes, and ALL THREE application sites (create,
#: provisioning re-sync, settings-update) are driven by this one walk, so
#: divergence between them is not expressible.
#:
#: Guarded by tests/unit/test_architecture_sync_accounts_field_policy.py.
#: Which disposition each field deserves is graded behaviorally by the
#: entry-field-disposition scenarios in BR-UC-011 — a row is a claim about the
#: wire, and only a wire scenario can hold it to that.
_FIELD_POLICY: dict[str, _FieldPolicy] = {
    "payment_terms": _FieldPolicy(
        provisioning=_APPLIED,
        settings_update=_APPLIED,
        resolve=lambda entry, existing: _resolve_scalar(entry, existing, "payment_terms"),
    ),
    "notification_configs": _FieldPolicy(
        provisioning=_APPLIED,
        settings_update=_APPLIED,
        resolve=lambda entry, existing: _resolve_notification_configs(
            entry, serialize_notification_configs(getattr(existing, "notification_configs", None))
        ),
    ),
    "billing_entity": _FieldPolicy(
        provisioning=_APPLIED,
        settings_update=_APPLIED,
        resolve=_resolve_billing_entity,
    ),
    "billing": _FieldPolicy(
        provisioning=_APPLIED,
        settings_update=_Disposition(
            "spec_forbidden",
            "v3.1.1 sync-accounts-request.json"
            "#/properties/accounts/items/oneOf/1/allOf/2 (SettingsUpdateMode: not required billing)",
        ),
        resolve=lambda entry, existing: _resolve_scalar(entry, existing, "billing"),
    ),
    "sandbox": _FieldPolicy(
        provisioning=_APPLIED,
        settings_update=_Disposition(
            "rejected",
            "UNSUPPORTED_FEATURE, v3.1.1 core/account.json#/properties/sandbox "
            "(natural key: honoring it would re-key the account and orphan it from later syncs)",
        ),
        resolve=_resolve_sandbox,
    ),
    "governance_agents": _FieldPolicy(
        provisioning=_APPLIED,
        settings_update=_Disposition(
            "local_extension",
            "v3.1.1 sync-accounts-request.json#/properties/accounts/items/additionalProperties — "
            "not a sync_accounts property at all; the spec's governance surface is sync_governance "
            "(dist/compliance/3.1.1/domains/governance/index.yaml). Accepted on the provisioning arm "
            "only because that is how governed accounts are seeded today; retire with sync_governance.",
        ),
        resolve=_resolve_governance_agents,
    ),
    "preferred_reporting_protocol": _FieldPolicy(
        provisioning=_Disposition("ignored_by_design", _PREFERRED_PROTOCOL_CITATION),
        settings_update=_Disposition("ignored_by_design", _PREFERRED_PROTOCOL_CITATION),
    ),
}


def _disposition(field: str, mode: str) -> _Disposition:
    """The disposition ``field`` carries in ``mode`` (``provisioning``/``settings_update``)."""
    return getattr(_FIELD_POLICY[field], mode)


def _resolve_entry_changes(entry: SyncEntry, existing: DBAccount | None, *, mode: str) -> dict[str, object]:
    """The ONE field-application walk, shared by all three sites.

    ``existing=None`` IS the create case — a create is "resolve against nothing",
    which is how ``_resolve_notification_configs(entry, None)`` already worked
    before this table existed. Returns ``{column: value}`` for every field whose
    disposition in ``mode`` is ``applied`` and whose resolver reports a change,
    suitable both as ``repo.update_fields(**changes)`` and as ``DBAccount``
    kwargs.
    """
    changes: dict[str, object] = {}
    for field, policy in _FIELD_POLICY.items():
        if _disposition(field, mode).kind != "applied":
            continue
        if policy.resolve is None:  # pragma: no cover - no applied row lacks a resolver
            continue
        changed, value = policy.resolve(entry, existing)
        if changed:
            changes[field] = value
    return changes


def _rejected_field_errors(entry: SyncEntry, *, mode: str, index: int) -> list["Error"] | None:
    """Per-account errors for fields the table marks ``rejected`` in ``mode``.

    A ``rejected`` field is schema-LEGAL on this arm but cannot be honored, so
    the buyer must be TOLD rather than have it silently ignored (the project's
    no-quiet-failure rule). ``UNSUPPORTED_FEATURE`` over
    ``UNSUPPORTED_PROVISIONING``: the latter's enumMetadata suggestion is about
    entry SHAPE ("re-issue with the entry shape the seller supports"), and it
    already means "no account matches this reference" in this file; the former's
    is literally "check get_adcp_capabilities and remove unsupported fields",
    which is exactly the buyer action here.
    """
    from adcp.types import Error

    errors: list[Error] = []
    for field, policy in _FIELD_POLICY.items():
        if getattr(policy, mode).kind != "rejected":
            continue
        if getattr(entry, field, None) is None:
            continue
        errors.append(
            Error(  # structural-guard: advisory per-account result in SyncAccountsResponse.errors[]
                code="UNSUPPORTED_FEATURE",
                message=f"'{field}' cannot be changed on a settings-update entry: it is part of the "
                "account's natural key, so changing it would re-key the account and orphan it from "
                "subsequent syncs.",
                suggestion="Remove the field from the entry; provision a separate account if you need "
                "the other sandbox value.",
                field=f"accounts[{index}].{field}",
                recovery="correctable",
            )
        )
    return errors or None


def _account_fields_changed(db_account: DBAccount, entry: SyncEntry) -> dict[str, object]:
    """Fields a PROVISIONING re-sync changes on an existing account.

    Thin wrapper over the shared table walk, kept as a named function because the
    dry-run and write call sites both read better with it.
    """
    changes = _resolve_entry_changes(entry, db_account, mode="provisioning")
    # Only report fields whose resolved value actually differs from what is
    # persisted -- the resolvers answer "did the buyer submit this", and
    # "submitted the same value again" is not a change.
    return {field: value for field, value in changes.items() if _persisted_value(db_account, field) != value}


def _build_sync_result(
    *,
    brand: LibraryBrandReference | Mapping[str, object],
    operator: str,
    action: str,
    status: str,
    account_id: str | None = None,
    name: str | None = None,
    billing: str | None = None,
    payment_terms: str | None = None,
    sandbox: bool | None = None,
    errors: list["Error"] | None = None,
    setup: "Setup | None" = None,
    notification_configs: Iterable[BaseModel | Mapping[str, object]] | None = None,
    billing_entity: BusinessEntity | Mapping[str, object] | None = None,
) -> SyncResponseAccount:
    """Build an AdCP sync response Account object.

    The seller-assigned ``account_id`` MUST be echoed back for any non-failure
    action (created/updated/unchanged) so the buyer can reference the account
    in subsequent calls (BR-UC-011 POST-S5). Only ``failed`` results legitimately
    omit it because no account was provisioned.

    ``notification_configs`` is scrubbed of write-only credentials HERE rather
    than at the call sites: this builder exists so a shared shape can't drift
    across call sites, which makes it the one place a leak cannot be introduced
    by forgetting a call. ``billing_entity`` is scrubbed of write-only ``bank``
    for the same reason and in the same place.
    """
    return SyncResponseAccount(
        brand=brand,
        operator=operator,
        action=action,
        status=status,
        account_id=account_id,
        name=name,
        billing=billing,
        payment_terms=payment_terms,
        sandbox=sandbox,
        errors=errors,
        setup=setup,
        notification_configs=_scrub_notification_credentials(notification_configs),
        billing_entity=_scrub_business_entity(billing_entity),
    )


def _build_failed_result(
    *,
    brand: LibraryBrandReference | Mapping[str, object],
    operator: str,
    billing: str | None,
    sandbox: bool | None,
    errors: list["Error"],
) -> SyncResponseAccount:
    """Build a failed/rejected sync result -- the single source for every
    per-entry gate rejection (domain validity, billing policy, sandbox
    capability, settings-update-not-found), so a shared shape can't drift
    across call sites (salesagent-5g8e disease scan).

    The single choke point where every accounts.py advisory ``errors[]`` list
    is routed through ``normalize_advisory_errors`` before reaching the wire
    (#1721 M1) -- one call here covers all six gate-check sites
    plus the settings-update-not-found and activation-proof advisories, since
    they all build their result through this function.
    """
    from src.core.exceptions import normalize_advisory_errors

    return _build_sync_result(
        brand=brand,
        operator=operator,
        action="failed",
        status="rejected",
        billing=billing,
        sandbox=sandbox,
        errors=normalize_advisory_errors(errors),
    )


def _first_gate_failure(gates: Iterable[Callable[[], list["Error"] | None]]) -> list["Error"] | None:
    """Run per-entry gate checks in order; return the first one's errors, or None.

    Both the provisioning arm (domain/billing/sandbox/notification-configs) and
    the settings-update arm (notification-configs/rejected-fields) are a list of
    independent gate checks where the first failure short-circuits the rest --
    this is the ONE place that shape is expressed (#1721 M1; was 6
    duplicated check-then-build-then-continue blocks).
    """
    for gate in gates:
        errors = gate()
        if errors is not None:
            return errors
    return None


def _provisioning_gates(
    *,
    brand_domain: str,
    billing_val: str | None,
    identity: ResolvedIdentity,
    sandbox: bool | None,
    tenant: Mapping[str, object] | None,
    index: int,
    entry: SyncEntry,
    proof_failures: dict[int, list["Error"]],
) -> list[Callable[[], list["Error"] | None]]:
    """The provisioning arm's gate list, in order: domain validity (reserved
    TLDs) -> billing policy (BR-RULE-059) -> sandbox capability (BR-RULE-209
    INV-6) -> notification_configs. The first failure short-circuits the rest.

    A module-level function, not a per-entry closure defined inside the sync
    loop: the returned lambdas close over ITS OWN parameters (fresh on every
    call), so this adds zero complexity to ``_sync_accounts_impl`` and raises
    no ruff B023 loop-variable-closure warning (#1721 M1).
    """
    return [
        lambda: _check_domain_validity(brand_domain),
        lambda: _check_billing_policy(billing_val, identity),
        lambda: _check_sandbox_capability(sandbox, tenant, index),
        lambda: _notification_configs_gate(entry, proof_failures.get(index)),
    ]


def _build_setup_for_approval(mode: str, tenant_id: str) -> "Setup | None":
    """Build a Setup object based on the approval mode.

    Returns Setup for pending_approval modes, None for auto-approve.
    """
    from datetime import datetime, timedelta

    from adcp.types import Setup  # SDK 5.7: moved from sync_accounts_response to adcp.types

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


def _check_domain_validity(brand_domain: str) -> list["Error"] | None:
    """Check if the brand domain is valid for account provisioning.

    Returns a list of Error objects if invalid, None if valid.
    Reserved TLDs (.test, .invalid, .example, .localhost) are rejected.
    """
    from adcp.types import Error

    from src.core.security.url_validator import RESERVED_TLDS

    for tld in RESERVED_TLDS:
        if brand_domain.endswith(tld):
            return [
                Error(  # structural-guard: advisory per-account result in SyncAccountsResponse.errors[]
                    code="VALIDATION_ERROR",
                    message=f"Domain '{brand_domain}' uses reserved TLD '{tld}' "
                    f"and cannot be used for account provisioning.",
                    suggestion="Use a real domain name for production accounts.",
                    field="brand.domain",
                    recovery="correctable",
                )
            ]
    return None


def _check_billing_policy(
    billing_val: str | None,
    identity: ResolvedIdentity,
) -> list["Error"] | None:
    """Check if the billing model is supported by the seller.

    Returns a list of Error objects if rejected, None if accepted.
    Per BR-RULE-059: unsupported billing → BILLING_NOT_SUPPORTED.
    """
    from adcp.types import Error

    from src.core.billing_policy import resolve_supported_billing

    # BR-RULE-059 governs UNSUPPORTED billing, not OMITTED billing — an
    # omitted (None) billing is never rejected, configured tenant or not.
    if billing_val is None:
        return None

    # Read billing policy from tenant configuration (not identity).
    # Both dict and TenantContext expose .get() identically, so no branching needed.
    tenant = identity.tenant if identity else None
    supported = resolve_supported_billing(tenant)

    if billing_val not in supported:
        # billing-not-supported.json: supported_billing minItems 1, "Sellers MAY
        # omit this field" -- an empty resolved policy must omit the key entirely,
        # never emit a schema-invalid empty array (salesagent-hh1f review MEDIUM #1).
        details: dict[str, object] = {"scope": "capability"}
        supported_suffix = ""
        if supported:
            details["supported_billing"] = supported
            supported_suffix = f" Supported models: {', '.join(supported)}."
        return [
            Error(  # structural-guard: advisory per-account result in SyncAccountsResponse.errors[]
                code="BILLING_NOT_SUPPORTED",
                message=f"Billing model '{billing_val}' is not supported by this seller.{supported_suffix}",
                suggestion=f"Use one of the supported billing models: {', '.join(supported)}."
                if supported
                else "Contact the seller to enable a supported billing model.",
                recovery="correctable",
                details=details,
            )
        ]
    return None


def _extract_natural_key(entry: SyncEntry) -> tuple[str, str | None, str, bool | None]:
    """Extract natural key components from a PROVISIONING-mode sync request entry.

    Returns (brand_domain, brand_id, operator, sandbox).

    Callers dispatch settings-update entries (``entry.account`` set) to
    ``_process_settings_update_entry`` BEFORE reaching this function — an
    entry that lands here with no ``brand`` carries neither the provisioning
    trio nor an account reference, a genuinely malformed request the pinned
    3.1 spec's ``required: ["brand", "operator", "billing"]`` (provisioning
    arm) rejects as a buyer-correctable 400 (salesagent-5g8e; previously this
    branch also caught settings-update entries before that mode was
    implemented — it no longer does).

    Raises:
        AdCPValidationError: if the entry omits ``brand`` or ``operator`` --
            both REQUIRED for provisioning mode per the pinned spec. Only
            ``brand`` was checked before typing this function surfaced that
            ``operator`` is ``str | None`` on the ``SyncEntry`` union (it is
            optional on the settings-update arm) with no matching runtime
            guard here.
    """
    brand = entry.brand
    operator = entry.operator
    if brand is None or operator is None:
        raise AdCPValidationError(
            "Each provisioning account entry must include 'brand', 'operator', and 'billing' "
            "(or 'account' for a settings-update entry).",
            recovery="correctable",
        )
    brand_domain, brand_id = brand_key_parts(brand)
    sandbox = entry.sandbox
    return brand_domain, brand_id, operator, sandbox


def _check_sandbox_capability(
    entry_sandbox: bool | None, tenant: Mapping[str, object] | None, index: int
) -> list["Error"] | None:
    """Reject sandbox provisioning when the seller has not declared account.sandbox support.

    Mirrors the ``_check_domain_validity``/``_check_billing_policy`` per-entry
    gate shape. BR-RULE-209 INV-6: only a seller with ``account.sandbox: true``
    (Tenant.account_sandbox) supports sandbox provisioning.
    """
    from adcp.types import Error

    if not entry_sandbox:
        return None
    if tenant and tenant.get("account_sandbox", True):
        return None
    return [
        Error(  # structural-guard: advisory per-account result in SyncAccountsResponse.errors[]
            code="UNSUPPORTED_FEATURE",
            message="Sandbox account provisioning was requested, but this seller does not "
            "declare account.sandbox support.",
            field=f"accounts[{index}].sandbox",
            suggestion="Check get_adcp_capabilities and remove the unsupported sandbox field, "
            "or provision a production account instead.",
            recovery="correctable",
        )
    ]


# Media-buy-anchored notification event types. These describe the lifecycle of a
# media buy's delivery reporting, not an account, so they do not belong on the
# account surface. There is deliberately no account-lifecycle event type either
# (no account.status_changed) -- poll list_accounts or use push_notification_config.
_MEDIA_BUY_ANCHORED_EVENT_TYPES = frozenset({"scheduled", "final", "delayed", "adjusted", "impairment"})


def _check_notification_configs(configs: Iterable[NotificationConfig] | None) -> list["Error"] | None:
    """Validate a submitted notification_configs array; None when it is acceptable.

    Same per-entry gate shape as ``_check_domain_validity`` / ``_check_billing_policy``
    / ``_check_sandbox_capability``, and called from BOTH entry handlers so the two
    arms cannot drift.

    Check ORDER is load-bearing: the first failure decides the reported
    ``error.field``, and the scenarios pin exact pointers. Duplicates are detected
    before event scope so a duplicated entry reports its own
    ``[j].subscriber_id`` rather than some unrelated later field.

    These are per-account failures INSIDE a transport-level success -- the caller
    turns them into ``_build_failed_result``, never a transport error.
    """
    from adcp.types import Error

    from src.core.security.url_validator import check_url_syntax

    if not configs:
        return None

    seen_subscribers: set[str] = set()
    for index, config in enumerate(configs):
        subscriber_id = getattr(config, "subscriber_id", None)
        if subscriber_id in seen_subscribers:
            return [
                Error(  # structural-guard: advisory per-account result in SyncAccountsResponse.errors[]
                    code="INVALID_REQUEST",
                    message=f"Duplicate subscriber_id '{subscriber_id}' in the submitted "
                    "notification_configs array; each subscriber_id may appear at most once.",
                    field=f"notification_configs[{index}].subscriber_id",
                    suggestion="Send one entry per subscriber_id -- the array declares the full "
                    "desired set, so a repeated id is ambiguous rather than an update.",
                    recovery="correctable",
                )
            ]
        if subscriber_id is not None:
            seen_subscribers.add(subscriber_id)

        for event_index, event_type in enumerate(getattr(config, "event_types", None) or []):
            if enum_value(event_type) in _MEDIA_BUY_ANCHORED_EVENT_TYPES:
                return [
                    Error(  # structural-guard: advisory per-account result in SyncAccountsResponse.errors[]
                        code="INVALID_REQUEST",
                        message=f"Event type '{enum_value(event_type)}' is media-buy-anchored and "
                        "cannot be subscribed to on the account surface.",
                        field=f"notification_configs[{index}].event_types[{event_index}]",
                        suggestion="Subscribe to media-buy delivery events on the media buy itself; "
                        "account-level subscriptions carry creative and account-scoped events only.",
                        recovery="correctable",
                    )
                ]

        url = getattr(config, "url", None)
        # Syntax only -- NOT the DNS-resolving check_url_ssrf. A buyer may register
        # a webhook before standing the endpoint up, so requiring resolution at
        # write time would reject legitimate registrations. Reachability is the
        # activation proof's job (F4c), at fire time.
        url_ok, url_error = check_url_syntax(str(url), require_https=True)
        if not url_ok:
            return [
                Error(  # structural-guard: advisory per-account result in SyncAccountsResponse.errors[]
                    code="INVALID_REQUEST",
                    message=f"notification_configs url is not acceptable: {url_error}",
                    field=f"notification_configs[{index}].url",
                    suggestion="Provide an absolute https:// URL on a publicly routable host.",
                    recovery="correctable",
                )
            ]
    return None


def _notification_configs_gate(entry: SyncEntry, proof_errors: list["Error"] | None) -> list["Error"] | None:
    """The notification_configs gate, shared verbatim by both sync-accounts arms.

    Runs BEFORE any write so a rejected entry leaves the persisted array
    byte-identical. When the array itself is schema-valid, falls back to the
    precomputed activation-proof errors -- the proof ran before any
    transaction opened, so a failure here still writes nothing for this entry.
    A module-level function (not a per-entry closure) so neither call site's
    complexity grows with it (#1721 M1).
    """
    errors = _check_notification_configs(getattr(entry, "notification_configs", None))
    return errors if errors is not None else proof_errors


def _settings_update_preview_state(
    existing: DBAccount | None,
    ref: AccountRef,
    previewed_by_key: dict[tuple[str | None, str | None, str, bool], DBAccount],
) -> DBAccount | None:
    """The request-local stand-in a dry_run settings-update reads and writes.

    Mirrors the provisioning arm's seeding: first touch of a persisted row on a
    key registers a never-persisted copy, so the entry's in-memory "write" can
    not reach the session and later entries on the same key grade against the
    previewed state. Seeded with the PROVISIONING superset of applied fields —
    the settings-update set is a strict subset, and a later provisioning entry
    on the same key must read real values off this object.

    With no persisted row, a natural-key reference (AccountReference2) may still
    target an account an EARLIER entry in this same preview would create — the
    live arm finds that row because ``repo.create()`` flushed it, so the preview
    must consult ``previewed_by_key`` for parity. An ``account_id`` reference
    cannot name a not-yet-created account (ids are server-generated), so a repo
    miss there stays unmatched, exactly as on the live arm.
    """
    if existing is not None:
        brand_domain, brand_id = brand_key_parts(existing.brand)
        key = (brand_domain, brand_id, existing.operator or "", bool(existing.sandbox))
        state = previewed_by_key.get(key)
        if state is None:
            state = _preview_state_from(
                existing,
                mode="provisioning",
                brand_domain=brand_domain or "",
                brand_id=brand_id,
                operator=existing.operator or "",
            )
            previewed_by_key[key] = state
        return state
    if isinstance(ref, AccountReference1):
        return None
    brand_domain, brand_id = brand_key_parts(ref.brand)
    return previewed_by_key.get((brand_domain, brand_id, ref.operator or "", bool(ref.sandbox)))


def _resolve_settings_update_target(ref: AccountRef, repo: AccountRepository) -> DBAccount | None:
    """Resolve a settings-update AccountReference to its persisted row, if any."""
    if isinstance(ref, AccountReference1):
        return repo.get_by_id(ref.account_id)
    brand_domain, brand_id = brand_key_parts(ref.brand)
    return repo.get_by_natural_key(
        operator=ref.operator,
        brand_domain=brand_domain,
        brand_id=brand_id,
        sandbox=ref.sandbox,
    )


def _unmatched_settings_update_result(ref: AccountRef) -> SyncResponseAccount:
    """The UNSUPPORTED_PROVISIONING failure for an unmatched account reference.

    brand/operator are REQUIRED on SyncResponseAccount. A natural-key reference
    (AccountReference2) still carries brand/operator to echo even when unmatched;
    an account_id reference (AccountReference1) carries none -- "unknown" is the
    established placeholder convention in this file for exactly that situation
    (cf. the publisher-domain placeholder above), not a fabricated real value.
    """
    from adcp.types import Error

    if isinstance(ref, AccountReference1):
        fail_brand: LibraryBrandReference | Mapping[str, object] = {"domain": "unknown"}
        fail_operator = "unknown"
    else:
        fail_brand = ref.brand if ref.brand else {"domain": "unknown"}
        fail_operator = ref.operator or "unknown"
    return _build_failed_result(
        brand=fail_brand,
        operator=fail_operator,
        billing=None,
        sandbox=None,
        errors=[
            Error(  # structural-guard: advisory per-account result in SyncAccountsResponse.errors[]
                code="UNSUPPORTED_PROVISIONING",
                message="No existing account matches the provided account reference; "
                "a settings-update entry never provisions a new account.",
                suggestion="Provide 'brand', 'operator', and 'billing' to provision a new account instead.",
                recovery="correctable",
            )
        ],
    )


def _process_settings_update_entry(
    entry: SyncEntry,
    repo: AccountRepository,
    proof_errors: list["Error"] | None = None,
    index: int = 0,
    *,
    dry_run: bool = False,
    previewed_by_key: dict[tuple[str | None, str | None, str, bool], DBAccount] | None = None,
) -> SyncResponseAccount:
    """Handle a settings-update entry (keyed by AccountReference) -- update an
    EXISTING account's settable fields, NEVER provision (F1b/F1c).

    ``entry.account`` is ``RootModel[AccountReference1 | AccountReference2]``:
    ``AccountReference1`` carries ``account_id`` (seller-assigned handle);
    ``AccountReference2`` carries the natural key (``brand``/``operator``/
    ``sandbox``). An unmatched reference is rejected with
    UNSUPPORTED_PROVISIONING -- a settings-update entry MUST NOT provision a
    new account under any circumstance.

    Under ``dry_run`` the entry is graded identically but writes nothing: the
    resolved changes are applied to a request-local preview state (the same
    ``previewed_by_key`` mechanism the provisioning arm uses) instead of
    ``repo.update_fields`` (sync-accounts-request.json#/properties/dry_run:
    "preview what would change without applying").
    """
    assert entry.account is not None, "caller dispatches here only when entry.account is set"
    ref = entry.account.root
    existing = _resolve_settings_update_target(ref, repo)

    # Echo brand/operator from the persisted row when there is one — the preview
    # state's brand dict is REBUILT by AccountRepository.build_row and may drop
    # extra keys.
    persisted = existing
    if dry_run:
        if previewed_by_key is None:
            previewed_by_key = {}
        existing = _settings_update_preview_state(existing, ref, previewed_by_key)

    if existing is None:
        return _unmatched_settings_update_result(ref)

    echo_brand = persisted.brand if persisted is not None else existing.brand
    echo_operator = persisted.operator if persisted is not None else existing.operator
    # Account.brand/.operator are DB-nullable (defensive column typing) but
    # AccountRepository.build_row always sets both at creation -- an EXISTING,
    # matched account (which is what this arm always operates on) cannot
    # actually have either unset.
    assert echo_brand is not None, "persisted account has no brand -- should be unreachable"

    # notification_configs (shared with the provisioning arm) -> rejected-field
    # check (BR-RULE-209-family fields the table marks `rejected` on this arm --
    # schema-LEGAL, so per-account "failed", never an operation-level raise).
    gate_errors = _first_gate_failure(
        [
            lambda: _notification_configs_gate(entry, proof_errors),
            lambda: _rejected_field_errors(entry, mode="settings_update", index=index),
        ]
    )
    if gate_errors is not None:
        return _build_failed_result(
            brand=echo_brand,
            operator=echo_operator or "",
            billing=existing.billing,
            sandbox=existing.sandbox,
            errors=gate_errors,
        )

    # The SAME table walk the provisioning arm runs -- the two arms cannot
    # disagree about which fields they apply, because neither names a field.
    resolved = _resolve_entry_changes(entry, existing, mode="settings_update")
    changes = {field: value for field, value in resolved.items() if _persisted_value(existing, field) != value}

    action = "unchanged"
    if changes:
        if dry_run:
            # The write the live arm performs, in memory only: `existing` is the
            # request-local preview state (never a persisted row), so this can
            # reach no session and later entries on the key read post-write state.
            for field, value in changes.items():
                setattr(existing, field, value)
        else:
            repo.update_fields(existing.account_id, **changes)
        action = "updated"

    return _build_sync_result(
        brand=echo_brand,
        operator=echo_operator or "",
        action=action,
        status=existing.status,
        account_id=existing.account_id,
        name=existing.name,
        billing=existing.billing,
        payment_terms=existing.payment_terms,
        sandbox=existing.sandbox,
        # Post-write state: the applied value when this entry changed it, the
        # persisted one otherwise. changes is dict[str, object] (see
        # _resolve_entry_changes); cast back to each field's own type.
        notification_configs=cast(
            "list[dict[str, object]] | None",
            changes.get("notification_configs", existing.notification_configs),
        ),
        billing_entity=cast("dict[str, object] | None", changes.get("billing_entity", existing.billing_entity)),
    )


#: Ceiling on how long ALL activation challenges in one request may take. The
#: per-challenge timeout bounds a single endpoint; without a request-level bound a
#: buyer could submit 16 configs x N accounts and hold a worker for minutes.
_PROOF_BUDGET_SECONDS = 6.0


def _proof_tuple(config: NotificationConfig) -> tuple[str, str, str | None, tuple[str, ...]]:
    """The identity of a proof, per the spec's proof-reuse allowance.

    A re-sent config whose (subscriber_id, normalized url, auth binding, normalized
    event_types) matches an already-proven persisted entry MAY skip re-proof.
    """
    auth = config.authentication
    auth_scheme = getattr(auth, "scheme", None) if auth is not None else None
    return (
        config.subscriber_id,
        str(config.url or "").rstrip("/"),
        enum_value(auth_scheme) if auth_scheme is not None else None,
        tuple(sorted(enum_value(e) for e in (config.event_types or []))),
    )


def _collect_activating_entries(entries: list[SyncEntry]) -> list[tuple[int, SyncEntry, NotificationConfig]]:
    """(entry index, entry, config) for every config declaring ``active: true``."""
    activating: list[tuple[int, SyncEntry, NotificationConfig]] = []
    for index, entry in enumerate(entries):
        for config in getattr(entry, "notification_configs", None) or []:
            if getattr(config, "active", False):
                activating.append((index, entry, config))
    return activating


def _proof_error(entry: SyncEntry, config: NotificationConfig, message: str, suggestion: str) -> "Error":
    """The per-account error a failed/skipped activation produces.

    One builder for both the dry_run and challenge-failure paths -- they carry the
    same code, recovery and field pointer, and only the explanation differs.
    """
    from adcp.types import Error

    return Error(  # structural-guard: advisory per-account result in SyncAccountsResponse.errors[]
        code="VALIDATION_ERROR",
        message=message,
        field=f"notification_configs[{_config_index(entry, config)}].url",
        suggestion=suggestion,
        recovery="correctable",
    )


def _already_proven_tuples(
    activating: list[tuple[int, SyncEntry, NotificationConfig]], tenant_id: str
) -> dict[int, set[tuple]]:
    """Proof tuples already persisted as active, per entry index.

    Its own SHORT read-only transaction, closed before any socket is opened -- the
    whole point of hoisting the proof out of the write transaction.
    """
    already_proven: dict[int, set[tuple]] = {}
    with AccountUoW(tenant_id) as uow:
        assert uow.accounts is not None
        for index, entry, _ in activating:
            existing = _lookup_existing_for_entry(entry, uow.accounts)
            if existing is None:
                continue
            already_proven.setdefault(index, set()).update(
                _proof_tuple(c) for c in (existing.notification_configs or []) if getattr(c, "active", False)
            )
    return already_proven


async def _resolve_activation_proofs(
    entries: list[SyncEntry], tenant_id: str, *, dry_run: bool
) -> dict[int, list["Error"]]:
    """Run proof-of-control for every entry activating a subscriber. Index -> errors.

    Runs BEFORE the write transaction opens: an outbound call inside an open
    Postgres transaction would hold it for the whole network round trip, which the
    owner's carve-out for in-request proof deliberately does not cover.

    Skipped entirely on ``dry_run``: a preview must not fire a request at a buyer's
    endpoint. Such an entry is reported as failed rather than previewed as active,
    because a preview must not claim an outcome it did not verify.
    """
    activating = _collect_activating_entries(entries)
    if not activating:
        return {}

    if dry_run:
        return {
            index: [
                _proof_error(
                    entry,
                    config,
                    "Activating a notification subscriber requires a proof-of-control challenge, "
                    "which dry_run does not perform; this preview cannot confirm the subscriber "
                    "would be activated.",
                    "Re-send without dry_run to perform the challenge.",
                )
            ]
            for index, entry, config in activating
        }

    already_proven = _already_proven_tuples(activating, tenant_id)
    prover = get_notification_proof_service()
    failures: dict[int, list[Error]] = {}
    budget = _PROOF_BUDGET_SECONDS

    for index, entry, config in activating:
        # Identical tuple already proven and persisted as active -- the spec permits
        # skipping re-proof, so no challenge is sent at all.
        if _proof_tuple(config) in already_proven.get(index, set()):
            continue
        proven, budget = await _prove_within_budget(prover, entry, config, budget)
        if not proven:
            failures.setdefault(index, []).append(
                _proof_error(
                    entry,
                    config,
                    "Proof-of-control challenge failed for the notification endpoint; the "
                    "subscriber was not activated and the account's previous notification_configs "
                    "are unchanged.",
                    "Ensure the endpoint answers the challenge POST with 2xx, then re-send.",
                )
            )
    return failures


async def _prove_within_budget(
    prover: NotificationProofService, entry: SyncEntry, config: NotificationConfig, budget: float
) -> tuple[bool, float]:
    """Run one challenge if the request-level budget allows. Returns (proven, budget left).

    An exhausted budget is "not proven" rather than an unbounded wait: the caller is
    holding an HTTP request open.
    """
    if budget <= 0:
        return False, budget
    started = time.monotonic()
    proven = await prover.prove(_entry_account_hint(entry), config)
    return proven, budget - (time.monotonic() - started)


def _config_index(entry: SyncEntry, config: NotificationConfig) -> int:
    """Position of *config* in *entry*'s submitted array (for error.field)."""
    for index, candidate in enumerate(getattr(entry, "notification_configs", None) or []):
        if candidate is config:
            return index
    return 0


def _entry_account_hint(entry: SyncEntry) -> str:
    """A human-meaningful account identifier for proof logging."""
    ref = getattr(entry, "account", None)
    if ref is not None and isinstance(getattr(ref, "root", None), AccountReference1):
        return str(ref.root.account_id)
    brand = getattr(entry, "brand", None)
    return str(getattr(brand, "domain", None) or "unknown")


def _preview_state_from(
    existing: DBAccount, *, mode: str, brand_domain: str, brand_id: str | None, operator: str
) -> DBAccount:
    """A request-local, never-persisted stand-in for an account already on file.

    This is the POST-WRITE state object the dry_run arms have to read their
    result fields off. The live arm gets that state for free — ``update_fields``
    ``setattr``s the identity-mapped instance and flushes — so ``existing`` there
    means "the row as the write LEFT it". Under dry_run nothing mutates the
    loaded row, and mutating it would be written out at commit, so the preview
    needs its own object to apply the resolved changes to.

    The copied column set is DERIVED from :data:`_FIELD_POLICY` — every field
    ``mode`` marks ``applied``, which is exactly the set the comparison can
    change. Hand-listing it would make this seed the third field list this bug
    is about. Raw ``getattr``, not :func:`_persisted_value`: the comparison
    applies its own serialization on top, on both arms, so the seed must mirror
    the raw COLUMN state the live arm's row carries.

    Construction goes through :meth:`AccountRepository.build_row` so accounts.py
    keeps ONE row-construction call site
    (tests/unit/test_guards_sync_accounts_row_builder.py). Never
    ``copy``/``deepcopy``/``expunge`` the loaded row: the first two carry
    ``_sa_instance_state`` and the last breaks the live arm's identity map.

    ``mode`` is a parameter rather than a constant because the settings-update
    arm needs the same state object under its own disposition column.

    Note the row's identity comes from the caller's NATURAL KEY rather than off
    ``existing``: the two agree by construction (``get_by_natural_key`` filtered
    on exactly these, and all three are immutable at the repository), and the key
    is the non-optional spelling. A consequence is that ``brand`` is REBUILT, so
    any extra key in the persisted brand dict is dropped — safe on the
    provisioning arm, where nothing reads ``state.brand`` (the result echoes
    ``entry.brand`` and ``_FIELD_POLICY`` has no brand field), but do not hand
    this object to code that does.
    """
    created_fields = {
        field: getattr(existing, field) for field in _FIELD_POLICY if _disposition(field, mode).kind == "applied"
    }
    return AccountRepository.build_row(
        tenant_id=existing.tenant_id,
        account_id=existing.account_id,
        name=existing.name,
        status=existing.status,
        brand_domain=brand_domain,
        brand_id=brand_id,
        operator=operator,
        principal_id=existing.principal_id,
        created_fields=created_fields,
    )


def _build_update_result(
    *, entry: SyncEntry, operator: str, state: DBAccount, changes: dict[str, object]
) -> SyncResponseAccount:
    """The ONE place a provisioning ``updated``/``unchanged`` result is built.

    ``state`` MUST be the POST-WRITE row — the row as the write would leave it,
    which is the loaded instance after ``repo.update_fields`` on the live arm and
    the request-local preview object after the equivalent ``setattr`` loop on the
    dry arm. Every reported value is read off it, so the two arms cannot describe
    the same outcome differently.

    Drift insurance, not a bug fix: before #1721 both arms already called one
    builder with a byte-identical argument list and STILL diverged, because the
    row they handed it meant different things. What this function adds is a
    single, guarded place where "post-write" is stated as a precondition
    (tests/unit/test_guards_sync_accounts_update_result_builder.py), so a future
    edit cannot quietly reintroduce a pre-write read next to a post-write one.
    """
    assert entry.brand is not None, "only called for provisioning-mode entries"
    return _build_sync_result(
        brand=entry.brand,
        operator=operator,
        action="updated" if changes else "unchanged",
        status=state.status,
        account_id=state.account_id,
        name=state.name,
        billing=state.billing,
        sandbox=state.sandbox,
        notification_configs=state.notification_configs,
        billing_entity=state.billing_entity,
    )


def _apply_to_existing_account(
    entry: SyncEntry, existing: DBAccount, repo: AccountRepository, operator: str
) -> SyncResponseAccount:
    """Apply a provisioning entry to the account that already holds its natural key.

    Shared by the two ways an entry can turn out to be an update rather than a
    create: the lookup found the account, or the create LOST the unique-index race
    to a concurrent writer (#1721). Both must produce the same result — the race
    outcome is only "what this entry would have returned had it arrived a
    microsecond later" — so they cannot be allowed to drift apart.
    """
    changes = _account_fields_changed(existing, entry)
    if changes:
        # ``update_fields`` setattrs the identity-mapped instance and flushes, so
        # ``existing`` is the POST-write row from here on — which is what
        # _build_update_result requires.
        repo.update_fields(existing.account_id, **changes)

    return _build_update_result(entry=entry, operator=operator, state=existing, changes=changes)


def _lookup_existing_for_entry(entry: SyncEntry, repo: AccountRepository) -> DBAccount | None:
    """Resolve the persisted account an entry targets, in either entry mode."""
    ref = getattr(entry, "account", None)
    if ref is not None:
        inner = ref.root
        if isinstance(inner, AccountReference1):
            return repo.get_by_id(inner.account_id)
        brand_domain, brand_id = brand_key_parts(inner.brand)
        return repo.get_by_natural_key(
            operator=inner.operator, brand_domain=brand_domain, brand_id=brand_id, sandbox=inner.sandbox
        )
    brand = getattr(entry, "brand", None)
    operator = getattr(entry, "operator", None)
    if brand is None or operator is None:
        # A malformed provisioning entry (missing brand/operator) matches no
        # existing account here; _extract_natural_key rejects it explicitly
        # later in the main loop.
        return None
    brand_domain, brand_id = brand_key_parts(brand)
    return repo.get_by_natural_key(
        operator=operator, brand_domain=brand_domain, brand_id=brand_id, sandbox=entry.sandbox
    )


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
        # No key is minted for the caller: idempotency_key is client-generated
        # (sync-accounts-request.json 3.1.1). A keyless request stays keyless -- it then
        # fails the empty-accounts check below, which is the honest outcome.
        req = SyncAccountsRequest(accounts=[])

    # BR-RULE-055: sync requires auth (consistent with list_accounts). require_principal_id
    # first so the canonical auth message surfaces for a missing/anonymous token; require_identity
    # then narrows the type for _check_billing_policy below.
    principal_id = require_principal_id(identity, context=req.context)
    identity = require_identity(identity, context=req.context)
    tenant = require_tenant(identity, context=req.context)
    tenant_id = tenant["tenant_id"]

    # Validate non-empty accounts array
    if not req.accounts:
        raise AdCPValidationError("accounts array must not be empty — at least one account is required.")
    dry_run = bool(req.dry_run)
    delete_missing = bool(req.delete_missing)

    results: list[SyncResponseAccount] = []
    # Track natural keys in the payload for delete_missing
    seen_account_ids: set[str] = set()
    #: dry_run only — natural key -> the POST-WRITE state this request has left on
    #: that key: the row an earlier entry WOULD have created, or a never-persisted
    #: stand-in for the row already on file. Stands in for the state the live arm
    #: gets for free from repo.create()'s flush and repo.update_fields()' setattr.
    previewed_by_key: dict[tuple[str | None, str | None, str, bool], DBAccount] = {}

    # Activation proof runs BEFORE the write transaction opens (see
    # _resolve_activation_proofs). Holding a Postgres transaction across an
    # outbound HTTP call is what the owner's carve-out explicitly does not cover.
    proof_failures = await _resolve_activation_proofs(req.accounts, tenant_id, dry_run=dry_run)

    with AccountUoW(tenant_id) as uow:
        assert uow.accounts is not None
        repo = uow.accounts

        for index, entry in enumerate(req.accounts):
            # Mode-exclusivity guard (F1a): an entry carrying BOTH an account
            # reference and any provisioning-trio field violates the request
            # schema's item oneOf -- a structural, operation-level rejection,
            # not a per-account business-rule failure. Must run before any
            # dispatch; the SDK union has no real oneOf enforcement and would
            # otherwise silently parse this as the provisioning arm.
            if entry.account is not None and (
                entry.brand is not None or entry.operator is not None or entry.billing is not None
            ):
                raise AdCPValidationError(
                    f"accounts[{index}] carries both an account reference (settings-update) and "
                    "provisioning fields (brand/operator/billing) -- these are mutually exclusive.",
                    field=f"accounts[{index}]",
                    recovery="correctable",
                )

            if entry.account is not None:
                su_result = _process_settings_update_entry(
                    entry,
                    repo,
                    proof_failures.get(index),
                    index,
                    dry_run=dry_run,
                    previewed_by_key=previewed_by_key,
                )
                results.append(su_result)
                # delete_missing deactivates accounts "not included in this
                # request" (sync-accounts-request.json#/properties/delete_missing)
                # — a settings-update target IS included, so it must be marked
                # seen or the very request that updated it would close it. A
                # FAILED result carries no account_id (built by
                # _build_failed_result), so a failed entry does not shield its
                # account — same boundary as failed provisioning entries, which
                # never reach seen_account_ids either.
                if su_result.account_id:
                    seen_account_ids.add(su_result.account_id)
                continue

            brand_domain, brand_id, operator, sandbox = _extract_natural_key(entry)
            billing_val = _enum_to_str(entry.billing)

            gate_errors = _first_gate_failure(
                _provisioning_gates(
                    brand_domain=brand_domain,
                    billing_val=billing_val,
                    identity=identity,
                    sandbox=sandbox,
                    tenant=tenant,
                    index=index,
                    entry=entry,
                    proof_failures=proof_failures,
                )
            )
            if gate_errors is not None:
                results.append(
                    _build_failed_result(
                        brand=entry.brand,
                        operator=operator,
                        billing=billing_val,
                        sandbox=sandbox,
                        errors=gate_errors,
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

            # The FULL key the unique index and every resolver use — a partial key
            # would collapse accounts that differ only by brand_id or sandbox, which
            # are legitimately distinct.
            natural_key = (brand_domain, brand_id, operator, bool(sandbox))
            if dry_run:
                # The state this request has already left on the key — seeded from
                # the persisted row on first touch, so a key that ALSO has a row
                # gains the same in-request memory a previewed CREATE has. Without
                # the seed every entry on that key is graded against the same
                # unmutated row, and the result echoes the value the buyer is
                # REPLACING rather than the one they would get (#1721).
                state = previewed_by_key.get(natural_key)
                if state is None and existing is not None:
                    state = _preview_state_from(
                        existing,
                        mode="provisioning",
                        brand_domain=brand_domain,
                        brand_id=brand_id,
                        operator=operator,
                    )
                    previewed_by_key[natural_key] = state
                if state is not None:
                    # Everything downstream — the create/update decision, the
                    # comparison, and every reported field — reads the state, never
                    # the loaded row.
                    existing = state

            if existing is not None:
                seen_account_ids.add(existing.account_id)

                if dry_run:
                    changes = _account_fields_changed(existing, entry)
                    # The write the live arm performs, in memory only: `existing` is
                    # the request-local state seeded above, never a persisted row,
                    # so this can reach no session. Applied UNFILTERED, exactly as
                    # the live arm hands `changes` to repo.update_fields — filtering
                    # here would let the preview report a change no real run makes.
                    for field, value in changes.items():
                        setattr(existing, field, value)
                    results.append(
                        _build_update_result(entry=entry, operator=operator, state=existing, changes=changes)
                    )
                    continue

                results.append(_apply_to_existing_account(entry, existing, repo, operator))
            else:
                # Create new account. A create IS "resolve against nothing": the
                # SAME table walk both update sites run, with existing=None, so a
                # field cannot be applied on re-sync but dropped at create (the
                # aperture bug that hid billing_entity). An omitted field simply
                # produces no kwarg and the column keeps its default.
                created_fields = _resolve_entry_changes(entry, None, mode="provisioning")
                # created_fields is dict[str, object] (a generic field-application
                # bag shared by all three _FIELD_POLICY call sites) -- cast() back
                # to each field's own resolver-return type (_resolve_scalar /
                # _resolve_notification_configs / _resolve_billing_entity), never Any.
                billing_val = cast("str | None", created_fields.get("billing"))
                notification_configs_val = cast(
                    "list[dict[str, object]] | None", created_fields.get("notification_configs")
                )
                billing_entity_val = cast("dict[str, object] | None", created_fields.get("billing_entity"))

                account_id = _generate_account_id()
                account_name = _generate_account_name(brand_domain, operator, brand_id)

                # BR-RULE-060: determine approval status from tenant config.
                # account_approval_mode is a distinct field from creative approval_mode
                # (BR-RULE-037) — do NOT fall back to approval_mode.
                # Resolved BEFORE the dry_run branch so previews reflect what a real
                # create would return (BR-RULE-062).
                approval_mode = tenant.get("account_approval_mode")
                setup = _build_setup_for_approval(approval_mode or "auto", tenant_id)
                initial_status = "pending_approval" if setup else "active"

                if dry_run:
                    # Remember what this entry WOULD create, so a later entry on the
                    # same natural key resolves against it. The live arm gets that
                    # memory for free — repo.create() flushes, so the next lookup
                    # finds the row — and without an equivalent here a payload
                    # carrying one key twice previewed "created" twice, under two
                    # account_ids, an outcome no real run can produce (BR-RULE-062).
                    # The row is built by the SAME helper the live arm uses and is
                    # deliberately never added to the session.
                    previewed_by_key[natural_key] = AccountRepository.build_row(
                        tenant_id=tenant_id,
                        account_id=account_id,
                        name=account_name,
                        status=initial_status,
                        brand_domain=brand_domain,
                        brand_id=brand_id,
                        operator=operator,
                        principal_id=principal_id,
                        created_fields=created_fields,
                    )
                    # account_id was generated above (BR-RULE-062 — preview reflects
                    # what a real create would return). It is a preview value, not a
                    # commitment to that specific id.
                    results.append(
                        _build_sync_result(
                            brand=entry.brand,
                            operator=operator,
                            action="created",
                            status=initial_status,
                            account_id=account_id,
                            name=account_name,
                            billing=billing_val,
                            sandbox=sandbox,
                            setup=setup,
                            notification_configs=notification_configs_val,
                            billing_entity=billing_entity_val,
                        )
                    )
                    continue

                new_account = AccountRepository.build_row(
                    tenant_id=tenant_id,
                    account_id=account_id,
                    name=account_name,
                    status=initial_status,
                    brand_domain=brand_domain,
                    brand_id=brand_id,
                    operator=operator,
                    principal_id=principal_id,
                    created_fields=created_fields,
                )
                try:
                    repo.create(new_account)
                except NaturalKeyConflict as exc:
                    # Lost the unique-index race: a concurrent writer committed this
                    # natural key between our lookup above and our insert. The buyer's
                    # semantic here is upsert-by-natural-key, so resolve to the winner
                    # rather than failing the entry — the only difference between this
                    # entry and one that arrived a microsecond later is timing, and
                    # timing must not change the answer. repo.create rolled its insert
                    # back through a SAVEPOINT, so this transaction is healthy and the
                    # rest of the batch still runs.
                    winner = repo.get_by_id(exc.existing_account_id) if exc.existing_account_id else None
                    if winner is None:
                        # The conflict named a row; if it is gone the key is free again
                        # and we cannot explain the failure. Do not invent a cause.
                        raise
                    seen_account_ids.add(winner.account_id)
                    results.append(_apply_to_existing_account(entry, winner, repo, operator))
                    continue

                seen_account_ids.add(account_id)

                # Grant agent access to the new account
                repo.grant_access(principal_id, account_id)

                results.append(
                    _build_sync_result(
                        brand=entry.brand,
                        operator=operator,
                        action="created",
                        status=initial_status,
                        account_id=account_id,
                        name=account_name,
                        billing=billing_val,
                        sandbox=sandbox,
                        setup=setup,
                        notification_configs=notification_configs_val,
                        billing_entity=billing_entity_val,
                    )
                )

        # BR-RULE-061: delete_missing — close accounts not in payload.
        # The block runs under dry_run too, and ONLY the mutation is skipped:
        # deactivation is the sole effect delete_missing has, and dry_run "returns
        # what would be created/updated/deactivated" (v3.1.1
        # sync-accounts-request.json#/properties/dry_run), so a preview that walks
        # nothing tells the buyer none of their accounts would close. Building the
        # result outside the guard is what makes the two arms byte-identical here.
        if delete_missing:
            agent_accounts = repo.list_by_principal(principal_id)
            for db_acct in agent_accounts:
                if db_acct.account_id not in seen_account_ids:
                    if not dry_run:
                        repo.update_status(db_acct.account_id, "closed")
                    assert db_acct.brand is not None, "persisted account has no brand -- should be unreachable"
                    results.append(
                        _build_sync_result(
                            brand=db_acct.brand,
                            operator=db_acct.operator or "",
                            action="updated",
                            status="closed",
                            account_id=db_acct.account_id,
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
# sync_accounts shared request builder
# ---------------------------------------------------------------------------


def build_sync_accounts_request(
    *,
    accounts: list[SyncAccountInput | SettingsUpdateAccountInput] | None = None,
    delete_missing: bool | None = None,
    dry_run: bool | None = None,
    idempotency_key: str | None = None,
    push_notification_config: dict | None = None,
    ext: dict | None = None,
    context: ContextObject | None = None,
    adcp_version: str | None = None,
    adcp_major_version: int | None = None,
) -> SyncAccountsRequest:
    """Build the shared sync_accounts request for transport wrappers.

    Mirrors build_list_accounts_request and build_get_adcp_capabilities_request.

    ``idempotency_key`` is threaded VERBATIM and is never generated here. Per
    sync-accounts-request.json 3.1.1 the field is client-generated ("MUST be unique per
    (seller, request) pair. Use a fresh UUID v4 for each request") -- a seller that mints
    its own key on every call can never recognise a retry, which defeats the only thing
    the field exists for. Its shape is validated once, on the model
    (SyncAccountsRequest._check_idempotency_key), so every transport rejects a malformed
    key identically.
    """
    return SyncAccountsRequest(
        accounts=accounts or [],
        delete_missing=delete_missing,
        dry_run=dry_run,
        idempotency_key=idempotency_key,
        push_notification_config=push_notification_config,
        ext=ext,
        context=context,
        adcp_version=adcp_version,
        adcp_major_version=adcp_major_version,
    )


# ---------------------------------------------------------------------------
# sync_accounts MCP wrapper
# ---------------------------------------------------------------------------


async def sync_accounts(
    accounts: list[SyncAccountInput | SettingsUpdateAccountInput] | None = None,
    delete_missing: Annotated[
        bool | None, Field(description="Deactivate accounts not present in the sync list")
    ] = None,
    dry_run: Annotated[bool | None, Field(description="Preview sync results without making changes")] = None,
    idempotency_key: Annotated[
        str | None,
        Field(description="Client-generated key for at-most-once execution (16-255 chars, [A-Za-z0-9_.:-])"),
    ] = None,
    push_notification_config: Annotated[
        dict | None, Field(description="Webhook configuration for asynchronous sync notifications")
    ] = None,
    ext: Annotated[dict | None, Field(description="AdCP extension object")] = None,
    context: ContextObject | None = None,
    ctx: Context | ToolContext | None = None,
) -> ToolResult:
    """Sync accounts by natural key (MCP tool).

    MCP wrapper that accepts individual parameters per AdCP spec and
    constructs a SyncAccountsRequest for the shared implementation.

    Args:
        accounts: List of accounts to upsert.
        delete_missing: Deactivate accounts not in the list.
        dry_run: Preview changes without persisting.
        idempotency_key: Client-generated at-most-once key (sync-accounts-request.json 3.1.1).
        push_notification_config: Webhook configuration for async sync notifications.
        ext: AdCP extension object.
        context: Application-level context per AdCP spec.
        ctx: FastMCP context for authentication.

    Returns:
        ToolResult with human-readable text and structured data.
    """
    with adcp_validation_boundary(context="sync_accounts request"):
        req = build_sync_accounts_request(
            accounts=accounts,
            delete_missing=delete_missing,
            dry_run=dry_run,
            idempotency_key=idempotency_key,
            push_notification_config=push_notification_config,
            ext=ext,
            context=context,
        )
    identity = (await ctx.get_state("identity")) if isinstance(ctx, Context) else None
    response = await _sync_accounts_impl(req, identity)

    return build_tool_result(str(response), response)


# ---------------------------------------------------------------------------
# sync_accounts A2A raw wrapper
# ---------------------------------------------------------------------------


async def sync_accounts_raw(
    req: SyncAccountsRequest | None = None,
    ctx: Context | ToolContext | None = None,
    identity: IdentityOrNotProvided = NOT_PROVIDED,
) -> SyncAccountsResponse:
    """Sync accounts by natural key (raw function for A2A).

    Args:
        req: Sync request with accounts to upsert.
        ctx: FastMCP context.
        identity: Pre-resolved identity (if available).

    Returns:
        SyncAccountsResponse with per-account action results.
    """
    identity = resolve_identity_if_not_provided(identity, ctx, require_valid_token=True)
    return await _sync_accounts_impl(req, identity)
