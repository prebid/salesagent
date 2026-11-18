"""Tenant roles are enforced by require_tenant_access, through the real admin routes.

Roles were stored but never checked: any active tenant user, a viewer included, could
add an admin. Now a viewer is read-only, a manager can change everything except the
admin-only routes (users, sign-in, deactivating the tenant), and an admin can do
anything. Each case signs in as an ordinary tenant user, not a super admin, since a
super admin passes every check and would prove nothing.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from src.core.database.models import AuditLog, Tenant, User

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

TENANT_ID = "rbac_tenant"
ROLES = ("viewer", "manager", "admin")
EMAILS = {role: f"{role}@rbac.example" for role in ROLES}
USER_IDS = {role: f"rbac_{role}" for role in ROLES}
NEW_USER = "invited@rbac.example"


@pytest.fixture
def rbac_tenant(integration_db):
    """A tenant with one active user per role, one authorized domain and one authorized email."""
    from tests.factories import TenantFactory, UserFactory
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
        yield env


def _client_as(app, role):
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user"] = {"email": EMAILS[role]}
    return client


def _invited_user(env):
    session = env.get_session()
    session.expire_all()
    return session.scalars(select(User).filter_by(tenant_id=TENANT_ID, email=NEW_USER)).first()


def _add_user(client):
    return client.post(f"/tenant/{TENANT_ID}/users/add", data={"email": NEW_USER, "role": "admin"})


@pytest.mark.parametrize("role", ["viewer", "manager"])
def test_only_an_admin_may_add_a_user(rbac_tenant, test_admin_app, role):
    response = _add_user(_client_as(test_admin_app, role))
    assert response.status_code == 403
    assert _invited_user(rbac_tenant) is None


def test_an_admin_may_add_a_user(rbac_tenant, test_admin_app):
    response = _add_user(_client_as(test_admin_app, "admin"))
    assert response.status_code == 302
    invited = _invited_user(rbac_tenant)
    assert invited is not None
    assert invited.role == "admin"


def test_a_viewer_may_read(rbac_tenant, test_admin_app):
    assert _client_as(test_admin_app, "viewer").get(f"/tenant/{TENANT_ID}/users").status_code == 200


def test_a_viewer_may_not_change_tenant_settings_but_a_manager_may_reach_them(rbac_tenant, test_admin_app):
    """The role check runs before the view, so a manager gets past it whatever the view then says."""
    path = f"/tenant/{TENANT_ID}/update"
    assert _client_as(test_admin_app, "viewer").post(path, data={}).status_code == 403
    assert _client_as(test_admin_app, "manager").post(path, data={}).status_code != 403


# --- Authorized domains and emails --------------------------------------------------
# Signing in from an authorized domain (OIDC or Google) or an authorized email (Google)
# with no User row creates the user as an admin, so a manager who could add an address
# they control could make themselves an admin. Changing those lists is admin-only.

AUTHORIZED_LIST_ROUTES = [
    ("domains/add", "domain", "attacker.example", "authorized_domains", True),
    ("domains/remove", "domain", "rbac.example", "authorized_domains", False),
    ("emails/add", "email", "attacker@attacker.example", "authorized_emails", True),
    ("emails/remove", "email", "owner@rbac.example", "authorized_emails", False),
]


def _authorized_list(env, column):
    session = env.get_session()
    session.expire_all()
    return getattr(session.scalars(select(Tenant).filter_by(tenant_id=TENANT_ID)).one(), column)


@pytest.mark.parametrize(("route", "field", "value", "column", "adds"), AUTHORIZED_LIST_ROUTES)
def test_a_manager_may_not_change_who_can_sign_in(rbac_tenant, test_admin_app, route, field, value, column, adds):
    before = _authorized_list(rbac_tenant, column)
    response = _client_as(test_admin_app, "manager").post(f"/tenant/{TENANT_ID}/settings/{route}", data={field: value})
    assert response.status_code == 403
    assert _authorized_list(rbac_tenant, column) == before


@pytest.mark.parametrize(("route", "field", "value", "column", "adds"), AUTHORIZED_LIST_ROUTES)
def test_an_admin_may_change_who_can_sign_in(rbac_tenant, test_admin_app, route, field, value, column, adds):
    response = _client_as(test_admin_app, "admin").post(f"/tenant/{TENANT_ID}/settings/{route}", data={field: value})
    assert response.status_code == 302
    assert (value in _authorized_list(rbac_tenant, column)) is adds


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
    """The newest audit row for *operation* on the tenant (log_admin_action writes it)."""
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
    assert _last_audit(rbac_tenant, "update_role").success is False


def test_the_last_active_admin_may_not_be_deactivated(rbac_tenant, test_admin_app):
    response = _toggle(_client_as(test_admin_app, "admin"), "admin")
    assert LAST_ADMIN_ERROR in response.data
    assert _user(rbac_tenant, "admin").is_active is True
    assert _last_audit(rbac_tenant, "toggle_user").success is False


def test_an_admin_may_be_demoted_once_there_is_another(rbac_tenant, test_admin_app):
    client = _client_as(test_admin_app, "admin")
    _set_role(client, "manager", "admin")
    response = _set_role(client, "admin", "viewer")
    assert LAST_ADMIN_ERROR not in response.data
    assert _user(rbac_tenant, "admin").role == "viewer"


def test_an_admin_may_be_deactivated_once_there_is_another(rbac_tenant, test_admin_app):
    client = _client_as(test_admin_app, "admin")
    _set_role(client, "manager", "admin")
    response = _toggle(client, "admin")
    assert LAST_ADMIN_ERROR not in response.data
    assert _user(rbac_tenant, "admin").is_active is False


def test_other_users_may_be_demoted_and_deactivated_beside_the_last_admin(rbac_tenant, test_admin_app):
    client = _client_as(test_admin_app, "admin")
    _set_role(client, "manager", "viewer")
    _toggle(client, "viewer")
    assert _user(rbac_tenant, "manager").role == "viewer"
    assert _user(rbac_tenant, "viewer").is_active is False
