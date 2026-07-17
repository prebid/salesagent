"""Redaction helpers for logging security-sensitive request material.

A single choke point so a credential-bearing structure can be logged without
leaking the secret to anyone with log access (aggregators, CI artifacts, support
tooling). See #1617 (this credential true-positive) and #1450 (the broader
logging-hygiene backlog).
"""

from __future__ import annotations

from typing import Any

_REDACTED = "***REDACTED***"


def redact_push_notification_config(config: Any) -> dict[str, Any]:
    """Return a loggable view of a push-notification config, credential removed.

    The config carries the buyer's webhook credential: the A2A wire dict holds it
    under ``authentication.credentials``; the stored ``DBPushNotificationConfig``
    model holds it under ``authentication_token``. Logging the raw config leaks a
    replayable secret, so this keeps only non-sensitive fields (id, url, auth
    type) and masks the credential when one is present. Accepts the wire dict
    shape or a typed/DB model; ``None`` returns ``{}``.

    Use at every push-notification log site so the redaction cannot drift:
    ``logger.info("registering pnc: %s", redact_push_notification_config(cfg))``.
    """
    if config is None:
        return {}

    def _get(key: str, default: Any = None) -> Any:
        if isinstance(config, dict):
            return config.get(key, default)
        return getattr(config, key, default)

    auth = _get("authentication") or {}
    # A2A wire shape: authentication.{schemes: [...], credentials: "<secret>"}.
    schemes = auth.get("schemes") if isinstance(auth, dict) else None
    wire_credential = auth.get("credentials") if isinstance(auth, dict) else None
    # DB/model shape: flat authentication_type + authentication_token ("<secret>").
    auth_type = (schemes[0] if schemes else None) or _get("authentication_type")
    has_credential = bool(wire_credential) or bool(_get("authentication_token"))

    return {
        "id": _get("id"),
        "url": _get("url"),
        "authentication_type": auth_type,
        "authentication": _REDACTED if has_credential else None,
    }
