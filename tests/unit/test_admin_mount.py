"""Unit tests for AdminWSGIMount ASGI middleware.

Covers the host-based admin dispatch added to replace the bundled-nginx
``server_name admin.${SALES_AGENT_DOMAIN}`` block. Path-based dispatch
(``/admin/*`` etc.) is exercised end-to-end by the integration suite.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from core.middleware.admin_mount import AdminWSGIMount


def _http_scope(
    *,
    host: str | None = None,
    apx_host: str | None = None,
    path: str = "/",
    query_string: bytes = b"",
    method: str = "GET",
) -> dict:
    """Build an ASGI HTTP scope with the given host headers and path."""
    headers: list[tuple[bytes, bytes]] = []
    if host is not None:
        headers.append((b"host", host.encode("latin-1")))
    if apx_host is not None:
        headers.append((b"apx-incoming-host", apx_host.encode("latin-1")))
    return {
        "type": "http",
        "method": method,
        "path": path,
        "raw_path": path.encode("latin-1"),
        "headers": headers,
        "root_path": "",
        "query_string": query_string,
    }


@pytest.mark.asyncio
class TestAdminWSGIMountHostDispatch:
    """Host-based dispatch routes admin.<domain>/* to Flask with root_path=/admin."""

    async def test_admin_host_routes_to_wsgi_with_admin_root_path(self):
        wsgi_app = AsyncMock()
        inner_app = AsyncMock()
        mount = AdminWSGIMount(inner_app, wsgi_app=wsgi_app)
        scope = _http_scope(host="admin.sales-agent.example.com", path="/")

        with patch("core.middleware.admin_mount.is_admin_domain", return_value=True):
            await mount(scope, AsyncMock(), AsyncMock())

        wsgi_app.assert_called_once()
        dispatched_scope = wsgi_app.call_args.args[0]
        assert dispatched_scope["root_path"] == "/admin"
        # Path is preserved — admin.host/foo serves /foo under SCRIPT_NAME=/admin
        assert dispatched_scope["path"] == "/"
        inner_app.assert_not_called()

    async def test_admin_host_preserves_non_root_path(self):
        wsgi_app = AsyncMock()
        inner_app = AsyncMock()
        mount = AdminWSGIMount(inner_app, wsgi_app=wsgi_app)
        scope = _http_scope(host="admin.sales-agent.example.com", path="/login")

        with patch("core.middleware.admin_mount.is_admin_domain", return_value=True):
            await mount(scope, AsyncMock(), AsyncMock())

        wsgi_app.assert_called_once()
        dispatched_scope = wsgi_app.call_args.args[0]
        assert dispatched_scope["path"] == "/login"
        assert dispatched_scope["root_path"] == "/admin"

    async def test_apx_incoming_host_takes_precedence_over_host(self):
        """Approximated proxy header beats raw Host (matches domain_routing.py)."""
        wsgi_app = AsyncMock()
        inner_app = AsyncMock()
        mount = AdminWSGIMount(inner_app, wsgi_app=wsgi_app)
        scope = _http_scope(
            host="backend.internal.fly.dev",
            apx_host="admin.sales-agent.example.com",
        )

        observed: list[str] = []

        def fake_is_admin(h: str) -> bool:
            observed.append(h)
            return h == "admin.sales-agent.example.com"

        with patch("core.middleware.admin_mount.is_admin_domain", side_effect=fake_is_admin):
            await mount(scope, AsyncMock(), AsyncMock())

        assert observed == ["admin.sales-agent.example.com"]
        wsgi_app.assert_called_once()

    async def test_non_admin_host_with_admin_path_uses_path_dispatch(self):
        """Regression: tenant.host/admin/foo still strips /admin and sets root_path."""
        wsgi_app = AsyncMock()
        inner_app = AsyncMock()
        mount = AdminWSGIMount(inner_app, wsgi_app=wsgi_app)
        scope = _http_scope(host="acme.sales-agent.example.com", path="/admin/login")

        with patch("core.middleware.admin_mount.is_admin_domain", return_value=False):
            await mount(scope, AsyncMock(), AsyncMock())

        wsgi_app.assert_called_once()
        dispatched_scope = wsgi_app.call_args.args[0]
        assert dispatched_scope["path"] == "/login"
        assert dispatched_scope["root_path"] == "/admin"
        inner_app.assert_not_called()

    async def test_non_admin_host_root_path_falls_through_to_inner(self):
        """A2A landing page at tenant.host/ still reaches the inner app."""
        wsgi_app = AsyncMock()
        inner_app = AsyncMock()
        mount = AdminWSGIMount(inner_app, wsgi_app=wsgi_app)
        scope = _http_scope(host="acme.sales-agent.example.com", path="/")

        with patch("core.middleware.admin_mount.is_admin_domain", return_value=False):
            await mount(scope, AsyncMock(), AsyncMock())

        inner_app.assert_called_once()
        wsgi_app.assert_not_called()

    async def test_missing_host_header_does_not_match_admin(self):
        wsgi_app = AsyncMock()
        inner_app = AsyncMock()
        mount = AdminWSGIMount(inner_app, wsgi_app=wsgi_app)
        scope = _http_scope(path="/")

        # is_admin_domain should never be called with a falsy host — guard
        # short-circuits before delegating.
        with patch("core.middleware.admin_mount.is_admin_domain") as mock_is_admin:
            await mount(scope, AsyncMock(), AsyncMock())
            mock_is_admin.assert_not_called()

        inner_app.assert_called_once()
        wsgi_app.assert_not_called()

    async def test_lifespan_scope_passes_through(self):
        """Non-HTTP scopes (lifespan, websocket) bypass admin dispatch."""
        wsgi_app = AsyncMock()
        inner_app = AsyncMock()
        mount = AdminWSGIMount(inner_app, wsgi_app=wsgi_app)
        scope = {"type": "lifespan"}

        await mount(scope, AsyncMock(), AsyncMock())

        inner_app.assert_called_once()
        wsgi_app.assert_not_called()


@pytest.mark.asyncio
class TestAdminWSGIMountApexRedirect:
    """Apex ``sales-agent.example.com/`` → 302 ``/signup``.

    Replaces the bundled-nginx ``server_name ${SALES_AGENT_DOMAIN};
    location = /`` block from the multi-tenant config (PR #25 removed it).
    """

    async def test_apex_root_redirects_to_signup(self):
        wsgi_app = AsyncMock()
        inner_app = AsyncMock()
        mount = AdminWSGIMount(inner_app, wsgi_app=wsgi_app)
        scope = _http_scope(host="sales-agent.example.com", path="/")
        send = AsyncMock()

        with patch(
            "core.middleware.admin_mount.get_sales_agent_domain",
            return_value="sales-agent.example.com",
        ):
            with patch("core.middleware.admin_mount.is_admin_domain", return_value=False):
                await mount(scope, AsyncMock(), send)

        # Two send() calls: response.start + response.body
        assert send.call_count == 2
        start_msg = send.call_args_list[0].args[0]
        assert start_msg["type"] == "http.response.start"
        assert start_msg["status"] == 302
        location = dict(start_msg["headers"]).get(b"location")
        assert location == b"/signup"
        wsgi_app.assert_not_called()
        inner_app.assert_not_called()

    async def test_apex_redirect_strips_port_from_host(self):
        """``sales-agent.example.com:8080`` still matches the apex domain."""
        wsgi_app = AsyncMock()
        inner_app = AsyncMock()
        mount = AdminWSGIMount(inner_app, wsgi_app=wsgi_app)
        scope = _http_scope(host="sales-agent.example.com:8080", path="/")
        send = AsyncMock()

        with patch(
            "core.middleware.admin_mount.get_sales_agent_domain",
            return_value="sales-agent.example.com",
        ):
            with patch("core.middleware.admin_mount.is_admin_domain", return_value=False):
                await mount(scope, AsyncMock(), send)

        assert send.call_count == 2
        assert send.call_args_list[0].args[0]["status"] == 302

    async def test_apex_non_root_path_falls_through(self):
        """``sales-agent.example.com/foo`` is not redirected (only bare /)."""
        wsgi_app = AsyncMock()
        inner_app = AsyncMock()
        mount = AdminWSGIMount(inner_app, wsgi_app=wsgi_app)
        scope = _http_scope(host="sales-agent.example.com", path="/foo")
        send = AsyncMock()

        with patch(
            "core.middleware.admin_mount.get_sales_agent_domain",
            return_value="sales-agent.example.com",
        ):
            with patch("core.middleware.admin_mount.is_admin_domain", return_value=False):
                await mount(scope, AsyncMock(), send)

        # No redirect emitted; falls through to inner A2A app.
        send.assert_not_called()
        inner_app.assert_called_once()
        wsgi_app.assert_not_called()

    async def test_subdomain_host_root_does_not_redirect(self):
        """``acme.sales-agent.example.com/`` is the tenant landing — must not redirect."""
        wsgi_app = AsyncMock()
        inner_app = AsyncMock()
        mount = AdminWSGIMount(inner_app, wsgi_app=wsgi_app)
        scope = _http_scope(host="acme.sales-agent.example.com", path="/")
        send = AsyncMock()

        with patch(
            "core.middleware.admin_mount.get_sales_agent_domain",
            return_value="sales-agent.example.com",
        ):
            with patch("core.middleware.admin_mount.is_admin_domain", return_value=False):
                await mount(scope, AsyncMock(), send)

        send.assert_not_called()
        inner_app.assert_called_once()
        wsgi_app.assert_not_called()

    async def test_apex_with_unset_domain_does_not_redirect(self):
        """Single-tenant / dev (no SALES_AGENT_DOMAIN) skips the apex branch."""
        wsgi_app = AsyncMock()
        inner_app = AsyncMock()
        mount = AdminWSGIMount(inner_app, wsgi_app=wsgi_app)
        scope = _http_scope(host="localhost:8080", path="/")
        send = AsyncMock()

        with patch("core.middleware.admin_mount.get_sales_agent_domain", return_value=None):
            with patch("core.middleware.admin_mount.is_admin_domain", return_value=False):
                await mount(scope, AsyncMock(), send)

        send.assert_not_called()
        inner_app.assert_called_once()

    async def test_apx_incoming_host_drives_apex_match(self):
        """Approximated header beats raw Host for apex detection too."""
        wsgi_app = AsyncMock()
        inner_app = AsyncMock()
        mount = AdminWSGIMount(inner_app, wsgi_app=wsgi_app)
        scope = _http_scope(
            host="backend.internal.fly.dev",
            apx_host="sales-agent.example.com",
            path="/",
        )
        send = AsyncMock()

        with patch(
            "core.middleware.admin_mount.get_sales_agent_domain",
            return_value="sales-agent.example.com",
        ):
            with patch("core.middleware.admin_mount.is_admin_domain", return_value=False):
                await mount(scope, AsyncMock(), send)

        assert send.call_count == 2
        assert send.call_args_list[0].args[0]["status"] == 302

    async def test_apex_match_is_case_insensitive(self):
        """Mixed-case Host headers (RFC 3986) still match the apex."""
        wsgi_app = AsyncMock()
        inner_app = AsyncMock()
        mount = AdminWSGIMount(inner_app, wsgi_app=wsgi_app)
        scope = _http_scope(host="Sales-Agent.Example.COM", path="/")
        send = AsyncMock()

        with patch(
            "core.middleware.admin_mount.get_sales_agent_domain",
            return_value="sales-agent.example.com",
        ):
            with patch("core.middleware.admin_mount.is_admin_domain", return_value=False):
                await mount(scope, AsyncMock(), send)

        assert send.call_count == 2
        assert send.call_args_list[0].args[0]["status"] == 302

    async def test_apex_redirect_preserves_query_string(self):
        """Marketing attribution survives the apex bounce."""
        wsgi_app = AsyncMock()
        inner_app = AsyncMock()
        mount = AdminWSGIMount(inner_app, wsgi_app=wsgi_app)
        scope = _http_scope(
            host="sales-agent.example.com",
            path="/",
            query_string=b"utm_source=google&utm_campaign=launch",
        )
        send = AsyncMock()

        with patch(
            "core.middleware.admin_mount.get_sales_agent_domain",
            return_value="sales-agent.example.com",
        ):
            with patch("core.middleware.admin_mount.is_admin_domain", return_value=False):
                await mount(scope, AsyncMock(), send)

        location = dict(send.call_args_list[0].args[0]["headers"]).get(b"location")
        assert location == b"/signup?utm_source=google&utm_campaign=launch"

    async def test_apex_redirect_sets_cache_control_no_store(self):
        """Redirect target may change — block intermediary caching."""
        wsgi_app = AsyncMock()
        inner_app = AsyncMock()
        mount = AdminWSGIMount(inner_app, wsgi_app=wsgi_app)
        scope = _http_scope(host="sales-agent.example.com", path="/")
        send = AsyncMock()

        with patch(
            "core.middleware.admin_mount.get_sales_agent_domain",
            return_value="sales-agent.example.com",
        ):
            with patch("core.middleware.admin_mount.is_admin_domain", return_value=False):
                await mount(scope, AsyncMock(), send)

        cache_control = dict(send.call_args_list[0].args[0]["headers"]).get(b"cache-control")
        assert cache_control == b"no-store"


@pytest.mark.asyncio
class TestAdminWSGIMountRobotsTxt:
    """``/robots.txt`` is served as a public ``Disallow: /`` response.

    Without the short-circuit, the request falls through to the inner
    A2A surface, which is wrapped in bearer-token auth and 401s every
    crawler — flooding production logs with 401 access lines.
    """

    async def test_robots_txt_served_directly(self):
        wsgi_app = AsyncMock()
        inner_app = AsyncMock()
        mount = AdminWSGIMount(inner_app, wsgi_app=wsgi_app)
        scope = _http_scope(host="api.example.com", path="/robots.txt")
        send = AsyncMock()

        await mount(scope, AsyncMock(), send)

        assert send.call_count == 2
        start = send.call_args_list[0].args[0]
        body = send.call_args_list[1].args[0]
        assert start["type"] == "http.response.start"
        assert start["status"] == 200
        headers = dict(start["headers"])
        assert headers.get(b"content-type") == b"text/plain; charset=utf-8"
        assert b"max-age=86400" in headers.get(b"cache-control", b"")
        assert body["type"] == "http.response.body"
        assert body["body"] == b"User-agent: *\nDisallow: /\n"
        # Neither downstream surface should see the request.
        wsgi_app.assert_not_called()
        inner_app.assert_not_called()

    async def test_robots_txt_head_returns_headers_only(self):
        """HEAD ``/robots.txt`` returns 200 with empty body per RFC 9110."""
        wsgi_app = AsyncMock()
        inner_app = AsyncMock()
        mount = AdminWSGIMount(inner_app, wsgi_app=wsgi_app)
        scope = _http_scope(host="api.example.com", path="/robots.txt", method="HEAD")
        send = AsyncMock()

        await mount(scope, AsyncMock(), send)

        assert send.call_args_list[0].args[0]["status"] == 200
        # Content-Length still reflects the would-be body so caches behave.
        cl = dict(send.call_args_list[0].args[0]["headers"]).get(b"content-length")
        assert cl == str(len(b"User-agent: *\nDisallow: /\n")).encode("ascii")
        assert send.call_args_list[1].args[0]["body"] == b""

    async def test_robots_txt_post_falls_through(self):
        """POST ``/robots.txt`` is not a crawler probe — let the inner app
        respond however it would (today: 401/405). The short-circuit only
        covers safe methods that crawlers actually use."""
        wsgi_app = AsyncMock()
        inner_app = AsyncMock()
        mount = AdminWSGIMount(inner_app, wsgi_app=wsgi_app)
        scope = _http_scope(host="api.example.com", path="/robots.txt", method="POST")
        send = AsyncMock()

        receive = AsyncMock()
        await mount(scope, receive, send)

        send.assert_not_called()
        inner_app.assert_called_once_with(scope, receive, send)

    async def test_robots_txt_short_circuits_before_a2a(self):
        """The bug we're fixing: requests on the API host (not the admin
        host) must NOT reach the inner A2A app, where bearer auth would
        401 the crawler. Patches ``is_admin_domain`` to False to simulate
        the production API host where the spam originated."""
        wsgi_app = AsyncMock()
        inner_app = AsyncMock()
        mount = AdminWSGIMount(inner_app, wsgi_app=wsgi_app)
        scope = _http_scope(host="api.example.com", path="/robots.txt")
        send = AsyncMock()

        with patch("core.middleware.admin_mount.is_admin_domain", return_value=False):
            await mount(scope, AsyncMock(), send)

        inner_app.assert_not_called()
        wsgi_app.assert_not_called()
        assert send.call_args_list[0].args[0]["status"] == 200
