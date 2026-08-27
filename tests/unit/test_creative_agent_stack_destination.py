"""Guard: every CI/test topology must hand the creative agent a destination the
armed fire-time SSRF gate accepts.

``creative_agent_registry._fetch_formats_via_raw_mcp`` dials the configured
agent through ``check_url_ssrf`` with no ``require_https`` and no testing
carve-out. Its comment states the deliberate position: the stack "puts its
compose network on a NON-private subnet ... precisely so the armed gate passes
on ``http://creative-agent:8080`` rather than needing an exemption". So the
burden is on the TOPOLOGY to offer an acceptable destination, never on the gate
to make an exception.

salesagent-2xnqw: ``.github/workflows/ci.yml`` ran the ``Integration
(creative)`` group on the HOST against a loopback-published agent
(``http://localhost:9999/api/creative-agent``). ``localhost`` is in
``BLOCKED_HOSTNAMES``, so the gate refused it and all 17 dialling tests in
``tests/integration/test_creative_agent_live.py`` failed — against a file that
is byte-identical to origin/main. The fix ran that group in-network, where the
agent is reached by service name like every other dependency.

This guard grades the SHAPE of every configured destination
(``check_url_syntax`` — scheme, hostname blocklist, IP-literal ranges) rather
than resolving DNS: ``creative-agent`` is a compose service name that resolves
only inside the stack, which is the whole point of running the suite there.
"""

from __future__ import annotations

import re

import pytest
import yaml

from src.core.security.url_validator import check_url_syntax
from tests.unit._architecture_helpers import repo_root

_COMPOSE = "docker-compose.e2e.yml"
_WORKFLOW = ".github/workflows/ci.yml"


def _configured_creative_agent_urls() -> list[tuple[str, str]]:
    """Every literal ``CREATIVE_AGENT_URL`` the e2e compose stack sets."""
    compose = yaml.safe_load((repo_root() / _COMPOSE).read_text())
    found: list[tuple[str, str]] = []
    for service_name, service in (compose.get("services") or {}).items():
        env = (service or {}).get("environment") or {}
        if isinstance(env, list):  # `- KEY=value` form
            env = dict(item.split("=", 1) for item in env if "=" in item)
        url = env.get("CREATIVE_AGENT_URL")
        if url:
            found.append((service_name, str(url)))
    return found


def test_compose_sets_at_least_one_creative_agent_url() -> None:
    """Anti-vacuity: the loop below must actually have something to grade."""
    configured = _configured_creative_agent_urls()

    assert configured, (
        f"{_COMPOSE} sets no CREATIVE_AGENT_URL. Either the stack stopped "
        f"providing the reference agent or this guard's parser drifted — "
        f"either way it is no longer grading anything."
    )


@pytest.mark.parametrize("service, url", _configured_creative_agent_urls())
def test_configured_destination_passes_the_armed_gate(service: str, url: str) -> None:
    """The URL handed to the gated fetch path must survive the gate's shape checks.

    ``_fetch_formats_via_raw_mcp`` appends ``/mcp`` before validating, so that
    exact string is what has to pass — not the bare base URL.
    """
    mcp_url = url.rstrip("/") + "/mcp"

    is_safe, error = check_url_syntax(mcp_url)

    assert is_safe, (
        f"{_COMPOSE} points service {service!r} at {url!r}, which the armed "
        f"fire-time gate rejects: {error}. Every test in "
        f"tests/integration/test_creative_agent_live.py that dials the agent "
        f"would fail with 'not an allowed destination'. Reach the agent by "
        f"service name on the stack's own network — do NOT add a testing "
        f"carve-out to the gate, and do NOT publish a host port."
    )


def test_ci_workflow_does_not_hardcode_a_creative_agent_url() -> None:
    """CI must inherit the destination from the stack, never restate it.

    Two sources of truth for one URL is what let the host path drift onto
    loopback while the compose path stayed correct. ``run_all_tests_host.sh``
    already models the right shape by asking the stack script for the value.
    """
    workflow = (repo_root() / _WORKFLOW).read_text()

    hardcoded = re.findall(r"CREATIVE_AGENT_URL\s*:\s*(.+)", workflow)

    assert not hardcoded, (
        f"{_WORKFLOW} sets CREATIVE_AGENT_URL directly ({hardcoded!r}). The "
        f"destination belongs to the stack that serves the agent "
        f"({_COMPOSE}); restating it in CI is how it drifted onto loopback and "
        f"broke 17 tests (salesagent-2xnqw). Run the suite in-network and let "
        f"the stack supply it."
    )
