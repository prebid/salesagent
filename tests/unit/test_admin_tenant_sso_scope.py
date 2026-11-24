"""A session from a tenant's own SSO is bound to that tenant and is never a super admin.

A tenant admin chooses the tenant's OIDC provider, so the email that provider asserts
is only as good as the tenant admin's word. Deployment-wide privilege, and any other
tenant, are out of its reach no matter which email it carries: ``is_super_admin`` says
no for such a session, and ``require_tenant_access`` / ``require_auth`` refuse any
tenant but the one the session was issued for.

The super-admin answer for a deployment sign-in comes from the session cache
(``cached_super_admin``), so none of these tests reads a database.
"""

from __future__ import annotations

import pytest
from flask import Flask

from src.admin.utils.helpers import is_super_admin, require_auth, require_tenant_access
from tests.helpers.admin_session import cached_super_admin

SUPER_ADMIN = "root@operator.example"
OWN_TENANT = "acme"
OTHER_TENANT = "globex"


def _session(*, auth_method: str | None, tenant_id: str = OWN_TENANT) -> dict:
    """A signed-in session for SUPER_ADMIN that is_super_admin would recognise from its cache."""
    payload = {"user": SUPER_ADMIN, "tenant_id": tenant_id, "authenticated": True, **cached_super_admin(SUPER_ADMIN)}
    if auth_method is not None:
        payload["auth_method"] = auth_method
    return payload


@pytest.fixture
def app():
    app = Flask(__name__)
    app.secret_key = "test"

    @app.route("/tenant/<tenant_id>/settings")
    @require_tenant_access()
    def tenant_settings(tenant_id):
        return f"settings of {tenant_id}"

    @app.route("/create_tenant", methods=["POST"])
    @require_auth(admin_only=True)
    def create_tenant():
        return "created"

    @app.route("/tenant/<tenant_id>/reporting")
    @require_auth()
    def reporting(tenant_id):
        return f"reporting of {tenant_id}"

    @app.route("/tenant/<tenant_id>/login", endpoint="auth.tenant_login")
    def tenant_login(tenant_id):
        return "login"

    return app


def _client(app, session_values: dict):
    client = app.test_client()
    with client.session_transaction() as sess:
        sess.update(session_values)
    return client


def test_a_tenant_sso_session_is_not_a_super_admin_whatever_its_email(app):
    with app.test_request_context("/"):
        from flask import session

        session.update(_session(auth_method="oidc"))
        assert is_super_admin(SUPER_ADMIN) is False


def test_a_deployment_sign_in_with_the_same_email_is_a_super_admin(app):
    """The control: the same email and cache, signed in through the deployment's own login."""
    with app.test_request_context("/"):
        from flask import session

        session.update(_session(auth_method=None))
        assert is_super_admin(SUPER_ADMIN) is True


def test_a_tenant_sso_session_cannot_reach_another_tenant(app):
    response = _client(app, _session(auth_method="oidc")).get(f"/tenant/{OTHER_TENANT}/settings")
    assert response.status_code == 403


def test_a_tenant_sso_session_without_a_tenant_reaches_none(app):
    session_values = _session(auth_method="oidc")
    del session_values["tenant_id"]
    response = _client(app, session_values).get(f"/tenant/{OTHER_TENANT}/settings")
    assert response.status_code == 403


def test_a_deployment_super_admin_reaches_every_tenant(app):
    response = _client(app, _session(auth_method=None)).get(f"/tenant/{OTHER_TENANT}/settings")
    assert response.status_code == 200
    assert response.get_data(as_text=True) == f"settings of {OTHER_TENANT}"


def test_a_tenant_sso_session_cannot_use_a_super_admin_only_route(app):
    response = _client(app, _session(auth_method="oidc")).post("/create_tenant")
    assert response.status_code == 403


def test_a_deployment_super_admin_can_use_a_super_admin_only_route(app):
    response = _client(app, _session(auth_method=None)).post("/create_tenant")
    assert response.status_code == 200
    assert response.get_data(as_text=True) == "created"


def test_require_auth_holds_a_tenant_sso_session_to_its_own_tenant(app):
    client = _client(app, _session(auth_method="oidc"))
    assert client.get(f"/tenant/{OTHER_TENANT}/reporting").status_code == 403
    own = client.get(f"/tenant/{OWN_TENANT}/reporting")
    assert own.status_code == 200
    assert own.get_data(as_text=True) == f"reporting of {OWN_TENANT}"


def test_a_sign_in_clears_the_identity_an_earlier_one_left(app):
    """A tenant SSO sign-in after a deployment one must not inherit its super-admin keys."""
    from src.admin.utils.helpers import clear_session_identity

    with app.test_request_context("/"):
        from flask import session

        session.update(_session(auth_method=None))
        session["role"] = "super_admin"
        session["login_next_url"] = "/kept"
        clear_session_identity()
        assert dict(session) == {"login_next_url": "/kept"}


def test_a_tenant_sso_session_cannot_sign_up_a_tenant():
    """Signup authorizes the caller's email domain; a tenant's provider can assert any address."""
    from src.admin.app import create_app

    app = create_app({"TESTING": True, "SECRET_KEY": "test-secret"})
    session_values = {**_session(auth_method="oidc"), "user": "ceo@victim-corp.example", "signup_flow": True}

    for response in (
        _client(app, session_values).get("/signup/onboarding"),
        _client(app, session_values).post("/signup/provision", data={"publisher_name": "X"}),
    ):
        assert response.status_code == 302
        assert response.headers["Location"].endswith("/signup")
