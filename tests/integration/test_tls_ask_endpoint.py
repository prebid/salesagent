"""``GET /tls/ask``: the reverse proxy's on-demand TLS gate, against real tenant rows.

Caddy calls the endpoint with ``?domain=<host>`` before it requests a certificate and
issues only on a 2xx. These cases mount the routes a deployment opts into through
``include_optional_routers``, the function ``src/app.py`` composes the app with, and drive
them over PostgreSQL, so the active filter and the ``virtual_host`` lookup are the ones
production runs.

A tenant is served at the host it declares as its ``virtual_host`` and nowhere else, so a
tenant's ``subdomain`` under ``SALES_AGENT_DOMAIN`` is NOT a served host unless the tenant
declares it.

The route exists only when ``TLS_ASK_ENABLED`` is set; otherwise the path is as unknown as
any other.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from tests.factories import TenantFactory
from tests.helpers.settings_injection import inject_runtime

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

APEX = "agent.example.com"


def _deployment_app(monkeypatch, *, tls_ask_enabled: bool) -> TestClient:
    """A client for an app carrying the opt-in routes this deployment's settings select."""
    from src.app import include_optional_routers
    from src.core.config import get_settings

    inject_runtime(monkeypatch, sales_agent_domain=APEX, admin_domain=None, tls_ask_enabled=tls_ask_enabled)
    app = FastAPI()
    include_optional_routers(app, get_settings())
    return TestClient(app)


@pytest.fixture
def client(factory_session, monkeypatch):
    TenantFactory(tenant_id="t_acme", subdomain="acme", virtual_host="ads.publisher.example")
    TenantFactory(tenant_id="t_beta", subdomain="beta", virtual_host=f"beta.{APEX}")
    TenantFactory(tenant_id="t_port", subdomain="port", virtual_host="alt.publisher.example:8443")
    TenantFactory(tenant_id="t_gone", subdomain="gone", virtual_host="old.publisher.example", is_active=False)
    return _deployment_app(monkeypatch, tls_ask_enabled=True)


def test_disabled_route_does_not_exist(factory_session, monkeypatch):
    TenantFactory(tenant_id="t_acme", subdomain="acme", virtual_host="ads.publisher.example")
    client = _deployment_app(monkeypatch, tls_ask_enabled=False)
    assert client.get("/tls/ask", params={"domain": "ads.publisher.example"}).status_code == 404


def test_built_app_mounts_the_route_exactly_when_enabled():
    """``src.app`` was composed from ``src.app.settings``; the route is there iff that says so."""
    from src.app import app, settings

    mounted = {getattr(route, "path", None) for route in app.routes}
    assert ("/tls/ask" in mounted) is settings.runtime.tls_ask_enabled


@pytest.mark.parametrize(
    "host",
    [
        APEX,
        f"admin.{APEX}",
        "ads.publisher.example",
        "Ads.Publisher.Example",
        "ads.publisher.example.",
        f"beta.{APEX}",
        "alt.publisher.example",
    ],
)
def test_served_host_is_allowed(client, host):
    response = client.get("/tls/ask", params={"domain": host})
    assert response.status_code == 200


@pytest.mark.parametrize(
    "host",
    [
        f"acme.{APEX}",
        "old.publisher.example",
        f"gone.{APEX}",
        f"nobody.{APEX}",
        f"x.beta.{APEX}",
        f"beta.{APEX}.evil.example",
        "random.example.org",
        f"*.{APEX}",
        "ads.publisher.example:443",
        "",
    ],
)
def test_unserved_host_is_refused(client, host):
    response = client.get("/tls/ask", params={"domain": host})
    assert response.status_code == 403


def test_missing_domain_parameter_is_refused(client):
    assert client.get("/tls/ask").status_code == 403
