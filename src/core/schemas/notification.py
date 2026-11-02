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
"""

from adcp.types import NotificationConfig as LibraryNotificationConfig
from adcp.types import PushNotificationConfig as LibraryPushNotificationConfig
from adcp.types.generated_poc.core.notification_config import Authentication as LibraryNotificationAuthentication
from adcp.types.generated_poc.core.push_notification_config import Authentication as LibraryPushAuthentication
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
    """The pinned ``core/notification-config.json``, with its block narrowed to the base class."""

    # The sanctioned redeclaration: narrowed to a local subclass, same nullability, same
    # default, the parent's own description.
    authentication: Authentication | None = Field(
        default=None, description=LibraryNotificationConfig.model_fields["authentication"].description
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
