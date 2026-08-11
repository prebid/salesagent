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
from adcp.types import ContextObject as LibraryContextObject
from adcp.types import Error as LibraryError
from adcp.types import ListAccountsRequest as LibraryListAccountsRequest
from adcp.types import ListAccountsResponse as LibraryListAccountsResponse
from adcp.types import NotificationConfig as LibraryNotificationConfig
from adcp.types import Setup as LibrarySetup
from adcp.types import SyncAccountsRequest as LibrarySyncAccountsRequest
from adcp.types.aliases import SyncAccountsSuccessResponse as LibrarySyncAccountsSuccess
from adcp.types.generated_poc.core.brand_ref import BrandReference as LibraryBrandReference
from adcp.types.generated_poc.core.business_entity import BusinessEntity as LibraryBusinessEntity
from pydantic import ConfigDict, model_validator

from src.core.config import get_pydantic_extra_mode
from src.core.schemas._base import NestedModelSerializerMixin, SalesAgentBaseModel, validate_idempotency_key_shape

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

    Library provides: account, status, pagination, sandbox, context, ext.
    idempotency_key added locally -- the library type doesn't declare it
    (unlike SyncAccountsRequest), but v3.1.1's read-tool-idempotency.yaml
    compliance phase requires read tools to tolerate it (salesagent-tm97 F5;
    previously rejected under Pattern #7 extra=forbid).
    """

    model_config = ConfigDict(extra=get_pydantic_extra_mode())

    idempotency_key: str | None = None


class SyncAccountsRequest(LibrarySyncAccountsRequest):
    """Extends library SyncAccountsRequest.

    Library provides: idempotency_key, accounts, delete_missing, dry_run,
    push_notification_config, context, ext.
    """

    model_config = ConfigDict(extra=get_pydantic_extra_mode())

    # sync-accounts-request.json 3.1.1 lists idempotency_key in /required.  Override as
    # optional: every existing keyless caller would otherwise break, and the field is inert
    # until sync_accounts consumes it through the idempotency-attempt machinery.  Tightening
    # to required belongs with that work, not here.  What is NOT optional is the shape --
    # when a buyer does supply a key, it must satisfy the spec constraint on every transport.
    idempotency_key: str | None = None  # type: ignore[assignment]

    @model_validator(mode="after")
    def _check_idempotency_key(self):
        """Reject a malformed idempotency_key with VALIDATION_ERROR (AdCP 16-255).

        Same duty as the media-buy requests (_base.py) -- validating on the model is what
        makes every transport reject an out-of-spec key identically, instead of each
        wrapper deciding for itself.
        """
        validate_idempotency_key_shape(self.idempotency_key)
        return self


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
    payment_terms: str | None = None
    sandbox: bool | None = None
    errors: list[LibraryError] | None = None
    setup: LibrarySetup | None = None
    # #1592 T2: the applied notification subscriber set, echoed on created/updated/
    # unchanged. None omits the field ("never configured"); [] is emitted as an
    # empty array ("cleared") -- the two are different states to the buyer.
    # authentication.credentials is write-only and is stripped before this is built
    # (see _scrub_notification_credentials in src/core/tools/accounts.py).
    notification_configs: list[LibraryNotificationConfig] | None = None
    # "Echoed from the request. Sellers MAY add fields the agent omitted ... but
    # MUST NOT return data from a different entity. Bank details are omitted
    # (write-only)" (v3.1.1 sync-accounts-response.json, accounts.items.
    # billing_entity). The bank strip happens in _build_sync_result via
    # _scrub_business_entity, the single place a persisted entity becomes a
    # response object.
    billing_entity: LibraryBusinessEntity | None = None


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


__all__ = [
    "Account",
    "ListAccountsRequest",
    "ListAccountsResponse",
    "SyncAccountsRequest",
    "SyncAccountsResponse",
    "SyncResponseAccount",
]
