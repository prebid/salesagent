"""L0-06 — CSRFOriginMiddleware obligation tests.

Pattern (a) Red: stub ``CSRFOriginMiddleware.__call__`` raises
``NotImplementedError``. Red tests assert specific response codes (200 / 403)
per Origin scenario; all fail with the 500/exception from the stub — a
SEMANTIC failure, not ImportError.

Canonical: .claude/notes/flask-to-fastapi/flask-to-fastapi-foundation-modules.md §11.7.
"""

from __future__ import annotations

import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from src.admin.csrf import CSRFOriginMiddleware, _extract_header, _origin_of


def _build(
    *,
    allowed_origins: tuple[str, ...] = ("https://admin.sales-agent.example.com",),
    allowed_suffixes: tuple[str, ...] = (),
) -> TestClient:
    async def ok(request):  # type: ignore[no-untyped-def]
        return PlainTextResponse("ok")

    inner = Starlette(
        routes=[
            Route("/admin/foo", ok, methods=["GET", "POST", "PUT", "DELETE"]),
            Route("/mcp/tool", ok, methods=["POST"]),
            Route("/a2a/foo", ok, methods=["POST"]),
            Route("/api/v1/foo", ok, methods=["POST"]),
            Route("/_internal/health", ok, methods=["POST"]),
            Route("/.well-known/foo", ok, methods=["POST"]),
            Route("/agent.json", ok, methods=["POST"]),
            Route("/admin/auth/google/callback", ok, methods=["POST"]),
            Route("/admin/auth/oidc/callback", ok, methods=["POST"]),
            Route("/admin/auth/gam/callback", ok, methods=["POST"]),
        ]
    )
    wrapped = CSRFOriginMiddleware(
        inner,
        allowed_origins=allowed_origins,
        allowed_origin_suffixes=allowed_suffixes,
    )
    return TestClient(wrapped)


# ---------------------------------------------------------------------------
# Harness sanity — proves the middleware is actually wired.
# ---------------------------------------------------------------------------


def test_wrong_origin_returns_403_not_200() -> None:
    """If the middleware were bypassed, this POST would be 200. It must be 403."""
    with _build() as c:
        r = c.post("/admin/foo", headers={"Origin": "https://evil.example.com"})
    assert r.status_code == 403


def test_missing_allowed_origins_raises_runtime_error() -> None:
    """Constructor rejects empty-allowed: misconfiguration must fail loud."""

    async def noop(scope, receive, send):  # type: ignore[no-untyped-def]
        pass

    with pytest.raises(RuntimeError):
        CSRFOriginMiddleware(noop, allowed_origins=(), allowed_origin_suffixes=())


# ---------------------------------------------------------------------------
# Safe methods bypass
# ---------------------------------------------------------------------------


def test_safe_method_get_bypasses_origin_check() -> None:
    with _build() as c:
        assert c.get("/admin/foo").status_code == 200


def test_safe_method_head_bypasses_origin_check() -> None:
    with _build() as c:
        assert c.head("/admin/foo").status_code == 200


# ---------------------------------------------------------------------------
# Exempt path prefixes — 7 transport exempts + 3 OAuth callback exempts.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/mcp/tool",
        "/a2a/foo",
        "/api/v1/foo",
        "/_internal/health",
        "/.well-known/foo",
        "/agent.json",
    ],
)
def test_transport_exempt_paths_post_without_origin_passes(path: str) -> None:
    """AdCP / internal transports bypass CSRF regardless of Origin."""
    with _build() as c:
        assert c.post(path).status_code == 200


def test_bare_mcp_mount_exempt_via_exact_match() -> None:
    """POST to ``/mcp`` (no trailing slash) is exempt — the MCP mount serves
    there. Pre-rename, the ``startswith("/mcp")`` predicate exempted this by
    accident; post-rename, it is exempt by the new exact-match semantic.
    """
    # Construct a scope-level test: the default _build() harness routes
    # /mcp/tool but not bare /mcp. The predicate is what matters, so we
    # exercise it directly.
    from src.admin.csrf import _is_exempt

    assert _is_exempt("/mcp") is True
    assert _is_exempt("/a2a") is True


def test_transport_prefix_does_not_bleed_into_sibling_routes() -> None:
    """A future ``/mcpanything`` or ``/a2aadmin`` top-level route MUST NOT be
    silently CSRF-exempt. The pre-rename ``startswith("/mcp")`` predicate
    exempted these. Post-rename, bare entries match exact OR ``entry + "/"``
    prefix — so ``/mcpanything`` is NOT exempt.
    """
    from src.admin.csrf import _is_exempt

    assert _is_exempt("/mcpanything") is False
    assert _is_exempt("/a2aadmin") is False
    assert _is_exempt("/agent.jsonify") is False


def test_trailing_slash_prefixes_require_the_slash() -> None:
    """Entries declared with a trailing slash (``/api/v1/``, ``/_internal/``)
    match only via prefix, not as a bare path. A hypothetical bare ``/api/v1``
    or ``/_internal`` is NOT exempt — there are no such routes today, but the
    invariant is enforced in case one is added without the corresponding
    exempt entry.
    """
    from src.admin.csrf import _is_exempt

    assert _is_exempt("/api/v1/foo") is True
    assert _is_exempt("/api/v1") is False
    assert _is_exempt("/_internal") is False
    assert _is_exempt("/.well-known") is False


@pytest.mark.parametrize(
    "path",
    [
        "/admin/auth/google/callback",
        "/admin/auth/oidc/callback",
        "/admin/auth/gam/callback",
    ],
)
def test_oauth_callback_paths_post_without_origin_passes(path: str) -> None:
    """OAuth callbacks arrive from the provider's origin (byte-immutable URIs)."""
    with _build() as c:
        assert c.post(path).status_code == 200


# ---------------------------------------------------------------------------
# Origin-header validation on unsafe methods / non-exempt paths.
# ---------------------------------------------------------------------------


def test_matching_origin_passes() -> None:
    with _build() as c:
        r = c.post(
            "/admin/foo",
            headers={"Origin": "https://admin.sales-agent.example.com"},
        )
    assert r.status_code == 200


def test_mismatched_origin_403() -> None:
    with _build() as c:
        r = c.post("/admin/foo", headers={"Origin": "https://evil.example.com"})
    assert r.status_code == 403


def test_null_origin_403() -> None:
    """Origin: null from file:// / sandboxed iframes — rejected."""
    with _build() as c:
        r = c.post("/admin/foo", headers={"Origin": "null"})
    assert r.status_code == 403


def test_unparseable_origin_403() -> None:
    with _build() as c:
        r = c.post("/admin/foo", headers={"Origin": "not-a-url"})
    assert r.status_code == 403


def test_missing_origin_with_matching_referer_passes() -> None:
    with _build() as c:
        r = c.post(
            "/admin/foo",
            headers={"Referer": "https://admin.sales-agent.example.com/prev"},
        )
    assert r.status_code == 200


def test_missing_origin_and_referer_passes_under_lax() -> None:
    """Legacy UA case — SameSite=Lax session cookie is primary defense."""
    with _build() as c:
        assert c.post("/admin/foo").status_code == 200


def test_missing_origin_with_mismatched_referer_403() -> None:
    with _build() as c:
        r = c.post("/admin/foo", headers={"Referer": "https://evil.example.com/x"})
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# Origin normalization — RFC 6454 serialization.
# ---------------------------------------------------------------------------


def test_uppercase_origin_normalized() -> None:
    with _build() as c:
        r = c.post(
            "/admin/foo",
            headers={"Origin": "HTTPS://ADMIN.SALES-AGENT.EXAMPLE.COM"},
        )
    assert r.status_code == 200


def test_origin_of_strips_default_https_port() -> None:
    assert _origin_of("https://admin.sales-agent.example.com:443") == "https://admin.sales-agent.example.com"


def test_origin_of_strips_default_http_port() -> None:
    assert _origin_of("http://admin.sales-agent.example.com:80") == "http://admin.sales-agent.example.com"


def test_origin_of_preserves_nondefault_port() -> None:
    assert _origin_of("https://admin.sales-agent.example.com:8443") == "https://admin.sales-agent.example.com:8443"


# ---------------------------------------------------------------------------
# Wildcard subdomain matching — for newly-provisioned tenants.
# ---------------------------------------------------------------------------


def test_wildcard_suffix_match_passes() -> None:
    with _build(allowed_origins=(), allowed_suffixes=(".sales-agent.example.com",)) as c:
        r = c.post(
            "/admin/foo",
            headers={"Origin": "https://tenant-foo.sales-agent.example.com"},
        )
    assert r.status_code == 200


def test_wildcard_suffix_nonmatch_403() -> None:
    with _build(allowed_origins=(), allowed_suffixes=(".sales-agent.example.com",)) as c:
        r = c.post(
            "/admin/foo",
            headers={"Origin": "https://sales-agent.example.com.evil.com"},
        )
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# Header extraction — ASGI contract.
# ---------------------------------------------------------------------------


def test_extract_header_case_insensitive_lookup() -> None:
    scope = {"headers": [(b"Origin", b"https://foo")]}
    assert _extract_header(scope, "origin") == "https://foo"


def test_extract_header_first_match_wins_for_duplicate_origin() -> None:
    scope = {
        "headers": [
            (b"origin", b"https://admin.sales-agent.example.com"),
            (b"origin", b"https://evil.example.com"),
        ]
    }
    assert _extract_header(scope, "origin") == "https://admin.sales-agent.example.com"
