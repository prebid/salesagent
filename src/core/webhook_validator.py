"""Webhook URL validation to prevent SSRF attacks.

This module provides security validation for webhook URLs to prevent
Server-Side Request Forgery (SSRF) attacks where malicious users could
trick the server into making requests to internal services.

Relationship to ``src/core/security/outbound_http.py``: that module is the seam
every outbound *request* goes through, and it owns SEND-time policy outright —
address, TLS, redirect and retry, delegated to the adcp SDK. This module keeps
exactly ONE gate, and only because the seam cannot yet express it: registration.

Both of the seam's pre-connection entry points (``send``/``asend`` and
``validate_url``) go through ``adcp.signing.resolve_and_validate_host``, which
ALWAYS resolves DNS. Registration is deliberately a no-DNS verdict — an
unresolvable but public hostname must be ACCEPTED at registration and re-checked
with DNS when the callback is actually dialled. ``WebhookURLValidator.
validate_webhook_url_registration`` below is now a thin ``(bool, str)`` wrapper
over :meth:`~src.core.security.egress.policy.EgressPolicy.check_registration` —
the shared address predicate both verdicts read now lives in
``src/core/security/egress/policy.py``, and ``src/core/security/
url_validator.py`` (this module's former SSRF-computation dependency) has been
deleted; nothing under ``src/`` computes address policy outside the egress
package.

There is no send-side gate here any more. There used to be
(``validate_outbound_webhook_url`` and friends); it had no production callers and
survived only as a patch target that made test controls look live while
intercepting nothing, so it was deleted. Any new outbound send goes through the
seam — never a second copy of address policy here.

The one thing this gate MUST NOT decide for itself is the scheme. That decision
belongs to :class:`~src.core.security.egress.policy.EgressPolicy`, which
requires https unconditionally on both verdicts (GH #1802 deleted the
send-side escape hatch). An ingest gate that admitted a scheme the seam refuses
would accept a buyer's webhook URL with a success envelope and then never
deliver to it, which is the one failure mode the buyer cannot see or correct.

``validate_webhook_task_type`` below is an unrelated concern (SDK payload enum
coercion) that happens to live in this file.
"""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlparse

from adcp.types import ContextObject, TaskType

from src.core.exceptions import AdCPBlockedUrlError
from src.core.security.egress.policy import EgressPolicy

# Fallback used when an action label is not a member of the SDK's closed
# TaskType enum. create_mcp_webhook_payload() restricts task_type to that
# enum and would otherwise reject the payload as schema-invalid.
WEBHOOK_TASK_TYPE_FALLBACK = "update_media_buy"

WEBHOOK_SSRF_SUGGESTION = (
    "Provide a public https webhook URL that does not target private, loopback, "
    "link-local, CGNAT, multicast, or cloud-metadata hosts."
)

# Log fallback when sanitize_webhook_url_for_log cannot parse scheme/host —
# never fall back to the raw buyer URL (credentials / query).
UNPARSEABLE_WEBHOOK_URL_FOR_LOG = "<unparseable-url>"


def _adcp_testing() -> bool:
    """True when ADCP_TESTING allows localhost/HTTP for capture servers."""
    return os.environ.get("ADCP_TESTING") == "true"


def validate_webhook_task_type(task_type: str, fallback: str = WEBHOOK_TASK_TYPE_FALLBACK) -> str:
    """Coerce a task_type to a value accepted by the SDK webhook payload builder.

    ``create_mcp_webhook_payload()`` validates ``task_type`` against the closed
    :class:`adcp.types.TaskType` enum. Action labels sourced from untrusted data
    (e.g. ``workflow_steps.tool_name``) may not be enum members, which would make
    the payload schema-invalid. This helper returns ``task_type`` unchanged when
    it is a valid enum value, otherwise returns ``fallback``.

    This validates ONLY the value destined for the SDK/webhook payload. Callers
    must keep the original action label for internal metadata (audit log,
    delivery-webhook guards, ``WebhookDeliveryLog.task_type``) — see
    .

    Args:
        task_type: The candidate action label.
        fallback: The value to return when ``task_type`` is not a TaskType member.

    Returns:
        ``task_type`` if it is a valid TaskType, otherwise ``fallback``.
    """
    try:
        TaskType(task_type)
    except ValueError:
        return fallback
    return task_type


def webhook_ssrf_suggestion() -> str:
    """Buyer-facing suggestion for registration/outbound SSRF rejections.

    Always the strict https wording (GH #1802): there is no posture
    left in which a plain-http webhook URL is ever admissible, so there is no
    second wording to select between. It used to key on
    :meth:`WebhookURLValidator._require_https`, which selected between this and
    a now-deleted "http(s)" wording depending on the (now also deleted)
    outbound scheme hatch.
    """
    return WEBHOOK_SSRF_SUGGESTION


# Every character ``str.splitlines()`` treats as a line boundary. ``urlsplit``
# strips only \t \r \n (``parse._UNSAFE_URL_BYTES_TO_REMOVE``); VT, FF, the file/
# group/record separators, NEL, U+2028 and U+2029 all survive it — and the PATH is
# carried through verbatim below, so without this a buyer-supplied path forges a
# second log line at every caller. Escaped rather than deleted: a dropped
# character would silently change the URL an operator is reading.
_LINE_BREAKING = "\n\r\v\f\x1c\x1d\x1e\x85  "
_LOG_SAFE = str.maketrans({c: c.encode("unicode_escape").decode("ascii") for c in _LINE_BREAKING})


def sanitize_webhook_url_for_log(url: str | None) -> str | None:
    """Return ``scheme://host/path`` for logs — never credentials or query.

    Returns ``None`` rather than raising on a URL ``urlparse`` cannot read (an
    unterminated IPv6 bracket raises ``ValueError``). :func:`webhook_url_for_log`
    documents itself as TOTAL, and a stored row written before the ingest gate
    can still carry such a URL — so a caller rendering one for a log line, or
    inside a ``__repr__``, must get the placeholder rather than an exception
    thrown from a debugger frame or a pytest diff.
    """
    if not url:
        return None
    try:
        parsed = urlparse(str(url))
    except ValueError:
        return None
    if parsed.scheme and parsed.hostname:
        return f"{parsed.scheme}://{parsed.hostname}{parsed.path or ''}".translate(_LOG_SAFE)
    return None


def webhook_url_for_log(url: str | None) -> str:
    """Total log helper: sanitized URL or the unparseable placeholder (never raw)."""
    return sanitize_webhook_url_for_log(url) or UNPARSEABLE_WEBHOOK_URL_FOR_LOG


def reject_unsafe_webhook_registration_url(
    url: str | None,
    *,
    field: str,
    context: ContextObject | dict[str, Any] | None = None,
) -> None:
    """Raise AdCPBlockedUrlError when ``url`` fails the registration SSRF gate.

    The same class the dial-time egress seam raises (``OutboundRequestBlocked``):
    a refused buyer URL gets one wire answer regardless of which gate noticed it.

    Blank / whitespace-only / ``None`` URLs are a no-op (not a rejection) so
    callers can extract-then-call unconditionally.
    """
    if url is None or not str(url).strip():
        return
    # The cause is logged by EgressPolicy.check_registration, which computes it.
    # Logging it a second time here would double every refusal in the operator's
    # log for no added fact — this frame only adds ``field``, which the buyer
    # already receives on the error.
    is_valid, _ = WebhookURLValidator.validate_webhook_url_registration(str(url))
    if not is_valid:
        raise AdCPBlockedUrlError(
            field=field,
            suggestion=webhook_ssrf_suggestion(),
            context=context,
        )


class WebhookURLValidator:
    """Validates webhook URLs to prevent SSRF attacks.

    ``_maybe_allow_localhost`` and ``_require_https`` (the localhost/loopback
    rescue and the unconditional-https rule) deleted from this class —
    :class:`~src.core.security.egress.policy.EgressPolicy` owns both now, so
    this class no longer computes SSRF policy itself. It survives as a thin
    ``(bool, str)`` wrapper because its call sites (this module's own
    ``reject_unsafe_webhook_registration_url`` and one direct caller,
    ``src/core/database/repositories/push_notification_config.py``) both
    depend on that return shape.
    """

    @classmethod
    def validate_webhook_url_registration(cls, url: str) -> tuple[bool, str]:
        """Registration-time SSRF gate (no DNS required).

        Delegates entirely to
        :meth:`~src.core.security.egress.policy.EgressPolicy.check_registration`.
        ``AdCPBlockedUrlError`` defines no ``__str__`` override — it calls
        ``Exception.__init__(message)`` — so ``str(exc)`` here is exactly the
        bare message :meth:`EgressPolicy.check_registration` raised, and the
        ``(bool, str)`` contract this method's own callers depend on survives
        byte-identically.

        When ``ADCP_TESTING=true``, localhost/loopback are allowed for
        capture servers — graded on both arms in
        ``tests/unit/test_webhook_security.py::TestLocalhostAllowanceUnderTestingMode``.
        """
        try:
            EgressPolicy.check_registration(url, allow_loopback=_adcp_testing())
        except AdCPBlockedUrlError as exc:
            return False, str(exc)
        return True, ""
