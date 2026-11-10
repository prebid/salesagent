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
(``EgressPolicy.check_registration`` via the ``(bool, str)`` wrapper — scheme,
hostname blocklist, IP-literal ranges; https is unconditional since GH #1757) rather
than resolving DNS: ``creative-agent`` is a compose service name that resolves
only inside the stack, which is the whole point of running the suite there.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

import pytest
import yaml

from src.core.config import IntegrationSettings
from src.core.creative_agent_registry import PUBLIC_DEFAULT_AGENT_URL
from src.core.webhook_validator import WebhookURLValidator
from tests.unit._architecture_helpers import repo_root

_COMPOSE = "docker-compose.e2e.yml"
_WORKFLOW = ".github/workflows/ci.yml"


#: Where the pinned reference creative agent serves MCP: ``POST <base>/api/creative-agent/mcp``
#: (adcp ``server/src/creative-agent/index.ts``). The bare ``/api/creative-agent`` is refused
#: by its CSRF check, so a stack naming it spends every call's retry budget before the client's
#: ``/mcp`` fallback.
_REFERENCE_AGENT_MCP_PATH = "/api/creative-agent/mcp"


def _service_environments() -> dict[str, dict[str, str]]:
    """Each e2e compose service's environment, as a mapping."""
    compose = yaml.safe_load((repo_root() / _COMPOSE).read_text())
    environments: dict[str, dict[str, str]] = {}
    for service_name, service in (compose.get("services") or {}).items():
        env = (service or {}).get("environment") or {}
        if isinstance(env, list):  # `- KEY=value` form
            env = dict(item.split("=", 1) for item in env if "=" in item)
        environments[service_name] = {str(key): str(value) for key, value in env.items()}
    return environments


def _configured_creative_agent_urls() -> list[tuple[str, str]]:
    """Every literal ``CREATIVE_AGENT_URL`` the e2e compose stack sets."""
    return [
        (service_name, env["CREATIVE_AGENT_URL"])
        for service_name, env in _service_environments().items()
        if env.get("CREATIVE_AGENT_URL")
    ]


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

    ``CREATIVE_AGENT_URL`` is the MCP endpoint and is dialled as given, so that exact
    string is what has to pass.
    """
    is_safe, error = WebhookURLValidator.validate_webhook_url_registration(url)

    assert is_safe, (
        f"{_COMPOSE} points service {service!r} at {url!r}, which the armed "
        f"fire-time gate rejects: {error}. Every test in "
        f"tests/integration/test_creative_agent_live.py that dials the agent "
        f"would fail with 'not an allowed destination'. Reach the agent by "
        f"service name on the stack's own network — do NOT add a testing "
        f"carve-out to the gate, and do NOT publish a host port."
    )


@pytest.mark.parametrize("service, url", _configured_creative_agent_urls())
def test_configured_destination_is_the_reference_agents_mcp_endpoint(service: str, url: str) -> None:
    """The stack dials the endpoint itself: MCP mandates no path, so nothing may be appended."""
    assert urlsplit(url).path == _REFERENCE_AGENT_MCP_PATH, (
        f"{_COMPOSE} points service {service!r} at {url!r}. CREATIVE_AGENT_URL is the MCP "
        f"endpoint the seller dials, and the pinned reference agent serves MCP only at "
        f"{_REFERENCE_AGENT_MCP_PATH!r}."
    )


@pytest.mark.parametrize("service, url", _configured_creative_agent_urls())
def test_every_endpoint_comes_with_the_identity_it_stands_in_for(service: str, url: str) -> None:
    """A service that dials the stack's agent also names whose identity that agent carries.

    The in-network agent stands in for the PUBLIC reference agent: every reference the stack
    seeds or serves names it. The identity must load as a settings value, which refuses
    anything but an origin.
    """
    origin = _service_environments()[service].get("CREATIVE_AGENT_ORIGIN")

    assert origin == PUBLIC_DEFAULT_AGENT_URL, (
        f"{_COMPOSE} service {service!r} dials {url!r} but sets CREATIVE_AGENT_ORIGIN={origin!r}; "
        f"the stack's agent stands in for {PUBLIC_DEFAULT_AGENT_URL!r}."
    )
    assert IntegrationSettings(creative_agent_origin=origin).creative_agent_origin == origin


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
