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

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import select

from src.core.database.database_session import get_db_session
from src.core.database.models import AdapterConfig, CurrencyLimit, Tenant, User


class TestSelfServiceSignupFlow:
    """Test self-service tenant signup flow."""

    def test_landing_page_accessible_without_auth(self, integration_db, client):
        """Test that landing page is accessible without authentication."""
        response = client.get("/signup")
        assert response.status_code == 200
        assert b"Connect Your Ad Inventory to AI Buyers" in response.data
        assert b"Get Started with Google" in response.data

    def test_root_redirects_to_landing_when_not_authenticated(self, client):
        """Test that root URL redirects to landing page for unauthenticated users."""
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
            "subdomain": "testpub",
            "adapter": "mock",
        }

        response = client.post("/signup/provision", data=form_data, follow_redirects=False)

        # Should redirect to completion page
        assert response.status_code == 302
        assert "/signup/complete" in response.headers["Location"]

        # Verify tenant was created
        with get_db_session() as db_session:
            tenant = db_session.scalars(select(Tenant).filter_by(subdomain="testpub")).first()
            assert tenant is not None
            assert tenant.name == "Test Publisher"
            assert tenant.ad_server == "mock"
            assert tenant.is_active is True

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
            "subdomain": "keveltest",
            "adapter": "kevel",
            "kevel_network_id": "12345",
            "kevel_api_key": "test_api_key_12345",
        }

        response = client.post("/signup/provision", data=form_data, follow_redirects=False)

        # Should redirect to completion page
        assert response.status_code == 302
        assert "/signup/complete" in response.headers["Location"]

        # Verify tenant and adapter config
        with get_db_session() as db_session:
            tenant = db_session.scalars(select(Tenant).filter_by(subdomain="keveltest")).first()
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
            "subdomain": "gamtest",
            "adapter": "google_ad_manager",
        }

        response = client.post("/signup/provision", data=form_data, follow_redirects=False)

        # Should redirect to completion page
        assert response.status_code == 302
        assert "/signup/complete" in response.headers["Location"]

        # Verify tenant was created with GAM adapter (no credentials yet)
        with get_db_session() as db_session:
            tenant = db_session.scalars(select(Tenant).filter_by(subdomain="gamtest")).first()
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

    def test_subdomain_uniqueness_validation(self, integration_db, client):
        """Test that duplicate subdomains are rejected."""
        # Create an existing tenant
        with get_db_session() as db_session:
            existing_tenant = Tenant(
                tenant_id="existing",
                name="Existing Publisher",
                subdomain="existingpub",
                ad_server="mock",
                is_active=True,
                billing_plan="standard",
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
                enable_axe_signals=True,
                human_review_required=True,
            )
            db_session.add(existing_tenant)
            db_session.commit()

        try:
            # Try to create tenant with same subdomain
            with client.session_transaction() as sess:
                sess["signup_flow"] = True
                sess["user"] = "admin@test.com"
                sess["user_name"] = "Test Admin"

            form_data = {
                "publisher_name": "Duplicate Publisher",
                "subdomain": "existingpub",
                "adapter": "mock",
            }

            response = client.post("/signup/provision", data=form_data, follow_redirects=True)
            assert response.status_code == 200
            assert b"already taken" in response.data or b"already exists" in response.data

        finally:
            # Cleanup
            with get_db_session() as db_session:
                tenant = db_session.scalars(select(Tenant).filter_by(subdomain="existingpub")).first()
                if tenant:
                    db_session.delete(tenant)
                    db_session.commit()

    def test_reserved_subdomain_rejection(self, client):
        """Test that reserved subdomains are rejected."""
        reserved_subdomains = ["admin", "www", "api", "mcp", "a2a"]

        for subdomain in reserved_subdomains:
            with client.session_transaction() as sess:
                sess["signup_flow"] = True
                sess["user"] = f"admin@{subdomain}test.com"
                sess["user_name"] = "Test Admin"

            form_data = {
                "publisher_name": f"{subdomain.title()} Publisher",
                "subdomain": subdomain,
                "adapter": "mock",
            }

            response = client.post("/signup/provision", data=form_data, follow_redirects=True)
            assert response.status_code == 200
            assert b"reserved" in response.data.lower()

    def test_signup_completion_page_renders(self, integration_db, client):
        """Test that signup completion page renders with tenant information."""
        # Create a test tenant
        with get_db_session() as db_session:
            test_tenant = Tenant(
                tenant_id="completiontest",
                name="Completion Test Publisher",
                subdomain="completiontest",
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

        app, _ = create_app({"TESTING": True, "SECRET_KEY": "test_key"})

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

    def test_session_cleanup_after_provisioning(self, integration_db, client):
        """Test that signup session flags are cleared after provisioning."""
        with client.session_transaction() as sess:
            sess["signup_flow"] = True
            sess["signup_step"] = "oauth"
            sess["user"] = "admin@sessiontest.com"
            sess["user_name"] = "Session Test"

        form_data = {
            "publisher_name": "Session Test Publisher",
            "subdomain": "sessiontest",
            "adapter": "mock",
        }

        response = client.post("/signup/provision", data=form_data, follow_redirects=False)
        assert response.status_code == 302

        # Verify session flags are cleaned up
        with client.session_transaction() as sess:
            assert "signup_flow" not in sess
            assert "signup_step" not in sess
            # User session should be set for tenant access
            assert sess.get("tenant_id") == "sessiontest"
            assert sess.get("is_tenant_admin") is True

        # Cleanup
        with get_db_session() as db_session:
            tenant = db_session.scalars(select(Tenant).filter_by(subdomain="sessiontest")).first()
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

    app, _ = create_app({"TESTING": True, "SECRET_KEY": "test_key"})
    with app.test_client() as test_client:
        yield test_client
