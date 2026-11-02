"""The landing page and the agent card publish ONE origin for a tenant: its stored one.

Both surfaces answer "where is this agent reachable", and a buyer reads them together — the
page shows the endpoints to paste into a client, the card is what a client fetches. The
stored ``virtual_host`` is the tenant's only statement of that origin, so a reader that
rebuilt it from the request's ``Host`` publishes whatever spelling the caller happened to
send: a request arriving without the port, or in another case, gets a page naming an origin
the card contradicts and nothing serves (#1845).

Tenant resolution matches host to host and folds case (``_same_host``), so one request
reaches the tenant under a ``Host`` that differs from the stored origin in BOTH port and
case. That is what makes the two readers separable: a page derived from the request host
cannot agree with a card derived from the row.

Integration rather than BDD: neither surface is an AdCP transport. The card is a
``/.well-known`` JSON document and the page is HTML, so no scenario can dispatch them
through the cross-transport harness — there is no tool, no envelope and no wire response to
assert on. Both are reachable over HTTP against the real app with a real tenant row, which
is what this drives.
"""

import re

import pytest
from starlette.testclient import TestClient

#: A host under the test TLD, stored WITH a port because the card publishes this verbatim
#: and a client connects to what the card says.
ORIGIN = "probe.adcp.test:8443"

#: How the request arrives: the same DNS name in a different case and with no port. Both
#: differences are ones a proxy or a client can introduce, and neither changes which tenant
#: is being addressed.
REQUEST_HOST = "PROBE.adcp.test"

#: Every absolute URL in the page whose host is this tenant's, however it is spelled. The
#: set of origins it yields is the page's answer to "where is this agent", and a second
#: member means the page named a host the card does not.
_TENANT_ORIGINS = re.compile(r"https?://[^/\s\"'<]*probe\.adcp\.test[^/\s\"'<]*", re.IGNORECASE)


@pytest.mark.requires_db
def _endpoint_mentions(html: str, origin: str) -> str:
    """Every URL on the page at *origin*, so a path mismatch prints what it found."""
    return "\n".join(sorted(set(re.findall(rf"https://{re.escape(origin)}\S*?(?=[\"'<\s])", html))))


def test_the_page_and_the_card_publish_the_stored_origin(integration_db):
    from src.app import app
    from tests.factories import AdapterConfigFactory, PrincipalFactory, TenantFactory
    from tests.harness import ProductEnv

    with ProductEnv(tenant_id="origin-t", principal_id="origin-p") as env:
        tenant = TenantFactory(tenant_id="origin-t", subdomain="origint", virtual_host=ORIGIN)
        PrincipalFactory(tenant=tenant, principal_id="origin-p")
        # The landing page shows endpoints only for a tenant whose ad server is configured;
        # without this it renders the pending-configuration page instead.
        AdapterConfigFactory(tenant=tenant, adapter_type="kevel", kevel_api_key="test-key", kevel_network_id="42")
        env._commit_factory_data()

        client = TestClient(app)

        card = client.get("/.well-known/agent-card.json", headers={"Host": REQUEST_HOST})
        assert card.status_code == 200, card.text
        card_urls = [interface["url"] for interface in card.json()["supportedInterfaces"]]
        assert card_urls == [f"https://{ORIGIN}/a2a"], f"the card published {card_urls}, not the stored origin"

        page = client.get("/", headers={"Host": REQUEST_HOST})
        assert page.status_code == 200, page.text
        html = page.text

        assert set(_TENANT_ORIGINS.findall(html)) == {f"https://{ORIGIN}"}, (
            f"the page published {sorted(set(_TENANT_ORIGINS.findall(html)))} for a tenant stored at "
            f"{ORIGIN!r}, so it and the card do not name the same agent"
        )
        assert f"https://{ORIGIN}/.well-known/agent-card.json" in html, "the page linked no card at the stored origin"

        # The PATHS have to agree too, not only the origin. The page and the card are two
        # publishers of one fact, and AGENT_ENDPOINT_PATHS is the fact -- it is also what
        # src/app.py mounts, so a path the page invents is an address nothing answers.
        from src.core.agent_identity import AGENT_ENDPOINT_PATHS

        for protocol, path in AGENT_ENDPOINT_PATHS.items():
            assert f"https://{ORIGIN}{path}" in html, (
                f"the page named no reachable {protocol.upper()} endpoint: expected "
                f"https://{ORIGIN}{path}, the path this deployment serves and the card "
                f"publishes, in\n{_endpoint_mentions(html, ORIGIN)}"
            )
