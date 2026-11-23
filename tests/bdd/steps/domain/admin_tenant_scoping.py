"""BDD step definitions for BR-ADMIN-TENANT-SCOPING: tenant-scoped admin routes (#2203, #2204).

Steps for the routes that take ``<tenant_id>`` from the URL. The harness
(``AdminTenantScopingEnv``) is provided by the ``_harness_env`` fixture in
tests/bdd/conftest.py. The generic admin steps (``the caller is not authenticated``,
``the page returns status``, ``the JSON response has "key" as "value"``) come from
tests/bdd/steps/domain/admin_accounts.py and read the same ``ctx["admin_page"]`` key.

"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pytest_bdd import given, parsers, then, when

from src.core.database.models import UserRole
from tests.bdd.steps.domain.admin_accounts import _require_admin_page
from tests.harness.admin_tenant_scoping import (
    ACTIVE_BUY_BUDGET,
    NON_MATCHING_ROW_ROLE,
    REDIRECTS,
    expected_suggestions_body,
    redirect_path,
)

if TYPE_CHECKING:
    from tests.harness.admin_tenant_scoping import AdminTenantScopingEnv


def _env(ctx: dict) -> AdminTenantScopingEnv:
    """Get the tenant-scoping harness from context."""
    return ctx["env"]


# ═══════════════════════════════════════════════════════════════════════════
# GIVEN steps
# ═══════════════════════════════════════════════════════════════════════════


@given("a target tenant with one catalogue product, one active media buy and an active member")
def given_target_tenant(ctx: dict) -> None:
    """Seed the tenant every scenario reads. Its member is somebody other than the caller."""
    _env(ctx).seed_target_tenant()


@given("the caller is an active member of a different tenant")
def given_caller_member_elsewhere(ctx: dict) -> None:
    """Logged in with an active User row in another tenant and no row in the target."""
    _env(ctx).login_as_member_of_other_tenant()


@given("the caller's membership in the target tenant is inactive")
def given_caller_inactive_member(ctx: dict) -> None:
    """Logged in with a User row in the target tenant that has is_active=False."""
    _env(ctx).login_with_target_membership(is_active=False, role=NON_MATCHING_ROW_ROLE)


@given("the caller is an active member of the target tenant")
def given_caller_active_member(ctx: dict) -> None:
    """Logged in with an active User row in the target tenant — never the super-admin bypass."""
    _env(ctx).login_with_target_membership(is_active=True)


@given(parsers.parse("the caller is an active member of the target tenant with the {role} role"))
def given_caller_active_member_with_role(ctx: dict, role: str) -> None:
    """The same active User row, holding the role the scenario names in place of the harness default."""
    _env(ctx).login_with_target_membership(is_active=True, role=UserRole(role))


@given(
    parsers.parse(
        'another tenant holds a product named "{name}" under the product id the target tenant\'s media buy names'
    )
)
def given_other_tenant_product(ctx: dict, name: str) -> None:
    _env(ctx).seed_product_of_other_tenant(name)


@given("the caller is a super admin of a different tenant with no membership in the target tenant")
def given_caller_super_admin_elsewhere(ctx: dict) -> None:
    """Logged in as a super admin at another tenant; the target holds no User row for the caller."""
    _env(ctx).login_as_super_admin_of_other_tenant()


# ═══════════════════════════════════════════════════════════════════════════
# WHEN steps
# ═══════════════════════════════════════════════════════════════════════════


@when(parsers.parse("the caller sends {method} to the {route} of the target tenant"))
def when_caller_sends(ctx: dict, method: str, route: str) -> None:
    """Send one request to one of the routes in ``ROUTES``, for the target tenant's id."""
    ctx["admin_page"] = _env(ctx).send(method, route)


@when(parsers.parse("the caller sends {method} to the {route} of the target tenant, asking for JSON"))
def when_caller_sends_asking_for_json(ctx: dict, method: str, route: str) -> None:
    """The same request from a caller that asks for a JSON answer, so a refusal states its reason."""
    ctx["admin_page"] = _env(ctx).send(method, route, asks_for_json=True)


# ═══════════════════════════════════════════════════════════════════════════
# THEN steps
# ═══════════════════════════════════════════════════════════════════════════


@then(parsers.parse("the page redirects to the {page} of the target tenant"))
def then_redirect_to_tenant_page(ctx: dict, page: str) -> None:
    """A 302 to the named page of the target tenant, by the ``REDIRECTS`` table beside the routes."""
    response = _require_admin_page(ctx)
    assert response.status_code == 302, f"Expected 302, got {response.status_code}"
    path = redirect_path(response)
    expected = REDIRECTS[page].format(tenant_id=_env(ctx).target.tenant_id)
    assert path == expected, f"Expected redirect to {expected}, got {path}"


@then("the target tenant's stored data is unchanged")
def then_target_unchanged(ctx: dict) -> None:
    """A rejected request leaves the tenant's stored data as the request found it.

    The env says what that data is (``target_state``): here the tenant's row counts and
    policy settings; for the publisher routes (``PublisherAuthorizationEnv``) every column
    of its publisher rows and of what was derived from them.
    """
    env = _env(ctx)
    before, after = env.state_before_request, env.target_state()
    assert after == before, f"The request changed the target tenant: {before!r} became {after!r}"


@then(parsers.parse('the media buys page lists the target tenant\'s media buy with no product named "{name}"'))
def then_media_buys_page_omits_product(ctx: dict, name: str) -> None:
    """The buy's row is on the page (the page shows its id's first 12 characters), and *name* is nowhere on it."""
    html = _require_admin_page(ctx).data.decode()
    assert f"{_env(ctx).target.media_buy_id[:12]}..." in html, "the page does not list the target tenant's media buy"
    assert name not in html, f"the page names {name!r}, a product the target tenant does not hold"


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
