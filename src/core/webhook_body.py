"""Canonical compact JSON body for AdCP legacy-HMAC webhooks.

The legacy HMAC scheme signs ``{timestamp}.{body}``, so the bytes that get
signed and the bytes that get POSTed must be identical — re-serializing
between the two is the drift that caused #1441. The SDK's
``sign_legacy_webhook`` returns that body alongside the headers, and senders
that call it should POST the bytes it hands back rather than rebuilding them.

This function exists for the sites that must produce the same bytes WITHOUT
signing: the unsigned sender branches, and the verifier's deprecated dict
path. Keeping them on one implementation means a change to the canonical form
moves one line instead of four, and the guard test pins the output to
``sign_legacy_webhook``'s own body so a drift in the SDK fails loudly here
rather than as a 401 in a buyer's receiver.
"""

import json
from typing import Any


def compact_webhook_body(payload: dict[str, Any]) -> bytes:
    """Serialize *payload* to the canonical compact on-wire form.

    Compact separators (``","`` / ``":"``) and INSERTION order — deliberately
    not ``sort_keys``. The spec does not canonicalize key order, so sorting
    here would diverge from the SDK's body and break byte-equality for any
    payload whose keys are not already sorted.
    """
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


def sign_or_serialize(
    headers: dict[str, str],
    payload: dict[str, Any],
    secret: str | None,
    *,
    timestamp: str | int | None = None,
) -> bytes:
    """Serialize *payload* once and return the exact bytes to transmit.

    One home for the sign-or-serialize pairing that keeps signed-bytes ==
    sent-bytes across all three senders. When *secret* is truthy the body is
    signed via ``sign_legacy_webhook`` and the spec signature headers
    (``X-AdCP-Signature`` / ``X-AdCP-Timestamp``) are merged into *headers* in
    place; the returned bytes are the SIGNED bytes, so the caller MUST POST them
    verbatim (``data=``/``content=``, never ``json=``). When *secret* is falsy
    the body is the unsigned compact form and no signature headers are added.

    Callers keep their own auth gating around this call (which secret to sign
    with, weak-secret checks, Bearer tokens) and route the body through here so
    a future edit cannot sign on one arm while skipping the compact form on the
    other. ``sign_legacy_webhook`` is imported lazily to keep this module (and
    thus the stdlib-only verifier that imports :func:`compact_webhook_body`)
    free of the SDK sender dependency.
    """
    if secret:
        from adcp import sign_legacy_webhook

        signed_headers, body_bytes = sign_legacy_webhook(secret, payload, timestamp=timestamp)
        headers.update(signed_headers)
        return body_bytes
    return compact_webhook_body(payload)
