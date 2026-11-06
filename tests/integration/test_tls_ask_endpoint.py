"""``GET /tls/ask``: what ``tests/bdd/features/local-tls-ask.feature`` cannot vary.

That feature grades the gate on the composed app, on every transport. A live stack has one
spelling of each domain setting and one value of ``TLS_ASK_ENABLED`` for its whole run, so the
two things that differ per deployment are graded here: whether the composition mounts the
route, and how the gate reads the configured domains.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from tests.helpers.settings_injection import inject_runtime

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

APEX = "agent.example.com"
REPO_ROOT = Path(__file__).resolve().parents[2]

#: Prints whether the app ``src/app.py`` composed at import carries the route.
_ROUTE_IS_MOUNTED = "import src.app as m; print('/tls/ask' in {getattr(r, 'path', None) for r in m.app.routes})"


def _ask(monkeypatch, domain: str, **runtime: str | None) -> int:
    """The gate's answer about *domain* under these settings, from the routes they select."""
    from src.app import include_optional_routers
    from src.core.config import get_settings

    inject_runtime(monkeypatch, tls_ask_enabled=True, **runtime)
    app = FastAPI()
    include_optional_routers(app, get_settings())
    return TestClient(app).get("/tls/ask", params={"domain": domain}).status_code


@pytest.mark.parametrize(("value", "mounted"), [("true", "True"), ("false", "False")])
def test_composed_app_mounts_the_route_exactly_when_enabled(value, mounted):
    """A fresh interpreter per value: ``src.app`` reads the setting once, when it is imported."""
    result = subprocess.run(
        [sys.executable, "-c", _ROUTE_IS_MOUNTED],
        env={**os.environ, "TLS_ASK_ENABLED": value},
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.splitlines()[-1] == mounted


@pytest.mark.parametrize("configured", ["Agent.Example.COM", f"{APEX}:8443", f"{APEX}."])
@pytest.mark.parametrize("asked", [APEX, f"admin.{APEX}"])
def test_configured_domain_is_read_as_a_hostname(factory_session, monkeypatch, configured, asked):
    """Any spelling of ``SALES_AGENT_DOMAIN`` names the same host, and so does the admin domain derived from it."""
    assert _ask(monkeypatch, asked, sales_agent_domain=configured, admin_domain=None) == 200


@pytest.mark.parametrize("configured", ["Console.Ops.Example", "console.ops.example:8443", "console.ops.example."])
def test_explicit_admin_domain_is_read_as_a_hostname(factory_session, monkeypatch, configured):
    assert _ask(monkeypatch, "console.ops.example", sales_agent_domain=APEX, admin_domain=configured) == 200


def test_explicit_admin_domain_replaces_the_derived_one(factory_session, monkeypatch):
    runtime = {"sales_agent_domain": APEX, "admin_domain": "console.ops.example"}
    assert _ask(monkeypatch, f"admin.{APEX}", **runtime) == 403


@pytest.mark.parametrize("asked", [APEX, f"admin.{APEX}"])
def test_unset_domains_serve_nothing(factory_session, monkeypatch, asked):
    assert _ask(monkeypatch, asked, sales_agent_domain=None, admin_domain=None) == 403
