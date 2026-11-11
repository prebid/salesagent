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
    def _dispatch(frame, status=200, entrypoint="client", local_parser=False):
        from tests.harness.transport import E2EConfig
        from tests.helpers.local_http_origin import run_local_origin

        with run_local_origin() as origin:
            origin.respond_with(status, body=json.dumps(frame).encode())
            env = BaseTestEnv(e2e_config=E2EConfig(base_url=origin.base_url, postgres_url="postgresql://unused/db"))
            env.A2A_OVER_HTTP = True
            env.A2A_SKILL = "get_products"
            env._signed_dispatch = False
            if local_parser:
                from adcp.types import GetAdcpCapabilitiesResponse

                # A mismatched env parser must remain a harness error even though
                # the received get_products frame passes the core's pinned parser.
                env.RESPONSE_MODEL = GetAdcpCapabilitiesResponse
            if entrypoint == "dispatcher":
                return env.call_via(Transport.A2A, credential={})
            if entrypoint != "client":
                return getattr(env, entrypoint)(credential={})
            return AdCPTestClient(env).call("get_products", {}, Transport.E2E_A2A, credential={})

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

    def test_completed_result_retains_http_response_and_native_frame(self):
        frame = self._frame()
        result = self._dispatch(frame)

        assert result.is_success, result.error
        assert result.has_wire
        assert result.raw_response.status_code == 200
        assert result.envelope["task"] == frame["result"]["task"]
        assert result.wire_response == {"products": []}

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
    @pytest.mark.parametrize("entrypoint", ["call_a2a", "deliver_a2a"])
    def test_legacy_http_delivery_raises_instead_of_returning_an_error_payload(self, kind, entrypoint):
        from tests.harness._base import WireError, WireRefusal

        frame, _body = self._delivery_failure(kind)
        error_type = WireError if kind == "native" else WireRefusal
        with pytest.raises(error_type) as caught:
            self._dispatch(frame, entrypoint=entrypoint, local_parser=kind == "local_parse")

        assert caught.value.response.status_code == 200
        if kind != "native":
            assert caught.value.__cause__ is not None

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
            assert result.error.__cause__ is not None

    @pytest.mark.parametrize("field, value", [("status", "completed"), ("artifacts", [None])])
    def test_malformed_native_task_retains_the_http_evidence(self, field, value):
        frame = self._frame()
        frame["result"]["task"][field] = value
        result = self._dispatch(frame)

        assert result.is_error
        assert result.has_wire
        assert result.raw_response.status_code == 200

    def test_http_numeric_fields_are_not_repaired_by_the_reader(self):
        frame = self._frame()
        frame["result"]["task"]["artifacts"][0]["parts"][0]["data"]["returned_count"] = 2.0
        result = self._dispatch(frame)

        assert type(result.wire_response["returned_count"]) is float

    def test_protobuf_capture_restores_wire_integer_fields(self):
        from a2a.types import Task
        from google.protobuf.json_format import ParseDict

        from tests.harness._base import _a2a_protobuf_data

        frame = self._frame()["result"]["task"]
        frame["artifacts"][0]["parts"][0]["data"]["returned_count"] = 2
        task = ParseDict(frame, Task())

        data = _a2a_protobuf_data(task)

        assert type(data["returned_count"]) is int
        assert data["returned_count"] == 2

    @pytest.mark.parametrize("signed", [False, True])
    @pytest.mark.parametrize(
        "credential",
        [
            credential_headers(token="invalid-token", tenant="foreign-tenant"),
            credential_headers(token="foreign-token", host="foreign-tenant.example"),
        ],
    )
    def test_signable_http_route_preserves_presented_credentials(self, signed, credential):
        from tests.harness.signing_capability import SigningCapability
        from tests.harness.transport import E2EConfig
        from tests.helpers.local_http_origin import run_local_origin
        from tests.helpers.signing import keypair_for

        private_key, jwks = keypair_for("a2a-reader")
        with run_local_origin() as origin:
            origin.respond_with(200, body=json.dumps(self._frame()).encode())
            env = BaseTestEnv(e2e_config=E2EConfig(base_url=origin.base_url, postgres_url="postgresql://unused/db"))
            env._signing = SigningCapability(private_key, jwks, "capability-token", "a2a-reader", origin.base_url)
            env.post_a2a({"method": "SendMessage"}, credential, signed=signed)
            headers = {key.lower(): value for key, value in origin.requests[0].headers.items()}

        for header in ("Authorization", "x-adcp-auth"):
            assert headers.get(header.lower()) == credential.get(header)
        for header in ("x-adcp-tenant", "Host"):
            if header in credential:
                assert headers[header.lower()] == credential[header]
        assert ("signature" in headers) is signed

    @pytest.mark.parametrize("state", ["TASK_STATE_SUBMITTED", "TASK_STATE_WORKING", "TASK_STATE_INPUT_REQUIRED", ""])
    def test_nonterminal_native_state_is_rejected_even_with_an_artifact(self, state):
        result = self._dispatch(self._frame(state))

        assert result.is_error
        assert result.has_wire
        assert result.raw_response.status_code == 200
        assert result.wire_response is None

    @pytest.mark.parametrize("artifacts, parts", [(0, 1), (2, 1), (1, 0), (1, 2)])
    def test_task_requires_exactly_one_artifact_and_one_data_part(self, artifacts, parts):
        frame = self._frame()
        frame["result"]["task"]["artifacts"] = [
            {"parts": [{"data": {"products": []}} for _ in range(parts)]} for _ in range(artifacts)
        ]
        result = self._dispatch(frame)

        assert result.is_error
        assert result.has_wire
        assert result.wire_response is None

    @pytest.mark.parametrize("status", [200, 401])
    def test_jsonrpc_refusal_retains_the_response_and_standard_code(self, status):
        frame = {"jsonrpc": "2.0", "id": "reader-contract", "error": {"code": -32001, "message": "purged"}}
        result = self._dispatch(frame, status)

        assert result.is_error
        assert result.has_wire
        assert result.raw_response.status_code == status
        assert result.envelope["transport"] == "e2e_a2a"
        assert result.envelope["jsonrpc_error_code"] == -32001
        assert result.wire_error_envelope is None


class TestClientE2eA2aDelivery:
    """``_deliver_e2e_a2a`` (the wire-grading work) — real JSON-RPC
    ``message/send`` HTTP delivery, HTTP layer mocked (unit-level; genuine
    e2e-with-real-server verification happens in tests/e2e/ against a live
    Docker stack, per this task's scope)."""

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

    def test_success_posts_jsonrpc_and_returns_stripped_artifact_data(self):
        from unittest.mock import patch

        import httpx

        from tests.harness.transport import E2EConfig

        class _UnitEnv(BaseTestEnv):
            pass

        artifact_data = self._MINIMAL_GET_PRODUCTS_ARTIFACT
        rpc_response = self._rpc_success_body(artifact_data=artifact_data)

        with _UnitEnv(e2e_config=E2EConfig(base_url="http://e2e-stack:8080", postgres_url="postgresql://x")) as env:
            client = AdCPTestClient(env)
            with patch("httpx.Client") as mock_client_cls:
                mock_response = httpx.Response(200, json=rpc_response)
                mock_post = mock_client_cls.return_value.__enter__.return_value.post
                mock_post.return_value = mock_response

                result = client.call("get_products", {"brief": "video ads"}, Transport.E2E_A2A)

        assert result.is_success, result.error
        # payload is the pinned GetProductsResponse model, not the raw dict —
        # attribute access, not subscripting.
        assert result.payload.products == []
        assert result.wire_response == artifact_data  # unstripped — captured before pop

        # POST went to the real A2A JSON-RPC endpoint with a well-formed envelope.
        call_args = mock_post.call_args
        assert call_args.args[0] == "/a2a"
        rpc_body = json.loads(call_args.kwargs["content"])
        assert rpc_body["jsonrpc"] == "2.0"
        assert rpc_body["method"] == "SendMessage"
        skill_part = rpc_body["params"]["message"]["parts"][0]["data"]
        assert skill_part["skill"] == "get_products"
        assert skill_part["parameters"] == {"brief": "video ads"}

    def test_credential_is_sent_as_auth_and_tenant_headers(self):
        from unittest.mock import patch

        import httpx

        from tests.harness.transport import E2EConfig

        class _UnitEnv(BaseTestEnv):
            pass

        credential = credential_headers(token="tok_123", tenant="t1")
        rpc_response = self._rpc_success_body(artifact_data=self._MINIMAL_GET_PRODUCTS_ARTIFACT)

        with _UnitEnv(e2e_config=E2EConfig(base_url="http://e2e-stack:8080", postgres_url="postgresql://x")) as env:
            client = AdCPTestClient(env)
            with patch("httpx.Client") as mock_client_cls:
                mock_response = httpx.Response(200, json=rpc_response)
                mock_post = mock_client_cls.return_value.__enter__.return_value.post
                mock_post.return_value = mock_response

                result = client.call("get_products", {"brief": "x"}, Transport.E2E_A2A, credential=credential)

        assert result.is_success, result.error
        headers = mock_post.call_args.kwargs["headers"]
        assert headers["Authorization"] == "Bearer tok_123"
        assert headers["x-adcp-tenant"] == "t1"

    def test_unauthenticated_dispatch_sends_no_auth_header(self):
        from unittest.mock import patch

        import httpx

        from tests.harness.transport import E2EConfig

        class _UnitEnv(BaseTestEnv):
            pass

        rpc_response = self._rpc_success_body(artifact_data=self._MINIMAL_GET_PRODUCTS_ARTIFACT)

        with _UnitEnv(e2e_config=E2EConfig(base_url="http://e2e-stack:8080", postgres_url="postgresql://x")) as env:
            client = AdCPTestClient(env)
            with patch("httpx.Client") as mock_client_cls:
                mock_response = httpx.Response(200, json=rpc_response)
                mock_post = mock_client_cls.return_value.__enter__.return_value.post
                mock_post.return_value = mock_response

                client.call("get_products", {"brief": "x"}, Transport.E2E_A2A, credential={})

        headers = mock_post.call_args.kwargs["headers"]
        assert "Authorization" not in headers
        assert "x-adcp-tenant" not in headers

    def test_task_state_failed_surfaces_the_wire_error_envelope_verbatim(self):
        """A failed Task's artifact envelope reaches the caller unchanged.

        Arranged as production emits it: ``AdcpErrorResponse.of`` "Carries the SAME error
        object at both levels the wire expects -- ``adcp_error`` on the envelope and
        ``errors[0]``", which pinned ``core/protocol-envelope.json`` asks for ("a fatal task
        failure SHOULD populate both this envelope-level field AND the payload's ``errors[]``
        array"). ``_wire_envelope`` returns that body and nothing else -- it fills no layer
        and reshapes no body, so this equality grades what the buyer received rather than a
        harness repair of it.
        """
        from unittest.mock import patch

        import httpx

        from tests.harness.transport import E2EConfig

        class _UnitEnv(BaseTestEnv):
            pass

        adcp_error = {"code": "PRODUCT_NOT_FOUND", "message": "no such product", "recovery": "retry"}
        envelope = {"adcp_error": adcp_error, "errors": [adcp_error]}
        rpc_response = {
            "jsonrpc": "2.0",
            "id": "req-1",
            "result": {
                "task": {
                    "id": "task_fail",
                    "status": {"state": "TASK_STATE_FAILED"},
                    "artifacts": [{"artifactId": "art-1", "parts": [{"data": envelope}]}],
                }
            },
        }

        with _UnitEnv(e2e_config=E2EConfig(base_url="http://e2e-stack:8080", postgres_url="postgresql://x")) as env:
            client = AdCPTestClient(env)
            with patch("httpx.Client") as mock_client_cls:
                mock_response = httpx.Response(200, json=rpc_response)
                mock_post = mock_client_cls.return_value.__enter__.return_value.post
                mock_post.return_value = mock_response

                result = client.call("get_products", {"brief": "x"}, Transport.E2E_A2A)

        assert result.is_error
        assert result.wire_error_envelope == envelope

    def test_task_state_submitted_is_rejected(self):
        """The retired status-message frame must fail instead of hiding a writer regression."""
        from unittest.mock import patch

        import httpx

        from tests.harness.transport import E2EConfig

        class _UnitEnv(BaseTestEnv):
            pass

        submitted_payload = {
            "status": "submitted",
            "task_id": "task_submitted_1",
            "message": "Media buy submitted for approval.",
            "context": {"correlation_id": "corr-1"},
        }
        rpc_response = {
            "jsonrpc": "2.0",
            "id": "req-1",
            "result": {
                "task": {
                    "id": "task_submitted_1",
                    "status": {
                        "state": "TASK_STATE_SUBMITTED",
                        "message": {
                            "role": "ROLE_AGENT",
                            "parts": [{"text": "Media buy submitted for approval."}, {"data": submitted_payload}],
                        },
                    },
                    "artifacts": [],
                }
            },
        }

        with _UnitEnv(e2e_config=E2EConfig(base_url="http://e2e-stack:8080", postgres_url="postgresql://x")) as env:
            client = AdCPTestClient(env)
            with patch("httpx.Client") as mock_client_cls:
                mock_response = httpx.Response(200, json=rpc_response)
                mock_post = mock_client_cls.return_value.__enter__.return_value.post
                mock_post.return_value = mock_response

                result = client.call("create_media_buy", {"buyer_ref": "x"}, Transport.E2E_A2A)

        assert result.is_error
        assert result.wire_response is None

    def test_http_error_status_still_surfaces_the_wire_error_envelope(self):
        """An A2A response with an HTTP error status must NOT discard its body.

        change-set B4 : ``_deliver_e2e_a2a``
        calls ``response.raise_for_status()`` BEFORE ``response.json()``, so on
        any >=400 the JSON-RPC error body — the only place the AdCP two-layer
        envelope exists on this leg — is thrown away and the caller gets a bare
        ``httpx.HTTPStatusError``. ``unwrap_a2a_error``
        (``tests/harness/client.py``) then has nothing to read, so
        ``wire_error_envelope`` is ``None`` and every error-path Then step that
        asserts on the wire (``tests/CLAUDE.md`` § Error Verification Policy —
        the wire envelope is the primary authority, reconstructed exceptions
        are lossy) fails for a reason unrelated to the behavior under test.

        The REST sibling already gets this right: ``unwrap_rest_response``
        parses the body on ``status_code >= 400`` and hands it out as
        ``wire_error_envelope``. B4 makes A2A match — parse the body first,
        then let the existing ``"error" in body`` branch reconstruct through
        ``_envelope_to_adcp_error``, which stashes the real envelope on the
        exception for ``_wire_envelope_from_exception`` to pick up.

        Lives here beside the other ``_deliver_e2e_a2a`` graders (same mocked
        -httpx convention, same ``TestClientE2eA2aDelivery`` fixtures) because
        the HTTP-error branch of this DELIVER function is not reachable from a
        BDD scenario without a live Docker stack; the live-stack sibling
        obligation is graded by
        ``tests/integration/test_harness_client_transport_parity.py``
        ``TestEnvVsClientEquivalenceE2E::test_e2e_a2a_success_and_error_equivalence``.
        """
        from unittest.mock import patch

        import httpx

        from tests.harness.transport import E2EConfig

        class _UnitEnv(BaseTestEnv):
            pass

        envelope = {
            "adcp_error": {
                "code": "AUTH_REQUIRED",
                "message": "authentication required",
                "recovery": "correctable",
            },
            "errors": [
                {
                    "code": "AUTH_REQUIRED",
                    "message": "authentication required",
                    "recovery": "correctable",
                }
            ],
        }
        rpc_error_body = {
            "jsonrpc": "2.0",
            "id": "req-1",
            "error": {"code": -32600, "message": "authentication required", "data": envelope},
        }

        with _UnitEnv(e2e_config=E2EConfig(base_url="http://e2e-stack:8080", postgres_url="postgresql://x")) as env:
            client = AdCPTestClient(env)
            with patch("httpx.Client") as mock_client_cls:
                mock_response = httpx.Response(401, json=rpc_error_body)
                mock_post = mock_client_cls.return_value.__enter__.return_value.post
                mock_post.return_value = mock_response

                result = client.call("get_products", {"brief": "x"}, Transport.E2E_A2A, credential={})

        assert result.is_error, f"a 401 must be an error result, got payload {result.payload!r}"
        result.assert_wire_error("AUTH_REQUIRED", recovery="correctable")
        assert result.wire_error_envelope == envelope, (
            "the envelope the 401 body carried must reach the caller INTACT. Shape alone "
            "cannot distinguish 'the body was parsed' from 'something rebuilt an envelope "
            "that happens to look right', and pass-through is the whole subject of this "
            f"test: got {result.wire_error_envelope!r}"
        )

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

    def test_dispatch_with_tool_name_delegates_to_client(self):
        from unittest.mock import patch

        import httpx

        from tests.harness.dispatchers import A2AE2EDispatcher
        from tests.harness.transport import E2EConfig

        class _UnitEnv(BaseTestEnv):
            pass

        rpc_response = TestClientE2eA2aDelivery._rpc_success_body(
            artifact_data=TestClientE2eA2aDelivery._MINIMAL_GET_PRODUCTS_ARTIFACT
        )

        with _UnitEnv(e2e_config=E2EConfig(base_url="http://e2e-stack:8080", postgres_url="postgresql://x")) as env:
            with patch("httpx.Client") as mock_client_cls:
                mock_response = httpx.Response(200, json=rpc_response)
                mock_post = mock_client_cls.return_value.__enter__.return_value.post
                mock_post.return_value = mock_response

                result = A2AE2EDispatcher().dispatch(env, tool_name="get_products", brief="video ads")

        assert result.is_success, result.error
        skill_part = json.loads(mock_post.call_args.kwargs["content"])["params"]["message"]["parts"][0]["data"]
        assert skill_part["skill"] == "get_products"
        assert skill_part["parameters"] == {"brief": "video ads"}


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
