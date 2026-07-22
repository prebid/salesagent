"""Verifier paths that were documented but ungraded.

``WebhookVerifier`` is a receiver-side reference: buyers read it to learn how
to verify an AdCP legacy-HMAC webhook. Two of its branches had prose but no
test -- the timestamp parser and the deprecated dict-body path -- so nothing
stopped them from drifting away from the sender, or from the repo's OTHER
reference verifier (``WebhookAuthenticator``).

The timestamp branch is graded strictly here: unix seconds per spec, and an
ISO-8601 string rejected. Accepting ISO is what made the two verifiers
disagree on the same webhook.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime

import pytest

from src.core.webhook_authenticator import WebhookAuthenticator
from src.core.webhook_body import compact_webhook_body
from src.services.webhook_verification import (
    WebhookVerificationError,
    WebhookVerifier,
    verify_adcp_webhook,
)

SECRET = "verifier-unit-secret-0123456789abcdef"  # 32+ chars
PAYLOAD = {"zeta": "caf\u00e9", "alpha": 250.0, "nested": {"b": 1, "a": 2}}


def _sign(body: bytes, timestamp: str) -> str:
    return hmac.new(SECRET.encode("utf-8"), timestamp.encode("utf-8") + b"." + body, hashlib.sha256).hexdigest()


def _now_unix() -> str:
    return str(int(datetime.now(UTC).timestamp()))


class TestTimestampFormat:
    """Unix seconds are the spec format; ISO-8601 is not accepted."""

    def test_unix_seconds_accepted(self):
        body = compact_webhook_body(PAYLOAD)
        ts = _now_unix()
        assert verify_adcp_webhook(
            SECRET, body, {"X-AdCP-Signature": f"sha256={_sign(body, ts)}", "X-AdCP-Timestamp": ts}
        )

    def test_iso8601_rejected(self):
        """The removed legacy fallback: ISO is not a spec timestamp.

        Deletion oracle: restore the ``datetime.fromisoformat`` fallback in
        ``_verify_timestamp`` and this reddens — the ISO timestamp is accepted,
        no ``WebhookVerificationError`` is raised, and ``pytest.raises`` fails.
        """
        body = compact_webhook_body(PAYLOAD)
        iso = datetime.now(UTC).isoformat()

        with pytest.raises(WebhookVerificationError, match="unix seconds"):
            WebhookVerifier(SECRET).verify_webhook(body, f"sha256={_sign(body, iso)}", iso)

    def test_non_numeric_rejected(self):
        body = compact_webhook_body(PAYLOAD)
        with pytest.raises(WebhookVerificationError, match="unix seconds"):
            WebhookVerifier(SECRET).verify_webhook(body, f"sha256={_sign(body, 'not-a-time')}", "not-a-time")

    def test_both_reference_verifiers_agree_on_iso(self):
        """The divergence this removal closes.

        ``WebhookAuthenticator`` has always rejected ISO (it does ``int()``
        with no fallback). Before this change ``WebhookVerifier`` accepted it,
        so the same webhook verified against one reference and not the other.
        """
        body = compact_webhook_body(PAYLOAD)
        iso = datetime.now(UTC).isoformat()
        signature = f"sha256={_sign(body, iso)}"

        assert not WebhookAuthenticator.verify_signature(body.decode("utf-8"), signature, iso, SECRET)
        with pytest.raises(WebhookVerificationError):
            WebhookVerifier(SECRET).verify_webhook(body, signature, iso)

    def test_stale_timestamp_rejected(self):
        body = compact_webhook_body(PAYLOAD)
        stale = str(int(datetime.now(UTC).timestamp()) - 4000)  # > 300s window
        with pytest.raises(WebhookVerificationError):
            WebhookVerifier(SECRET).verify_webhook(body, f"sha256={_sign(body, stale)}", stale)


class TestDictBodyPath:
    """The deprecated dict path must reconstruct exactly what a compact sender sent."""

    def test_dict_payload_verifies_against_compact_sender_bytes(self):
        """A dict body verifies only because it rebuilds the compact form.

        This is the path's entire contract, and it was untested: the verifier
        re-serializes the dict and must land on the sender's exact bytes.
        """
        body = compact_webhook_body(PAYLOAD)
        ts = _now_unix()
        signature = f"sha256={_sign(body, ts)}"

        # Same signature, dict input instead of raw bytes.
        assert WebhookVerifier(SECRET).verify_webhook(PAYLOAD, signature, ts)

    def test_dict_path_fails_for_a_non_compact_sender(self):
        """And why raw bytes are the reliable contract.

        A sender that emitted the SPACED form produces a signature the dict
        path cannot reproduce, because the dict path always rebuilds compact.
        Documents the known limitation rather than leaving it implied.
        """
        spaced_body = json.dumps(PAYLOAD).encode("utf-8")  # default separators
        ts = _now_unix()
        signature = f"sha256={_sign(spaced_body, ts)}"

        # Raw bytes: verifies, because the verifier sees what was sent.
        assert WebhookVerifier(SECRET).verify_webhook(spaced_body, signature, ts)

        # Same webhook as a dict: cannot verify.
        with pytest.raises(WebhookVerificationError):
            WebhookVerifier(SECRET).verify_webhook(PAYLOAD, signature, ts)
