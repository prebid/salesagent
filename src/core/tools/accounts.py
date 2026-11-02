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

"""

import base64
import logging
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal, NamedTuple, TypedDict, cast

from adcp.types import BrandReference as LibraryBrandReference
from adcp.types import PaginationRequest, PaginationResponse
from adcp.types.generated_poc.core.account_ref import AccountReference1, AccountReference2
from adcp.types.generated_poc.core.business_entity import BusinessEntity
from pydantic import BaseModel

from src.core.audit_logger import get_audit_logger
from src.core.database.models import Account as DBAccount
from src.core.database.repositories.account import AccountRepository, NaturalKey, NaturalKeyConflict
from src.core.database.repositories.account_serialization import (
    account_from_row,
    scrub_business_entity,
    scrub_notification_credentials,
)
from src.core.database.repositories.uow import AccountUoW
from src.core.errors.codes import ErrorCode, ErrorCodeT
from src.core.errors.details import BillingNotSupportedDetails, ConfigurationDetails, ErrorDetails, ValidationDetails
from src.core.exceptions import AdCPConfigurationError, AdCPValidationError
from src.core.helpers import enum_value
from src.core.helpers.brand_key import brand_key_parts
from src.core.resolved_identity import ResolvedIdentity, TransportProtocol
from src.core.schemas.account import (
    Account,
    ListAccountsRequest,
    ListAccountsResponse,
    SettingsUpdateAccountInput,
    SyncAccountInput,
    SyncAccountsRequest,
    SyncAccountsResponse,
    SyncResponseAccount,
)
from src.core.schemas.notification import NotificationConfig
from src.core.tenant_context import TenantContext
from src.core.webhooks.registration import accept_push_notification_config
from src.services.notification_proof_service import (
    ChallengeSigning,
    NotificationProofService,
    get_notification_proof_service,
)

if TYPE_CHECKING:
    from adcp.types import Setup

    from src.core.schemas import Error

logger = logging.getLogger(__name__)

#: Either sync_accounts entry shape: the provisioning trio (brand/operator/
#: billing) or the account-reference settings-update branch. Typed here (not
#: Any) so a resolver written for one branch cannot silently accept the other's
#: entry -- exactly the class of bug _FIELD_POLICY exists to prevent.
SyncEntry = SyncAccountInput | SettingsUpdateAccountInput

#: Either account-reference shape a settings-update entry's ``account`` field
#: carries: the seller-assigned handle (AccountReference1) or the natural key
#: (AccountReference2).
AccountRef = AccountReference1 | AccountReference2


def _encode_cursor(offset: int) -> str:
    """Encode an offset as a base64 cursor string."""
    return base64.b64encode(str(offset).encode()).decode()


def _decode_cursor(cursor: str) -> int:
    """Decode a base64 cursor string to an offset. Returns 0 for invalid cursors."""
    try:
        return int(base64.b64decode(cursor).decode())
    except Exception:
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
    status_filter = req.status
    if status_filter is not None:
        status_str = enum_value(status_filter)
        db_accounts = [a for a in db_accounts if a.status == status_str]

    sandbox_filter = req.sandbox
    if sandbox_filter is not None:
        db_accounts = [a for a in db_accounts if a.sandbox == sandbox_filter]

    account_filter = req.account
    if account_filter is not None:
        # account_filter is always AccountReference (a RootModel) when present.
        db_accounts = [a for a in db_accounts if _matches_account_ref(a, account_filter.root)]

    return db_accounts


def _list_accounts_impl(
    req: ListAccountsRequest | None,
    identity: ResolvedIdentity,
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

    # BR-RULE-055 INV-3: an unauthenticated caller is refused by the boundary; the
    # ResolvedIdentity this takes carries a principal by type.
    principal_id = identity.principal.principal_id
    tenant = identity.tenant
    tenant_id = tenant.tenant_id

    with AccountUoW(tenant_id) as uow:
        assert uow.accounts is not None
        # BR-RULE-054: agent-scoped results
        db_accounts = uow.accounts.list_for_agent(principal_id)
        db_accounts = _apply_list_account_filters(db_accounts, req)

        # Sort for deterministic pagination
        db_accounts.sort(key=lambda a: a.account_id)

        # Convert ORM models to schema models while session is alive
        schema_accounts = [account_from_row(a) for a in db_accounts]

    # Apply pagination after conversion
    paginated, pagination_resp = _apply_pagination(schema_accounts, getattr(req, "pagination", None))

    return ListAccountsResponse(
        accounts=paginated,
        pagination=pagination_resp,
        message=f"Found {len(paginated)} account{'s' if len(paginated) != 1 else ''}.",
    )


# ---------------------------------------------------------------------------
# Shared request builder
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# MCP wrapper
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# A2A raw wrapper
# ---------------------------------------------------------------------------


# ===========================================================================
# sync_accounts — upsert accounts by natural key (BR-RULE-056..062)
# ===========================================================================


def _generate_account_name(brand_domain: str, operator: str, brand_id: str | None = None) -> str:
    """Generate a human-readable account name from brand + operator."""
    brand_part = f"{brand_domain}:{brand_id}" if brand_id else brand_domain
    return f"{brand_part} c/o {operator}"


def _enum_to_str(val: object) -> str | None:
    """Extract string value from an enum or return as-is. Returns None for None."""
    return enum_value(val)


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
    return True, submitted or []


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
        return False, getattr(existing, "governance_agents", None)
    return True, submitted


def _resolve_billing_entity(entry: SyncEntry, existing: DBAccount | None) -> tuple[bool, object]:
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
        return False, getattr(existing, "billing_entity", None)
    if isinstance(submitted, dict):
        submitted = BusinessEntity.model_validate(submitted)
    return True, submitted


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
    the settings-update branch already rejects it for exactly that hazard.
    """
    if existing is not None:
        return False, existing.sandbox
    return True, getattr(entry, "sandbox", None)


#: The two entry modes a sync_accounts entry can be dispatched as. Spelled as a
#: Literal rather than ``str`` because it is also the ATTRIBUTE NAME looked up on
#: a ``_FieldPolicy`` row -- a typo used to be a silent AttributeError-or-worse at
#: one of three call sites.
EntryMode = Literal["provisioning", "settings_update"]

#: What a disposition can be. Closed set, transcribed from the rows actually in
#: :data:`_FIELD_POLICY` -- every value other than ``applied`` is a declared
#: non-application and carries a citation:
#:   applied            the buyer's value reaches persistence
#:   rejected           schema-legal here, refused with a per-account error
#:   spec_forbidden     the pinned request schema does not allow it on this branch
#:   local_extension    not a spec property at all; accepted only where declared
#:   ignored_by_design  accepted and deliberately a no-op, per its citation
DispositionKind = Literal["applied", "rejected", "spec_forbidden", "local_extension", "ignored_by_design"]


@dataclass(frozen=True)
class _Disposition:
    """What a sync_accounts entry field DOES in one entry mode.

    ``applied`` is the only disposition that needs no citation: it means the
    buyer's value reaches persistence. Every other value tells the buyer their
    field will not take effect, which under the project's no-quiet-failure rule
    is acceptable only as a DECLARED decision traceable to the pinned spec — so
    the citation is required by construction, not by review discipline.

    Frozen: these rows are module-level shared state read by all three
    application sites, so a mutation would silently re-point the policy for the
    whole process.
    """

    kind: DispositionKind
    citation: str = ""


@dataclass(frozen=True)
class _FieldPolicy:
    """One row of :data:`_FIELD_POLICY`: a disposition per mode + how to apply it."""

    provisioning: _Disposition
    settings_update: _Disposition
    resolve: Callable[[SyncEntry, DBAccount | None], tuple[bool, object]] | None = None

    def for_mode(self, mode: EntryMode) -> _Disposition:
        """This row's disposition in *mode*.

        The ONE place a mode name becomes an attribute lookup. Both call sites
        used to spell ``getattr(policy, mode)`` themselves, which is how a mode
        string and a row attribute could drift apart unnoticed.
        """
        return self.provisioning if mode == "provisioning" else self.settings_update


#: Why a per-entry gate refused. The gate states the CLASS; the wire code is
#: derived from it by :data:`_FAILURE_CLASS_TO_CODE`, so a gate cannot invent a
#: code and two gates in the same class cannot drift to different ones (which is
#: exactly what the BR-UC-011 scenarios had to spell as a disjunction).
FailureClass = Literal[
    "unsupported_field",
    "billing_not_supported",
    "sandbox_not_supported",
    "notification_config_invalid",
]

#: The ONE mapping from refusal class to pinned wire code.
#:
#: Typed ``ErrorCodeT``, not ``str``: the values ARE vocabulary members, so a
#: typo or a retired code is a mypy error here rather than a ValidationError
#: discovered when some gate happens to fire at runtime.
_FAILURE_CLASS_TO_CODE: dict[FailureClass, ErrorCodeT] = {
    "unsupported_field": ErrorCode.UNSUPPORTED_FEATURE,
    "billing_not_supported": ErrorCode.BILLING_NOT_SUPPORTED,
    "sandbox_not_supported": ErrorCode.UNSUPPORTED_FEATURE,
    "notification_config_invalid": ErrorCode.VALIDATION_ERROR,
}


@dataclass(frozen=True)
class GateFailure:
    """One per-entry gate refusal, before it becomes a wire ``Error``.

    ``field`` is ENTRY-RELATIVE ("sandbox", "brand.domain",
    "notification_configs[0].url") — never request-rooted with an
    ``accounts[i].`` prefix. That is the rooting the graded contract pins
    (notification-config-event-scope.yaml grades
    ``field == 'notification_configs[0].event_types[0]'``), and it is what makes
    a pointer meaningful to the entry it describes rather than to its accidental
    position in a batch.

    There is deliberately NO ``message``/``suggestion`` here. Both used to be
    REQUIRED, so all eight construction sites had to invent buyer-facing prose --
    prose that reached no channel once the advisory started deriving its text from
    the code (salesagent-3dawm.14). Requiring a site to author a sentence nobody
    reads is the defect this epic removes, so the fields are gone rather than
    defaulted: a gate decides the CLASS (which maps to the code via
    ``_FAILURE_CLASS_TO_CODE``) and the SPECIFICS (``field``, ``details``), and
    the sentence follows from the code.
    """

    failure_class: FailureClass
    field: str | None = None
    details: ErrorDetails | None = None


def _gate_failures_to_errors(failures: list[GateFailure]) -> list["Error"]:
    """The single GateFailure -> Error conversion in this module.

    Seven gate sites used to assemble ``Error(...)`` by hand, each free to pick
    its own code, recovery and pointer rooting. They now describe WHY they
    refused and this decides how that reaches the wire.
    """
    from src.core.schemas import Error

    return [
        # message, suggestion and recovery are NOT passed: Error derives all three
        # from CODE_TABLE at validation, so the sentence a buyer reads on a gate
        # refusal comes from the same authority as every other advisory. GateFailure
        # no longer even carries prose to pass (salesagent-3dawm.13). What the gate
        # decides is the CODE and the specifics: field and details.
        Error.of(  # structural-guard: advisory per-account result in SyncAccountsResponse.errors[]
            _FAILURE_CLASS_TO_CODE[f.failure_class],
            field=f.field,
            details=f.details,
        )
        for f in failures
    ]


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
    "(offline_delivery_protocols declared unbacked, #1729). FIXME(#1729): revisit when offline delivery lands."
)


class ResolvedFields(TypedDict, total=False):
    """The field bag :func:`_resolve_entry_changes` produces, per entry mode.

    ONE walk of :data:`_FIELD_POLICY` feeds all three application sites (create,
    provisioning re-sync, settings-update), so the bag they share is worth
    naming: ``dict[str, object]`` told a reader nothing about which keys exist,
    and every consumer had to ``cast()`` a value back to the type its own
    resolver had just produced.

    ``total=False`` because a field only appears when its resolver reported a
    change -- an omitted field produces no key at all, which is what makes
    "omission is not clearance" expressible.

    Values are what the BUYER SENT, not stored shapes; the repository decides
    how they persist (``AccountRepository.serialize_field``).
    """

    billing: str | None
    payment_terms: str | None
    rate_card: str | None
    credit_limit: float | None
    sandbox: bool | None
    notification_configs: object
    governance_agents: object
    billing_entity: object


#: THE record of what every ``sync_accounts`` entry field does, per entry mode.
#:
#: This table replaces two hand-maintained allowlists (``_KNOWN_ASYMMETRIC`` and
#: ``_KNOWN_DROPPED_BY_BOTH`` in the deleted handler-symmetry guard). Those
#: encoded "undecided", which is what let a field sit in debt indefinitely and
#: let ``billing``/``sandbox``/``governance_agents`` be honored on one branch and
#: silently discarded on the other. Here every in-scope field — declared on the
#: request branches OR arriving through ``extra="allow"`` — carries an explicit
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
            entry,
            cast("list[dict[str, object]] | None", AccountRepository.persisted_value(existing, "notification_configs")),
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
            "(dist/compliance/3.1.1/domains/governance/index.yaml). Accepted on the provisioning branch "
            "only because that is how governed accounts are seeded today; retire with sync_governance.",
        ),
        resolve=_resolve_governance_agents,
    ),
    "preferred_reporting_protocol": _FieldPolicy(
        provisioning=_Disposition("ignored_by_design", _PREFERRED_PROTOCOL_CITATION),
        settings_update=_Disposition("ignored_by_design", _PREFERRED_PROTOCOL_CITATION),
    ),
}


def _disposition(field: str, mode: EntryMode) -> _Disposition:
    """The disposition ``field`` carries in ``mode`` (``provisioning``/``settings_update``)."""
    return _FIELD_POLICY[field].for_mode(mode)


def _resolve_entry_changes(entry: SyncEntry, existing: DBAccount | None, *, mode: EntryMode) -> ResolvedFields:
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
    return cast("ResolvedFields", changes)


def _rejected_field_errors(entry: SyncEntry, *, mode: EntryMode) -> list[GateFailure] | None:
    """Per-account errors for fields the table marks ``rejected`` in ``mode``.

    A ``rejected`` field is schema-LEGAL on this branch but cannot be honored, so
    the buyer must be TOLD rather than have it silently ignored (the project's
    no-quiet-failure rule). ``UNSUPPORTED_FEATURE`` over
    ``UNSUPPORTED_PROVISIONING``: the latter's enumMetadata suggestion is about
    entry SHAPE ("re-issue with the entry shape the seller supports"), and it
    already means "no account matches this reference" in this file; the former's
    is literally "check get_adcp_capabilities and remove unsupported fields",
    which is exactly the buyer action here.
    """
    failures: list[GateFailure] = []
    for field, policy in _FIELD_POLICY.items():
        if policy.for_mode(mode).kind != "rejected":
            continue
        if getattr(entry, field, None) is None:
            continue
        failures.append(
            GateFailure(
                failure_class="unsupported_field",
                field=field,
            )
        )
    return failures or None


def _account_fields_changed(db_account: DBAccount, entry: SyncEntry) -> dict[str, object]:
    """Fields a PROVISIONING re-sync changes on an existing account.

    Thin wrapper over the shared table walk, kept as a named function because the
    dry-run and write call sites both read better with it.
    """
    changes = _resolve_entry_changes(entry, db_account, mode="provisioning")
    # Only report fields whose resolved value actually differs from what is
    # persisted -- the resolvers answer "did the buyer submit this", and
    # "submitted the same value again" is not a change.
    return {
        field: value
        for field, value in changes.items()
        if AccountRepository.persisted_value(db_account, field) != AccountRepository.serialize_field(field, value)
    }


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
        notification_configs=scrub_notification_credentials(notification_configs),
        billing_entity=scrub_business_entity(billing_entity),
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
    reaches the wire (#1721 M1) -- one path here covers all six gate-check sites
    plus the settings-update-not-found and activation-proof advisories, since
    they all build their result through this function.

    No normalization step remains: ``Error`` derives ``message``/``suggestion``/
    ``recovery`` from ``CODE_TABLE`` at validation, so an advisory is already
    correct by the time it is constructed.
    """
    return _build_sync_result(
        brand=brand,
        operator=operator,
        action="failed",
        status="rejected",
        billing=billing,
        sandbox=sandbox,
        errors=errors,
    )


def _first_gate_failure(gates: Iterable[Callable[[], list[GateFailure] | None]]) -> list["Error"] | None:
    """Run per-entry gate checks in order; return the first one's errors, or None.

    Both the provisioning branch (domain/billing/sandbox/notification-configs) and
    the settings-update branch (notification-configs/rejected-fields) are a list of
    independent gate checks where the first failure short-circuits the rest --
    this is the ONE place that shape is expressed (#1721 M1; was 6
    duplicated check-then-build-then-continue blocks).
    """
    for gate in gates:
        failures = gate()
        if failures is not None:
            # The ONE conversion point: gates state a refusal class, and the
            # wire code/recovery/pointer rooting is decided here, once.
            return _gate_failures_to_errors(failures)
    return None


def _provisioning_gates(
    *,
    billing_val: str | None,
    identity: ResolvedIdentity,
    sandbox: bool | None,
    tenant: TenantContext | None,
    index: int,
    entry: SyncEntry,
    proof_failures: dict[int, list[GateFailure]],
) -> list[Callable[[], list[GateFailure] | None]]:
    """The provisioning branch's gate list, in order: billing policy (BR-RULE-059) ->
    sandbox capability (BR-RULE-209 INV-6) -> notification_configs. The first failure
    short-circuits the rest.

    ``brand.domain`` is NOT gated here. It is an identifier -- a lookup key for the
    account relationship, never dereferenced -- so ``core/brand-ref.json``'s hostname
    pattern is the whole rule, and the request model already enforces it. See
    ``_sync_accounts_impl``'s callers for why: nothing resolves a brand domain.

    A module-level function, not a per-entry closure defined inside the sync
    loop: the returned lambdas close over ITS OWN parameters (fresh on every
    call), so this adds zero complexity to ``_sync_accounts_impl`` and raises
    no ruff B023 loop-variable-closure warning (#1721 M1).
    """
    return [
        lambda: _check_billing_policy(billing_val, identity),
        lambda: _check_sandbox_capability(sandbox, tenant),
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


def _check_billing_policy(
    billing_val: str | None,
    identity: ResolvedIdentity,
) -> list[GateFailure] | None:
    """Check if the billing model is supported by the seller.

    Returns a list of Error objects if rejected, None if accepted.
    Per BR-RULE-059: unsupported billing → BILLING_NOT_SUPPORTED.
    """
    from src.core.billing_policy import resolve_supported_billing

    # BR-RULE-059 governs UNSUPPORTED billing, not OMITTED billing — an
    # omitted (None) billing is never rejected, configured tenant or not.
    if billing_val is None:
        return None

    # Read billing policy from tenant configuration (not identity).
    # Both dict and TenantContext expose .get() identically, so no branching needed.
    supported = resolve_supported_billing(identity.tenant)

    if billing_val not in supported:
        # billing-not-supported.json: supported_billing minItems 1, "Sellers MAY
        # omit this field" -- an empty resolved policy must omit the key entirely,
        # never emit a schema-invalid empty array (salesagent-hh1f review MEDIUM #1).
        # billing-not-supported.json declares both keys and `scope`'s enum, so the
        # shape carries the pin's spelling. to_wire() drops supported_billing when
        # unset, which is what the "omit the key entirely" rule asks for.
        return [
            GateFailure(
                failure_class="billing_not_supported",
                details=BillingNotSupportedDetails(scope="capability", supported_billing=supported or None),
            )
        ]
    return None


def _extract_natural_key(entry: SyncEntry) -> NaturalKey:
    """Extract natural key components from a PROVISIONING-mode sync request entry.

    Returns (brand_domain, brand_id, operator, sandbox).

    Callers dispatch settings-update entries (``entry.account`` set) to
    ``_process_settings_update_entry`` BEFORE reaching this function — an
    entry that lands here with no ``brand`` carries neither the provisioning
    trio nor an account reference, a genuinely malformed request the pinned
    3.1 spec's ``required: ["brand", "operator", "billing"]`` (provisioning
    branch) rejects as a buyer-correctable 400 (salesagent-5g8e; previously this
    branch also caught settings-update entries before that mode was
    implemented — it no longer does).

    Raises:
        AdCPValidationError: if the entry omits ``brand`` or ``operator`` --
            both REQUIRED for provisioning mode per the pinned spec. Only
            ``brand`` was checked before typing this function surfaced that
            ``operator`` is ``str | None`` on the ``SyncEntry`` union (it is
            optional on the settings-update branch) with no matching runtime
            guard here.
    """
    brand = entry.brand
    operator = entry.operator
    if brand is None or operator is None:
        raise AdCPValidationError()
    brand_domain, brand_id = brand_key_parts(brand)
    return NaturalKey.from_parts(brand_domain, brand_id, operator, entry.sandbox)


def _check_sandbox_capability(entry_sandbox: bool | None, tenant: TenantContext | None) -> list[GateFailure] | None:
    """Reject sandbox provisioning when the seller has not declared account.sandbox support.

    Mirrors the ``_check_billing_policy`` per-entry gate shape.
    BR-RULE-209 INV-6: only a seller with ``account.sandbox: true``
    (Tenant.account_sandbox) supports sandbox provisioning.

    The posture comes from :func:`resolve_account_sandbox`, the SAME resolver the
    capabilities declaration reads. That is the point: this gate ENFORCES what
    that block CLAIMS, so they must not be able to disagree.
    """
    from src.core.billing_policy import resolve_account_sandbox

    if not entry_sandbox:
        return None
    if resolve_account_sandbox(tenant):
        return None
    return [
        GateFailure(
            failure_class="sandbox_not_supported",
            field="sandbox",
        )
    ]


# Media-buy-anchored notification event types. These describe the lifecycle of a
# media buy's delivery reporting, not an account, so they do not belong on the
# account surface. There is deliberately no account-lifecycle event type either
# (no account.status_changed) -- poll list_accounts or use push_notification_config.
_MEDIA_BUY_ANCHORED_EVENT_TYPES = frozenset({"scheduled", "final", "delayed", "adjusted", "impairment"})


def _check_notification_configs(configs: Iterable[NotificationConfig] | None) -> list[GateFailure] | None:
    """Validate a submitted notification_configs array; None when it is acceptable.

    Same per-entry gate shape as ``_check_billing_policy``
    / ``_check_sandbox_capability``, and called from BOTH entry handlers so the two
    branches cannot drift.

    Check ORDER is load-bearing: the first failure decides the reported
    ``error.field``, and the scenarios pin exact pointers. Duplicates are detected
    before event scope so a duplicated entry reports its own
    ``[j].subscriber_id`` rather than some unrelated later field.

    These are per-account failures INSIDE a transport-level success -- the caller
    turns them into ``_build_failed_result``, never a transport error.
    """
    if not configs:
        return None

    seen_subscribers: set[str] = set()
    for index, config in enumerate(configs):
        subscriber_id = getattr(config, "subscriber_id", None)
        if subscriber_id in seen_subscribers:
            return [
                GateFailure(
                    failure_class="notification_config_invalid",
                    field=f"notification_configs[{index}].subscriber_id",
                )
            ]
        if subscriber_id is not None:
            seen_subscribers.add(subscriber_id)

        for event_index, event_type in enumerate(getattr(config, "event_types", None) or []):
            if enum_value(event_type) in _MEDIA_BUY_ANCHORED_EVENT_TYPES:
                return [
                    GateFailure(
                        failure_class="notification_config_invalid",
                        field=f"notification_configs[{index}].event_types[{event_index}]",
                    )
                ]

        # `notification_configs[].url` is the SAME wire contract as
        # `push-notification-config.url` (notification-config.json says so in
        # words), and its `authentication` block carries "the same precedence and
        # semantics as push-notification-config.authentication". So it is a
        # registration in exactly the sense src.core.webhooks.registration exists
        # to accept, and it goes through the ONE registration gate rather than a
        # local URL-only check.
        #
        # BOTH HALVES, not just the URL. A URL-only check accepts an
        # `HMAC-SHA256` subscriber with no usable secret -- a registration the
        # fail-closed sender can never deliver against, persisted as though it
        # were live. The gate refuses it here, naming
        # `notification_configs[j].authentication.credentials`, while the buyer's
        # sync_accounts request still exists to carry the refusal.
        #
        # Registration-time and therefore DNS-FREE -- deliberately NOT the egress
        # seam (EgressPolicy.check_registration / resolve_for_dial). A buyer may
        # register a webhook before standing the endpoint up, so resolving at
        # write time would reject legitimate registrations; the seam is the
        # SEND-time gate and re-resolves when the endpoint is actually dialled
        # (Strategy C, gh-#1697 / gh-#1589). Reachability is the activation
        # proof's job (F4c). The scheme / hostname / blocklist / IP-literal
        # verdict and the unconditional https requirement are unchanged: the gate
        # delegates its URL half to reject_unsafe_webhook_registration_url, which
        # runs over the same shared address predicate.
        #
        # The refusal CAUSE is deliberately dropped rather than reported: AdCP 3.1.1 L1
        # Security Considerations forbid echoing internal hostnames or addresses back to
        # the party that supplied the URL, and the buyer already gets the field pointer.
        # `exc.field` is the gate's own pointer -- it is built from this prefix, so
        # it names the refused half (`.url` or `.authentication.credentials`)
        # without carrying any refused host or credential into buyer-facing text.
        try:
            accept_push_notification_config(
                {
                    "url": getattr(config, "url", None),
                    "authentication": getattr(config, "authentication", None),
                },
                field_prefix=f"notification_configs[{index}]",
            )
        except AdCPValidationError as exc:
            return [
                GateFailure(
                    failure_class="notification_config_invalid",
                    field=getattr(exc, "field", None) or f"notification_configs[{index}].url",
                )
            ]
    return None


def _notification_configs_gate(entry: SyncEntry, proof_errors: list[GateFailure] | None) -> list[GateFailure] | None:
    """The notification_configs gate, shared verbatim by both sync-accounts branches.

    Runs BEFORE any write so a rejected entry leaves the persisted array
    byte-identical. When the array itself is schema-valid, falls back to the
    precomputed activation-proof errors -- the proof ran before any
    transaction opened, so a failure here still writes nothing for this entry.
    A module-level function (not a per-entry closure) so neither call site's
    complexity grows with it (#1721 M1).
    """
    errors = _check_notification_configs(getattr(entry, "notification_configs", None))
    return errors if errors is not None else proof_errors


def _resolve_settings_update_target(ref: AccountRef, repo: AccountRepository) -> DBAccount | None:
    """Resolve a settings-update AccountReference to its persisted row, if any."""
    if isinstance(ref, AccountReference1):
        return repo.get_by_id(ref.account_id)
    return repo.get_by_natural_key(NaturalKey.from_reference(ref))


def _unmatched_settings_update_result(ref: AccountRef) -> SyncResponseAccount:
    """The UNSUPPORTED_PROVISIONING failure for an unmatched account reference.

    brand/operator are REQUIRED on SyncResponseAccount. A natural-key reference
    (AccountReference2) still carries brand/operator to echo even when unmatched;
    an account_id reference (AccountReference1) carries none -- "unknown" is the
    established placeholder convention in this file for exactly that situation
    (cf. the publisher-domain placeholder above), not a fabricated real value.
    """
    from src.core.schemas import Error

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
            Error.of(  # structural-guard: advisory per-account result in SyncAccountsResponse.errors[]
                ErrorCode.UNSUPPORTED_PROVISIONING,
                details=ValidationDetails(reasons=["settings_update_never_provisions"]),
            )
        ],
    )


def _process_settings_update_entry(
    entry: SyncEntry,
    repo: AccountRepository,
    proof_errors: list[GateFailure] | None = None,
) -> SyncResponseAccount:
    """Handle a settings-update entry (keyed by AccountReference) -- update an
    EXISTING account's settable fields, NEVER provision (F1b/F1c).

    ``entry.account`` is ``RootModel[AccountReference1 | AccountReference2]``:
    ``AccountReference1`` carries ``account_id`` (seller-assigned handle);
    ``AccountReference2`` carries the natural key (``brand``/``operator``/
    ``sandbox``). An unmatched reference is rejected with
    UNSUPPORTED_PROVISIONING -- a settings-update entry MUST NOT provision a
    new account under any circumstance.

    There is ONE write path. ``dry_run`` is handled at the UoW boundary
    (``AccountUoW(..., dry_run=...)`` rolls back instead of committing), so this
    branch runs identically either way and a preview's reads see its own flushed
    writes (sync-accounts-request.json#/properties/dry_run: "preview what would
    change without applying").
    """
    if entry.account is None:
        raise AdCPConfigurationError()
    ref = entry.account.root
    existing = _resolve_settings_update_target(ref, repo)

    if existing is None:
        return _unmatched_settings_update_result(ref)

    echo_brand = existing.brand
    echo_operator = existing.operator
    # Account.brand/.operator are DB-nullable (defensive column typing) but
    # AccountRepository.build_row always sets both at creation -- an EXISTING,
    # matched account (which is what this branch always operates on) cannot
    # actually have either unset.
    if echo_brand is None:
        # accounts.brand is a NULLABLE column, so a brand-less row is a state the
        # seller's own storage permits -- an assert here was an AssertionError,
        # which is not an AdCP error at all: the buyer got an untyped 500 through
        # the generic transport lane with no code, recovery or suggestion.
        # Whatever the seller does about its own inconsistent row, the RESPONSE
        # must still be a conformant error object.
        raise AdCPConfigurationError(
            details=ConfigurationDetails(account_id=existing.account_id),
        )

    # notification_configs (shared with the provisioning branch) -> rejected-field
    # check (BR-RULE-209-family fields the table marks `rejected` on this branch --
    # schema-LEGAL, so per-account "failed", never an operation-level raise).
    gate_errors = _first_gate_failure(
        [
            lambda: _notification_configs_gate(entry, proof_errors),
            lambda: _rejected_field_errors(entry, mode="settings_update"),
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

    # The SAME table walk the provisioning branch runs -- the two branches cannot
    # disagree about which fields they apply, because neither names a field.
    resolved = _resolve_entry_changes(entry, existing, mode="settings_update")
    changes = {
        field: value
        for field, value in resolved.items()
        if AccountRepository.persisted_value(existing, field) != AccountRepository.serialize_field(field, value)
    }

    action = "unchanged"
    if changes:
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


def _proof_error(entry: SyncEntry, config: NotificationConfig) -> GateFailure:
    """The per-account refusal a failed/skipped activation produces.

    One builder for both the dry_run and challenge-failure paths. They used to be
    told apart by a ``message``/``suggestion`` pair the caller wrote; those reached
    no channel once the advisory derived its text from the code, so the parameters
    are gone (salesagent-3dawm.13). Both paths carry the same class and the same
    field pointer, which is the whole of what the buyer can act on.
    """
    return GateFailure(
        failure_class="notification_config_invalid",
        field=f"notification_configs[{_config_index(entry, config)}].url",
    )


class _ProofPreflight(NamedTuple):
    """Everything the proof pass needs from the database, read in ONE transaction."""

    #: Proof tuples already persisted as active, per entry index.
    already_proven: dict[int, set[tuple]]
    #: The account id each entry's challenge must name, per entry index.
    account_ids: dict[int, str]


def _proof_preflight(
    activating: list[tuple[int, SyncEntry, NotificationConfig]], tenant_id: str, minted_ids: dict[int, str]
) -> _ProofPreflight:
    """Read the persisted proof tuples and resolve each entry's account id.

    Its own SHORT read-only transaction, closed before any socket is opened -- the
    whole point of hoisting the proof out of the write transaction. Both facts come from
    the SAME existence lookup, so resolving them together is one read rather than two.

    The account id matters because the receiver scopes its proof to it (#1291 C2): an
    entry resolving to an existing account must name THAT id, and a provisioning-mode
    entry must name the id it is about to be CREATED with -- which is why the ids are
    minted before this pass and threaded into the create, rather than generated inside the
    write transaction after the challenge has already gone out.
    """
    already_proven: dict[int, set[tuple]] = {}
    account_ids: dict[int, str] = dict(minted_ids)
    with AccountUoW(tenant_id) as uow:
        assert uow.accounts is not None
        for index, entry, _ in activating:
            existing = _lookup_existing_for_entry(entry, uow.accounts)
            if existing is None:
                continue
            account_ids[index] = existing.account_id
            already_proven.setdefault(index, set()).update(
                _proof_tuple(c) for c in (existing.notification_configs or []) if getattr(c, "active", False)
            )
    return _ProofPreflight(already_proven=already_proven, account_ids=account_ids)


def _challenge_signing(tenant_id: str) -> ChallengeSigning | None:
    """The signing identity for this tenant's challenges, or ``None`` if there is none.

    ONE short transaction yielding both halves a conformant challenge needs -- the tenant
    row (whose stored host IS the agent identity) and the signing keys -- then closed
    before any socket opens. ``TrustRootUoW`` already pairs exactly those two repositories,
    and ``src/routes/well_known.py`` is the precedent for reading an ORM row and handing
    only resolved values out of the session that owns it.

    ``seller_agent_url`` is a PUBLISHED ``agents[]`` entry of ours, taken from
    :func:`agent_endpoint_urls` and never composed here: a receiver matches it
    byte-for-byte against our brand.json (security.mdx @ v3.1.1 :1104 step 5) and then
    resolves that entry's ``jwks_uri``.

    It does NOT vary by the transport the registration arrived on, and deliberately so.
    ``webhook-challenge.json`` defines the field as the agent "whose RFC 9421 webhook
    profile key signs this challenge and that will send subsequent webhooks" -- and both
    facts are per TENANT here: one key set, published once at the tenant's origin, and one
    webhook sender that dials the same seam whichever surface the buyer registered on.
    Every entry :func:`agent_endpoint_urls` publishes shares that origin and resolves to
    that same JWKS, so naming the protocol endpoint satisfies step 5 for a buyer that
    arrived on any transport. (A per-arrival-transport lookup is also not expressible on
    this side of the boundary: ``ResolvedIdentity`` carries no ``protocol`` -- the
    transport is a label ``invoke_tool`` holds for its observability record, and nothing
    in ``src/`` reads it off an identity.)

    Returns ``None``, never raises, for every reason we cannot honestly challenge:

    * the tenant holds no ACTIVE signing key this deployment can open on a publishable
      origin -- an unsigned challenge is one no conformant receiver can attribute to us;
    * the tenant row is gone, or this deployment publishes no ``agents[]`` entry at all,
      so there is no ``seller_agent_url`` a receiver could resolve;
    * a configuration fault in the key material (a revoked row, a failed tripwire).

    ``AdCPSalesAgentError`` is caught HERE rather than left to escape, because this runs OUTSIDE the
    per-entry error path: an exception would fail the whole ``sync_accounts`` request for
    every other entry in the batch instead of failing the one subscriber that cannot be
    proven.
    """
    from src.core.agent_identity import agent_endpoint_urls
    from src.core.database.repositories.uow import TrustRootUoW
    from src.core.exceptions import AdCPSalesAgentError
    from src.core.signing.outbound import adcp_challenge_signer

    try:
        with TrustRootUoW(tenant_id) as uow:
            assert uow.tenant_config is not None
            assert uow.signing_keys is not None
            tenant = uow.tenant_config.get_tenant()
            if tenant is None:
                return None
            seller_agent_url = agent_endpoint_urls(tenant).get(TransportProtocol.MCP)
            if seller_agent_url is None:
                logger.warning(
                    "Notification proof refused for tenant %s: this agent publishes no agents[] "
                    "entry, so no seller_agent_url a receiver could resolve exists (#1291)",
                    tenant_id,
                )
                return None
            strategy = adcp_challenge_signer(tenant_id=tenant_id, repo=uow.signing_keys, now=datetime.now(UTC))
    except AdCPSalesAgentError as exc:
        logger.warning("Notification proof refused for tenant %s: %s", tenant_id, exc)
        return None
    if strategy is None:
        return None
    return ChallengeSigning(strategy=strategy, seller_agent_url=seller_agent_url)


async def _resolve_activation_proofs(
    entries: list[SyncEntry], tenant_id: str, *, dry_run: bool, minted_ids: dict[int, str]
) -> dict[int, list[GateFailure]]:
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
        return {index: [_proof_error(entry, config)] for index, entry, config in activating}

    preflight = _proof_preflight(activating, tenant_id, minted_ids)
    # Resolved once for the whole batch: the signing identity is per TENANT, not per
    # subscriber, so building it per challenge would open one session per entry.
    signing = _challenge_signing(tenant_id)
    prover = get_notification_proof_service()
    failures: dict[int, list[GateFailure]] = {}
    budget = _PROOF_BUDGET_SECONDS

    for index, entry, config in activating:
        # Identical tuple already proven and persisted as active -- the spec permits
        # skipping re-proof, so no challenge is sent at all.
        if _proof_tuple(config) in preflight.already_proven.get(index, set()):
            continue
        proven, budget = await _prove_within_budget(
            prover, preflight.account_ids[index], config, budget, signing=signing
        )
        if not proven:
            failures.setdefault(index, []).append(_proof_error(entry, config))
    return failures


async def _prove_within_budget(
    prover: NotificationProofService,
    account_id: str,
    config: NotificationConfig,
    budget: float,
    *,
    signing: ChallengeSigning | None = None,
) -> tuple[bool, float]:
    """Run one challenge if the request-level budget allows. Returns (proven, budget left).

    An exhausted budget is "not proven" rather than an unbounded wait: the caller is
    holding an HTTP request open.

    Takes the resolved ``account_id`` rather than the entry, because the challenge body
    NAMES it and the receiver scopes its proof to it (#1291 C2). It used to be handed
    ``_entry_account_hint``, which returns a BRAND DOMAIN when the entry carries no account
    reference — fine for a log line, and not an account id at all.
    """
    if budget <= 0:
        return False, budget
    started = time.monotonic()
    proven = await prover.prove(account_id, config, signing=signing)
    return proven, budget - (time.monotonic() - started)


def _config_index(entry: SyncEntry, config: NotificationConfig) -> int:
    """Position of *config* in *entry*'s submitted array (for error.field)."""
    for index, candidate in enumerate(getattr(entry, "notification_configs", None) or []):
        if candidate is config:
            return index
    return 0


def _build_update_result(
    *, entry: SyncEntry, operator: str, state: DBAccount, changes: dict[str, object]
) -> SyncResponseAccount:
    """The ONE place a provisioning ``updated``/``unchanged`` result is built.

    ``state`` MUST be the POST-WRITE row — the row as ``repo.update_fields`` left
    it. Every reported value is read off it, so the result cannot describe a
    pre-write state.

    Historical note: before #1721 a preview branch and a live branch each called one
    builder with a byte-identical argument list and STILL diverged, because the
    row they handed it meant different things. There is now a single write path
    and a single branch — ``dry_run`` is disposed of at the UoW boundary — so the
    two-branch drift this precondition guarded against no longer has a mechanism.
    """
    if entry.brand is None:
        raise AdCPConfigurationError()
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
    """Resolve the persisted account an entry targets, in either entry mode.

    The settings-update half DELEGATES to :func:`_resolve_settings_update_target`
    rather than repeating its reference-resolution: the two answered the same
    question ("which row does this AccountReference name?") with two copies of
    the same isinstance-and-natural-key walk, so a change to reference
    resolution had to be made twice to stay correct.
    """
    ref = getattr(entry, "account", None)
    if ref is not None:
        return _resolve_settings_update_target(ref.root, repo)
    brand = getattr(entry, "brand", None)
    operator = getattr(entry, "operator", None)
    if brand is None or operator is None:
        # A malformed provisioning entry (missing brand/operator) matches no
        # existing account here; _extract_natural_key rejects it explicitly
        # later in the main loop.
        return None
    brand_domain, brand_id = brand_key_parts(brand)
    return repo.get_by_natural_key(NaturalKey.from_parts(brand_domain, brand_id, operator, entry.sandbox))


async def _sync_accounts_impl(
    req: SyncAccountsRequest,
    identity: ResolvedIdentity,
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
    # BR-RULE-055: sync requires auth (consistent with list_accounts); the boundary
    # refused an anonymous caller before this ran.
    principal_id = identity.principal.principal_id
    tenant = identity.tenant
    tenant_id = tenant.tenant_id

    # Validate non-empty accounts array. field= names WHICH input was rejected: the
    # buyer-facing sentence is derived from the code through CODE_TABLE and so cannot say
    # "accounts", which would otherwise leave the buyer with a bare "Request validation
    # failed" and no way to know what to fix.
    if not req.accounts:
        raise AdCPValidationError(field="accounts")

    # Request-level webhook ingest. `push_notification_config` is stored now and
    # dialled later, so ingest is the only point with a request left to refuse
    # into -- same reasoning and same gate as sync_creatives / create_media_buy.
    # Both halves run: an unsafe URL and a credential-less HMAC registration are
    # each refused with a correctable VALIDATION_ERROR naming its own field.
    # Runs after auth (so an unauthenticated caller still gets the auth answer
    # first) and before any write.
    if req.push_notification_config:
        accept_push_notification_config(
            req.push_notification_config,
            field_prefix="push_notification_config",
        )

    # bool() here narrows the ANNOTATION, it does not supply the default. The model
    # declares ``bool | None`` -- wider than the pinned {"type": "boolean"} -- so an
    # explicit null is still representable and two callees below want a real bool. The
    # default itself now arrives from the model, because the builder omits an unsent
    # field instead of forwarding a None over it.
    dry_run = bool(req.dry_run)
    delete_missing = req.delete_missing

    results: list[SyncResponseAccount] = []
    # Track natural keys in the payload for delete_missing
    seen_account_ids: set[str] = set()

    # Activation proof runs BEFORE the write transaction opens (see
    # _resolve_activation_proofs). Holding a Postgres transaction across an
    # outbound HTTP call is what the owner's carve-out explicitly does not cover.
    # Account ids are minted BEFORE the proof pass so the id a challenge names is the id
    # the account is created with (#1291 C2). The receiver scopes its proof to that id, so
    # generating it later — inside the write transaction, after the challenge has already
    # gone out — would invalidate the proof the moment the account existed. Minted for
    # every entry: an entry that resolves to an existing account simply never uses its id,
    # which is cheaper than needing the repository to decide.
    minted_ids = {index: AccountRepository.mint_account_id() for index in range(len(req.accounts))}

    proof_failures = await _resolve_activation_proofs(
        req.accounts,
        tenant_id,
        dry_run=dry_run,
        minted_ids=minted_ids,
    )

    # ONE write path for both branches: dry_run rolls this transaction back on clean
    # exit instead of committing it (BaseUoW). Every entry below therefore runs
    # the identical resolve/validate/write code, and each entry's reads see the
    # earlier entries' writes as flushed rows -- the in-request memory a preview
    # previously had to fake with a parallel state machine
    # (sync-accounts-request.json#/properties/dry_run @ v3.1.1).
    with AccountUoW(tenant_id, dry_run=dry_run) as uow:
        assert uow.accounts is not None
        repo = uow.accounts

        for index, entry in enumerate(req.accounts):
            # Mode-exclusivity guard (F1a): an entry carrying BOTH an account
            # reference and any provisioning-trio field violates the request
            # schema's item oneOf -- a structural, operation-level rejection,
            # not a per-account business-rule failure. Must run before any
            # dispatch; the SDK union has no real oneOf enforcement and would
            # otherwise silently parse this as the provisioning branch.
            if entry.account is not None and (
                entry.brand is not None or entry.operator is not None or entry.billing is not None
            ):
                raise AdCPValidationError(
                    details=ValidationDetails(index=index),
                    field=f"accounts[{index}]",
                )

            if entry.account is not None:
                su_result = _process_settings_update_entry(entry, repo, proof_failures.get(index))
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

            natural_key = _extract_natural_key(entry)
            brand_domain, brand_id, operator, sandbox = (
                natural_key.brand_domain,
                natural_key.brand_id,
                natural_key.operator,
                natural_key.sandbox,
            )

            billing_val = _enum_to_str(entry.billing)

            gate_errors = _first_gate_failure(
                _provisioning_gates(
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
            existing = repo.get_by_natural_key(natural_key)

            if existing is not None:
                seen_account_ids.add(existing.account_id)

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

                # The id minted before the proof pass, so a challenge that already went out
                # named THIS account (#1291 C2) rather than one generated after the fact.
                # Still the repository's mint (AccountRepository.mint_account_id) -- only
                # the MOMENT it runs moved, not who owns the format.
                account_id = minted_ids[index]
                account_name = _generate_account_name(brand_domain, operator, brand_id)

                # BR-RULE-060: determine approval status from tenant config.
                # account_approval_mode is a distinct field from creative approval_mode
                # (BR-RULE-037) — do NOT fall back to approval_mode.
                approval_mode = tenant.account_approval_mode
                setup = _build_setup_for_approval(approval_mode or "auto", tenant_id)
                initial_status = "pending_approval" if setup else "active"

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
        # Runs identically on both branches: dry_run "returns what would be
        # created/updated/deactivated" (v3.1.1
        # sync-accounts-request.json#/properties/dry_run), so a preview that
        # walked nothing would tell the buyer none of their accounts close. The
        # deactivation is a write like any other -- the UoW rolls it back.
        if delete_missing:
            agent_accounts = repo.list_by_principal(principal_id)
            for db_acct in agent_accounts:
                if db_acct.account_id not in seen_account_ids:
                    repo.update_status(db_acct.account_id, "closed")
                    if db_acct.brand is None:
                        # Same seller-side inconsistency as the settings-update
                        # branch: a NULLABLE column the buyer cannot fix.
                        raise AdCPConfigurationError(
                            details=ConfigurationDetails(account_id=db_acct.account_id),
                        )
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
        message=f"Synced {len(results)} account{'s' if len(results) != 1 else ''}{' (dry run)' if dry_run else ''}.",
    )


# ---------------------------------------------------------------------------
# sync_accounts shared request builder
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# sync_accounts MCP wrapper
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# sync_accounts A2A raw wrapper
# ---------------------------------------------------------------------------
