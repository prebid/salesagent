"""Redaction helpers for logging security-sensitive request material.

A single choke point so a credential-bearing structure can be logged without
leaking the secret to anyone with log access (aggregators, CI artifacts, support
tooling). See #1617 (this credential true-positive) and #1450 (the broader
logging-hygiene backlog).
"""

from __future__ import annotations

from typing import Any

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


def redact_push_notification_config(config: Any) -> dict[str, Any]:
    """Return a loggable view of a push-notification config, credential removed.

    The config carries the buyer's webhook credential: the A2A wire dict holds it
    under ``authentication.credentials``; the stored ``DBPushNotificationConfig``
    model holds it under ``authentication_token``. Logging the raw config leaks a
    replayable secret, so this keeps only non-sensitive fields (id, url, auth
    type) and masks the credential when one is present. Accepts the wire dict
    shape or a typed/DB model; ``None`` returns ``{}``.

    Both the outer config and its nested ``authentication`` block are read
    shape-agnostically (mapping key or attribute), so a typed authentication
    object reports its scheme and credential presence the same way the wire dict
    does.

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

    def _auth_get(key: str) -> Any:
        """Same mapping-or-attribute read as ``_get``, for the nested auth block."""
        if isinstance(auth, dict):
            return auth.get(key)
        return getattr(auth, key, None)

    # A2A wire shape: authentication.{schemes: [...], credentials: "<secret>"}.
    schemes = _auth_get("schemes")
    wire_credential = _auth_get("credentials")
    # DB/model shape: flat authentication_type + authentication_token ("<secret>").
    auth_type = (schemes[0] if schemes else None) or _get("authentication_type")
    has_credential = bool(wire_credential) or bool(_get("authentication_token"))

    return {
        "id": _get("id"),
        "url": _get("url"),
        "authentication_type": auth_type,
        "authentication": REDACTED if has_credential else None,
    }
