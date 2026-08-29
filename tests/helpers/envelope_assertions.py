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


def _assert_no_tokens(message: str, tokens: tuple[str, ...], label: str) -> None:
    """Assert none of ``tokens`` survives into a buyer-facing ``message``.

    Shared body for both leak oracles below: each owns a token tuple, the loop
    and the failure text live here once, and adding a marker to either set is a
    one-line edit beside that set.
    """
    for token in tokens:
        assert token not in message, f"{label} leaked into buyer-facing message: {token!r} in {message!r}"


# Raw Pydantic internals that must never survive validation-error sanitization
# onto the buyer-facing wire: the echoed input value and the docs URL Pydantic
# appends to its own error text.
_RAW_VALIDATION_LEAK_TOKENS: tuple[str, ...] = (
    "input_value",
    "errors.pydantic.dev",
)


def assert_no_raw_validation_leak(message: str) -> None:
    """Assert a buyer-facing validation message omits raw Pydantic internals."""
    _assert_no_tokens(message, _RAW_VALIDATION_LEAK_TOKENS, "raw Pydantic detail")


# Sensitive markers a raw untyped exception can carry — a SQL keyword, a table
# name, and a filesystem path — that must never survive sanitization onto the
# buyer-facing wire. RAW_EXCEPTION_LEAK_SENTINEL embeds all of them so every leak
# test injects the same raw-exception input, and assert_no_raw_exception_leak
# checks the same token set. Adding a new marker happens once, here.
#
# These three literals are a SAMPLE of what an untyped exception can disclose,
# not a completeness oracle: a sink that leaks some other internal detail passes
# this check. They exist to make the known disclosure shapes non-silent.
_RAW_EXCEPTION_LEAK_TOKENS: tuple[str, ...] = (
    "SELECT",
    "secret_table",
    "/var/secrets/db.key",
)

RAW_EXCEPTION_LEAK_SENTINEL = "SELECT token FROM secret_table -- /var/secrets/db.key"

# The oracle is only as strong as its token tuple, and nothing downstream would
# notice it being hollowed out: an empty tuple turns assert_no_raw_exception_leak
# into a no-op at every call site, and a token that is absent from the sentinel
# is never actually injected by the tests that call it, so it grades nothing.
# Pin both at import so neutering the tuple fails loudly instead of going green.
assert _RAW_EXCEPTION_LEAK_TOKENS, "_RAW_EXCEPTION_LEAK_TOKENS is empty — assert_no_raw_exception_leak grades nothing"
assert all(token in RAW_EXCEPTION_LEAK_SENTINEL for token in _RAW_EXCEPTION_LEAK_TOKENS), (
    "every _RAW_EXCEPTION_LEAK_TOKENS entry must appear in RAW_EXCEPTION_LEAK_SENTINEL, "
    "otherwise the tests injecting the sentinel never exercise it: "
    f"{[t for t in _RAW_EXCEPTION_LEAK_TOKENS if t not in RAW_EXCEPTION_LEAK_SENTINEL]!r}"
)


def assert_no_raw_exception_leak(message: str) -> None:
    """Assert a buyer-facing message omits raw untyped-exception internals.

    Mirrors ``assert_no_raw_validation_leak`` — both delegate to
    ``_assert_no_tokens`` — so every sink (the untyped-normalization message, the
    A2A ``_internal_error_for`` message, the A2A push-config endpoint) grades the
    same markers and no site can silently drop one. Pair with
    ``RAW_EXCEPTION_LEAK_SENTINEL`` as the injected raw exception text.
    """
    _assert_no_tokens(message, _RAW_EXCEPTION_LEAK_TOKENS, "raw exception detail")


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
