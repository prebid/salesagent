"""Pure-ASGI middleware injecting browser-hardening response headers.

Canonical spec: ``flask-to-fastapi-foundation-modules.md`` §11.28.

Runtime position (canonical L2+ stack per §11.36):

    RequestID → Fly → ExternalDomain → TrustedHost → SecurityHeaders →
    UnifiedAuth → LegacyAdminRedirect → Session → CSRF → RestCompat → CORS

INSIDE TrustedHostMiddleware, OUTSIDE UnifiedAuthMiddleware — so login
pages, 403s, and error pages carry the same hardening headers as
authenticated pages. Flask had no equivalent module; Approximated's
edge proxy sets a subset of these but is bypassed when a request
reaches via the subdomain path.

At L0 this module is scaffold-only. L2 wires it into the middleware
stack alongside ``TrustedHostMiddleware``.

Header set
----------
* ``Strict-Transport-Security`` (HSTS) — forces HTTPS for ``hsts_seconds``
  (default 1 year), preloadable. Gated by ``https_only`` — HSTS is
  IRREVOCABLE client-side for its ``max-age`` so MUST NOT be emitted in
  staging with a non-public domain.
* ``X-Frame-Options: DENY`` — double-defence with CSP ``frame-ancestors``
  for legacy UAs.
* ``X-Content-Type-Options: nosniff`` — disables MIME sniffing.
* ``Referrer-Policy: strict-origin-when-cross-origin`` — leaks only
  origin (not path) on cross-origin navigations.
* ``Content-Security-Policy`` — restricts script/style/img/font/connect
  sources. Default permits ``'unsafe-inline'`` on styles for existing
  inline style usage; tightening is v2.1 scope.
* ``Permissions-Policy`` — disables sensor/media APIs admin does not use.
"""

from __future__ import annotations

from starlette.types import ASGIApp, Receive, Scope, Send

from src.admin.middleware._ascgi_headers import HeaderPair, build_header_wrapper

# Default CSP — tight but compatible with current admin templates.
DEFAULT_CSP = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: https:; "
    "font-src 'self' data:; "
    "connect-src 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self';"
)

# Permissions-Policy — disable sensors/media APIs admin does not use.
_DISABLED_PERMISSIONS = (
    "accelerometer=()",
    "camera=()",
    "geolocation=()",
    "gyroscope=()",
    "magnetometer=()",
    "microphone=()",
    "payment=()",
    "usb=()",
)
DEFAULT_PERMISSIONS_POLICY = ", ".join(_DISABLED_PERMISSIONS)

DEFAULT_HSTS_SECONDS = 31_536_000  # 1 year


class SecurityHeadersMiddleware:
    """Inject hardening headers on every HTTP response.

    Args:
        app: downstream ASGI app.
        https_only: if ``True``, emit HSTS. Default ``True``. Set ``False``
            in staging environments with a non-public domain — HSTS is
            irrevocable for ``hsts_seconds`` once a browser has seen it.
        csp: override the default CSP string. ``None`` → ``DEFAULT_CSP``.
        hsts_seconds: HSTS ``max-age``. Default ``DEFAULT_HSTS_SECONDS``
            (1 year).
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        https_only: bool = True,
        csp: str | None = None,
        hsts_seconds: int = DEFAULT_HSTS_SECONDS,
    ) -> None:
        self.app = app
        self.https_only = https_only
        self.csp = csp if csp is not None else DEFAULT_CSP
        self.hsts_seconds = hsts_seconds

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Append-if-missing semantics: an individual handler may override
        # any of these (rare — e.g., a PDF endpoint needing a tighter
        # ``default-src 'none'`` CSP). The list is computed lazily at
        # response time via the thunk so the conditional HSTS branch
        # doesn't plumb a separate flag through the wrapper.
        wrapped_send = build_header_wrapper(
            send,
            to_set=self._compute_headers,
            mode="append_if_missing",
        )
        await self.app(scope, receive, wrapped_send)

    def _compute_headers(self) -> list[HeaderPair]:
        """Build the list of security headers for this response.

        Called lazily by the header-wrapper on ``http.response.start``.
        Conditional HSTS is baked into this list (present iff ``https_only``)
        so the wrapper does not need to know about the condition.
        """
        headers: list[HeaderPair] = [
            (b"x-frame-options", b"DENY"),
            (b"x-content-type-options", b"nosniff"),
            (b"referrer-policy", b"strict-origin-when-cross-origin"),
            (b"content-security-policy", self.csp.encode("latin-1")),
            (b"permissions-policy", DEFAULT_PERMISSIONS_POLICY.encode("latin-1")),
        ]
        if self.https_only:
            hsts_value = f"max-age={self.hsts_seconds}; includeSubDomains; preload".encode("latin-1")
            headers.append((b"strict-transport-security", hsts_value))
        return headers
