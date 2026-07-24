"""Behavioral oracles for E2E compose health readiness (host-path contract).

Complements the AST guard in ``test_architecture_e2e_stack_readiness``: that
module pins wiring; this module proves the success predicate and wait failure
naming so a dead ``"running"`` fallback cannot silently pass probes.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from tests.e2e.stack_readiness import (
    _compose_reports_ready,
    _compose_service_health,
    _probe_creative_agent,
    wait_for_e2e_stack,
)


def _ps_completed(stdout: str, *, returncode: int = 0) -> MagicMock:
    result = MagicMock()
    result.returncode = returncode
    result.stdout = stdout
    result.stderr = ""
    return result


class TestComposeReportsReadyContract:
    """Empty-Health / bare running must not satisfy the host-path hard gate."""

    def test_only_healthy_counts_as_ready(self):
        assert _compose_reports_ready("healthy") is True
        assert _compose_reports_ready("running") is False
        assert _compose_reports_ready("up") is False
        assert _compose_reports_ready(None) is False
        assert _compose_reports_ready("starting") is False

    def test_empty_health_running_state_is_not_ready(self):
        payload = json.dumps({"Health": "", "State": "running", "Name": "creative-agent"})
        with (
            patch("tests.e2e.stack_readiness.compose_available", return_value=True),
            patch("tests.e2e.stack_readiness.subprocess.run", return_value=_ps_completed(payload)),
        ):
            health, err = _compose_service_health("creative-agent", ("docker-compose.e2e.yml",))
        assert health is None
        assert err is None
        assert _compose_reports_ready(health) is False

    def test_explicit_healthy_is_ready(self):
        payload = json.dumps({"Health": "healthy", "State": "running"})
        with (
            patch("tests.e2e.stack_readiness.compose_available", return_value=True),
            patch("tests.e2e.stack_readiness.subprocess.run", return_value=_ps_completed(payload)),
        ):
            health, err = _compose_service_health("creative-agent", ("docker-compose.e2e.yml",))
        assert health == "healthy"
        assert err is None
        assert _compose_reports_ready(health) is True

    def test_host_creative_agent_probe_rejects_running_only(self):
        payload = json.dumps({"Health": "", "Status": "Up", "Name": "creative-agent"})
        with (
            patch("tests.e2e.stack_readiness.compose_available", return_value=True),
            patch("tests.e2e.stack_readiness.subprocess.run", return_value=_ps_completed(payload)),
        ):
            assert _probe_creative_agent(host="localhost", compose_files=("docker-compose.e2e.yml",)) is False


class TestWaitForE2EStackCreativeAgentOracle:
    """Creative-agent miss must fail the wait naming that probe."""

    def test_creative_agent_false_fails_naming_probe(self):
        def _fake_probe(name, **_kw):
            return name != "creative-agent"

        probes = {
            "postgres": lambda **kw: _fake_probe("postgres", **kw),
            "creative-agent": lambda **kw: _fake_probe("creative-agent", **kw),
            "adcp_health": lambda **kw: _fake_probe("adcp_health", **kw),
        }
        with (
            patch.dict("tests.e2e.stack_readiness._PROBE_FUNCS", probes, clear=False),
            patch("tests.e2e.stack_readiness._dump_e2e_compose_logs"),
            pytest.raises(pytest.fail.Exception, match="failed probe: creative-agent") as exc_info,
        ):
            wait_for_e2e_stack(
                ports={"mcp": 8000, "postgres": 5432},
                compose_files=("docker-compose.e2e.yml",),
                host="localhost",
                timeout_s=0.01,
                poll_interval_s=0.001,
            )
        assert "creative-agent" in str(exc_info.value)
