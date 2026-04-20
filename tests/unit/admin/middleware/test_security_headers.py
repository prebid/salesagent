"""Unit tests for L0-27 SecurityHeadersMiddleware (module scaffold).

Canonical spec: ``flask-to-fastapi-foundation-modules.md`` §11.28.

The middleware injects browser-hardening headers on every HTTP response.
At L0 the module is authored but NOT wired into the middleware stack —
that wiring lands at L2 positioned INSIDE ``TrustedHostMiddleware`` and
OUTSIDE ``UnifiedAuthMiddleware`` (per §11.36 MIDDLEWARE_STACK_VERSION
assertion). These tests validate the module in isolation.

The brief requires the 4 canonical security headers:
  1. ``Strict-Transport-Security`` (HSTS) — forces HTTPS, preloadable
  2. ``X-Content-Type-Options: nosniff`` — disables MIME sniffing
  3. ``X-Frame-Options: DENY`` — prevents clickjacking
  4. ``Content-Security-Policy`` — restricts script/style/etc. sources

§11.28 also covers ``Referrer-Policy`` and ``Permissions-Policy``;
these are exercised by the same response-interceptor path.

Obligations:
  1. ``Strict-Transport-Security`` present on 200 responses when
     ``https_only=True`` (the production default).
  2. ``X-Content-Type-Options: nosniff`` present on 200 responses.
  3. ``X-Frame-Options: DENY`` present on 200 responses.
  4. ``Content-Security-Policy`` is set (the canonical default includes
     ``default-src 'self'`` and ``frame-ancestors 'none'``).
  5. ``Strict-Transport-Security`` is ABSENT when ``https_only=False``
     (staging / non-public domain — HSTS is irrevocable for 1 year).
  6. Headers survive 403 / error responses (the middleware sits outside
     UnifiedAuth/CSRF so auth-rejected responses also carry hardening).
"""

from __future__ import annotations

from starlette.applications import Starlette
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from src.admin.middleware.security_headers import SecurityHeadersMiddleware


def _build_app(*, https_only: bool = True) -> Starlette:
    async def ok(_request):
        return PlainTextResponse("ok")

    async def forbidden(_request):
        return JSONResponse({"detail": "forbidden"}, status_code=403)

    app = Starlette(
        routes=[
            Route("/ok", ok),
            Route("/403", forbidden),
        ]
    )
    app.add_middleware(SecurityHeadersMiddleware, https_only=https_only)
    return app


def test_hsts_header_on_200() -> None:
    client = TestClient(_build_app())

    response = client.get("/ok")

    assert response.status_code == 200
    hsts = response.headers.get("strict-transport-security", "")
    assert "max-age=" in hsts, f"HSTS header missing on 200 response: {response.headers!r}"


def test_x_content_type_options_nosniff() -> None:
    client = TestClient(_build_app())

    response = client.get("/ok")

    assert response.headers.get("x-content-type-options") == "nosniff"


def test_x_frame_options_deny() -> None:
    client = TestClient(_build_app())

    response = client.get("/ok")

    assert response.headers.get("x-frame-options") == "DENY"


def test_content_security_policy_set() -> None:
    client = TestClient(_build_app())

    response = client.get("/ok")

    csp = response.headers.get("content-security-policy", "")
    assert "default-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp


def test_hsts_absent_when_https_only_false() -> None:
    """HSTS is irrevocable for its ``max-age`` — do not emit in staging."""
    client = TestClient(_build_app(https_only=False))

    response = client.get("/ok")

    assert "strict-transport-security" not in response.headers
    # Other hardening headers still present — https_only only gates HSTS.
    assert response.headers.get("x-frame-options") == "DENY"


def test_headers_present_on_403() -> None:
    """Hardening headers fire on auth-rejected responses too.

    Load-bearing: at L2 this middleware sits OUTSIDE UnifiedAuth/CSRF, so
    403 responses from the inner chain still carry the canonical header
    set.
    """
    client = TestClient(_build_app())

    response = client.get("/403")

    assert response.status_code == 403
    assert response.headers.get("x-frame-options") == "DENY"
    assert "default-src 'self'" in response.headers.get("content-security-policy", "")
