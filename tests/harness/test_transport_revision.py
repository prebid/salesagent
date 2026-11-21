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


@pytest.mark.parametrize(
    "text", [json.dumps({"errors": [{"code": "AUTH_MISSING"}]}), "('AUTH_MISSING', 'missing token')"]
)
@pytest.mark.parametrize("frame_kind", ["error", "result"])
def test_mcp_http_and_tool_error_text_have_the_same_envelope(text, frame_kind):
    from fastmcp.exceptions import ToolError

    expected = _base._mcp_wire_envelope(ToolError(text))
    payload = {"isError": True, "content": [{"type": "text", "text": text}]}
    frame = {frame_kind: payload}
    response = httpx.Response(200, json=frame)
    with pytest.raises(_base.WireError) as caught:
        _base._mcp_jsonrpc_result(response)
    assert caught.value.envelope == expected
    assert caught.value.response is response


def test_mcp_jsonrpc_message_is_retained():
    assert _base._mcp_error_text({"message": "unknown tool"}) == "unknown tool"


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

    def refuse(*args, **kwargs):
        raise failure

    monkeypatch.setattr(httpx, "get", refuse)
    with pytest.raises(RuntimeError, match="configured.*unreachable"):
        E2EConfig.from_environment()


@pytest.mark.parametrize(
    "worker,base_url,postgres_url,tls_base_url,server_host",
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
def test_stack_keeps_tls_database_and_seller_host(
    no_stack, monkeypatch, worker, base_url, postgres_url, tls_base_url, server_host
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
    config = E2EConfig.from_environment()
    assert seen == [f"{base_url}/health"]
    assert config.base_url == base_url
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

    def refuse(*args, **kwargs):
        raise failure

    monkeypatch.setattr(httpx, "get", refuse)
    with pytest.raises(RuntimeError) as caught:
        E2EConfig.from_environment()
    assert configured_stack in str(caught.value)
    assert "E2E_CA_BUNDLE" in str(caught.value)
    assert "/app/.test-tls/ca.pem" in str(caught.value)
    assert caught.value.__cause__ is failure
    assert (failure.__cause__ if wrapped else failure) is cause


def test_unrecognized_stack_probe_error_propagates(configured_stack, monkeypatch):
    def refuse(*args, **kwargs):
        raise ValueError("probe bug")

    monkeypatch.setattr(httpx, "get", refuse)
    with pytest.raises(ValueError, match="probe bug"):
        E2EConfig.from_environment()
