"""Tenant roles are enforced by require_tenant_access, through the real admin routes.

Roles were stored but never checked: any active tenant user, a viewer included, could
add an admin. Now a viewer is read-only, a manager can change everything except the
admin-only routes (users, sign-in, SSO, deactivating the tenant), and an admin can do
anything. Each case signs in as an ordinary tenant user, not a super admin, since a
super admin passes every check and would prove nothing.
"""

from __future__ import annotations

from typing import Any, NamedTuple

import pytest
from sqlalchemy import select

from src.core.database.models import AuditLog, Tenant, TenantAuthConfig, User

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

TENANT_ID = "rbac_tenant"
ROLES = ("viewer", "manager", "admin")
EMAILS = {role: f"{role}@rbac.example" for role in ROLES}
USER_IDS = {role: f"rbac_{role}" for role in ROLES}
NEW_USER = "invited@rbac.example"


@pytest.fixture
def rbac_tenant(integration_db):
    """A tenant with one active user per role, one authorized domain and email, and SSO enabled."""
    from tests.factories import TenantAuthConfigFactory, TenantFactory, UserFactory
    from tests.harness._base import IntegrationEnv

    with IntegrationEnv() as env:
        tenant = TenantFactory(
            tenant_id=TENANT_ID,
            name="RBAC",
            subdomain="rbac",
            authorized_domains=["rbac.example"],
            authorized_emails=["owner@rbac.example"],
        )
        for role in ROLES:
            UserFactory(tenant=tenant, user_id=USER_IDS[role], email=EMAILS[role], role=role)
        TenantAuthConfigFactory(tenant=tenant, oidc_enabled=True)
        yield env


def _client_as(app, role):
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user"] = {"email": EMAILS[role]}
        # signed in through SSO, which disable-setup-mode requires of its caller
        sess["auth_method"] = "oidc"
    return client


def _tenant(env):
    session = env.get_session()
    session.expire_all()
    return session.scalars(select(Tenant).filter_by(tenant_id=TENANT_ID)).one()


def test_a_viewer_may_read(rbac_tenant, test_admin_app):
    assert _client_as(test_admin_app, "viewer").get(f"/tenant/{TENANT_ID}/users").status_code == 200


def test_a_viewer_may_not_change_tenant_settings_but_a_manager_may(rbac_tenant, test_admin_app):
    path = f"/tenant/{TENANT_ID}/update"
    form = {"name": "Renamed", "subdomain": "rbac"}
    assert _client_as(test_admin_app, "viewer").post(path, data=form).status_code == 403
    assert _tenant(rbac_tenant).name == "RBAC"
    assert _client_as(test_admin_app, "manager").post(path, data=form).status_code == 302
    assert _tenant(rbac_tenant).name == "Renamed"


# --- Admin-only routes ----------------------------------------------------------------
# Every route marked require_tenant_access(role=UserRole.ADMIN). Signing in from an
# authorized domain (OIDC or Google) or an authorized email (Google) with no User row
# creates the user as an admin, so the sign-in lists and SSO are admin-only along with
# user management: a manager who could add an address they control could make
# themselves an admin. A viewer or manager gets 403 and nothing changes; an admin gets
# the route's own answer.


class AdminRoute(NamedTuple):
    method: str
    url: str
    request: dict[str, Any]
    admin_status: int
    admin_changes_state: bool = True


_T = f"/tenant/{TENANT_ID}"
_SSO = f"/auth/oidc/tenant/{TENANT_ID}"

ADMIN_ONLY_ROUTES = {
    "users.add_user": AdminRoute("POST", f"{_T}/users/add", {"data": {"email": NEW_USER, "role": "admin"}}, 302),
    "users.toggle_user": AdminRoute("POST", f"{_T}/users/{USER_IDS['viewer']}/toggle", {}, 302),
    "users.update_role": AdminRoute(
        "POST", f"{_T}/users/{USER_IDS['viewer']}/update_role", {"data": {"role": "admin"}}, 302
    ),
    "users.add_domain": AdminRoute("POST", f"{_T}/users/domains", {"json": {"domain": "new.example"}}, 200),
    "users.remove_domain": AdminRoute("DELETE", f"{_T}/users/domains", {"json": {"domain": "rbac.example"}}, 200),
    "users.disable_setup_mode": AdminRoute("POST", f"{_T}/users/disable-setup-mode", {}, 200),
    # setup mode starts on, so turning it on again changes nothing
    "users.enable_setup_mode": AdminRoute("POST", f"{_T}/users/enable-setup-mode", {}, 200, admin_changes_state=False),
    "settings.add_authorized_domain": AdminRoute(
        "POST", f"{_T}/settings/domains/add", {"data": {"domain": "attacker.example"}}, 302
    ),
    "settings.remove_authorized_domain": AdminRoute(
        "POST", f"{_T}/settings/domains/remove", {"data": {"domain": "rbac.example"}}, 302
    ),
    "settings.add_authorized_email": AdminRoute(
        "POST", f"{_T}/settings/emails/add", {"data": {"email": "attacker@attacker.example"}}, 302
    ),
    "settings.remove_authorized_email": AdminRoute(
        "POST", f"{_T}/settings/emails/remove", {"data": {"email": "owner@rbac.example"}}, 302
    ),
    "oidc.save_config": AdminRoute(
        "POST", f"{_SSO}/config", {"json": {"provider": "google", "client_id": "new-client", "client_secret": "s"}}, 200
    ),
    # the SSO config was never tested, so the view itself refuses to enable it
    "oidc.enable": AdminRoute("POST", f"{_SSO}/enable", {}, 400, admin_changes_state=False),
    "oidc.disable": AdminRoute("POST", f"{_SSO}/disable", {}, 200),
    "tenants.deactivate_tenant": AdminRoute("POST", f"{_T}/deactivate", {"data": {"confirm_name": "RBAC"}}, 302),
}


def _state(env):
    """Everything an admin-only route can change: the tenant row, its users and its SSO config."""
    session = env.get_session()
    session.expire_all()
    tenant = session.scalars(select(Tenant).filter_by(tenant_id=TENANT_ID)).one()
    users = session.scalars(select(User).filter_by(tenant_id=TENANT_ID).order_by(User.user_id)).all()
    sso = session.scalars(select(TenantAuthConfig).filter_by(tenant_id=TENANT_ID)).one()
    return (
        (tenant.name, tenant.is_active, tenant.auth_setup_mode),
        (tuple(tenant.authorized_domains or ()), tuple(tenant.authorized_emails or ())),
        tuple((u.user_id, u.email, u.role, u.is_active) for u in users),
        (sso.oidc_enabled, sso.oidc_client_id),
    )


def _call(app, role, route):
    return _client_as(app, role).open(route.url, method=route.method, **route.request)


@pytest.mark.parametrize("role", ["viewer", "manager"])
@pytest.mark.parametrize("endpoint", ADMIN_ONLY_ROUTES)
def test_only_an_admin_may_use_an_admin_only_route(rbac_tenant, test_admin_app, endpoint, role):
    before = _state(rbac_tenant)
    assert _call(test_admin_app, role, ADMIN_ONLY_ROUTES[endpoint]).status_code == 403
    assert _state(rbac_tenant) == before


@pytest.mark.parametrize("endpoint", ADMIN_ONLY_ROUTES)
def test_an_admin_may_use_an_admin_only_route(rbac_tenant, test_admin_app, endpoint):
    route = ADMIN_ONLY_ROUTES[endpoint]
    before = _state(rbac_tenant)
    response = _call(test_admin_app, "admin", route)
    assert response.status_code == route.admin_status, response.get_data(as_text=True)[:500]
    assert (_state(rbac_tenant) != before) is route.admin_changes_state


def test_each_admin_only_url_reaches_its_endpoint(test_admin_app):
    """The list's keys name the view each URL reaches, so a moved route cannot leave a stale entry."""
    adapter = test_admin_app.url_map.bind("localhost")
    for endpoint, route in ADMIN_ONLY_ROUTES.items():
        assert adapter.match(route.url, method=route.method)[0] == endpoint


def test_a_refused_fetch_gets_a_json_error(rbac_tenant, test_admin_app):
    """templates/users.html reads the answer with response.json() and shows its error."""
    response = _call(test_admin_app, "manager", ADMIN_ONLY_ROUTES["users.add_domain"])
    assert response.status_code == 403
    assert response.get_json() == {"error": "Your role does not allow this"}


# --- The last active admin ------------------------------------------------------------
# Demoting or deactivating the tenant's only active admin would leave no one but a super
# admin able to manage users or sign-in, so the Users page refuses it and writes nothing.


def _user(env, role):
    session = env.get_session()
    session.expire_all()
    return session.scalars(select(User).filter_by(tenant_id=TENANT_ID, user_id=USER_IDS[role])).one()


def _toggle(client, role):
    return client.post(f"/tenant/{TENANT_ID}/users/{USER_IDS[role]}/toggle", follow_redirects=True)


def _set_role(client, role, new_role):
    return client.post(
        f"/tenant/{TENANT_ID}/users/{USER_IDS[role]}/update_role", data={"role": new_role}, follow_redirects=True
    )


LAST_ADMIN_ERROR = b"is the last active admin of this tenant"


def _last_audit(env, operation):
    """The newest audit row for *operation* (``AdminUI.<view>``, as log_admin_action names it) on the tenant."""
    session = env.get_session()
    session.expire_all()
    return session.scalars(
        select(AuditLog).filter_by(tenant_id=TENANT_ID, operation=operation).order_by(AuditLog.log_id.desc())
    ).first()


@pytest.mark.parametrize("new_role", ["manager", "viewer"])
def test_the_last_active_admin_may_not_be_demoted(rbac_tenant, test_admin_app, new_role):
    response = _set_role(_client_as(test_admin_app, "admin"), "admin", new_role)
    assert LAST_ADMIN_ERROR in response.data
    assert _user(rbac_tenant, "admin").role == "admin"
    # the refusal is audited as a failure, not as a successful change
    assert _last_audit(rbac_tenant, "AdminUI.update_role").success is False


def test_the_last_active_admin_may_not_be_deactivated(rbac_tenant, test_admin_app):
    response = _toggle(_client_as(test_admin_app, "admin"), "admin")
    assert LAST_ADMIN_ERROR in response.data
    assert _user(rbac_tenant, "admin").is_active is True
    assert _last_audit(rbac_tenant, "AdminUI.toggle_user").success is False


def test_an_admin_may_be_demoted_once_there_is_another(rbac_tenant, test_admin_app):
    client = _client_as(test_admin_app, "admin")
    _set_role(client, "manager", "admin")
    response = _set_role(client, "admin", "viewer")
    assert LAST_ADMIN_ERROR not in response.data
    assert b"role updated to viewer" in response.data
    assert _user(rbac_tenant, "admin").role == "viewer"


def test_an_admin_may_be_deactivated_once_there_is_another(rbac_tenant, test_admin_app):
    client = _client_as(test_admin_app, "admin")
    _set_role(client, "manager", "admin")
    response = _toggle(client, "admin")
    assert LAST_ADMIN_ERROR not in response.data
    assert b"deactivated" in response.data
    assert _user(rbac_tenant, "admin").is_active is False


def test_other_users_may_be_demoted_and_deactivated_beside_the_last_admin(rbac_tenant, test_admin_app):
    client = _client_as(test_admin_app, "admin")
    _set_role(client, "manager", "viewer")
    _toggle(client, "viewer")
    assert _user(rbac_tenant, "manager").role == "viewer"
    assert _user(rbac_tenant, "viewer").is_active is False
