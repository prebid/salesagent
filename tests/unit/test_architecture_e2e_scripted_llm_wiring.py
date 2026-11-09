"""Structural guard: every e2e server's model clients reach the scripted origin the test programs.

The get_products ranking and policy scenarios run on e2e_rest because the live server's
model calls go to ``scripted-llm`` (``tests/e2e/scripted_llm_origin_service.py``), whose
answers the test installs. That takes three sites in step, and a half-wired stack does not
fail where it is broken: a server whose Gemini client goes elsewhere, or an origin the
runner never started, makes every such scenario fall back to catalog order or skip its
policy check, and fail as a production defect.

1. ``GOOGLE_GEMINI_BASE_URL`` on the ``adcp-server`` environment (which the storyboard
   server and the per-worker servers inherit) names a compose service, and
   ``ANTHROPIC_BASE_URL`` names the same one: a model production must refuse to build for
   an Anthropic row would otherwise fail against the real vendor, the consumer would fail
   open, and the scenario asserting the refusal would pass with the refusal reverted;
2. that service runs the scripted origin module on the port the URL names;
3. ``run_all_tests.sh`` starts it, and the test's control client (``tests/e2e/_scripted_llm.py``)
   addresses the same origin the server dials.

A fourth fact rides along: the server's own platform key is the ``E2E_PLATFORM_AI_KEY`` the
harness scripts when a scenario declares the platform's credential. A different value would
leave that declaration unscripted, and a scenario asserting "ranking did not fall back to the
platform" would pass because the origin refused the platform's call, not because production
never made it.

The origin's own behaviour, driven by production's real agents, is
``tests/e2e/test_scripted_llm_origin_service.py``.
"""

from __future__ import annotations

import os
from unittest.mock import patch
from urllib.parse import urlsplit

import pytest

from tests.e2e._scripted_llm import E2E_PLATFORM_AI_KEY, scripted_llm_origin
from tests.unit._architecture_helpers import load_yaml, repo_root, runner_started_services

_REPO_ROOT = repo_root()
_SERVICES = load_yaml(_REPO_ROOT / "docker-compose.e2e.yml")["services"]
_BASE_URL = _SERVICES["adcp-server"]["environment"]["GOOGLE_GEMINI_BASE_URL"]


@pytest.mark.arch_guard
def test_the_server_gemini_base_url_names_the_scripted_origin_service() -> None:
    target = urlsplit(_BASE_URL)
    service = _SERVICES.get(target.hostname or "")

    assert service is not None, f"GOOGLE_GEMINI_BASE_URL={_BASE_URL!r} names no compose service"
    assert service["command"] == ["python", "-m", "tests.e2e.scripted_llm_origin_service"]
    assert service["environment"]["PORT"] == str(target.port)


@pytest.mark.arch_guard
def test_the_server_anthropic_base_url_names_the_same_origin() -> None:
    assert _SERVICES["adcp-server"]["environment"]["ANTHROPIC_BASE_URL"] == _BASE_URL


@pytest.mark.arch_guard
@pytest.mark.parametrize("variable", ["GOOGLE_GEMINI_BASE_URL", "ANTHROPIC_BASE_URL"])
def test_every_server_inherits_the_base_url(variable: str) -> None:
    """The storyboard server merges the adcp-server environment rather than restating it."""
    assert _SERVICES["adcp-server-storyboard"]["environment"][variable] == _BASE_URL


@pytest.mark.arch_guard
def test_the_runner_starts_the_scripted_origin() -> None:
    started = runner_started_services((_REPO_ROOT / "run_all_tests.sh").read_text(encoding="utf-8"))

    assert urlsplit(_BASE_URL).hostname in started


@pytest.mark.arch_guard
def test_the_control_client_addresses_the_origin_the_server_dials() -> None:
    with patch.dict(os.environ):
        os.environ.pop("SCRIPTED_LLM_ORIGIN", None)
        assert scripted_llm_origin() == _BASE_URL


@pytest.mark.arch_guard
def test_every_server_carries_the_platform_key_the_harness_scripts() -> None:
    assert _SERVICES["adcp-server"]["environment"]["GEMINI_API_KEY"] == E2E_PLATFORM_AI_KEY
    assert _SERVICES["adcp-server-storyboard"]["environment"]["GEMINI_API_KEY"] == E2E_PLATFORM_AI_KEY
