"""In-process transport for the authorization contract of prebid/salesagent#2203 and #2204.

The contract classes live in tests/helpers/admin_tenant_scoping_contract.py, which says
what each one grades. Importing them here collects them on the Flask ``test_client``
through the ``scoping_env`` fixture in tests/admin/conftest.py. The harness
(``tests/harness/admin_tenant_scoping.py``) logs in through ``/test/auth`` against a home
tenant that is never the target, so the decorator's test-mode bypass cannot grant and the
target tenant's ``User`` row is the only thing that decides; each rejection test fails if
the decorator is removed. Test data comes from factory-boy factories (``tests/CLAUDE.md``).

The two classes defined below run in process only: they sign in through OIDC and Google
OAuth, and the Docker stack has no identity provider (nor does the test process own the
server's ``SUPER_ADMIN_EMAILS``).
"""

from __future__ import annotations

import pytest

from tests.harness.admin_tenant_scoping import (
    GAM_ADMITTED,
    GAM_ROUTE_NAMES,
    AdminTenantScopingEnv,
    assert_membership_rejected,
)

# Collected here, not just imported: pytest picks up every Test* class in the module namespace.
from tests.helpers.admin_tenant_scoping_contract import (  # noqa: F401
    TestActiveMemberUnchanged,
    TestAnonymousDenied,
    TestNonMemberDenied,
)

pytestmark = [pytest.mark.admin, pytest.mark.requires_db]


class TestSingleSignOnCallers:
    """A session written by OIDC or Google login is decided like any other, never with a 500.

    Both logins store ``session["user"]`` as the email string and set no ``session["role"]``
    (``src/admin/blueprints/oidc.py``, ``src/admin/blueprints/auth.py``). The six GAM
    reporting routes answered such a session with 500 on every request, the caller's own
    tenant included (#2204). The harness drives the real login and callback, and checks the
    session has exactly that shape before any route is called.
    """

    @pytest.mark.parametrize("route", GAM_ROUTE_NAMES)
    def test_non_member_receives_403(self, sso_scoping_env: AdminTenantScopingEnv, route: str) -> None:
        sso_scoping_env.login_as_member_of_other_tenant()

        response = sso_scoping_env.send("GET", route)

        assert_membership_rejected(response, api_mode=True)

    @pytest.mark.parametrize("route", GAM_ROUTE_NAMES)
    def test_member_reaches_the_handler(self, sso_scoping_env: AdminTenantScopingEnv, route: str) -> None:
        sso_scoping_env.login_with_target_membership(is_active=True)

        response = sso_scoping_env.send("GET", route)

        assert (response.status_code, response.get_json()) == GAM_ADMITTED[route]


class TestSuperAdminReachesAnotherTenant:
    """A super-admin keeps cross-tenant access to the six GAM reporting routes.

    The maintainer amended #2204's AC4 to "made explicit, with a test that pins the behaviour"
    (https://github.com/prebid/salesagent/issues/2204#issuecomment-5712126259), per
    ``docs/security.md`` ("Super admins have full access to all tenants"). The caller signs in
    through OIDC at a tenant of its own, has no row in the target, and is a super-admin only
    because its email is in ``SUPER_ADMIN_EMAILS``. The same caller without that setting is
    refused by ``TestSingleSignOnCallers.test_non_member_receives_403`` (the ``oidc`` cases),
    so the pair fails if the guard admits everyone or admits no super-admin.
    """

    @pytest.mark.parametrize("route", GAM_ROUTE_NAMES)
    def test_super_admin_reaches_the_handler(self, super_admin_scoping_env: AdminTenantScopingEnv, route: str) -> None:
        super_admin_scoping_env.login_as_member_of_other_tenant()

        response = super_admin_scoping_env.send("GET", route)

        assert (response.status_code, response.get_json()) == GAM_ADMITTED[route]
