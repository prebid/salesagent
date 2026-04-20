"""Admin-UI UnifiedAuthMiddleware — session-cookie-backed auth gate.

Canonical spec: ``flask-to-fastapi-foundation-modules.md`` §11.4 +
§11.36 (Middleware Stack Versioning).

Resolves an admin ``Principal`` from the session cookie (populated
upstream by Starlette's ``SessionMiddleware``) and stashes it on
``request.state.principal``. For unauthenticated requests that target
``/admin/*`` or ``/tenant/*``:

  - HTML/browser clients get a 302 redirect to ``/admin/login``.
  - JSON/API clients get a 401 response.

Public paths — ``/admin/login``, ``/admin/logout``, ``/admin/auth/*``,
``/admin/public/*``, ``/admin/static/*`` — bypass the gate. OAuth
callback URIs MUST remain public to complete the login flow (per
Critical Invariant #6 byte-immutability).

``Principal`` is a **detached POJO** (frozen dataclass, no ORM) per
canonical §11.3.1 — the middleware constructs it from session data and
any DB lookup is expected to run inside a short-lived session that is
closed before the POJO is stashed. This prevents
``DetachedInstanceError`` in downstream middleware (e.g.,
``LegacyAdminRedirectMiddleware`` at L1c) that reads
``principal.tenant_id`` after the DB session has closed.

Distinct from ``src/core/auth_middleware.UnifiedAuthMiddleware`` which
handles token-based MCP/A2A/REST auth. Admin-UI auth is
session-cookie-based; the two stacks are layered independently.
"""

from __future__ import annotations

from typing import Any

from src.admin.auth.principal import Principal, Role

# Paths that MUST bypass the auth gate — OAuth callbacks (Invariant #6
# byte-immutable), login/logout entry points, and static assets. The
# canonical static mount is ``/admin/static`` (``StaticFiles(name="static")``
# per Invariant #1).
_PUBLIC_PREFIXES: tuple[str, ...] = (
    "/admin/login",
    "/admin/logout",
    "/admin/auth/",
    "/admin/public/",
    "/admin/static/",
)

# Paths that trigger the auth gate. ``/tenant/{tenant_id}/*`` is the
# canonical admin mount post-D1 2026-04-16 (CLAUDE.md #Critical
# Invariants); ``/admin/*`` is the legacy/operator form handled by
# ``LegacyAdminRedirectMiddleware`` at L1c.
_GATED_PREFIXES: tuple[str, ...] = ("/admin/", "/tenant/")


def _is_public(path: str) -> bool:
    """Return True when *path* must bypass the auth gate."""
    for prefix in _PUBLIC_PREFIXES:
        if path == prefix.rstrip("/") or path.startswith(prefix):
            return True
    return False


def _is_gated(path: str) -> bool:
    """Return True when *path* requires authentication."""
    return any(path.startswith(prefix) for prefix in _GATED_PREFIXES)


def _wants_json(headers: list[tuple[bytes, bytes]]) -> bool:
    """Heuristic: does the client prefer JSON over HTML?

    Looks at the ``Accept`` header. ``application/json`` (exact or
    subtype-specific) → JSON. Anything else — including ``text/html`` or
    ``*/*`` — is treated as browser/HTML. Matches the
    ``_response_mode()`` helper introduced at L0-14 (§11.10).
    """
    accept = b""
    for name, value in headers:
        if name == b"accept":
            accept = value
            break
    if not accept:
        return False
    lower = accept.decode("latin-1", errors="replace").lower()
    # An explicit JSON preference beats wildcard fallback.
    if "application/json" in lower and "text/html" not in lower:
        return True
    return False


def _session_role(raw: Any) -> Role:
    """Coerce a session-stored role string into the ``Role`` literal.

    Unknown values fall back to ``tenant_user`` — the least-privileged
    authenticated role. The detailed role-resolution flow (super-admin
    env-list, DB fallback) lands in ``src/admin/deps/auth.py`` at L0-12;
    the middleware itself only mirrors what the session already carries.
    """
    if isinstance(raw, str):
        if raw == "super_admin":
            return "super_admin"
        if raw == "tenant_admin":
            return "tenant_admin"
        if raw == "test":
            return "test"
        if raw == "tenant_user":
            return "tenant_user"
    return "tenant_user"


def _principal_from_session(session: dict[str, Any]) -> Principal | None:
    """Construct a detached ``Principal`` from session dict, or None.

    Tolerates both string and dict ``user`` values (legacy Flask code
    stored either — OAuth claims produced a dict, while test-mode code
    sometimes stored a bare email string).
    """
    user_raw = session.get("user")
    if isinstance(user_raw, dict):
        email = str(user_raw.get("email") or "").strip().lower()
    elif isinstance(user_raw, str):
        email = user_raw.strip().lower()
    else:
        email = ""

    if not email:
        return None

    tenant_id = session.get("tenant_id")
    if not isinstance(tenant_id, str) or not tenant_id:
        return None

    role = _session_role(session.get("role"))

    raw_available = session.get("available_tenants") or ()
    if isinstance(raw_available, (list, tuple, set, frozenset)):
        available = frozenset(str(t) for t in raw_available if t)
    else:
        available = frozenset()
    if tenant_id not in available:
        available = available | {tenant_id}

    is_test_user = bool(session.get("test_user", False))

    return Principal(
        user_email=email,
        role=role,
        tenant_id=tenant_id,
        available_tenants=available,
        is_test_user=is_test_user,
    )


async def _emit_redirect(send: Any, location: str) -> None:
    """Send a 302 redirect to *location* with a minimal HTML body."""
    await send(
        {
            "type": "http.response.start",
            "status": 302,
            "headers": [
                (b"location", location.encode("latin-1")),
                (b"content-type", b"text/html; charset=utf-8"),
                (b"content-length", b"0"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": b"", "more_body": False})


async def _emit_401_json(send: Any) -> None:
    """Send a 401 JSON response."""
    body = b'{"error":"unauthorized","message":"Authentication required"}'
    await send(
        {
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("latin-1")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body, "more_body": False})


class UnifiedAuthMiddleware:
    """Gate admin routes on a session-cookie-backed ``Principal``."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        session = scope.get("session") or {}
        principal = _principal_from_session(session) if isinstance(session, dict) else None

        scope.setdefault("state", {})
        scope["state"]["principal"] = principal

        # Public paths always pass through; the admin handler may still
        # render an unauth view (e.g., the login form).
        if _is_public(path):
            await self.app(scope, receive, send)
            return

        # Gated paths require a principal.
        if _is_gated(path) and principal is None:
            headers = scope.get("headers", []) or []
            if _wants_json(headers):
                await _emit_401_json(send)
            else:
                await _emit_redirect(send, "/admin/login")
            return

        # Non-gated paths (e.g., ``/api/v1/...``, ``/mcp/...``) fall
        # through — those surfaces use token-based auth handled by the
        # separate ``src/core/auth_middleware.UnifiedAuthMiddleware``.
        await self.app(scope, receive, send)
