"""Shared delivery-response assertions.

Keeps the webhook-only-field omission check (#1570) in one place so every poll
site asserts the SAME full set — the constant `WEBHOOK_ONLY_FIELDS` is the
production source of truth, so a field added there is enforced everywhere at
once (avoids the drift where some sites silently dropped `sequence_number`).
"""

from __future__ import annotations

from src.core.tools._media_buy_status import WEBHOOK_ONLY_FIELDS


def assert_omits_webhook_only_fields(wire: dict, *, context: str = "synchronous poll") -> None:
    """Assert a serialized delivery body omits ALL webhook-only fields (#1570).

    The three fields (notification_type / sequence_number / next_expected_at) are
    "only present in webhook deliveries"; the polling response must carry none.
    Works on any serialized dict — a real transport `wire_response` or a
    `model_dump(mode="json")` of the response.
    """
    for field in sorted(WEBHOOK_ONLY_FIELDS):
        assert field not in wire, (
            f"{context} must omit webhook-only {field!r}, got {wire.get(field)!r} (keys={list(wire.keys())})"
        )
