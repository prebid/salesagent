"""Account-related Pydantic schemas.

Extends adcp library account types per pattern #1 (schema inheritance).
All classes are re-exported from ``src.core.schemas`` for backward compatibility.

beads: salesagent-x79
"""

import uuid
from typing import Any

from adcp.types import Error, Setup
from adcp.types import ListAccountsRequest as LibraryListAccountsRequest
from adcp.types import ListAccountsResponse as LibraryListAccountsResponse
from adcp.types import SyncAccountsRequest as LibrarySyncAccountsRequest
from adcp.types.base import AdCPBaseModel
from adcp.types.generated_poc.account.sync_accounts_response import (
    SyncAccountsResponse1 as LibrarySyncAccountsSuccess,
)
from adcp.types.generated_poc.core.account_with_authorization import (
    AccountWithAuthorization as LibraryAccountDomain,
)
from adcp.types.generated_poc.core.brand_ref import BrandReference
from pydantic import ConfigDict, Field, field_validator

from src.core.config import get_pydantic_extra_mode
from src.core.schemas._base import NestedModelSerializerMixin

# ---------------------------------------------------------------------------
# Core domain Account (used in ListAccountsResponse.accounts)
# ---------------------------------------------------------------------------


class Account(LibraryAccountDomain):
    """Extends library Account with salesagent model_config.

    Library provides: account_id, name, advertiser, billing_proxy, status,
    brand, operator, billing, rate_card, payment_terms, credit_limit, setup,
    account_scope, governance_agents, sandbox, authorization, ext.
    """

    model_config = ConfigDict(extra=get_pydantic_extra_mode())

    def model_dump(self, **kwargs: Any) -> dict[str, Any]:
        """Keep optional account fields visible in default serialized output."""
        result = super().model_dump(**kwargs)
        if not kwargs.get("exclude_none"):
            for field in ("advertiser", "rate_card", "payment_terms"):
                result.setdefault(field, getattr(self, field, None))
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

    Library provides: accounts, delete_missing, dry_run, idempotency_key,
    push_notification_config, context, ext.

    adcp 4.4.3 made ``idempotency_key`` required. Auto-default to a fresh
    UUID so pre-v3 callers (and most internal tests) keep working without
    minting a key by hand.
    """

    model_config = ConfigDict(extra=get_pydantic_extra_mode())

    idempotency_key: str = Field(
        default_factory=lambda: f"idem_{uuid.uuid4()}",
        description="Client-generated unique key. Auto-defaults to a fresh UUID when omitted.",
        min_length=16,
        max_length=255,
        pattern=r"^[A-Za-z0-9_.:-]{16,255}$",
    )


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class SyncResponseAccount(AdCPBaseModel):
    """One per-account result row in the sync_accounts response.

    adcp 5.7 collapses the generated sync_accounts response to a loose
    protocol envelope, so there is no longer a generated row model to extend.
    Keep the row shape explicit locally because SalesAgent still emits the
    historical ``accounts[]`` envelope and the SDK preserves it as extra data.
    """

    model_config = ConfigDict(extra=get_pydantic_extra_mode())

    brand: BrandReference
    operator: str
    action: str
    status: str
    account_id: str | None = None
    name: str | None = None
    billing: str | None = None
    sandbox: bool | None = None
    errors: list[Error] | None = None
    setup: Setup | None = None
    notification_configs: list[Any] | None = None


class ListAccountsResponse(NestedModelSerializerMixin, LibraryListAccountsResponse):
    """Extends library ListAccountsResponse.

    Library provides: accounts, errors, pagination, context, ext.
    NestedModelSerializerMixin ensures nested Account objects serialize correctly.
    """

    model_config = ConfigDict(extra=get_pydantic_extra_mode())

    @field_validator("accounts", mode="after")
    @classmethod
    def _coerce_accounts_to_local_schema(cls, accounts: list[LibraryAccountDomain]) -> list[LibraryAccountDomain]:
        """Use the local Account subclass so nested dumps include stable optional keys."""
        return [
            account if isinstance(account, Account) else Account.model_validate(account.model_dump())
            for account in accounts
        ]

    def __str__(self) -> str:
        """Return human-readable summary message for protocol envelope."""
        count = len(self.accounts) if self.accounts else 0
        return f"Found {count} account{'s' if count != 1 else ''}."


class SyncAccountsResponse(NestedModelSerializerMixin, LibrarySyncAccountsSuccess):
    """Extends library SyncAccountsResponse success variant.

    adcp 3.10: SyncAccountsResponse is a union TypeAlias (not RootModel).
    Since the error variant is never constructed (ToolError handles failures),
    we subclass the success variant directly. Fields (accounts, dry_run,
    context, ext) are inherited.
    """

    model_config = ConfigDict(extra=get_pydantic_extra_mode())

    # SyncResponseAccount is an independent local row model (extends AdCPBaseModel,
    # NOT a subclass of the parent's element type). adcp 6.3 retyped the parent's
    # accounts to list[Account], so redeclaring the element type is an incompatible
    # override and mypy needs the ignore. Safe because this response field is only
    # ever constructed and serialized with SyncResponseAccount rows, never read back
    # or appended to as list[Account].
    accounts: list[SyncResponseAccount]  # type: ignore[assignment]
    dry_run: bool | None = None
    context: Any | None = None

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
]
