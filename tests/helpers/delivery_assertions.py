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

    The fields in WEBHOOK_ONLY_FIELDS (notification_type / sequence_number /
    next_expected_at / partial_data / unavailable_count) are "only present in
    webhook deliveries"; the polling response must carry none.
    Works on any serialized dict — a real transport `wire_response` or a
    `model_dump(mode="json")` of the response.
    """
    for field in sorted(WEBHOOK_ONLY_FIELDS):
        assert field not in wire, (
            f"{context} must omit webhook-only {field!r}, got {wire.get(field)!r} (keys={list(wire.keys())})"
        )


def assert_partial_data_pairing(payload: dict, *, context: str) -> None:
    """Assert the ``partial_data`` / ``unavailable_count`` pairing on a webhook body.

    The polling-response schema scopes ``unavailable_count`` to "only present in webhook
    deliveries when partial_data is true", so with ``partial_data`` false the field must
    be absent. (The dedicated webhook-payload schema words the same rule without an
    ``if/then`` and allows additional properties, so asserting the stricter polling
    reading is safe — omitting is conformant under both.)

    Production hardcodes ``partial_data = False`` today; this pins that pairing so a
    future partial-data change cannot silently put a spec-divergent shape on the wire.
    One home for the rule, shared by the integration and e2e wire graders.
    """
    assert payload.get("partial_data") is False, (
        f"{context}: expected partial_data False, got {payload.get('partial_data')!r}"
    )
    assert "unavailable_count" not in payload, (
        f"{context}: unavailable_count must be absent when partial_data is False, "
        f"got {payload.get('unavailable_count')!r}"
    )


def assert_next_expected_at_shape(payload: dict, *, present: bool, context: str) -> None:
    """Assert ``next_expected_at`` presence + date-time shape (or strict absence).

    Single source of truth for the rule, shared by the BDD steps, the integration
    wire tests and the e2e capture — they differ only in where the payload comes
    from, never in the rule (CLAUDE.md DRY invariant).

    A "final" notification must OMIT the field entirely: the schema types it as a
    NON-nullable date-time "only present ... when notification_type is not 'final'",
    so an explicit null is non-conforming (UC-004-SERIAL-01). When present it must be
    a full date-time string — date-only, empty, or non-parseable values are equally
    non-conforming, and each would slip past a bare ``is not None``.
    """
    from datetime import datetime

    has_key = "next_expected_at" in payload
    if not present:
        assert not has_key, (
            f"{context}: expected 'next_expected_at' absent for a final notification, "
            f"got {payload.get('next_expected_at')!r} (explicit null is non-conforming)"
        )
        return

    assert has_key, f"{context}: a non-final notification must include 'next_expected_at'; keys={list(payload.keys())}"
    value = payload["next_expected_at"]
    assert isinstance(value, str) and value, f"{context}: next_expected_at must be a date-time string, got {value!r}"
    assert "T" in value, (
        f"{context}: next_expected_at must be a full date-time (schema format 'date-time'), got date-only {value!r}"
    )
    # Normalize the RFC-3339 "Z" suffix so every caller's tolerance is identical
    # (this is exactly where the hand-rolled integration copy had already drifted).
    # Re-raise as AssertionError so the failure carries `context` — with four graders
    # sharing this helper, that prefix is the only thing identifying which one failed.
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise AssertionError(f"{context}: next_expected_at is not a parseable date-time: {value!r}") from None
