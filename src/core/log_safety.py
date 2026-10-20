"""Redaction helpers for logging security-sensitive request material.

A single choke point so a credential-bearing structure can be logged without
leaking the secret to anyone with log access (aggregators, CI artifacts, support
tooling). See #1617 (this credential true-positive) and #1450 (the broader
logging-hygiene backlog).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, TypedDict

from src.core.webhook_validator import webhook_url_for_log

if TYPE_CHECKING:
    # Typing-only for the SDK model: the SDK and DB models share a name, so alias
    # the SDK one here.
    from adcp import PushNotificationConfig as WirePushNotificationConfig


class FlatCredentialConfig(Protocol):
    """The flat, credential-bearing config shape this helper can redact.

    Structural and READ-ONLY, declared here rather than imported, because the
    objects that satisfy it live in layers ``src.core`` must not depend on: the
    stored ORM ``PushNotificationConfig`` row, and the sender-side
    ``DeliverableWebhookTarget`` values (a ``ValidatedWebhookRegistration`` among
    them) that ``src/services/protocol_webhook_service.py`` delivers with. Naming
    either concrete class would either invert the layering or exclude the other,
    which is exactly how the sender ended up keeping its own hand-rolled copy of
    "what is safe to log about a config".

    Read-only properties, not plain attributes: a Protocol with mutable
    attributes is invariant and would refuse a frozen(slots) dataclass.
    """

    @property
    def url(self) -> str: ...

    @property
    def authentication_type(self) -> str | None: ...

    @property
    def authentication_token(self) -> str | None: ...


#: Mask written in place of a credential. Deliberately DISTINCT from the ``'***'``
#: that ``PushNotificationConfig.__repr__`` writes (src/core/database/models.py):
#: the redaction site is handed that ORM row, so a site that stopped calling this
#: helper and went back to rendering the row would still print ``'***'`` and no
#: secret. Aligning the two tokens would therefore make the deletion oracle in
#: tests/integration/test_push_notification_log_redaction.py unable to tell
#: "routed through the redactor" apart from "rode on the row's repr", turning it
#: into a decorative test. The cost — a log grep must know both tokens — is
#: accepted in exchange for a testable choke-point invariant. Pinned by
#: tests/unit/test_log_safety.py::test_sentinel_is_distinct_from_model_repr_mask.
REDACTED = "***REDACTED***"


class RedactedPushNotificationConfig(TypedDict):
    """The closed, credential-free view logged for a push-notification config.

    A fixed four-key record, not the open ``dict[str, Any]`` this helper used to
    return: ``id`` / ``url`` / ``authentication_type`` are the config's
    non-sensitive fields, and ``authentication`` is the ``REDACTED`` sentinel when
    a credential is present, ``None`` when it is not — never the secret itself.
    The ``None`` input reconciles to an all-``None`` instance of this same record,
    so every return path is one shape a reader (and ``%s``) can rely on.

    ``url`` is the ``webhook_url_for_log`` rendering — ``scheme://host/path``,
    never userinfo and never the query — so it is a ``str`` for both input
    families rather than the SDK model's ``AnyUrl``. A credential can ride in a
    URL's query as easily as in the auth block, so masking ``authentication``
    while printing the raw URL beside it would redact one half of the same
    secret.
    """

    id: str | None
    url: str | None
    authentication_type: str | None
    authentication: str | None


def redact_push_notification_config(
    config: WirePushNotificationConfig | FlatCredentialConfig | None,
) -> RedactedPushNotificationConfig:
    """Return a loggable view of a push-notification config, credential removed.

    Takes a typed config object, not the wire dict: this is business logic, not a
    transport boundary, so a caller holding raw wire material normalizes it to a
    model first. Two families carry the buyer's webhook credential in different
    places — the SDK ``PushNotificationConfig`` under a nested ``authentication``
    object (``schemes`` + ``credentials``), and the flat
    :class:`FlatCredentialConfig` shapes (the stored ORM row, the sender's
    ``DeliverableWebhookTarget`` values) under ``authentication_token``. Logging
    the raw object leaks a replayable secret, so this keeps only non-sensitive
    fields (id, url, auth type) and masks the credential when one is present.
    ``None`` returns an all-``None`` instance of this record (same four keys), so
    every return shape reconciles.

    Every field is read by attribute (``getattr`` with a default, because the two
    families have disjoint field sets — the SDK model has no ``id`` /
    ``authentication_token``, the flat shapes have no nested ``authentication``,
    and ``DeliverableWebhookTarget`` has no ``id`` at all), so a typed
    authentication object reports its scheme and credential presence directly.

    Use at every push-notification log site so the redaction cannot drift:
    ``logger.info("registering pnc: %s", redact_push_notification_config(cfg))``.
    """
    if config is None:
        return RedactedPushNotificationConfig(id=None, url=None, authentication_type=None, authentication=None)

    # SDK wire model: config.authentication.{schemes: [...], credentials: "<secret>"}.
    auth = getattr(config, "authentication", None)
    schemes = getattr(auth, "schemes", None)
    wire_credential = getattr(auth, "credentials", None)

    # DB model: flat authentication_type + authentication_token ("<secret>").
    scheme = schemes[0] if schemes else None
    # ``schemes[0]`` is an ``AuthenticationScheme`` enum on the SDK model; log its
    # string value ("Bearer"), not the enum repr.
    auth_type = getattr(scheme, "value", scheme) or getattr(config, "authentication_type", None)
    has_credential = bool(wire_credential) or bool(getattr(config, "authentication_token", None))

    return RedactedPushNotificationConfig(
        id=getattr(config, "id", None),
        # Sanitized, not raw: `webhook_url_for_log` drops userinfo and the query
        # string, either of which can carry the buyer's token. It is also TOTAL —
        # it returns a placeholder rather than raising on a URL `urlparse` cannot
        # read — which a log helper has to be. Same renderer the sender's own log
        # lines and `ValidatedWebhookRegistration.__repr__` use, so a webhook URL
        # reaches the log exactly one way.
        url=webhook_url_for_log(getattr(config, "url", None)),
        authentication_type=auth_type,
        authentication=REDACTED if has_credential else None,
    )
