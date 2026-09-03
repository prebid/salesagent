"""Grading for HMAC-SHA256 webhook signatures, over the RAW bytes received.

Recomputing over the origin's captured ``.body`` (rather than a fresh
serialization of the payload dict) is the whole point: a recompute from the
dict uses whatever serialization formula the caller happens to pick, which
can silently agree with a sender that signed one serialization and
transmitted another — the exact defect GH #1802 fixed. Several test
files need to grade that same signed-bytes-equal-wire-bytes property, so it
lives here once instead of six near-identical copies drifting apart.
"""

from __future__ import annotations

import hashlib
import hmac
from typing import Any

# The two header names AdCP 3.1.1 pins for the legacy HMAC-SHA256 fallback
# (``docs/building/by-layer/L3/webhooks.mdx:404-418``). Named
# constants rather than inline defaults because tests that grade the ABSENCE of
# a signature need the same name this module verifies against: a second string
# literal spelled somewhere else would keep passing after a header rename, which
# is precisely the regression an absence assertion exists to catch.
SIGNATURE_HEADER = "X-AdCP-Signature"
TIMESTAMP_HEADER = "X-AdCP-Timestamp"


def assert_signature_verifies_over_wire_body(
    request: Any,
    secret: str,
    *,
    signature_header: str = SIGNATURE_HEADER,
    timestamp_header: str = TIMESTAMP_HEADER,
) -> None:
    """Assert ``request``'s HMAC signature verifies against ``request.body``.

    Args:
        request: an ``OriginRequest`` (or equivalent) exposing ``.headers``
            (case-insensitive) and ``.body`` (the raw bytes the origin
            actually received).
        secret: the shared HMAC secret the sender signed with.
        signature_header: the header carrying ``sha256=<hex>``.
        timestamp_header: the header carrying the unix-seconds timestamp the
            signed message is ``f"{timestamp}."`` prefixed with.

    Raises:
        AssertionError: if the header is missing the ``sha256=`` prefix, or
            if the signature does not verify against ``request.body`` — the
            message that means for a real buyer: the signature was computed
            over bytes other than the ones that crossed the socket.
    """
    sent_signature = request.headers[signature_header]
    timestamp = request.headers[timestamp_header]

    # Named BEFORE the prefix check. A missing header reads as ``None`` from an
    # ``http.client.HTTPMessage``, so ``.startswith`` would raise AttributeError
    # and report a delivery that went out entirely UNSIGNED as a Python bug
    # rather than as the security failure it is (GH #1802).
    assert sent_signature is not None, (
        f"the delivery carries no {signature_header} at all — it went out UNSIGNED, "
        f"so no receiver can attribute it to us"
    )
    assert timestamp is not None, f"the delivery carries no {timestamp_header}, so its signature cannot be verified"

    assert sent_signature.startswith("sha256="), sent_signature

    expected = hmac.new(
        secret.encode("utf-8"),
        f"{timestamp}.".encode() + request.body,
        hashlib.sha256,
    ).hexdigest()

    assert sent_signature == f"sha256={expected}", (
        f"the buyer's endpoint could not verify this webhook: {signature_header} was "
        f"computed over bytes other than the {len(request.body)} that crossed the "
        f"socket ({request.body[:120]!r}...)"
    )


def assert_delivered_unsigned(env: Any, *, signature_header: str = SIGNATURE_HEADER) -> None:
    """Assert exactly one delivery arrived and it carries NO signature header.

    The absence half is named against ``SIGNATURE_HEADER`` -- the same constant
    :func:`assert_signature_verifies_over_wire_body` verifies through -- so a
    header rename cannot leave this passing vacuously against a name nothing
    emits any more. That is the one way an absence assertion silently stops
    grading.

    The count half is not decoration either: "no signature header" is vacuously
    true of a request that never happened, so ``delivery_attempts == 1`` is what
    makes the header claim a claim about a real delivery -- and, where this is
    used as the CONTROL for a signed case, what makes it the same single send,
    differing only in its signing.

    This module's docstring already said the signed half lives here "instead of
    six near-identical copies drifting apart". Its negative twin had three.
    """
    assert env.delivery_attempts == 1, (
        f"expected exactly one delivery to grade, saw {env.delivery_attempts} — "
        f"an absence-of-signature claim about zero deliveries is vacuous"
    )
    assert signature_header not in env.last_delivery.headers, (
        f"a delivery that did not ask for HMAC-SHA256 carries {signature_header} — "
        f"signing is gated by the scheme, not by whether a credential exists"
    )
