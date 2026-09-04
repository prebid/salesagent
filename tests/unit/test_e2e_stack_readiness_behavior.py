"""Behavioral oracles for E2E compose health readiness (host-path contract).

Complements the AST guard in ``test_architecture_e2e_stack_readiness``: that
module pins wiring; this module proves the success predicate and wait failure
naming so a dead ``"running"`` fallback cannot silently pass probes.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from unittest.mock import MagicMock, call, patch

import httpx
import pytest

from tests.e2e.stack_readiness import (
    _PROBE_FUNCS,
    BASE_E2E_COMPOSE_FILE,
    REQUIRED_E2E_PROBES,
    ServiceHealth,
    _compose_reports_ready,
    _compose_service_health,
    _http_authority,
    _Probe,
    _probe_adcp_health,
    _probe_creative_agent,
    _probe_postgres,
    _wait_for_probes,
    compose_argv,
    compose_available,
    compose_down_argv,
    compose_exec_argv,
    e2e_db_url_build,
    e2e_db_url_default,
    e2e_host_default,
    e2e_ports,
    health_of,
    in_network,
    use_container_exec,
    wait_for_e2e_stack,
)
from tests.e2e.utils import wait_for_server_readiness


def _ps_completed(stdout: str, *, returncode: int = 0) -> MagicMock:
    result = MagicMock()
    result.returncode = returncode
    result.stdout = stdout
    result.stderr = ""
    return result


@contextmanager
def _compose_ps_patches(payload: str, *, returncode: int = 0) -> Iterator[None]:
    """Shared compose-available + subprocess.run patch seam for health oracles."""
    with (
        patch("tests.e2e.stack_readiness.compose_available", return_value=True),
        patch(
            "tests.e2e.stack_readiness.subprocess.run",
            return_value=_ps_completed(payload, returncode=returncode),
        ),
    ):
        yield


def _health_for(payload: str, *, returncode: int = 0) -> tuple[ServiceHealth | None, str | None]:
    """Patch compose ps and return ``_compose_service_health`` for creative-agent."""
    with _compose_ps_patches(payload, returncode=returncode):
        return _compose_service_health("creative-agent", ("docker-compose.e2e.yml",))


def _stub_probes(
    *,
    postgres: bool = True,
    creative_agent: bool = True,
    adcp_health: bool = True,
) -> dict[str, _Probe]:
    """Build a stub ``_PROBE_FUNCS`` overlay keyed from ``REQUIRED_E2E_PROBES``."""
    outcomes = {
        "postgres": postgres,
        "creative-agent": creative_agent,
        "adcp_health": adcp_health,
    }
    assert frozenset(outcomes) == frozenset(REQUIRED_E2E_PROBES), (
        f"_stub_probes keys {sorted(outcomes)} must match REQUIRED_E2E_PROBES {list(REQUIRED_E2E_PROBES)}"
    )

    def _make(ok: bool) -> _Probe:
        def _probe(**_kw: object) -> bool:
            return ok

        return _probe

    return {name: _make(outcomes[name]) for name in REQUIRED_E2E_PROBES}


class TestHealthOfContract:
    """Pure ``health_of`` table — no mocks; ``unhealthy`` must not grant ready."""

    @pytest.mark.parametrize(
        ("record", "expected"),
        [
            ({"Health": "healthy", "State": "running"}, ServiceHealth.HEALTHY),
            ({"Health": "unhealthy", "State": "running"}, ServiceHealth.UNHEALTHY),
            ({"Health": "starting", "State": "running"}, ServiceHealth.STARTING),
            ({"Health": "weird", "State": "running"}, ServiceHealth.UNKNOWN),
            ({"Health": "", "State": "running"}, ServiceHealth.UNKNOWN),
            ({"Health": "", "State": "running (healthy)"}, ServiceHealth.HEALTHY),
            ({"Health": "", "State": "running (unhealthy)"}, ServiceHealth.UNHEALTHY),
            ({"Health": "", "State": "running (starting)"}, ServiceHealth.STARTING),
            ({"Health": "", "Status": "Up 5 seconds (healthy)"}, ServiceHealth.HEALTHY),
            ({"Health": "", "Status": "Up"}, ServiceHealth.UNKNOWN),
            ({"Health": "", "State": "running", "Status": "Up"}, ServiceHealth.UNKNOWN),
        ],
    )
    def test_health_of_table(self, record, expected):
        assert health_of(record) is expected
        assert _compose_reports_ready(health_of(record)) is (expected is ServiceHealth.HEALTHY)

    def test_only_healthy_enum_counts_as_ready(self):
        assert _compose_reports_ready(ServiceHealth.HEALTHY) is True
        assert _compose_reports_ready(ServiceHealth.UNHEALTHY) is False
        assert _compose_reports_ready(ServiceHealth.STARTING) is False
        assert _compose_reports_ready(ServiceHealth.UNKNOWN) is False
        assert _compose_reports_ready(None) is False

    def test_empty_health_running_state_is_not_ready(self):
        payload = json.dumps({"Health": "", "State": "running", "Name": "creative-agent"})
        health, err = _health_for(payload)
        assert health is ServiceHealth.UNKNOWN
        assert err is None
        assert _compose_reports_ready(health) is False

    def test_empty_health_unhealthy_state_is_not_ready_at_parser(self):
        payload = json.dumps({"Health": "", "State": "running (unhealthy)", "Name": "creative-agent"})
        health, err = _health_for(payload)
        assert health is ServiceHealth.UNHEALTHY
        assert err is None
        assert _compose_reports_ready(health) is False

    def test_empty_health_state_containing_healthy_is_ready(self):
        payload = json.dumps({"Health": "", "State": "running (healthy)", "Name": "creative-agent"})
        health, err = _health_for(payload)
        assert health is ServiceHealth.HEALTHY
        assert err is None
        assert _compose_reports_ready(health) is True

    def test_json_array_payload_parses_first_record(self):
        payload = json.dumps([{"Health": "healthy", "Name": "creative-agent"}, {"Health": "unhealthy"}])
        health, err = _health_for(payload)
        assert health is ServiceHealth.HEALTHY
        assert err is None

    def test_ndjson_payload_parses_via_line_fallback(self):
        ndjson = (
            json.dumps({"Health": "healthy", "Name": "creative-agent"})
            + "\n"
            + json.dumps({"Health": "unhealthy", "Name": "other"})
        )
        health, err = _health_for(ndjson)
        assert health is ServiceHealth.HEALTHY
        assert err is None

    def test_rc_nonzero_returns_none_health_with_diagnostic(self):
        health, err = _health_for("", returncode=1)
        assert health is None
        assert err is not None
        assert "failed" in err

    def test_host_creative_agent_probe_rejects_running_only(self):
        payload = json.dumps({"Health": "", "Status": "Up", "Name": "creative-agent"})
        with _compose_ps_patches(payload):
            assert (
                _probe_creative_agent(
                    ports=e2e_ports(mcp=8000, postgres=5432),
                    host="localhost",
                    compose_files=("docker-compose.e2e.yml",),
                )
                is False
            )

    def test_host_creative_agent_probe_rejects_unhealthy_state(self):
        payload = json.dumps({"Health": "", "State": "running (unhealthy)", "Name": "creative-agent"})
        with _compose_ps_patches(payload):
            assert (
                _probe_creative_agent(
                    ports=e2e_ports(mcp=8000, postgres=5432),
                    host="localhost",
                    compose_files=("docker-compose.e2e.yml",),
                )
                is False
            )

    def test_probe_creative_agent_host_uses_compose_ready(self):
        with patch("tests.e2e.stack_readiness._compose_service_ready", return_value=True) as ready:
            assert (
                _probe_creative_agent(
                    ports=e2e_ports(mcp=8000, postgres=5432),
                    host="localhost",
                    compose_files=("docker-compose.e2e.yml",),
                )
                is True
            )
        ready.assert_called_once_with("creative-agent", ("docker-compose.e2e.yml",))


def _which_docker_and_compose(name: str) -> str | None:
    """Shared ``shutil.which`` side-effect for compose argv-selection oracles."""
    return f"/usr/bin/{name}" if name in {"docker", "docker-compose"} else None


class TestComposeArgvSelection:
    """Compose V2 plugin must win over a legacy docker-compose V1 binary."""

    def test_prefers_docker_compose_plugin_when_version_ok(self):
        version_ok = _ps_completed("Docker Compose version v2.24.0", returncode=0)

        with (
            patch("tests.e2e.stack_readiness.shutil.which", side_effect=_which_docker_and_compose),
            patch("tests.e2e.stack_readiness.subprocess.run", return_value=version_ok) as run,
        ):
            argv = compose_argv(("docker-compose.e2e.yml",))
        assert argv[:2] == ["docker", "compose"]
        run.assert_called_once_with(
            ["docker", "compose", "version"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )

    def test_falls_back_to_docker_compose_v1_when_plugin_probe_fails(self):
        version_fail = _ps_completed("", returncode=1)

        with (
            patch("tests.e2e.stack_readiness.shutil.which", side_effect=_which_docker_and_compose),
            patch("tests.e2e.stack_readiness.subprocess.run", return_value=version_fail),
        ):
            argv = compose_argv(("docker-compose.e2e.yml",))
        assert argv[0] == "docker-compose"
        assert argv[1:3] == ["-f", "docker-compose.e2e.yml"]

    @pytest.mark.parametrize("exc", [OSError("boom"), subprocess.TimeoutExpired(cmd="docker", timeout=5)])
    def test_falls_back_to_docker_compose_v1_when_plugin_probe_crashes(self, exc):
        with (
            patch("tests.e2e.stack_readiness.shutil.which", side_effect=_which_docker_and_compose),
            patch("tests.e2e.stack_readiness.subprocess.run", side_effect=exc),
        ):
            argv = compose_argv(("docker-compose.e2e.yml",))
        assert argv[0] == "docker-compose"
        assert argv[1:3] == ["-f", "docker-compose.e2e.yml"]

    def test_falls_back_to_docker_compose_v1_when_docker_absent(self):
        which = {"docker-compose": "/usr/bin/docker-compose"}.get

        with patch("tests.e2e.stack_readiness.shutil.which", side_effect=which):
            argv = compose_argv(("docker-compose.e2e.yml",))
        assert argv[0] == "docker-compose"
        assert argv[1:3] == ["-f", "docker-compose.e2e.yml"]

    def test_falls_back_to_docker_compose_plugin_name_when_neither_binary_found(self):
        with patch("tests.e2e.stack_readiness.shutil.which", return_value=None):
            argv = compose_argv(("docker-compose.e2e.yml",))
        assert argv[:2] == ["docker", "compose"]
        assert argv[2:4] == ["-f", "docker-compose.e2e.yml"]


class TestComposeAvailableAndTopology:
    """compose_available is CLI presence; in_network/use_container_exec is topology."""

    def test_compose_available_true_when_docker_on_path(self):
        with patch(
            "tests.e2e.stack_readiness.shutil.which", side_effect=lambda n: "/bin/docker" if n == "docker" else None
        ):
            assert compose_available() is True

    def test_compose_available_false_when_neither_binary(self):
        with patch("tests.e2e.stack_readiness.shutil.which", return_value=None):
            assert compose_available() is False

    def test_in_network_loopback_hosts(self):
        assert in_network("localhost") is False
        assert in_network("127.0.0.1") is False
        assert in_network("::1") is False
        assert in_network("postgres") is True
        assert in_network("adcp-server") is True

    def test_use_container_exec_is_host_path_topology(self):
        assert use_container_exec("localhost") is True
        assert use_container_exec("postgres") is False


class TestComposeExecAndDownArgv:
    """Behavioral oracles for compose exec/down SSOT helpers (``-T`` / ``-v``)."""

    def test_compose_exec_argv_emits_exec_dash_t_service_cmd(self):
        version_ok = _ps_completed("Docker Compose version v2.24.0", returncode=0)

        with (
            patch("tests.e2e.stack_readiness.shutil.which", side_effect=_which_docker_and_compose),
            patch("tests.e2e.stack_readiness.subprocess.run", return_value=version_ok),
        ):
            argv = compose_exec_argv("adcp-server", "python", "-c", "x")

        assert argv[-6:] == ["exec", "-T", "adcp-server", "python", "-c", "x"]
        assert "-f" in argv
        assert argv[argv.index("-f") + 1] == BASE_E2E_COMPOSE_FILE

    def test_compose_down_argv_emits_down_dash_v(self):
        version_ok = _ps_completed("Docker Compose version v2.24.0", returncode=0)

        with (
            patch("tests.e2e.stack_readiness.shutil.which", side_effect=_which_docker_and_compose),
            patch("tests.e2e.stack_readiness.subprocess.run", return_value=version_ok),
        ):
            argv = compose_down_argv()

        assert argv[-2:] == ["down", "-v"]
        assert "-f" in argv
        assert argv[argv.index("-f") + 1] == BASE_E2E_COMPOSE_FILE


class TestHostAndDbEnvDefaults:
    def test_e2e_host_default_reads_adcp_test_host(self):
        with patch.dict("os.environ", {"ADCP_TEST_HOST": "proxy"}, clear=False):
            assert e2e_host_default() == "proxy"

    def test_e2e_host_default_falls_back_to_localhost(self):
        import os

        cleaned = {k: v for k, v in os.environ.items() if k != "ADCP_TEST_HOST"}
        with patch.dict("os.environ", cleaned, clear=True):
            assert e2e_host_default() == "localhost"

    def test_e2e_db_url_default_prefers_e2e_database_url(self):
        with patch.dict(
            "os.environ",
            {
                "E2E_DATABASE_URL": "postgresql://u:p@e2ehost:1111/adcp",
                "DATABASE_URL": "postgresql://u:p@other:2222/adcp",
            },
            clear=False,
        ):
            assert e2e_db_url_default() == "postgresql://u:p@e2ehost:1111/adcp"

    def test_e2e_db_url_build_ignores_env(self):
        with patch.dict(
            "os.environ",
            {"E2E_DATABASE_URL": "postgresql://u:p@e2ehost:1111/adcp"},
            clear=False,
        ):
            assert e2e_db_url_build(host="localhost", port=5435).endswith("@localhost:5435/adcp")


class TestProbeWiring:
    """Direct probe tests — host + in-network branches, real registry mapping."""

    def test_probe_postgres_host_uses_published_port(self):
        with patch("tests.e2e.stack_readiness._tcp_open", return_value=True) as tcp:
            assert (
                _probe_postgres(
                    ports=e2e_ports(mcp=8000, postgres=55432),
                    host="localhost",
                    compose_files=("docker-compose.e2e.yml",),
                )
                is True
            )
        tcp.assert_called_once_with("localhost", 55432)

    def test_probe_postgres_host_without_port_fails_closed(self):
        # Absent postgres key must not silently degrade to compose health.
        with patch("tests.e2e.stack_readiness._compose_service_ready", return_value=True) as ready:
            assert (
                _probe_postgres(
                    ports={"mcp": 8000},  # type: ignore[typeddict-item]
                    host="localhost",
                    compose_files=("docker-compose.e2e.yml",),
                )
                is False
            )
        ready.assert_not_called()

    def test_probe_postgres_in_network_default_host(self):
        with (
            patch.dict("os.environ", {"E2E_DATABASE_URL": "", "DATABASE_URL": ""}, clear=False),
            patch("tests.e2e.stack_readiness._tcp_open", return_value=True) as tcp,
        ):
            assert (
                _probe_postgres(
                    ports=e2e_ports(mcp=8000, postgres=5432),
                    host="postgres",
                    compose_files=("docker-compose.e2e.yml",),
                )
                is True
            )
        tcp.assert_called_once_with("postgres", 5432)

    def test_probe_postgres_in_network_from_database_url(self):
        with (
            patch.dict(
                "os.environ",
                {"E2E_DATABASE_URL": "postgresql://u:p@dbhost:6543/adcp"},
                clear=False,
            ),
            patch("tests.e2e.stack_readiness._tcp_open", return_value=True) as tcp,
        ):
            assert (
                _probe_postgres(
                    ports=e2e_ports(mcp=8000, postgres=5432),
                    host="adcp-server",
                    compose_files=("docker-compose.e2e.yml",),
                )
                is True
            )
        tcp.assert_called_once_with("dbhost", 6543)

    def test_probe_adcp_health_host_hits_mcp_port(self):
        with patch("tests.e2e.stack_readiness._http_ok", return_value=True) as http:
            assert (
                _probe_adcp_health(
                    ports=e2e_ports(mcp=18000, postgres=5432),
                    host="localhost",
                    compose_files=("docker-compose.e2e.yml",),
                )
                is True
            )
        http.assert_called_once_with("http://localhost:18000/health")

    def test_probe_adcp_health_fail_closed_when_no_port(self):
        with patch("tests.e2e.stack_readiness._http_ok", return_value=True) as http:
            assert (
                _probe_adcp_health(
                    ports={"postgres": 5432},  # type: ignore[typeddict-item]
                    host="localhost",
                    compose_files=("docker-compose.e2e.yml",),
                )
                is False
            )
        http.assert_not_called()

    def test_probe_adcp_health_in_network(self):
        with patch("tests.e2e.stack_readiness._http_ok", return_value=True) as http:
            assert (
                _probe_adcp_health(
                    ports=e2e_ports(mcp=8000, postgres=5432),
                    host="adcp-server",
                    compose_files=("docker-compose.e2e.yml",),
                )
                is True
            )
        http.assert_called_once_with("http://adcp-server:8000/health")

    def test_probe_adcp_health_ipv6_host_returns_bool_never_raises(self):
        # ::1 must be bracketed; InvalidURL must not escape the probe.
        assert _probe_adcp_health(
            ports=e2e_ports(mcp=8092, postgres=5432),
            host="::1",
            compose_files=("docker-compose.e2e.yml",),
        ) in (True, False)

    def test_http_authority_brackets_ipv6(self):
        assert _http_authority("::1", 8092) == "[::1]:8092"
        assert _http_authority("localhost", 8092) == "localhost:8092"

    def test_http_ok_catches_invalid_url(self):
        from tests.e2e.stack_readiness import _http_ok

        with patch("tests.e2e.stack_readiness.httpx.Client") as client_cls:
            client = MagicMock()
            client_cls.return_value.__enter__.return_value = client
            client.get.side_effect = httpx.InvalidURL("bad")
            assert _http_ok("http://::1:8092/health") is False

    def test_probe_creative_agent_in_network_hits_service_url(self):
        with patch("tests.e2e.stack_readiness._http_ok", return_value=True) as http:
            assert (
                _probe_creative_agent(
                    ports=e2e_ports(mcp=8000, postgres=5432),
                    host="adcp-server",
                    compose_files=("docker-compose.e2e.yml",),
                )
                is True
            )
        http.assert_called_once_with("http://creative-agent:8080/api/creative-agent/health")

    def test_real_probe_funcs_postgres_key_wires_tcp(self):
        with patch("tests.e2e.stack_readiness._tcp_open", return_value=True) as tcp:
            assert _PROBE_FUNCS["postgres"](
                ports=e2e_ports(mcp=8000, postgres=54321),
                host="localhost",
                compose_files=("docker-compose.e2e.yml",),
            )
        tcp.assert_called_once_with("localhost", 54321)

    def test_real_probe_funcs_adcp_key_wires_http(self):
        with patch("tests.e2e.stack_readiness._http_ok", return_value=True) as http:
            assert _PROBE_FUNCS["adcp_health"](
                ports=e2e_ports(mcp=9000, postgres=5432),
                host="localhost",
                compose_files=("docker-compose.e2e.yml",),
            )
        http.assert_called_once_with("http://localhost:9000/health")


class TestWaitForE2EStackCreativeAgentOracle:
    """Creative-agent miss must fail the wait naming that probe."""

    def test_creative_agent_false_fails_naming_probe_with_compose_diag(self):
        probes = _stub_probes(creative_agent=False)
        with (
            patch.dict("tests.e2e.stack_readiness._PROBE_FUNCS", probes, clear=False),
            patch("tests.e2e.stack_readiness._dump_e2e_compose_logs"),
            patch(
                "tests.e2e.stack_readiness._compose_service_health",
                return_value=(None, "diag"),
            ),
            pytest.raises(pytest.fail.Exception, match="failed probe: creative-agent") as exc_info,
        ):
            wait_for_e2e_stack(
                ports=e2e_ports(mcp=8000, postgres=5432),
                compose_files=("docker-compose.e2e.yml",),
                host="localhost",
                timeout_s=0.01,
                poll_interval_s=0.001,
            )
        msg = str(exc_info.value)
        assert "creative-agent" in msg
        assert "compose_ps_diag=diag" in msg

    def test_creative_agent_false_reports_compose_health_when_no_err(self):
        probes = _stub_probes(creative_agent=False)
        with (
            patch.dict("tests.e2e.stack_readiness._PROBE_FUNCS", probes, clear=False),
            patch("tests.e2e.stack_readiness._dump_e2e_compose_logs"),
            patch(
                "tests.e2e.stack_readiness._compose_service_health",
                return_value=(ServiceHealth.UNHEALTHY, None),
            ),
            pytest.raises(pytest.fail.Exception) as exc_info,
        ):
            wait_for_e2e_stack(
                ports=e2e_ports(mcp=8000, postgres=5432),
                compose_files=("docker-compose.e2e.yml",),
                host="localhost",
                timeout_s=0.01,
                poll_interval_s=0.001,
            )
        assert "compose_health=" in str(exc_info.value)

    def test_probe_order_names_postgres_first_on_double_false(self):
        probes = _stub_probes(postgres=False, adcp_health=False)
        with (
            patch.dict("tests.e2e.stack_readiness._PROBE_FUNCS", probes, clear=False),
            patch("tests.e2e.stack_readiness._dump_e2e_compose_logs"),
            pytest.raises(pytest.fail.Exception, match="failed probe: postgres"),
        ):
            wait_for_e2e_stack(
                ports=e2e_ports(mcp=8000, postgres=5432),
                compose_files=("docker-compose.e2e.yml",),
                host="localhost",
                timeout_s=0.01,
                poll_interval_s=0.001,
            )

    def test_all_probes_ready_returns_without_log_dump(self):
        probes = _stub_probes()
        with (
            patch.dict("tests.e2e.stack_readiness._PROBE_FUNCS", probes, clear=False),
            patch("tests.e2e.stack_readiness._dump_e2e_compose_logs") as dump,
        ):
            wait_for_e2e_stack(
                ports=e2e_ports(mcp=8000, postgres=5432),
                compose_files=("docker-compose.e2e.yml",),
                host="localhost",
                timeout_s=1.0,
                poll_interval_s=0.001,
            )
        dump.assert_not_called()

    def test_empty_names_raises_value_error_via_private_helper(self):
        with (
            patch("tests.e2e.stack_readiness._dump_e2e_compose_logs") as dump,
            pytest.raises(ValueError, match="non-empty required"),
        ):
            _wait_for_probes(
                (),
                ports=e2e_ports(mcp=8000, postgres=5432),
                compose_files=("docker-compose.e2e.yml",),
                host="localhost",
                timeout_s=1.0,
            )
        dump.assert_not_called()

    def test_unknown_probe_raises_value_error_via_private_helper(self):
        with (
            patch("tests.e2e.stack_readiness._dump_e2e_compose_logs") as dump,
            pytest.raises(ValueError, match="Unknown E2E readiness probes"),
        ):
            _wait_for_probes(
                ("nope",),
                ports=e2e_ports(mcp=8000, postgres=5432),
                compose_files=("docker-compose.e2e.yml",),
                host="localhost",
                timeout_s=1.0,
            )
        dump.assert_not_called()

    def test_wait_uses_real_registry_with_patched_low_level(self):
        with (
            patch("tests.e2e.stack_readiness._tcp_open", return_value=True),
            patch("tests.e2e.stack_readiness._compose_service_ready", return_value=True),
            patch("tests.e2e.stack_readiness._http_ok", return_value=True) as http,
            patch("tests.e2e.stack_readiness._dump_e2e_compose_logs") as dump,
        ):
            wait_for_e2e_stack(
                ports=e2e_ports(mcp=8000, postgres=5432),
                compose_files=("docker-compose.e2e.yml",),
                host="localhost",
                timeout_s=1.0,
                poll_interval_s=0.001,
            )
        dump.assert_not_called()
        assert call("http://localhost:8000/health") in http.call_args_list


class TestWaitForServerReadinessWrapper:
    def test_fail_closed_when_mcp_url_missing_port(self):
        with pytest.raises(pytest.fail.Exception, match="cannot derive host/port"):
            wait_for_server_readiness("http://localhost", postgres_port=5432)

    def test_forwards_to_wait_for_e2e_stack(self):
        with patch("tests.e2e.utils.wait_for_e2e_stack") as wait:
            wait_for_server_readiness("http://localhost:18000", postgres_port=55432, timeout=12.5)
        wait.assert_called_once_with(
            ports=e2e_ports(mcp=18000, postgres=55432),
            compose_files=("docker-compose.e2e.yml", "docker-compose.e2e.ports.yml"),
            host="localhost",
            timeout_s=12.5,
        )
