"""Shared assertion helpers for multi-transport behavioral tests.

These helpers verify transport-specific envelope shapes and shared
payload properties. Use with TransportResult from dispatchers.

Usage::

    result = env.call_via(Transport.REST, creatives=[...])
    assert_envelope(result, Transport.REST)
    assert result.is_success
    assert result.payload.creatives[0].action == CreativeAction.created
"""

from __future__ import annotations

import re
from typing import Any

from tests.harness.transport import Transport, TransportResult
from tests.helpers.envelope_assertions import assert_envelope_shape


def assert_envelope(result: TransportResult, transport: Transport) -> None:
    """Assert transport-specific envelope shape is correct."""
    assert result.envelope.get("transport") == transport.value, (
        f"Expected envelope transport={transport.value}, got {result.envelope}"
    )

    if transport == Transport.REST:
        assert_rest_envelope(result)


def assert_rest_envelope(result: TransportResult, expected_status: int = 200) -> None:
    """Assert REST-specific envelope: HTTP status + content-type."""
    assert result.envelope.get("status_code") == expected_status, (
        f"Expected HTTP {expected_status}, got {result.envelope.get('status_code')}"
    )
    content_type = result.envelope.get("content_type", "")
    assert "application/json" in content_type, f"Expected JSON content-type, got {content_type}"


def assert_error_result(
    result: TransportResult,
    expected_type: type[Exception],
    match: str | None = None,
) -> None:
    """Assert result is an error of the expected type, optionally matching message."""
    assert result.is_error, f"Expected error but got success: {result.payload}"
    assert isinstance(result.error, expected_type), (
        f"Expected {expected_type.__name__}, got {type(result.error).__name__}: {result.error}"
    )
    if match is not None:
        assert re.search(match, str(result.error)), (
            f"Error message {str(result.error)!r} does not match pattern {match!r}"
        )


def assert_rejected(
    result: TransportResult,
    *,
    code: str | None = None,
    field: str | None = None,
    reason: str | None = None,
    message_contains: str | None = None,
) -> None:
    """Assert the request was rejected, checking WHAT field and WHY.

    Checks observable behavior — what the buyer sees — not which internal
    layer caught the error. Works across all transports and environments.

    Args:
        result: TransportResult from env.call_via()
        code: Expected error code (e.g., "VALIDATION_ERROR").
        field: Expected field name (e.g., "max_width", "agent_url").
        reason: Expected error reason (e.g., "Field required",
            "Input should be a valid integer"). This distinguishes
            "field missing" from "field has wrong type" on the same field.
        message_contains: Additional substring that must appear in the error.
    """
    assert result.is_error, f"Expected rejection but got success: {result.payload}"

    error = result.error
    error_str = str(error)

    if code is not None:
        error_code = getattr(error, "error_code", None)
        assert error_code == code or code in error_str, (
            f"Expected error code '{code}', got {error_code!r}. Full error: {error_str[:200]}"
        )

    if field is not None:
        details = getattr(error, "details", None) or {}
        details_str = str(details)
        assert field in error_str or field in details_str, (
            f"Expected field '{field}' in error. Error: {error_str[:200]}"
        )

    if reason is not None:
        assert reason in error_str, f"Expected reason '{reason}' in error. Got: {error_str[:200]}"

    if message_contains is not None:
        message = getattr(error, "message", error_str)
        assert message_contains in str(message), f"Expected '{message_contains}' in message. Got: {str(message)[:200]}"


def assert_replayed_rejection(
    result: TransportResult,
    *,
    code: str,
    message_contains: str | None = None,
    recovery: str | None = None,
) -> None:
    """Assert a *replayed* cached rejection raised through the boundary as a two-layer envelope.

    With the raise-based idempotency model, a replayed rejection is re-raised as a
    typed ``AdCPError`` (``replayed=True``), so it surfaces as an ERROR result
    carrying the real two-layer wire envelope — byte-identical to the fresh reject,
    plus the spec ``replayed: true`` marker (``ProtocolEnvelope.replayed``).

    Args:
        result: TransportResult from a wire transport (REST/A2A/MCP).
        code: Expected wire error code; must match both envelope layers.
        message_contains: Substring that must appear in ``errors[0].message``. Pass
            the full cached message for a byte-identical round-trip check.
        recovery: Expected recovery hint in both layers (optional).
    """
    assert result.is_error, f"Expected a (raised) replayed rejection but got success: {result.payload}"
    envelope = result.wire_error_envelope or result.synthesized_error_envelope
    assert envelope is not None, f"Replayed rejection must carry a wire error envelope. Error: {result.error}"
    assert_envelope_shape(envelope, code, recovery=recovery, message_substr=message_contains)
    assert envelope.get("replayed") is True, (
        f"Replayed rejection envelope must carry replayed=true (spec ProtocolEnvelope.replayed). Got: {envelope}"
    )


def assert_rejected_with_suggestion(
    result: TransportResult,
    *,
    code: str,
    suggestion_contains: str | None = None,
) -> None:
    """Assert rejection with a suggestion for the caller.

    Checks error code and optionally that a suggestion/recovery hint is present.
    On transports that support structured details (impl/a2a), checks details dict.
    On MCP, checks the error message string (details serialized as JSON in the message).
    """
    assert_rejected(result, code=code)

    error = result.error
    error_str = str(error)

    # Check for suggestion in details or message
    details = getattr(error, "details", None) or {}
    has_suggestion = bool(details.get("suggestion")) or "suggestion" in error_str.lower()

    if suggestion_contains is not None:
        details_str = str(details)
        assert suggestion_contains in error_str or suggestion_contains in details_str, (
            f"Expected suggestion containing '{suggestion_contains}'. "
            f"Error: {error_str[:200]}, Details: {details_str[:200]}"
        )
    else:
        assert has_suggestion, (
            f"Expected suggestion in error with code '{code}'. Error: {error_str[:200]}, Details: {details}"
        )


def assert_payload_field(
    result: TransportResult,
    field: str,
    expected: Any,
) -> None:
    """Assert a specific field on the payload matches expected value."""
    assert result.is_success, f"Expected success but got error: {result.error}"
    actual = getattr(result.payload, field)  # Let AttributeError propagate for typos
    assert actual == expected, f"payload.{field}: expected {expected!r}, got {actual!r}"
