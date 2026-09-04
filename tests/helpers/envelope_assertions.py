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

The helper catches TWO kinds of drift, not one:

- exception <-> wire: the two envelope layers must agree with each other and
  with the caller's expectation, so a typed exception whose recovery stops
  reaching the wire reddens.
- wire <-> spec: the ``recovery`` the caller pins must be the one the pinned
  ``error-code.json`` ``enumMetadata`` classifies that code as. Checking only
  the first left the helper blind to the second, and a *shipped, green* test
  graded ``SERVICE_UNAVAILABLE`` + ``terminal`` — a pair the normative pin
  contradicts (``SERVICE_UNAVAILABLE`` is ``transient``). Deriving the
  expectation from the pin makes that contradiction unwritable in any future
  test rather than merely absent from today's ones.
"""

from __future__ import annotations

from typing import Any

from tests.helpers import pinned_schema


def assert_no_raw_validation_leak(message: str) -> None:
    """Assert a buyer-facing validation message omits raw Pydantic internals."""
    assert "input_value" not in message, f"raw Pydantic input leaked into validation message: {message!r}"
    assert "errors.pydantic.dev" not in message, f"Pydantic documentation URL leaked into message: {message!r}"


def assert_envelope_shape(
    target: Any,
    code: str,
    *,
    recovery: str,
    message_substr: str | None = None,
    field: str | None = None,
    suggestion: str | None = None,
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
                exactly the regression this helper exists to catch. It must
                ALSO agree with the pinned ``enumMetadata`` classification of
                *code* whenever the pin defines one: the caller's literal pins
                intent, but an intent the spec contradicts is not gradeable.
                Codes the pin does not classify (e.g. ``NOT_SUPPORTED``) keep
                the caller's literal as the only expectation.
        message_substr: If provided, must appear in ``errors[0].message``.
                ``adcp_error.message`` is allowed to differ (it carries the
                envelope-level summary).
        field: If provided, both ``adcp_error.field`` and ``errors[0].field``
                must equal this JSONPath-lite path into the buyer's request
                payload (``core/error.json`` @3.1.1 — e.g.
                ``property_list.agent_url``). Checked on BOTH layers for the
                same reason ``recovery`` is: the pinned storyboards read both
                in the wild — ``proposal_finalize.yaml:207/352/397`` grade
                ``adcp_error.field`` while the other scenarios grade
                ``errors[0].field`` — and ``error-handling.mdx:88`` calls
                populating only one layer "the source-of-truth for most
                interop bugs". ``None`` (the default) does not assert absence:
                ``field`` is optional in the schema, so most envelopes legally
                carry none. A call site that needs "no ``field`` key at all"
                asserts that itself.
        suggestion: If provided, must equal the buyer-facing ``suggestion`` on
                ``errors[0]`` (falling back to ``adcp_error.suggestion``). Pass
                the pinned ``enums/error-code.json`` enumMetadata text — never a
                Python ClassVar — so nulling the envelope seam or swapping the
                ClassVar reddens (#1812 B4).
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

    pinned_recovery = pinned_schema.recovery_by_code().get(code)
    assert pinned_recovery is None or pinned_recovery == recovery, (
        f"this call grades ({code!r}, {recovery!r}), but the pinned error-code.json "
        f"enumMetadata says {code!r} is {pinned_recovery!r} — a test may not grade a pair "
        f"the spec contradicts. The enumMetadata recovery is normative, so either the raise "
        f"site is wrong (pick the exception class whose pinned recovery IS the intent) or "
        f"the pin moved (advance it); do not relax this helper."
    )

    if field is not None:
        assert body["adcp_error"].get("field") == field, (
            f"adcp_error.field={body['adcp_error'].get('field')!r}, expected {field!r}"
        )
        assert body["errors"][0].get("field") == field, (
            f"errors[0].field={body['errors'][0].get('field')!r}, expected {field!r}"
        )

    if message_substr is not None:
        actual = body["errors"][0].get("message", "")
        assert message_substr in actual, f"errors[0].message={actual!r} does not contain {message_substr!r}"

    if suggestion is not None:
        actual_suggestion = body["errors"][0].get("suggestion") or body["adcp_error"].get("suggestion")
        assert actual_suggestion == suggestion, (
            f"wire suggestion={actual_suggestion!r}, expected pinned {suggestion!r} for {code}"
        )
