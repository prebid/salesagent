"""L0-14 — content_negotiation obligation tests (Pattern a: stub-first + semantic).

Red state: ``_response_mode`` always returns 'json'. Tests fail because the
Accept-matrix semantic obligation (Invariant #3) is not met.

Green state: real branching per foundation-modules.md §11.11 + frontend-deep-audit
F6/H7 — path-scope to /admin/* + /tenant/*, XHR reject, AJAX reject, HTML for
browser navigation, HTML for browser-fetch (no AJAX indicator) on admin paths.
Also exercises templates/_fastapi_error.html pinned contract
{error_code, message, status_code}. The FastAPI-native error template lives
at templates/_fastapi_error.html; Flask routers continue to render
templates/error.html with the legacy {error, error_title, error_message,
back_url} contract until they are ported at L1+.
"""

from __future__ import annotations

from unittest.mock import MagicMock


def _mock_request(path: str, accept: str = "*/*", xhr: bool = False):
    req = MagicMock()
    req.url = MagicMock()
    req.url.path = path
    headers = {"accept": accept}
    if xhr:
        headers["x-requested-with"] = "XMLHttpRequest"

    def _get(key: str, default: str = "") -> str:
        return headers.get(key.lower(), default)

    req.headers = MagicMock()
    req.headers.get = _get
    return req


class TestResponseModeAcceptMatrix:
    def test_admin_html_accept_returns_html(self) -> None:
        """Obligation #1: admin path + browser navigation Accept → HTML."""
        from src.admin.content_negotiation import _response_mode

        req = _mock_request("/admin/users", accept="text/html,application/xhtml+xml,*/*;q=0.8")
        assert _response_mode(req) == "html"

    def test_admin_json_accept_returns_json(self) -> None:
        """Obligation #2: admin path + explicit JSON Accept → JSON."""
        from src.admin.content_negotiation import _response_mode

        req = _mock_request("/admin/users", accept="application/json")
        assert _response_mode(req) == "json"

    def test_mcp_path_always_json(self) -> None:
        """Obligation #3: AdCP MCP surface always JSON regardless of Accept."""
        from src.admin.content_negotiation import _response_mode

        req = _mock_request("/mcp/", accept="text/html")
        assert _response_mode(req) == "json"

    def test_tenant_html_accept_returns_html(self) -> None:
        """Obligation #4: /tenant/{id}/* path + text/html Accept → HTML.

        Per D1 2026-04-16 canonical URL routing, the admin surface lives at
        /tenant/{tenant_id}/... so it must participate in HTML negotiation.
        """
        from src.admin.content_negotiation import _response_mode

        req = _mock_request("/tenant/t1/users", accept="text/html")
        assert _response_mode(req) == "html"

    def test_xhr_always_json_even_with_html_accept(self) -> None:
        """Obligation #5: XHR indicator forces JSON regardless of Accept.

        Admin JS fetch() calls with X-Requested-With: XMLHttpRequest must not
        receive HTML — they expect structured error payloads.
        """
        from src.admin.content_negotiation import _response_mode

        req = _mock_request("/admin/users", accept="text/html", xhr=True)
        assert _response_mode(req) == "json"

    def test_browser_fetch_wildcard_on_admin_returns_html(self) -> None:
        """Obligation #6 (NEW at v2): browser-fetch with Accept: */* (no AJAX
        indicator) on /admin/* returns HTML.

        Prevents false-positive JSON for browser navigation that happens to
        send a wildcard Accept. Per frontend-deep-audit F6/H7.
        """
        from src.admin.content_negotiation import _response_mode

        req = _mock_request("/admin/foo", accept="*/*")
        assert _response_mode(req) == "html"

    def test_non_admin_path_wildcard_returns_json(self) -> None:
        """AdCP surfaces (anything outside /admin/ + /tenant/) get JSON even
        on wildcard Accept."""
        from src.admin.content_negotiation import _response_mode

        req = _mock_request("/api/v1/resource", accept="*/*")
        assert _response_mode(req) == "json"

    def test_non_admin_path_with_html_accept_still_json(self) -> None:
        """Even Accept: text/html on a non-admin path returns JSON — the
        AdCP wire format is byte-stable."""
        from src.admin.content_negotiation import _response_mode

        req = _mock_request("/api/v1/resource", accept="text/html")
        assert _response_mode(req) == "json"


class TestErrorTemplatePinnedContract:
    """Golden render test for templates/_fastapi_error.html pinned variable
    contract {error_code, message, status_code}. Any drift here breaks
    handlers that reuse the template per foundation-modules.md §11.11.

    This template is the FastAPI-native companion to the legacy Flask
    templates/error.html (which keeps the {error, error_title, error_message,
    back_url} contract until L1+ port). Keeping them as separate files
    preserves L0's zero-visible-change thesis.
    """

    def test_template_renders_with_pinned_contract(self) -> None:
        from pathlib import Path

        from jinja2 import DictLoader, Environment

        project_root = Path(__file__).resolve().parents[3]
        template_path = project_root / "templates" / "_fastapi_error.html"
        base_path = project_root / "templates" / "base.html"

        assert template_path.exists(), "templates/_fastapi_error.html must be authored at L0-14"
        assert base_path.exists(), "templates/base.html must exist"

        # Isolated Jinja environment — stub base.html to avoid dragging in the
        # full template heritage (static, url_for, etc.). The contract assertion
        # is that rendering with exactly {error_code, message, status_code}
        # succeeds AND emits every pinned variable verbatim.
        src = template_path.read_text(encoding="utf-8")
        env = Environment(
            loader=DictLoader(
                {
                    "_fastapi_error.html": src,
                    "base.html": (
                        "<!doctype html><html><head><title>{% block title %}"
                        "{% endblock %}</title></head><body>{% block content %}"
                        "{% endblock %}</body></html>"
                    ),
                }
            ),
            autoescape=True,
        )
        rendered = env.get_template("_fastapi_error.html").render(
            error_code="NOT_FOUND", message="Tenant missing", status_code=404
        )

        # Contract: every pinned variable appears in rendered output. If this
        # fires, the template stopped using the {error_code, message,
        # status_code} triple.
        assert "NOT_FOUND" in rendered
        assert "Tenant missing" in rendered
        assert "404" in rendered

    def test_template_does_not_reference_legacy_variables(self) -> None:
        """The pinned contract is EXACTLY {error_code, message, status_code}.
        Legacy Flask-era names (error_title, error_message, back_url) MUST
        be absent from the FastAPI-native template — no invented extra
        variables per L0-14 constraint."""
        from pathlib import Path

        project_root = Path(__file__).resolve().parents[3]
        src = (project_root / "templates" / "_fastapi_error.html").read_text(encoding="utf-8")

        for legacy in ("error_title", "error_message", "back_url"):
            assert legacy not in src, f"Legacy variable {legacy!r} must not appear in pinned contract"
