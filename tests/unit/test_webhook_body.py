"""The local compact-body helper must stay byte-identical to the SDK's.

The legacy HMAC scheme signs ``{timestamp}.{body}``. ``sign_legacy_webhook``
returns the body it signed, so signed senders are safe by construction. The
UNSIGNED sender branches and the verifier's dict path build the body
themselves via ``compact_webhook_body``, and nothing else forces those bytes
to match the SDK's canonical form.

If they drift, no test in the delivery suites notices \u2014 each one signs and
verifies with the same helper, so both sides move together and stay green.
The failure only appears in production, as a 401 in a buyer's receiver. These
tests are the pin that makes the drift fail here instead.
"""

import json

import pytest
from adcp import sign_legacy_webhook

from src.core.webhook_body import compact_webhook_body

SECRET = "a-test-webhook-secret-at-least-32-chars"

# Deliberately hostile to naive serialization: keys are NOT in sorted order,
# the payload carries non-ASCII text and a float, and it nests a dict whose
# keys are also unsorted. Each of these is a real drift source \u2014 sort_keys,
# ensure_ascii, and float repr all change the bytes without changing the JSON.
AWKWARD_PAYLOAD = {
    "zeta": "last key alphabetically, first in insertion order",
    "notification_type": "creative.status_changed",
    "amount": 1234.5,
    "advertiser": "M\u00fcnchen Stra\u00dfenbahn \u2014 \u00dc\u00c4\u00d6",
    "nested": {"z": 1, "a": 2},
    "emoji": "\U0001f3af",
}


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(AWKWARD_PAYLOAD, id="non-ascii-float-unsorted"),
        pytest.param({}, id="empty"),
        pytest.param({"only": "one"}, id="single-key"),
    ],
)
def test_compact_body_matches_sign_legacy_webhook(payload):
    """The helper's bytes are exactly the bytes the SDK would have signed."""
    _headers, sdk_body = sign_legacy_webhook(SECRET, payload)

    assert compact_webhook_body(payload) == sdk_body, (
        "compact_webhook_body drifted from adcp.sign_legacy_webhook's canonical body; "
        "unsigned senders and the verifier dict path would emit bytes a receiver "
        "cannot verify"
    )


def test_compact_body_preserves_insertion_order_not_sorted():
    """Sorting keys is the specific drift this helper must not introduce.

    Pinned separately from the SDK-equality test above: if BOTH the SDK and the
    helper started sorting, that test would still pass while every previously
    signed payload changed shape. This one fails on its own.
    """
    body = compact_webhook_body(AWKWARD_PAYLOAD).decode("utf-8")

    assert body.startswith('{"zeta":'), f"insertion order not preserved: {body[:40]!r}"
    assert body != json.dumps(AWKWARD_PAYLOAD, separators=(",", ":"), sort_keys=True), (
        "helper produced the sorted form; key order is not canonicalized by the spec"
    )


def test_compact_body_uses_compact_separators():
    """No spaces after ',' or ':' \u2014 the separator drift that caused #1441.

    Pinned as exact bytes on a payload with no punctuation inside its values,
    so the assertion cannot be fooled by a ``", "`` that is part of a string
    rather than a separator.
    """
    payload = {"b": 1, "a": {"x": 2, "y": 3}}

    assert compact_webhook_body(payload) == b'{"b":1,"a":{"x":2,"y":3}}'
    # the spaced default is what the old json= path emitted
    assert compact_webhook_body(payload).decode("utf-8") != json.dumps(payload)
