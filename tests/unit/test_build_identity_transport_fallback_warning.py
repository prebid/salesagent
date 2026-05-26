"""Regression test: ``_build_identity`` warns once when it falls back to
``protocol='mcp'`` inside an authenticated request scope.

Issue #221 — defense-in-depth against the next #64-style silent-drop.
``adcp.server.current_transport ContextVar`` populates ``current_transport`` based on
the inbound URL path. If a future middleware-chain reordering (or a
new code path that bypasses the middleware) leaves the ContextVar
unset while ``current_principal`` IS set (i.e. the request is real
and authenticated), every A2A buyer silently regresses to MCP-shaped
webhooks. We log a single WARNING the first time this happens per
process so operators have a clear signal before buyers complain.

The legitimate fallback paths (lifespan events, unit tests, admin
requests that don't go through bearer auth) don't populate
``current_principal``, so they don't trigger the warning.
"""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import MagicMock

import pytest


def _reset_warn_flag() -> None:
    """Reset the module-level dedupe flag so each test sees a fresh process."""
    from core.platforms import _delegate

    _delegate._TRANSPORT_FALLBACK_WARNED = False


@pytest.fixture(autouse=True)
def _reset_state() -> None:
    """Each test starts with the warn-once flag cleared."""
    _reset_warn_flag()
    yield
    _reset_warn_flag()


def _make_ctx(tenant_id: str = "t1", auth_principal: str | None = "fallback-principal") -> Any:
    """Minimal RequestContext stand-in: only ``ctx.account.metadata`` and
    ``ctx.auth_principal`` are read by ``_build_identity``."""
    ctx = MagicMock()
    ctx.account.metadata = {"tenant_id": tenant_id}
    ctx.auth_principal = auth_principal
    return ctx


def _patch_supporting_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub out the things ``_build_identity`` calls into beyond the
    contextvars under test."""
    from core.platforms import _delegate

    monkeypatch.setattr(_delegate, "get_tenant_by_id", lambda tid: {"tenant_id": tid})


class TestBuildIdentityTransportFallback:
    def test_warns_once_when_principal_set_but_transport_unset(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Misconfig signal: real authenticated request reached
        ``_build_identity`` without transport detection running."""
        from adcp.server import current_transport
        from adcp.server.auth import current_principal

        from core.platforms._delegate import _build_identity

        _patch_supporting_calls(monkeypatch)

        principal_token = current_principal.set("principal-1")
        transport_token = current_transport.set(None)
        try:
            with caplog.at_level(logging.WARNING, logger="core.platforms._delegate"):
                identity = _build_identity(_make_ctx())
        finally:
            current_principal.reset(principal_token)
            current_transport.reset(transport_token)

        assert identity.protocol == "mcp"
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1, f"Expected exactly one WARNING, got {len(warnings)}: {[r.message for r in warnings]}"
        assert "adcp.server.current_transport ContextVar" in warnings[0].message
        assert "#221" in warnings[0].message

    def test_warning_dedupes_across_repeated_misconfig(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Warn-once-per-process: repeated misconfigured calls don't spam logs."""
        from adcp.server import current_transport
        from adcp.server.auth import current_principal

        from core.platforms._delegate import _build_identity

        _patch_supporting_calls(monkeypatch)

        principal_token = current_principal.set("principal-1")
        transport_token = current_transport.set(None)
        try:
            with caplog.at_level(logging.WARNING, logger="core.platforms._delegate"):
                for _ in range(5):
                    _build_identity(_make_ctx())
        finally:
            current_principal.reset(principal_token)
            current_transport.reset(transport_token)

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1, "Expected warn-once-per-process; got log spam"

    def test_no_warning_when_transport_is_set(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Happy path: transport detected → no warning, protocol=actual."""
        from adcp.server import current_transport
        from adcp.server.auth import current_principal

        from core.platforms._delegate import _build_identity

        _patch_supporting_calls(monkeypatch)

        principal_token = current_principal.set("principal-1")
        transport_token = current_transport.set("a2a")
        try:
            with caplog.at_level(logging.WARNING, logger="core.platforms._delegate"):
                identity = _build_identity(_make_ctx())
        finally:
            current_principal.reset(principal_token)
            current_transport.reset(transport_token)

        assert identity.protocol == "a2a"
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert warnings == [], "No misconfig — must not warn"

    def test_request_context_transport_takes_precedence(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A2A decisioning RequestContext carries transport directly; use it
        even if the older server ContextVar is absent."""
        from adcp.server import current_transport
        from adcp.server.auth import current_principal

        from core.platforms._delegate import _build_identity

        _patch_supporting_calls(monkeypatch)

        ctx = _make_ctx()
        ctx.transport = "a2a"
        principal_token = current_principal.set("principal-1")
        transport_token = current_transport.set(None)
        try:
            with caplog.at_level(logging.WARNING, logger="core.platforms._delegate"):
                identity = _build_identity(ctx)
        finally:
            current_principal.reset(principal_token)
            current_transport.reset(transport_token)

        assert identity.protocol == "a2a"
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert warnings == []

    def test_no_warning_when_no_principal(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Lifespan / unit-test / admin paths don't set ``current_principal`` —
        the fallback to ``mcp`` is legitimate, no warning should fire."""
        from adcp.server import current_transport
        from adcp.server.auth import current_principal

        from core.platforms._delegate import _build_identity

        _patch_supporting_calls(monkeypatch)

        # Both contextvars unset (or principal explicitly None)
        principal_token = current_principal.set(None)
        transport_token = current_transport.set(None)
        try:
            with caplog.at_level(logging.WARNING, logger="core.platforms._delegate"):
                identity = _build_identity(_make_ctx())
        finally:
            current_principal.reset(principal_token)
            current_transport.reset(transport_token)

        assert identity.protocol == "mcp"
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert warnings == [], "Lifespan/unit-test path — must not warn on legitimate fallback"
