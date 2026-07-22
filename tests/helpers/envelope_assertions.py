"""Unified AdCP two-layer error envelope assertion.

Single helper for the wire shape every transport boundary must emit::

    {
        "adcp_error": {"code": "...", "message": "...", "recovery": "..."},
        "errors":     [{"code": "...", "message": "...", "recovery": "..."}],
        "context":    {...},   # optional
    }

Replaces the per-boundary helpers (``_assert_two_layer_envelope``,
``_assert_mcp_envelope``, ``_assert_a2a_envelope``, ``_assert_rest_envelope``)
that all verified the same shape with diverging signatures. A spec change to
the envelope now requires updating exactly one helper.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from tests.harness.transport import Transport, TransportResult


def error_envelope_for_raw_a2a_env(result: TransportResult, transport: Transport) -> dict[str, Any] | None:
    """Pick the error envelope to assert on for an env whose A2A path is the raw wrapper.

    Some envs (e.g. ``CreativeSyncEnv``) route ``call_a2a`` through the direct
    ``*_raw`` wrapper rather than ``_run_a2a_handler`` — no Task framing, so no
    captured wire. For those envs the transports that observe real wire bytes are
    **REST and MCP** (``wire_error_envelope``); **IMPL and A2A** have only the
    boundary-builder output (``synthesized_error_envelope``).

    This is deliberately per-transport, not a ``wire or synthesized`` fallback: a
    ``None`` ``wire_error_envelope`` on REST/MCP is a genuine boundary regression
    and must surface as a failed ``assert_envelope_shape``, not be silently masked
    by falling back to the synthesized value.
    """
    from tests.harness.transport import Transport

    if transport in (Transport.REST, Transport.MCP):
        return result.wire_error_envelope
    return result.synthesized_error_envelope


def assert_no_raw_validation_leak(message: str) -> None:
    """Assert a buyer-facing validation message omits raw Pydantic internals."""
    assert "input_value" not in message, f"raw Pydantic input leaked into validation message: {message!r}"
    assert "errors.pydantic.dev" not in message, f"Pydantic documentation URL leaked into message: {message!r}"


def assert_envelope_field(envelope: dict, field: str) -> None:
    """Assert BOTH envelope layers name ``field`` as the offending field.

    The buyer's remediation pointer lives in two places — ``adcp_error.field``
    and ``errors[0].field`` — and a boundary can drop one while keeping the
    other. Three step modules hand-rolled this check and one of the copies
    omitted the top layer, so a top-layer regression was invisible wherever
    that copy was used. One home, so the copies cannot drift again.
    """
    errors_field = (envelope.get("errors") or [{}])[0].get("field")
    assert errors_field == field, f"errors[0].field={errors_field!r}, expected {field!r}"
    adcp_field = (envelope.get("adcp_error") or {}).get("field")
    assert adcp_field == field, f"adcp_error.field={adcp_field!r}, expected {field!r}"


def assert_envelope_shape(
    target: Any,
    code: str,
    *,
    recovery: str,
    message_substr: str | None = None,
    check_mcp_tool_error: bool = False,
) -> None:
    """Assert the AdCP spec two-layer error envelope shape.

    Args:
        target: The envelope under test. Accepts either:
                - a ``dict`` (REST JSON body, A2A ``error.data``, raw envelope),
                - an ``AdCPToolError`` (MCP boundary) — its ``.envelope`` attr
                  is read transparently.
        code: Expected wire error code; must match BOTH ``adcp_error.code``
                and ``errors[0].code``. Two-layer invariant: both layers
                always agree.
        recovery: Required. Both ``adcp_error.recovery`` and
                ``errors[0].recovery`` must equal this hint. Pinning recovery
                is mandatory: it is the buyer-facing retry semantics
                (``correctable`` / ``transient`` / ``terminal``) and a silent
                drift between a typed exception's recovery and the wire is
                exactly the regression this helper exists to catch.
        message_substr: If provided, must appear in ``errors[0].message``.
                ``adcp_error.message`` is allowed to differ (it carries the
                envelope-level summary).
        check_mcp_tool_error: If ``True``, additionally assert that ``target``
                is an ``AdCPToolError`` instance before reading its envelope.
                MCP-boundary call sites use this to pin the exception type as
                well as the wire shape — a plain ``ToolError`` would still
                expose ``.envelope`` via duck-typing but would not be the
                typed MCP-boundary exception the test claims to inspect.
    """
    if check_mcp_tool_error:
        from src.core.tool_error_logging import AdCPToolError

        assert isinstance(target, AdCPToolError), f"expected AdCPToolError, got {type(target).__name__}"

    body = target.envelope if hasattr(target, "envelope") else target

    assert isinstance(body, dict), f"envelope target must resolve to dict, got {type(body).__name__}"
    assert "adcp_error" in body, f"missing envelope-level adcp_error: {body}"
    assert "errors" in body, f"missing payload-level errors[]: {body}"
    assert body["errors"], "errors[] must contain at least one entry"

    assert body["adcp_error"]["code"] == code, f"adcp_error.code={body['adcp_error']['code']!r}, expected {code!r}"
    assert body["errors"][0]["code"] == code, f"errors[0].code={body['errors'][0]['code']!r}, expected {code!r}"

    assert body["adcp_error"].get("recovery") == recovery, (
        f"adcp_error.recovery={body['adcp_error'].get('recovery')!r}, expected {recovery!r}"
    )
    assert body["errors"][0].get("recovery") == recovery, (
        f"errors[0].recovery={body['errors'][0].get('recovery')!r}, expected {recovery!r}"
    )

    if message_substr is not None:
        actual = body["errors"][0].get("message", "")
        assert message_substr in actual, f"errors[0].message={actual!r} does not contain {message_substr!r}"
