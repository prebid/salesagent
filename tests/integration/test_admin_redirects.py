"""Registered admin destinations after real saves and error paths."""

from urllib.parse import urlsplit

import pytest

from tests.harness._base import BareIntegrationEnv
from tests.helpers.admin_session import admin_auth_session, admin_test_app
from tests.integration.gam_oauth_helpers import callback, log_in, start_flow
from tests.integration.property_list_helpers import allow_local_origin


@pytest.fixture
def admin_env(integration_db, monkeypatch):
    from src.core.config import get_settings

    monkeypatch.setattr(get_settings().runtime, "adcp_multi_tenant", True)
    with BareIntegrationEnv() as env:
        tenant, _ = env.setup_default_data(name="Before", subdomain="before")
        app = admin_test_app()
        with app.test_client() as client:
            admin_auth_session(client, tenant.tenant_id)
            yield env, client, tenant


@pytest.mark.requires_db
@pytest.mark.parametrize("script_root", ["", "/admin"])
@pytest.mark.parametrize("valid", [True, False])
def test_tenant_save_redirect_and_persistence(admin_env, script_root, valid):
    env, client, tenant = admin_env
    tenant_id = tenant.tenant_id
    response = client.post(
        f"/tenant/{tenant_id}/update",
        data={"name": "After" if valid else "", "subdomain": "after"},
        base_url="https://localhost",
        environ_overrides={"SCRIPT_NAME": script_root},
    )
    assert response.status_code == 302
    assert response.headers["Location"] == f"{script_root}/tenant/{tenant_id}/settings"
    env._session.refresh(tenant)
    assert (tenant.name, tenant.subdomain) == (("After", "after") if valid else ("Before", "before"))
    destination = urlsplit(response.headers["Location"]).path.removeprefix(script_root)
    page = client.get(destination, base_url="https://localhost", environ_overrides={"SCRIPT_NAME": script_root})
    assert page.status_code == 200


@pytest.mark.requires_db
def test_anonymous_save_cannot_write(admin_env):
    env, client, tenant = admin_env
    with client.session_transaction() as session:
        session.clear()
    response = client.post(f"/tenant/{tenant.tenant_id}/update", data={"name": "After", "subdomain": "after"})
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]
    env._session.refresh(tenant)
    assert (tenant.name, tenant.subdomain) == ("Before", "before")


@pytest.mark.requires_db
@pytest.mark.parametrize("script_root", ["", "/admin"])
def test_platform_settings_redirect(admin_env, script_root):
    _, client, _ = admin_env
    response = client.post("/settings/update", environ_overrides={"SCRIPT_NAME": script_root})
    assert response.status_code == 302
    assert response.headers["Location"] == f"{script_root}/settings"
    assert client.get("/settings", environ_overrides={"SCRIPT_NAME": script_root}).status_code == 200


@pytest.mark.requires_db
@pytest.mark.parametrize("script_root", ["", "/admin"])
def test_unconfigured_gam_returns_to_tenant_settings(admin_env, script_root):
    _, client, tenant = admin_env
    client.application.oauth = None
    response = client.get(f"/auth/gam/authorize/{tenant.tenant_id}", environ_overrides={"SCRIPT_NAME": script_root})
    assert response.status_code == 302
    assert response.headers["Location"] == f"{script_root}/tenant/{tenant.tenant_id}/settings"


@pytest.mark.requires_db
@pytest.mark.parametrize("script_root", ["", "/admin"])
@pytest.mark.parametrize(
    "status,body,saved_token",
    [
        (200, b'{"access_token":"short-lived"}', None),
        (400, b'{"error":"invalid_grant"}', None),
        (503, b'{"error":"unavailable"}', None),
        (200, b'{"refresh_token":"local-test-refresh-token"}', "local-test-refresh-token"),
    ],
    ids=["missing-refresh-token", "rejected-code", "unavailable", "success"],
)
def test_gam_callback_redirect_and_persistence(
    admin_env, local_origin_tls, gam_oauth_configured, monkeypatch, script_root, status, body, saved_token
):
    from src.core.config import get_settings
    from src.core.database.models import AdapterConfig
    from src.core.security.egress.destination import VendorConstant
    from src.services import google_oauth_client
    from tests.factories import UserFactory

    env, client, tenant = admin_env
    tenant_id = tenant.tenant_id
    user = UserFactory(tenant=tenant, role="admin")
    with client.session_transaction() as session:
        session.clear()
    log_in(client, user)
    client.application.oauth = object()
    runtime = get_settings().runtime
    monkeypatch.setattr(runtime, "production", False)
    monkeypatch.setattr(runtime, "environment", "development")
    monkeypatch.setattr(runtime, "fly_app_name", "")
    allow_local_origin(monkeypatch)
    monkeypatch.setattr(
        google_oauth_client, "GOOGLE_TOKEN_URL", VendorConstant(url=f"{local_origin_tls.base_url}/token")
    )
    local_origin_tls.respond_with(status, body=body)

    response = callback(client, start_flow(client, tenant_id, script_root=script_root), script_root=script_root)

    assert response.status_code == 302
    assert response.headers["Location"] == f"{script_root}/tenant/{tenant_id}/settings"
    assert local_origin_tls.hits == 1
    env.get_session().expire_all()
    adapter = env.get_session().get(AdapterConfig, tenant_id)
    assert (adapter.gam_refresh_token if adapter else None) == saved_token
    assert tenant.ad_server == ("google_ad_manager" if saved_token else "mock")
    assert (adapter is not None) == (saved_token is not None)
    assert (
        client.get(f"/tenant/{tenant_id}/settings", environ_overrides={"SCRIPT_NAME": script_root}).status_code == 200
    )


@pytest.mark.requires_db
@pytest.mark.parametrize(
    "suffix",
    [
        "authorized-properties",
        "authorized-properties/upload",
        "property-tags",
        "authorized-properties/create",
        "authorized-properties/missing/edit",
    ],
)
@pytest.mark.parametrize("script_root", ["", "/admin"])
def test_missing_property_tenant_returns_to_admin_entry(admin_env, suffix, script_root):
    _, client, _ = admin_env
    response = client.get(f"/tenant/missing/{suffix}", environ_overrides={"SCRIPT_NAME": script_root})
    assert response.status_code == 302
    assert response.headers["Location"] in ("/admin", "/admin/")
    destination = urlsplit(response.headers["Location"]).path.removeprefix(script_root) or "/"
    assert (
        client.get(
            destination, base_url="https://localhost", environ_overrides={"SCRIPT_NAME": script_root}
        ).status_code
        == 200
    )


@pytest.mark.requires_db
def test_member_cannot_save_another_tenant(admin_env):
    from tests.factories import TenantFactory, UserFactory

    env, client, tenant = admin_env
    own_tenant = TenantFactory()
    user = UserFactory(tenant=own_tenant, role="admin", email="member@example.com")
    with client.session_transaction() as session:
        session.clear()
        session["user"] = {"email": user.email}
        session["role"] = "tenant_admin"
    response = client.post(
        f"/tenant/{tenant.tenant_id}/update",
        data={"name": "After", "subdomain": "after"},
        base_url="https://localhost",
    )
    assert response.status_code == 403
    env._session.refresh(tenant)
    assert (tenant.name, tenant.subdomain) == ("Before", "before")
