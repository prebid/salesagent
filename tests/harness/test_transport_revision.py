"""Regression checks for shared wire readers and configured-stack failures."""

import json

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


def test_mcp_jsonrpc_message_is_retained():
    assert str(_base._mcp_error_to_exception({"message": "unknown tool"})) == "unknown tool"


@pytest.fixture
def no_stack(monkeypatch):
    for name in (
        "E2E_BASE_URL",
        "E2E_POSTGRES_URL",
        "ADCP_SALES_PORT",
        "POSTGRES_PORT",
        "E2E_TLS_BASE_URL",
        "E2E_CA_BUNDLE",
        "E2E_PER_WORKER",
        "PYTEST_XDIST_WORKER",
    ):
        monkeypatch.delenv(name, raising=False)


def test_absent_stack_is_optional(no_stack):
    assert E2EConfig.from_environment() is None


def test_configured_unreachable_stack_fails(no_stack, monkeypatch):
    monkeypatch.setenv("E2E_BASE_URL", "http://configured.test")
    monkeypatch.setenv("E2E_POSTGRES_URL", "postgresql://test/adcp")

    def refuse(*args, **kwargs):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "get", refuse)
    with pytest.raises(RuntimeError, match="configured.*unreachable"):
        E2EConfig.from_environment()


def test_worker_stack_keeps_tls_and_uses_own_database(no_stack, monkeypatch):
    for key, value in {
        "E2E_BASE_URL": "http://shared.test",
        "E2E_POSTGRES_URL": "postgresql://db/adcp?sslmode=disable",
        "E2E_TLS_BASE_URL": "https://tls.test",
        "E2E_CA_BUNDLE": "/tmp/test-ca.pem",
        "E2E_PER_WORKER": "1",
        "PYTEST_XDIST_WORKER": "gw2",
        "COMPOSE_PROJECT_NAME": "revision",
    }.items():
        monkeypatch.setenv(key, value)
    seen = []

    def healthy(url, **kwargs):
        seen.append(url)
        return httpx.Response(200, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", healthy)
    config = E2EConfig.from_environment()
    assert seen == ["http://revision-server-gw2:8080/health"]
    assert config.postgres_url == "postgresql://db/adcp_gw2?sslmode=disable"
    assert config.tls_base_url == "https://revision-tls-gw2.adcp.test:8443"
    assert config.ca_bundle == "/tmp/test-ca.pem"


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
