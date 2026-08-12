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
from collections.abc import Sequence
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


def assert_payload_field(
    result: TransportResult,
    field: str,
    expected: Any,
) -> None:
    """Assert a specific field on the payload matches expected value."""
    assert result.is_success, f"Expected success but got error: {result.error}"
    actual = getattr(result.payload, field)  # Let AttributeError propagate for typos
    assert actual == expected, f"payload.{field}: expected {expected!r}, got {actual!r}"


def assert_wire_omits_unset(
    result: TransportResult,
    *,
    schema: str | None,
    absent_paths: Sequence[str],
    transport: Transport,
) -> None:
    """Assert the literal wire response omits unset optional fields (never emits null).

    Regression for the #1710/#1868 null-leak class: FastMCP's ``ToolResult``
    serializes ``structured_content`` via ``pydantic_core.to_jsonable_python()``
    when it isn't already a plain dict, bypassing ``model_dump()``'s
    ``exclude_none=True`` default -- an unset optional field then serializes as
    invalid wire ``null`` instead of being omitted.

    Args:
        result: TransportResult from ``env.call_via()``. Must be success.
        schema: Pinned AdCP schema filename to validate the full wire response
            against (e.g. ``"protocol/get-adcp-capabilities-response.json"``),
            or ``None`` to skip full-schema validation -- use ``None`` when no
            pinned schema matches this response's actual shape (a documented,
            pre-existing spec-grounding gap), so the omission check still runs
            without asserting a structural contract that doesn't apply.
        absent_paths: Dotted paths (e.g. ``"media_buy.supported_pricing_models"``)
            that must be ABSENT from the wire -- stricter than "not null", the
            only check available when the field is itself nullable or no schema
            exists to type it. Each intermediate segment must resolve to a dict
            in the wire; a missing intermediate is a hard failure (not a vacuous
            pass), so a typo'd path can't silently no-op.
    """
    assert result.is_success, f"{transport}: expected success but got error: {result.error}"
    wire = result.wire_response
    assert wire is not None, f"{transport}: harness captured no wire response"

    if schema is not None:
        from tests.helpers.pinned_schema import validate_against_pinned_schema

        validate_against_pinned_schema(schema, wire)

    assert_omits_paths(wire, absent_paths, context=str(transport))


def assert_omits_paths(payload: dict, absent_paths: Sequence[str], *, context: str) -> None:
    """Assert every dotted path in *absent_paths* is ABSENT from *payload*.

    The dict-level core of the omission check, split out so callers holding a
    plain dict rather than a ``TransportResult`` -- a nested asset off a wire
    body, say -- use the same traversal instead of hand-rolling ``x not in y``
    and losing the intermediate-segment guard below.

    Stricter than "not null": each intermediate segment must resolve to a dict,
    so a typo'd path is a hard failure rather than a vacuous pass.
    """
    for path in absent_paths:
        segments = path.split(".")
        node = payload
        for segment in segments[:-1]:
            assert isinstance(node, dict) and segment in node, (
                f"{context}: cannot check '{path}' absence -- '{segment}' missing at this level: {node!r}"
            )
            node = node[segment]
        leaf = segments[-1]
        assert isinstance(node, dict), f"{context}: cannot check '{path}' absence -- parent is not an object: {node!r}"
        assert leaf not in node, (
            f"{context}: expected '{path}' absent (unset optional field must be omitted, not null), got {node[leaf]!r}"
        )
