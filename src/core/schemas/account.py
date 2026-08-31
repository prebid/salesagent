"""Account-related Pydantic schemas.

Extends adcp library account types per pattern #1 (schema inheritance).
All classes are re-exported from ``src.core.schemas`` for backward compatibility.


SDK 5.7 type:ignore tracking (adcontextprotocol/adcp-client-python#913):
- [misc] on line ~127: SyncAccountsResponse class def. Pydantic metaclass
  interaction in SDK hierarchy; permanent.
- [assignment] on line ~79: idempotency_key override (required -> optional).
  Architectural; permanent.
"""

from typing import ClassVar, Literal, NoReturn

from adcp.types import Account as LibraryAccountDomain
from adcp.types import AccountReference as LibraryAccountReference
from adcp.types import CoreGovernanceAgent as LibraryGovernanceAgent
from adcp.types import Error as LibraryError
from adcp.types import ListAccountsRequest as LibraryListAccountsRequest
from adcp.types import ListAccountsResponse as LibraryListAccountsResponse
from adcp.types import Setup as LibrarySetup
from adcp.types import SyncAccountsRequest as LibrarySyncAccountsRequest
from adcp.types import SyncGovernanceRequest as LibrarySyncGovernanceRequest
from adcp.types import SyncGovernanceResponse as LibrarySyncGovernanceResponse
from adcp.types.aliases import SyncAccountsSuccessResponse as LibrarySyncAccountsSuccess
from adcp.types.generated_poc.core.brand_ref import BrandReference as LibraryBrandReference
from pydantic import ConfigDict, model_validator
from pydantic_core import InitErrorDetails, PydanticCustomError
from pydantic_core import ValidationError as CoreValidationError

from src.core.config import get_pydantic_extra_mode
from src.core.schemas._base import (
    AlwaysIncludeFieldsMixin,
    CompletedTaskStatusMixin,
    NestedModelSerializerMixin,
    SalesAgentBaseModel,
)
from src.core.webhook_validator import webhook_url_for_log

# ---------------------------------------------------------------------------
# Core domain Account (used in ListAccountsResponse.accounts)
# ---------------------------------------------------------------------------


class Account(AlwaysIncludeFieldsMixin, LibraryAccountDomain):
    """Extends library Account with salesagent model_config.

    Library provides: account_id, name, advertiser, billing_proxy, status,
    brand, operator, billing, rate_card, payment_terms, credit_limit, setup,
    account_scope, governance_agents, sandbox, ext.
    """

    model_config = ConfigDict(extra=get_pydantic_extra_mode())

    # Derived from the pin, not declared. core/account.json types advertiser,
    # rate_card and payment_terms as plain non-nullable optionals and lists none of
    # them in `required`, so the intersection is empty and all three are omitted
    # when null. Declaring them always-include emitted a document that FAILED
    # validation against that schema, on list_accounts — a registered A2A skill.
    _PINNED_SCHEMA_REF: ClassVar[str] = "core/account.json"


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------


class ListAccountsRequest(LibraryListAccountsRequest):
    """Extends library ListAccountsRequest.

    Library provides: status, pagination, sandbox, context, ext.
    """

    model_config = ConfigDict(extra=get_pydantic_extra_mode())


class SyncAccountsRequest(LibrarySyncAccountsRequest):
    """Extends library SyncAccountsRequest.

    Library provides: idempotency_key, accounts, delete_missing, dry_run,
    push_notification_config, context, ext.
    """

    model_config = ConfigDict(extra=get_pydantic_extra_mode())

    # adcp 4.3 makes idempotency_key required.  Override as optional —
    # generated at the transport boundary when not supplied by the caller.
    idempotency_key: str | None = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class ListAccountsResponse(NestedModelSerializerMixin, LibraryListAccountsResponse):
    """Extends library ListAccountsResponse.

    Library provides: accounts, errors, pagination, context, ext.
    NestedModelSerializerMixin ensures nested Account objects serialize correctly.
    Accounts field redeclared for Pattern #4 (nested serialization with local subclass).
    """

    model_config = ConfigDict(extra=get_pydantic_extra_mode())

    # Required (no default): pinned 3.1 list-accounts-response marks 'accounts'
    # required. Redeclared for Pattern #4 (nested serialization with local subclass)
    # and to enforce the spec-required field (#1399 Plan-B).
    accounts: list[Account]  # type: ignore[assignment]

    def __str__(self) -> str:
        """Return human-readable summary message for protocol envelope."""
        count = len(self.accounts) if self.accounts else 0
        return f"Found {count} account{'s' if count != 1 else ''}."


class SyncResponseAccount(SalesAgentBaseModel):
    """Per-account result in a sync_accounts response.

    SDK 4.3 provided this as adcp.types.generated_poc.account.sync_accounts_response.Account.
    SDK 5.7 restructured the response; we now own this model.

    Fields are typed with adcp library models (Error, Setup) so Pydantic
    reconstructs them properly on transport roundtrip (A2A/MCP/REST).

    brand/operator/action/status are REQUIRED per the pinned AdCP schema
    (adcontextprotocol/adcp@04f59d2d5, sync-accounts-response success variant,
    accounts.items.required) — the model enforces them rather than relying on every
    call site. billing stays optional (not in the schema's required set).
    """

    brand: LibraryBrandReference
    operator: str
    action: str
    status: str
    account_id: str | None = None
    name: str | None = None
    billing: str | None = None
    sandbox: bool | None = None
    errors: list[LibraryError] | None = None
    setup: LibrarySetup | None = None


class SyncAccountsResponse(
    CompletedTaskStatusMixin,
    NestedModelSerializerMixin,
    LibrarySyncAccountsSuccess,  # type: ignore[misc]
):
    """Extends library SyncAccountsResponse success variant.

    adcp 3.10: SyncAccountsResponse is a union TypeAlias (not RootModel).
    Since the error variant is never constructed (ToolError handles failures),
    we subclass the success variant directly.

    SDK 5.7 had collapsed the success envelope to just `status`, and this class
    carried local copies of accounts/dry_run/context/ext as a result. adcp 6.6
    re-added all four, typed, so only `accounts` is still declared here — and only
    to narrow its item type (Pattern #4). The rest are inherited.
    """

    model_config = ConfigDict(extra=get_pydantic_extra_mode())

    # Protocol-envelope `status` comes from CompletedTaskStatusMixin (composed above).
    # account/sync-accounts-response.json composes the envelope arm via a top-level
    # allOf, and this class is a TEMPORARY adopter: at adcp 6.6
    # SyncAccountsSuccessResponse has no ProtocolEnvelope in its MRO and no status
    # field, so the mixin is ADDITIVE here and deletes as a no-op the day the SDK
    # ships the field.
    #
    # "completed" is invariant rather than a TaskStatus: the pinned response's oneOf
    # arms are [['accounts'], ['errors']] with NO submitted arm, the error variant is
    # never constructed here, and sync_accounts models approval PER ACCOUNT
    # (src/core/tools/accounts.py) rather than per task — so the task itself always
    # completes. Same shape and same obsolescence condition as SyncCreativesResponse
    # (src/core/schemas/creative.py, GH #1710).

    # Pattern #4: narrowed to SyncResponseAccount for proper deserialization on
    # transport roundtrip. `accounts` is REQUIRED (no default): AdCP 3.1
    # sync-accounts-response is oneOf(SyncAccountsSuccess requires `accounts` |
    # SyncAccountsError requires `errors`). This model is the success variant, so
    # omitting `accounts` entirely is invalid (it would be neither a valid success
    # nor error). May be an empty list for a zero-account sync, but must be present.
    #
    # dry_run / context / ext are NOT redeclared. They carried a stale "SDK 5.7
    # removed these from the parent" note; adcp 6.6 re-added all three, typed, and
    # the SyncCreativesResponse twin already inherits them. Two of the local copies
    # were also strictly worse: `context` widened to accept a raw dict although
    # SyncAccountsRequest.context is itself a ContextObject, and `ext` weakened the
    # parent's ExtensionObject to a bare dict while no construction site passes it.
    accounts: list[SyncResponseAccount]

    def __str__(self) -> str:
        """Return human-readable summary message for protocol envelope."""
        count = len(self.accounts) if self.accounts else 0
        dry_run_note = " (dry run)" if self.dry_run else ""
        return f"Synced {count} account{'s' if count != 1 else ''}{dry_run_note}."


# ---------------------------------------------------------------------------
# sync_governance — bind a governance agent per account (UC-030, #1329)
# ---------------------------------------------------------------------------


def _raise_governance_url_error(loc: tuple[str | int, ...], message: str, input_value: str) -> NoReturn:
    """Raise a field-located ``ValidationError`` for a rejected governance agent url.

    A ``model_validator(mode="after")`` that raises a bare ``ValueError`` produces
    ``loc=()`` — the buyer wire then carries ``field=""`` on both envelope layers and an
    empty bullet. Emitting an explicit ``loc`` via ``from_exception_data`` restores the
    ``accounts[i].governance_agents[j].url`` field pointer so error consumers (and the
    BR-UC-030 wire steps) can pin ``field=`` instead of a free-text message match
    (#1329). The rendered message must be a literal (no ``{}`` placeholders) —
    ``PydanticCustomError`` treats the second arg as a template.
    """
    raise CoreValidationError.from_exception_data(
        "SyncGovernanceRequest",
        [InitErrorDetails(type=PydanticCustomError("value_error", message), loc=loc, input=input_value)],
    )


class SyncGovernanceRequest(LibrarySyncGovernanceRequest):
    """Extends library SyncGovernanceRequest.

    Library provides: idempotency_key (required), accounts, context, ext.
    Per the pinned 3.1.1 schema (account/sync-governance-request.json),
    ``idempotency_key`` is REQUIRED (``x-mutates-state: true``) and each
    ``accounts[]`` entry pairs an ``AccountReference`` with a ``governance_agents``
    array of ``maxItems: 1``. Unlike SyncAccountsRequest, we do NOT relax
    ``idempotency_key`` to optional: UC-030 grades rejection when it is absent,
    so a missing key must surface as a validation error, not be auto-generated.
    """

    model_config = ConfigDict(extra=get_pydantic_extra_mode())

    @model_validator(mode="after")
    def _validate_governance_agent_url_shape(self) -> "SyncGovernanceRequest":
        """Enforce the ``^https://`` url SHAPE the SDK codegen drops (uniform across transports).

        The pinned 3.1.1 request schema marks the agent ``url`` ``pattern: ^https://``, but the
        generated ``AnyUrl`` field does not carry that constraint (SDK codegen gap), so an
        ``http://`` url would slip through. This is a pure schema-SHAPE check — env-independent —
        so it stays on the type as a field-located ``VALIDATION_ERROR`` at construction. The
        rendered message shows only the sanitized url (``webhook_url_for_log`` strips userinfo +
        query + fragment), so it can never echo a credential.

        The credential-in-args (userinfo) and SSRF-host policies are NOT here: they raise typed
        AdCPErrors — ``CREDENTIAL_IN_ARGS`` (terminal) for embedded userinfo, and the repo-owned
        webhook-registration SSRF gate for disallowed hosts — so they live in
        ``governance.build_sync_governance_request`` AFTER construction, off the type layer, as the
        ONE host-policy home shared with webhook registration (no forked policy in the model,
        #1329). The schema layer must not depend on ``src/core/security``.
        """
        for a_idx, account in enumerate(self.accounts):
            for g_idx, agent in enumerate(account.governance_agents):
                url_str = str(agent.url)
                if not url_str.startswith("https://"):
                    loc = ("accounts", a_idx, "governance_agents", g_idx, "url")
                    safe_url = webhook_url_for_log(url_str)
                    _raise_governance_url_error(
                        loc, f"governance agent url must use https:// (got '{safe_url}')", safe_url
                    )
        return self


class SyncGovernanceResponseAccount(SalesAgentBaseModel):
    """Per-account result in a sync_governance response.

    The SDK collapsed the response ``oneOf`` into a flat envelope with a bare
    ``payload`` dict (no typed ``accounts``), so — mirroring SyncResponseAccount
    — we own this model. Shape from the pinned 3.1.1 success variant
    (sync-governance-response.json ``accounts.items``): ``account`` echoed,
    ``status`` in {synced, failed}, ``governance_agents`` present on synced
    entries (url only), per-account ``errors`` present on failed entries.
    """

    account: LibraryAccountReference
    # Two-member enum per the pinned sync-governance-response.json (status.enum
    # ["synced","failed"]); a Literal makes the constraint structural rather than
    # call-site discipline.
    status: Literal["synced", "failed"]
    # The echoed agent is the SDK's url-only ``CoreGovernanceAgent`` (Pattern #1): the
    # response MUST NOT echo credentials (sync-governance-response.json success
    # ``governance_agents.items`` = url only), and the SDK ships exactly that url-only
    # type — reusing it makes credential-strip a structural guarantee AND keeps the echo
    # in lockstep with the pinned schema instead of a hand-maintained parallel type.
    governance_agents: list[LibraryGovernanceAgent] | None = None
    errors: list[LibraryError] | None = None


class SyncGovernanceResponse(NestedModelSerializerMixin, LibrarySyncGovernanceResponse):
    """Extends library SyncGovernanceResponse (success variant).

    The library type is the flattened protocol envelope; ``accounts`` is
    re-declared locally (Pattern #4 nested serialization) and is REQUIRED on
    the success variant (sync-governance-response.json ``oneOf`` requires
    ``accounts`` on success | ``errors`` on error). ``status`` defaults to
    ``completed`` on the library base — the synchronous success path — so it is
    not set here. ``context`` (inherited from the protocol envelope) is echoed
    unchanged, which the specialism storyboards grade.
    """

    model_config = ConfigDict(extra=get_pydantic_extra_mode())

    accounts: list[SyncGovernanceResponseAccount]

    def __str__(self) -> str:
        """Return human-readable summary message for protocol envelope."""
        synced = sum(1 for a in self.accounts if a.status == "synced")
        total = len(self.accounts)
        return f"Synced governance for {synced}/{total} account{'s' if total != 1 else ''}."


__all__ = [
    "Account",
    "ListAccountsRequest",
    "ListAccountsResponse",
    "SyncAccountsRequest",
    "SyncAccountsResponse",
    "SyncGovernanceRequest",
    "SyncGovernanceResponse",
    "SyncGovernanceResponseAccount",
    "SyncResponseAccount",
]
