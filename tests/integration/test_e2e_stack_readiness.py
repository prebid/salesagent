"""Readiness has no buyer wire: grade the harness against real PostgreSQL/HTTP.

Host cases grade the PostgreSQL probe's TCP-connect contract with an ephemeral
listener; in-network cases use the real PostgreSQL endpoint. Only Compose DNS and diagnostic CLI responses are substituted; every readiness
probe reaches a real endpoint.
"""

import importlib
import os
import shutil
import socket
import subprocess
from collections import Counter

import psycopg2
import pytest

from tests.helpers.local_http_origin import responds, run_local_origin


@pytest.fixture
def readiness():
    return importlib.import_module("tests.e2e.stack_readiness")


@pytest.fixture
def tcp_port():
    # The host probe asks whether TCP connects. A real listener keeps that
    # contract test independent of the suite DB's location (localhost on a
    # developer machine, postgres:5432 inside the Compose runner).
    with run_local_origin() as listener:
        yield listener.port


@pytest.fixture
def closed_port():
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        yield reserved.getsockname()[1]


@pytest.fixture
def creative_agent():
    with run_local_origin() as origin:
        yield origin


@pytest.fixture
def compose(monkeypatch):
    commands = []

    def run(argv, **kwargs):
        commands.append(argv)
        # Compose is for diagnostics only: a readiness poll must never ask it
        # for a JSON health verdict.
        assert "--format" not in argv
        return subprocess.CompletedProcess(argv, 0, "container diagnostic", "")

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/docker" if name == "docker" else None)
    return commands


def wait(readiness, origin, tcp_port, creative_agent, **kwargs):
    readiness.wait_for_e2e_stack(
        ports={"mcp": origin.port, "postgres": tcp_port, "creative_agent": creative_agent.port},
        host=origin.host,
        timeout_s=kwargs.pop("timeout_s", 0.2),
        poll_interval_s=0.02,
        **kwargs,
    )


def test_all_services_ready_returns_without_failure_logs(
    readiness, local_origin, tcp_port, creative_agent, compose, capsys
):
    wait(readiness, local_origin, tcp_port, creative_agent, timeout_s=3)
    assert creative_agent.paths == ["/api/creative-agent/health"]
    assert local_origin.paths == ["/health"]
    assert compose == []
    assert "Dumping compose logs" not in capsys.readouterr().out


def test_postgres_failure_prevents_later_probes(readiness, local_origin, closed_port, creative_agent, compose, capsys):
    with pytest.raises(pytest.fail.Exception, match="failed probe: postgres"):
        wait(readiness, local_origin, closed_port, creative_agent)
    assert creative_agent.hits == 0
    assert local_origin.hits == 0
    assert capsys.readouterr().out.count("Dumping compose logs") == 1


@pytest.mark.parametrize("status", [503, 404, 302])
def test_creative_failure_blocks_healthy_server(
    readiness, local_origin, tcp_port, creative_agent, compose, status, capsys
):
    creative_agent.respond_with(status)
    with pytest.raises(pytest.fail.Exception, match="failed probe: creative-agent"):
        wait(readiness, local_origin, tcp_port, creative_agent)
    assert creative_agent.paths
    assert set(creative_agent.paths) == {"/api/creative-agent/health"}
    assert local_origin.hits == 0
    assert capsys.readouterr().out.count("Dumping compose logs") == 1


def test_creative_connection_refusal_blocks_server(readiness, local_origin, tcp_port, closed_port, compose):
    with pytest.raises(pytest.fail.Exception, match="failed probe: creative-agent"):
        readiness.wait_for_e2e_stack(
            ports={"mcp": local_origin.port, "postgres": tcp_port, "creative_agent": closed_port},
            host="127.0.0.1",
            timeout_s=0.1,
        )
    assert local_origin.hits == 0


def test_server_is_retried_until_it_answers_healthy(readiness, local_origin, tcp_port, creative_agent, compose):
    local_origin.respond_in_sequence([responds(503), responds(200)])
    wait(readiness, local_origin, tcp_port, creative_agent, timeout_s=3)
    assert local_origin.paths == ["/health", "/health"]
    assert creative_agent.paths == ["/api/creative-agent/health", "/api/creative-agent/health"]
    assert compose == []


@pytest.mark.parametrize(
    "missing, failed", [("postgres", "postgres"), ("creative_agent", "creative-agent"), ("mcp", "adcp_health")]
)
def test_missing_port_cannot_report_ready(readiness, local_origin, tcp_port, creative_agent, compose, missing, failed):
    ports = {"mcp": local_origin.port, "postgres": tcp_port, "creative_agent": creative_agent.port}
    del ports[missing]
    with pytest.raises(pytest.fail.Exception, match=f"failed probe: {failed}"):
        readiness.wait_for_e2e_stack(ports=ports, host="127.0.0.1", timeout_s=0.1)
    assert local_origin.hits == 0


def _count_env_reads(monkeypatch):
    real_getenv = os.getenv
    reads = Counter()

    def getenv(key, default=None):
        reads[key] += 1
        return real_getenv(key, default)

    monkeypatch.setattr(os, "getenv", getenv)
    return reads


def test_db_url_fallback_reads_each_setting_once(readiness, monkeypatch):
    monkeypatch.delenv("E2E_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    reads = _count_env_reads(monkeypatch)
    assert readiness.resolve_e2e_db(fallback_host="::1", fallback_port=5456).url == (
        "postgresql://adcp_user:secure_password_change_me@[::1]:5456/adcp"
    )
    assert reads == {"E2E_DATABASE_URL": 1, "DATABASE_URL": 1}


def test_db_url_precedence_and_fallback_match(readiness, monkeypatch):
    monkeypatch.setenv("E2E_DATABASE_URL", "postgresql://u:p@e2e-db:5433/adcp")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@suite-db:5434/test")
    assert readiness.resolve_e2e_db()[1:] == ("e2e-db", 5433, "u", "p", "adcp")
    assert readiness.resolve_e2e_db().url == "postgresql://u:p@e2e-db:5433/adcp"
    monkeypatch.delenv("E2E_DATABASE_URL")
    assert readiness.resolve_e2e_db()[1:] == ("suite-db", 5434, "u", "p", "test")
    monkeypatch.delenv("DATABASE_URL")
    assert readiness.resolve_e2e_db(fallback_port=5456)[1:] == (
        "localhost",
        5456,
        "adcp_user",
        "secure_password_change_me",
        "adcp",
    )
    assert readiness.resolve_e2e_db(fallback_port=5456).url.endswith("@localhost:5456/adcp")


def test_db_record_fills_missing_url_fields_without_rewriting_dsn(readiness, monkeypatch):
    url = "postgresql:///?sslmode=require"
    monkeypatch.setenv("E2E_DATABASE_URL", url)
    assert readiness.resolve_e2e_db(fallback_host="::1", fallback_port=5456) == (
        url,
        "::1",
        5456,
        "adcp_user",
        "secure_password_change_me",
        "adcp",
    )


@pytest.mark.parametrize("configured", [None, "", "42123"])
def test_env_port_is_read_once_and_published_for_compose(monkeypatch, configured):
    from tests.e2e import conftest

    name = "E2E_CREATIVE_AGENT_PORT"
    # Register restoration even when the original variable is absent.
    monkeypatch.setenv(name, configured or "")
    if configured is None:
        monkeypatch.delenv(name)
    reads = _count_env_reads(monkeypatch)
    port = conftest.resolve_env_port(name, 50000, 60000)
    assert reads == {name: 1}
    assert os.environ[name] == str(port)
    if configured:
        assert port == int(configured)
    else:
        assert 50000 <= port < 60000
        with socket.socket() as listener:
            listener.bind(("", port))
    assert conftest.resolve_env_port(name, 50000, 60000) == port


def test_env_port_rejects_invalid_configuration(monkeypatch):
    from tests.e2e import conftest

    monkeypatch.setenv("POSTGRES_PORT", "invalid")
    with pytest.raises(ValueError, match="invalid"):
        conftest.resolve_env_port("POSTGRES_PORT", 25000, 30000)


@pytest.mark.parametrize("agent_status", [200, 503])
def test_in_network_gate_uses_service_addresses(readiness, local_origin, test_database_url, monkeypatch, agent_status):
    """Compose DNS is substituted; both HTTP services and PostgreSQL are real."""
    with psycopg2.connect(test_database_url) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT 1")
        assert cursor.fetchone() == (1,)
    with run_local_origin() as creative_agent:
        creative_agent.respond_with(agent_status)
        real_lookup = socket.getaddrinfo
        addresses = {("proxy", 8000): local_origin.port, ("creative-agent", 8080): creative_agent.port}

        def lookup(host, port, *args, **kwargs):
            target = addresses.get((host, port))
            if target is not None:
                return real_lookup("127.0.0.1", target, *args, **kwargs)
            return real_lookup(host, port, *args, **kwargs)

        monkeypatch.setattr(socket, "getaddrinfo", lookup)
        monkeypatch.setenv("NO_PROXY", "proxy,creative-agent")
        monkeypatch.setenv("E2E_DATABASE_URL", test_database_url)
        monkeypatch.setenv("ADCP_TEST_HOST", "proxy")
        # The in-network route must work without a host-published DB port and
        # without reaching for a Docker CLI on the runner.
        monkeypatch.setattr(shutil, "which", lambda name: None)
        ports = readiness.e2e_ports(mcp=8000, postgres=1, creative_agent=1)
        if agent_status == 200:
            readiness.wait_for_e2e_stack(ports=ports, timeout_s=3)
            assert local_origin.paths == ["/health"]
        else:
            with pytest.raises(pytest.fail.Exception, match="failed probe: creative-agent"):
                readiness.wait_for_e2e_stack(ports=ports, timeout_s=0.2, poll_interval_s=0.02)
            assert local_origin.hits == 0
        assert creative_agent.paths
        assert set(creative_agent.paths) == {"/api/creative-agent/health"}


@pytest.mark.parametrize(
    "docker, legacy, version, expected",
    [
        (True, True, 0, ["docker", "compose"]),
        (True, True, 1, ["docker-compose"]),
        (False, True, 0, ["docker-compose"]),
        (True, False, 1, ["docker", "compose"]),
        (False, False, 0, ["docker", "compose"]),
        (True, True, OSError("plugin unavailable"), ["docker-compose"]),
        (True, True, subprocess.TimeoutExpired("docker compose version", 5), ["docker-compose"]),
    ],
)
def test_compose_selection_preserves_exec_and_teardown_flags(readiness, monkeypatch, docker, legacy, version, expected):
    def which(name):
        present = {"docker": docker, "docker-compose": legacy}
        return f"/usr/bin/{name}" if present.get(name) else None

    def run(argv, **kwargs):
        assert argv == ["docker", "compose", "version"]
        if isinstance(version, Exception):
            raise version
        return subprocess.CompletedProcess(argv, version, "", "")

    monkeypatch.setattr(shutil, "which", which)
    monkeypatch.setattr(subprocess, "run", run)
    assert readiness.compose_available() is (docker or legacy)
    assert readiness.compose_exec_argv("adcp-server", "python", "-c", "x") == [
        *expected,
        "-f",
        "docker-compose.e2e.yml",
        "exec",
        "-T",
        "adcp-server",
        "python",
        "-c",
        "x",
    ]
    assert readiness.compose_down_argv() == [*expected, "-f", "docker-compose.e2e.yml", "down", "-v"]
