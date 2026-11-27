"""Regression checks for shared wire readers and configured-stack failures."""

import json
import ssl

import httpx
import pytest

from tests.harness import _base
from tests.harness.transport import E2EConfig
from tests.harness.wire_fixtures import wire_error_result


@pytest.mark.parametrize("sse", [False, True])
@pytest.mark.parametrize("frame", [{"error": "unauthorized"}, {"error": {"message": "bad method"}}])
def test_mcp_protocol_refusal_preserves_response_and_message(frame, sse):
    response = httpx.Response(
        401,
        content=("data: " + json.dumps(frame) + "\n\n") if sse else json.dumps(frame),
        headers={"Content-Type": "text/event-stream" if sse else "application/json"},
    )
    with pytest.raises(_base.WireRefusal) as caught:
        _base._mcp_jsonrpc_result(response)
    assert caught.value.response is response
    assert ("unauthorized" if isinstance(frame["error"], str) else "bad method") in str(caught.value)


def _frame_response(frame):
    return httpx.Response(200, json=frame)


@pytest.mark.parametrize("path", ["structured", "data", "text", "payload"])
def test_mcp_keeps_each_spec_error_envelope_verbatim(path):
    envelope = {"adcp_error": {"code": "AUTH_MISSING", "recovery": "correctable"}}
    if path == "structured":
        frame = {"result": {"isError": True, "structuredContent": envelope}}
    elif path == "data":
        frame = {"error": {"code": -32028, "data": envelope}}
    elif path == "text":
        frame = {"result": {"isError": True, "content": [{"type": "text", "text": json.dumps(envelope)}]}}
    else:
        envelope = {"errors": [{"code": "AUTH_MISSING"}]}
        frame = {"result": {"isError": True, "structuredContent": envelope}}
    response = _frame_response(frame)
    with pytest.raises(_base.WireError) as caught:
        _base._mcp_jsonrpc_result(response)
    assert caught.value.envelope == envelope
    assert caught.value.response is response


def test_mcp_prefers_structured_error_to_conflicting_text():
    structured = {"adcp_error": {"code": "AUTH_MISSING"}}
    response = _frame_response(
        {
            "result": {
                "isError": True,
                "structuredContent": structured,
                "content": [{"type": "text", "text": json.dumps({"adcp_error": {"code": "AUTH_INVALID"}})}],
            }
        }
    )
    with pytest.raises(_base.WireError) as caught:
        _base._mcp_jsonrpc_result(response)
    assert caught.value.envelope == structured


@pytest.mark.parametrize("text", ["('AUTH_MISSING', 'missing token')", "plain refusal"])
def test_mcp_does_not_reconstruct_an_envelope_from_legacy_text(text):
    from fastmcp.exceptions import ToolError

    assert _base._mcp_wire_error(ToolError(text)).__class__ is ToolError
    with pytest.raises(_base.WireRefusal):
        _base._mcp_jsonrpc_result(
            _frame_response(
                {
                    "result": {
                        "isError": True,
                        "content": [{"type": "text", "text": text}],
                    }
                }
            )
        )


def test_mcp_does_not_read_jsonrpc_message_as_an_error_envelope():
    envelope = {"adcp_error": {"code": "AUTH_MISSING"}, "errors": [{"code": "AUTH_MISSING"}]}
    with pytest.raises(_base.WireRefusal):
        _base._mcp_jsonrpc_result(_frame_response({"error": {"message": json.dumps(envelope)}}))


def test_mcp_success_text_is_not_an_error():
    result = {"content": [{"type": "text", "text": json.dumps({"adcp_error": {"code": "AUTH_MISSING"}})}]}
    assert _base._mcp_jsonrpc_result(_frame_response({"result": result})) == result


@pytest.mark.parametrize("code", [None, 3, "", "a" * 65])
def test_mcp_rejects_malformed_error_codes(code):
    with pytest.raises(_base.WireRefusal):
        _base._mcp_jsonrpc_result(
            _frame_response(
                {
                    "result": {
                        "isError": True,
                        "structuredContent": {"adcp_error": {"code": code}},
                    }
                }
            )
        )


@pytest.mark.parametrize(
    "message,accepted", [("界" * 1000, True), ("a" * 5000, False)], ids=["compact-utf8", "oversized"]
)
def test_mcp_error_limit_counts_compact_utf8_bytes(message, accepted):
    envelope = {"adcp_error": {"code": "AUTH_MISSING", "message": message}}
    response = _frame_response({"result": {"isError": True, "structuredContent": envelope}})
    expected = _base.WireError if accepted else _base.WireRefusal
    with pytest.raises(expected) as caught:
        _base._mcp_jsonrpc_result(response)
    if accepted:
        assert caught.value.envelope == envelope


@pytest.mark.parametrize("first", [None, {"code": ""}])
def test_mcp_payload_fallback_grades_the_first_error_only(first):
    envelope = {"errors": [first, {"code": "AUTH_MISSING"}]}
    response = _frame_response({"result": {"isError": True, "structuredContent": envelope}})
    with pytest.raises(_base.WireRefusal):
        _base._mcp_jsonrpc_result(response)


def _refuse_probe(monkeypatch, failure):
    def refuse(*args, **kwargs):
        raise failure

    monkeypatch.setattr(httpx, "get", refuse)


@pytest.fixture
def no_stack(monkeypatch):
    for name in (
        "E2E_BASE_URL",
        "E2E_POSTGRES_URL",
        "ADCP_SALES_PORT",
        "POSTGRES_PORT",
        "E2E_TLS_BASE_URL",
        "E2E_CA_BUNDLE",
        "E2E_SERVER_HOST",
        "COMPOSE_PROJECT_NAME",
        "E2E_PER_WORKER",
        "PYTEST_XDIST_WORKER",
    ):
        monkeypatch.delenv(name, raising=False)


def test_absent_stack_is_optional(no_stack):
    assert E2EConfig.from_environment() is None


@pytest.mark.parametrize(
    "failure",
    [httpx.ConnectError("connection refused"), httpx.ConnectTimeout("timed out"), httpx.ReadTimeout("timed out")],
)
def test_configured_unreachable_stack_fails(no_stack, monkeypatch, failure):
    monkeypatch.setenv("E2E_BASE_URL", "http://configured.test")
    monkeypatch.setenv("E2E_POSTGRES_URL", "postgresql://test/adcp")

    _refuse_probe(monkeypatch, failure)
    with pytest.raises(RuntimeError, match="configured.*unreachable"):
        E2EConfig.from_environment()


@pytest.mark.parametrize(
    "worker,expected_base_url,postgres_url,tls_base_url,server_host",
    [
        ("", "http://shared.test", "postgresql://db/adcp?sslmode=disable", "https://tls.test", "shared-server"),
        (
            "gw2",
            "http://revision-server-gw2:8080",
            "postgresql://db/adcp_gw2?sslmode=disable",
            "https://revision-tls-gw2.adcp.test:8443",
            "revision-server-gw2",
        ),
    ],
)
@pytest.mark.parametrize("per_worker", [False, True])
def test_stack_keeps_tls_database_and_seller_host(
    no_stack, monkeypatch, worker, expected_base_url, postgres_url, tls_base_url, server_host, per_worker
):
    for key, value in {
        "E2E_BASE_URL": "http://shared.test",
        "E2E_POSTGRES_URL": "postgresql://db/adcp?sslmode=disable",
        "E2E_TLS_BASE_URL": "https://tls.test",
        "E2E_CA_BUNDLE": "/tmp/test-ca.pem",
        "E2E_SERVER_HOST": "shared-server",
        "E2E_PER_WORKER": "1",
        "PYTEST_XDIST_WORKER": worker,
        "COMPOSE_PROJECT_NAME": "revision",
    }.items():
        monkeypatch.setenv(key, value)
    seen = []

    def healthy(url, **kwargs):
        seen.append(url)
        return httpx.Response(200, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", healthy)
    if not per_worker:
        expected_base_url = "http://shared.test"
        postgres_url = "postgresql://db/adcp?sslmode=disable"
        tls_base_url = "https://tls.test"
        server_host = "shared-server"
    config = E2EConfig.from_environment(per_worker=per_worker)
    assert seen == [f"{expected_base_url}/health"]
    assert config.base_url == expected_base_url
    assert config.postgres_url == postgres_url
    assert config.tls_base_url == tls_base_url
    assert config.ca_bundle == "/tmp/test-ca.pem"
    assert config.server_host == server_host


@pytest.mark.parametrize("status,header", [(200, "Bearer"), (401, None), (401, 'Signature error="wrong"')])
def test_auth_challenge_rejects_missing_or_wrong_wire_evidence(status, header):
    response = httpx.Response(status, headers={} if header is None else {"WWW-Authenticate": header})
    result = wire_error_result(
        {"errors": [{"code": "AUTH_MISSING"}]}, error=RuntimeError("refused"), raw_response=response
    )
    with pytest.raises(AssertionError):
        result.assert_auth_challenge("AUTH_MISSING")


def test_auth_challenge_requires_an_http_response():
    with pytest.raises(AssertionError, match="HTTP response"):
        wire_error_result(None, error=RuntimeError("no wire")).assert_auth_challenge("AUTH_MISSING")


def test_auth_challenge_accepts_observed_bearer_refusal():
    result = wire_error_result(
        {"errors": [{"code": "AUTH_MISSING"}]},
        error=RuntimeError("refused"),
        raw_response=httpx.Response(401, headers={"WWW-Authenticate": "Bearer"}),
    )
    result.assert_auth_challenge("AUTH_MISSING")


def test_leftover_ports_do_not_configure_a_stack(no_stack, monkeypatch):
    monkeypatch.setenv("ADCP_SALES_PORT", "12345")
    monkeypatch.setenv("POSTGRES_PORT", "5432")
    assert E2EConfig.from_environment() is None


def test_configured_stack_requires_its_database_url(no_stack, monkeypatch):
    monkeypatch.setenv("E2E_BASE_URL", "http://configured.test")
    with pytest.raises(RuntimeError, match="requires E2E_POSTGRES_URL"):
        E2EConfig.from_environment()


@pytest.fixture
def configured_stack(no_stack, monkeypatch):
    url = "https://probe-host.adcp.test:8443"
    monkeypatch.setenv("E2E_BASE_URL", url)
    monkeypatch.setenv("E2E_POSTGRES_URL", "postgresql://db/adcp")
    monkeypatch.setenv("E2E_CA_BUNDLE", "/app/.test-tls/ca.pem")
    return url


@pytest.mark.parametrize("wrapped", [False, True])
def test_tls_failure_keeps_the_url_ca_and_underlying_error(configured_stack, monkeypatch, wrapped):
    cause = ssl.SSLCertVerificationError(1, "certificate verify failed")
    failure = httpx.ConnectError("certificate verify failed") if wrapped else cause
    if wrapped:
        failure.__cause__ = cause

    _refuse_probe(monkeypatch, failure)
    with pytest.raises(RuntimeError) as caught:
        E2EConfig.from_environment()
    assert configured_stack in str(caught.value)
    assert "E2E_CA_BUNDLE" in str(caught.value)
    assert "/app/.test-tls/ca.pem" in str(caught.value)
    assert caught.value.__cause__ is failure
    assert (failure.__cause__ if wrapped else failure) is cause


def test_unrecognized_stack_probe_error_propagates(configured_stack, monkeypatch):
    _refuse_probe(monkeypatch, ValueError("probe bug"))
    with pytest.raises(ValueError, match="probe bug"):
        E2EConfig.from_environment()


def test_mcp_ignores_failed_optional_discovery_when_call_succeeds(monkeypatch):
    import httpx2

    from tests.harness._base import BaseTestEnv
    from tests.harness.address_table import ADDRESS_TABLE
    from tests.harness.client import _deliver_e2e_mcp
    from tests.harness.transport import Transport
    from tests.helpers.mcp_http import mcp_handshake_response, mock_mcp_http

    def respond(request):
        if request.method != "POST":
            return httpx2.Response(405)
        frame = json.loads(request.content)
        handshake = mcp_handshake_response(frame, discover_status=404)
        if handshake is not None:
            return handshake
        return httpx2.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": frame["id"],
                "result": {
                    "content": [{"type": "text", "text": "{}"}],
                    "structuredContent": {"products": []},
                },
            },
        )

    mock_mcp_http(monkeypatch, respond)
    env = BaseTestEnv(e2e_config=E2EConfig(base_url="http://transport-test", postgres_url="postgresql://unused/db"))
    result = _deliver_e2e_mcp(env, ADDRESS_TABLE.resolve("get_products", Transport.E2E_MCP), {}, {})
    assert result.wire_response == {"products": []}


def test_mcp_payload_fallback_does_not_preempt_transport_error_text():
    payload = {"errors": [{"code": "AUTH_INVALID"}]}
    transport = {"adcp_error": {"code": "AUTH_MISSING"}}
    response = _frame_response(
        {
            "result": {
                "isError": True,
                "structuredContent": payload,
                "content": [
                    {"type": "text", "text": json.dumps(payload)},
                    {"type": "text", "text": json.dumps(transport)},
                ],
            }
        }
    )
    with pytest.raises(_base.WireError) as caught:
        _base._mcp_jsonrpc_result(response)
    assert caught.value.envelope == transport


@pytest.mark.parametrize(
    "text", ["plain refusal", '{"adcp_error":{"code":"AUTH_INVALID"},"errors":[{"code":"AUTH_INVALID"}]}']
)
@pytest.mark.parametrize("placement", ["structured", "jsonrpc"])
@pytest.mark.parametrize("status", [200, 401])
def test_sdk_error_preserves_received_envelope_before_text(monkeypatch, text, placement, status):
    import httpx2

    from tests.harness._base import BaseTestEnv
    from tests.harness.address_table import ADDRESS_TABLE
    from tests.harness.client import _deliver_e2e_mcp
    from tests.harness.transport import Transport
    from tests.helpers.mcp_http import mcp_handshake_response, mock_mcp_http

    entry = {"code": "RATE_LIMITED", "message": "Rate limited", "recovery": "transient", "retry_after": 5}
    structured = {"adcp_error": entry, "errors": [entry], "marker": "received structured body"}

    def respond(request):
        if request.method != "POST":
            return httpx2.Response(405)
        frame = json.loads(request.content)
        handshake = mcp_handshake_response(frame)
        if handshake is not None:
            return handshake
        body = (
            {
                "result": {
                    "isError": True,
                    "structuredContent": structured,
                    "content": [{"type": "text", "text": text}],
                }
            }
            if placement == "structured"
            else {"error": {"code": -32028, "message": text, "data": structured}}
        )
        return httpx2.Response(status, json={"jsonrpc": "2.0", "id": frame["id"], **body})

    mock_mcp_http(monkeypatch, respond)
    env = BaseTestEnv(e2e_config=E2EConfig(base_url="http://transport-test", postgres_url="postgresql://unused/db"))
    with pytest.raises(_base.WireError) as caught:
        _deliver_e2e_mcp(env, ADDRESS_TABLE.resolve("get_products", Transport.E2E_MCP), {}, {})
    assert caught.value.envelope == structured
