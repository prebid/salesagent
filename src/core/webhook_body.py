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
