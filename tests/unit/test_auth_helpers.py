"""Unit tests for shared API key auth helper.

Tests the extracted auth helper that both tenant_management_api.py
and sync_api.py delegate to.

Fixes: salesagent-p01
"""

from unittest.mock import MagicMock, patch


class TestGetApiKeyFromConfig:
    """Test the key retrieval function (env var → DB fallback)."""

    def test_env_var_takes_priority_over_db(self):
        """When both env var and DB have keys, env var wins."""
        from src.admin.auth_helpers import get_api_key_from_config

        with patch.dict("os.environ", {"TEST_API_KEY": "env-key"}):
            with patch("src.admin.auth_helpers.get_db_session") as mock_db:
                mock_session = MagicMock()
                mock_config = MagicMock()
                mock_config.config_value = "db-key"
                mock_session.scalars.return_value.first.return_value = mock_config
                mock_db.return_value.__enter__ = MagicMock(return_value=mock_session)
                mock_db.return_value.__exit__ = MagicMock(return_value=False)

                result = get_api_key_from_config("TEST_API_KEY", "test_config_key")
                assert result == "env-key"

    def test_falls_back_to_db_when_no_env_var(self):
        """When env var not set, falls back to DB lookup."""
        from src.admin.auth_helpers import get_api_key_from_config

        with patch.dict("os.environ", {}, clear=False):
            # Ensure TEST_API_KEY is not in env
            import os

            os.environ.pop("TEST_API_KEY", None)

            with patch("src.admin.auth_helpers.get_db_session") as mock_db:
                mock_session = MagicMock()
                mock_config = MagicMock()
                mock_config.config_value = "db-key"
                mock_session.scalars.return_value.first.return_value = mock_config
                mock_db.return_value.__enter__ = MagicMock(return_value=mock_session)
                mock_db.return_value.__exit__ = MagicMock(return_value=False)

                result = get_api_key_from_config("TEST_API_KEY", "test_config_key")
                assert result == "db-key"

    def test_returns_none_when_neither_configured(self):
        """When neither env var nor DB has a key, returns None."""
        from src.admin.auth_helpers import get_api_key_from_config

        with patch.dict("os.environ", {}, clear=False):
            import os

            os.environ.pop("TEST_API_KEY", None)

            with patch("src.admin.auth_helpers.get_db_session") as mock_db:
                mock_session = MagicMock()
                mock_session.scalars.return_value.first.return_value = None
                mock_db.return_value.__enter__ = MagicMock(return_value=mock_session)
                mock_db.return_value.__exit__ = MagicMock(return_value=False)

                result = get_api_key_from_config("TEST_API_KEY", "test_config_key")
                assert result is None


class TestRequireApiKeyAuth:
    """Test the decorator factory."""

    def test_missing_header_returns_401(self):
        """Request without the auth header returns 401."""
        from src.admin.auth_helpers import require_api_key_auth

        decorator = require_api_key_auth(env_var="TEST_KEY", config_key="test_key", header="X-Test-Key")

        @decorator
        def protected_view():
            return {"data": "secret"}, 200

        from flask import Flask

        app = Flask(__name__)
        app.add_url_rule("/test", view_func=protected_view)
        with app.test_client() as client:
            resp = client.get("/test")
            assert resp.status_code == 401

    def test_unconfigured_key_returns_503(self):
        """When no key is configured anywhere, returns 503."""
        from src.admin.auth_helpers import require_api_key_auth

        decorator = require_api_key_auth(env_var="UNCONFIGURED_KEY_XYZ", config_key="nonexistent", header="X-Test-Key")

        @decorator
        def protected_view():
            return {"data": "secret"}, 200

        from flask import Flask

        app = Flask(__name__)
        app.add_url_rule("/test", view_func=protected_view)

        with patch("src.admin.auth_helpers.get_db_session") as mock_db:
            mock_session = MagicMock()
            mock_session.scalars.return_value.first.return_value = None
            mock_db.return_value.__enter__ = MagicMock(return_value=mock_session)
            mock_db.return_value.__exit__ = MagicMock(return_value=False)

            with app.test_client() as client:
                resp = client.get("/test", headers={"X-Test-Key": "any-key"})
                assert resp.status_code == 503

    def test_valid_key_passes_through(self):
        """Correct key allows request through."""
        from src.admin.auth_helpers import require_api_key_auth

        decorator = require_api_key_auth(env_var="TEST_VALID_KEY", config_key="test_key", header="X-Test-Key")

        @decorator
        def protected_view():
            return {"data": "secret"}, 200

        from flask import Flask

        app = Flask(__name__)
        app.add_url_rule("/test", view_func=protected_view)

        with patch.dict("os.environ", {"TEST_VALID_KEY": "correct-key"}):
            with app.test_client() as client:
                resp = client.get("/test", headers={"X-Test-Key": "correct-key"})
                assert resp.status_code == 200

    def test_wrong_key_returns_401(self):
        """Incorrect key returns 401."""
        from src.admin.auth_helpers import require_api_key_auth

        decorator = require_api_key_auth(env_var="TEST_WRONG_KEY", config_key="test_key", header="X-Test-Key")

        @decorator
        def protected_view():
            return {"data": "secret"}, 200

        from flask import Flask

        app = Flask(__name__)
        app.add_url_rule("/test", view_func=protected_view)

        with patch.dict("os.environ", {"TEST_WRONG_KEY": "correct-key"}):
            with app.test_client() as client:
                resp = client.get("/test", headers={"X-Test-Key": "wrong-key"})
                assert resp.status_code == 401
