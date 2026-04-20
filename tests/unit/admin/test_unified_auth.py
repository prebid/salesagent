"""Unit tests for L0-10 admin UnifiedAuthMiddleware.

Canonical spec: ``flask-to-fastapi-foundation-modules.md`` §11.4 + §11.36.

Obligations (4 scenarios per L0-implementation-plan-v2 §L0-10):
  A. Authenticated session → ``request.state.principal`` populated with
     a ``Principal`` whose email and tenant_id match the session.
  B. Unauthenticated ``/admin/*`` + ``Accept: text/html`` → 302 redirect
     to ``/admin/login``.
  C. Unauthenticated ``/admin/*`` + ``Accept: application/json`` → 401
     JSON response (no redirect).
  D. Public paths bypass the auth gate without redirect/401.

The session is provided via ``scope["session"]`` (the contract
``SessionMiddleware`` sets) — the middleware reads user identity from
``session["user"]`` and tenant context from ``session["tenant_id"]``
per canonical §11.4 session-reading convention.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from src.admin.auth.principal import Principal
from src.admin.unified_auth import UnifiedAuthMiddleware


async def _noop_receive() -> dict:
    return {"type": "http.request", "body": b"", "more_body": False}


def _http_scope(
    path: str,
    session: dict[str, Any] | None = None,
    accept: str = "text/html",
) -> dict:
    return {
        "type": "http",
        "method": "GET",
        "path": path,
        "headers": [(b"accept", accept.encode("latin-1"))],
        "session": session or {},
        "state": {},
    }


async def _run(mw: UnifiedAuthMiddleware, scope: dict):
    """Run middleware, return (scope-state-seen-by-inner, response-messages)."""
    messages: list[dict] = []
    seen: dict = {}

    async def inner(s: dict, r, sd) -> None:
        seen["state"] = dict(s.get("state", {}))
        await sd({"type": "http.response.start", "status": 200, "headers": []})
        await sd({"type": "http.response.body", "body": b""})

    # Rebind inner to the mw instance for this call.
    mw.app = inner

    async def send(msg: dict) -> None:
        messages.append(msg)

    await mw(scope, _noop_receive, send)  # type: ignore[arg-type]
    return seen, messages


# --- Scenario A: authenticated session populates principal ---


@pytest.mark.asyncio
async def test_authenticated_session_populates_principal():
    """Valid session produces a ``Principal`` on ``request.state.principal``."""
    mw = UnifiedAuthMiddleware(AsyncMock())
    scope = _http_scope(
        "/admin/accounts",
        session={
            "user": {"email": "alice@example.com"},
            "tenant_id": "t_default",
            "role": "tenant_admin",
        },
    )
    seen, msgs = await _run(mw, scope)

    principal = seen["state"].get("principal")
    assert principal is not None, "authenticated session did not populate principal"
    assert isinstance(principal, Principal)
    assert principal.user_email == "alice@example.com"
    assert principal.tenant_id == "t_default"
    assert principal.role == "tenant_admin"

    # Handler ran normally (no 302/401 issued by middleware).
    status_messages = [m for m in msgs if m["type"] == "http.response.start"]
    assert status_messages[0]["status"] == 200


# --- Scenario B: unauth /admin/* + HTML -> 302 redirect to /admin/login ---


@pytest.mark.asyncio
async def test_unauth_admin_html_redirects_to_login():
    """Unauth HTML browser gets 302 with Location: /admin/login."""
    mw = UnifiedAuthMiddleware(AsyncMock())
    scope = _http_scope("/admin/accounts", session={}, accept="text/html")
    _, msgs = await _run(mw, scope)

    starts = [m for m in msgs if m["type"] == "http.response.start"]
    assert starts, "middleware emitted no response.start"
    assert starts[0]["status"] == 302, f"expected 302, got {starts[0]['status']}"
    location = dict(starts[0]["headers"]).get(b"location", b"").decode()
    assert location.startswith("/admin/login"), f"redirect Location: {location!r}"


# --- Scenario C: unauth /admin/* + JSON -> 401 JSON ---


@pytest.mark.asyncio
async def test_unauth_admin_json_returns_401():
    """Unauth JSON client gets 401, not a redirect."""
    mw = UnifiedAuthMiddleware(AsyncMock())
    scope = _http_scope("/admin/accounts", session={}, accept="application/json")
    _, msgs = await _run(mw, scope)

    starts = [m for m in msgs if m["type"] == "http.response.start"]
    assert starts, "middleware emitted no response.start"
    assert starts[0]["status"] == 401, f"expected 401, got {starts[0]['status']}"


# --- Scenario D: public paths bypass the gate ---


@pytest.mark.asyncio
async def test_public_paths_bypass_auth_gate():
    """``/admin/login``, ``/admin/auth/*``, ``/admin/public/*`` pass through."""
    mw = UnifiedAuthMiddleware(AsyncMock())
    for public_path in (
        "/admin/login",
        "/admin/auth/google/callback",
        "/admin/public/health",
    ):
        scope = _http_scope(public_path, session={}, accept="text/html")
        seen, msgs = await _run(mw, scope)

        # principal may be None (unauth), but no redirect / 401 is emitted —
        # the inner handler runs normally with 200.
        starts = [m for m in msgs if m["type"] == "http.response.start"]
        assert starts, f"public path {public_path} produced no response"
        assert starts[0]["status"] == 200, f"public path {public_path} was blocked (status {starts[0]['status']})"
