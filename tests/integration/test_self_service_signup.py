"""
Integration tests for self-service tenant signup flow.

Tests the complete signup journey:
1. Landing page access (unauthenticated)
2. OAuth initiation with signup context
3. OAuth callback redirecting to onboarding
4. Onboarding wizard form rendering
5. Tenant provisioning with various adapters
6. Success page and dashboard redirect
"""

import re
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import select

from src.core.credentials import hash_token
from src.core.database.database_session import get_db_session
from src.core.database.models import AdapterConfig, CurrencyLimit, Tenant, User
from src.core.database.repositories.principal import PrincipalRepository
from tests.harness._base import IntegrationEnv

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_TOKEN_RE = re.compile(r"tok_[A-Za-z0-9_-]{20,}")


def _provisioned_tenant_id(response) -> str:
    """The tenant id read off the completion page the provision POST renders itself.

    The POST renders ``signup_complete.html`` in its own response instead of flashing
    and redirecting, because the one-time credential reveal must not travel in the
    signed session cookie (``src/admin/blueprints/public.py``). So the tenant id comes
    out of the rendered body, not a ``Location`` header.
    """
    assert response.status_code == 200, (
        f"expected the completion page rendered in the POST response, got {response.status_code}"
    )
    ids = set(_UUID_RE.findall(response.data.decode()))
    assert len(ids) == 1, f"expected exactly one tenant id on the completion page, found {sorted(ids)}"
    return ids.pop()


class TestSelfServiceSignupFlow:
    """Test self-service tenant signup flow."""

    @pytest.fixture(autouse=True)
    def _multi_tenant_deployment(self, monkeypatch, client):
        """Self-serve signup only exists on a deployment that can host a self-serve tenant.

        A signing-up publisher cannot know where the deployment answers, so signup is the one
        creation path that DERIVES its tenant's ``virtual_host`` — and it may only do so where
        ``config/nginx/nginx-multi-tenant.conf`` serves ``*.${SALES_AGENT_DOMAIN}`` and lets the
        app pick the tenant from the ``Host``. Those two settings together are what make the
        derived name a host something actually serves; without them the route refuses, because
        deriving anyway publishes an unreachable agent URL (#1845).

        So these cases have to declare the deployment they are testing. Two traps:

        * Set on the settings OBJECT, not the environment. Settings are built once and an env
          write lands only before the first read, so ``monkeypatch.setenv`` is silently ignored.
        * Takes ``client`` so it runs AFTER it. ``create_app`` calls ``load_settings()``
          (``src/admin/app.py``), which REBUILDS the global settings — patching first and
          building the client second throws the patch away, and the route then refuses with
          the fields apparently set.
        """
        from src.core.config import get_settings

        runtime = get_settings().runtime
        monkeypatch.setattr(runtime, "adcp_multi_tenant", True)
        monkeypatch.setattr(runtime, "sales_agent_domain", "sales-agent.adcp.test")

    def test_signup_is_refused_when_the_deployment_hosts_no_wildcard(self, integration_db, client, monkeypatch):
        """Without the wildcard, signup refuses rather than inventing a host for the tenant.

        The other half of the rule above, and the half nothing graded until now: on a
        single-tenant install nginx is ``server_name _`` with no wildcard, so
        ``f"{subdomain}.{sales_agent_domain}"`` names nothing. Refusing is what keeps the
        derivation true by construction of the deployment that permits it.
        """
        from src.core.config import get_settings

        monkeypatch.setattr(get_settings().runtime, "adcp_multi_tenant", False)

        with client.session_transaction() as sess:
            sess["signup_flow"] = True
            sess["user"] = "newpublisher@example.com"
            sess["user_name"] = "New Publisher"

        response = client.post(
            "/signup/provision",
            data={"publisher_name": "No Wildcard Publisher", "adapter": "mock"},
            follow_redirects=False,
        )

        assert response.status_code == 302, "a refused signup redirects back to onboarding"
        assert "/signup/onboarding" in response.headers["Location"]

        # Through the harness's own session rather than get_db_session() in a test body —
        # the surrounding file is allowlisted for that and a new test does not inherit the
        # exemption. Queried by name because that is the only key the caller supplied: the
        # subdomain and the host are both derived server-side, and the refusal fires before
        # either exists.
        with IntegrationEnv() as env:
            created = env.get_session().scalars(select(Tenant).filter_by(name="No Wildcard Publisher")).first()
        assert created is None, "a tenant was provisioned on a deployment that serves no wildcard host"

    def test_landing_page_accessible_without_auth(self, integration_db, client):
        """Test that landing page is accessible without authentication."""
        response = client.get("/signup")
        assert response.status_code == 200
        assert b"Connect Your Ad Inventory to AI Buyers" in response.data
        assert b"Get Started with Google" in response.data

    def test_root_redirects_to_landing_when_not_authenticated(self, integration_db, client):
        """Test that root URL redirects to landing page for unauthenticated users in multi-tenant mode."""
        # In multi-tenant mode, unauthenticated users at root should redirect to signup
        with patch("src.core.config_loader.is_single_tenant_mode", return_value=False):
            response = client.get("/", follow_redirects=False)
            assert response.status_code == 302
            assert "/signup" in response.headers["Location"]

    def test_signup_start_sets_session_context(self, client):
        """Test that signup start sets signup context in session."""
        response = client.get("/signup/start", follow_redirects=False)
        assert response.status_code == 302

        # Check that session has signup context
        with client.session_transaction() as sess:
            assert sess.get("signup_flow") is True
            assert sess.get("signup_step") == "oauth"

    def test_onboarding_requires_signup_flow(self, client):
        """Test that onboarding requires active signup flow in session."""
        response = client.get("/signup/onboarding")
        assert response.status_code == 302
        assert b"Invalid signup session" in response.data or "/signup" in response.headers.get("Location", "")

    def test_onboarding_wizard_renders_with_authenticated_user(self, client):
        """Test that onboarding wizard renders for authenticated users in signup flow."""
        with client.session_transaction() as sess:
            sess["signup_flow"] = True
            sess["user"] = "test@publisher.com"
            sess["user_name"] = "Test Publisher"

        response = client.get("/signup/onboarding")
        assert response.status_code == 200
        assert b"Create Your Sales Agent Account" in response.data
        assert b"Test Publisher" in response.data  # Template shows user_name, not email
        assert b"Publisher Information" in response.data
        assert b"Ad Server Integration" in response.data  # Changed from "Select Your Ad Server" for GAM-only signup

    def test_provision_tenant_mock_adapter(self, integration_db, client):
        """Test tenant provisioning with mock adapter."""
        with client.session_transaction() as sess:
            sess["signup_flow"] = True
            sess["user"] = "admin@testpublisher.com"
            sess["user_name"] = "Test Admin"

        form_data = {
            "publisher_name": "Test Publisher",
            # No subdomain - auto-generated from UUID
            "adapter": "mock",
        }

        response = client.post("/signup/provision", data=form_data, follow_redirects=False)

        tenant_id = _provisioned_tenant_id(response)

        # Verify tenant was created
        with get_db_session() as db_session:
            tenant = db_session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
            assert tenant is not None
            assert tenant.name == "Test Publisher"
            assert tenant.ad_server == "mock"
            assert tenant.is_active is True
            # Subdomain should be 8-char hex (first 8 chars of UUID)
            assert tenant.subdomain is not None
            assert len(tenant.subdomain) == 8

            # Verify adapter config
            adapter_config = db_session.scalars(select(AdapterConfig).filter_by(tenant_id=tenant.tenant_id)).first()
            assert adapter_config is not None
            assert adapter_config.adapter_type == "mock"

            # Verify currency limit was created
            currency_limit = db_session.scalars(
                select(CurrencyLimit).filter_by(tenant_id=tenant.tenant_id, currency_code="USD")
            ).first()
            assert currency_limit is not None
            assert currency_limit.max_daily_package_spend == 10000
            assert currency_limit.min_package_budget == 100

            # Verify admin user was created
            user = db_session.scalars(
                select(User).filter_by(tenant_id=tenant.tenant_id, email="admin@testpublisher.com")
            ).first()
            assert user is not None
            assert user.role == "admin"
            assert user.is_active is True

            # Cleanup (currency_limit will cascade delete with tenant)
            db_session.delete(user)
            db_session.delete(adapter_config)
            db_session.delete(tenant)
            db_session.commit()

    def test_provision_tenant_kevel_adapter_with_credentials(self, integration_db, client):
        """Test tenant provisioning with Kevel adapter and credentials."""
        with client.session_transaction() as sess:
            sess["signup_flow"] = True
            sess["user"] = "admin@keveltest.com"
            sess["user_name"] = "Kevel Admin"

        form_data = {
            "publisher_name": "Kevel Test Publisher",
            # No subdomain - auto-generated from UUID
            "adapter": "kevel",
            "kevel_network_id": "12345",
            "kevel_api_key": "test_api_key_12345",
        }

        response = client.post("/signup/provision", data=form_data, follow_redirects=False)

        tenant_id = _provisioned_tenant_id(response)

        # Verify tenant and adapter config
        with get_db_session() as db_session:
            tenant = db_session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
            assert tenant is not None
            assert tenant.ad_server == "kevel"

            adapter_config = db_session.scalars(select(AdapterConfig).filter_by(tenant_id=tenant.tenant_id)).first()
            assert adapter_config is not None
            assert adapter_config.adapter_type == "kevel"
            assert adapter_config.kevel_network_id == "12345"
            assert adapter_config.kevel_api_key == "test_api_key_12345"

            # Cleanup
            user = db_session.scalars(select(User).filter_by(tenant_id=tenant.tenant_id)).first()
            if user:
                db_session.delete(user)
            db_session.delete(adapter_config)
            db_session.delete(tenant)
            db_session.commit()

    def test_provision_tenant_gam_adapter_without_oauth(self, integration_db, client):
        """Test tenant provisioning with GAM adapter (to be configured later)."""
        with client.session_transaction() as sess:
            sess["signup_flow"] = True
            sess["user"] = "admin@gamtest.com"
            sess["user_name"] = "GAM Admin"

        form_data = {
            "publisher_name": "GAM Test Publisher",
            # No subdomain - auto-generated from UUID
            "adapter": "google_ad_manager",
        }

        response = client.post("/signup/provision", data=form_data, follow_redirects=False)

        tenant_id = _provisioned_tenant_id(response)

        # Verify tenant was created with GAM adapter (no credentials yet)
        with get_db_session() as db_session:
            tenant = db_session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
            assert tenant is not None
            assert tenant.ad_server == "google_ad_manager"

            adapter_config = db_session.scalars(select(AdapterConfig).filter_by(tenant_id=tenant.tenant_id)).first()
            assert adapter_config is not None
            assert adapter_config.adapter_type == "google_ad_manager"
            # Refresh token should be empty (to be configured later)
            assert adapter_config.gam_refresh_token is None or adapter_config.gam_refresh_token == ""

            # Cleanup
            user = db_session.scalars(select(User).filter_by(tenant_id=tenant.tenant_id)).first()
            if user:
                db_session.delete(user)
            db_session.delete(adapter_config)
            db_session.delete(tenant)
            db_session.commit()

    def test_subdomain_auto_generation(self, integration_db, client):
        """Test that subdomains are automatically generated as 8-char hex UUIDs."""
        with client.session_transaction() as sess:
            sess["signup_flow"] = True
            sess["user"] = "admin@test.com"
            sess["user_name"] = "Test Admin"

        form_data = {
            "publisher_name": "UUID Test Publisher",
            # No subdomain field - auto-generated
            "adapter": "mock",
        }

        response = client.post("/signup/provision", data=form_data, follow_redirects=False)
        tenant_id = _provisioned_tenant_id(response)

        # Verify subdomain is 8-char hex (first 8 chars of UUID)
        with get_db_session() as db_session:
            tenant = db_session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
            assert tenant is not None
            assert tenant.subdomain is not None
            assert len(tenant.subdomain) == 8
            # Check it's valid hex (only 0-9 and a-f)
            assert all(c in "0123456789abcdef" for c in tenant.subdomain)

            # Cleanup
            user = db_session.scalars(select(User).filter_by(tenant_id=tenant.tenant_id)).first()
            adapter_config = db_session.scalars(select(AdapterConfig).filter_by(tenant_id=tenant.tenant_id)).first()
            if user:
                db_session.delete(user)
            if adapter_config:
                db_session.delete(adapter_config)
            db_session.delete(tenant)
            db_session.commit()

    def test_subdomain_uniqueness_extremely_rare_collision(self, integration_db, client):
        """Test that extremely rare UUID collisions are handled (astronomically unlikely)."""
        # This test documents the collision handling behavior, though collisions are astronomically rare
        # We create a tenant, then try to create another with hope of collision detection
        # In practice, UUID4 8-char hex has 4.3 billion possibilities, so collisions are extremely unlikely
        with client.session_transaction() as sess:
            sess["signup_flow"] = True
            sess["user"] = "admin@test1.com"
            sess["user_name"] = "Test Admin 1"

        form_data = {
            "publisher_name": "First Publisher",
            "adapter": "mock",
        }

        response1 = client.post("/signup/provision", data=form_data, follow_redirects=False)
        tenant_id_1 = _provisioned_tenant_id(response1)

        # Create second tenant - should get different UUID/subdomain
        with client.session_transaction() as sess:
            sess["signup_flow"] = True
            sess["user"] = "admin@test2.com"
            sess["user_name"] = "Test Admin 2"

        form_data["publisher_name"] = "Second Publisher"
        response2 = client.post("/signup/provision", data=form_data, follow_redirects=False)
        tenant_id_2 = _provisioned_tenant_id(response2)

        # Verify different tenant_ids and subdomains
        with get_db_session() as db_session:
            tenant1 = db_session.scalars(select(Tenant).filter_by(tenant_id=tenant_id_1)).first()
            tenant2 = db_session.scalars(select(Tenant).filter_by(tenant_id=tenant_id_2)).first()

            assert tenant1.tenant_id != tenant2.tenant_id
            assert tenant1.subdomain != tenant2.subdomain

            # Cleanup
            for tenant in [tenant1, tenant2]:
                user = db_session.scalars(select(User).filter_by(tenant_id=tenant.tenant_id)).first()
                adapter_config = db_session.scalars(select(AdapterConfig).filter_by(tenant_id=tenant.tenant_id)).first()
                if user:
                    db_session.delete(user)
                if adapter_config:
                    db_session.delete(adapter_config)
                db_session.delete(tenant)
            db_session.commit()

    def test_signup_completion_page_renders(self, integration_db, client):
        """Test that signup completion page renders with tenant information."""
        # Create a test tenant
        with get_db_session() as db_session:
            test_tenant = Tenant(
                tenant_id="completiontest",
                name="Completion Test Publisher",
                subdomain="completiontest",
                virtual_host="completiontest.adcp.test",
                ad_server="mock",
                is_active=True,
                billing_plan="standard",
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
                enable_axe_signals=True,
                human_review_required=True,
            )
            db_session.add(test_tenant)
            db_session.commit()

        try:
            response = client.get("/signup/complete?tenant_id=completiontest")
            assert response.status_code == 200
            assert b"Welcome to AdCP Sales Agent!" in response.data
            assert b"Completion Test Publisher" in response.data
            assert b"completiontest" in response.data
            assert b"Next Steps" in response.data

        finally:
            # Cleanup
            with get_db_session() as db_session:
                tenant = db_session.scalars(select(Tenant).filter_by(tenant_id="completiontest")).first()
                if tenant:
                    db_session.delete(tenant)
                    db_session.commit()

    @pytest.mark.requires_db  # Uses database - skip in quick mode
    def test_oauth_callback_redirects_to_onboarding_for_signup_flow(self, client, integration_db):
        """Test that OAuth callback redirects to onboarding when signup_flow is active.

        This test verifies the signup flow logic in the OAuth callback handler.
        """
        # Import the app to access oauth object
        from src.admin.app import create_app

        app = create_app({"TESTING": True, "SECRET_KEY": "test_key"})

        # Mock OAuth at the app level (where it's actually used)
        with patch.object(app, "oauth", create=True) as mock_oauth:
            mock_google = MagicMock()
            mock_google.authorize_access_token.return_value = {
                "userinfo": {"email": "newuser@example.com", "name": "New User"},
                "id_token": None,
            }
            mock_oauth.google = mock_google

            # Create a test client with the mocked app
            with app.test_client() as test_client:
                # Set signup flow in session
                with test_client.session_transaction() as sess:
                    sess["signup_flow"] = True

                # Make the OAuth callback request
                response = test_client.get("/auth/google/callback", follow_redirects=False)

                # Should redirect to onboarding wizard
                assert response.status_code == 302
                assert "/signup/onboarding" in response.headers["Location"]

                # Verify session was updated with user info
                with test_client.session_transaction() as sess:
                    assert sess.get("user") == "newuser@example.com"
                    assert sess.get("user_name") == "New User"
                    assert sess.get("signup_flow") is True

    def test_provision_reveals_the_token_in_the_body_and_never_in_a_response_header(self, integration_db, client):
        """The minted principal token is shown once in the page body and rides no header.

        A flash is stored in the signed session cookie, so a flashed credential leaves
        the server in ``Set-Cookie``. In production that cookie is ``HTTPONLY=False``,
        ``SAMESITE="None"`` and shared across subdomains (``src/admin/app.py``:126-136),
        which makes it readable by any script on the domain and sent on cross-site
        requests. Both halves are asserted here, because either one alone passes for the
        wrong reason: a page that reveals nothing has no token to leak, and a header
        check over a token the page never minted grades nothing.

        The reveal is genuine rather than decorative: the token found in the body is
        hashed and looked up, so it must be the credential that actually authenticates
        the principal the signup created.
        """
        with client.session_transaction() as sess:
            sess["signup_flow"] = True
            sess["user"] = "admin@revealtest.com"
            sess["user_name"] = "Reveal Test"

        form_data = {"publisher_name": "Reveal Test Publisher", "adapter": "mock"}
        response = client.post("/signup/provision", data=form_data, follow_redirects=False)
        tenant_id = _provisioned_tenant_id(response)

        body = response.data.decode()
        found = _TOKEN_RE.findall(body)
        assert len(found) == 1, f"expected the plaintext token exactly once in the body, found {len(found)}"
        revealed = found[0]

        # Genuine credential: the stored row keeps only a hash, so this resolves only if
        # the revealed value is the one that authenticates. Through the harness's own
        # session rather than get_db_session() in a test body — the surrounding file is
        # allowlisted for that, and a new test does not inherit the exemption.
        with IntegrationEnv() as env:
            principal = PrincipalRepository(env.get_session(), tenant_id).find_by_token_hash(hash_token(revealed))
            assert principal is not None, "the revealed token does not hash to any stored principal"
            assert principal.principal_id == f"{tenant_id}_default"

        # The disclosure path is closed. This has to read the DECODED session, not the
        # Set-Cookie header: Flask signs and base64-encodes the session, so a flashed
        # token is not a literal substring of the header and a header scan would pass
        # over the exact defect this test exists for.
        with client.session_transaction() as sess:
            for key, value in sess.items():
                assert revealed not in repr(value), f"the plaintext token is stored in session[{key!r}]"

        # The header scan catches the other shape of the same mistake — a token put in a
        # redirect URL or a custom header, where it lands in logs and Referer.
        for name, value in response.headers.items():
            assert revealed not in value, f"the plaintext token leaked in the {name} header"

        # Cleanup through the harness's session, for the same reason as the read-back.
        with IntegrationEnv() as env:
            session = env.get_session()
            tenant = session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
            if tenant:
                session.delete(tenant)
                session.commit()

    def test_session_cleanup_after_provisioning(self, integration_db, client):
        """Test that signup session flags are cleared after provisioning."""
        with client.session_transaction() as sess:
            sess["signup_flow"] = True
            sess["signup_step"] = "oauth"
            sess["user"] = "admin@sessiontest.com"
            sess["user_name"] = "Session Test"

        form_data = {
            "publisher_name": "Session Test Publisher",
            # No subdomain - auto-generated from UUID
            "adapter": "mock",
        }

        response = client.post("/signup/provision", data=form_data, follow_redirects=False)
        tenant_id = _provisioned_tenant_id(response)

        # Verify session flags are cleaned up
        with client.session_transaction() as sess:
            assert "signup_flow" not in sess
            assert "signup_step" not in sess
            # User session should be set for tenant access
            assert sess.get("tenant_id") == tenant_id
            assert sess.get("is_tenant_admin") is True

        # Cleanup
        with get_db_session() as db_session:
            tenant = db_session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
            if tenant:
                user = db_session.scalars(select(User).filter_by(tenant_id=tenant.tenant_id)).first()
                adapter_config = db_session.scalars(select(AdapterConfig).filter_by(tenant_id=tenant.tenant_id)).first()
                if user:
                    db_session.delete(user)
                if adapter_config:
                    db_session.delete(adapter_config)
                db_session.delete(tenant)
                db_session.commit()


@pytest.fixture
def client():
    """Create Flask test client."""
    from src.admin.app import create_app

    app = create_app({"TESTING": True, "SECRET_KEY": "test_key"})
    with app.test_client() as test_client:
        yield test_client
