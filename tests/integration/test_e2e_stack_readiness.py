"""Readiness has no buyer wire: grade the harness against real PostgreSQL/HTTP.

Host cases grade the PostgreSQL probe's TCP-connect contract with an ephemeral
listener; in-network cases use the real PostgreSQL endpoint. Only Docker CLI
responses and Compose DNS are substituted, without patching our own probes.
"""

import importlib
import json
import shutil
import socket
import subprocess
from types import SimpleNamespace

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
def compose(monkeypatch):
    state = SimpleNamespace(payload='{"Health":"healthy"}', returncode=0, commands=[])
    real_run, real_which = subprocess.run, shutil.which

    def run(argv, **kwargs):
        if argv[:2] != ["docker", "compose"]:
            return real_run(argv, **kwargs)
        state.commands.append(argv)
        if argv[2:] == ["version"]:
            return subprocess.CompletedProcess(argv, 0, "Docker Compose version v2", "")
        if "--format" in argv:
            assert argv[-4:] == ["ps", "--format", "json", "creative-agent"]
            return subprocess.CompletedProcess(argv, state.returncode, state.payload, "compose diagnostic")
        return subprocess.CompletedProcess(argv, 0, "container diagnostic", "")

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/docker" if name == "docker" else real_which(name))
    return state


def wait(readiness, origin, tcp_port, **kwargs):
    readiness.wait_for_e2e_stack(
        ports=readiness.e2e_ports(mcp=origin.port, postgres=tcp_port),
        host=origin.host,
        timeout_s=kwargs.pop("timeout_s", 0.2),
        poll_interval_s=0.02,
        **kwargs,
    )


def test_all_services_ready_returns_without_failure_logs(readiness, local_origin, tcp_port, compose, capsys):
    wait(readiness, local_origin, tcp_port, timeout_s=3)
    assert local_origin.paths == ["/health"]
    assert "Dumping compose logs" not in capsys.readouterr().out


def test_postgres_failure_prevents_later_probes(readiness, local_origin, closed_port, compose, capsys):
    compose.payload = '{"Health":"unhealthy"}'
    local_origin.respond_with(status=503)
    with pytest.raises(pytest.fail.Exception, match="failed probe: postgres"):
        wait(readiness, local_origin, closed_port)
    assert local_origin.hits == 0
    assert not any("--format" in argv for argv in compose.commands)
    assert capsys.readouterr().out.count("Dumping compose logs") == 1


@pytest.mark.parametrize(
    "payload, diagnostic",
    [
        ('{"Health":"unhealthy"}', "unhealthy"),
        ('{"Health":"","Status":"Up 2 minutes (unhealthy)"}', "unhealthy"),
        ('{"Health":"starting"}', "starting"),
        ('{"State":"running"}', "unknown"),
        ("not json", "no parseable records"),
    ],
)
def test_creative_failure_blocks_healthy_server(
    readiness, local_origin, tcp_port, compose, payload, diagnostic, capsys
):
    compose.payload = payload
    with pytest.raises(pytest.fail.Exception, match="failed probe: creative-agent") as failure:
        wait(readiness, local_origin, tcp_port)
    assert diagnostic in str(failure.value)
    assert local_origin.hits == 0
    assert capsys.readouterr().out.count("Dumping compose logs") == 1


@pytest.mark.parametrize("encoding", ["object", "array", "ndjson"])
def test_compose_json_encodings_reach_server(readiness, local_origin, tcp_port, compose, encoding):
    record = {"Health": "healthy"}
    compose.payload = {
        "object": json.dumps(record),
        "array": json.dumps([record]),
        "ndjson": json.dumps(record) + "\n" + json.dumps(record),
    }[encoding]
    wait(readiness, local_origin, tcp_port, timeout_s=3)
    assert local_origin.paths == ["/health"]


def test_server_is_retried_until_it_answers_healthy(readiness, local_origin, tcp_port, compose):
    local_origin.respond_in_sequence([responds(503), responds(200)])
    wait(readiness, local_origin, tcp_port, timeout_s=3)
    assert local_origin.paths == ["/health", "/health"]


def test_legacy_utility_runs_the_same_gate(local_origin, tcp_port, compose):
    from tests.e2e.utils import wait_for_server_readiness

    compose.payload = '{"Health":"unhealthy"}'
    with pytest.raises(pytest.fail.Exception, match="failed probe: creative-agent"):
        wait_for_server_readiness(local_origin.base_url, postgres_port=tcp_port, timeout=0.2)
    assert local_origin.hits == 0


def test_missing_tcp_port_cannot_degrade_to_compose_health(readiness, local_origin, compose):
    with pytest.raises(pytest.fail.Exception, match="failed probe: postgres"):
        readiness.wait_for_e2e_stack(ports={"mcp": local_origin.port}, host=local_origin.host, timeout_s=0.1)
    assert local_origin.hits == 0


def test_missing_server_port_cannot_report_ready(readiness, tcp_port, compose):
    with pytest.raises(pytest.fail.Exception, match="failed probe: adcp_health"):
        readiness.wait_for_e2e_stack(ports={"postgres": tcp_port}, host="127.0.0.1", timeout_s=0.1)


def test_db_url_precedence_and_fallback_match(readiness, monkeypatch):
    monkeypatch.setenv("E2E_DATABASE_URL", "postgresql://u:p@e2e-db:5433/adcp")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@suite-db:5434/test")
    assert readiness.resolve_e2e_db_endpoint() == ("e2e-db", 5433)
    assert readiness.e2e_db_url_default() == "postgresql://u:p@e2e-db:5433/adcp"
    monkeypatch.delenv("E2E_DATABASE_URL")
    assert readiness.resolve_e2e_db_endpoint() == ("suite-db", 5434)
    monkeypatch.delenv("DATABASE_URL")
    assert readiness.resolve_e2e_db_endpoint(fallback_port=5456) == ("localhost", 5456)
    assert readiness.e2e_db_url_default(fallback_port=5456).endswith("@localhost:5456/adcp")


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
        ports = readiness.e2e_ports(mcp=8000, postgres=1)
        if agent_status == 200:
            readiness.wait_for_e2e_stack(ports=ports, timeout_s=3)
            assert local_origin.paths == ["/health"]
        else:
            with pytest.raises(pytest.fail.Exception, match="failed probe: creative-agent"):
                readiness.wait_for_e2e_stack(ports=ports, timeout_s=0.2, poll_interval_s=0.02)
            assert local_origin.hits == 0
        assert creative_agent.paths
        assert set(creative_agent.paths) == {"/api/creative-agent/health"}


@pytest.mark.parametrize("url", ["http://localhost", "/missing-host:8092"])
def test_utility_rejects_an_unresolved_server_url(url):
    from tests.e2e.utils import wait_for_server_readiness

    with pytest.raises(pytest.fail.Exception, match="explicit port"):
        wait_for_server_readiness(url, postgres_port=5432)


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
