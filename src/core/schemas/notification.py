"""Webhook registration schemas: one ``Authentication`` concept, two pinned strictnesses.

The pin spells the legacy ``authentication`` object inline in two schemas,
``core/notification-config.json`` and ``core/push-notification-config.json``, with no shared
``$ref``, so codegen emits two classes with the same two fields (``schemes``,
``credentials``). Pydantic validates a model-typed slot by INSTANCE, so an instance of one
generated class is refused where the other is declared. That is how ``sync_accounts``,
handing a ``NotificationConfig``'s block to the registration gate that validates a
``PushNotificationConfig``, came to refuse every authenticated registration
(salesagent-3cs7o.12, item 1).

The two pins also differ on one axis: ``credentials`` is optional in
``notification-config.json`` and required in ``push-notification-config.json``. Neither a
dump in the caller nor a class that subclasses both with the requiredness decided by base
order is acceptable: the first is the mid-flow second representation pattern 4 bans, the
second misrepresents one pin through a hidden declaration. So the two strictnesses are a
TYPE RELATION, one concept with a narrowing subtype:

* :class:`Authentication` extends the notification-config spelling and declares nothing:
  ``credentials`` optional, as that pin says. Every site that only reads a block types it
  this way.
* :class:`PushAuthentication` extends :class:`Authentication` and the push-config spelling,
  and redeclares ``credentials`` as required, as that pin says. It is the sanctioned
  redeclaration (a narrowing, optional to required), and an instance is an instance of both
  generated classes. ``src/core/security/webhook_egress.py`` constructs it where a
  credential is needed to sign; a block with no credential is an undeliverable outcome there.

The local config models narrow their slot accordingly. ``PushNotificationConfig`` adopts a
base-typed block by rebuilding the subtype from the block's own two fields, so a
``NotificationConfig``'s block reaches the registration gate without a dump and is held to
the push pin's requiredness at the pin's own pointer (``authentication.credentials``).

Wire effect: the account-level path keeps optional credentials (a credential-less block on
``accounts[i].notification_configs[j]`` is not an INVALID_REQUEST); the push path keeps them
required.

:class:`NotificationConfig` carries one further divergence, on ``event_types``, for a
related reason: the pinned field states an enum AND, in the same sentence, states that a
value outside it is reported PER ENTRY rather than refused wholesale. A model that refuses
the document cannot do the second. See that class's docstring for the weakened axis.
"""

from adcp.types import NotificationConfig as LibraryNotificationConfig
from adcp.types import PushNotificationConfig as LibraryPushNotificationConfig
from adcp.types.domains.core.notification_config import Authentication as LibraryNotificationAuthentication
from adcp.types.domains.core.push_notification_config import Authentication as LibraryPushAuthentication
from adcp.types.domains.enums.notification_type import NotificationType as LibraryNotificationType
from pydantic import ConfigDict, Field

__all__ = ["Authentication", "NotificationConfig", "PushAuthentication", "PushNotificationConfig"]


class Authentication(LibraryNotificationAuthentication):
    """The legacy ``authentication`` block as ``core/notification-config.json`` declares it.

    Declares nothing: ``schemes`` is required and ``credentials`` optional, inherited. The
    base of the concept; :class:`PushAuthentication` is its narrowing.
    """


class PushAuthentication(Authentication, LibraryPushAuthentication):
    """The same block as ``core/push-notification-config.json`` declares it: ``credentials`` required.

    A narrowing subtype: an instance is an :class:`Authentication` and an instance of both
    generated classes. The one redeclaration tightens the base's optional ``credentials`` to
    required, with the parent's own ``minLength: 32`` and description restated.

    ``from_attributes`` is what lets a BASE-typed block fill this slot. ``sync_accounts``
    hands a ``NotificationConfig``'s block -- an :class:`Authentication` -- to the
    registration gate, and pydantic refuses a base instance in a subtype slot by identity.
    With this setting pydantic reads ``schemes`` and ``credentials`` off the base instance
    and validates them against this class, so the block arrives AS the subtype and the push
    pin's required ``credentials`` still does the refusing, at
    ``authentication.credentials``. A hand-written ``mode="before"`` validator on
    ``PushNotificationConfig`` used to do exactly this by rebuilding the subtype from the
    base's two fields; pydantic's own mechanism makes it unnecessary, so it is deleted.

    ``extra="forbid"`` is RESTATED rather than inherited, and dropping it would be a silent
    widening: a ``model_config`` on a subclass REPLACES the parents' rather than merging
    with it, so declaring this config for ``from_attributes`` alone would discard the
    generated parent's ``extra="forbid"``. That setting is the pin's
    ``additionalProperties: false`` on the authentication block
    (``core/push-notification-config.json``), so losing it would let an undeclared key ride
    along on a credential block.
    """

    model_config = ConfigDict(extra="forbid", from_attributes=True)

    credentials: str = Field(
        min_length=32,
        description=LibraryPushAuthentication.model_fields["credentials"].description,
    )


class NotificationConfig(LibraryNotificationConfig):
    """The pinned ``core/notification-config.json``, with its block narrowed to the base class.

    ``event_types`` is the one field declared WIDER than the parent types it, and the pin
    asks for that in the same sentence that states the enum. ``core/notification-config.json``
    narrows the property to an inline account-anchored enum and then says what a seller does
    with a value outside it: media-buy-anchored types "are schema-invalid on this surface and
    sellers MUST reject those entries as PER-ACCOUNT validation failures with INVALID_REQUEST
    or VALIDATION_ERROR and error.field pointing at the invalid ``event_types`` entry RATHER
    THAN SILENTLY DROPPING THEM"
    (``#/properties/event_types``, AdCP). ``account/sync-accounts-request.json`` states
    the same MUST independently at the field a buyer actually sends
    (``#/properties/accounts/items/properties/notification_configs``), and
    ``account/sync-accounts-response.json`` declares the channel it goes down:
    ``accounts[].errors[]``, "required and non-empty when action is 'failed'", under the
    ``SyncAccountsSuccess`` branch. A seller that refuses the DOCUMENT cannot produce any of
    that -- it reports no entry, no ``account_id``, and none of the OTHER entries' results
    either.

    So the enum is still enforced; the layer that enforces it is the one that can report it
    per entry. ``_check_notification_configs`` (src/core/tools/accounts.py) refuses exactly
    ``NotificationType - EventType`` and names
    ``notification_configs[i].event_types[j]``, so this widening and that gate are
    complementary by construction: what this field admits minus what the gate refuses IS the
    parent's enum, member for member. Before it, the two sets were DISJOINT -- every value
    the gate refused, pydantic refused first -- which is why the gate's event-scope branch
    was unreachable and had drifted (``window_update`` missing) with nothing to catch it.

    Deliberately NOT ``list[str]``: a value outside the notification vocabulary ENTIRELY is
    not a question about which surface it anchors at, it is a malformed document, and it
    keeps earning a request-level INVALID_REQUEST. The widening is exactly as wide as the
    pin's own discussion of the field.

    Critical pattern #7 is not in tension with this. It governs UNDECLARED fields --
    ``deep_strip_to_schema`` strips what the DTO does not declare -- and ``event_types`` IS
    declared; the question is its value domain. Pattern #7 states its own remedy for a value
    this seller must accept ("declare it on the DTO -- that is the only mechanism"), and its
    rationale is that an unimplemented rule be LOUD: this rule is implemented, and only the
    value could not reach it. The weakened axis is named here rather than in a
    guard allowlist, per critical pattern #1.
    """

    # The sanctioned redeclaration: narrowed to a local subclass, same nullability, same
    # default, the parent's own description.
    authentication: Authentication | None = Field(
        default=None, description=LibraryNotificationConfig.model_fields["authentication"].description
    )

    # WIDENED past the parent (see the class docstring). The parent's own ``MinLen(1)`` and
    # description are restated so nothing but the value domain changes; ``uniqueItems`` is
    # the parent's and unaffected.
    event_types: list[LibraryNotificationType] = Field(  # type: ignore[assignment]
        ...,
        min_length=1,
        description=LibraryNotificationConfig.model_fields["event_types"].description,
    )


class PushNotificationConfig(LibraryPushNotificationConfig):
    """The pinned ``core/push-notification-config.json``, with its block narrowed to the subtype."""

    # No adoption validator. A base-typed block is accepted by PushAuthentication's own
    # from_attributes, which reads the two fields off the instance and validates them
    # against the subtype -- so the requiredness that refuses a credential-less block is
    # the pin's, applied by the subtype, at the same pointer a wire dict earns.
    authentication: PushAuthentication | None = Field(
        default=None, description=LibraryPushNotificationConfig.model_fields["authentication"].description
    )
