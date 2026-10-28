"""Guard: every curl-based healthcheck on a Python app server (adcp-server,
admin-ui) across every docker-compose*.yml declares a start_period.

Reported live: adcp-server showed as UNHEALTHY (surfaced loudly — it's a
REQUIRED service) during ordinary startup under heavy concurrent test load,
not because it was actually broken. Root cause: its healthcheck had no
start_period, so Docker starts counting failed checks from container start
with no grace window — a slow-to-warm-up container under load goes straight
to genuine `unhealthy` after `retries` failed checks, never through the
`starting` status a start_period would give it. cassini (the CI-offload
tool that surfaces this) already excludes `starting` from what it reports
as unhealthy; that only helps if the compose file actually gives Docker a
start_period to report `starting` during.

The bug wasn't confined to one file: the identical healthcheck shape
(curl .../health, interval 30s, timeout 10s, retries 3, no start_period)
was found in THREE compose files during this fix's own codebase-wide
disease scan — docker-compose.e2e.yml (the reported instance),
docker-compose.yml (local dev), and docker-compose.multi-tenant.yml
(adcp-server AND admin-ui). Parametrized across all four rather than
guarding only the one that was reported.

Originally read through `docker compose config` so the guard saw what Docker
ACTUALLY runs after interpolation and anchor merging, not the pre-merge source
text. That made it unrunnable wherever the docker CLI is absent — which is the
unit environment itself: on the CI box every one of these errored with
`FileNotFoundError: 'docker'`, so the guard graded nothing exactly where it was
meant to run. It now reads the YAML in-process and keeps the original concern as
an explicit assertion: no healthcheck block may contain an anchor/merge key or
`${...}` interpolation, so if someone later parameterizes one, this fails loudly
instead of silently checking text Docker would have rewritten.
"""

from __future__ import annotations

import re

import pytest
import yaml

from tests.unit._architecture_helpers import compose_service, load_yaml_mapping, repo_root

# (compose file, service) pairs that must declare start_period.
# postgres and creative-pg are not here: pg_isready against their own process
# is a different risk from a Python app server warming up under load.
# proxy is here because it fronts adcp-server. Without a start_period its
# probe fails with 502 while adcp-server is still inside its own start_period.
# creative-agent already has start_period: 30s and is the existing correct
# pattern these instances are being brought in line with.
_MUST_HAVE_START_PERIOD = (
    ("docker-compose.e2e.yml", "adcp-server"),
    ("docker-compose.e2e.yml", "proxy"),
    ("docker-compose.yml", "adcp-server"),
    ("docker-compose.multi-tenant.yml", "adcp-server"),
    ("docker-compose.multi-tenant.yml", "admin-ui"),
)


#: What would make a raw YAML read diverge from `docker compose config`.
_NEEDS_DOCKER_RESOLUTION = re.compile(r"\$\{|^<<$|^\*")


def _resolved_healthcheck(compose_file: str, service: str) -> dict:
    doc = yaml.safe_load((repo_root() / compose_file).read_text())
    hc = doc["services"][service].get("healthcheck") or {}
    unresolved = [
        f"{k}: {v!r}"
        for k, v in hc.items()
        if _NEEDS_DOCKER_RESOLUTION.search(str(k)) or _NEEDS_DOCKER_RESOLUTION.search(str(v))
    ]
    assert not unresolved, (
        f"{compose_file}:{service}'s healthcheck now needs Docker's own resolution "
        f"(interpolation or anchor merge) — a raw YAML read no longer sees what Docker runs: {unresolved}"
    )
    return hc


@pytest.mark.arch_guard
@pytest.mark.parametrize(
    "compose_file,service", _MUST_HAVE_START_PERIOD, ids=[f"{f}:{s}" for f, s in _MUST_HAVE_START_PERIOD]
)
def test_app_server_healthcheck_has_a_start_period(compose_file: str, service: str) -> None:
    hc = _resolved_healthcheck(compose_file, service)
    assert hc.get("start_period"), (
        f"{compose_file}:{service}'s healthcheck has no start_period — Docker will mark "
        f"ordinary, slow startup under load as genuinely unhealthy rather than 'starting' "
        f"(resolved healthcheck: {hc!r})"
    )


def _duration_seconds(value: object) -> float:
    """Parse a compose duration (``60s``, ``1m``, or a number) into seconds."""
    if value is None:
        raise AssertionError("duration value is missing")
    if isinstance(value, (int, float)):
        seconds = float(value)
    else:
        text = str(value).strip().lower()
        if text.endswith("ms"):
            seconds = float(text[:-2]) / 1000.0
        elif text.endswith("s"):
            seconds = float(text[:-1])
        elif text.endswith("m"):
            seconds = float(text[:-1]) * 60.0
        elif text.endswith("h"):
            seconds = float(text[:-1]) * 3600.0
        else:
            seconds = float(text)
    if seconds <= 0:
        raise AssertionError(f"duration must be positive (got {value!r})")
    return seconds


def _e2e_compose() -> dict:
    return load_yaml_mapping(repo_root() / "docker-compose.e2e.yml")


@pytest.mark.arch_guard
def test_e2e_proxy_waits_until_adcp_server_is_healthy() -> None:
    """proxy does not start until adcp-server answers /health.

    The probe budget is ``interval * retries >= start_period``. Pinning the
    three numbers separately stays green when the probe is ``CMD true`` or
    when start_period grows past the budget.
    """
    compose = _e2e_compose()
    proxy_deps = compose_service(compose, "proxy").get("depends_on") or {}
    assert isinstance(proxy_deps, dict), "proxy depends_on must be a mapping."
    adcp_dep = proxy_deps.get("adcp-server") or {}
    assert isinstance(adcp_dep, dict) and adcp_dep.get("condition") == "service_healthy", (
        f"proxy must wait for adcp-server to be healthy (got {adcp_dep!r})."
    )
    adcp = _resolved_healthcheck("docker-compose.e2e.yml", "adcp-server")
    assert "/health" in " ".join(str(part) for part in adcp.get("test") or []), (
        f"adcp-server must probe its own /health (got {adcp.get('test')!r})."
    )
    budget = _duration_seconds(adcp.get("interval")) * int(str(adcp.get("retries", 0)))
    assert budget >= _duration_seconds(adcp.get("start_period")), (
        f"adcp-server probe budget {budget}s is shorter than start_period {adcp.get('start_period')!r}."
    )
    proxy = _resolved_healthcheck("docker-compose.e2e.yml", "proxy")
    proxy_budget = _duration_seconds(proxy.get("interval")) * int(str(proxy.get("retries", 0)))
    assert proxy_budget >= _duration_seconds(proxy.get("start_period")), (
        f"proxy probe budget {proxy_budget}s is shorter than start_period {proxy.get('start_period')!r}."
    )
    creative = _resolved_healthcheck("docker-compose.e2e.yml", "creative-agent")
    assert creative.get("test"), "creative-agent healthcheck.test must be non-empty."


@pytest.mark.arch_guard
def test_e2e_fronting_services_wait_for_healthchecked_upstreams() -> None:
    """A service that fronts a healthchecked upstream uses condition: service_healthy.

    The list form starts the front before /health answers. tls-proxy is in
    this file and is covered by the walk, not by a special case.
    """
    services = _e2e_compose()["services"]
    problems: list[str] = []
    for name, service in services.items():
        if not isinstance(service, dict):
            continue
        deps = service.get("depends_on")
        if not deps:
            continue
        if isinstance(deps, list):
            healthchecked = [
                upstream for upstream in deps if compose_service({"services": services}, upstream).get("healthcheck")
            ]
            if healthchecked:
                problems.append(f"{name} lists healthchecked upstreams {healthchecked}")
            continue
        if not isinstance(deps, dict):
            problems.append(f"{name} depends_on is {type(deps).__name__}")
            continue
        for upstream, spec in deps.items():
            if not compose_service({"services": services}, upstream).get("healthcheck"):
                continue
            condition = spec.get("condition") if isinstance(spec, dict) else None
            if condition != "service_healthy":
                problems.append(f"{name} -> {upstream} condition is {condition!r}")
    assert not problems, "e2e compose fronts a healthchecked upstream without waiting:\n" + "\n".join(problems)
