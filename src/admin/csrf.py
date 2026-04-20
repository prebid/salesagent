"""CSRF defense via Origin header validation + SameSite=Lax session cookie.

Protects unsafe HTTP methods (POST/PUT/PATCH/DELETE) on non-exempt paths by
requiring the Origin (or Referer fallback) header to match one of the
configured allowed origins. Safe methods and exempt paths pass through.

Design choices (per foundation-modules.md §11.7):

1. Pure-ASGI, NOT BaseHTTPMiddleware — avoids Starlette #1729 task-group
   interleaving and keeps body streams intact for downstream handlers.
2. Header-only validation — no form-field parsing, no body buffering.
3. Paired with SessionMiddleware's SameSite=Lax HttpOnly=True session cookie
   (the session cookie is the only credential that matters for admin CSRF).
4. Wildcard subdomain matching — ``allowed_origin_suffixes`` accepts newly-
   provisioned tenant subdomains without a startup-time refresh.
5. Exempt paths include AdCP transport surfaces (``/mcp``, ``/a2a``,
   ``/api/v1``), internal health/well-known (``/_internal/``,
   ``/.well-known/``, ``/agent.json``), and the 3 OAuth callback paths that
   providers POST to directly (byte-immutable per notes/CLAUDE.md invariant 6).

Per .claude/notes/flask-to-fastapi/flask-to-fastapi-foundation-modules.md §11.7.
Critical Invariant #5 (Option A — SameSite=Lax + Origin validation).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from html import escape
from typing import Any
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

_SAFE_METHODS: frozenset[str] = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})

# Byte-immutable OAuth callback paths (notes/CLAUDE.md invariant 6). Providers
# POST the authorization-code response to these URLs; they cannot be
# CSRF-protected because the POST originates from the provider's origin.
_OAUTH_CALLBACK_EXEMPTS: tuple[str, ...] = (
    "/admin/auth/google/callback",
    "/admin/auth/oidc/callback",
    "/admin/auth/gam/callback",
)

# AdCP and internal transport surfaces — out-of-scope for admin CSRF.
_TRANSPORT_EXEMPT_PREFIXES: tuple[str, ...] = (
    "/mcp",
    "/a2a",
    "/api/v1/",
    "/.well-known/",
    "/agent.json",
    "/_internal/",
)
# FIXME(adcp-webhooks): when AdCP inbound push-notification receivers land
# (per PushNotificationConfig DB rows), add their path prefix to this tuple
# and update test_architecture_csrf_exempt_covers_webhooks.py.


_DEFAULT_PORTS: dict[str, int] = {"http": 80, "https": 443, "ws": 80, "wss": 443}


def _extract_header(scope: dict, name: str) -> str | None:
    """Extract a single header value from an ASGI scope.

    ASGI guarantees header names in ``scope["headers"]`` are lowercased bytes.
    The defensive ``.lower()`` on the raw name covers the case where a unit
    test constructs a scope by hand with mixed-case header bytes; it is cheap
    (byte-level) and avoids a test-only footgun.

    First-match policy: duplicate headers (a client sending two Origin lines)
    are reduced to the first occurrence. RFC 6454 §7.2 says Origin is a
    single-value header, so two Origin lines is already malformed — rejecting
    it by reading only the first is safe and avoids ambiguity.
    """
    target = name.lower().encode("latin-1")
    for raw_name, raw_value in scope.get("headers", []):
        if raw_name.lower() == target:
            return raw_value.decode("latin-1")
    return None


def _origin_of(url: str) -> str | None:
    """Return RFC 6454-serialized scheme://host[:port] for a URL.

    Applies the serialization rules browsers apply before sending Origin /
    Referer-derived origins on the wire:
      - scheme lowercased
      - host lowercased
      - default port for the scheme stripped (443 for https, 80 for http, etc.)
      - IPv6 hosts re-bracketed (urllib strips brackets in ``.hostname``)

    Any ``allowed_origins`` entry registered WITH a default port would
    otherwise never match a browser Origin header (which serializes without
    the default port), 403-ing every same-origin POST.
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if not parts.scheme or not parts.hostname:
        return None
    scheme = parts.scheme.lower()
    host = parts.hostname.lower()
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    try:
        port = parts.port
    except ValueError:
        return None
    if port is None or port == _DEFAULT_PORTS.get(scheme):
        return f"{scheme}://{host}"
    return f"{scheme}://{host}:{port}"


def _is_exempt(path: str) -> bool:
    if path in _OAUTH_CALLBACK_EXEMPTS:
        return True
    return any(path.startswith(p) for p in _TRANSPORT_EXEMPT_PREFIXES)


def _scope_wants_html(scope: dict) -> bool:
    """Mirror of ``src.admin.content_negotiation._wants_html``, scope-native.

    Used by ``_respond_403`` to emit HTML for admin browser users and JSON for
    everything else. Middleware runs BEFORE FastAPI exception handlers, so the
    negotiation is inlined here rather than delegated to the AdCPError handler.
    """
    path: str = scope.get("path", "")
    if not (path.startswith("/admin/") or path.startswith("/tenant/")):
        return False
    if _extract_header(scope, "hx-request"):
        return False
    xrw = _extract_header(scope, "x-requested-with")
    if xrw and xrw.lower() == "xmlhttprequest":
        return False
    accept = _extract_header(scope, "accept") or ""
    if "application/json" in accept and "text/html" not in accept:
        return False
    return "text/html" in accept


def _render_403_html(detail: str) -> str:
    return (
        "<!DOCTYPE html>\n"
        '<html><head><meta charset="utf-8"><title>403 Forbidden</title></head>'
        '<body style="font-family: sans-serif; max-width: 600px; margin: 4em auto;">'
        "<h1>403 Forbidden</h1>"
        f"<p>{escape(detail)}</p>"
        '<p><a href="/admin/">Return to the admin dashboard</a></p>'
        "</body></html>"
    )


async def _respond_403(send: Any, scope: dict, detail: str) -> None:
    """403 response, Accept-aware.

    Browsers hitting admin paths get HTML; API/MCP/A2A callers and XHR fetches
    get JSON. Mirrors the AdCPError handler's negotiation logic so the CSRF
    reject path and the domain-error path have identical UX.
    """
    if _scope_wants_html(scope):
        body = _render_403_html(detail).encode("utf-8")
        content_type = b"text/html; charset=utf-8"
    else:
        body = json.dumps({"detail": detail}).encode("utf-8")
        content_type = b"application/json"
    await send(
        {
            "type": "http.response.start",
            "status": 403,
            "headers": [
                (b"content-type", content_type),
                (b"content-length", str(len(body)).encode()),
                (b"vary", b"Origin, Accept"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


class CSRFOriginMiddleware:
    """Pure-ASGI CSRF defense via Origin header validation.

    Constructor accepts:

    - ``allowed_origins`` — explicit set of origin strings
      (e.g. ``{"https://admin.example.com"}``)
    - ``allowed_origin_suffixes`` — domain suffixes for wildcard matching
      (e.g. ``{".scope3.com"}`` accepts any
      ``https://*.scope3.com`` / ``http://*.scope3.com``)

    The wildcard set lets newly-provisioned tenant subdomains work without a
    startup-time refresh. Both sets are closed over at construction.

    Raises ``RuntimeError`` on empty-allowed — this is a misconfiguration that
    would silently 403 every unsafe request, so fail loud at construction.
    """

    def __init__(
        self,
        app: Any,
        *,
        allowed_origins: Iterable[str] = (),
        allowed_origin_suffixes: Iterable[str] = (),
    ) -> None:
        self.app = app
        self.allowed_origins: frozenset[str] = frozenset(o.rstrip("/").lower() for o in allowed_origins if o)
        self.allowed_suffixes: tuple[str, ...] = tuple(s.lower() for s in allowed_origin_suffixes if s)
        if not self.allowed_origins and not self.allowed_suffixes:
            raise RuntimeError(
                "CSRFOriginMiddleware requires at least one of allowed_origins or allowed_origin_suffixes"
            )

    def _origin_allowed(self, normalized_origin: str) -> bool:
        if normalized_origin in self.allowed_origins:
            return True
        # Wildcard subdomain match: extract host from normalized origin
        # ("https://foo.example.com" → "foo.example.com")
        try:
            host = urlsplit(normalized_origin).netloc.lower()
        except ValueError:
            return False
        return any(host.endswith(suffix) or host == suffix.lstrip(".") for suffix in self.allowed_suffixes)

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method: str = scope["method"]
        path: str = scope["path"]

        # Safe methods always bypass
        if method in _SAFE_METHODS:
            await self.app(scope, receive, send)
            return

        # Exempt paths bypass
        if _is_exempt(path):
            await self.app(scope, receive, send)
            return

        # Origin validation
        origin = _extract_header(scope, "origin")

        if origin is None:
            # No Origin header — legacy user-agent or a same-origin request
            # the browser did not annotate. Fall back to Referer.
            #
            # SAFETY: "missing Origin AND missing Referer = accept" is safe
            # ONLY because SameSite=Lax on the session cookie blocks the
            # cookie on cross-site unsafe requests. Without Lax this branch
            # would be a CSRF hole. The Lax-dependency is load-bearing — if
            # SessionMiddleware is configured with ``same_site="none"``, this
            # branch must reject instead.
            referer = _extract_header(scope, "referer")
            if referer is None:
                await self.app(scope, receive, send)
                return
            ref_origin = _origin_of(referer)
            if ref_origin is None:
                await _respond_403(send, scope, "CSRF: unparseable Referer")
                return
            if not self._origin_allowed(ref_origin):
                logger.info(
                    "CSRF rejection (referer): path=%s method=%s referer_origin=%s",
                    path,
                    method,
                    ref_origin,
                )
                await _respond_403(send, scope, "CSRF: cross-origin request rejected")
                return
            await self.app(scope, receive, send)
            return

        if origin == "null":
            # Origin: null appears for file://, sandboxed iframes with
            # "allow-scripts" but not "allow-same-origin", and certain
            # cross-origin redirect chains. None of these are legitimate
            # admin request sources — reject.
            logger.info("CSRF rejection (null origin): path=%s method=%s", path, method)
            await _respond_403(send, scope, "CSRF: opaque origin rejected")
            return

        normalized = _origin_of(origin)
        if normalized is None:
            await _respond_403(send, scope, "CSRF: unparseable Origin")
            return

        if not self._origin_allowed(normalized):
            logger.info(
                "CSRF rejection: path=%s method=%s origin=%s",
                path,
                method,
                normalized,
            )
            await _respond_403(send, scope, "CSRF: cross-origin request rejected")
            return

        await self.app(scope, receive, send)
