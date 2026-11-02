"""Unit tests for admin utilities."""

from unittest.mock import MagicMock, Mock, patch

from src.admin.utils import (
    get_tenant_config_from_db,
    parse_json_config,
    require_auth,
    validate_gam_network_response,
    validate_gam_user_response,
)


class TestConfigUtilities:
    """Test configuration utility functions."""

    def test_parse_json_config_valid(self):
        """Test parsing valid JSON config."""
        config_str = '{"key": "value", "number": 123}'
        result = parse_json_config(config_str)
        assert result == {"key": "value", "number": 123}

    def test_parse_json_config_empty(self):
        """Test parsing empty config."""
        assert parse_json_config("") == {}
        assert parse_json_config(None) == {}

    def test_parse_json_config_invalid(self):
        """Test parsing invalid JSON config."""
        assert parse_json_config("not json") == {}
        assert parse_json_config("{invalid}") == {}

    @patch("src.admin.utils.get_db_session")
    def test_get_tenant_config_from_db(self, mock_get_db_session):
        """Test getting tenant config from database."""
        # Setup mock
        mock_session = MagicMock()
        mock_get_db_session.return_value.__enter__.return_value = mock_session

        mock_tenant = Mock()
        # No admin_token: the column is gone (84a86e019) and get_tenant_config_from_db no
        # longer emits the key. A Mock() accepts any attribute, so setting it here and
        # asserting it below graded the test's own setup, not production.
        mock_tenant.slack_webhook_url = "https://slack.webhook"
        mock_tenant.adapter_config = '{"google_ad_manager": {"enabled": true}}'
        mock_tenant.max_daily_budget = 10000
        mock_tenant.enable_axe_signals = True
        mock_tenant.auto_approve_format_ids = ["display_300x250"]
        mock_tenant.human_review_required = False
        mock_tenant.policy_settings = '{"strict": false}'

        mock_session.query.return_value.filter_by.return_value.first.return_value = mock_tenant

        # Test
        config = get_tenant_config_from_db("tenant_123")

        assert config["slack_webhook_url"] == "https://slack.webhook"
        assert config["adapters"]["google_ad_manager"]["enabled"]
        assert config["features"]["max_daily_budget"] == 10000
        assert config["features"]["enable_axe_signals"] is True
        assert config["creative_engine"]["auto_approve_format_ids"] == ["display_300x250"]
        assert config["creative_engine"]["human_review_required"] is False
        assert not config["policy_settings"]["strict"]

    @patch("src.admin.utils.get_db_session")
    def test_get_tenant_config_not_found(self, mock_get_db_session):
        """Test getting config for non-existent tenant."""
        mock_session = MagicMock()
        mock_get_db_session.return_value.__enter__.return_value = mock_session
        mock_session.query.return_value.filter_by.return_value.first.return_value = None

        config = get_tenant_config_from_db("nonexistent")
        assert config == {}


class TestValidationFunctions:
    """Test validation utility functions."""

    def test_validate_gam_network_response_valid(self):
        """Test validating valid GAM network response."""
        network = {"networkCode": "123456", "displayName": "Test Network", "id": "789"}
        is_valid, error = validate_gam_network_response(network)
        assert is_valid
        assert error is None

    def test_validate_gam_network_response_missing_fields(self):
        """Test validating GAM network with missing fields."""
        network = {"networkCode": "123456"}
        is_valid, error = validate_gam_network_response(network)
        assert not is_valid
        assert "Missing required field" in error

    def test_validate_gam_network_response_invalid_types(self):
        """Test validating GAM network with invalid types."""
        network = {"networkCode": "not_a_number", "displayName": "Test Network", "id": "789"}
        is_valid, error = validate_gam_network_response(network)
        assert not is_valid
        assert "must be numeric" in error

    def test_validate_gam_user_response_valid(self):
        """Test validating valid GAM user response."""
        user = {"id": "12345"}
        is_valid, error = validate_gam_user_response(user)
        assert is_valid
        assert error is None

    def test_validate_gam_user_response_invalid(self):
        """Test validating invalid GAM user response."""
        # Missing ID
        user = {"name": "Test User"}
        is_valid, error = validate_gam_user_response(user)
        assert not is_valid
        assert "Missing user ID" in error

        # Non-numeric ID
        user = {"id": "not_numeric"}
        is_valid, error = validate_gam_user_response(user)
        assert not is_valid
        assert "must be numeric" in error


class TestAuthDecorators:
    """Test authentication decorator functions."""

    def test_require_auth_redirects_unauthenticated(self):
        """Test that require_auth redirects unauthenticated users."""
        from flask import Flask

        app = Flask(__name__)
        app.config["SECRET_KEY"] = "test"

        # Add a dummy login route so url_for("auth.login") works
        from flask import Blueprint

        auth_bp = Blueprint("auth", __name__)

        @auth_bp.route("/login")
        def login():
            return "Login page"

        app.register_blueprint(auth_bp)

        @app.route("/protected")
        @require_auth()
        def protected():
            return "Protected content"

        with app.test_client() as client:
            response = client.get("/protected")
            assert response.status_code == 302
            assert "/login" in response.location

    def test_require_auth_allows_authenticated(self):
        """Test that require_auth allows authenticated users."""
        from flask import Flask, g

        app = Flask(__name__)
        app.config["SECRET_KEY"] = "test"

        @app.route("/protected")
        @require_auth()
        def protected():
            return f"Protected content for {g.user}"

        with app.test_client() as client:
            with client.session_transaction() as sess:
                sess["user"] = "test@example.com"

            response = client.get("/protected")
            assert response.status_code == 200
            assert b"Protected content for test@example.com" in response.data

    @patch("src.admin.utils.is_super_admin")
    def test_require_auth_admin_only(self, mock_is_super_admin):
        """Test that admin_only flag works correctly."""
        from flask import Flask

        app = Flask(__name__)
        app.config["SECRET_KEY"] = "test"

        @app.route("/admin-only")
        @require_auth(admin_only=True)
        def admin_only():
            return "Admin content"

        with app.test_client() as client:
            with client.session_transaction() as sess:
                sess["user"] = "test@example.com"

            # Non-admin should get 403
            mock_is_super_admin.return_value = False
            response = client.get("/admin-only")
            assert response.status_code == 403

            # Admin should get 200
            mock_is_super_admin.return_value = True
            response = client.get("/admin-only")
            assert response.status_code == 200
            assert b"Admin content" in response.data
