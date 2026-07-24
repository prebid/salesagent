"""Webhook URL validation to prevent SSRF attacks.

This module provides security validation for webhook URLs to prevent
Server-Side Request Forgery (SSRF) attacks where malicious users could
trick the server into making requests to internal services.
"""

from __future__ import annotations

import logging
import os
from typing import Any
from urllib.parse import urlparse

from adcp.types import ContextObject, TaskType

from src.core.config import is_production
from src.core.exceptions import AdCPValidationError
from src.core.security.url_validator import check_url_ssrf

# Fallback used when an action label is not a member of the SDK's closed
# TaskType enum. create_mcp_webhook_payload() restricts task_type to that
# enum and would otherwise reject the payload as schema-invalid.
WEBHOOK_TASK_TYPE_FALLBACK = "update_media_buy"

WEBHOOK_SSRF_SUGGESTION = (
    "Provide a public https webhook URL that does not target private, loopback, "
    "link-local, CGNAT, multicast, or cloud-metadata hosts."
)
WEBHOOK_SSRF_SUGGESTION_DEV = (
    "Provide a public http(s) webhook URL that does not target private, loopback, "
    "link-local, CGNAT, multicast, or cloud-metadata hosts."
)

# Log fallback when sanitize_webhook_url_for_log cannot parse scheme/host —
# never fall back to the raw buyer URL (credentials / query).
UNPARSEABLE_WEBHOOK_URL_FOR_LOG = "<unparseable-url>"


def _adcp_testing() -> bool:
    """True when ADCP_TESTING allows localhost/HTTP for capture servers."""
    return os.environ.get("ADCP_TESTING") == "true"


def _strict_mode() -> bool:
    """Production SSRF policy: HTTPS required and no testing localhost bypass."""
    return is_production() and not _adcp_testing()


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
    salesagent-yi3s.

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
    """Buyer-facing suggestion for registration/outbound SSRF rejections."""
    if _strict_mode():
        return WEBHOOK_SSRF_SUGGESTION
    return WEBHOOK_SSRF_SUGGESTION_DEV


def sanitize_webhook_url_for_log(url: str | None) -> str | None:
    """Return ``scheme://host/path`` for logs — never credentials or query."""
    if not url:
        return None
    parsed = urlparse(str(url))
    if parsed.scheme and parsed.hostname:
        return f"{parsed.scheme}://{parsed.hostname}{parsed.path or ''}"
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
    """Raise AdCPValidationError when ``url`` fails the registration SSRF gate.

    Blank / whitespace-only / ``None`` URLs are a no-op (not a rejection) so
    callers can extract-then-call unconditionally.
    """
    if url is None or not str(url).strip():
        return
    is_valid, error_msg = WebhookURLValidator.validate_webhook_url_registration(str(url))
    if not is_valid:
        raise AdCPValidationError(
            f"Invalid {field}: {error_msg}",
            field=field,
            suggestion=webhook_ssrf_suggestion(),
            recovery="correctable",
            context=context,
        )


def reject_unsafe_outbound_webhook_url(
    url: str,
    *,
    log: logging.Logger,
    kind: str,
) -> tuple[bool, str]:
    """Send-time SSRF gate with standardized error logging.

    Returns ``(rejected, error_msg)``. On rejection, logs once with a shared
    message shape so protocol and application delivery paths cannot drift.
    Callers that maintain a circuit breaker should record failure locally.
    """
    is_valid, error_msg = WebhookURLValidator.validate_outbound_webhook_url(url)
    if is_valid:
        return False, ""
    log.error(
        "%s webhook URL failed SSRF validation (url=%s): %s",
        kind,
        webhook_url_for_log(url),
        error_msg,
    )
    return True, error_msg


class WebhookURLValidator:
    """Validates webhook URLs to prevent SSRF attacks."""

    @staticmethod
    def _maybe_allow_localhost(is_valid: bool, error: str, *, allow_localhost: bool) -> tuple[bool, str]:
        """Override localhost/loopback SSRF failures when testing allows them."""
        if not is_valid and allow_localhost:
            if "localhost" in error.lower() or "127.0.0" in error or "loopback" in error.lower():
                return True, ""
        return is_valid, error

    @staticmethod
    def _require_https() -> bool:
        """Production requires HTTPS; ADCP_TESTING keeps HTTP for capture servers."""
        return _strict_mode()

    @classmethod
    def validate_webhook_url(cls, url: str) -> tuple[bool, str]:
        """
        Validate webhook URL for SSRF protection.

        Args:
            url: The webhook URL to validate

        Returns:
            (is_valid, error_message) - is_valid is True if safe, error_message explains failures
        """
        return check_url_ssrf(url, require_https=cls._require_https())

    @classmethod
    def validate_webhook_url_registration(cls, url: str) -> tuple[bool, str]:
        """Registration-time SSRF gate (no DNS required).

        Blocks known-bad hostnames and literal private IPs. Unresolvable
        public hostnames are allowed here; send-time re-checks with DNS
        (``validate_outbound_webhook_url``). When ``ADCP_TESTING=true``,
        localhost/loopback are allowed for capture servers. Production
        requires HTTPS.
        """
        allow_localhost = _adcp_testing()
        is_valid, error = check_url_ssrf(
            url,
            resolve_dns=False,
            require_https=cls._require_https(),
        )
        return cls._maybe_allow_localhost(is_valid, error, allow_localhost=allow_localhost)

    @classmethod
    def validate_outbound_webhook_url(cls, url: str) -> tuple[bool, str]:
        """Send-time SSRF gate (full DNS), with localhost allowance under ADCP_TESTING."""
        if _adcp_testing():
            return cls.validate_for_testing(url, allow_localhost=True)
        return cls.validate_webhook_url(url)

    @classmethod
    def validate_for_testing(cls, url: str, allow_localhost: bool = False) -> tuple[bool, str]:
        """
        Validate webhook URL with optional localhost allowance for testing.

        This is useful for development/testing scenarios where webhooks need to
        point to localhost services. Production should use validate_webhook_url().

        Args:
            url: The webhook URL to validate
            allow_localhost: If True, allows localhost and 127.0.0.1

        Returns:
            (is_valid, error_message)
        """
        # Testing path always allows HTTP (capture servers, local harnesses).
        is_valid, error = check_url_ssrf(url, require_https=False)
        return cls._maybe_allow_localhost(is_valid, error, allow_localhost=allow_localhost)
