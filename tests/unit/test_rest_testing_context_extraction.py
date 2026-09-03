"""Regression: REST/MCP/A2A testing-context extraction and list mock clock.

Covers REST from_headers parity, list reference_today + simulate under mock_time,
RestE2EDispatcher / apply_testing_hook_headers harness wiring, and
resolve_identity_from_context reading mock_time from resolved headers.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from adcp.types import MediaBuyStatus

from src.core.auth_context import AuthContext, _require_auth_dep, _resolve_auth_dep
from src.core.schemas import GetMediaBuysRequest
from src.core.testing_hooks import AdCPTestContext
from src.core.tools._media_buy_status import resolve_canonical_status
from src.core.tools.media_buy_list import _compute_status, _get_media_buys_impl
from tests.factories.principal import PrincipalFactory


class TestRestTestingContextExtraction:
    """REST ``_resolve_auth_dep`` / ``_require_auth_dep`` pass testing_context."""

    def test_resolve_auth_passes_mock_time_from_headers(self):
        auth_ctx = AuthContext(
            auth_token="test-token",
            headers={
                "x-adcp-auth": "test-token",
                "x-mock-time": "2026-03-15T12:00:00Z",
            },
        )
        mock_identity = PrincipalFactory.make_identity(protocol="rest")
        expected_tc = AdCPTestContext.from_headers(dict(auth_ctx.headers))
        with patch("src.core.resolved_identity.resolve_identity", return_value=mock_identity) as mock_resolve:
            _resolve_auth_dep(auth_ctx)

        mock_resolve.assert_called_once_with(
            headers=dict(auth_ctx.headers),
            auth_token="test-token",
            require_valid_token=False,
            protocol="rest",
            testing_context=expected_tc,
        )
        assert expected_tc is not None
        assert expected_tc.mock_time == datetime(2026, 3, 15, 12, 0, 0, tzinfo=UTC)

    def test_require_auth_passes_dry_run_from_headers(self):
        auth_ctx = AuthContext(
            auth_token="test-token",
            headers={
                "x-adcp-auth": "test-token",
                "x-dry-run": "true",
            },
        )
        mock_identity = PrincipalFactory.make_identity(protocol="rest")
        expected_tc = AdCPTestContext.from_headers(dict(auth_ctx.headers))
        with patch("src.core.resolved_identity.resolve_identity", return_value=mock_identity) as mock_resolve:
            _require_auth_dep(auth_ctx)

        mock_resolve.assert_called_once_with(
            headers=dict(auth_ctx.headers),
            auth_token="test-token",
            require_valid_token=True,
            protocol="rest",
            testing_context=expected_tc,
        )
        assert expected_tc is not None
        assert expected_tc.dry_run is True

    def test_resolve_auth_passes_none_without_test_headers(self):
        auth_ctx = AuthContext(
            auth_token="test-token",
            headers={"x-adcp-auth": "test-token"},
        )
        mock_identity = PrincipalFactory.make_identity(protocol="rest")
        with patch("src.core.resolved_identity.resolve_identity", return_value=mock_identity) as mock_resolve:
            _resolve_auth_dep(auth_ctx)

        mock_resolve.assert_called_once_with(
            headers=dict(auth_ctx.headers),
            auth_token="test-token",
            require_valid_token=False,
            protocol="rest",
            testing_context=None,
        )


class TestMediaBuyListHonorsMockTime:
    """``_get_media_buys_impl`` uses testing_context.mock_time for ``today`` + simulate."""

    def test_list_today_uses_mock_time_date(self):
        mock_time = datetime(2026, 3, 15, 12, 0, 0, tzinfo=UTC)
        identity = PrincipalFactory.make_identity(
            protocol="rest",
            testing_context=AdCPTestContext(mock_time=mock_time),
        )
        captured: dict[str, object] = {}

        def _capture_fetch(_req, _principal_id, _uow, today, _row_advisories, *, simulate=False):
            captured["today"] = today
            captured["simulate"] = simulate
            return []

        with (
            patch("src.core.tools.media_buy_list.MediaBuyUoW") as m_uow,
            patch("src.core.tools.media_buy_list.get_principal_object", return_value=MagicMock()),
            patch("src.core.tools.media_buy_list.require_tenant", return_value={"tenant_id": "test_tenant"}),
            patch("src.core.tools.media_buy_list._fetch_target_media_buys", side_effect=_capture_fetch),
            patch("src.core.tools.media_buy_list._fetch_creative_approvals", return_value={}),
            patch("src.core.tools.media_buy_list._fetch_packages", return_value={}),
        ):
            uow = MagicMock()
            uow.media_buys = MagicMock()
            uow.session = MagicMock()
            m_uow.return_value.__enter__.return_value = uow
            m_uow.return_value.__exit__.return_value = False

            _get_media_buys_impl(GetMediaBuysRequest(), identity=identity)

        assert captured["today"] == date(2026, 3, 15)
        assert captured["simulate"] is False

    def test_list_pending_creatives_past_flight_keeps_simulate_false_under_mock_time(self):
        """#1830: mock_time moves today only; persisted status stays unrefined on list."""
        buy = SimpleNamespace(
            status="pending_creatives",
            start_date=date(2025, 1, 1),
            end_date=date(2025, 3, 31),
            start_time=None,
            end_time=None,
            is_paused=False,
        )
        past = date(2025, 6, 1)
        assert resolve_canonical_status(buy, past, simulate=True) == "completed"
        assert resolve_canonical_status(buy, past, simulate=False) == "pending_creatives"
        assert _compute_status(buy, past, simulate=True) is MediaBuyStatus.completed
        assert _compute_status(buy, past, simulate=False) is MediaBuyStatus.pending_creatives


class TestRestE2EDispatcherMockTimeHeader:
    """RestE2EDispatcher forwards X-Mock-Time from identity / env."""

    def test_dispatcher_sets_x_mock_time_header(self):
        from tests.harness.dispatchers import RestE2EDispatcher

        mock_time = datetime(2026, 3, 15, 12, 0, 0, tzinfo=UTC)
        identity = PrincipalFactory.make_identity(
            protocol="rest",
            testing_context=AdCPTestContext(mock_time=mock_time, dry_run=True),
            auth_token="tok",
        )
        env = SimpleNamespace(
            e2e_config=SimpleNamespace(base_url="http://example.test"),
            REST_ENDPOINT="/api/v1/media-buys/query",
            REST_METHOD="post",
            build_rest_body=lambda **_kw: {},
            parse_rest_response=lambda data: data,
            parse_rest_error=lambda *_a: Exception("err"),
            mock_time=None,
        )
        captured_headers: dict[str, str] = {}

        class _FakeResponse:
            status_code = 200
            headers = {"content-type": "application/json"}

            def json(self):
                return {"media_buys": []}

        class _FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def post(self, endpoint, json=None, headers=None):  # noqa: A002
                captured_headers.update(headers or {})
                return _FakeResponse()

        with patch("httpx.Client", _FakeClient):
            RestE2EDispatcher().dispatch(env, identity=identity)

        assert captured_headers.get("x-mock-time") == "2026-03-15T12:00:00Z"
        assert captured_headers.get("x-dry-run") == "true"


class TestApplyTestingHookHeaders:
    """Shared harness helper used by e2e_rest + real-token A2A/MCP."""

    def test_forwards_mock_time_and_dry_run_from_identity(self):
        from tests.harness.dispatchers import apply_testing_hook_headers

        mock_time = datetime(2026, 3, 15, 12, 0, 0, tzinfo=UTC)
        identity = PrincipalFactory.make_identity(
            protocol="a2a",
            testing_context=AdCPTestContext(mock_time=mock_time, dry_run=True),
        )
        headers: dict[str, str] = {"x-adcp-auth": "tok"}
        apply_testing_hook_headers(headers, identity)
        assert headers["x-mock-time"] == "2026-03-15T12:00:00Z"
        assert headers["x-dry-run"] == "true"

    def test_falls_back_to_env_mock_time(self):
        from tests.harness.dispatchers import apply_testing_hook_headers

        mock_time = datetime(2026, 3, 14, 12, 0, 0, tzinfo=UTC)
        identity = PrincipalFactory.make_identity(
            protocol="a2a",
            testing_context=AdCPTestContext(mock_time=None),
        )
        headers: dict[str, str] = {}
        apply_testing_hook_headers(headers, identity, fallback_mock_time=mock_time)
        assert headers["x-mock-time"] == "2026-03-14T12:00:00Z"


class TestResolveNowSharedClock:
    """resolve_now is the single mock/wall clock for list + hooks + delivery mock branch."""

    def test_resolve_now_prefers_mock_time(self):
        from src.core.testing_hooks import resolve_now

        mock_time = datetime(2026, 3, 15, 12, 0, 0, tzinfo=UTC)
        assert resolve_now(AdCPTestContext(mock_time=mock_time)) == mock_time

    def test_resolve_now_wall_clock_when_no_mock(self):
        from src.core.testing_hooks import resolve_now

        before = datetime.now(UTC)
        got = resolve_now(AdCPTestContext(dry_run=True))
        after = datetime.now(UTC)
        assert before <= got <= after

    def test_resolve_clock_mock_time_is_clock_only(self):
        from src.core.testing_hooks import resolve_clock, resolve_now

        mock_time = datetime(2026, 3, 15, 12, 0, 0, tzinfo=UTC)
        tc = AdCPTestContext(mock_time=mock_time)
        clock, simulate = resolve_clock(tc)
        assert clock == resolve_now(tc) == mock_time
        assert simulate is False
        assert clock.date() == date(2026, 3, 15)


class TestResolveIdentityFromContextUsesHeaders:
    """MCP Client path: testing_context from resolved headers, not a re-fetch."""

    def test_from_headers_even_when_testing_hooks_get_http_headers_empty(self):
        from src.core.transport_helpers import resolve_identity_from_context

        headers = {
            "x-adcp-auth": "tok",
            "x-mock-time": "2026-03-15T12:00:00Z",
        }
        expected_tc = AdCPTestContext.from_headers(headers)
        mock_identity = PrincipalFactory.make_identity(protocol="mcp")
        with (
            patch("src.core.transport_helpers.get_http_headers", return_value=headers),
            patch("src.core.testing_hooks.get_http_headers", return_value={}),
            patch("src.core.transport_helpers.resolve_identity", return_value=mock_identity) as mock_resolve,
        ):
            resolve_identity_from_context(MagicMock(), require_valid_token=False, protocol="mcp")

        mock_resolve.assert_called_once_with(
            headers=headers,
            require_valid_token=False,
            protocol="mcp",
            testing_context=expected_tc,
        )
        assert expected_tc is not None
        assert expected_tc.mock_time == datetime(2026, 3, 15, 12, 0, 0, tzinfo=UTC)


class TestHarnessRealTokenAppliesTestingHookHeaders:
    """Altitude-correct: real-token A2A/MCP preambles call apply_testing_hook_headers."""

    def test_a2a_real_token_preamble_calls_apply_testing_hook_headers(self):
        from a2a.types import Task, TaskState, TaskStatus

        from src.core.auth_context import AUTH_CONTEXT_STATE_KEY
        from src.core.schemas import GetMediaBuysResponse
        from tests.harness.dispatchers import apply_testing_hook_headers
        from tests.harness.media_buy_list import MediaBuyListEnv

        mock_time = datetime(2026, 3, 15, 12, 0, 0, tzinfo=UTC)
        env = MediaBuyListEnv(principal_id="p1", tenant_id="t1")
        identity = PrincipalFactory.make_identity(
            protocol="a2a",
            auth_token="real-tok",
            tenant_id="t1",
            principal_id="p1",
            testing_context=AdCPTestContext(mock_time=mock_time),
        )
        captured: dict[str, object] = {}

        async def _fake_on_message_send(_params, server_context):
            auth = server_context.state[AUTH_CONTEXT_STATE_KEY]
            captured["headers"] = dict(auth.headers)
            return Task(
                id="task-1",
                contextId="ctx-1",
                status=TaskStatus(state=TaskState.completed),
                artifacts=[],
            )

        with (
            patch(
                "tests.harness.dispatchers.apply_testing_hook_headers",
                wraps=apply_testing_hook_headers,
            ) as spy,
            patch("src.a2a_server.adcp_a2a_server.AdCPRequestHandler") as handler_cls,
            patch.object(env, "_ensure_tenant_for_audit"),
            patch.object(env, "_commit_factory_data"),
        ):
            handler_cls.return_value.on_message_send = _fake_on_message_send
            try:
                env._run_a2a_handler("get_media_buys", GetMediaBuysResponse, identity=identity)
            except Exception:
                # Empty artifacts may fail response parse — preamble is enough.
                pass

        assert spy.called, "real-token A2A path must call apply_testing_hook_headers"
        headers = captured.get("headers") or {}
        assert headers.get("x-mock-time") == "2026-03-15T12:00:00Z", headers

    def test_mcp_real_token_preamble_calls_apply_testing_hook_headers(self):
        from src.core.schemas import GetMediaBuysResponse
        from tests.harness.dispatchers import apply_testing_hook_headers
        from tests.harness.media_buy_list import MediaBuyListEnv

        mock_time = datetime(2026, 3, 15, 12, 0, 0, tzinfo=UTC)
        env = MediaBuyListEnv(principal_id="p1", tenant_id="t1")
        identity = PrincipalFactory.make_identity(
            protocol="mcp",
            auth_token="real-tok",
            tenant_id="t1",
            principal_id="p1",
            testing_context=AdCPTestContext(mock_time=mock_time),
        )

        captured: dict[str, object] = {}

        class _FakeToolResult:
            structured_content = {"media_buys": []}

        class _FakeClient:
            def __init__(self, *args, **kwargs):
                self._kwargs = kwargs

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def call_tool(self, name, arguments):
                return _FakeToolResult()

        with (
            patch(
                "tests.harness.dispatchers.apply_testing_hook_headers",
                wraps=apply_testing_hook_headers,
            ) as spy,
            patch("fastmcp.Client", _FakeClient),
            patch.object(env, "_commit_factory_data"),
        ):
            try:
                env._run_mcp_client("get_media_buys", GetMediaBuysResponse, identity=identity)
            except Exception:
                pass

        assert spy.called, "real-token MCP path must call apply_testing_hook_headers"
        call = spy.call_args
        headers = call.args[0] if call.args else call.kwargs.get("headers") or {}
        assert headers.get("x-mock-time") == "2026-03-15T12:00:00Z", headers
