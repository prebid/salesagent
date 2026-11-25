"""Meta-tests for AdCPTestClient — the transport-generic dispatch core (the transport-generic client).

``AdCPTestClient.call(tool, payload, transport)`` bypasses every env's
hand-written ``call_a2a``/``call_mcp``/``build_rest_body``/``parse_rest_response``
quartet (design doc §1) and dispatches purely from the derived
``ADDRESS_TABLE`` + the shared ``_run_mcp_client``/``_run_a2a_handler``/
``get_rest_client`` primitives on ``BaseTestEnv``/``IntegrationEnv``.

These tests deliberately use envs that do NOT implement ``call_a2a``/
``call_mcp`` (e.g. ``tests.harness.product_unit.ProductEnv``) to prove the
client does not need those per-env methods at all — the whole point of the
design (§1 "MediaBuyDualEnv is the reductio").
"""

from __future__ import annotations

import json

import pytest

from tests.harness._base import BaseTestEnv
from tests.harness.address_table import NoAddressForTransport, ToolAddress
from tests.harness.client import AdCPTestClient, _wrap_rest, unwrap_rest_response
from tests.harness.transport import Transport
from tests.helpers.credentials import credential_headers


def _origin_env(origin, *, a2a_over_http=False, a2a_skill=None):
    from tests.harness.transport import E2EConfig

    env = BaseTestEnv(e2e_config=E2EConfig(base_url=origin.base_url, postgres_url="postgresql://unused/db"))
    env.A2A_SKILL = a2a_skill
    env._signed_dispatch = False
    if a2a_over_http:
        env._run_a2a_handler = env._run_a2a_over_http
    return env


class TestClientMcpDispatchNoDb:
    """MCP dispatch through the generic client — no per-env call_mcp needed."""

    def test_get_products_via_mcp_succeeds(self):
        from tests.harness.product_unit import ProductEnv

        with ProductEnv() as env:
            env.add_product(product_id="prod_001", name="Display Ad")
            client = AdCPTestClient(env)

            result = client.call("get_products", {"brief": "display ads"}, Transport.MCP)

        assert result.is_success, result.error
        assert result.envelope["transport"] == "mcp"
        assert result.wire_response is not None
        # payload is the pinned GetProductsResponse model, not the raw dict —
        # attribute access, not subscripting.
        product_ids = [p.product_id for p in result.payload.products]
        assert product_ids == ["prod_001"]


class TestClientA2ADispatchNoDb:
    """A2A dispatch through the generic client — no per-env call_a2a needed."""

    def test_get_products_via_a2a_succeeds(self):
        from tests.harness.product_unit import ProductEnv

        with ProductEnv() as env:
            env.add_product(product_id="prod_001", name="Display Ad")
            client = AdCPTestClient(env)

            result = client.call("get_products", {"brief": "display ads"}, Transport.A2A)

        assert result.is_success, result.error
        assert result.envelope["transport"] == "a2a"
        # payload is the pinned GetProductsResponse model, not the raw dict —
        # attribute access, not subscripting.
        product_ids = [p.product_id for p in result.payload.products]
        assert product_ids == ["prod_001"]


class TestClientNoAddressForTransport:
    """Tools that don't exist on a transport raise, they don't KeyError or hang."""

    def test_a2a_only_skill_on_rest_raises_no_address(self):
        class _UnitEnv(BaseTestEnv):
            pass

        with _UnitEnv() as env:
            client = AdCPTestClient(env)
            with pytest.raises(NoAddressForTransport):
                client.call("approve_creative", {}, Transport.REST)


class TestClientRestWrapPathParamPeeling:
    """Pure-function coverage of the path-param generalization (design doc §4) —
    the rule that replaces MediaBuyDualEnv's hand-coded single-route version."""

    def test_peels_path_param_into_url_and_out_of_body(self):
        address = ToolAddress(
            Transport.REST, name="update_media_buy", path_template="/api/v1/media-buys/{media_buy_id}", method="put"
        )
        wrapped = _wrap_rest(address, {"media_buy_id": "mb_123", "paused": True})

        assert wrapped["url"] == "/api/v1/media-buys/mb_123"
        assert wrapped["body"] == {"paused": True}

    def test_no_path_params_leaves_body_and_url_untouched(self):
        address = ToolAddress(Transport.REST, name="get_products", path_template="/api/v1/products", method="post")
        wrapped = _wrap_rest(address, {"brief": "video ads"})

        assert wrapped["url"] == "/api/v1/products"
        assert wrapped["body"] == {"brief": "video ads"}

    def test_multiple_path_params_all_peeled(self):
        address = ToolAddress(Transport.REST, name="fake_tool", path_template="/api/v1/a/{a_id}/b/{b_id}", method="put")
        wrapped = _wrap_rest(address, {"a_id": "1", "b_id": "2", "extra": "kept"})

        assert wrapped["url"] == "/api/v1/a/1/b/2"
        assert wrapped["body"] == {"extra": "kept"}


class TestUnwrapRestResponse:
    """``unwrap_rest_response`` — the one REST unwrap
    shared by ``RestDispatcher``, ``RestE2EDispatcher``
    (``tests/harness/dispatchers.py``) and the generic client's
    ``_unwrap_rest``. Before this consolidation, the generic client core's
    REST unwrap aliased ``payload`` and ``wire_response`` to the SAME dict
    object, dropping the #1417 pristine-wire deepcopy rule."""

    class _FakeRestResponse:
        def __init__(self, status_code: int, body: dict) -> None:
            self.status_code = status_code
            self.headers = {"content-type": "application/json"}
            self._body = body

        def json(self):
            return self._body

    def test_payload_and_wire_response_do_not_alias(self):
        raw = self._FakeRestResponse(200, {"products": [{"product_id": "prod_001"}]})

        class _UnitEnv(BaseTestEnv):
            pass

        with _UnitEnv() as env:
            result = unwrap_rest_response(env, raw, Transport.REST, lambda body: body)

        assert result.payload is not result.wire_response
        result.payload["products"].append({"product_id": "injected"})
        assert result.wire_response["products"] == [{"product_id": "prod_001"}]

    def test_tag_derived_from_transport_enum_not_a_literal(self):
        raw = self._FakeRestResponse(200, {})

        class _UnitEnv(BaseTestEnv):
            pass

        with _UnitEnv() as env:
            rest_result = unwrap_rest_response(env, raw, Transport.REST, lambda body: body)
            e2e_result = unwrap_rest_response(env, raw, Transport.E2E_REST, lambda body: body)

        assert rest_result.envelope["transport"] == "rest"
        assert e2e_result.envelope["transport"] == "e2e_rest"

    def test_typed_parse_policy_gets_its_own_deepcopy(self):
        """Dispatchers pass env.parse_rest_response (a typed parser) as
        parse_policy — it must receive a deep copy too, not the dict backing
        wire_response, so an in-place-mutating parser (e.g.
        _parse_update_rest_response popping "status", #1417) never corrupts
        the stashed wire capture."""
        raw = self._FakeRestResponse(200, {"status": "accepted", "media_buy_id": "mb_1"})

        def _mutating_parser(data: dict) -> dict:
            data.pop("status", None)
            return data

        class _UnitEnv(BaseTestEnv):
            pass

        with _UnitEnv() as env:
            result = unwrap_rest_response(env, raw, Transport.REST, _mutating_parser)

        assert result.payload == {"media_buy_id": "mb_1"}
        assert result.wire_response == {"status": "accepted", "media_buy_id": "mb_1"}


class TestClientE2eRestDelivery:
    """E2E_REST DELIVER (``_deliver_e2e_rest``, the wire-grading work) — real
    HTTP through nginx to a live Docker stack. Mocks ``httpx.Client`` so
    coverage does not require a live server; genuine e2e-with-real-server
    verification happens in ``tests/e2e/``."""

    def _make_env_with_e2e_config(self):
        from tests.harness._base import BaseTestEnv
        from tests.harness.transport import E2EConfig

        class _UnitEnv(BaseTestEnv):
            pass

        return _UnitEnv(e2e_config=E2EConfig(base_url="http://e2e-stack.test", postgres_url="postgresql://x/y"))

    def test_e2e_rest_delivery_sends_real_http_request(self, monkeypatch):
        import httpx

        captured = {}

        class _FakeResponse:
            status_code = 200
            headers = {"content-type": "application/json"}

            def json(self):
                return {"products": []}

        class _FakeClient:
            def __init__(self, *, base_url, timeout):
                captured["base_url"] = base_url
                captured["timeout"] = timeout

            def __enter__(self):
                return self

            def __exit__(self, *exc_info):
                return False

            def post(self, url, *, json, headers):
                captured["url"] = url
                captured["json"] = json
                captured["headers"] = headers
                return _FakeResponse()

        monkeypatch.setattr(httpx, "Client", _FakeClient)

        credential = credential_headers(token="tok_abc", tenant="t1")

        with self._make_env_with_e2e_config() as env:
            client = AdCPTestClient(env)
            result = client.call("get_products", {"brief": "video ads"}, Transport.E2E_REST, credential=credential)

        assert result.is_success, result.error
        assert captured["base_url"] == "http://e2e-stack.test"
        assert captured["url"] == "/api/v1/products"
        assert captured["json"] == {"brief": "video ads"}
        assert captured["headers"]["Authorization"] == "Bearer tok_abc"
        assert captured["headers"]["x-adcp-tenant"] == "t1"

    def test_e2e_rest_delivery_sends_exactly_the_shared_producers_headers(self, monkeypatch):
        """``_deliver_e2e_rest`` must emit the header set ``credential()`` produces, and
        nothing else.

        ``RestE2EDispatcher`` used to build these inline until commit 4363757dc routed
        delivery through the shared producer. e2e_rest is a live caller (real HTTP to the
        Docker stack), so a regression here silently drops a header a real server request
        depends on -- or adds one no seller reads. The set is Authorization +
        x-adcp-tenant plus the content type: the third header this case used to check,
        x-dry-run, is gone with the testing-hook channel (commit a1b79d22d), and asserting
        the dict WHOLE is what keeps it gone."""
        import httpx

        captured = {}

        class _FakeResponse:
            status_code = 200
            headers = {"content-type": "application/json"}

            def json(self):
                return {"products": []}

        class _FakeClient:
            def __init__(self, *, base_url, timeout):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *exc_info):
                return False

            def post(self, url, *, json, headers):
                captured["headers"] = headers
                return _FakeResponse()

        monkeypatch.setattr(httpx, "Client", _FakeClient)

        from tests.harness.transport import E2EConfig

        class _UnitEnv(BaseTestEnv):
            pass

        env = _UnitEnv(
            principal_id="p1",
            tenant_id="t1",
            e2e_config=E2EConfig(base_url="http://e2e-stack.test", postgres_url="postgresql://x/y"),
        )
        with env:
            client = AdCPTestClient(env)
            result = client.call("get_products", {"brief": "video ads"}, Transport.E2E_REST)

        assert result.is_success, result.error
        assert captured["headers"] == {
            "Content-Type": "application/json",
            "Authorization": env.credential()["Authorization"],
            "x-adcp-tenant": "t1",
        }

    def test_e2e_rest_delivery_unauthenticated_omits_auth_header(self, monkeypatch):
        import httpx

        from src.core.exceptions import AdCPAuthRequiredError
        from tests.helpers.envelope_assertions import envelope_for

        # ADR-010: ``AdCPSalesAgentError.__init__`` is keyword-only and has no
        # ``message`` parameter — ``message``/``suggestion``/``recovery``/``status_code``
        # are read-only properties resolved from CODE_TABLE, so the class alone
        # yields the envelope.
        wire_body = envelope_for(AdCPAuthRequiredError())
        captured = {}

        class _FakeResponse:
            status_code = 401
            headers = {"content-type": "application/json"}
            text = '{"errors": [{"code": "AUTH_MISSING"}]}'

            def json(self):
                return wire_body

        class _FakeClient:
            def __init__(self, *, base_url, timeout):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *exc_info):
                return False

            def post(self, url, *, json, headers):
                captured["headers"] = headers
                return _FakeResponse()

        monkeypatch.setattr(httpx, "Client", _FakeClient)

        with self._make_env_with_e2e_config() as env:
            client = AdCPTestClient(env)
            result = client.call("get_products", {"brief": "x"}, Transport.E2E_REST, credential={})

        assert "Authorization" not in captured["headers"]
        assert result.is_error
        # AUTH_MISSING, not the deprecated AUTH_REQUIRED: the pinned AdCP 3.1.1
        # ``enums/error-code.json`` marks AUTH_REQUIRED "**Deprecated** — use
        # ``AUTH_MISSING`` (no credentials presented)", and AUTH_MISSING's own
        # entry is a MUST: "Sellers MUST return this code when no ``Authorization``
        # header was included in the request." ``AdCPAuthRequiredError`` — the class
        # this fixture's wire body is built from — carries ``ErrorCode.AUTH_MISSING``
        # accordingly, so the arranged body and the assertion now name one code.
        result.assert_wire_error("AUTH_MISSING")

    def test_e2e_rest_delivery_requires_e2e_config(self):
        from tests.harness.address_table import ToolAddress
        from tests.harness.client import _deliver_e2e_rest

        class _UnitEnv(BaseTestEnv):
            pass

        with _UnitEnv() as env:
            address = ToolAddress(Transport.E2E_REST, name="/api/v1/products", method="post")
            with pytest.raises(RuntimeError, match="e2e_config"):
                _deliver_e2e_rest(env, address, {"url": "/api/v1/products", "body": {}}, {})


class TestClientE2eMcpDelivery:
    """Real e2e MCP DELIVER — mocks the fastmcp
    ``Client``/HTTP transport layer for unit-level coverage. Genuine
    e2e-with-a-real-server verification happens in ``tests/e2e/`` via
    ``./run_all_tests.sh`` (no Docker stack available in this worktree)."""

    class _FakeToolResult:
        def __init__(self, structured_content: dict) -> None:
            self.structured_content = structured_content

    class _FakeMcpClient:
        """Records the transport it was built with and the tool_name/arguments
        of the last call_tool — enough to assert DELIVER built the real HTTP
        transport with the right URL/headers and dispatched the right tool."""

        instances: list = []

        def __init__(self, transport=None):
            self.transport = transport
            self.calls: list[tuple[str, dict]] = []
            type(self).instances.append(self)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc_info):
            return False

        async def call_tool(self, name, arguments):
            self.calls.append((name, arguments))
            # Empty products list — a full Product needs many required nested
            # fields (publisher_properties, pricing_options,
            # reporting_capabilities, ...) irrelevant to what these tests
            # verify (transport wiring / headers / delegation); an empty list
            # still round-trips through GetProductsResponse
            # (the spec_response_model parse-back).
            return TestClientE2eMcpDelivery._FakeToolResult({"products": []})

    def test_e2e_mcp_dispatch_builds_real_http_transport_and_succeeds(self, monkeypatch):
        from tests.harness._base import BaseTestEnv
        from tests.harness.transport import E2EConfig

        self._FakeMcpClient.instances = []
        monkeypatch.setattr("fastmcp.Client", self._FakeMcpClient)

        class _UnitEnv(BaseTestEnv):
            pass

        env = _UnitEnv(e2e_config=E2EConfig(base_url="http://e2e-host:9000", postgres_url="postgresql://x/y"))
        client = AdCPTestClient(env)

        result = client.call("get_products", {"brief": "video ads"}, Transport.E2E_MCP, credential={})

        assert result.is_success, result.error
        # Tag derived from Transport.E2E_MCP.value ("e2e_mcp") — before
        # this incorrectly pinned the in-process "mcp"
        # tag on an E2E dispatch (client.py's _unwrap_mcp_success is shared
        # by both Transport.MCP and Transport.E2E_MCP).
        assert result.envelope["transport"] == "e2e_mcp"
        assert result.wire_response == {"products": []}

        fake_client = self._FakeMcpClient.instances[0]
        assert fake_client.transport.url == "http://e2e-host:9000/mcp/"
        assert fake_client.transport.headers == {}  # credential={} -> no headers at all
        assert fake_client.calls == [("get_products", {"brief": "video ads"})]

    def test_e2e_mcp_dispatch_sends_the_credential_as_headers(self, monkeypatch):
        from tests.harness._base import BaseTestEnv
        from tests.harness.transport import E2EConfig

        self._FakeMcpClient.instances = []
        monkeypatch.setattr("fastmcp.Client", self._FakeMcpClient)

        class _UnitEnv(BaseTestEnv):
            pass

        env = _UnitEnv(e2e_config=E2EConfig(base_url="http://e2e-host:9000", postgres_url="postgresql://x/y"))
        client = AdCPTestClient(env)

        client.call(
            "get_products",
            {"brief": "video ads"},
            Transport.E2E_MCP,
            credential=credential_headers(token="tok_123", tenant="t1"),
        )

        fake_client = self._FakeMcpClient.instances[0]
        assert fake_client.transport.headers["Authorization"] == "Bearer tok_123"
        assert fake_client.transport.headers["x-adcp-tenant"] == "t1"

    def test_e2e_mcp_dispatch_requires_e2e_config(self):
        """Missing env.e2e_config is a genuine precondition failure (mirrors
        RestE2EDispatcher.dispatch's own check) — caught by
        AdCPTestClient.call()'s generic except and surfaced as an error
        TransportResult, same as any other DELIVER-raised Exception (NOT the
        NotImplementedError special-case that re-raises loudly)."""
        from tests.harness._base import BaseTestEnv

        class _UnitEnv(BaseTestEnv):
            pass

        with _UnitEnv() as env:
            client = AdCPTestClient(env)
            result = client.call("get_products", {"brief": "x"}, Transport.E2E_MCP, credential={})

        assert result.is_error
        assert "e2e_config" in str(result.error)

    def test_e2e_mcp_dispatch_tool_error_unwraps_to_adcp_error(self, monkeypatch):
        from fastmcp.exceptions import ToolError

        from tests.harness._base import BaseTestEnv
        from tests.harness.transport import E2EConfig

        class _FailingMcpClient(self._FakeMcpClient):
            async def call_tool(self, name, arguments):
                raise ToolError(
                    '{"errors": [{"code": "AUTH_REQUIRED", "message": "no auth", "recovery": "correctable"}], '
                    '"adcp_error": {"code": "AUTH_REQUIRED", "message": "no auth", "recovery": "correctable"}}'
                )

        monkeypatch.setattr("fastmcp.Client", _FailingMcpClient)

        class _UnitEnv(BaseTestEnv):
            pass

        env = _UnitEnv(e2e_config=E2EConfig(base_url="http://e2e-host:9000", postgres_url="postgresql://x/y"))
        client = AdCPTestClient(env)

        result = client.call("get_products", {"brief": "x"}, Transport.E2E_MCP, credential={})

        assert result.is_error
        result.assert_wire_error("AUTH_REQUIRED")


class TestA2AHttpResultContract:
    """The client reads actual HTTP frames without accepting the old native lifecycle."""

    @staticmethod
    def _dispatch(
        frame,
        status=200,
        entrypoint="client",
        local_parser=False,
        *,
        tool="get_products",
        transport=Transport.E2E_A2A,
    ):
        from tests.helpers.local_http_origin import run_local_origin

        with run_local_origin() as origin:
            origin.respond_with(status, body=json.dumps(frame).encode())
            env = _origin_env(origin, a2a_over_http=True, a2a_skill="get_products")
            if local_parser:
                from adcp.types import GetAdcpCapabilitiesResponse

                # A mismatched env parser must remain a harness error even though
                # the received get_products frame passes the core's pinned parser.
                env.RESPONSE_MODEL = GetAdcpCapabilitiesResponse
            if entrypoint == "dispatcher":
                return env.call_via(Transport.A2A, credential={})
            if entrypoint != "client":
                return getattr(env, entrypoint)(credential={})
            return AdCPTestClient(env).call(tool, {}, transport, credential={})

    @staticmethod
    def _frame(state="TASK_STATE_COMPLETED"):
        return {
            "jsonrpc": "2.0",
            "id": "reader-contract",
            "result": {
                "task": {
                    "id": "native-receipt",
                    "status": {"state": state},
                    "artifacts": [{"parts": [{"data": {"products": []}}]}],
                }
            },
        }

    @staticmethod
    def _read_native(frame, over_http):
        import httpx

        from tests.harness.a2a_wire import jsonrpc_http_result, native_task_result

        response = httpx.Response(200, json=frame)
        result = (
            jsonrpc_http_result(Transport.A2A, response)
            if over_http
            else native_task_result(Transport.A2A, frame["result"]["task"])
        )
        return result, response

    @pytest.mark.parametrize("transport", [Transport.A2A, Transport.E2E_A2A])
    def test_account_environment_routes_list_readback_then_returns_to_sync(self, transport):
        from src.core.schemas.account import ListAccountsRequest, ListAccountsResponse, SyncAccountsResponse
        from tests.harness.account_sync import AccountSyncEnv
        from tests.harness.transport import E2EConfig
        from tests.helpers.local_http_origin import run_local_origin

        frame = self._frame()
        body = {"accounts": [], "context": {"trace": "account-readback"}}
        frame["result"]["task"]["artifacts"][0]["parts"][0]["data"] = body
        request = ListAccountsRequest(include_webhook_activity=True, webhook_activity_limit=3)
        with run_local_origin() as origin:
            origin.respond_with(200, body=json.dumps(frame).encode())
            env = AccountSyncEnv(e2e_config=E2EConfig(base_url=origin.base_url, postgres_url="postgresql://unused/db"))
            env._run_a2a_handler = env._run_a2a_over_http

            listed = env.call_via(transport, req=request, credential={})
            synced = env.call_via(transport, accounts=[], credential={})

        sent = [entry.json()["params"]["message"]["parts"][0]["data"] for entry in origin.requests]
        assert sent == [
            {"skill": "list_accounts", "parameters": request.model_dump(mode="json", exclude_none=True)},
            {"skill": "sync_accounts", "parameters": {"accounts": []}},
        ]
        for result, model in ((listed, ListAccountsResponse), (synced, SyncAccountsResponse)):
            assert result.is_success, result.error
            assert type(result.payload) is model
            assert result.payload.accounts == []
            assert result.payload.context == {"trace": "account-readback"}
            assert result.wire_response == body
            assert result.has_wire
            assert result.raw_response.status_code == 200
            assert result.envelope["task"] == frame["result"]["task"]

    def test_completed_result_retains_http_response_and_native_frame(self):
        frame = self._frame()
        result = self._dispatch(frame)

        assert result.is_success, result.error
        assert result.has_wire
        assert result.raw_response.status_code == 200
        assert result.envelope["task"] == frame["result"]["task"]
        assert result.wire_response == {"products": []}
        assert result.envelope["a2a_task_id"] == "native-receipt"
        assert result.envelope["a2a_task_state"] == "TASK_STATE_COMPLETED"

    def test_failed_result_retains_http_response_and_native_frame(self):
        frame = self._frame("TASK_STATE_FAILED")
        entry = {"code": "PRODUCT_NOT_FOUND", "message": "missing", "recovery": "retry"}
        envelope = {"adcp_error": entry, "errors": [entry]}
        frame["result"]["task"]["artifacts"][0]["parts"][0]["data"] = envelope
        result = self._dispatch(frame)

        assert result.is_error
        assert result.has_wire
        assert result.raw_response.status_code == 200
        assert result.envelope["task"] == frame["result"]["task"]
        assert result.error_envelope() == envelope

    @pytest.mark.parametrize("transport", [Transport.A2A, Transport.E2E_A2A])
    def test_generic_client_uses_requested_tool_model_instead_of_environment_model(self, transport):
        frame = self._frame()
        result = self._dispatch(frame, local_parser=True, transport=transport)

        assert result.is_success, result.error
        assert result.payload.products == []
        assert result.wire_response == {"products": []}
        assert result.envelope["task"] == frame["result"]["task"]

    @pytest.mark.parametrize("transport", [Transport.A2A, Transport.E2E_A2A])
    def test_generic_client_retains_success_without_a_single_pinned_model(self, transport):
        frame = self._frame()
        body = {"status": "submitted", "task_id": "business-task"}
        frame["result"]["task"]["artifacts"][0]["parts"][0]["data"] = body

        result = self._dispatch(frame, tool="create_media_buy", transport=transport)

        assert result.error is None, result.error
        assert result.payload is None
        assert result.wire_response == body
        assert result.envelope["task"] == frame["result"]["task"]
        assert result.raw_response.status_code == 200
        assert result.has_wire

    @pytest.mark.parametrize("poll_task_id", ["durable-adcp-handle", "returned-poll-handle"])
    @pytest.mark.parametrize("transport", [Transport.A2A, Transport.E2E_A2A])
    def test_receipt_environment_reads_submitted_and_polled_e2e_a2a_bodies(self, poll_task_id, transport):
        from unittest.mock import call, patch

        from pydantic import RootModel

        from tests.harness.a2a_task_ownership import A2ATaskOwnershipEnv
        from tests.harness.transport import E2EConfig
        from tests.helpers.local_http_origin import run_local_origin

        frames = []
        for receipt_id, status, task_id in (
            ("native-invocation-receipt", "submitted", "durable-adcp-handle"),
            ("native-poll-receipt", "input-required", poll_task_id),
        ):
            frame = self._frame()
            task = frame["result"]["task"]
            task["id"] = receipt_id
            task["artifacts"][0]["parts"][0]["data"] = {
                "status": status,
                "task_id": task_id,
                "context": {"trace": "native-receipt"},
            }
            frames.append(frame)

        with (
            run_local_origin() as origin,
            patch("tests.harness.a2a_task_ownership.RootModel", wraps=RootModel) as read_root,
        ):
            origin.respond_in_sequence([(200, json.dumps(frame).encode()) for frame in frames])
            env = A2ATaskOwnershipEnv(
                e2e_config=E2EConfig(base_url=origin.base_url, postgres_url="postgresql://unused/db")
            )
            env.skill = "update_media_buy"
            env.parameters = {"media_buy_id": "existing-buy", "context": {"trace": "native-receipt"}}
            declared_skill = env.A2A_SKILL

            expected_reads = []
            for count, (action, frame) in enumerate(zip(("invoke", "poll"), frames, strict=True), start=1):
                result = env.call_via(transport, action=action, caller="owner")
                assert env.A2A_SKILL == declared_skill
                task = frame["result"]["task"]
                body = task["artifacts"][0]["parts"][0]["data"]

                assert result.is_success, result.error
                assert isinstance(result.payload, RootModel)
                assert result.payload.root == body
                expected_reads.append(call(body))
                assert read_root.call_args_list == expected_reads
                assert result.require_wire() == body
                assert result.has_wire
                assert result.raw_response.status_code == 200
                assert result.raw_response.json() == frame
                assert result.envelope["transport"] == Transport.E2E_A2A.value
                assert result.envelope["task"] == task
                assert result.envelope["a2a_task_id"] == task["id"]
                assert result.envelope["a2a_task_state"] == "TASK_STATE_COMPLETED"
                assert env.task_id == "native-invocation-receipt"
                assert env.adcp_task_id == "durable-adcp-handle"
                assert len(origin.requests) == count
                request = origin.requests[-1]
                assert request.method == "POST"
                assert request.path == "/a2a"
                assert request.json()["params"]["message"]["parts"][0]["data"] == {
                    "skill": "update_media_buy" if action == "invoke" else "get_task_status",
                    "parameters": env.parameters if action == "invoke" else {"task_id": "durable-adcp-handle"},
                }

    @pytest.mark.parametrize("entrypoint", ["reader", "deliver_a2a"])
    def test_override_reader_accepts_boundary_owned_context(self, entrypoint):
        from src.core.schemas import GetProductsResponse
        from tests.harness.a2a_wire import _response_reader, parse_response_result
        from tests.helpers.local_http_origin import run_local_origin

        frame = self._frame()
        frame["result"]["task"]["artifacts"][0]["parts"][0]["data"]["context"] = {"trace": "reader-context"}
        with run_local_origin() as origin:
            origin.respond_with(200, body=json.dumps(frame).encode())
            env = _origin_env(origin, a2a_over_http=True, a2a_skill="get_products")
            env.RESPONSE_MODEL = GetProductsResponse
            result = (
                parse_response_result(
                    env._run_a2a_over_http("get_products", credential={}), _response_reader(GetProductsResponse)
                )
                if entrypoint == "reader"
                else env.deliver_a2a(credential={})
            )

        assert result.is_success, result.error
        assert type(result.payload) is GetProductsResponse
        assert result.payload.context == {"trace": "reader-context"}
        assert result.wire_response["context"] == {"trace": "reader-context"}
        assert result.has_wire

    @classmethod
    def _delivery_failure(cls, kind):
        frame = cls._frame("TASK_STATE_FAILED" if kind == "native" else "TASK_STATE_COMPLETED")
        if kind == "native":
            entry = {"code": "PRODUCT_NOT_FOUND", "message": "missing", "recovery": "retry"}
            body = {"adcp_error": entry, "errors": [entry]}
        elif kind == "typed_parse":
            body = {"products": "invalid-list"}
        else:
            body = {"products": []}
        frame["result"]["task"]["artifacts"][0]["parts"][0]["data"] = body
        if kind == "malformed":
            frame["result"]["task"]["status"] = "invalid-status-object"
        return frame, body

    @pytest.mark.parametrize("kind", ["native", "typed_parse", "local_parse", "malformed"])
    def test_delivery_returns_captured_failure_and_legacy_call_raises_it(self, kind):
        from tests.harness.transport import TransportResult

        frame, body = self._delivery_failure(kind)
        result = self._dispatch(frame, entrypoint="deliver_a2a", local_parser=kind == "local_parse")
        assert isinstance(result, TransportResult)
        assert result.is_error
        assert result.raw_response.status_code == 200
        assert result.envelope["task"] == frame["result"]["task"]
        assert result.wire_response == (None if kind == "malformed" else body)
        env = BaseTestEnv()
        env.deliver_a2a = lambda **kwargs: result
        with pytest.raises(type(result.error)) as caught:
            env.call_a2a()
        assert caught.value is result.error

    @pytest.mark.parametrize(
        "status,kind", [(200, "task_data"), (200, "bare"), (401, "bare"), (401, "two_layer"), (401, "envelope_only")]
    )
    def test_jsonrpc_refusal_retains_complete_http_and_adcp_evidence(self, status, kind):
        from tests.harness.transport import WireError, WireRefusal

        refusal = {"code": -32001, "message": "purged"}
        body = None
        if kind == "task_data":
            refusal["data"] = {"task_id": "native-receipt"}
        elif kind in ("two_layer", "envelope_only"):
            entry = {"code": "AUTH_REQUIRED", "message": "credential required", "recovery": "correctable"}
            body = {"adcp_error": entry}
            if kind == "two_layer":
                body["errors"] = [entry]
            refusal = {"code": -32600, "message": "authentication required", "data": body}
        frame = {"jsonrpc": "2.0", "id": "request", "error": refusal}
        result = self._dispatch(frame, status)
        assert isinstance(result.error, WireError if body is not None else WireRefusal)
        assert result.envelope["jsonrpc_error"] == refusal
        assert result.envelope["jsonrpc_error_code"] == refusal["code"]
        assert result.envelope["transport"] == "e2e_a2a"
        assert result.raw_response.status_code == status
        assert result.raw_response.json() == frame
        assert result.has_wire
        assert result.error_envelope_or_none() == body
        if kind == "two_layer":
            result.assert_wire_error("AUTH_REQUIRED", recovery="correctable")

    @pytest.mark.parametrize("entrypoint", ["reader", "client", "dispatcher"])
    def test_malformed_event_stream_retains_http_evidence(self, entrypoint):
        import httpx

        from tests.harness.a2a_wire import jsonrpc_http_result
        from tests.helpers.local_http_origin import run_local_origin

        body = 'event: message\ndata: {"unexpected": true}\n\n'
        headers = {"content-type": "text/event-stream", "x-evidence": "malformed-stream"}
        if entrypoint == "reader":
            response = httpx.Response(200, text=body, headers=headers)
            result = jsonrpc_http_result(Transport.E2E_A2A, response)
            assert result.raw_response is response
        else:
            with run_local_origin() as origin:
                origin.respond_with(
                    200,
                    body=body.encode(),
                    content_type="text/event-stream",
                    headers={"x-evidence": "malformed-stream"},
                )
                env = _origin_env(origin)
                if entrypoint == "client":
                    result = AdCPTestClient(env).call("get_products", {}, Transport.E2E_A2A, credential={})
                else:
                    result = env.call_via(Transport.E2E_A2A, tool_name="get_products", credential={})

        assert result.is_error
        framing_error = result.error.__cause__ or result.error
        assert type(framing_error) is AssertionError
        assert repr(body) in str(framing_error)
        assert result.has_wire
        assert result.envelope["status"] == "transport_fault"
        assert result.envelope["status_code"] == 200
        assert result.raw_response.status_code == 200
        assert result.raw_response.headers["content-type"] == "text/event-stream"
        assert result.raw_response.headers["x-evidence"] == "malformed-stream"
        assert result.raw_response.text == body
        assert result.payload is None
        assert result.wire_response is None
        assert result.error_envelope_or_none() is None

    @pytest.mark.parametrize("lane", ["native", "http_reader", "client"])
    @pytest.mark.parametrize(
        "state,body,error_kind",
        [
            ("TASK_STATE_COMPLETED", {"products": []}, None),
            ("TASK_STATE_COMPLETED", {"products": [], "errors": [{"code": "ADVISORY", "message": "partial"}]}, None),
            (
                "TASK_STATE_COMPLETED",
                {
                    "adcp_error": {"code": "MEDIA_BUY_NOT_FOUND", "message": "missing"},
                    "errors": [{"code": "MEDIA_BUY_NOT_FOUND", "message": "missing"}],
                },
                "domain",
            ),
            ("TASK_STATE_FAILED", {"errors": [{"code": "MEDIA_BUY_NOT_FOUND", "message": "missing"}]}, "domain"),
            (
                "TASK_STATE_FAILED",
                {
                    "adcp_error": {"code": "PRODUCT_NOT_FOUND", "message": "missing", "recovery": "retry"},
                    "errors": [{"code": "PRODUCT_NOT_FOUND", "message": "missing", "recovery": "retry"}],
                },
                "domain",
            ),
            ("TASK_STATE_FAILED", {"products": []}, "malformed"),
            ("TASK_STATE_SUBMITTED", {"products": []}, "malformed"),
            ("TASK_STATE_WORKING", {"products": []}, "malformed"),
            ("TASK_STATE_INPUT_REQUIRED", {"products": []}, "malformed"),
            ("", {"products": []}, "malformed"),
            ("malformed-status", {"products": []}, "malformed"),
            (
                "legacy-submitted",
                {
                    "status": "submitted",
                    "task_id": "adcp-handle",
                    "message": "submitted for approval",
                    "context": {"correlation_id": "corr-1"},
                },
                "malformed",
            ),
        ],
    )
    def test_native_and_http_readers_classify_authentic_task_outcomes(self, lane, state, body, error_kind):
        from tests.harness.transport import WireError, WireRefusal

        frame = self._frame(state)
        task = frame["result"]["task"]
        task["artifacts"][0]["parts"][0]["data"] = body
        if state == "malformed-status":
            task["status"] = "completed"
        if state == "legacy-submitted":
            task["status"] = {
                "state": "TASK_STATE_SUBMITTED",
                "message": {"role": "ROLE_AGENT", "parts": [{"text": "submitted for approval"}, {"data": body}]},
            }
            task["artifacts"] = []
        tool = "create_media_buy" if state == "legacy-submitted" else "get_products"
        result, response = self._read_frame(frame, lane, tool=tool)
        assert result.has_wire is (lane != "native")
        assert result.raw_response is response
        assert result.envelope["task"] == task
        terminal = state in ("TASK_STATE_COMPLETED", "TASK_STATE_FAILED")
        assert result.wire_response == (body if terminal else None)
        if error_kind is None:
            assert result.is_success, result.error
            if lane == "client":
                assert result.payload.products == []
            else:
                result.payload.root["changed"] = True
                assert "changed" not in body
        else:
            assert isinstance(result.error, WireError if error_kind == "domain" else WireRefusal)
        if error_kind == "domain":
            assert result.error_envelope() == body
        if response is not None:
            assert response.status_code == 200
            assert response.json() == frame

    def _read_frame(self, frame, lane, *, tool="get_products"):
        if lane == "client":
            result = self._dispatch(frame, tool=tool)
            return result, result.raw_response
        result, response = self._read_native(frame, lane == "http_reader")
        return result, response if lane == "http_reader" else None

    @pytest.mark.parametrize("over_http", [False, True])
    @pytest.mark.parametrize("state", ["TASK_STATE_FAILED", "TASK_STATE_COMPLETED"])
    def test_envelope_only_adcp_error_retains_domain_evidence(self, over_http, state):
        from tests.harness._base import WireError

        # AdCP3.2.7 L0 a2a-response-format Error Handling illustrates this
        # shape; L3 error-handling's two-layer model permits a single layer.
        body = {"adcp_error": {"code": "MEDIA_BUY_NOT_FOUND", "message": "missing", "recovery": "correctable"}}
        frame = self._frame(state)
        task = frame["result"]["task"]
        task["artifacts"][0]["parts"][0]["data"] = body
        result, response = self._read_native(frame, over_http)

        assert result.envelope["status"] == "adcp_error"
        assert isinstance(result.error, WireError)
        assert result.payload is None
        assert result.error_envelope() == body
        assert result.error_envelope_or_none() is result.wire_response
        assert result.error.envelope is result.wire_response
        assert "errors" not in result.error_envelope()
        assert result.envelope["task"] == task
        assert result.raw_response is (response if over_http else None)
        assert result.has_wire is over_http

    @pytest.mark.parametrize("kind", ["native", "typed_parse", "local_parse", "malformed"])
    def test_http_dispatcher_keeps_error_and_native_evidence(self, kind):
        frame, body = self._delivery_failure(kind)
        result = self._dispatch(frame, entrypoint="dispatcher", local_parser=kind == "local_parse")

        assert result.is_error
        assert result.has_wire
        assert result.raw_response.status_code == 200
        assert result.envelope["task"] == frame["result"]["task"]
        if kind == "native":
            assert result.error_envelope() == body
        else:
            assert result.wire_response == (None if kind == "malformed" else body)
            assert result.payload is None
            if kind == "malformed":
                assert result.error.__cause__ is not None
            else:
                from pydantic import ValidationError

                assert isinstance(result.error, ValidationError)

    @pytest.mark.parametrize("with_errors", [False, True])
    def test_native_protocol_refusal_preserves_adcp_data(self, with_errors):
        from a2a.utils.errors import InvalidRequestError

        from tests.harness.a2a_wire import unwrap_delivery_error

        entry = {"code": "INVALID_REQUEST", "message": "invalid", "recovery": "correctable"}
        body = {"adcp_error": entry}
        if with_errors:
            body["errors"] = [entry]
        refusal = InvalidRequestError(data=body)
        result = unwrap_delivery_error(refusal, Transport.A2A)
        assert result.error_envelope() == body
        assert result.envelope["jsonrpc_error_code"] == -32600
        assert result.error is refusal
        assert not result.has_wire

    def test_typed_parser_failure_preserves_original_exception_and_body(self):
        from tests.harness.a2a_wire import native_task_result, parse_response_result

        task = self._frame()["result"]["task"]
        captured = native_task_result(Transport.A2A, task)
        original = ValueError("typed parser rejected the body")
        seen = []

        def reject(body):
            seen.append(body.copy())
            body.clear()
            raise original

        parsed = parse_response_result(captured, reject)
        assert parsed.error is original
        assert parsed.wire_response == {"products": []}
        assert parsed.envelope["task"] == task
        assert seen == [{"products": []}]
        assert parse_response_result(parsed, reject) is parsed
        assert len(seen) == 1

    @pytest.mark.parametrize(
        "artifacts",
        [
            [],
            [{"parts": [{"data": {"products": []}}]}, {"parts": [{"data": {"products": []}}]}],
            [{"parts": [{"text": "no body"}]}],
            [{"parts": [{"data": {"products": []}}, {"data": {"products": []}}]}],
            [{"parts": []}],
            [None],
        ],
    )
    @pytest.mark.parametrize("lane", ["native", "http_reader", "client"])
    def test_malformed_artifact_cardinality_keeps_native_evidence(self, artifacts, lane):
        from tests.harness.transport import WireRefusal

        frame = self._frame()
        task = frame["result"]["task"]
        task["artifacts"] = artifacts
        result, response = self._read_frame(frame, lane)
        assert isinstance(result.error, WireRefusal)
        assert result.envelope["task"] == task
        assert result.raw_response is response
        assert result.has_wire is (lane != "native")
        assert result.wire_response is None
        if response is not None:
            assert response.status_code == 200
            assert response.json() == frame

    def test_root_request_preserves_query_and_headers(self):
        from tests.helpers.local_http_origin import run_local_origin

        with run_local_origin() as origin:
            env = _origin_env(origin)
            response = env.get_root("/root", params={"tenant": "a b"}, headers={"X-Example": "caller"})
            (request,) = origin.requests
        assert response.status_code == 200
        assert request.method == "GET"
        assert request.path == "/root?tenant=a+b"
        assert request.headers["X-Example"] == "caller"

    def test_http_numeric_fields_are_not_repaired_by_the_reader(self):
        frame = self._frame()
        frame["result"]["task"]["artifacts"][0]["parts"][0]["data"]["returned_count"] = 2.0
        result = self._dispatch(frame)

        assert type(result.wire_response["returned_count"]) is float

    def test_protobuf_capture_restores_wire_integer_fields(self):
        from a2a.types import Task
        from google.protobuf.json_format import ParseDict

        from tests.harness.a2a_wire import _a2a_protobuf_task

        frame = self._frame()["result"]["task"]
        frame["artifacts"][0]["parts"][0]["data"]["returned_count"] = 2
        task = ParseDict(frame, Task())

        data = _a2a_protobuf_task(task)["artifacts"][0]["parts"][0]["data"]

        assert type(data["returned_count"]) is int
        assert data["returned_count"] == 2

    @pytest.mark.parametrize("route", ["a2a", "rest_post", "update_put"])
    @pytest.mark.parametrize("capability", [False, True])
    @pytest.mark.parametrize("signed", [False, True])
    @pytest.mark.parametrize(
        "credential",
        [
            credential_headers(token="invalid-token", tenant="foreign-tenant"),
            {
                name.swapcase(): "" if name.lower() == "authorization" else value
                for name, value in credential_headers(token="foreign-token", host="foreign-tenant.example").items()
            },
            {"x-adcp-tenant": "foreign-tenant"},
            {},
        ],
    )
    def test_http_routes_share_signing_identity_and_exact_request_bytes(self, route, capability, signed, credential):
        import time

        from adcp.signing.autosign import signing_profile_for_adcp_version
        from adcp.signing.jwks import StaticJwksResolver
        from adcp.signing.verifier import VerifierCapability, VerifyOptions, verify_request_signature
        from pydantic import RootModel

        from tests.harness.media_buy_dual import MediaBuyDualEnv
        from tests.harness.signing_capability import SigningCapability
        from tests.helpers.local_http_origin import run_local_origin
        from tests.helpers.signing import keypair_for

        private_key, jwks = keypair_for("a2a-reader")
        with run_local_origin() as origin:
            origin.respond_with(200, body=json.dumps(self._frame()).encode())
            env = _origin_env(origin)
            if route == "update_put":
                env = MediaBuyDualEnv(e2e_config=env.e2e_config)
            if capability:
                env._signing = SigningCapability(private_key, jwks, "capability-token", "a2a-reader", origin.base_url)
            body = {"marker": "exact sent bytes"}
            method, path = (
                ("PUT", "/api/v1/media-buys/buy-1")
                if route == "update_put"
                else ("POST", "/a2a" if route == "a2a" else "/api/v1/products")
            )

            def send():
                if route == "a2a":
                    return env.send_a2a(body, credential, signed=signed)
                if route == "update_put":
                    return env._run_update_rest_request(
                        media_buy_id="buy-1", **body, credential=credential, signed=signed
                    )
                return env._run_rest_request(path, req=RootModel(body), credential=credential, signed=signed)

            if signed and not capability:
                with pytest.raises(AssertionError, match="no signing capability"):
                    send()
                assert origin.requests == []
                return
            send()
            (request,) = origin.requests
            headers = {key.lower(): value for key, value in request.headers.items()}
            assert request.method == method
            assert request.path == path
            assert request.json() == body
            if capability:
                presents_auth = any(name.lower() == "authorization" for name in credential)
                assert headers.get("authorization") == ("Bearer capability-token" if presents_auth else None)
                assert "x-adcp-auth" not in headers
                assert headers["x-adcp-tenant"] == env._tenant_id
                assert headers["host"] == origin.base_url.removeprefix("http://")
            else:
                for name, value in credential.items():
                    assert headers[name.lower()] == value
            assert ("signature" in headers) is signed
            if signed:
                from adcp import get_adcp_spec_version

                signer = verify_request_signature(
                    method=request.method,
                    url=origin.base_url + request.path,
                    headers=request.headers,
                    body=request.body,
                    options=VerifyOptions(
                        now=time.time(),
                        capability=VerifierCapability(supported=True),
                        operation="test",
                        jwks_resolver=StaticJwksResolver(jwks),
                        signing_profile_version=signing_profile_for_adcp_version(get_adcp_spec_version()),
                    ),
                )
                assert signer.body_authenticated
                assert signer.parsed_body == body


class TestClientE2eA2aDelivery:
    """Generic client and environment dispatcher over a real local HTTP origin."""

    #: The smallest get_products success body the pinned schema accepts.
    #:
    #: ``media-buy/get-products-response.json`` states a constraint outside its
    #: properties, and the rebuilt SDK compiles it onto the exported class, so
    #: ``client.py``'s parse-back through ``spec_response_model`` refuses a document
    #: carrying only ``message`` and ``success``. ``products`` is what the constraint
    #: wants; an empty list satisfies it without hand-maintaining a full Product
    #: fixture, whose many required nested members (publisher_properties,
    #: pricing_options, reporting_capabilities, ...) none of these tests assert on.
    #:
    #: One constant rather than a literal per test: two of the four call sites wrote
    #: their own body, omitted ``products``, and stopped running at the new pin
    #: (salesagent-8qela.123). A test that asserts on the REQUEST should not be
    #: authoring a response at all, let alone a different one each time.
    _MINIMAL_GET_PRODUCTS_ARTIFACT = {"products": [], "message": "ok", "success": True}

    @staticmethod
    def _rpc_success_body(*, task_id: str = "task_abc123", state: str = "TASK_STATE_COMPLETED", artifact_data: dict):
        return {
            "jsonrpc": "2.0",
            "id": "req-1",
            "result": {
                "task": {
                    "id": task_id,
                    "contextId": "ctx_1",
                    "status": {"state": state},
                    "artifacts": [{"artifactId": "art-1", "parts": [{"data": artifact_data}]}],
                }
            },
        }

    @pytest.mark.parametrize("entrypoint", ["client", "dispatcher"])
    @pytest.mark.parametrize("credential", [credential_headers(token="tok123", tenant="t1"), {}])
    def test_request_and_response_cross_the_http_origin(self, entrypoint, credential):
        from tests.harness.dispatchers import A2AE2EDispatcher
        from tests.helpers.local_http_origin import run_local_origin

        body = self._MINIMAL_GET_PRODUCTS_ARTIFACT
        frame = self._rpc_success_body(artifact_data=body)
        with run_local_origin() as origin:
            origin.respond_with(200, body=json.dumps(frame).encode())
            env = _origin_env(origin)
            if entrypoint == "client":
                result = AdCPTestClient(env).call(
                    "get_products", {"brief": "video ads"}, Transport.E2E_A2A, credential=credential
                )
            else:
                result = A2AE2EDispatcher().dispatch(
                    env, tool_name="get_products", brief="video ads", credential=credential
                )
            (request,) = origin.requests

        assert result.is_success, result.error
        assert result.payload.products == []
        assert result.wire_response == body
        assert result.raw_response.json() == frame
        assert result.has_wire
        assert request.method == "POST"
        assert request.path == "/a2a"
        sent = request.json()
        assert sent["jsonrpc"] == "2.0"
        assert sent["method"] == "SendMessage"
        assert sent["params"]["message"]["parts"][0]["data"] == {
            "skill": "get_products",
            "parameters": {"brief": "video ads"},
        }
        headers = {name.lower(): value for name, value in request.headers.items()}
        assert headers.get("authorization") == ("Bearer tok123" if credential else None)
        assert headers.get("x-adcp-tenant") == ("t1" if credential else None)

    def test_missing_e2e_config_raises(self):
        class _UnitEnv(BaseTestEnv):
            pass

        with _UnitEnv() as env:
            client = AdCPTestClient(env)
            result = client.call("get_products", {"brief": "x"}, Transport.E2E_A2A)

        assert result.is_error
        assert "e2e_config" in str(result.error)


class TestA2AE2EDispatcher:
    """``A2AE2EDispatcher`` (the wire-grading work) — the legacy
    ``env.call_via(Transport.E2E_A2A, ...)`` entry point, delegating to
    ``AdCPTestClient``/``_deliver_e2e_a2a`` under the hood."""

    def test_dispatch_requires_a_tool_name(self):
        from tests.harness.dispatchers import A2AE2EDispatcher
        from tests.harness.transport import E2EConfig, MissingToolNameError

        class _UnitEnv(BaseTestEnv):
            pass

        with _UnitEnv(e2e_config=E2EConfig(base_url="http://e2e-stack:8080", postgres_url="postgresql://x")) as env:
            # MissingToolNameError subclasses NotImplementedError, so this stays
            # the "hard wiring failure" _dispatch_core re-raises rather than
            # downgrading into an error TransportResult -- pinning the exact
            # unified type instead of the bare parent.
            with pytest.raises(MissingToolNameError, match="tool_name"):
                A2AE2EDispatcher().dispatch(env, brief="video ads")


class TestMcpE2EDispatcherDelegation:
    """``McpE2EDispatcher`` (``tests/harness/dispatchers.py``) is the legacy
    ``env.call_via(Transport.E2E_MCP, **kwargs)`` entry point — it must
    delegate to ``AdCPTestClient`` rather than reimplement ADDRESS/WRAP/
    DELIVER/UNWRAP a second time."""

    def test_requires_tool_name(self):
        from tests.harness._base import BaseTestEnv
        from tests.harness.dispatchers import McpE2EDispatcher
        from tests.harness.transport import MissingToolNameError

        class _UnitEnv(BaseTestEnv):
            pass

        with _UnitEnv() as env:
            # MissingToolNameError (tests.harness.transport) is the ONE missing-
            # tool-name exception type -- it used to be a
            # per-dispatcher fork (TypeError here, NotImplementedError on
            # A2AE2EDispatcher below).
            with pytest.raises(MissingToolNameError, match="tool_name"):
                McpE2EDispatcher().dispatch(env, credential={})

    def test_delegates_to_client_with_flattened_req(self, monkeypatch):
        from tests.harness._base import BaseTestEnv
        from tests.harness.dispatchers import McpE2EDispatcher
        from tests.harness.transport import E2EConfig

        TestClientE2eMcpDelivery._FakeMcpClient.instances = []
        monkeypatch.setattr("fastmcp.Client", TestClientE2eMcpDelivery._FakeMcpClient)

        class _UnitEnv(BaseTestEnv):
            pass

        class _FakeReq:
            def model_dump(self, **kwargs):
                return {"brief": "video ads"}

        env = _UnitEnv(e2e_config=E2EConfig(base_url="http://e2e-host:9000", postgres_url="postgresql://x/y"))

        result = McpE2EDispatcher().dispatch(env, tool_name="get_products", req=_FakeReq(), credential={})

        assert result.is_success, result.error
        fake_client = TestClientE2eMcpDelivery._FakeMcpClient.instances[0]
        assert fake_client.calls == [("get_products", {"brief": "video ads"})]
