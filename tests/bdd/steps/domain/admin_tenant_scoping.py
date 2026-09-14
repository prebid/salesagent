"""BDD step definitions for BR-ADMIN-TENANT-SCOPING: tenant-scoped admin routes (#2203).

Steps for the four routes that take ``<tenant_id>`` from the URL. The harness
(``AdminTenantScopingEnv``) is provided by the ``_harness_env`` fixture in
tests/bdd/conftest.py. The generic admin Then steps (``the JSON response returns
status``, ``the JSON response has "key" as "value"``, ``the page returns status``)
come from tests/bdd/steps/domain/admin_accounts.py and read the same
``ctx["admin_page"]`` key.

"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pytest_bdd import given, parsers, then, when

from tests.bdd.steps.domain.admin_accounts import _require_admin_page
from tests.harness.admin_tenant_scoping import ACTIVE_BUY_BUDGET, expected_suggestions_body, redirect_path

if TYPE_CHECKING:
    from tests.harness.admin_tenant_scoping import AdminTenantScopingEnv


def _env(ctx: dict) -> AdminTenantScopingEnv:
    """Get the tenant-scoping harness from context."""
    return ctx["env"]


# ═══════════════════════════════════════════════════════════════════════════
# GIVEN steps
# ═══════════════════════════════════════════════════════════════════════════


@given("a target tenant with one catalogue product and one active media buy")
def given_target_tenant(ctx: dict) -> None:
    """Seed the tenant every scenario reads; the harness snapshots its state."""
    _env(ctx).seed_target_tenant()


@given("the caller is not authenticated")
def given_caller_anonymous(ctx: dict) -> None:
    """Drop any session."""
    _env(ctx).clear_auth()


@given("the caller is an active member of a different tenant")
def given_caller_member_elsewhere(ctx: dict) -> None:
    """Logged in with an active User row in another tenant and no row in the target."""
    _env(ctx).login_as_member_of_other_tenant()


@given("the caller's membership in the target tenant is inactive")
def given_caller_inactive_member(ctx: dict) -> None:
    """Logged in with a User row in the target tenant that has is_active=False."""
    _env(ctx).login_with_target_membership(is_active=False)


@given("the caller is an active member of the target tenant")
def given_caller_active_member(ctx: dict) -> None:
    """Logged in with an active User row in the target tenant — never the super-admin bypass."""
    _env(ctx).login_with_target_membership(is_active=True)


# ═══════════════════════════════════════════════════════════════════════════
# WHEN steps
# ═══════════════════════════════════════════════════════════════════════════


@when(parsers.parse("the caller sends {method} to the {route} of the target tenant"))
def when_caller_sends(ctx: dict, method: str, route: str) -> None:
    """Send one request to one of the four routes, for the target tenant's id."""
    ctx["admin_page"] = _env(ctx).send(method, route)


# ═══════════════════════════════════════════════════════════════════════════
# THEN steps
# ═══════════════════════════════════════════════════════════════════════════


@then("the page redirects to the login page of the target tenant")
def then_redirect_to_tenant_login(ctx: dict) -> None:
    """The tenant login page, not the generic /login require_auth sends to and not the handler's own redirect."""
    response = _require_admin_page(ctx)
    assert response.status_code == 302, f"Expected 302, got {response.status_code}"
    path = redirect_path(response)
    expected = f"/tenant/{_env(ctx).target.tenant_id}/login"
    assert path == expected, f"Expected redirect to {expected}, got {path}"


@then("the page redirects to the policy page of the target tenant")
def then_redirect_to_policy_index(ctx: dict) -> None:
    """policy.rules keeps its redirect to policy.index for an authorized member."""
    response = _require_admin_page(ctx)
    assert response.status_code == 302, f"Expected 302, got {response.status_code}"
    path = redirect_path(response)
    expected = f"/tenant/{_env(ctx).target.tenant_id}/policy/"
    assert path == expected, f"Expected redirect to {expected}, got {path}"


@then("the target tenant's stored data is unchanged")
def then_target_unchanged(ctx: dict) -> None:
    """A rejected request leaves the tenant's products, media buys and policy settings as seeded."""
    env = _env(ctx)
    assert env.target_state() == env.seeded_state, f"Target tenant state changed from {env.seeded_state!r}"


@then("the revenue chart lists the target tenant's active media buy")
def then_revenue_chart_lists_buy(ctx: dict) -> None:
    """Exactly today's body: one label per principal, the buy's budget as its value."""
    body = _require_admin_page(ctx).get_json()
    expected = {"labels": [_env(ctx).target.principal_name], "values": [float(ACTIVE_BUY_BUDGET)]}
    assert body == expected, f"Expected {expected!r}, got {body!r}"


@then("the product list is the target tenant's catalogue")
def then_product_list_is_catalogue(ctx: dict) -> None:
    """Exactly today's body: the seeded product, serialized as the handler does."""
    body = _require_admin_page(ctx).get_json()
    expected = {"products": _env(ctx).target.products_payload}
    assert body == expected, f"Expected {expected!r}, got {body!r}"


@then("the suggestions list the default catalogue")
def then_suggestions_are_defaults(ctx: dict) -> None:
    """Exactly today's body: every default product, in the handler's order, with its metadata."""
    body = _require_admin_page(ctx).get_json()
    expected = expected_suggestions_body()
    assert body == expected, f"Expected {expected!r}, got {body!r}"
