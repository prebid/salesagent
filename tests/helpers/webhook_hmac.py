"""One assertion for the AdCP legacy-HMAC byte-equality contract.

The contract is narrow: the signature covers ``{unix_timestamp}.{raw_body}``,
where ``raw_body`` is the exact bytes on the wire. Recomputing it from a
re-serialized payload is the receiver-side bug #1441 fixed, and a test that
re-serializes passes for the wrong reason -- sender and test share the same
wrong serialization, so both move together and stay green.

This assertion was copy-pasted across the webhook suites with drift: some
copies checked that the timestamp header is unix seconds, others did not, so
an ISO timestamp could regress in the files missing the check. Keeping one
implementation means the contract is graded identically everywhere.
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Mapping

SIGNATURE_HEADER = "x-adcp-signature"
TIMESTAMP_HEADER = "x-adcp-timestamp"


def sign_over_transmitted_bytes(secret: str, body: bytes, timestamp: str) -> str:
    """Return the hex HMAC-SHA256 for the legacy signed message ``{timestamp}.{body}``.

    The produce half of the byte-equality contract: the exact digest a sender
    must place (``sha256=``-prefixed) in ``X-AdCP-Signature`` for *body*
    transmitted verbatim. Kept next to the verify assertion so sender-side
    recompute in tests has one home and cannot drift from what this module
    verifies.
    """
    return hmac.new(secret.encode("utf-8"), timestamp.encode("utf-8") + b"." + body, hashlib.sha256).hexdigest()


def assert_signature_headers_present(headers: Mapping[str, str]) -> tuple[str, str]:
    """Assert the AdCP signature/timestamp headers are present and spec-formatted.

    The format-only half of the byte-equality contract -- everything gradable
    WITHOUT the secret or the raw body: both headers present, the signature
    ``sha256=``-prefixed over a 64-char hex digest, and the timestamp unix
    seconds. Callers that also hold the secret and raw bytes should prefer
    :func:`assert_hmac_over_transmitted_bytes`, which layers digest
    verification on top of these same checks.

    This is the ONE home for the format contract: presence-only, sig-prefix-only,
    and isdigit-only copies had forked across the webhook suites, so a sender
    emitting a bare-hex signature or an ISO timestamp could stay green in the
    looser copies while the helper-based suites went red. Grading every site
    through here keeps the contract identical everywhere.

    Args:
        headers: Response/request headers; looked up case-insensitively, since
            ``requests``/``httpx``/``BaseHTTPRequestHandler`` each normalize
            header casing differently.

    Returns:
        The ``(signature_hex, timestamp)`` pair -- signature with the
        ``sha256=`` prefix stripped -- so a caller adding digest verification
        need not re-lower or re-strip.
    """
    lowered = {k.lower(): v for k, v in headers.items()}

    assert SIGNATURE_HEADER in lowered, f"missing {SIGNATURE_HEADER}; got {sorted(lowered)}"
    assert TIMESTAMP_HEADER in lowered, f"missing {TIMESTAMP_HEADER}; got {sorted(lowered)}"

    raw_signature = lowered[SIGNATURE_HEADER]
    timestamp = lowered[TIMESTAMP_HEADER]

    # Each of these was asserted by SOME copy of this block and not others.
    assert raw_signature.startswith("sha256="), f"spec signature header is sha256=-prefixed, got {raw_signature!r}"
    assert timestamp.isdigit(), f"spec timestamp is unix seconds, got {timestamp!r}"

    signature = raw_signature.removeprefix("sha256=")
    assert len(signature) == 64, f"HMAC-SHA256 hex digest is 64 chars, got {len(signature)}: {signature!r}"

    return signature, timestamp


def assert_hmac_over_transmitted_bytes(
    secret: str,
    body: bytes,
    headers: Mapping[str, str],
    *,
    cross_check_receivers: bool = True,
) -> None:
    """Assert the HMAC in *headers* verifies over *body* exactly as transmitted.

    Args:
        secret: The shared signing secret.
        body: The RAW bytes as sent/received -- never a re-serialized payload.
        headers: Response/request headers; looked up case-insensitively, since
            ``requests``/``httpx``/``BaseHTTPRequestHandler`` each normalize
            header casing differently.
        cross_check_receivers: Also feed the raw body through both in-repo
            receiver references (``WebhookAuthenticator.verify_signature`` and
            ``verify_adcp_webhook``) and require they agree. Defaults on;
            disable only where a caller has no real transport.
    """
    signature, timestamp = assert_signature_headers_present(headers)

    expected = sign_over_transmitted_bytes(secret, body, timestamp)
    assert signature == expected, (
        "HMAC does not verify over the transmitted raw bytes -- the sender signed "
        "something other than what it put on the wire"
    )

    if cross_check_receivers:
        from src.core.webhook_authenticator import WebhookAuthenticator
        from src.services.webhook_verification import verify_adcp_webhook

        assert WebhookAuthenticator.verify_signature(body, signature, timestamp, secret), (
            "WebhookAuthenticator rejected a signature that verifies by hand"
        )
        assert verify_adcp_webhook(secret, body, headers), (
            "verify_adcp_webhook rejected a signature that verifies by hand"
        )
