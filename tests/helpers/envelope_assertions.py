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

from typing import Any


def assert_no_raw_validation_leak(message: str) -> None:
    """Assert a buyer-facing validation message omits raw Pydantic internals."""
    assert "input_value" not in message, f"raw Pydantic input leaked into validation message: {message!r}"
    assert "errors.pydantic.dev" not in message, f"Pydantic documentation URL leaked into message: {message!r}"


def assert_no_tenant_disclosure(target: Any, tenant_id: str) -> None:
    """Assert an auth rejection discloses *tenant_id* nowhere the buyer can see it.

    The tenant is resolved from request headers BEFORE the token is validated, so
    echoing it back to a caller whose token was rejected hands an unauthenticated
    party an internal identifier (the tenant UUID in a host-routed deploy). This
    is the single assertion for that contract, so the sites grading it cannot
    drift apart: the unit test (``test_resolved_identity``), both isolation
    integration tests, and the A2A + MCP BDD no-disclosure scenarios all route
    through here.

    Args:
        target: What the buyer receives. Accepts:
            - a two-layer envelope ``dict`` (the wire body);
            - an ``AdCPError`` exception, whose two-layer envelope is built here
              so the message AND every envelope field are graded;
            - any other ``Exception`` (e.g. a raw fastmcp ``ToolError`` from an
              MCP auth rejection, which has no envelope) — graded on its message
              alone, the only buyer-facing surface it carries.
        tenant_id: The internal tenant identifier that must not appear. Use a
            UUID: a slug can collide with unrelated envelope text and make the
            check pass or fail for the wrong reason.

    Checks BOTH the rendered message and the FULL serialized envelope — a
    message-only check passes while the id sits in ``details``/``context``.
    """
    import json

    if isinstance(target, BaseException):
        rendered = str(target)
        if hasattr(target, "wire_error_code"):
            from src.core.exceptions import build_two_layer_error_envelope

            envelope = build_two_layer_error_envelope(target)
        else:
            # A non-AdCP exception — a raw fastmcp ``ToolError`` — has no two-layer
            # envelope to build: MCP raises the rejection outside the tool boundary
            # that would wrap it, so the message is the only buyer-facing surface.
            # Grade the message alone; the MCP no-disclosure scenario documents this
            # as the weaker, message-only half of the same contract.
            envelope = {"errors": [{"message": rendered}]}
    else:
        envelope = target.envelope if hasattr(target, "envelope") else target
        assert isinstance(envelope, dict), f"envelope target must resolve to dict, got {type(envelope).__name__}"
        rendered = str((envelope.get("errors") or [{}])[0].get("message", ""))

    assert tenant_id not in rendered, f"auth error message disclosed the tenant id {tenant_id!r}: {rendered!r}"

    serialized = json.dumps(envelope, default=str)
    if tenant_id in serialized:
        leaking = [key for key, value in _flatten_envelope(envelope) if tenant_id in json.dumps(value, default=str)]
        raise AssertionError(
            f"tenant id {tenant_id!r} leaked into the error envelope (field(s): {leaking or ['<unknown>']}): {envelope}"
        )


def _flatten_envelope(envelope: dict, prefix: str = "") -> list[tuple[str, Any]]:
    """Yield (dotted-path, value) leaf pairs so a leak names its own field."""
    pairs: list[tuple[str, Any]] = []
    for key, value in envelope.items():
        path = f"{prefix}{key}"
        if isinstance(value, dict):
            pairs.extend(_flatten_envelope(value, prefix=f"{path}."))
        else:
            pairs.append((path, value))
    return pairs


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
