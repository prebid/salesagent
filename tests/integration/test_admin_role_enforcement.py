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

from src.core.database.models import User

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

TENANT_ID = "rbac_tenant"
EMAILS = {role: f"{role}@rbac.example" for role in ("viewer", "manager", "admin")}
NEW_USER = "invited@rbac.example"


@pytest.fixture
def rbac_tenant(integration_db):
    """A tenant with one active user per role."""
    from tests.factories import TenantFactory, UserFactory
    from tests.harness._base import IntegrationEnv

    with IntegrationEnv() as env:
        tenant = TenantFactory(tenant_id=TENANT_ID, name="RBAC", subdomain="rbac")
        for role, email in EMAILS.items():
            UserFactory(tenant=tenant, email=email, role=role)
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
