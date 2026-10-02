"""``/debug/tenant`` reports the routing decision the app actually makes.

The debug endpoint exists to answer "which tenant did this ``Host`` reach", and the only
answer worth reporting is the one the root route acts on. When it performs its own host
lookup instead of asking ``route_landing_page``, the two can disagree — and an operator
debugging a proxy is then told a detection the deployment does not have, which the endpoint's
own comment calls worse than no endpoint.

The admin domain is where they diverge: the root route sends it to admin login and never
looks for a tenant, while a bare ``virtual_host`` lookup answers with whichever tenant
declares that host.

BDD cannot reach this: a debug route is not a buyer-visible AdCP response, so there is no
wire, no transport and no storyboard step to grade it on.
"""

from unittest.mock import patch

import pytest
from starlette.testclient import TestClient

from src.core.config import get_settings
from tests.factories import TenantFactory
from tests.harness._base import BareIntegrationEnv

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

ADMIN_HOST = "admin.debug-routes.example.com"


@pytest.mark.requires_db
def test_the_debug_report_names_a_tenant_only_where_the_root_route_serves_one(integration_db):
    from src.app import app

    with BareIntegrationEnv() as env:
        TenantFactory(tenant_id="dbg-admin", virtual_host=ADMIN_HOST)
        env._commit_factory_data()

        client = TestClient(app)
        with patch.object(get_settings().runtime, "admin_domain", ADMIN_HOST):
            root = client.get("/", headers={"Host": ADMIN_HOST}, follow_redirects=False)
            debug = client.get("/debug/tenant", headers={"Host": ADMIN_HOST})

    assert root.status_code == 302, f"the root route served this host instead of routing it to admin: {root.text}"
    assert root.headers["location"] == "/admin/login"

    reported = debug.json()
    assert reported["host"] == ADMIN_HOST
    assert reported["tenant_id"] is None, "the debug report names a tenant the root route never serves"
    assert reported["detection_method"] is None


@pytest.mark.requires_db
def test_the_debug_report_names_the_tenant_the_root_route_serves(integration_db):
    """The other half: a host a tenant declares resolves the same way on both routes."""
    from src.app import app

    with BareIntegrationEnv() as env:
        TenantFactory(tenant_id="dbg-served", name="Debug Publisher", virtual_host="served.debug-routes.example.com")
        env._commit_factory_data()

        client = TestClient(app)
        root = client.get("/", headers={"Host": "served.debug-routes.example.com"}, follow_redirects=False)
        debug = client.get("/debug/tenant", headers={"Host": "served.debug-routes.example.com"})

    assert root.status_code == 200, f"the root route did not serve the tenant's page: {root.text[:300]}"
    assert "Debug Publisher" in root.text

    reported = debug.json()
    assert reported["tenant_id"] == "dbg-served"
    assert reported["tenant_name"] == "Debug Publisher"
    assert reported["detection_method"] == "host"
