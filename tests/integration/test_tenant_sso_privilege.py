"""A tenant's own SSO grants access to that tenant only, through the real admin routes.

A tenant admin configures the tenant's OIDC provider, so whatever email that provider
asserts is only as trustworthy as the tenant admin. Two things keep it from becoming
deployment-wide privilege, and each is graded here on a real database:

* a session signed in through a tenant's SSO is bound to that tenant and is never a
  super admin, whatever email it carries — so a super admin's address may be a tenant's
  user or authorized domain (the documented first-time SSO setup adds it) without
  granting anything outside that tenant;
* the SSO test flow, which ends by enabling the tenant's SSO, is only for that tenant's
  admin, and the admin who started it must be the one who finishes it.

The identity provider is the one thing stubbed (``_StubIdentityProvider``, at authlib's
``OAuth`` seam): it is a system outside this deployment that the test cannot call.
"""

from __future__ import annotations

from typing import Any

import pytest
from flask import redirect

import src.core.config as config_module
from src.core.database.models import TenantAuthConfig, User, UserRole
from tests.helpers.admin_session import cached_super_admin, tenant_user_session

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

TENANT = "sso_acme"
OTHER_TENANT = "sso_globex"
TENANT_ADMIN = "owner@acme.example"
TENANT_ADMIN_ID = "acme_owner"
SECOND_ADMIN = "deputy@acme.example"
SUPER_ADMIN_EMAIL = "root@ops.example"
SUPER_ADMIN_DOMAIN = "operator.example"  # SUPER_ADMIN_DOMAINS
DB_SUPER_ADMIN_DOMAIN = "db-ops.example"  # the super_admin_domains row


class _StubIdentityProvider:
    """Stands in for authlib's ``OAuth`` and the tenant's identity provider behind it.

    ``asserted`` is the claim set the provider returns once its login completes.
    """

    asserted: dict[str, Any] = {}

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    def init_app(self, app: Any) -> None:
        pass

    def register(self, name: str, **kwargs: Any) -> None:
        pass

    def __getattr__(self, client_name: str) -> _StubIdentityProvider:
        return self  # oauth.test_oidc / oauth.login_oidc

    def authorize_redirect(self, redirect_uri: str) -> Any:
        return redirect("https://idp.example/authorize")

    def authorize_access_token(self) -> dict[str, Any]:
        return {"userinfo": dict(self.asserted)}


@pytest.fixture
def sso_tenants(integration_db, test_admin_app, monkeypatch):
    """Two tenants; the first has an admin and a saved (not yet enabled) SSO configuration."""
    from tests.factories import (
        TenantAuthConfigFactory,
        TenantFactory,
        TenantManagementConfigFactory,
        UserFactory,
    )
    from tests.harness._base import IntegrationEnv

    monkeypatch.setenv("SUPER_ADMIN_EMAILS", SUPER_ADMIN_EMAIL)
    monkeypatch.setenv("SUPER_ADMIN_DOMAINS", SUPER_ADMIN_DOMAIN)
    monkeypatch.setattr(config_module, "_settings", None)
    monkeypatch.setattr("src.admin.blueprints.oidc.OAuth", _StubIdentityProvider)
    monkeypatch.setattr(_StubIdentityProvider, "asserted", {})

    with IntegrationEnv(tenant_id=TENANT) as env:
        tenant = TenantFactory(tenant_id=TENANT, name="Acme")
        TenantFactory(tenant_id=OTHER_TENANT, name="Globex")
        UserFactory(tenant=tenant, user_id=TENANT_ADMIN_ID, email=TENANT_ADMIN, role=UserRole.ADMIN)
        UserFactory(tenant=tenant, email=SECOND_ADMIN, role=UserRole.ADMIN)
        TenantAuthConfigFactory(tenant=tenant, oidc_enabled=False)
        TenantManagementConfigFactory(config_key="super_admin_domains", config_value=DB_SUPER_ADMIN_DOMAIN)
        yield env


def _admin_client(app, email: str = TENANT_ADMIN):
    client = app.test_client()
    tenant_user_session(client, email)
    return client


def _switch_user(client, email: str) -> None:
    """Change who the browser is signed in as, keeping the rest of its session (an SSO test in flight)."""
    with client.session_transaction() as sess:
        sess["user"] = email


def _fresh(env):
    env.get_session().expire_all()
    return env


def _tenant(env, tenant_id: str):
    from src.core.database.models import Tenant

    return _fresh(env).get_one(Tenant, tenant_id=tenant_id)


def _users(env) -> set[str]:
    return {u.email for u in _fresh(env).query(User, tenant_id=TENANT)}


def _tenant_lists(env) -> tuple[tuple[str, ...], tuple[str, ...]]:
    tenant = _tenant(env, TENANT)
    return tuple(tenant.authorized_domains or ()), tuple(tenant.authorized_emails or ())


def _sso(env) -> TenantAuthConfig:
    return _fresh(env).get_one(TenantAuthConfig, tenant_id=TENANT)


# --- A tenant SSO session stays in its tenant ------------------------------------------


def _sign_in_through_tenant_sso(app, email: str):
    """Complete the tenant's real OIDC login with its provider asserting *email*."""
    _StubIdentityProvider.asserted = {"email": email, "email_verified": True}
    client = app.test_client()
    assert client.get(f"/auth/oidc/login/{TENANT}").status_code == 302
    assert client.get("/auth/oidc/callback").status_code == 302
    with client.session_transaction() as sess:
        assert (sess["user"], sess["auth_method"], sess["tenant_id"]) == (email, "oidc", TENANT)
    return client


@pytest.mark.parametrize("email", [SUPER_ADMIN_EMAIL, f"anyone@{SUPER_ADMIN_DOMAIN}", f"y@{DB_SUPER_ADMIN_DOMAIN}"])
def test_a_super_admin_email_from_a_tenant_sso_reaches_no_other_tenant(sso_tenants, test_admin_app, email):
    """The tenant admin adds the address, as the SSO setup guide says to, then signs in as it."""
    response = _admin_client(test_admin_app).post(f"/tenant/{TENANT}/users/add", data={"email": email, "role": "admin"})
    assert response.status_code == 302
    assert email in _users(sso_tenants)
    client = _sign_in_through_tenant_sso(test_admin_app, email)

    assert client.get(f"/tenant/{OTHER_TENANT}/users").status_code == 403
    assert client.post("/create_tenant", data={"tenant_name": "Pwned"}).status_code == 403
    # Its own tenant, as the admin the tenant made it.
    assert client.get(f"/tenant/{TENANT}/users").status_code == 200


#: The keys ``google_callback`` writes for a super admin, with the cache ``is_super_admin`` keeps.
_DEPLOYMENT_SUPER_ADMIN_SESSION = {
    "user": SUPER_ADMIN_EMAIL,
    "role": "super_admin",
    **cached_super_admin(SUPER_ADMIN_EMAIL),
}


def test_a_tenant_sso_sign_in_drops_an_earlier_super_admin_session(sso_tenants, test_admin_app, monkeypatch):
    """No key of the earlier super admin's identity survives a tenant SSO sign-in in the same browser."""
    from src.core.config import get_settings

    monkeypatch.setattr(get_settings().runtime, "adcp_multi_tenant", True)  # core.index's super-admin branch
    _StubIdentityProvider.asserted = {"email": TENANT_ADMIN, "email_verified": True}
    client = test_admin_app.test_client()
    with client.session_transaction() as sess:
        sess.update(_DEPLOYMENT_SUPER_ADMIN_SESSION)
    assert client.get(f"/auth/oidc/login/{TENANT}").status_code == 302
    assert client.get("/auth/oidc/callback").status_code == 302

    with client.session_transaction() as sess:
        assert {"role", *cached_super_admin(SUPER_ADMIN_EMAIL)}.isdisjoint(sess)
        assert (sess["user"], sess["auth_method"]) == (TENANT_ADMIN, "oidc")
    assert client.get("/").status_code == 403


def test_a_deployment_sign_in_drops_an_earlier_tenant_sso_binding(sso_tenants, test_admin_app, monkeypatch):
    """A super admin who used a tenant's SSO in this browser is a super admin again after the deployment sign-in."""
    _StubIdentityProvider.asserted = {"email": SUPER_ADMIN_EMAIL, "email_verified": True}
    monkeypatch.setattr(test_admin_app, "oauth", _StubIdentityProvider(), raising=False)  # oauth.google
    client = test_admin_app.test_client()
    with client.session_transaction() as sess:
        sess.update({"user": TENANT_ADMIN, "auth_method": "oidc", "tenant_id": TENANT, "authenticated": True})
    assert client.get("/auth/google/callback").status_code == 302

    with client.session_transaction() as sess:
        assert "auth_method" not in sess
        assert (sess["user"], sess["role"]) == (SUPER_ADMIN_EMAIL, "super_admin")
    assert client.get(f"/tenant/{OTHER_TENANT}/users").status_code == 200


def test_a_tenant_sso_user_of_another_tenant_is_refused_there(sso_tenants, test_admin_app):
    """An email that is a real user elsewhere reaches only the tenant whose SSO vouched for it."""
    from tests.factories import UserFactory

    UserFactory(tenant=_tenant(sso_tenants, OTHER_TENANT), email=TENANT_ADMIN, role=UserRole.ADMIN)
    client = _sign_in_through_tenant_sso(test_admin_app, TENANT_ADMIN)

    assert client.get(f"/tenant/{OTHER_TENANT}/users").status_code == 403
    assert client.get(f"/tenant/{TENANT}/users").status_code == 200


# --- The SSO test flow belongs to the tenant's admin ------------------------------------


def test_an_anonymous_caller_cannot_start_an_sso_test_or_create_a_config(sso_tenants, test_admin_app):
    client = test_admin_app.test_client()
    assert client.post(f"/auth/oidc/test/{TENANT}").status_code in (302, 401)
    assert client.post(f"/auth/oidc/test/{OTHER_TENANT}").status_code in (302, 401)
    with client.session_transaction() as sess:
        assert "oidc_test_flow" not in sess
    assert _fresh(sso_tenants).query(TenantAuthConfig, tenant_id=OTHER_TENANT) == []


def test_a_tenant_viewer_cannot_start_an_sso_test(sso_tenants, test_admin_app):
    from tests.factories import UserFactory

    UserFactory(tenant=_tenant(sso_tenants, TENANT), email="viewer@acme.example", role=UserRole.VIEWER)
    assert _admin_client(test_admin_app, "viewer@acme.example").post(f"/auth/oidc/test/{TENANT}").status_code == 403


def test_an_sso_test_callback_with_no_admin_behind_it_changes_nothing(sso_tenants, test_admin_app):
    """A session that claims a test flow it never started through test_initiate."""
    _StubIdentityProvider.asserted = {"email": "stranger@unrelated.example"}
    client = test_admin_app.test_client()
    with client.session_transaction() as sess:
        sess["oidc_test_flow"] = True
        sess["oidc_test_tenant_id"] = TENANT
    client.get("/auth/oidc/callback")

    sso = _sso(sso_tenants)
    assert (sso.oidc_enabled, sso.oidc_verified_at) == (False, None)


def test_an_sso_test_finished_by_a_different_session_changes_nothing(sso_tenants, test_admin_app):
    _StubIdentityProvider.asserted = {"email": TENANT_ADMIN}
    client = _admin_client(test_admin_app)
    assert client.post(f"/auth/oidc/test/{TENANT}").status_code == 302
    # The browser's identity changes between the start and the callback.
    _switch_user(client, "stranger@unrelated.example")
    client.get("/auth/oidc/callback")

    sso = _sso(sso_tenants)
    assert (sso.oidc_enabled, sso.oidc_verified_at) == (False, None)


def test_an_sso_test_finished_by_another_admin_of_the_tenant_changes_nothing(sso_tenants, test_admin_app):
    """Only the initiator check refuses here: the second admin would pass the role re-check."""
    _StubIdentityProvider.asserted = {"email": SECOND_ADMIN}
    client = _admin_client(test_admin_app)
    assert client.post(f"/auth/oidc/test/{TENANT}").status_code == 302
    _switch_user(client, SECOND_ADMIN)
    client.get("/auth/oidc/callback")

    sso = _sso(sso_tenants)
    assert (sso.oidc_enabled, sso.oidc_verified_at) == (False, None)


def test_an_sso_test_finished_after_its_admin_was_demoted_changes_nothing(sso_tenants, test_admin_app):
    """Only the role re-check refuses here: the session is still the one that started the test."""
    _StubIdentityProvider.asserted = {"email": TENANT_ADMIN}
    client = _admin_client(test_admin_app)
    assert client.post(f"/auth/oidc/test/{TENANT}").status_code == 302
    demoted = _admin_client(test_admin_app, SECOND_ADMIN).post(
        f"/tenant/{TENANT}/users/{TENANT_ADMIN_ID}/update_role", data={"role": "viewer"}
    )
    assert demoted.status_code == 302
    assert _fresh(sso_tenants).get_one(User, user_id=TENANT_ADMIN_ID).role == UserRole.VIEWER
    client.get("/auth/oidc/callback")

    sso = _sso(sso_tenants)
    assert (sso.oidc_enabled, sso.oidc_verified_at) == (False, None)


def test_the_tenant_admin_who_starts_an_sso_test_enables_sso(sso_tenants, test_admin_app):
    _StubIdentityProvider.asserted = {"email": TENANT_ADMIN}
    client = _admin_client(test_admin_app)
    assert client.post(f"/auth/oidc/test/{TENANT}").status_code == 302
    client.get("/auth/oidc/callback")

    sso = _sso(sso_tenants)
    assert sso.oidc_enabled is True
    assert sso.oidc_verified_at is not None
    assert sso.oidc_verified_redirect_uri.endswith("/admin/auth/oidc/callback")


@pytest.mark.parametrize("domain", [SUPER_ADMIN_DOMAIN, DB_SUPER_ADMIN_DOMAIN])
def test_a_tenant_may_authorize_a_super_admin_domain(sso_tenants, test_admin_app, domain):
    """The single-tenant SSO setup authorizes the publisher's own domain, often the super admins' too."""
    response = _admin_client(test_admin_app).post(f"/tenant/{TENANT}/users/domains", json={"domain": domain})
    assert response.status_code == 200
    assert domain in _tenant_lists(sso_tenants)[0]


def test_an_sso_test_cannot_be_started_by_a_link(sso_tenants, test_admin_app):
    """A GET (a link from another site) does not start a test that ends by enabling SSO."""
    assert _admin_client(test_admin_app).get(f"/auth/oidc/test/{TENANT}").status_code == 405


def test_an_abandoned_sso_test_does_not_turn_a_later_login_into_a_test(sso_tenants, test_admin_app):
    _StubIdentityProvider.asserted = {"email": TENANT_ADMIN}
    client = _admin_client(test_admin_app)
    assert client.post(f"/auth/oidc/test/{TENANT}").status_code == 302
    # The admin never comes back from the provider, and later signs in through SSO.
    assert client.get(f"/auth/oidc/login/{TENANT}").status_code == 302
    client.get("/auth/oidc/callback")

    with client.session_transaction() as sess:
        assert (sess["auth_method"], sess["tenant_id"]) == ("oidc", TENANT)
    sso = _sso(sso_tenants)
    assert (sso.oidc_enabled, sso.oidc_verified_at) == (False, None)


# --- Signup takes its identity from the deployment's own sign-in -------------------------


@pytest.fixture
def self_serve_signup(sso_tenants, monkeypatch):
    """A deployment that hosts self-serve tenants (see test_self_service_signup.py)."""
    from src.core.config import get_settings

    runtime = get_settings().runtime
    monkeypatch.setattr(runtime, "adcp_multi_tenant", True)
    monkeypatch.setattr(runtime, "sales_agent_domain", "sales-agent.adcp.test")
    return sso_tenants


def _provision(client, publisher_name: str):
    with client.session_transaction() as sess:
        sess["signup_flow"] = True
    return client.post("/signup/provision", data={"publisher_name": publisher_name, "adapter": "mock"})


def _tenant_named(env, name: str):
    from src.core.database.models import Tenant

    return [t for t in _fresh(env).query(Tenant) if t.name == name]


def test_a_tenant_sso_session_cannot_sign_up_a_tenant(self_serve_signup, test_admin_app):
    """Its provider can assert any address, so signup would authorize a domain nobody verified."""
    from tests.factories import UserFactory

    UserFactory(tenant=_tenant(self_serve_signup, TENANT), email="ceo@victim-corp.example", role=UserRole.ADMIN)
    client = _sign_in_through_tenant_sso(test_admin_app, "ceo@victim-corp.example")

    with client.session_transaction() as sess:
        sess["signup_flow"] = True
    onboarding = client.get("/signup/onboarding")
    assert onboarding.status_code == 302
    assert onboarding.headers["Location"].endswith("/signup")
    response = _provision(client, "Victim Corp")
    assert response.status_code == 302
    assert "/signup" in response.headers["Location"]
    assert _tenant_named(self_serve_signup, "Victim Corp") == []


def test_a_publisher_signed_in_through_the_deployment_can_sign_up(self_serve_signup, test_admin_app):
    client = test_admin_app.test_client()
    with client.session_transaction() as sess:
        sess["user"] = "owner@new-publisher.example"
    assert _provision(client, "New Publisher").status_code == 200
    (tenant,) = _tenant_named(self_serve_signup, "New Publisher")
    assert tenant.authorized_domains == ["new-publisher.example"]
