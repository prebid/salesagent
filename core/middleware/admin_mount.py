"""ASGI middleware: dispatch admin paths to a WSGI Flask app.

Mounts the existing :func:`src.admin.app.create_app` Flask application
on the same Starlette binary that ``serve(transport="both")`` produces.
No nginx in the loop.

Routing decision (in order):

* ``Host`` (or ``Apx-Incoming-Host``) matches ``ADMIN_DOMAIN`` /
  ``admin.${SALES_AGENT_DOMAIN}`` → entire request goes to Flask with
  ``root_path=/admin`` (replaces the nginx ``server_name admin.*``
  block that injected ``X-Forwarded-Prefix: /admin``)
* ``Host`` equals ``SALES_AGENT_DOMAIN`` exactly (apex, no subdomain)
  and path is ``/`` → 302 redirect to ``/signup`` (replaces the nginx
  ``server_name ${SALES_AGENT_DOMAIN}; location = /`` block that
  proxied apex root to /signup)
* ``/robots.txt`` → public ``text/plain`` ``Disallow: /`` served directly.
  Without this short-circuit, the A2A surface answers /robots.txt with
  a 401 (bearer auth wraps every non-admin path), which generates log
  spam from well-behaved crawlers and confuses tools that key off the
  status code.
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

from src.core.domain_config import get_sales_agent_domain, is_admin_domain

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

            # Public robots.txt: serve a stable ``Disallow: /`` directly so
            # crawlers don't see a 401 from the bearer-auth-protected A2A
            # surface. This is a host-level resource (no tenant scope, no
            # auth), so neither Flask nor A2A is the right owner.
            if path == "/robots.txt" and scope.get("method", "GET") in ("GET", "HEAD"):
                await self._send_robots_txt(send, head_only=scope.get("method") == "HEAD")
                return

            # Apex redirect: bare ``sales-agent.example.com/`` (no
            # subdomain) goes to /signup. Replaces the nginx
            # ``location = /`` block in the multi-tenant config that
            # proxied apex root → /signup. Subdomain hosts (tenant.*,
            # admin.*) are unaffected — they fall through to A2A or
            # admin dispatch as before. Query string is preserved so
            # marketing attribution (utm_source, ref, etc.) survives.
            if path == "/" and self._is_apex_host(scope):
                qs = scope.get("query_string", b"") or b""
                location = "/signup"
                if qs:
                    location = location + "?" + qs.decode("latin-1")
                await self._send_redirect(send, location)
                return

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

    def _is_apex_host(self, scope: dict) -> bool:
        """True if the request host is the bare SALES_AGENT_DOMAIN (no subdomain).

        Strips any port suffix before comparing. Comparison is
        case-insensitive — Host headers are case-insensitive per RFC 3986
        and proxies can normalize either way. Returns False when
        ``SALES_AGENT_DOMAIN`` is unset (single-tenant / dev), so localhost
        and ``localtest.me`` aliases never trigger the apex redirect.
        """
        sales_domain = get_sales_agent_domain()
        if not sales_domain:
            return False
        host = self._resolve_host(scope)
        if not host:
            return False
        host_no_port = host.split(":", 1)[0]
        return host_no_port.lower() == sales_domain.lower()

    _ROBOTS_TXT_BODY = b"User-agent: *\nDisallow: /\n"

    @classmethod
    async def _send_robots_txt(cls, send: Any, *, head_only: bool) -> None:
        """Emit a public ``robots.txt`` response.

        The API host has no human-crawlable content, so the canonical
        response is ``Disallow: /``. ``cache-control: public, max-age=86400``
        keeps crawlers from re-fetching every minute. HEAD requests get
        the same headers with an empty body per RFC 9110.
        """
        body = cls._ROBOTS_TXT_BODY
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [
                    (b"content-type", b"text/plain; charset=utf-8"),
                    (b"content-length", str(len(body)).encode("ascii")),
                    (b"cache-control", b"public, max-age=86400"),
                ],
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": b"" if head_only else body,
                "more_body": False,
            }
        )

    @staticmethod
    async def _send_redirect(send: Any, location: str) -> None:
        """Emit a 302 redirect through the ASGI ``send`` channel.

        Uses 302 (Found) — the apex landing target may move (today
        ``/signup``, tomorrow a marketing page), so the redirect is
        intentionally non-cacheable. ``cache-control: no-store`` keeps
        intermediaries from pinning a stale target during rollout.
        """
        await send(
            {
                "type": "http.response.start",
                "status": 302,
                "headers": [
                    (b"location", location.encode("latin-1")),
                    (b"content-length", b"0"),
                    (b"cache-control", b"no-store"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": b"", "more_body": False})
