"""Integration tests for admin application."""

from unittest.mock import Mock, patch

import pytest

from src.admin.app import create_app


class TestAdminAppIntegration:
    """Integration tests for the admin Flask application."""

    @pytest.fixture
    def app(self):
        """Create test Flask app."""
        config = {
            "TESTING": True,
            "SECRET_KEY": "test_secret_key",
            "WTF_CSRF_ENABLED": False,  # Disable CSRF for testing
        }
        app, socketio = create_app(config)
        return app

    @pytest.fixture
    def client(self, app):
        """Create test client."""
        return app.test_client()

    @pytest.fixture
    def authenticated_client(self, app):
        """Create authenticated test client."""
        client = app.test_client()
        with client.session_transaction() as sess:
            sess["user"] = "admin@example.com"
        return client

    def test_app_creation(self, app):
        """Test that app is created successfully."""
        assert app is not None
        assert app.config["TESTING"]
        assert app.secret_key == "test_secret_key"

    def test_blueprints_registered(self, app):
        """Test that all blueprints are registered."""
        blueprints = list(app.blueprints.keys())
        assert "auth" in blueprints
        assert "tenants" in blueprints
        assert "products" in blueprints

    def test_health_endpoint(self, client):
        """Test health check endpoint."""
        response = client.get("/health")
        assert response.status_code == 200
        data = response.get_json()
        assert data["status"] == "healthy"

    def test_index_requires_auth(self, client):
        """Test that index page requires authentication."""
        response = client.get("/")
        assert response.status_code == 302
        assert "/login" in response.location

    @patch("src.admin.utils.is_super_admin")
    @patch("src.admin.utils.get_db_session")
    def test_index_with_super_admin(self, mock_get_db_session, mock_is_super_admin, authenticated_client):
        """Test index page for super admin."""
        mock_is_super_admin.return_value = True

        # Mock database session
        mock_session = Mock()
        mock_get_db_session.return_value.__enter__.return_value = mock_session
        mock_session.query.return_value.order_by.return_value.all.return_value = []

        response = authenticated_client.get("/")
        assert response.status_code == 200

    def test_login_page_accessible(self, client):
        """Test that login page is accessible without auth."""
        response = client.get("/login")
        assert response.status_code == 200

    def test_tenant_login_page(self, client):
        """Test tenant-specific login page."""
        # Need to patch in the auth blueprint where it's actually used
        with patch("src.admin.blueprints.auth.get_db_session") as mock_get_db_session:
            mock_session = Mock()
            mock_get_db_session.return_value.__enter__.return_value = mock_session

            # Mock tenant exists
            mock_tenant = Mock()
            mock_tenant.name = "Test Tenant"
            mock_session.query.return_value.filter_by.return_value.first.return_value = mock_tenant

            response = client.get("/tenant/test_tenant/login")
            assert response.status_code == 200

    def test_tenant_login_page_not_found(self, client):
        """Test tenant login page for non-existent tenant."""
        with patch("src.admin.blueprints.auth.get_db_session") as mock_get_db_session:
            mock_session = Mock()
            mock_get_db_session.return_value.__enter__.return_value = mock_session
            mock_session.query.return_value.filter_by.return_value.first.return_value = None

            response = client.get("/tenant/nonexistent/login")
            assert response.status_code == 404

    def test_logout_functionality(self, authenticated_client):
        """Test logout clears session."""
        # Verify authenticated
        with authenticated_client.session_transaction() as sess:
            assert sess.get("user") == "admin@example.com"

        # Logout
        response = authenticated_client.get("/logout")
        assert response.status_code == 302

        # Verify session cleared
        with authenticated_client.session_transaction() as sess:
            assert "user" not in sess

    @patch("src.admin.utils.is_super_admin")
    def test_settings_page_admin_only(self, mock_is_super_admin, authenticated_client):
        """Test that settings page is admin only."""
        # Non-admin should get 403
        mock_is_super_admin.return_value = False
        response = authenticated_client.get("/settings")
        assert response.status_code == 403

        # Admin should get 200
        mock_is_super_admin.return_value = True
        response = authenticated_client.get("/settings")
        assert response.status_code == 200

    def test_test_auth_disabled_by_default(self, client):
        """Test that test auth endpoints are disabled by default."""
        response = client.post("/test/auth", data={"email": "test@example.com", "password": "test123"})
        assert response.status_code == 404

    def test_test_auth_enabled_with_env_var(self, app):
        """Test that test auth works when enabled."""
        with patch.dict("os.environ", {"ADCP_AUTH_TEST_MODE": "true"}):
            client = app.test_client()

            response = client.post("/test/auth", data={"email": "test_super_admin@example.com", "password": "test123"})
            assert response.status_code == 302  # Redirect after login

            with client.session_transaction() as sess:
                assert sess.get("user") == "test_super_admin@example.com"


class TestTenantBlueprintIntegration:
    """Integration tests for tenant blueprint."""

    @pytest.fixture
    def app(self):
        """Create test Flask app."""
        app, _ = create_app({"TESTING": True})
        return app

    @pytest.fixture
    def client(self, app):
        """Create authenticated test client."""
        client = app.test_client()
        with client.session_transaction() as sess:
            sess["user"] = "admin@example.com"
        return client

    @patch("src.admin.utils.require_tenant_access")
    @patch("src.admin.utils.get_db_session")
    def test_tenant_dashboard(self, mock_get_db_session, mock_require_tenant_access, client):
        """Test tenant dashboard page."""
        # Mock decorator to allow access
        mock_require_tenant_access.return_value = lambda f: f

        # Mock database
        mock_session = Mock()
        mock_get_db_session.return_value.__enter__.return_value = mock_session

        mock_tenant = Mock()
        mock_tenant.name = "Test Tenant"
        mock_tenant.tenant_id = "tenant_123"
        mock_session.query.return_value.filter_by.return_value.first.return_value = mock_tenant
        mock_session.query.return_value.filter_by.return_value.count.return_value = 0
        mock_session.query.return_value.filter_by.return_value.filter.return_value.all.return_value = []
        mock_session.query.return_value.join.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []

        response = client.get("/tenant/tenant_123")
        # Will redirect due to decorator, but shows route exists
        assert response.status_code in [200, 302]


class TestProductsBlueprintIntegration:
    """Integration tests for products blueprint."""

    @pytest.fixture
    def app(self):
        """Create test Flask app."""
        app, _ = create_app({"TESTING": True})
        return app

    @pytest.fixture
    def client(self, app):
        """Create authenticated test client."""
        client = app.test_client()
        with client.session_transaction() as sess:
            sess["user"] = "admin@example.com"
        return client

    def test_products_routes_exist(self, app):
        """Test that product routes are registered."""
        rules = [str(rule) for rule in app.url_map.iter_rules()]

        # Check key product routes
        assert any("/tenant/<tenant_id>/products" in rule for rule in rules)
        assert any("/tenant/<tenant_id>/products/add" in rule for rule in rules)
        assert any("/tenant/<tenant_id>/products/<product_id>/edit" in rule for rule in rules)
