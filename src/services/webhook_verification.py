"""Webhook signature verification reference for AdCP webhook receivers.

Delegates entirely to ``adcp.webhook_receiver.verify_webhook_hmac`` (the
installed ``adcp==6.6.0`` SDK), which verifies the HMAC over the RAW body
bytes as received — never a re-serialization of a parsed payload. Per AdCP
3.1.1 (``docs/building/by-layer/L3/webhooks.mdx:404-418``):
"Verifiers MUST use the raw HTTP body bytes as received on the wire,
captured before any JSON parse or re-serialize." A verifier that
re-serializes a parsed dict (this module's own prior implementation)
recreates, on the receive side, the exact signed-bytes-vs-wire-bytes
divergence GH #1802 fixes on the send side — and masks it, because
a re-serializing verifier and a re-serializing signer can agree with each
other while both disagree with the real wire.

This is a reference implementation for AdCP webhook *receivers* — this
application is a sender, not a receiver, and has no inbound webhook route in
``src/`` today (GH #1802's disease scan confirmed zero production
callers of this module). The duplicate-object-key rejection below is
conformance work for that reference, graded by the vendored spec vectors, not
production ingress policy.
"""

import time
from collections.abc import Mapping
from typing import Any

from adcp.signing.webhook_hmac import (
    LegacyWebhookHmacError,
    LegacyWebhookHmacOptions,
    verify_webhook_hmac,
)

from src.core.security.webhook_strict_json import DuplicateKeyInput, loads_rejecting_duplicate_keys


class WebhookVerificationError(Exception):
    """Raised when webhook verification fails."""

    pass


class WebhookBodyMalformedError(WebhookVerificationError):
    """Raised when a webhook's signature verifies but the body is malformed.

    Deliberately a plain ``Exception`` subclass (not a typed ``AdCPError``):
    neither this class nor its parent can reach a transport boundary today
    (no production caller, no inbound route — see module docstring), so the
    wire-code machinery buys nothing yet, and the spec explicitly leaves
    error-carrier internals implementation-defined. This is a stated
    decision, not leftover debt.

    Distinct from a bare :class:`WebhookVerificationError` (signature
    mismatch, bad timestamp, malformed header) per AdCP 3.1.1
    L1/security.mdx §Duplicate object keys: *"the signature IS valid; the
    body is malformed"* — verifier checklist step 14 names this identifier
    ``webhook_body_malformed``, distinct from
    ``webhook_signature_digest_mismatch``. Raised strictly AFTER
    ``verify_webhook_hmac`` succeeds — never before, and never in place of a
    genuine signature failure.
    """

    pass


class WebhookVerifier:
    """Verifies AdCP webhook signatures and timestamps over the raw received body."""

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

    def verify_webhook(self, body: bytes, headers: Mapping[str, str]) -> dict[str, Any] | None:
        """Verify a webhook's ``X-AdCP-Signature``/``X-AdCP-Timestamp`` over ``body``.

        Args:
            body: The RAW HTTP request body bytes, exactly as received on the
                wire — never a re-serialization of a parsed payload.
            headers: HTTP request headers (case-insensitive; any Mapping).

        Returns:
            The parsed JSON payload once the signature verifies and the body
            contains no duplicate object key at any depth. If ``body`` is not
            a JSON OBJECT at all — not valid JSON, or valid JSON that parses
            to something other than an object (a top-level array, a scalar,
            the literal ``null``) — ``None`` is returned instead. Duplicate-
            key detection only applies to objects, so this method does not
            require the body to be JSON, or JSON-object-shaped, to succeed;
            it only adds the duplicate-key MUST on top of signature
            verification for the bodies that are.

            Returning the parsed payload (rather than ``True``) eliminates a
            double-parse: this method used to instruct callers to
            "parse the body again" after verification, which is a second,
            independent parse of the same bytes — exactly the
            parser-differential shape AdCP 3.1.1's duplicate-key rule cites
            CVE-2017-12635 to warn against (a verifier and a business-logic
            parser disagreeing about what a body means). This return-type
            change is NOT required by the duplicate-key MUST itself — a
            ``bool`` return with an internal raise-on-duplicate would already
            satisfy "reject after HMAC succeeds" — it is adopted to close
            that separate parser-differential risk.

        Raises:
            WebhookVerificationError: Signature, timestamp, or header format
                failure.
            WebhookBodyMalformedError: The signature verified but the body
                contains a duplicate JSON object key (AdCP 3.1.1
                L1/security.mdx §Duplicate object keys, verifier checklist
                step 14) — raised strictly AFTER signature verification
                succeeds, never before, and never conflated with a genuine
                signature failure.
        """
        try:
            verify_webhook_hmac(
                headers=headers,
                body=body,
                options=LegacyWebhookHmacOptions(
                    secret=self.webhook_secret.encode("utf-8"),
                    sender_identity="webhook_verifier",
                    now=time.time(),
                    window_seconds=self.replay_window_seconds,
                ),
            )
        except LegacyWebhookHmacError as exc:
            raise WebhookVerificationError(str(exc)) from exc

        try:
            payload = loads_rejecting_duplicate_keys(body)
        except DuplicateKeyInput as exc:
            raise WebhookBodyMalformedError(str(exc)) from exc
        except ValueError:
            # Not valid JSON at all (e.g. an empty body) -- the duplicate-key
            # check doesn't apply to non-JSON content; the signature already
            # verified, so this webhook is accepted.
            return None

        # loads_rejecting_duplicate_keys parses arbitrary JSON, not just
        # objects -- a top-level array, scalar, or ``null`` all parse
        # cleanly and are none of them a duplicate-key candidate. Narrowing
        # here (rather than returning `payload` unconditionally) is what
        # makes ``None`` mean exactly one thing: "not a JSON object", never
        # conflated with "JSON object that happens to be empty" or "the
        # literal JSON null".
        return payload if isinstance(payload, dict) else None


def verify_adcp_webhook(
    webhook_secret: str,
    body: bytes,
    request_headers: Mapping[str, str],
    replay_window_seconds: int = 300,
) -> dict[str, Any] | None:
    """Convenience function to verify an AdCP webhook in one call.

    Args:
        webhook_secret: Shared secret for HMAC verification
        body: The RAW HTTP request body bytes, exactly as received on the
            wire — read them BEFORE any JSON parse, e.g. ``request.get_data()``
            in Flask or ``await request.body()`` in Starlette, never
            ``request.json()`` (which discards the exact bytes a signature
            was computed over).
        request_headers: HTTP request headers
        replay_window_seconds: Maximum age of webhook (default: 300s = 5 min)

    Returns:
        The parsed JSON payload (or ``None`` for a body that isn't a JSON
        object) — see :meth:`WebhookVerifier.verify_webhook`.

    Raises:
        WebhookVerificationError: Signature/timestamp/format failure.
        WebhookBodyMalformedError: Signature verified but the body contains a
            duplicate JSON object key.

    Example:
        try:
            payload = verify_adcp_webhook(
                webhook_secret=os.environ["WEBHOOK_SECRET"],
                body=request.get_data(),
                request_headers=dict(request.headers)
            )
            # payload is already parsed and duplicate-key-checked -- do not
            # parse request.get_data() again.
        except WebhookVerificationError as e:
            # Reject webhook (catches both a signature failure and a
            # malformed-body rejection, since WebhookBodyMalformedError
            # subclasses this)
            return {"error": str(e)}, 401
    """
    verifier = WebhookVerifier(webhook_secret, replay_window_seconds)
    return verifier.verify_webhook(body, request_headers)
