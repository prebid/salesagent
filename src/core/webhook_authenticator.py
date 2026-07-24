"""Webhook signature verification (buyer/receiver reference).

Verifies the AdCP legacy HMAC-SHA256 scheme: the signature covers
``{unix_timestamp}.{raw_http_body}`` — the raw bytes exactly as received,
never a re-serialization. Similar to GitHub, Stripe, and Slack webhook
authentication.

SENDING side note: the signer that used to live here (``sign_payload``) was
removed — it signed a sorted-compact re-serialization while the HTTP client
re-serialized the body differently, so its signatures never matched the wire
(#1441). Senders now use :func:`adcp.sign_legacy_webhook`, which returns the
signed headers together with the exact ``body_bytes`` to transmit.
"""

import hashlib
import hmac
import time


class WebhookAuthenticator:
    """Verifies webhook payload signatures (HMAC-SHA256, raw-body)."""

    @staticmethod
    def verify_signature(
        payload: str, signature: str, timestamp: str, secret: str, tolerance_seconds: int = 300
    ) -> bool:
        """
        Verify a webhook signature against the RAW request body.

        ``payload`` must be the raw HTTP body exactly as received (decoded
        text) — re-parsing and re-serializing the JSON before verification
        breaks the byte-equality contract and rejects valid signatures.

        Args:
            payload: The raw payload string (exact received bytes, decoded)
            signature: The signature from the X-AdCP-Signature header (with
                "sha256=" prefix)
            timestamp: The unix-seconds timestamp from X-AdCP-Timestamp
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
