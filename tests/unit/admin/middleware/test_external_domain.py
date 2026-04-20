"""L0-07 — ApproximatedExternalDomainMiddleware obligation tests.

Pattern (a) Red: stub middleware redirects every request with 302 to a fixed
URL. Tests assert path-gating (no fire on ``/api/*``, ``/mcp/*``, etc.),
307-not-302 redirect semantics (POST body preserved), sales-agent-subdomain
bypass, tenant-lookup-failure fall-through, and external-domain to
subdomain redirect. Red fails SEMANTICALLY (wrong status, fires when it
shouldn't).

Canonical: .claude/notes/flask-to-fastapi/flask-to-fastapi-foundation-modules.md §11.8.
Critical Invariant #5 (Approximated BEFORE CSRF).
"""

from __future__ import annotations

from unittest.mock import patch

from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from src.admin.middleware.external_domain import (
    ApproximatedExternalDomainMiddleware,
)


def _build() -> TestClient:
    async def ok(request):  # type: ignore[no-untyped-def]
        return PlainTextResponse("ok")

    inner = Starlette(
        routes=[
            Route("/admin", ok, methods=["GET", "POST"]),
            Route("/admin/foo", ok, methods=["GET", "POST"]),
            Route("/tenant/t1", ok, methods=["GET", "POST"]),
            Route("/tenant/t1/foo", ok, methods=["GET", "POST"]),
            Route("/api/v1/stuff", ok, methods=["GET", "POST"]),
            Route("/mcp/tool", ok, methods=["POST"]),
            Route("/other", ok, methods=["GET"]),
        ]
    )
    wrapped = ApproximatedExternalDomainMiddleware(inner)
    return TestClient(wrapped, follow_redirects=False)


# ---------------------------------------------------------------------------
# Path gating — middleware MUST NOT fire outside /admin/* + /tenant/*.
# ---------------------------------------------------------------------------


def test_non_admin_non_tenant_path_passes_through_with_apx_header() -> None:
    """Even with Apx-Incoming-Host present, /api/v1/* must not redirect."""
    with _build() as c:
        r = c.post(
            "/api/v1/stuff",
            headers={"Apx-Incoming-Host": "publisher.example.com"},
        )
    assert r.status_code == 200


def test_mcp_path_passes_through_with_apx_header() -> None:
    """MCP transport is out-of-scope for Approximated redirect."""
    with _build() as c:
        r = c.post("/mcp/tool", headers={"Apx-Incoming-Host": "publisher.example.com"})
    assert r.status_code == 200


def test_other_path_passes_through() -> None:
    with _build() as c:
        r = c.get("/other", headers={"Apx-Incoming-Host": "publisher.example.com"})
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# /admin/* path — all 4 branches
# ---------------------------------------------------------------------------


def test_admin_path_without_apx_header_passes_through() -> None:
    """No Apx header → no redirect."""
    with _build() as c:
        r = c.get("/admin/foo")
    assert r.status_code == 200


def test_admin_path_with_sales_agent_subdomain_passes_through() -> None:
    """Apx host IS a sales-agent subdomain → normal routing."""
    with (
        patch(
            "src.admin.middleware.external_domain.is_sales_agent_domain",
            return_value=True,
        ),
    ):
        with _build() as c:
            r = c.get(
                "/admin/foo",
                headers={"Apx-Incoming-Host": "tenant.sales-agent.example.com"},
            )
    assert r.status_code == 200


def test_admin_path_with_external_domain_redirects_307(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """External domain + known tenant → 307 redirect (NOT 302) preserving POST body."""
    monkeypatch.delenv("PRODUCTION", raising=False)
    monkeypatch.setenv("ADCP_SALES_PORT", "8080")
    with (
        patch(
            "src.admin.middleware.external_domain.is_sales_agent_domain",
            return_value=False,
        ),
        patch(
            "src.admin.middleware.external_domain.get_tenant_by_virtual_host",
            return_value={"tenant_id": "t1", "subdomain": "pub-t1"},
        ),
    ):
        with _build() as c:
            r = c.post(
                "/admin/foo",
                headers={"Apx-Incoming-Host": "publisher.example.com"},
                content=b"form=data",
            )
    # 307 preserves POST body; 302 does not.
    assert r.status_code == 307
    assert "pub-t1" in r.headers["location"]
    assert "/admin/foo" in r.headers["location"]


def test_admin_path_external_domain_preserves_query_string(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Redirect URL must include the original query string."""
    monkeypatch.delenv("PRODUCTION", raising=False)
    monkeypatch.setenv("ADCP_SALES_PORT", "8080")
    with (
        patch(
            "src.admin.middleware.external_domain.is_sales_agent_domain",
            return_value=False,
        ),
        patch(
            "src.admin.middleware.external_domain.get_tenant_by_virtual_host",
            return_value={"tenant_id": "t1", "subdomain": "pub-t1"},
        ),
    ):
        with _build() as c:
            r = c.get(
                "/admin/foo?q=1&r=2",
                headers={"Apx-Incoming-Host": "publisher.example.com"},
            )
    assert r.status_code == 307
    assert "q=1" in r.headers["location"]
    assert "r=2" in r.headers["location"]


def test_admin_path_tenant_lookup_failure_falls_through() -> None:
    """DB down → pass through to admin handler (not 500) — availability guard."""
    with (
        patch(
            "src.admin.middleware.external_domain.is_sales_agent_domain",
            return_value=False,
        ),
        patch(
            "src.admin.middleware.external_domain.get_tenant_by_virtual_host",
            side_effect=RuntimeError("db down"),
        ),
    ):
        with _build() as c:
            r = c.get(
                "/admin/foo",
                headers={"Apx-Incoming-Host": "publisher.example.com"},
            )
    assert r.status_code == 200


def test_admin_path_tenant_lookup_none_falls_through() -> None:
    """Unknown virtual_host → pass through (admin UI shows its own error)."""
    with (
        patch(
            "src.admin.middleware.external_domain.is_sales_agent_domain",
            return_value=False,
        ),
        patch(
            "src.admin.middleware.external_domain.get_tenant_by_virtual_host",
            return_value=None,
        ),
    ):
        with _build() as c:
            r = c.get(
                "/admin/foo",
                headers={"Apx-Incoming-Host": "publisher.example.com"},
            )
    assert r.status_code == 200


def test_admin_path_tenant_without_subdomain_falls_through() -> None:
    """Tenant row lacks subdomain → pass through."""
    with (
        patch(
            "src.admin.middleware.external_domain.is_sales_agent_domain",
            return_value=False,
        ),
        patch(
            "src.admin.middleware.external_domain.get_tenant_by_virtual_host",
            return_value={"tenant_id": "t1", "subdomain": None},
        ),
    ):
        with _build() as c:
            r = c.get(
                "/admin/foo",
                headers={"Apx-Incoming-Host": "publisher.example.com"},
            )
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# /tenant/* path — same external-domain redirect should apply (D1 2026-04-16).
# ---------------------------------------------------------------------------


def test_tenant_path_with_external_domain_redirects_307(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Canonical tenant-prefix mount also gets the Approximated redirect."""
    monkeypatch.delenv("PRODUCTION", raising=False)
    monkeypatch.setenv("ADCP_SALES_PORT", "8080")
    with (
        patch(
            "src.admin.middleware.external_domain.is_sales_agent_domain",
            return_value=False,
        ),
        patch(
            "src.admin.middleware.external_domain.get_tenant_by_virtual_host",
            return_value={"tenant_id": "t1", "subdomain": "pub-t1"},
        ),
    ):
        with _build() as c:
            r = c.get(
                "/tenant/t1/foo",
                headers={"Apx-Incoming-Host": "publisher.example.com"},
            )
    assert r.status_code == 307
    assert "pub-t1" in r.headers["location"]


def test_tenant_path_without_apx_passes_through() -> None:
    with _build() as c:
        r = c.get("/tenant/t1/foo")
    assert r.status_code == 200
