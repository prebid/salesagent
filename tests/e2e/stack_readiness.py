"""Shared E2E stack readiness — ordered probes + one structured failure path.

SSOT for ``docker_services_e2e`` (verify-only and standalone) and
``wait_for_server_readiness``. Required hard-gate order:

    postgres → creative-agent → adcp ``/health``

Creative-agent has no host-published port in the e2e ports overlay; on the
host path we inspect compose health, and in-network we HTTP-probe the
compose service name. Do not assume ``localhost:9999`` (that is the
standalone ``creative-agent-stack.sh`` network).
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import time
from collections.abc import Mapping, Sequence
from typing import Final
from urllib.parse import urlparse

import httpx
import pytest

REQUIRED_E2E_PROBES: Final[tuple[str, ...]] = ("postgres", "creative-agent", "adcp_health")

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

_CREATIVE_AGENT_IN_NETWORK_HEALTH: Final = "http://creative-agent:8080/api/creative-agent/health"


def _e2e_host_default() -> str:
    return os.getenv("ADCP_TEST_HOST", "localhost")


def _in_network(host: str) -> bool:
    """True when the runner reaches the stack by compose service name."""
    return host not in {"localhost", "127.0.0.1", "::1"}


def compose_argv(compose_files: Sequence[str]) -> list[str]:
    """Build compose argv, preferring the Compose V2 plugin when available.

    Host-path creative-agent gating needs ``ps --format json``. Prefer
    ``docker compose`` (plugin) over a legacy ``docker-compose`` V1 binary that
    may lack JSON ``ps`` and make health always look unknown.
    """
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


def compose_available() -> bool:
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


def _compose_service_health(service: str, compose_files: Sequence[str]) -> tuple[str | None, str | None]:
    """Return ``(health, error_diag)``.

    ``health`` is the compose Health string (e.g. ``healthy``) or None when
    unknown/unavailable. ``error_diag`` is a human-readable diagnostic when
    health could not be determined; None on a successful parse.
    """
    if not compose_available():
        return None, "docker/compose unavailable for host-path health inspect"
    try:
        result = subprocess.run(
            [*compose_argv(compose_files), "ps", "--format", "json", service],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, (
            f"compose ps --format json for {service!r} raised {type(exc).__name__}: {exc}; "
            "Compose V2 JSON ps is required for host-path creative-agent gating"
        )
    if result.returncode != 0 or not result.stdout.strip():
        return None, (
            f"compose ps --format json for {service!r} failed "
            f"(rc={result.returncode}, stdout_empty={not bool(result.stdout.strip())}); "
            "Compose V2 JSON ps is required for host-path creative-agent gating. "
            f"stderr={result.stderr.strip()!r}"
        )

    # Compose v2 may emit one JSON object, an array, or NDJSON lines.
    payload = result.stdout.strip()
    records: list[dict] = []
    try:
        parsed = json.loads(payload)
        if isinstance(parsed, list):
            records = [r for r in parsed if isinstance(r, dict)]
        elif isinstance(parsed, dict):
            records = [parsed]
    except json.JSONDecodeError:
        for line in payload.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                records.append(obj)

    if not records:
        return None, (
            f"compose ps --format json for {service!r} produced no parseable records; "
            "Compose V2 JSON ps is required for host-path creative-agent gating. "
            f"raw_stdout={payload[:200]!r}"
        )
    health = records[0].get("Health") or records[0].get("health")
    if isinstance(health, str) and health.strip():
        return health.strip().lower(), None
    # Health empty: only treat State/Status that explicitly say healthy as ready.
    # Bare "running"/"up" is not a healthcheck pass — probes require "healthy".
    state = str(records[0].get("State") or records[0].get("Status") or "").lower()
    if "healthy" in state:
        return "healthy", None
    return None, None


def _compose_reports_ready(health: str | None) -> bool:
    """True only when compose reports an explicit healthcheck pass."""
    return health == "healthy"


def _tcp_open(host: str, port: int, *, timeout_s: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout_s):
            return True
    except OSError:
        return False


def _http_ok(url: str, *, timeout_s: float = 2.0) -> bool:
    try:
        with httpx.Client() as client:
            resp = client.get(url, timeout=timeout_s)
            return resp.status_code == 200
    except httpx.HTTPError:
        return False


def _probe_postgres(
    *,
    ports: Mapping[str, int],
    host: str,
    compose_files: Sequence[str],
) -> bool:
    postgres_port = ports.get("postgres")
    if _in_network(host):
        # Runner is on the compose network — resolve DB host from env when set.
        db_url = os.getenv("E2E_DATABASE_URL") or os.getenv("DATABASE_URL")
        if db_url:
            parsed = urlparse(db_url)
            db_host = parsed.hostname or "postgres"
            db_port = parsed.port or 5432
            return _tcp_open(db_host, db_port)
        return _tcp_open("postgres", 5432)

    if postgres_port is not None:
        # Host path publishes postgres via the ports overlay.
        return _tcp_open("127.0.0.1", int(postgres_port))

    health, _err = _compose_service_health("postgres", compose_files)
    return _compose_reports_ready(health)


def _probe_creative_agent(*, host: str, compose_files: Sequence[str]) -> bool:
    if _in_network(host):
        return _http_ok(_CREATIVE_AGENT_IN_NETWORK_HEALTH)

    health, _err = _compose_service_health("creative-agent", compose_files)
    return _compose_reports_ready(health)


def _probe_adcp_health(*, ports: Mapping[str, int], host: str) -> bool:
    # MCP/admin share the unified FastAPI port published by the proxy.
    port = ports.get("mcp") or ports.get("admin") or ports.get("adcp")
    if port is None:
        return False
    return _http_ok(f"http://{host}:{int(port)}/health")


_PROBE_FUNCS = {
    "postgres": lambda **kw: _probe_postgres(ports=kw["ports"], host=kw["host"], compose_files=kw["compose_files"]),
    "creative-agent": lambda **kw: _probe_creative_agent(host=kw["host"], compose_files=kw["compose_files"]),
    "adcp_health": lambda **kw: _probe_adcp_health(ports=kw["ports"], host=kw["host"]),
}

# Guard: probe registry must cover the required hard-gate set (no drift).
assert frozenset(_PROBE_FUNCS) >= frozenset(REQUIRED_E2E_PROBES), (
    f"_PROBE_FUNCS keys {sorted(_PROBE_FUNCS)} must cover REQUIRED_E2E_PROBES {list(REQUIRED_E2E_PROBES)}"
)


def wait_for_e2e_stack(
    *,
    ports: Mapping[str, int],
    compose_files: Sequence[str] | None = None,
    host: str | None = None,
    required: Sequence[str] = REQUIRED_E2E_PROBES,
    timeout_s: float = 60.0,
    poll_interval_s: float = 2.0,
) -> None:
    """Ordered required probes; on timeout dump logs once and ``pytest.fail`` once.

    Probes run in ``required`` order and short-circuit on the first miss each
    poll. Parallel "any healthy" semantics are intentionally forbidden so
    creative-agent cannot be skipped when adcp ``/health`` is already up.
    """
    files = tuple(compose_files) if compose_files is not None else DEFAULT_E2E_COMPOSE_FILES
    resolved_host = host if host is not None else _e2e_host_default()
    probe_names = tuple(required)
    if not probe_names:
        raise ValueError("wait_for_e2e_stack requires a non-empty required probe list")

    unknown = [name for name in probe_names if name not in _PROBE_FUNCS]
    if unknown:
        raise ValueError(f"Unknown E2E readiness probes: {unknown}")

    print(
        f"Waiting for E2E stack readiness (probes={list(probe_names)}, timeout={timeout_s}s, host={resolved_host})..."
    )
    deadline = time.monotonic() + timeout_s
    last_failed: str | None = None

    while time.monotonic() < deadline:
        last_failed = None
        for name in probe_names:
            ok = _PROBE_FUNCS[name](ports=ports, host=resolved_host, compose_files=files)
            if not ok:
                last_failed = name
                break
        else:
            print(f"✓ E2E stack ready ({', '.join(probe_names)})")
            return

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(poll_interval_s, remaining))

    failed = last_failed or probe_names[0]
    _dump_e2e_compose_logs(files)
    compose_hint = ""
    if failed == "creative-agent" and not _in_network(resolved_host):
        _health, compose_err = _compose_service_health("creative-agent", files)
        if compose_err:
            compose_hint = f"; compose_ps_diag={compose_err}"
    pytest.fail(
        f"E2E stack not ready after {timeout_s}s — failed probe: {failed} "
        f"(required order: {list(probe_names)}; host={resolved_host}; ports={dict(ports)}"
        f"{compose_hint})"
    )
