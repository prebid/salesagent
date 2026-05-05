"""ASGI middleware: dispatch admin paths to a WSGI Flask app.

Mounts the existing :func:`src.admin.app.create_app` Flask application
on the same Starlette binary that ``serve(transport="both")`` produces.
No nginx in the loop.

Routing decision (in order):

* ``Host`` (or ``Apx-Incoming-Host``) matches ``ADMIN_DOMAIN`` /
  ``admin.${SALES_AGENT_DOMAIN}`` → entire request goes to Flask with
  ``root_path=/admin`` (replaces the nginx ``server_name admin.*``
  block that injected ``X-Forwarded-Prefix: /admin``)
* ``/mcp/*`` → MCP (claimed by ``serve(transport="both")``'s inner
  dispatcher before this middleware decides anything)
* ``/admin/*``, ``/static/*``, ``/auth/*``, ``/login``, ``/logout``,
  ``/tenant/*``, ``/api/*``, ``/test/*``, ``/health``, ``/metrics``,
  ``/debug/*``, ``/create_tenant``, ``/signup`` → Flask via
  :class:`a2wsgi.WSGIMiddleware`
* everything else (including ``/`` and ``/.well-known/agent-card.json``)
  → A2A (the inner ``serve()`` app)

Google OAuth callback at ``/auth/google/callback`` is included in the
Flask carve-out so the existing Google Cloud Console redirect URI keeps
working unchanged.
"""

from __future__ import annotations

from typing import Any

from src.core.domain_config import is_admin_domain

# Path prefixes the Flask admin claims. Anything under one of these
# segments dispatches to Flask; everything else falls through to A2A
# (which serves /.well-known/agent-card.json + the A2A RPC endpoint at
# root). Order doesn't matter — first matching prefix wins.
DEFAULT_FLASK_PREFIXES: tuple[str, ...] = (
    "/admin",
    "/static",
    "/auth",
    "/login",
    "/logout",
    "/tenant",
    "/api",
    "/test",
    "/health",
    "/metrics",
    "/debug",
    "/create_tenant",
    "/signup",
)

# Prefixes whose path segment is *stripped* before the request is handed
# to the WSGI app — mirrors :func:`Starlette.routing.Mount` semantics on
# the legacy ``app.mount("/admin", admin_wsgi)`` path. Without stripping
# Flask sees ``/admin/login`` and 404s because the auth blueprint
# registers ``/login`` at the root, not ``/admin/login``.
#
# Prefixes NOT in this set pass through unchanged (e.g. ``/login``,
# ``/auth/google``, ``/static/...`` — Flask blueprints already register
# them at those paths).
DEFAULT_STRIP_PREFIXES: frozenset[str] = frozenset({"/admin"})


class AdminWSGIMount:
    """Path-prefix ASGI dispatcher to a single WSGI app.

    Tested-prefixes are matched as ``path == prefix`` or
    ``path.startswith(prefix + "/")`` to avoid bleeding into siblings
    (``/admin`` matches ``/admin`` and ``/admin/foo`` but never
    ``/administrators``).

    Strips any prefix in ``strip_prefixes`` from the request path before
    dispatching, so ``/admin/login`` reaches Flask as ``/login`` (matches
    the auth blueprint's root route). The legacy stack achieved this via
    Starlette's :class:`Mount`; we replicate it here to keep URL behavior
    stable through the cutover.

    Lifespan and websocket scopes always pass through to the inner app
    — the WSGI bridge has no lifespan, and the inner Starlette owns
    the framework's startup hooks (FastMCP session manager, a2a-sdk
    stores).
    """

    def __init__(
        self,
        app: Any,
        *,
        wsgi_app: Any,
        prefixes: tuple[str, ...] = DEFAULT_FLASK_PREFIXES,
        strip_prefixes: frozenset[str] = DEFAULT_STRIP_PREFIXES,
    ) -> None:
        self.app = app
        self.wsgi_app = wsgi_app
        self.prefixes = tuple(prefixes)
        self.strip_prefixes = strip_prefixes

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            # Host-based admin dispatch first: admin.<domain>/* serves
            # the entire admin app at root with SCRIPT_NAME=/admin so
            # url_for() still emits /admin/... URLs. Replaces the nginx
            # ``server_name admin.*`` block that proxied to upstream
            # with ``X-Forwarded-Prefix: /admin``.
            if self._is_admin_host(scope):
                new_scope = dict(scope)
                new_scope["root_path"] = scope.get("root_path", "") + "/admin"
                await self.wsgi_app(new_scope, receive, send)
                return

            path = scope.get("path", "")
            for prefix in self.prefixes:
                if path == prefix or path.startswith(prefix + "/"):
                    if prefix in self.strip_prefixes:
                        # Mirror Starlette Mount: strip the matched prefix
                        # so the WSGI app sees the unprefixed path. Set
                        # root_path so url_for() / request.script_root
                        # generate links with the prefix re-attached.
                        new_scope = dict(scope)
                        stripped = path[len(prefix) :] or "/"
                        new_scope["path"] = stripped
                        # raw_path is bytes; preserve the same stripping.
                        # Some ASGI servers omit raw_path — fall back to
                        # encoded path if so.
                        raw = scope.get("raw_path") or path.encode()
                        # Match the path strip on the raw bytes: strip the
                        # prefix's byte-length, fall back to "/" if empty.
                        # Use the encoded prefix length, which equals the
                        # str length for ASCII prefixes.
                        new_scope["raw_path"] = raw[len(prefix) :] or b"/"
                        new_scope["root_path"] = scope.get("root_path", "") + prefix
                        await self.wsgi_app(new_scope, receive, send)
                        return
                    await self.wsgi_app(scope, receive, send)
                    return
        await self.app(scope, receive, send)

    @staticmethod
    def _resolve_host(scope: dict) -> str | None:
        """Pick the externally-visible host from ASGI scope headers.

        Matches :func:`src.core.domain_routing.route_landing_page`'s
        precedence: Approximated's ``Apx-Incoming-Host`` wins over the
        raw ``Host`` header so Approximated-fronted deploys see the
        client-facing hostname instead of the Fly internal address.
        """
        apx = None
        host = None
        for raw_name, raw_value in scope.get("headers", ()):
            name = raw_name.decode("latin-1").lower()
            if name == "apx-incoming-host":
                apx = raw_value.decode("latin-1")
            elif name == "host":
                host = raw_value.decode("latin-1")
        return apx or host

    def _is_admin_host(self, scope: dict) -> bool:
        host = self._resolve_host(scope)
        if not host:
            return False
        return is_admin_domain(host)
