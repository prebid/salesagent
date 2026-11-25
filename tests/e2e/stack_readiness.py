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
from typing import Final, Literal, NamedTuple, Protocol, TypedDict
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

_DEFAULT_DB_USER: Final[str] = "adcp_user"
_DEFAULT_DB_PASSWORD: Final[str] = "secure_password_change_me"
_DEFAULT_DB_NAME: Final[str] = "adcp"


class E2EPorts(TypedDict, total=False):
    """Host-published ports; in-network runners need no published mappings."""

    mcp: int
    postgres: int
    creative_agent: int


class E2EService(NamedTuple):
    """The published-port key, Compose address and readiness identity of a service."""

    probe_name: str
    port_key: Literal["postgres", "creative_agent", "mcp"]
    compose_name: str
    container_port: int
    health_path: str


POSTGRES: Final = E2EService("postgres", "postgres", "postgres", 5432, "")
CREATIVE_AGENT: Final = E2EService(
    "creative-agent", "creative_agent", "creative-agent", 8080, "/api/creative-agent/health"
)
SERVER: Final = E2EService("adcp_health", "mcp", "proxy", 8000, "/health")


class UnpublishedPortError(ValueError):
    """A host runner has no published address for a required service."""


class _Probe(Protocol):
    def __call__(
        self,
        *,
        ports: E2EPorts,
        host: str,
    ) -> bool: ...


def e2e_host() -> str:
    """Published-port host or Compose service name used by this E2E runner."""
    return os.getenv("ADCP_TEST_HOST", "localhost")


class E2EDatabase(NamedTuple):
    """One resolved connection, shared by TCP readiness and direct DB consumers."""

    url: str
    host: str
    port: int
    user: str
    password: str
    dbname: str


def e2e_db_url_build(*, host: str = "localhost", port: int = POSTGRES.container_port) -> str:
    """Build the canonical e2e Postgres URL without reading the environment."""
    return f"postgresql://{_DEFAULT_DB_USER}:{_DEFAULT_DB_PASSWORD}@{_http_authority(host, port)}/{_DEFAULT_DB_NAME}"


def _service_address(service: E2EService, *, host: str, published_port: int | None) -> tuple[str, int]:
    """Choose a Compose address in-network or the published address on the host."""
    if e2e_in_network(host):
        return service.compose_name, service.container_port
    if published_port is None:
        raise UnpublishedPortError(f"{service.compose_name} requires a published port on the host")
    return host, published_port


def e2e_service_url(service: E2EService, *, host: str, published_port: int | None) -> str:
    """Resolve one HTTP service URL for probes and fixture consumers."""
    address, port = _service_address(service, host=host, published_port=published_port)
    return f"http://{_http_authority(address, port)}"


def resolve_e2e_db(*, host: str, published_port: int | None) -> E2EDatabase:
    """Resolve the E2E endpoint, independently of a suite's DATABASE_URL."""
    fallback_host, fallback_port = _service_address(POSTGRES, host=host, published_port=published_port)
    url = os.getenv("E2E_DATABASE_URL") or e2e_db_url_build(host=fallback_host, port=fallback_port)
    parsed = urlparse(url)
    return E2EDatabase(
        url=url,
        host=parsed.hostname or fallback_host,
        port=parsed.port or fallback_port,
        user=parsed.username or _DEFAULT_DB_USER,
        password=parsed.password or _DEFAULT_DB_PASSWORD,
        dbname=parsed.path.lstrip("/") or _DEFAULT_DB_NAME,
    )


def e2e_in_network(host: str | None = None) -> bool:
    """True when the runner reaches the stack by compose service name (topology)."""
    host = e2e_host() if host is None else host
    return host not in {"localhost", "127.0.0.1", "::1"}


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
) -> bool:
    connection = resolve_e2e_db(host=host, published_port=ports.get(POSTGRES.port_key))
    return _tcp_open(connection.host, connection.port)


def _http_probe(service: E2EService) -> _Probe:
    """Bind a service's health endpoint to the shared HTTP probe operation."""

    def probe(*, ports: E2EPorts, host: str) -> bool:
        return _http_ok(
            e2e_service_url(service, host=host, published_port=ports.get(service.port_key)) + service.health_path
        )

    return probe


# An ordered tuple binds each name to its implementation; there is no second
# key list to drift and no caller-supplied subset of the required checks.
_PROBES: Final[tuple[tuple[str, _Probe], ...]] = (
    (POSTGRES.probe_name, _probe_postgres),
    *((service.probe_name, _http_probe(service)) for service in (CREATIVE_AGENT, SERVER)),
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
    host = e2e_host() if host is None else host
    deadline = time.monotonic() + timeout_s
    failed = POSTGRES.probe_name
    print(f"Waiting for E2E stack readiness (timeout={timeout_s}s, host={host})...")
    while time.monotonic() < deadline:
        for name, probe in _PROBES:
            try:
                ready = probe(ports=ports, host=host)
            except UnpublishedPortError:
                ready = False
            if not ready:
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
