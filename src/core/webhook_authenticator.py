"""Webhook authentication using HMAC signatures.

This module provides HMAC-SHA256 signing for webhook payloads to allow
receivers to verify that webhooks are genuinely from this server.

Similar to GitHub, Stripe, and Slack webhook authentication.
"""

import hashlib
import hmac
import json
import time
from datetime import UTC, datetime


class WebhookAuthenticator:
    """Handles webhook payload signing with HMAC-SHA256."""

    @staticmethod
    def sign_payload(payload: dict, secret: str) -> dict[str, str]:
        """
        Sign webhook payload with HMAC-SHA256.

        Creates a signature that receivers can verify to ensure the webhook
        is authentic and hasn't been tampered with.

        Args:
            payload: The webhook payload dict to sign
            secret: The shared secret key (from webhook config)

        Returns:
            Headers dict to include in webhook HTTP request
        """
        # Serialize payload consistently (sorted keys, no spaces)
        payload_str = json.dumps(payload, separators=(",", ":"), sort_keys=True)

        # Include timestamp to prevent replay attacks
        timestamp = str(int(time.time()))

        # Create signed message: timestamp.payload
        signed_payload = f"{timestamp}.{payload_str}"

        # Generate HMAC-SHA256 signature
        signature = hmac.new(secret.encode("utf-8"), signed_payload.encode("utf-8"), hashlib.sha256).hexdigest()

        adcp_timestamp = datetime.now(UTC).isoformat()
        adcp_signed_payload = f"{adcp_timestamp}.{payload_str}"
        adcp_signature = hmac.new(
            secret.encode("utf-8"), adcp_signed_payload.encode("utf-8"), hashlib.sha256
        ).hexdigest()

        return {
            "X-Webhook-Signature": f"sha256={signature}",
            "X-Webhook-Timestamp": timestamp,
            "X-ADCP-Signature": f"sha256={adcp_signature}",
            "X-ADCP-Timestamp": adcp_timestamp,
        }

    @staticmethod
    def verify_signature(
        payload: str, signature: str, timestamp: str, secret: str, tolerance_seconds: int = 300
    ) -> bool:
        """
        Verify webhook signature (for testing or customer reference).

        Args:
            payload: The raw payload string
            signature: The signature from X-Webhook-Signature header (with "sha256=" prefix)
            timestamp: The timestamp from X-Webhook-Timestamp header
            secret: The shared secret key
            tolerance_seconds: Max age of webhook to accept (default 5 minutes)

        Returns:
            True if signature is valid, False otherwise
        """
        # Check timestamp to prevent replay attacks
        try:
            webhook_time = int(timestamp)
            if abs(time.time() - webhook_time) > tolerance_seconds:
                return False
        except (ValueError, TypeError):
            return False

        # Remove "sha256=" prefix if present
        if signature.startswith("sha256="):
            signature = signature[7:]

        # Reconstruct signed message
        signed_payload = f"{timestamp}.{payload}"

        # Generate expected signature
        expected_signature = hmac.new(
            secret.encode("utf-8"), signed_payload.encode("utf-8"), hashlib.sha256
        ).hexdigest()

        # Use constant-time comparison to prevent timing attacks
        return hmac.compare_digest(signature, expected_signature)
