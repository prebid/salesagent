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
import re
import shutil
import socket
import subprocess
import time
from collections.abc import Mapping, Sequence
from enum import StrEnum
from typing import Final, NotRequired, Protocol, TypedDict, cast
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

_CREATIVE_AGENT_IN_NETWORK_HEALTH: Final[str] = "http://creative-agent:8080/api/creative-agent/health"

# Shared diagnostic fragment for host-path compose JSON-ps failures (three branches).
_COMPOSE_JSON_PS_REQUIRED: Final[str] = "Compose V2 JSON ps is required for host-path creative-agent gating"

# Parenthetical health token in compose State/Status when Health is empty.
_STATE_HEALTH_RE: Final[re.Pattern[str]] = re.compile(r"\((healthy|unhealthy|starting)\)")


class ServiceHealth(StrEnum):
    """Closed set of compose healthcheck verdicts — fail-closed outside HEALTHY."""

    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"
    STARTING = "starting"
    UNKNOWN = "unknown"


class _ComposePsRecord(TypedDict):
    """Known keys from ``docker compose ps --format json`` (extra keys allowed)."""

    Health: NotRequired[str]
    State: NotRequired[str]
    Status: NotRequired[str]
    Name: NotRequired[str]


class E2EPorts(TypedDict):
    """Published ports the ordered hard gate needs — typos fail at the type boundary."""

    mcp: int
    postgres: int


def e2e_ports(*, mcp: int, postgres: int) -> E2EPorts:
    """Build the ports mapping every wait/probe call site must share."""
    return {"mcp": mcp, "postgres": postgres}


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


def _e2e_db_env_url() -> str | None:
    """Read the e2e DB URL env chain once — SSOT for endpoint + URL resolvers."""
    return os.getenv("E2E_DATABASE_URL") or os.getenv("DATABASE_URL")


def resolve_e2e_db_endpoint(
    *,
    fallback_host: str = "localhost",
    fallback_port: int = 5432,
) -> tuple[str, int]:
    """Resolve (host, port) for the e2e Postgres endpoint.

    Preference: ``E2E_DATABASE_URL`` → ``DATABASE_URL`` → ``(fallback_host, fallback_port)``.
    SSOT for ``_probe_postgres`` and ``conftest.e2e_db_url`` so the readiness gate
    and direct-DB helpers cannot drift on defaults.
    """
    db_url = _e2e_db_env_url()
    if db_url:
        parsed = urlparse(db_url)
        return (parsed.hostname or fallback_host, parsed.port or fallback_port)
    return (fallback_host, fallback_port)


def e2e_db_url_build(*, host: str = "localhost", port: int = 5432) -> str:
    """Build the canonical e2e Postgres URL (no env read) — for setdefault/seed paths."""
    return f"postgresql://adcp_user:secure_password_change_me@{host}:{port}/adcp"


def e2e_db_url_default(*, fallback_host: str = "localhost", fallback_port: int = 5432) -> str:
    """Full Postgres URL for direct-DB e2e helpers — env wins, else build from endpoint.

    SSOT for ``conftest.e2e_db_url`` (mirrors the ``e2e_host_default`` wrapper
    naming rule — shared stem + ``_default``, no ``db``/``database`` token split).
    """
    env_url = _e2e_db_env_url()
    if env_url:
        return env_url
    host, port = resolve_e2e_db_endpoint(fallback_host=fallback_host, fallback_port=fallback_port)
    return e2e_db_url_build(host=host, port=port)


def in_network(host: str) -> bool:
    """True when the runner reaches the stack by compose service name (topology)."""
    return host not in {"localhost", "127.0.0.1", "::1"}


def use_container_exec(host: str) -> bool:
    """True when seed/update should prefer ``compose exec`` (host-path topology)."""
    return not in_network(host)


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


def _map_health_token(token: str) -> ServiceHealth:
    """Map a normalized health token to the closed enum (unknown otherwise)."""
    try:
        return ServiceHealth(token)
    except ValueError:
        return ServiceHealth.UNKNOWN


def health_of(record: Mapping[str, object]) -> ServiceHealth:
    """Pure compose-ps verdict — fetch is elsewhere; this never substring-matches.

    ``"unhealthy"`` must not count as ready because it contains ``"healthy"``.
    Empty Health falls back to a parenthetical token in State/Status only.
    """
    raw = record.get("Health")
    if isinstance(raw, str) and raw.strip():
        return _map_health_token(raw.strip().lower())

    state = str(record.get("State") or record.get("Status") or "").lower()
    match = _STATE_HEALTH_RE.search(state)
    if match:
        return _map_health_token(match.group(1))
    return ServiceHealth.UNKNOWN


def _compose_service_health(service: str, compose_files: Sequence[str]) -> tuple[ServiceHealth | None, str | None]:
    """Return ``(health, error_diag)``.

    ``health`` is a ``ServiceHealth`` from a successful parse, or None when
    compose JSON-ps could not be fetched/parsed. ``error_diag`` is set when
    health could not be determined due to a tooling/parse failure.
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
            f"compose ps --format json for {service!r} raised {type(exc).__name__}: {exc}; {_COMPOSE_JSON_PS_REQUIRED}"
        )
    if result.returncode != 0 or not result.stdout.strip():
        return None, (
            f"compose ps --format json for {service!r} failed "
            f"(rc={result.returncode}, stdout_empty={not bool(result.stdout.strip())}); "
            f"{_COMPOSE_JSON_PS_REQUIRED}. "
            f"stderr={result.stderr.strip()!r}"
        )

    # Compose v2 may emit one JSON object, an array, or NDJSON lines.
    payload = result.stdout.strip()
    records: list[_ComposePsRecord] = []
    try:
        parsed = json.loads(payload)
        if isinstance(parsed, list):
            records = [cast(_ComposePsRecord, r) for r in parsed if isinstance(r, dict)]
        elif isinstance(parsed, dict):
            records = [cast(_ComposePsRecord, parsed)]
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
                records.append(cast(_ComposePsRecord, obj))

    if not records:
        return None, (
            f"compose ps --format json for {service!r} produced no parseable records; "
            f"{_COMPOSE_JSON_PS_REQUIRED}. "
            f"raw_stdout={payload[:200]!r}"
        )
    return health_of(records[0]), None


def _compose_reports_ready(health: ServiceHealth | None) -> bool:
    """True only when compose reports an explicit healthcheck pass — fail-closed."""
    return health is ServiceHealth.HEALTHY


def _compose_service_ready(service: str, compose_files: Sequence[str]) -> bool:
    """True when compose reports the named service as healthcheck-ready."""
    health, _err = _compose_service_health(service, compose_files)
    return _compose_reports_ready(health)


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
        db_host, db_port = resolve_e2e_db_endpoint(fallback_host="postgres", fallback_port=5432)
        return _tcp_open(db_host, db_port)

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
    del ports  # creative-agent has no host-published port in the e2e overlay
    if in_network(host):
        return _http_ok(_CREATIVE_AGENT_IN_NETWORK_HEALTH)

    return _compose_service_ready("creative-agent", compose_files)


def _probe_adcp_health(
    *,
    ports: E2EPorts,
    host: str,
    compose_files: Sequence[str],
) -> bool:
    del compose_files  # adcp readiness is HTTP /health on the published port
    # Live contract: producers emit {"mcp": …, "postgres": …} only — no admin/adcp keys.
    port = ports.get("mcp")
    if port is None:
        return False
    return _http_ok(f"http://{_http_authority(host, int(port))}/health")


_PROBE_FUNCS: dict[str, _Probe] = {
    "postgres": _probe_postgres,
    "creative-agent": _probe_creative_agent,
    "adcp_health": _probe_adcp_health,
}

# One home for the probe-set invariant (imported modules + arch guard share this).
assert frozenset(_PROBE_FUNCS) >= frozenset(REQUIRED_E2E_PROBES), (
    f"_PROBE_FUNCS keys {sorted(_PROBE_FUNCS)} must cover REQUIRED_E2E_PROBES {list(REQUIRED_E2E_PROBES)}"
)


def _wait_for_probes(
    names: Sequence[str],
    *,
    ports: E2EPorts,
    compose_files: Sequence[str],
    host: str,
    timeout_s: float = 60.0,
    poll_interval_s: float = 2.0,
) -> None:
    """Ordered named probes; on timeout dump logs once and ``pytest.fail`` once.

    Private seam for helper unit tests that need a narrowed name list. Production
    callers use :func:`wait_for_e2e_stack`, which always runs ``REQUIRED_E2E_PROBES``.
    """
    probe_names = tuple(names)
    if not probe_names:
        raise ValueError("wait_for_e2e_stack requires a non-empty required probe list")

    unknown = [name for name in probe_names if name not in _PROBE_FUNCS]
    if unknown:
        raise ValueError(f"Unknown E2E readiness probes: {unknown}")

    print(f"Waiting for E2E stack readiness (probes={list(probe_names)}, timeout={timeout_s}s, host={host})...")
    deadline = time.monotonic() + timeout_s
    last_failed: str | None = None

    while time.monotonic() < deadline:
        last_failed = None
        for name in probe_names:
            ok = _PROBE_FUNCS[name](ports=ports, host=host, compose_files=compose_files)
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
    _dump_e2e_compose_logs(compose_files)
    compose_hint = ""
    if failed == "creative-agent" and use_container_exec(host):
        health, compose_err = _compose_service_health("creative-agent", compose_files)
        if compose_err:
            compose_hint = f"; compose_ps_diag={compose_err}"
        else:
            compose_hint = f"; compose_health={health!r}"
    pytest.fail(
        f"E2E stack not ready after {timeout_s}s — failed probe: {failed} "
        f"(required order: {list(probe_names)}; host={host}; ports={dict(ports)}"
        f"{compose_hint})"
    )


def wait_for_e2e_stack(
    *,
    ports: E2EPorts,
    compose_files: Sequence[str] | None = None,
    host: str | None = None,
    timeout_s: float = 60.0,
    poll_interval_s: float = 2.0,
) -> None:
    """Ordered required probes; on timeout dump logs once and ``pytest.fail`` once.

    Always runs ``REQUIRED_E2E_PROBES`` in order and short-circuits on the first
    miss each poll. Parallel "any healthy" semantics are intentionally forbidden
    so creative-agent cannot be skipped when adcp ``/health`` is already up.
    """
    files = tuple(compose_files) if compose_files is not None else DEFAULT_E2E_COMPOSE_FILES
    resolved_host = host if host is not None else e2e_host_default()
    _wait_for_probes(
        REQUIRED_E2E_PROBES,
        ports=ports,
        compose_files=files,
        host=resolved_host,
        timeout_s=timeout_s,
        poll_interval_s=poll_interval_s,
    )
