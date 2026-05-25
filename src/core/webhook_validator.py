"""Webhook URL validation to prevent SSRF attacks.

This module provides security validation for webhook URLs to prevent
Server-Side Request Forgery (SSRF) attacks where malicious users could
trick the server into making requests to internal services.
"""

import ipaddress
import os
from urllib.parse import urlparse

from src.core.security.url_validator import check_url_ssrf

LOCAL_TEST_HOSTNAMES = {"localhost", "host.docker.internal", "gateway.docker.internal", "docker.host.internal"}


def _env_truthy(name: str) -> bool:
    return os.getenv(name, "").lower() in ("1", "true", "yes")


def _has_valid_http_scheme_and_host(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme in ("http", "https") and bool(parsed.hostname)


def _is_local_test_destination(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host in LOCAL_TEST_HOSTNAMES:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class WebhookURLValidator:
    """Validates webhook URLs to prevent SSRF attacks."""

    @classmethod
    def validate_webhook_url(cls, url: str) -> tuple[bool, str]:
        """
        Validate webhook URL for SSRF protection.

        Production webhook destinations must use HTTPS. Local test receivers
        that need plain HTTP should call validate_for_testing().

        Args:
            url: The webhook URL to validate

        Returns:
            (is_valid, error_message) - is_valid is True if safe, error_message explains failures
        """
        return check_url_ssrf(url, require_https=True)

    @classmethod
    def validate_delivery_url(cls, url: str) -> tuple[bool, str]:
        """
        Validate a webhook URL immediately before outbound delivery.

        This is a defense-in-depth check for URLs that may have been stored
        before the current registration rules existed or inserted outside the
        normal API boundary. Production delivery requires HTTPS plus the SSRF
        blocklist. Local CI/dev stacks can still target loopback webhook
        receivers when ADCP_AUTH_TEST_MODE=true.
        """
        if (_env_truthy("WEBHOOK_ALLOW_PRIVATE_IPS") or _env_truthy("ADCP_AUTH_TEST_MODE")) and (
            _is_local_test_destination(url)
        ):
            if _has_valid_http_scheme_and_host(url):
                return True, ""
            return False, "URL must use http or https protocol and have a valid hostname"

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

        # If validation failed but it's a localhost error and we allow it
        if not is_valid and allow_localhost:
            if "localhost" in error.lower() or "127.0.0" in error or "loopback" in error.lower():
                return True, ""

        return is_valid, error
