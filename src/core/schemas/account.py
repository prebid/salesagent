"""Account-related Pydantic schemas.

Extends adcp library account types per pattern #1 (schema inheritance).
All classes are re-exported from ``src.core.schemas`` for backward compatibility.

beads: salesagent-x79

SDK 5.7 type:ignore tracking (adcontextprotocol/adcp-client-python#913):
- [misc] on line ~127: SyncAccountsResponse class def. Pydantic metaclass
  interaction in SDK hierarchy; permanent.
- [assignment] on line ~79: idempotency_key override (required -> optional).
  Architectural; permanent.
"""

from typing import Any

from adcp.types import Account as LibraryAccountDomain
from adcp.types import AccountReference as LibraryAccountReference
from adcp.types import ContextObject as LibraryContextObject
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

from src.core.config import get_pydantic_extra_mode
from src.core.schemas._base import NestedModelSerializerMixin, SalesAgentBaseModel

# ---------------------------------------------------------------------------
# Core domain Account (used in ListAccountsResponse.accounts)
# ---------------------------------------------------------------------------


class Account(LibraryAccountDomain):
    """Extends library Account with salesagent model_config.

    Library provides: account_id, name, advertiser, billing_proxy, status,
    brand, operator, billing, rate_card, payment_terms, credit_limit, setup,
    account_scope, governance_agents, sandbox, ext.
    """

    model_config = ConfigDict(extra=get_pydantic_extra_mode())

    # POST-S3: Buyer knows advertiser, rate_card, and payment_terms.
    # Library model_dump defaults exclude_none=True which strips these when
    # None.  Override to always include them so callers can distinguish
    # "field absent" from "field=null".
    _ALWAYS_INCLUDE = {"advertiser", "rate_card", "payment_terms"}

    def model_dump(self, **kwargs: Any) -> dict[str, Any]:
        result = super().model_dump(**kwargs)
        for field in self._ALWAYS_INCLUDE:
            if field not in result:
                result[field] = getattr(self, field, None)
        return result


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


class SyncAccountsResponse(NestedModelSerializerMixin, LibrarySyncAccountsSuccess):  # type: ignore[misc]
    """Extends library SyncAccountsResponse success variant.

    adcp 3.10: SyncAccountsResponse is a union TypeAlias (not RootModel).
    Since the error variant is never constructed (ToolError handles failures),
    we subclass the success variant directly.

    SDK 5.7 collapsed the success envelope to just `status`. Fields previously
    inherited (accounts, dry_run, context, ext) are now declared locally.
    """

    model_config = ConfigDict(extra=get_pydantic_extra_mode())

    # SDK 5.7 removed these from the parent — declare locally.
    # Typed as SyncResponseAccount for proper deserialization on transport roundtrip.
    # `accounts` is REQUIRED (no default): AdCP 3.1 sync-accounts-response is
    # oneOf(SyncAccountsSuccess requires `accounts` | SyncAccountsError requires
    # `errors`). This model is the success variant, so omitting `accounts`
    # entirely is invalid (it would be neither a valid success nor error). May
    # be an empty list for a zero-account sync, but the field must be present.
    accounts: list[SyncResponseAccount]
    dry_run: bool | None = None
    context: LibraryContextObject | dict[str, Any] | None = None
    ext: dict[str, Any] | None = None

    def __str__(self) -> str:
        """Return human-readable summary message for protocol envelope."""
        count = len(self.accounts) if self.accounts else 0
        dry_run_note = " (dry run)" if self.dry_run else ""
        return f"Synced {count} account{'s' if count != 1 else ''}{dry_run_note}."


# ---------------------------------------------------------------------------
# sync_governance — bind a governance agent per account (UC-030, #1329)
# ---------------------------------------------------------------------------


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
    def _require_https_agent_urls(self) -> "SyncGovernanceRequest":
        """Enforce the schema's ``url: ^https://`` constraint on governance agents.

        The pinned 3.1.1 request schema marks the agent ``url`` ``pattern: ^https://``,
        but the generated ``AnyUrl`` field does not carry that constraint (SDK codegen
        gap) — an ``http://`` url would otherwise slip through. The spec is
        authoritative, so we enforce it here at construction, uniformly across every
        transport (MCP/A2A/REST all build this type).
        """
        for account in self.accounts:
            for agent in account.governance_agents:
                if not str(agent.url).startswith("https://"):
                    raise ValueError(
                        f"governance agent url must use https:// (field: governance_agents[].url, got '{agent.url}')"
                    )
        return self


class SyncedGovernanceAgent(SalesAgentBaseModel):
    """A governance agent as echoed on the sync_governance response.

    URL-only by construction. The request-side agent carries ``authentication``
    (schemes + credentials); the response MUST NOT echo credentials
    (sync-governance-response.json success ``governance_agents.items`` requires
    only ``url``). Modelling the echo with a url-only type makes that a
    structural guarantee, not a call-site discipline.
    """

    url: str


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
    status: str
    governance_agents: list[SyncedGovernanceAgent] | None = None
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
    "SyncedGovernanceAgent",
    "SyncGovernanceRequest",
    "SyncGovernanceResponse",
    "SyncGovernanceResponseAccount",
    "SyncResponseAccount",
]
