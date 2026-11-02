"""Account-related Pydantic schemas.

Extends adcp library account types per pattern #1 (schema inheritance).
All classes are re-exported from ``src.core.schemas`` for backward compatibility.


SDK 5.7 type:ignore tracking (adcontextprotocol/adcp-client-python#913):
- [misc] on line ~185: SyncAccountsResponse class def. Pydantic metaclass
  interaction in SDK hierarchy; permanent.
- [assignment] on line ~101: SyncAccountsRequest.idempotency_key override
  (required -> optional). Architectural; permanent. Note this is the SYNC
  request -- sync-accounts-request.json declares the property because a sync
  mutates. The read request declares none; see ListAccountsRequest.
"""

from typing import ClassVar

from adcp.types import Account as LibraryAccountDomain
from adcp.types import ListAccountsRequest as LibraryListAccountsRequest
from adcp.types import ListAccountsResponse as LibraryListAccountsResponse
from adcp.types import SyncAccountsRequest as LibrarySyncAccountsRequest
from adcp.types.aliases import SyncAccountsSuccessResponse as LibrarySyncAccountsSuccess
from adcp.types.generated_poc.account.sync_accounts_request import (
    Accounts as LibrarySyncAccountInput,
)
from adcp.types.generated_poc.account.sync_accounts_request import (
    Accounts1 as LibrarySettingsUpdateAccountInput,
)
from adcp.types.generated_poc.account.sync_accounts_response import (
    Account as LibraryAccount,
)  # TODO: no stable alias in adcp.types
from pydantic import ConfigDict, Field, model_validator

from src.core.config import get_pydantic_extra_mode
from src.core.schemas._base import (
    AdcpResponse,
    BuyerRequest,
    NestedModelSerializerMixin,
    validate_idempotency_key_shape,
)
from src.core.schemas.notification import NotificationConfig, PushNotificationConfig

# ---------------------------------------------------------------------------
# Core domain Account (used in ListAccountsResponse.accounts)
# ---------------------------------------------------------------------------


class Account(LibraryAccountDomain):
    """Extends library Account with salesagent model_config.

    Library provides: account_id, name, advertiser, billing_proxy, status,
    brand, operator, billing, rate_card, payment_terms, credit_limit, setup,
    account_scope, governance_agents, sandbox, ext.

    No required-nullable retention: it declares no field that is both required and
    nullable, so the mixin it used to name did nothing here. Its schema ref claimed
    otherwise for a while and emitted three explicit nulls that FAILED validation
    against core/account.json -- advertiser, rate_card and payment_terms are plain
    optionals there, listed in no ``required`` set.
    """

    model_config = ConfigDict(extra=get_pydantic_extra_mode())

    # Derived from the pin, not declared. core/account.json types advertiser,
    # rate_card and payment_terms as plain non-nullable optionals and lists none of
    # them in `required`, so the intersection is empty and all three are omitted
    # when null. Declaring them always-include emitted a document that FAILED
    # validation against that schema, on list_accounts — a registered A2A skill.


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------


class ListAccountsRequest(BuyerRequest, LibraryListAccountsRequest):
    """Extends library ListAccountsRequest.

    Library provides: account, status, pagination, sandbox, context, ext. Nothing is added:
    the field set is the pinned schema's, which is what lets the tool's advertised shape be
    derived from this model without publishing anything AdCP 3.1.1 does not define.

    ``idempotency_key`` used to be declared here, commented as read-tool-idempotency
    tolerance. The citation was right and the conclusion was backwards, and the generated
    BR-UC-011 scenario says so in its own words: account/list-accounts-request.json does NOT
    declare the property and DOES declare ``additionalProperties: true``, so the duty is
    TOLERANCE, not a declared field. Declaring it satisfied a tolerance obligation by
    inventing a spec field -- the exact defect this model is now graded against. Tolerance
    itself is already the boundary's job (critical pattern #7: production runs
    ``extra="ignore"``, so a buyer may send the key and it is ignored), and a read is
    idempotent by construction, so there is no at-most-once guarantee for a key to carry.
    Contrast SyncAccountsRequest below, where the spec DOES declare it because a sync mutates.

    """

    TAGS: ClassVar[tuple[str, ...]] = (
        "accounts",
        "billing",
        "discovery",
        "adcp",
    )

    model_config = ConfigDict(extra=get_pydantic_extra_mode())


class SyncAccountInput(LibrarySyncAccountInput):
    """The provisioning entry of ``sync_accounts`` (brand / operator / billing), per the pin.

    Redeclares only ``notification_configs``, narrowed to the local ``NotificationConfig``
    so the authentication block an entry carries is the ONE class every registration site
    names (src/core/schemas/notification.py). The parent's ``maxItems: 16`` is restated
    because the element type changed.
    """

    # The [assignment] ignore is the expected cost of narrowing a list element type (invariance).
    notification_configs: list[NotificationConfig] | None = Field(  # type: ignore[assignment]
        default=None,
        max_length=16,
        description=LibrarySyncAccountInput.model_fields["notification_configs"].description,
    )


class SettingsUpdateAccountInput(LibrarySettingsUpdateAccountInput):
    """The account-reference (settings-update) entry of ``sync_accounts``, per the pin.

    Same single redeclaration as :class:`SyncAccountInput`, for the same reason.
    """

    # The [assignment] ignore is the expected cost of narrowing a list element type (invariance).
    notification_configs: list[NotificationConfig] | None = Field(  # type: ignore[assignment]
        default=None,
        max_length=16,
        description=LibrarySettingsUpdateAccountInput.model_fields["notification_configs"].description,
    )


class SyncAccountsRequest(BuyerRequest, LibrarySyncAccountsRequest):
    """Extends library SyncAccountsRequest.

    Library provides: idempotency_key, accounts, delete_missing, dry_run,
    push_notification_config, context, ext.
    """

    TAGS: ClassVar[tuple[str, ...]] = (
        "accounts",
        "billing",
        "sync",
        "upsert",
        "adcp",
    )

    model_config = ConfigDict(extra=get_pydantic_extra_mode())

    # The two entry shapes, narrowed to the local subclasses above so every
    # notification_configs entry validates into the local NotificationConfig. Required and
    # ``maxItems: 1000`` as the parent declares; restated because the element types changed.
    # The [assignment] ignore is the expected cost of narrowing a list element type (invariance).
    accounts: list[SyncAccountInput | SettingsUpdateAccountInput] = Field(  # type: ignore[assignment]
        ...,
        max_length=1000,
        description=LibrarySyncAccountsRequest.model_fields["accounts"].description,
    )

    # Narrowed to the local class, like the media-buy and creative requests; see
    # CreateMediaBuyRequest in _base.py.
    push_notification_config: PushNotificationConfig | None = None

    # idempotency_key is INHERITED as required. The optional override that used to sit here
    # argued the field was "inert until sync_accounts consumes it through the
    # idempotency-attempt machinery", so tightening belonged "with that work, not here" --
    # and sync_accounts still does not consume it (only media_buy_create and creatives/_sync
    # reach idempotency_replay). That is the whole point: whether WE act on a field is not
    # what decides whether the buyer must send it. sync-accounts-request.json 3.1.1 lists it
    # in /required, so a request without one is not a valid request, and a model that accepts
    # it accepts something the spec does not. Deleted rather than rewritten, following
    # prkv.28 (update_media_buy) and prkv.68 (create_media_buy's account): if our model does
    # not require what the pin requires, the model is wrong.

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


class ListAccountsResponse(NestedModelSerializerMixin, LibraryListAccountsResponse, AdcpResponse):
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


class SyncResponseAccount(LibraryAccount):
    """Per-account result in a sync_accounts response (extends the pinned item).

    Every field is inherited, including the ``account_scope``, ``rate_card``,
    ``credit_limit``, ``warnings`` and ``authorization`` the hand-written version could not
    express. It also restores the pin's own typing: ``action`` and ``status`` are Literals,
    ``billing`` a BillingParty and ``payment_terms`` a PaymentTerms, where the copy had bare
    ``str`` for all four.

    Two behaviours live at the call site, not here. ``notification_configs`` distinguishes
    None ("never configured") from [] ("cleared"), and ``authentication.credentials`` is
    write-only and stripped by ``_scrub_notification_credentials``. ``billing_entity`` is
    echoed from the request with bank details removed by ``_scrub_business_entity`` -- "Bank
    details are omitted (write-only)" (v3.1.1 sync-accounts-response.json,
    accounts.items.billing_entity). Both scrubs are in src/core/tools/accounts.py.
    """


class SyncAccountsResponse(
    NestedModelSerializerMixin,
    LibrarySyncAccountsSuccess,  # type: ignore[misc]
    AdcpResponse,
):
    """Extends library SyncAccountsResponse success variant.

    adcp 3.10: SyncAccountsResponse is a union TypeAlias (not RootModel).
    Since the error variant is never constructed (ToolError handles failures),
    we subclass the success variant directly.

    ``ProtocolEnvelope`` IS INHERITED HERE AS A LOCAL WORKAROUND, for the same reason and with
    the same expiry as ``SyncCreativesResponse`` -- see that class, and
    adcontextprotocol/adcp-client-python#1136. The pinned
    ``account/sync-accounts-response.json`` composes the envelope with ``allOf``; the SDK's
    generated success branch does not inherit it, so without this base nine of its eleven fields
    are untyped and reach the wire only as pydantic extras.

    SDK 5.7 had collapsed the success envelope to just `status`, and this class
    carried local copies of accounts/dry_run/context/ext as a result. adcp 6.6
    re-added all four, typed, so only `accounts` is still declared here — and only
    to narrow its item type (Pattern #4). The rest are inherited.
    """

    model_config = ConfigDict(extra=get_pydantic_extra_mode())

    # Protocol-envelope `status` comes from ProtocolEnvelope (composed above).
    # account/sync-accounts-response.json composes the envelope branch via a top-level
    # allOf, and this class is a TEMPORARY adopter: at adcp 6.6
    # SyncAccountsSuccessResponse has no ProtocolEnvelope in its MRO and no status
    # field, so the mixin is ADDITIVE here and deletes as a no-op the day the SDK
    # ships the field.
    #
    # "completed" is invariant rather than a TaskStatus: the pinned response's oneOf
    # branches are [['accounts'], ['errors']] with NO submitted branch, the error variant is
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


__all__ = [
    "Account",
    "ListAccountsRequest",
    "ListAccountsResponse",
    "SettingsUpdateAccountInput",
    "SyncAccountInput",
    "SyncAccountsRequest",
    "SyncAccountsResponse",
    "SyncResponseAccount",
]
