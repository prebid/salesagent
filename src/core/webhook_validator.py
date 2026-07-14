"""Webhook URL validation to prevent SSRF attacks.

This module provides security validation for webhook URLs to prevent
Server-Side Request Forgery (SSRF) attacks where malicious users could
trick the server into making requests to internal services.
"""

import os

from adcp.types import TaskType

from src.core.security.url_validator import check_url_ssrf


def _allow_private_webhook_targets() -> bool:
    """Whether buyer callbacks to private/loopback targets are permitted.

    A DEDICATED opt-in (``ADCP_ALLOW_PRIVATE_WEBHOOKS``), deliberately NOT tied to
    ``ENVIRONMENT``: a real staging/dev deployment that serves buyers must still block
    private/internal targets, so gating on "not production" was too broad (#1512). This
    flag is set ONLY by the E2E harness, whose webhook receiver lives on the compose
    network / loopback. Cloud-metadata and link-local targets stay blocked even when it
    is set.
    """
    return os.getenv("ADCP_ALLOW_PRIVATE_WEBHOOKS", "").strip().lower() in ("1", "true", "yes")


# Fallback used when an action label is not a member of the SDK's closed
# TaskType enum. create_mcp_webhook_payload() restricts task_type to that
# enum and would otherwise reject the payload as schema-invalid.
WEBHOOK_TASK_TYPE_FALLBACK = "update_media_buy"


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


class WebhookURLValidator:
    """Validates webhook URLs to prevent SSRF attacks."""

    @classmethod
    def validate_webhook_url(cls, url: str) -> tuple[bool, str]:
        """
        Validate webhook URL for SSRF protection.

        Args:
            url: The webhook URL to validate

        Returns:
            (is_valid, error_message) - is_valid is True if safe, error_message explains failures
        """
        return check_url_ssrf(url)

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
        is_valid, error = check_url_ssrf(url)

        # If validation failed but it's a localhost error and we allow it
        if not is_valid and allow_localhost:
            if "localhost" in error.lower() or "127.0.0" in error or "loopback" in error.lower():
                return True, ""

        return is_valid, error

    @classmethod
    def validate_callback_url(cls, url: str) -> tuple[bool, str]:
        """Env-gated validation for a buyer-supplied push callback URL (#1512).

        The single gate used at callback registration AND delivery. By default —
        production, staging, and ordinary dev — it requires HTTPS and blocks all
        internal targets (loopback, RFC-1918, localhost/Docker aliases), validating
        EVERY resolved A/AAAA record. Only the dedicated ``ADCP_ALLOW_PRIVATE_WEBHOOKS``
        opt-in (set solely by the E2E harness) relaxes this to permit a trusted
        private/loopback receiver over plain HTTP. Cloud-metadata / link-local targets
        (169.254.x, fe80::, metadata.google.internal) stay blocked in EVERY environment.

        Callers connect to the validated address (connection pinning in
        protocol_webhook_service) so the checked IP is the one actually used;
        disabled redirects + HTTPS close the redirect-to-metadata and plain-HTTP vectors.
        """
        allow_private = _allow_private_webhook_targets()
        # Require HTTPS whenever private targets are NOT permitted (every real
        # deployment). The E2E opt-in that permits private receivers also permits HTTP.
        return check_url_ssrf(url, require_https=not allow_private, allow_private=allow_private)
