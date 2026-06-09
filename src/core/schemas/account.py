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
from adcp.types import Setup as LibrarySetup
from adcp.types import SyncAccountsRequest as LibrarySyncAccountsRequest
from adcp.types.aliases import SyncAccountsSuccessResponse as LibrarySyncAccountsSuccess
from adcp.types.generated_poc.core.brand_ref import BrandReference as LibraryBrandReference
from pydantic import ConfigDict

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

    accounts: list[Account] = []  # type: ignore[assignment]  # Pattern #4: use local Account subclass

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
    """

    brand: LibraryBrandReference | None = None
    operator: str | None = None
    action: str | None = None
    status: str | None = None
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


__all__ = [
    "Account",
    "ListAccountsRequest",
    "ListAccountsResponse",
    "SyncAccountsRequest",
    "SyncAccountsResponse",
    "SyncResponseAccount",
]
