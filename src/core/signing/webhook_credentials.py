"""Does this request hand the seller webhook credentials? (security.mdx @ v3.1.1 :1462-1465)

    Sellers that support request signing MUST require the inbound request to be
    9421-signed ... when ``authentication`` is present on
    ``push_notification_config.authentication`` or any
    ``accounts[].notification_configs[].authentication``, rejecting with
    ``request_signature_required``

restated at :1375 as a trigger that fires "regardless of ``required_for`` membership".

Why the rule cannot be folded into the operation buckets
-------------------------------------------------------
The composition rule (:1268-1271) exempts an unsigned-but-bearer-authenticated caller
from ``required_for``. This escalation must NOT inherit that exemption, and :1462 says
why: the rule exists BECAUSE the registering caller is normally bearer-authed and an
on-path mutator can inject or strip the ``authentication`` block silently. "Valid bearer
⇒ don't reject" would defeat it entirely. So it is a separate promotion, bounded by
``supported`` and by nothing else — which is exactly where :1465 draws the line.

Two typed reads, not an enumeration of wire locations
----------------------------------------------------
The pre-merge branch read this off the RAW BODY, because the verifier was an ASGI
middleware and raw bytes were all it had. That forced an enumeration of every place a
JSON-RPC envelope can carry a notification config — ``params.arguments``,
``params.configuration.push_notification_config``, the A2A v1.0 proto spelling, both
camelCase and snake_case for each — and the enumeration was by LOCATION rather than by
method precisely because a method list has a default arm, and a default arm on this
question is a default-ACCEPT over every method the list does not name.

#1721's boundary removes the whole problem. ``invoke_tool`` holds the VALIDATED request,
in which every wire spelling has already collapsed into one declared field of one
declared type, so the rule reduces to the two reads the spec sentence itself names.

WHAT THIS DOES NOT COVER, and it is not a simplification artefact: a request that never
reaches ``invoke_tool`` is never asked. The A2A JSON-RPC lifecycle methods —
``tasks/pushNotificationConfig/set`` above all, which registers a webhook AND its
credentials with no skill invocation anywhere in sight — are answered by the a2a-sdk's
own handlers, which do not call the boundary. See the module docstring of
:mod:`src.core.signing.verifier` § "What the boundary cannot see".
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any

from src.core.schemas._base import BuyerRequest

logger = logging.getLogger(__name__)


def _authenticated(config: Any) -> bool:
    """Whether one notification config carries a non-empty ``authentication`` block.

    Duck-typed on the FIELD rather than on the class, deliberately: the pin spells the
    block inline in two schemas, so ``push_notification_config`` and a
    ``notification_configs[]`` entry are two classes
    (:class:`src.core.schemas.notification.PushNotificationConfig` and
    :class:`~src.core.schemas.notification.NotificationConfig`) that declare the same
    field. Naming both here would be two branches of one rule.
    """
    return getattr(config, "authentication", None) is not None


def _configs_of(accounts: Iterable[Any]) -> Iterable[Any]:
    """Every ``notification_configs`` entry across an ``accounts`` list."""
    for account in accounts:
        yield from getattr(account, "notification_configs", None) or ()


def registers_webhook_credentials(req: BuyerRequest) -> bool:
    """Whether *req* registers legacy webhook credentials with this seller.

    Both triggers, not just the first: only ``push_notification_config`` has a compliance
    vector, so a reader handling it alone passes all 40 vectors and is still wrong.

    Read through ``__dict__`` rather than attribute access, the same way
    ``BuyerRequest.get_account`` and ``get_idempotency_key`` read the fields they cannot
    assume a DTO declares. Ten of the fourteen registry rows declare neither field, and a
    tool that grows one is covered the day its DTO declares it rather than the day someone
    remembers to add it here.
    """
    if _authenticated(req.__dict__.get("push_notification_config")):
        return True
    return any(_authenticated(config) for config in _configs_of(req.__dict__.get("accounts") or ()))


def log_arriving_webhook_credentials(operation: str) -> None:
    """Record one arriving request that hands this seller webhook credentials.

    security.mdx @ v3.1.1 :1464 — "Sellers MUST log every request that arrives with a
    non-empty ``authentication`` block." Per REQUEST, and unqualified by posture: :1465
    sends a seller that cannot ENFORCE the signing requirement here rather than exempting
    it, so this fires whatever the tenant declared.

    Called from ``invoke_tool`` beside the predicate above, which is the one place a
    request ARRIVES in the sense :1464 means. Not from
    :func:`~src.core.signing.verifier.verify_inbound_signature`, which is reached only
    after the bearer resolved and only while the verifier kill switch is on: a rejected
    token and a rolled-back verifier both leave the arrival unlogged, and the second is
    exactly the non-enforcing seller :1465 routes to the log. Not from the escalation
    either — ``_bucket_for`` reads ``registers_credentials and posture.supported``, which
    cannot fire for a seller declaring ``supported: false``.

    The message deliberately does not name the posture or whether the request was signed:
    neither is readable at the boundary, which reads no header and no capture.
    """
    logger.warning(
        "Inbound request carries a non-empty webhook authentication block "
        "(push_notification_config or accounts[].notification_configs): operation=%r. "
        "Legacy HMAC was selected by the buyer rather than RFC 9421 — alarm on this if the "
        "buyer expected 9421 (security.mdx @ v3.1.1 :1464).",
        operation,
    )
