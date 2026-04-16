"""Regression test for oidc.py test_initiate() nested-session refactor.

Before this refactor: test_initiate() opened an outer session, then called
get_or_create_auth_config() which opened an inner session. Under bare
sessionmaker (post-B2 in the Flask-to-FastAPI migration), the returned
TenantAuthConfig would be detached after the inner session closes, and any
attribute access that triggers a refresh/lazy-load on the ORM object would
raise DetachedInstanceError.

After this refactor: the config is queried inline against the outer session,
so attribute reads happen against an attached object under both
scoped_session (current) and bare sessionmaker (post-B2).

This test mirrors the pattern already applied to callback() at oidc.py:229.
"""

from datetime import UTC, datetime

import pytest

from src.core.database.database_session import get_db_session
from src.core.database.models import Tenant, TenantAuthConfig


@pytest.fixture
def tenant_without_oidc_config(integration_db):
    """Create a tenant that has NO TenantAuthConfig row.

    Exercises the `not config` branch of test_initiate()'s inline query —
    the same branch that `callback()` at oidc.py:229 uses.
    """
    tenant_id = "oidc_test_tenant_no_cfg"
    now = datetime.now(UTC)
    with get_db_session() as session:
        tenant = Tenant(
            tenant_id=tenant_id,
            name="OIDC Regression Tenant (no cfg)",
            subdomain="oidc-regress-nocfg",
            is_active=True,
            ad_server="mock",
            auth_setup_mode=False,
            authorized_emails=["test@example.com"],
            created_at=now,
            updated_at=now,
        )
        session.add(tenant)
        session.commit()
    yield tenant_id
    with get_db_session() as session:
        session.query(Tenant).filter_by(tenant_id=tenant_id).delete()
        session.commit()


@pytest.fixture
def tenant_with_oidc_config(integration_db):
    """Create a tenant with a TenantAuthConfig that has oidc_client_id set.

    Exercises the attribute-read branch — the code path most at risk of
    DetachedInstanceError under bare sessionmaker.
    """
    tenant_id = "oidc_test_tenant_with_cfg"
    now = datetime.now(UTC)
    with get_db_session() as session:
        tenant = Tenant(
            tenant_id=tenant_id,
            name="OIDC Regression Tenant (with cfg)",
            subdomain="oidc-regress-withcfg",
            is_active=True,
            ad_server="mock",
            auth_setup_mode=False,
            authorized_emails=["test@example.com"],
            created_at=now,
            updated_at=now,
        )
        session.add(tenant)
        session.flush()

        auth_config = TenantAuthConfig(
            tenant_id=tenant_id,
            oidc_enabled=False,
            oidc_provider="google",
            oidc_discovery_url="https://accounts.google.com/.well-known/openid-configuration",
            oidc_client_id="regression-test-client-id",
            oidc_scopes="openid email profile",
            created_at=now,
        )
        # Exercises the encryption setter — same path used by test_initiate()
        auth_config.oidc_client_secret = "regression-test-secret"
        session.add(auth_config)
        session.commit()
    yield tenant_id
    with get_db_session() as session:
        session.query(TenantAuthConfig).filter_by(tenant_id=tenant_id).delete()
        session.query(Tenant).filter_by(tenant_id=tenant_id).delete()
        session.commit()


@pytest.mark.requires_db
def test_test_initiate_redirects_when_no_config(admin_client, tenant_without_oidc_config):
    """When TenantAuthConfig row is absent, test_initiate returns a redirect
    with a flash — it must not raise DetachedInstanceError when evaluating
    `if not config or not config.oidc_client_id`.
    """
    response = admin_client.get(
        f"/admin/auth/oidc/test/{tenant_without_oidc_config}",
        follow_redirects=False,
    )
    # The route redirects (302) to tenant_settings when OIDC is not configured.
    assert response.status_code in (
        302,
        303,
    ), f"Expected redirect, got {response.status_code} — if this is 500, the nested-session refactor regressed"


@pytest.mark.requires_db
def test_test_initiate_reads_config_attributes_without_detached_error(
    admin_client, tenant_with_oidc_config, monkeypatch
):
    """When TenantAuthConfig has oidc_client_id, test_initiate reads
    .oidc_client_id, .oidc_client_secret, .oidc_discovery_url, .oidc_scopes
    on the ORM object. Under bare sessionmaker, a detached object would
    raise DetachedInstanceError on these reads.

    We stub authorize_redirect to avoid a real network call to Google's
    discovery endpoint — we only care that attribute reads succeed.
    """
    from authlib.integrations.flask_client import OAuth

    # Patch OAuth.register so no real client is built against Google's
    # discovery URL. We then stub the `.test_oidc.authorize_redirect` call
    # to return a harmless redirect.
    from flask import redirect as flask_redirect

    captured = {}

    class _StubOidcClient:
        def authorize_redirect(self, redirect_uri):
            captured["redirect_uri"] = redirect_uri
            return flask_redirect("/stub-oauth-redirect")

    def _register_stub(self, name, **kwargs):
        captured["register_kwargs"] = kwargs
        setattr(self, name, _StubOidcClient())

    monkeypatch.setattr(OAuth, "register", _register_stub, raising=True)

    response = admin_client.get(
        f"/admin/auth/oidc/test/{tenant_with_oidc_config}",
        follow_redirects=False,
    )

    # If attribute reads against a detached ORM object failed, we'd see a 500.
    # On success, we get a redirect (from our stub).
    assert response.status_code in (302, 303), (
        f"Expected redirect from stubbed authorize_redirect, got "
        f"{response.status_code} — DetachedInstanceError regression likely"
    )

    # Verify the refactored code actually read the ORM attributes and passed
    # them to OAuth.register — proves attribute access succeeded.
    assert "register_kwargs" in captured, "OAuth.register was never called"
    register_kwargs = captured["register_kwargs"]
    assert register_kwargs["client_id"] == "regression-test-client-id"
    assert register_kwargs["client_secret"] == "regression-test-secret"
    assert register_kwargs["server_metadata_url"] == "https://accounts.google.com/.well-known/openid-configuration"
    assert register_kwargs["client_kwargs"]["scope"] == "openid email profile"
