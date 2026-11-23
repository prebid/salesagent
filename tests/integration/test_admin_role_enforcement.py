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

from src.core.database.models import (
    AdapterConfig,
    Principal,
    PushNotificationConfig,
    SigningKey,
    Tenant,
    TenantAuthConfig,
    User,
    UserRole,
)
from tests.helpers.admin_session import tenant_user_client
from tests.utils.database_helpers import tenant_rows

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

TENANT_ID = "rbac_tenant"
ROLES = tuple(UserRole)
EMAILS = {role: f"{role}@rbac.example" for role in ROLES}
USER_IDS = {role: f"rbac_{role}" for role in ROLES}
NEW_USER = "invited@rbac.example"
#: The buyer whose token and webhooks the admin-only principal routes act on.
PRINCIPAL_ID = "rbac_buyer"
#: The signing key the revoke route acts on.
SIGNING_KID = "rbac-kid"


@pytest.fixture
def rbac_tenant(integration_db):
    """A tenant with one active user per role, one authorized domain and email, SSO enabled, a buyer and a signing key."""
    from tests.factories import PrincipalFactory, TenantAuthConfigFactory, TenantFactory, UserFactory
    from tests.factories.signing import SigningKeyFactory
    from tests.harness._base import IntegrationEnv

    with IntegrationEnv(tenant_id=TENANT_ID) as env:
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
        PrincipalFactory(tenant=tenant, principal_id=PRINCIPAL_ID)
        SigningKeyFactory(tenant=tenant, kid=SIGNING_KID)
        yield env


def _client_as(app, role):
    # signed in through SSO, which disable-setup-mode requires of its caller
    client = tenant_user_client(app, EMAILS[role], auth_method="oidc")
    with client.session_transaction() as sess:
        sess["tenant_id"] = TENANT_ID  # an SSO sign-in is bound to the tenant it was made for
    return client


def _fresh(env):
    """The env with its session expired: a route commits through its own session, so a row read earlier is stale."""
    env.get_session().expire_all()
    return env


def _tenant(env):
    return _fresh(env).get_one(Tenant, tenant_id=TENANT_ID)


def test_a_viewer_may_read(rbac_tenant, test_admin_app):
    assert _client_as(test_admin_app, "viewer").get(f"/tenant/{TENANT_ID}/users").status_code == 200


# --- Admin-only routes ----------------------------------------------------------------
# Every route marked require_tenant_access(role=UserRole.ADMIN).
# Signing in from an authorized domain (OIDC or Google) or an authorized email (Google)
# with no User row creates the user as an admin, so the sign-in lists and SSO are
# admin-only along with user management: a manager who could add an address they control
# could make themselves an admin. A viewer or manager gets 403 and nothing changes; an
# admin gets the route's own answer.
#
# oidc.test_initiate's admin answer (on to the identity provider) is graded in
# tests/bdd/features/BR-ADMIN-TENANT-SCOPING.feature (T-ADMIN-SCOPE-011 and -012); its row
# here grades the refusal, and only that an admin gets past the role check.


class AdminRoute(NamedTuple):
    """One admin-only route: how to call it, and what an admin gets back.

    ``admin_status`` None means only "past the role check" (anything but 403), and
    ``admin_changes_state`` None means the change is not asserted: for a route whose own
    answer needs a vendor, OAuth credentials or a key-encryption key the integration
    environment does not configure. The refusal half (403, nothing changed) holds for all.
    """

    method: str
    url: str
    request: dict[str, Any]
    admin_status: int | None
    admin_changes_state: bool | None = True


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
    "oidc.test_initiate": AdminRoute("POST", f"/auth/oidc/test/{TENANT_ID}", {}, None, None),
    # Where the tenant is served, its ad-server and AI credentials, the audit feed, buyer
    # webhooks and tokens, and its signing keys.
    "settings.update_general": AdminRoute("POST", f"{_T}/settings/general", {"data": {"name": "Renamed"}}, 302),
    "tenants.update": AdminRoute("POST", f"{_T}/update", {"data": {"name": "Renamed"}}, None, None),
    "settings.update_adapter": AdminRoute("POST", f"{_T}/settings/adapter", {"data": {"adapter": "mock"}}, None, None),
    "settings.update_slack": AdminRoute(
        "POST", f"{_T}/settings/slack", {"data": {"slack_audit_webhook_url": ""}}, None, None
    ),
    "settings.update_ai": AdminRoute("POST", f"{_T}/settings/ai", {"data": {"provider": "gemini"}}, None, None),
    "settings.register_approximated_domain": AdminRoute(
        "POST", f"{_T}/settings/approximated-register-domain", {"json": {"domain": "x.rbac.example"}}, None, None
    ),
    "settings.unregister_approximated_domain": AdminRoute(
        "POST", f"{_T}/settings/approximated-unregister-domain", {"json": {"domain": "x.rbac.example"}}, None, None
    ),
    "adapters.save_adapter_config": AdminRoute(
        "POST", f"/api/tenant/{TENANT_ID}/adapter-config", {"json": {"adapter_type": "mock", "config": {}}}, None, None
    ),
    "gam.configure_gam": AdminRoute("POST", f"{_T}/gam/configure", {"json": {"network_code": "123"}}, None, None),
    "gam.create_service_account": AdminRoute("POST", f"{_T}/gam/create-service-account", {}, None, None),
    "auth.gam_authorize": AdminRoute("GET", f"/auth/gam/authorize/{TENANT_ID}", {}, None, None),
    "policy.update": AdminRoute("POST", f"{_T}/policy/update", {"data": {}}, None, None),
    # The new token is in the answer, and the stored hash changes.
    "principals.rotate_token": AdminRoute("POST", f"{_T}/principal/{PRINCIPAL_ID}/rotate-token", {}, 200),
    # Back to the buyer's webhooks page, with a new push-notification row.
    "principals.register_webhook": AdminRoute(
        "POST",
        f"{_T}/principals/{PRINCIPAL_ID}/webhooks/register",
        {"data": {"url": "https://hooks.rbac.example/"}},
        302,
    ),
    "signing_keys.create_signing_key": AdminRoute(
        "POST", f"{_T}/signing-keys/create", {"data": {"alg": "ed25519"}}, None, None
    ),
    # Back to the keys page, with the key's revoked_at stamped.
    "signing_keys.revoke_signing_key_route": AdminRoute("POST", f"{_T}/signing-keys/{SIGNING_KID}/revoke", {}, 302),
}


#: Every table an admin-only route can write, snapshotted whole for the tenant.
_STATE_MODELS = (Tenant, User, TenantAuthConfig, AdapterConfig, Principal, PushNotificationConfig, SigningKey)


def _state(env):
    """Everything an admin-only route can change: each of those tables' rows for the tenant, every column."""
    return tenant_rows(_fresh(env).get_session(), TENANT_ID, _STATE_MODELS)


def _call(app, role, route):
    return _client_as(app, role).open(route.url, method=route.method, **route.request)


@pytest.mark.parametrize("role", [r for r in UserRole if r is not UserRole.ADMIN])
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
    if route.admin_status is None:
        assert response.status_code != 403, response.get_data(as_text=True)[:500]
    else:
        assert response.status_code == route.admin_status, response.get_data(as_text=True)[:500]
    if route.admin_changes_state is not None:
        assert (_state(rbac_tenant) != before) is route.admin_changes_state


def test_the_table_lists_exactly_the_admin_only_routes(test_admin_app):
    """One list: every endpoint tagged require_tenant_access(role=UserRole.ADMIN) has a row, and only those."""
    tagged = {
        endpoint
        for endpoint, view in test_admin_app.view_functions.items()
        if getattr(view, "tenant_access_role", None) == UserRole.ADMIN
    }
    assert set(ADMIN_ONLY_ROUTES) == tagged


def test_each_admin_only_url_reaches_its_endpoint(test_admin_app):
    """The list's keys name the view each URL reaches, so a moved route cannot leave a stale entry."""
    adapter = test_admin_app.url_map.bind("localhost")
    for endpoint, route in ADMIN_ONLY_ROUTES.items():
        assert adapter.match(route.url, method=route.method)[0] == endpoint


def test_a_refused_fetch_gets_a_json_error(rbac_tenant, test_admin_app):
    """templates/users.html reads the answer with response.json() and shows its error."""
    response = _call(test_admin_app, "manager", ADMIN_ONLY_ROUTES["users.add_domain"])
    assert response.status_code == 403
    assert response.get_json() == {"success": False, "error": "Your role does not allow this"}


# --- The last active admin ------------------------------------------------------------
# Demoting or deactivating the tenant's only active admin would leave no one but a super
# admin able to manage users or sign-in, so the Users page refuses it and writes nothing.


def _user(env, role):
    return _fresh(env).get_one(User, user_id=USER_IDS[role])


def _toggle(client, role):
    return client.post(f"/tenant/{TENANT_ID}/users/{USER_IDS[role]}/toggle", follow_redirects=True)


def _set_role(client, role, new_role):
    return client.post(
        f"/tenant/{TENANT_ID}/users/{USER_IDS[role]}/update_role", data={"role": new_role}, follow_redirects=True
    )


LAST_ADMIN_ERROR = b"is the last active admin of this tenant"


@pytest.mark.parametrize("new_role", ["manager", "viewer"])
def test_the_last_active_admin_may_not_be_demoted(rbac_tenant, test_admin_app, new_role):
    response = _set_role(_client_as(test_admin_app, "admin"), "admin", new_role)
    assert LAST_ADMIN_ERROR in response.data
    assert _user(rbac_tenant, "admin").role == "admin"
    # the refusal is audited as a failure, not as a successful change
    assert rbac_tenant.get_audit_logs("AdminUI.update_role")[-1].success is False


def test_the_last_active_admin_may_not_be_deactivated(rbac_tenant, test_admin_app):
    response = _toggle(_client_as(test_admin_app, "admin"), "admin")
    assert LAST_ADMIN_ERROR in response.data
    assert _user(rbac_tenant, "admin").is_active is True
    assert rbac_tenant.get_audit_logs("AdminUI.toggle_user")[-1].success is False


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
    # no redirect-following: once deactivated, the admin may not load the Users page
    response = client.post(f"/tenant/{TENANT_ID}/users/{USER_IDS['admin']}/toggle")
    assert response.status_code == 302
    assert _user(rbac_tenant, "admin").is_active is False


def test_other_users_may_be_demoted_and_deactivated_beside_the_last_admin(rbac_tenant, test_admin_app):
    client = _client_as(test_admin_app, "admin")
    _set_role(client, "manager", "viewer")
    _toggle(client, "viewer")
    assert _user(rbac_tenant, "manager").role == "viewer"
    assert _user(rbac_tenant, "viewer").is_active is False
