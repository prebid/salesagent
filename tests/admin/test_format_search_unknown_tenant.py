"""The admin format routes check access to, and existence of, the ``tenant_id`` they are given.

``/api/formats/search`` and ``/api/formats/list`` take the tenant from a query parameter
and hand it to the creative agent registry, which reads that tenant's
``use_default_creative_agent`` with ``one()``. For a tenant that does not exist that raised
"No row was found when one was required", and the routes' blanket ``except`` answered 500
carrying SQLAlchemy's text. A name that matches no tenant is a request for something that
is not there, so it is a 404, decided before the registry runs.

Each route is graded with BOTH answers, because a refusal row alone is satisfied by a route
that 404s everything: the control names a tenant that exists and must get past the check.
That tenant federates no creative agent and configures none, so its catalog is empty and
the control dials nothing.
"""

from __future__ import annotations

import pytest

from tests.factories import TenantFactory
from tests.helpers.admin_session import admin_auth_session

pytestmark = [pytest.mark.admin, pytest.mark.requires_db]

_TENANT_ID = "fmt_search_t1"
_NO_SUCH_TENANT = "fmt_search_no_such_tenant"

_SEARCH = "/api/formats/search?q=display&tenant_id={tenant_id}"
_LIST = "/api/formats/list?tenant_id={tenant_id}"


@pytest.fixture
def tenant(factory_session):
    return TenantFactory(tenant_id=_TENANT_ID, use_default_creative_agent=False)


@pytest.mark.parametrize("route", [_SEARCH, _LIST], ids=["search", "list"])
def test_an_unknown_tenant_is_refused_with_404(admin_client, tenant, route):
    admin_auth_session(admin_client, _TENANT_ID)

    response = admin_client.get(route.format(tenant_id=_NO_SUCH_TENANT))

    assert response.status_code == 404, response.get_data(as_text=True)
    assert response.get_json() == {"error": "Tenant not found"}


@pytest.mark.parametrize(
    ("route", "empty_catalog"),
    [(_SEARCH, {"formats": [], "count": 0}), (_LIST, {"agents": {}, "total_formats": 0})],
    ids=["search", "list"],
)
def test_a_known_tenant_gets_its_catalog(admin_client, tenant, route, empty_catalog):
    admin_auth_session(admin_client, _TENANT_ID)

    response = admin_client.get(route.format(tenant_id=_TENANT_ID))

    assert response.status_code == 200, response.get_data(as_text=True)
    assert response.get_json() == empty_catalog


_AGENTS = "/api/formats/agents?tenant_id={tenant_id}"


@pytest.mark.parametrize("route", [_SEARCH, _LIST, _AGENTS], ids=["search", "list", "agents"])
def test_a_user_of_another_tenant_is_refused_with_403(admin_client, tenant, route):
    """The tenant comes from the query, so the route checks access itself: no catalog, no fetch."""
    from src.core.database.models import UserRole
    from tests.factories import UserFactory
    from tests.helpers.admin_session import tenant_user_session

    outsider = UserFactory(tenant=TenantFactory(), role=UserRole.ADMIN)
    tenant_user_session(admin_client, outsider.email)

    response = admin_client.get(route.format(tenant_id=_TENANT_ID) + "&force_refresh=true")

    assert (response.status_code, response.get_json()) == (403, {"success": False, "error": "Access denied"})


@pytest.mark.parametrize("route", [_SEARCH, _LIST, _AGENTS], ids=["search", "list", "agents"])
def test_a_signed_in_account_with_no_tenant_is_refused_with_403(admin_client, tenant, route):
    """Completing the deployment's sign-in grants no tenant: require_auth alone must not open these."""
    from tests.helpers.admin_session import tenant_user_session

    tenant_user_session(admin_client, "nobody@unaffiliated.test")

    response = admin_client.get(route.format(tenant_id=_TENANT_ID))

    assert (response.status_code, response.get_json()) == (403, {"success": False, "error": "Access denied"})


def test_a_member_of_the_tenant_lists_its_creative_agents(admin_client, tenant):
    """The control for the refusals above: a viewer of the tenant gets past the check."""
    from src.core.database.models import UserRole
    from tests.factories import UserFactory
    from tests.helpers.admin_session import tenant_user_session

    member = UserFactory(tenant=tenant, role=UserRole.VIEWER)
    tenant_user_session(admin_client, member.email)

    response = admin_client.get(_AGENTS.format(tenant_id=_TENANT_ID))

    assert response.status_code == 200, response.get_data(as_text=True)
