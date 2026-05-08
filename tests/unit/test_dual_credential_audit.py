"""Unit tests for ``DualCredentialAuditMiddleware``.

Restores the audit signal the deleted ``BearerToAdcpAuthMiddleware`` shim
used to emit when a request carried two different bearer credentials —
one in ``Authorization: Bearer`` and one in ``x-adcp-auth``. adcp 4.5.0's
per-leg auth middleware reads only its configured header per leg, so
without this audit middleware the divergence signal disappears.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest

from core.middleware.dual_credential_audit import DualCredentialAuditMiddleware


class _CapturingApp:
    """Minimal ASGI sink — records that it was invoked."""

    def __init__(self) -> None:
        self.called = False

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        self.called = True


def _http_scope(headers: list[tuple[bytes, bytes]], path: str = "/") -> dict[str, Any]:
    return {
        "type": "http",
        "method": "POST",
        "path": path,
        "headers": headers,
    }


async def _drive(scope: dict[str, Any], app: Any) -> None:
    async def _receive() -> dict[str, Any]:
        return {"type": "http.disconnect"}

    async def _send(_message: dict[str, Any]) -> None:
        pass

    await app(scope, _receive, _send)


@pytest.mark.asyncio
async def test_warns_on_different_tokens_in_both_headers(caplog):
    """Two different bearer tokens in ``Authorization`` and ``x-adcp-auth``
    → WARNING logged with fingerprints (never the raw tokens)."""
    inner = _CapturingApp()
    middleware = DualCredentialAuditMiddleware(inner)

    headers = [
        (b"x-adcp-auth", b"adcp-token-aaa"),
        (b"authorization", b"Bearer rfc6750-token-bbb"),
    ]
    with caplog.at_level(logging.WARNING, logger="core.middleware.dual_credential_audit"):
        await _drive(_http_scope(headers), middleware)

    assert inner.called, "Middleware must always pass through to inner app"
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings, "Expected a dual-credential WARNING; got none"
    msg = warnings[0].getMessage()
    # Token values must NEVER appear in the log line.
    assert "adcp-token-aaa" not in msg, f"Raw x-adcp-auth token leaked into log: {msg!r}"
    assert "rfc6750-token-bbb" not in msg, f"Raw Bearer token leaked into log: {msg!r}"
    # Fingerprints (8 hex chars per credential) must appear so SOC can correlate.
    assert "x_adcp_fp=" in msg and "bearer_fp=" in msg, (
        f"Expected fingerprint markers in log; got {msg!r}"
    )


@pytest.mark.asyncio
async def test_no_warning_when_tokens_match(caplog):
    """Same token in both headers → no warning (the common cutover case
    where a buyer hasn't yet stopped emitting the legacy header). The
    SDK auth chain still picks one and authenticates normally; we don't
    spam the audit log for benign duplication."""
    inner = _CapturingApp()
    middleware = DualCredentialAuditMiddleware(inner)

    same = b"identical-token"
    headers = [
        (b"x-adcp-auth", same),
        (b"authorization", b"Bearer " + same),
    ]
    with caplog.at_level(logging.WARNING, logger="core.middleware.dual_credential_audit"):
        await _drive(_http_scope(headers), middleware)

    assert inner.called
    assert not [r for r in caplog.records if r.levelno == logging.WARNING], (
        "Expected no warning when both headers carry the same token"
    )


@pytest.mark.asyncio
async def test_no_warning_when_only_one_header_present(caplog):
    """Single credential header → no warning. This is the normal case
    for either MCP traffic (``x-adcp-auth``) or A2A traffic
    (``Authorization: Bearer``)."""
    inner = _CapturingApp()
    middleware = DualCredentialAuditMiddleware(inner)

    with caplog.at_level(logging.WARNING, logger="core.middleware.dual_credential_audit"):
        await _drive(_http_scope([(b"x-adcp-auth", b"only-mcp-token")]), middleware)
        await _drive(_http_scope([(b"authorization", b"Bearer only-bearer-token")]), middleware)
        await _drive(_http_scope([]), middleware)  # no auth at all

    assert not [r for r in caplog.records if r.levelno == logging.WARNING]


@pytest.mark.asyncio
async def test_non_bearer_authorization_ignored(caplog):
    """``Authorization: Basic ...`` (non-bearer scheme) does not count as
    a bearer credential — only Bearer-prefixed Authorization values can
    diverge from ``x-adcp-auth``."""
    inner = _CapturingApp()
    middleware = DualCredentialAuditMiddleware(inner)

    headers = [
        (b"x-adcp-auth", b"adcp-token"),
        (b"authorization", b"Basic dXNlcjpwYXNz"),
    ]
    with caplog.at_level(logging.WARNING, logger="core.middleware.dual_credential_audit"):
        await _drive(_http_scope(headers), middleware)

    assert inner.called
    assert not [r for r in caplog.records if r.levelno == logging.WARNING]


@pytest.mark.asyncio
async def test_lifespan_passes_through_without_inspection(caplog):
    """Non-HTTP scopes (lifespan, websocket) must pass through without
    header inspection — auth doesn't apply, and the headers field is
    typically absent on lifespan."""
    inner = _CapturingApp()
    middleware = DualCredentialAuditMiddleware(inner)

    lifespan_scope: dict[str, Any] = {"type": "lifespan"}
    with caplog.at_level(logging.WARNING, logger="core.middleware.dual_credential_audit"):
        await _drive(lifespan_scope, middleware)

    assert inner.called
    assert not [r for r in caplog.records if r.levelno == logging.WARNING]
