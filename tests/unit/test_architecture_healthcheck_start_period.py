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

from tests.unit._architecture_helpers import repo_root

# (compose file, service) pairs found carrying this exact healthcheck shape.
# postgres/proxy/creative-pg are deliberately NOT here: pg_isready/curl
# against their OWN lightweight process is a different, faster-starting
# risk profile than a whole Python app server warming up under load.
# creative-agent already has start_period: 30s and is the existing correct
# pattern these instances are being brought in line with.
_MUST_HAVE_START_PERIOD = (
    ("docker-compose.e2e.yml", "adcp-server"),
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
