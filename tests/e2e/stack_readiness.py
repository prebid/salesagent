"""One ordered E2E readiness gate: PostgreSQL, creative agent, then sales agent.

Every probe dials the service. Host runners use published ports; in-network
runners use Compose service addresses. Compose is used only for diagnostics.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import time
from collections.abc import Sequence
from typing import Final, NamedTuple, Protocol, TypedDict
from urllib.parse import urlparse

import httpx
import pytest

_LOG_DUMP_SERVICES: Final[tuple[str, ...]] = (
    "postgres",
    "creative-pg",
    "creative-agent",
    "adcp-server",
    "proxy",
)

# Public SSOT — imported by ``tests.e2e.conftest`` (fixture + wrapper must share).
DEFAULT_E2E_COMPOSE_FILES: Final[tuple[str, ...]] = (
    "docker-compose.e2e.yml",
    "docker-compose.e2e.ports.yml",
)

# Base compose file only (no ports overlay) — teardown / init / down paths.
BASE_E2E_COMPOSE_FILE: Final[str] = DEFAULT_E2E_COMPOSE_FILES[0]

_CREATIVE_AGENT_IN_NETWORK_HEALTH: Final[str] = "http://creative-agent:8080/api/creative-agent/health"


class E2EPorts(TypedDict):
    """Published ports the ordered hard gate needs — typos fail at the type boundary."""

    mcp: int
    postgres: int
    creative_agent: int


def e2e_ports(*, mcp: int, postgres: int, creative_agent: int) -> E2EPorts:
    """Build the ports mapping every wait/probe call site must share."""
    return {"mcp": mcp, "postgres": postgres, "creative_agent": creative_agent}


class _Probe(Protocol):
    def __call__(
        self,
        *,
        ports: E2EPorts,
        host: str,
        compose_files: Sequence[str],
    ) -> bool: ...


def e2e_host_default() -> str:
    """Host for e2e server URLs — SSOT for ``conftest.e2e_host`` and wait defaults."""
    return os.getenv("ADCP_TEST_HOST", "localhost")


class E2EDatabase(NamedTuple):
    """One resolved connection, shared by TCP readiness and direct DB consumers."""

    url: str
    host: str
    port: int


def e2e_db_url_build(*, host: str = "localhost", port: int = 5432) -> str:
    """Build the canonical e2e Postgres URL without reading the environment."""
    return f"postgresql://adcp_user:secure_password_change_me@{_http_authority(host, port)}/adcp"


def resolve_e2e_db(*, fallback_host: str = "localhost", fallback_port: int = 5432) -> E2EDatabase:
    """Resolve the environment chain once, preserving the configured DSN verbatim."""
    url = os.getenv("E2E_DATABASE_URL") or os.getenv("DATABASE_URL")
    if url:
        parsed = urlparse(url)
        return E2EDatabase(url, parsed.hostname or fallback_host, parsed.port or fallback_port)
    return E2EDatabase(e2e_db_url_build(host=fallback_host, port=fallback_port), fallback_host, fallback_port)


def e2e_db_url_default(*, fallback_host: str = "localhost", fallback_port: int = 5432) -> str:
    """Full Postgres URL from the same connection resolver the probe uses."""
    return resolve_e2e_db(fallback_host=fallback_host, fallback_port=fallback_port).url


def in_network(host: str) -> bool:
    """True when the runner reaches the stack by compose service name (topology)."""
    return host not in {"localhost", "127.0.0.1", "::1"}


def use_container_exec(host: str) -> bool:
    """True when seed/update should prefer ``compose exec`` (host-path topology)."""
    return not in_network(host)


def compose_argv(compose_files: Sequence[str]) -> list[str]:
    """Build compose argv for lifecycle commands and failure diagnostics."""
    argv: list[str]
    if shutil.which("docker"):
        try:
            version = subprocess.run(
                ["docker", "compose", "version"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            version = None
        if version is not None and version.returncode == 0:
            argv = ["docker", "compose"]
        elif shutil.which("docker-compose"):
            argv = ["docker-compose"]
        else:
            argv = ["docker", "compose"]
    elif shutil.which("docker-compose"):
        argv = ["docker-compose"]
    else:
        argv = ["docker", "compose"]

    for path in compose_files:
        argv.extend(["-f", path])
    return argv


def compose_exec_argv(service: str, *cmd: str, compose_files: Sequence[str] = (BASE_E2E_COMPOSE_FILE,)) -> list[str]:
    """Build ``compose exec -T <service> <cmd>`` argv.

    SSOT for the "run this command inside this compose service" unit —
    ``compose_argv`` only single-sources the binary+files, not the
    ``"exec", "-T", <service>`` tail that call sites used to hand-assemble
    (four diff-introduced sites forked this before it existed).
    """
    return [*compose_argv(compose_files), "exec", "-T", service, *cmd]


def compose_down_argv(compose_files: Sequence[str] = (BASE_E2E_COMPOSE_FILE,)) -> list[str]:
    """Build ``compose down -v`` argv — SSOT for the two teardown call sites."""
    return [*compose_argv(compose_files), "down", "-v"]


def compose_available() -> bool:
    """True when a docker/compose CLI is on PATH — gates compose inspect/log dump."""
    return shutil.which("docker-compose") is not None or shutil.which("docker") is not None


def _dump_e2e_compose_logs(compose_files: Sequence[str]) -> None:
    """Print last-100-line logs for the standard E2E service set (once)."""
    if not compose_available():
        print("⚠️  docker/compose unavailable — skipping service log dump")
        return

    base = compose_argv(compose_files)
    print("\n❌ E2E readiness failed. Dumping compose logs...")
    for service in _LOG_DUMP_SERVICES:
        try:
            print(f"\n📋 {service} logs (last 100 lines):")
            result = subprocess.run(
                [*base, "logs", "--tail=100", service],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            if result.stdout:
                print(result.stdout)
            if result.stderr:
                print(f"STDERR: {result.stderr}")
        except Exception as exc:  # noqa: BLE001 — best-effort diagnostics
            print(f"Could not get {service} logs: {exc}")

    try:
        print("\n📊 Container status:")
        ps_result = subprocess.run(
            [*base, "ps"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if ps_result.stdout:
            print(ps_result.stdout)
    except Exception as exc:  # noqa: BLE001
        print(f"Could not get container status: {exc}")


def _tcp_open(host: str, port: int, *, timeout_s: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout_s):
            return True
    except OSError:
        return False


def _http_authority(host: str, port: int) -> str:
    """Format ``host:port``, bracketing IPv6 literals so httpx accepts the URL."""
    if ":" in host and not host.startswith("["):
        return f"[{host}]:{port}"
    return f"{host}:{port}"


def _http_ok(url: str, *, timeout_s: float = 2.0) -> bool:
    try:
        with httpx.Client() as client:
            resp = client.get(url, timeout=timeout_s)
            return resp.status_code == 200
    except (httpx.HTTPError, httpx.InvalidURL):
        return False


def _probe_postgres(
    *,
    ports: E2EPorts,
    host: str,
    compose_files: Sequence[str],
) -> bool:
    del compose_files  # host path must use the published port — no compose-health degrade
    if in_network(host):
        # Runner is on the compose network — shared env/default endpoint SSOT.
        connection = resolve_e2e_db(fallback_host="postgres", fallback_port=5432)
        return _tcp_open(connection.host, connection.port)

    postgres_port = ports.get("postgres")
    if postgres_port is None:
        # Absent key must fail closed — never silently fall through to compose health.
        return False
    return _tcp_open(host, int(postgres_port))


def _probe_creative_agent(
    *,
    ports: E2EPorts,
    host: str,
    compose_files: Sequence[str],
) -> bool:
    del compose_files
    if in_network(host):
        return _http_ok(_CREATIVE_AGENT_IN_NETWORK_HEALTH)

    port = ports.get("creative_agent")
    if port is None:
        return False
    return _http_ok(f"http://{_http_authority(host, int(port))}/api/creative-agent/health")


def _probe_adcp_health(
    *,
    ports: E2EPorts,
    host: str,
    compose_files: Sequence[str],
) -> bool:
    del compose_files  # adcp readiness is HTTP /health on the published port
    port = ports.get("mcp")
    if port is None:
        return False
    return _http_ok(f"http://{_http_authority(host, int(port))}/health")


# An ordered tuple binds each name to its implementation; there is no second
# key list to drift and no caller-supplied subset of the required checks.
_PROBES: Final[tuple[tuple[str, _Probe], ...]] = (
    ("postgres", _probe_postgres),
    ("creative-agent", _probe_creative_agent),
    ("adcp_health", _probe_adcp_health),
)


def wait_for_e2e_stack(
    *,
    ports: E2EPorts,
    compose_files: Sequence[str] = DEFAULT_E2E_COMPOSE_FILES,
    host: str | None = None,
    timeout_s: float = 60.0,
    poll_interval_s: float = 2.0,
) -> None:
    """Require postgres, creative-agent and server health, in that order.

    Both fixture paths use this gate. A failed check
    prevents later probes in that attempt; timeout emits one diagnostic dump.
    """
    host = e2e_host_default() if host is None else host
    deadline = time.monotonic() + timeout_s
    failed = "postgres"
    print(f"Waiting for E2E stack readiness (timeout={timeout_s}s, host={host})...")
    while time.monotonic() < deadline:
        for name, probe in _PROBES:
            if not probe(ports=ports, host=host, compose_files=compose_files):
                failed = name
                break
        else:
            print("✓ E2E stack ready")
            return
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(poll_interval_s, remaining))

    _dump_e2e_compose_logs(compose_files)
    pytest.fail(f"E2E stack not ready after {timeout_s}s — failed probe: {failed} (host={host}; ports={dict(ports)})")
