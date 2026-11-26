"""Drive the GAM OAuth connection flow on the admin test client the way a browser does.

Shared by the tenant-binding tests (https://github.com/prebid/salesagent/issues/2205)
and the egress test of the callback's Google-400 branch, so "a member starts the
flow and comes back with the state it was issued" is written once. Since #2205 the
callback denies anything else before it exchanges the code, so every test that
wants to reach the exchange has to arrive this way.

``log_in`` states a member session with no tenant — none of the ``test_user`` /
``test_tenant_id`` keys that ``require_tenant_access`` accepts in test mode — so a
request that passes did so on membership.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

from tests.harness.admin_client import BROWSER_PAGE_HEADERS
from tests.helpers.admin_session import tenant_user_session

AUTH_CODE = "4/single-use-authorization-code"


def log_in(client, user) -> None:
    """Authenticate *client* as *user* with a plain session and no test-mode keys."""
    tenant_user_session(client, user.email)


def authorize(client, tenant_id: str):
    return client.get(f"/auth/gam/authorize/{tenant_id}", headers=BROWSER_PAGE_HEADERS)


def start_flow(client, tenant_id: str) -> str:
    """Authorize for *tenant_id* and return the ``state`` that step sent to Google."""
    response = authorize(client, tenant_id)
    assert response.status_code == 302, response.status
    location = urlsplit(response.headers["Location"])
    assert location.netloc == "accounts.google.com", location.geturl()
    return parse_qs(location.query)["state"][0]


def callback(client, state: str):
    return client.get(
        "/auth/gam/callback", query_string={"code": AUTH_CODE, "state": state}, headers=BROWSER_PAGE_HEADERS
    )
