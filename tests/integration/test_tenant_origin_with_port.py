"""A tenant served on a non-default port: one stored origin, three readers.

``virtual_host`` stores the ORIGIN a tenant is served at. Three readers want different
parts of it, and satisfying two while breaking the third is what happened twice in one day:

* the agent card publishes the origin VERBATIM — a client connects to what the card says,
  so dropping the port sent every A2A client to a closed one (27 passing conformance
  checks to 0);
* ``publisher_properties[].publisher_domain`` wants the HOSTNAME — AdCP's pattern admits
  no colon, and feeding one in failed every product of the tenant, so ``get_products``
  answered INTERNAL_ERROR for the whole catalogue;
* tenant resolution wants to match either spelling, because a deployment should not have
  to know which form a proxy forwards.

One test, because the three only conflict with each other.
"""

import pytest
from starlette.testclient import TestClient

ORIGIN = "storyboard.adcp.test:8443"
HOSTNAME = "storyboard.adcp.test"


@pytest.mark.requires_db
def test_one_stored_origin_serves_all_three_readers(integration_db):
    from src.app import app
    from src.core.config_loader import tenant_id_for
    from tests.factories import PrincipalFactory, TenantFactory
    from tests.harness import ProductEnv

    with ProductEnv(tenant_id="port-t", principal_id="port-p") as env:
        tenant = TenantFactory(tenant_id="port-t", virtual_host=ORIGIN)
        PrincipalFactory(tenant=tenant, principal_id="port-p")
        env._commit_factory_data()

        # Publisher identity: the hostname, so the AdCP pattern accepts it.
        assert tenant.primary_domain == HOSTNAME

        # Resolution: either spelling names the same tenant; a host nobody declares, none.
        assert tenant_id_for(virtual_host=ORIGIN) == "port-t"
        assert tenant_id_for(virtual_host=HOSTNAME) == "port-t"
        assert tenant_id_for(virtual_host="nobody-declares-this.invalid") is None

        # The card: the origin verbatim, port included, because that is where to connect.
        response = TestClient(app).get("/.well-known/agent-card.json", headers={"Host": ORIGIN})
        assert response.status_code == 200, response.text
        urls = [interface["url"] for interface in response.json()["supportedInterfaces"]]
        assert urls == [f"https://{ORIGIN}/a2a"], f"card published {urls}, which no client can reach"
