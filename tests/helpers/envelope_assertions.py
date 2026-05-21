"""Unified AdCP two-layer error envelope assertion.

Single helper for the wire shape every transport boundary must emit::

    {
        "adcp_error": {"code": "...", "message": "...", "recovery": "..."},
        "errors":     [{"code": "...", "message": "...", "recovery": "..."}],
        "context":    {...},   # optional
        # A2A only:
        "error_code": "...",   # backward-compat top-level mirror
        "recovery":   "...",   # backward-compat top-level mirror
    }

Replaces the per-boundary helpers (``_assert_two_layer_envelope``,
``_assert_mcp_envelope``, ``_assert_a2a_envelope``, ``_assert_rest_envelope``)
that all verified the same shape with diverging signatures. A spec change to
the envelope now requires updating exactly one helper.
"""

from __future__ import annotations

from typing import Any


def assert_envelope_shape(
    target: Any,
    code: str,
    *,
    recovery: str | None = None,
    message_substr: str | None = None,
    check_backward_compat: bool = False,
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
        recovery: If provided, both ``adcp_error.recovery`` and
                ``errors[0].recovery`` must equal this hint.
        message_substr: If provided, must appear in ``errors[0].message``.
                ``adcp_error.message`` is allowed to differ (it carries the
                envelope-level summary).
        check_backward_compat: If ``True``, additionally assert the top-level
                backward-compat keys ``error_code`` and ``recovery`` that A2A
                surfaces alongside the spec envelope. Off by default because
                REST + MCP envelopes don't carry them.
    """
    body = target.envelope if hasattr(target, "envelope") else target

    assert isinstance(body, dict), f"envelope target must resolve to dict, got {type(body).__name__}"
    assert "adcp_error" in body, f"missing envelope-level adcp_error: {body}"
    assert "errors" in body, f"missing payload-level errors[]: {body}"
    assert body["errors"], "errors[] must contain at least one entry"

    assert body["adcp_error"]["code"] == code, f"adcp_error.code={body['adcp_error']['code']!r}, expected {code!r}"
    assert body["errors"][0]["code"] == code, f"errors[0].code={body['errors'][0]['code']!r}, expected {code!r}"

    if recovery is not None:
        assert body["adcp_error"].get("recovery") == recovery, (
            f"adcp_error.recovery={body['adcp_error'].get('recovery')!r}, expected {recovery!r}"
        )
        assert body["errors"][0].get("recovery") == recovery, (
            f"errors[0].recovery={body['errors'][0].get('recovery')!r}, expected {recovery!r}"
        )

    if message_substr is not None:
        actual = body["errors"][0].get("message", "")
        assert message_substr in actual, f"errors[0].message={actual!r} does not contain {message_substr!r}"

    if check_backward_compat:
        assert body.get("error_code") == code, f"top-level error_code={body.get('error_code')!r}, expected {code!r}"
        if recovery is not None:
            assert body.get("recovery") == recovery, (
                f"top-level recovery={body.get('recovery')!r}, expected {recovery!r}"
            )
