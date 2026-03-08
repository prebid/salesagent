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


def assert_payload_field(
    result: TransportResult,
    field: str,
    expected: Any,
) -> None:
    """Assert a specific field on the payload matches expected value."""
    assert result.is_success, f"Expected success but got error: {result.error}"
    actual = getattr(result.payload, field, None)
    assert actual == expected, f"payload.{field}: expected {expected!r}, got {actual!r}"
