"""The two webhook secrets a buyer registers, and the one function that removes them from a document.

Every webhook registration in the pinned schemas carries two secrets:

* ``authentication.credentials`` -- "For Bearer: token sent in Authorization header. For
  HMAC-SHA256: shared secret used to generate signature" (``core/push-notification-config.json``,
  ``core/reporting-webhook.json``, ``create-media-buy-request.json`` ``artifact_webhook``,
  ``core/notification-config.json``). The account-level schema states the rule outright:
  "Credentials and shared secrets in ``authentication.credentials`` are write-only".
* ``token`` beside a registration's ``url`` -- the seller echoes it in every webhook payload
  so the buyer can tell the call came from the seller, which makes it a secret of the same
  kind (``core/push-notification-config.json``, ``core/reporting-webhook.json``,
  ``artifact_webhook``).

The seller stores both, because delivery needs them: ``ContextManager`` rehydrates a
workflow step's ``push_notification_config`` from the stored step, and an account's
notification configs are read back to sign its webhooks. So nothing here touches what is
stored. :func:`scrub_webhook_secrets` returns a copy for a reader that is not the delivery
path -- the account echo in ``account_serialization`` and the admin workflow step page.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

__all__ = ["scrub_webhook_secrets"]


def scrub_webhook_secrets(document: Any) -> Any:
    """A copy of *document* without any webhook registration's secrets, at any depth.

    Walks mappings and lists. A mapping's ``authentication`` mapping loses ``credentials``,
    and a mapping that carries a ``url`` loses its ``token``. Matching on the shape rather
    than on a list of registration names covers every registration a stored document can
    hold (``push_notification_config``, ``reporting_webhook``, ``artifact_webhook``, an
    account's ``notification_configs``), wherever a producer nested it: an update awaiting
    approval stores its request again under ``response_data["request_data"]``.
    """
    if isinstance(document, list):
        return [scrub_webhook_secrets(item) for item in document]
    if not isinstance(document, Mapping):
        return document
    scrubbed: dict[str, Any] = {}
    for key, value in document.items():
        if key == "token" and "url" in document:
            continue
        if key == "authentication" and isinstance(value, Mapping):
            value = {name: item for name, item in value.items() if name != "credentials"}
        scrubbed[key] = scrub_webhook_secrets(value)
    return scrubbed
