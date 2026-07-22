"""Webhook URL validation to prevent SSRF attacks.

This module provides security validation for webhook URLs to prevent
Server-Side Request Forgery (SSRF) attacks where malicious users could
trick the server into making requests to internal services.
"""

from adcp.types import TaskType

from src.core.security.url_validator import check_url_ssrf

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

    @staticmethod
    def _maybe_allow_localhost(is_valid: bool, error: str, *, allow_localhost: bool) -> tuple[bool, str]:
        """Override localhost/loopback SSRF failures when testing allows them."""
        if not is_valid and allow_localhost:
            if "localhost" in error.lower() or "127.0.0" in error or "loopback" in error.lower():
                return True, ""
        return is_valid, error

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
    def validate_webhook_url_registration(cls, url: str) -> tuple[bool, str]:
        """Registration-time SSRF gate (no DNS required).

        Blocks known-bad hostnames and literal private IPs. Unresolvable
        public hostnames are allowed here; send-time re-checks with DNS
        (``validate_outbound_webhook_url``). When ``ADCP_TESTING=true``,
        localhost/loopback are allowed for capture servers.
        """
        import os

        allow_localhost = os.environ.get("ADCP_TESTING") == "true"
        is_valid, error = check_url_ssrf(url, resolve_dns=False)
        return cls._maybe_allow_localhost(is_valid, error, allow_localhost=allow_localhost)

    @classmethod
    def validate_outbound_webhook_url(cls, url: str) -> tuple[bool, str]:
        """Send-time SSRF gate (full DNS), with localhost allowance under ADCP_TESTING."""
        import os

        if os.environ.get("ADCP_TESTING") == "true":
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
        is_valid, error = check_url_ssrf(url)
        return cls._maybe_allow_localhost(is_valid, error, allow_localhost=allow_localhost)
