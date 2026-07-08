"""Webhook signature verification utilities for AdCP webhook receivers.

This module provides utilities for webhook receivers to verify HMAC-SHA256
signatures and validate timestamps to prevent replay attacks per the AdCP
webhook spec.

The signed message is ``{timestamp}.{raw_http_body}`` — the RAW bytes exactly
as received. Verifiers must never re-parse and re-serialize the JSON before
verifying: key order and separators are not canonicalized by the spec, so a
re-serialization can (and in practice does) differ from the transmitted bytes
and reject valid signatures (#1441).
"""

import hashlib
import hmac
import json
from datetime import UTC, datetime
from typing import Any


class WebhookVerificationError(Exception):
    """Raised when webhook verification fails."""

    pass


class WebhookVerifier:
    """Verifies AdCP webhook signatures and timestamps."""

    def __init__(self, webhook_secret: str, replay_window_seconds: int = 300):
        """Initialize webhook verifier.

        Args:
            webhook_secret: Shared secret for HMAC verification (min 32 chars)
            replay_window_seconds: Maximum age of webhook in seconds (default: 300 = 5 minutes)
        """
        if len(webhook_secret) < 32:
            raise ValueError("Webhook secret must be at least 32 characters for security")

        self.webhook_secret = webhook_secret
        self.replay_window_seconds = replay_window_seconds

    def verify_webhook(
        self,
        payload: str | bytes | dict[str, Any],
        signature: str,
        timestamp: str,
    ) -> bool:
        """Verify webhook signature and timestamp.

        Args:
            payload: The RAW request body (str or bytes) exactly as received.
                Passing a dict is supported only as a deprecated convenience —
                it re-serializes compactly (the sender's canonical form) and
                will reject any payload whose wire bytes differ, which is
                exactly why raw-body verification is the contract.
            signature: HMAC signature from the X-ADCP-Signature header
                (``sha256=`` prefix accepted)
            timestamp: Timestamp from the X-ADCP-Timestamp header — unix
                seconds per spec (ISO-8601 accepted for back-compat)

        Returns:
            True if webhook is valid

        Raises:
            WebhookVerificationError: If verification fails
        """
        # Verify timestamp first (cheaper operation)
        self._verify_timestamp(timestamp)

        # Verify signature
        self._verify_signature(payload, signature, timestamp)

        return True

    def _verify_timestamp(self, timestamp: str):
        """Verify timestamp is recent (within replay window).

        Args:
            timestamp: Unix seconds (spec) or ISO-8601 (legacy)

        Raises:
            WebhookVerificationError: If timestamp is too old or invalid
        """
        # Spec format: unix seconds ("1720000000")
        webhook_time: datetime | None = None
        try:
            webhook_time = datetime.fromtimestamp(int(timestamp), tz=UTC)
        except (ValueError, TypeError, OverflowError, OSError):
            # Legacy ISO-8601 fallback
            try:
                webhook_time = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            except (ValueError, AttributeError) as e:
                raise WebhookVerificationError(f"Invalid timestamp format: {e}")
            if webhook_time.tzinfo is None:
                raise WebhookVerificationError("Timestamp must be timezone-aware (UTC)")

        # Check age
        age_seconds = (datetime.now(UTC) - webhook_time).total_seconds()

        if age_seconds < 0:
            raise WebhookVerificationError("Timestamp is in the future")

        if age_seconds > self.replay_window_seconds:
            raise WebhookVerificationError(
                f"Timestamp too old ({age_seconds:.0f}s > {self.replay_window_seconds}s window)"
            )

    def _verify_signature(
        self,
        payload: str | bytes | dict[str, Any],
        provided_signature: str,
        timestamp: str,
    ):
        """Verify HMAC-SHA256 over ``{timestamp}.{raw_body}``.

        Args:
            payload: Raw request body (str/bytes); dict accepted as a
                deprecated convenience (compact re-serialization)
            provided_signature: Signature from header (``sha256=`` prefix ok)
            timestamp: Timestamp header value, exactly as received

        Raises:
            WebhookVerificationError: If signature doesn't match
        """
        if isinstance(payload, bytes):
            payload_str = payload.decode("utf-8")
        elif isinstance(payload, dict):
            # Deprecated dict path: reconstruct the sender's canonical compact
            # form. Only correct when the sender serialized compactly
            # (separators=(",", ":"), insertion order) — raw-body input is
            # the reliable contract.
            payload_str = json.dumps(payload, separators=(",", ":"))
        else:
            payload_str = payload

        # Accept the sha256= prefix the spec's header format carries
        if provided_signature.startswith("sha256="):
            provided_signature = provided_signature[7:]

        # Create signature input: timestamp + raw body
        message = f"{timestamp}.{payload_str}"

        # Generate expected signature
        expected_signature = hmac.new(
            self.webhook_secret.encode("utf-8"),
            message.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        # Constant-time comparison to prevent timing attacks
        if not hmac.compare_digest(provided_signature, expected_signature):
            raise WebhookVerificationError("Signature verification failed")

    @staticmethod
    def extract_headers(request_headers: dict[str, str]) -> tuple[str, str]:
        """Extract signature and timestamp from request headers.

        Args:
            request_headers: HTTP request headers (case-insensitive)

        Returns:
            Tuple of (signature, timestamp)

        Raises:
            WebhookVerificationError: If required headers are missing
        """
        # Normalize header names to lowercase for case-insensitive lookup
        headers_lower = {k.lower(): v for k, v in request_headers.items()}

        signature = headers_lower.get("x-adcp-signature")
        timestamp = headers_lower.get("x-adcp-timestamp")

        if not signature:
            raise WebhookVerificationError("Missing X-ADCP-Signature header")

        if not timestamp:
            raise WebhookVerificationError("Missing X-ADCP-Timestamp header")

        return signature, timestamp


def verify_adcp_webhook(
    webhook_secret: str,
    payload: str | bytes | dict[str, Any],
    request_headers: dict[str, str],
    replay_window_seconds: int = 300,
) -> bool:
    """Convenience function to verify an AdCP webhook in one call.

    Args:
        webhook_secret: Shared secret for HMAC verification
        payload: RAW request body (str/bytes) exactly as received — preferred.
            A dict is accepted only as a deprecated convenience.
        request_headers: HTTP request headers
        replay_window_seconds: Maximum age of webhook (default: 300s = 5 min)

    Returns:
        True if webhook is valid

    Raises:
        WebhookVerificationError: If verification fails

    Example:
        try:
            verify_adcp_webhook(
                webhook_secret=os.environ["WEBHOOK_SECRET"],
                payload=request.body,          # RAW bytes, not request.json()
                request_headers=dict(request.headers)
            )
            # Process webhook
        except WebhookVerificationError as e:
            # Reject webhook
            return {"error": str(e)}, 401
    """
    verifier = WebhookVerifier(webhook_secret, replay_window_seconds)
    signature, timestamp = verifier.extract_headers(request_headers)
    return verifier.verify_webhook(payload, signature, timestamp)
