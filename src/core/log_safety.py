"""Redaction helpers for logging security-sensitive request material.

A single choke point so a credential-bearing structure can be logged without
leaking the secret to anyone with log access (aggregators, CI artifacts, support
tooling). See #1617 (this credential true-positive) and #1450 (the broader
logging-hygiene backlog).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    # Typing-only so log_safety imports nothing at runtime (no import cycle). The
    # helper accepts either the SDK wire model or the stored DB model; the two
    # share a name, so alias them here.
    from adcp import PushNotificationConfig as WirePushNotificationConfig

    from src.core.database.models import PushNotificationConfig as DBPushNotificationConfig

#: Mask written in place of a credential. Deliberately DISTINCT from the ``'***'``
#: that ``PushNotificationConfig.__repr__`` writes (src/core/database/models.py):
#: two of the three log sites are handed that DB model, so a site that stopped
#: calling this helper and went back to rendering the model would still print
#: ``'***'`` and no secret. Aligning the two tokens would therefore make the
#: per-site deletion oracles in
#: tests/integration/test_push_notification_log_redaction.py unable to tell
#: "routed through the redactor" apart from "rode on the model's repr", turning
#: both into decorative tests. The cost — a log grep must know both tokens — is
#: accepted in exchange for a testable choke-point invariant. Pinned by
#: tests/unit/test_log_safety.py::test_sentinel_is_distinct_from_model_repr_mask.
REDACTED = "***REDACTED***"


def redact_push_notification_config(
    config: WirePushNotificationConfig | DBPushNotificationConfig | None,
) -> dict[str, Any]:
    """Return a loggable view of a push-notification config, credential removed.

    Takes a typed config model, not the wire dict: this is business logic, not a
    transport boundary, so callers normalize the wire shape to a model first (the
    one legacy dict caller does ``PushNotificationConfig.model_validate(...)`` at
    its call site). Two typed shapes carry the buyer's webhook credential in
    different places — the SDK ``PushNotificationConfig`` under a nested
    ``authentication`` object (``schemes`` + ``credentials``), the stored DB
    ``PushNotificationConfig`` under the flat ``authentication_token``. Logging
    the raw model leaks a replayable secret, so this keeps only non-sensitive
    fields (id, url, auth type) and masks the credential when one is present.
    ``None`` returns ``{}``.

    Every field is read by attribute (``getattr`` with a default, because the two
    typed shapes have disjoint field sets — the SDK model has no ``id`` /
    ``authentication_token``, the DB model has no nested ``authentication``), so a
    typed authentication object reports its scheme and credential presence
    directly.

    Use at every push-notification log site so the redaction cannot drift:
    ``logger.info("registering pnc: %s", redact_push_notification_config(cfg))``.
    """
    if config is None:
        return {}

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

    return {
        "id": getattr(config, "id", None),
        "url": getattr(config, "url", None),
        "authentication_type": auth_type,
        "authentication": REDACTED if has_credential else None,
    }
