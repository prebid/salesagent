"""Authorization regression tests for the four tenant-scoped admin routes of #2203.

``GET /api/tenant/<tenant_id>/revenue-chart``, ``GET /api/tenant/<tenant_id>/products``,
``GET /api/tenant/<tenant_id>/products/suggestions`` and ``GET,POST
/tenant/<tenant_id>/policy/rules`` take the tenant from the URL. Before #2203 they carried
only ``require_auth()``, which checks that *somebody* is logged in. A tenant-scoped route must
prove the caller is an active member of that tenant, and exactly one component does that:
``require_tenant_access`` (``src/admin/utils/helpers.py``). Every expectation below is that
decorator's observable contract:

* anonymous        -> JSON 401 ``{"error": "Authentication required"}`` on the three
                      ``api_mode`` routes; redirect to the tenant login page on the HTML route
* other tenant     -> 403 (JSON ``{"error": "Access denied"}`` on the API routes)
* inactive member  -> 403, same shape (``is_active=False`` row in the requested tenant)
* active member    -> today's behaviour, same status and body
* any rejection    -> no state change

The three classes are the contract, written once. Here they run on the Flask
``test_client`` through the ``scoping_env`` fixture in tests/admin/conftest.py;
tests/e2e/test_admin_tenant_scoping_e2e.py imports them and supplies its own
``scoping_env`` against the Docker stack. The harness
(``tests/harness/admin_tenant_scoping.py``) logs in through ``/test/auth`` against a home
tenant that is never the target, so the decorator's test-mode bypass cannot grant and the
target tenant's ``User`` row is the only thing that decides; each rejection test fails if
the decorator is removed. Test data comes from factory-boy factories (``tests/CLAUDE.md``).
"""

from __future__ import annotations

import pytest

from tests.harness.admin_tenant_scoping import (
    ACTIVE_BUY_BUDGET,
    ROUTE_CASE_PARAMS,
    AdminTenantScopingEnv,
    assert_anonymous_rejected,
    assert_membership_rejected,
    expected_suggestions_body,
    redirect_path,
    route_cases,
)

pytestmark = [pytest.mark.admin, pytest.mark.requires_db]

_ROUTE_IDS, _ROUTE_ROWS = route_cases()


class TestAnonymousDenied:
    """Unauthenticated callers are rejected before the handler runs, with no state change."""

    @pytest.mark.parametrize(ROUTE_CASE_PARAMS, _ROUTE_ROWS, ids=_ROUTE_IDS)
    def test_anonymous_is_rejected(
        self, scoping_env: AdminTenantScopingEnv, method: str, route: str, api_mode: bool
    ) -> None:
        scoping_env.clear_auth()

        response = scoping_env.send(method, route)

        assert_anonymous_rejected(response, api_mode, scoping_env.target.tenant_id)
        assert scoping_env.target_state() == scoping_env.seeded_state


class TestNonMemberDenied:
    """A logged-in user without an active membership in the requested tenant receives 403.

    One caller is an active member somewhere else; the other has a row in the requested
    tenant with ``is_active=False``. Both must be refused identically.
    """

    @pytest.mark.parametrize(ROUTE_CASE_PARAMS, _ROUTE_ROWS, ids=_ROUTE_IDS)
    def test_member_of_other_tenant_receives_403(
        self, scoping_env: AdminTenantScopingEnv, method: str, route: str, api_mode: bool
    ) -> None:
        scoping_env.login_as_member_of_other_tenant()

        response = scoping_env.send(method, route)

        assert_membership_rejected(response, api_mode)
        assert scoping_env.target_state() == scoping_env.seeded_state

    @pytest.mark.parametrize(ROUTE_CASE_PARAMS, _ROUTE_ROWS, ids=_ROUTE_IDS)
    def test_inactive_member_receives_403(
        self, scoping_env: AdminTenantScopingEnv, method: str, route: str, api_mode: bool
    ) -> None:
        scoping_env.login_with_target_membership(is_active=False)

        response = scoping_env.send(method, route)

        assert_membership_rejected(response, api_mode)
        assert scoping_env.target_state() == scoping_env.seeded_state


class TestActiveMemberUnchanged:
    """An active same-tenant ``User`` row is enough; status and body stay exactly as today."""

    def test_revenue_chart_lists_the_tenant_buy(self, scoping_env: AdminTenantScopingEnv) -> None:
        scoping_env.login_with_target_membership(is_active=True)

        response = scoping_env.send("GET", "revenue chart API")

        assert response.status_code == 200
        assert response.get_json() == {
            "labels": [scoping_env.target.principal_name],
            "values": [float(ACTIVE_BUY_BUDGET)],
        }

    def test_products_lists_the_tenant_catalogue(self, scoping_env: AdminTenantScopingEnv) -> None:
        scoping_env.login_with_target_membership(is_active=True)

        response = scoping_env.send("GET", "products API")

        assert response.status_code == 200
        assert response.get_json() == {"products": scoping_env.target.products_payload}

    def test_product_suggestions_list_the_default_catalogue(self, scoping_env: AdminTenantScopingEnv) -> None:
        scoping_env.login_with_target_membership(is_active=True)

        response = scoping_env.send("GET", "product suggestions API")

        assert response.status_code == 200
        assert response.get_json() == expected_suggestions_body()

    @pytest.mark.parametrize("method", ["GET", "POST"])
    def test_policy_rules_redirects_to_policy_index(self, scoping_env: AdminTenantScopingEnv, method: str) -> None:
        scoping_env.login_with_target_membership(is_active=True)

        response = scoping_env.send(method, "policy rules page")

        assert response.status_code == 302
        assert redirect_path(response) == f"/tenant/{scoping_env.target.tenant_id}/policy/"
