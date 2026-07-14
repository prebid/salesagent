"""Unit tests for shared API key auth helper.

Tests the extracted auth helper that both tenant_management_api.py
and sync_api.py delegate to.

Fixes: salesagent-p01
"""

from unittest.mock import MagicMock, patch

import pytest

from src.core.exceptions import AdCPAuthenticationError
from tests.factories import PrincipalFactory


class TestRequirePrincipalId:
    """The require_principal_id entry guard (gh-1307 / salesagent-b1h).

    Single source of truth for the "identity has no principal_id" guard that
    every _impl runs at entry. Returns the validated principal_id or raises
    AdCPAuthenticationError with one canonical message.
    """

    CANONICAL_MESSAGE = (
        "Authentication required: Principal ID not found in identity. Provide a valid x-adcp-auth token."
    )

    def test_returns_principal_id_when_present(self):
        from src.core.auth import require_principal_id

        identity = PrincipalFactory.make_identity(principal_id="p1", tenant_id="t1")

        assert require_principal_id(identity) == "p1"

    def test_raises_canonical_error_when_identity_is_none(self):
        from src.core.auth import require_principal_id

        with pytest.raises(AdCPAuthenticationError) as exc_info:
            require_principal_id(None)

        assert exc_info.value.message == self.CANONICAL_MESSAGE

    def test_raises_canonical_error_when_principal_id_is_none(self):
        from src.core.auth import require_principal_id

        identity = PrincipalFactory.make_identity(principal_id=None, tenant_id="t1")

        with pytest.raises(AdCPAuthenticationError) as exc_info:
            require_principal_id(identity)

        assert exc_info.value.message == self.CANONICAL_MESSAGE

    def test_raises_canonical_error_when_principal_id_is_empty(self):
        from src.core.auth import require_principal_id

        identity = PrincipalFactory.make_identity(principal_id="", tenant_id="t1")

        with pytest.raises(AdCPAuthenticationError) as exc_info:
            require_principal_id(identity)

        assert exc_info.value.message == self.CANONICAL_MESSAGE

    def test_preserves_context_kwarg_onto_the_exception(self):
        from src.core.auth import require_principal_id

        sentinel_context = {"request_id": "req-123"}

        with pytest.raises(AdCPAuthenticationError) as exc_info:
            require_principal_id(None, context=sentinel_context)

        assert exc_info.value.context == sentinel_context


class TestRequireTenant:
    """The require_tenant entry guard (gh-1307 / salesagent-fum).

    Single source of truth for the "no tenant context available" guard — the
    most-repeated _impl prologue. Returns identity.tenant or raises
    AdCPAuthenticationError with one canonical, actionable message.
    """

    CANONICAL_MESSAGE = "No tenant context available. Check x-adcp-auth token and host headers."

    def test_returns_tenant_when_present(self):
        from src.core.auth import require_tenant

        identity = PrincipalFactory.make_identity(principal_id="p1", tenant_id="t1")

        assert require_tenant(identity) == identity.tenant

    def test_raises_canonical_error_when_identity_is_none(self):
        from src.core.auth import require_tenant

        with pytest.raises(AdCPAuthenticationError) as exc_info:
            require_tenant(None)

        assert exc_info.value.message == self.CANONICAL_MESSAGE

    def test_raises_canonical_error_when_tenant_is_none(self):
        from src.core.auth import require_tenant

        identity = PrincipalFactory.make_identity(principal_id="p1", tenant_id="t1", tenant=None)

        with pytest.raises(AdCPAuthenticationError) as exc_info:
            require_tenant(identity)

        assert exc_info.value.message == self.CANONICAL_MESSAGE

    def test_preserves_context_kwarg_onto_the_exception(self):
        from src.core.auth import require_tenant

        sentinel_context = {"request_id": "req-456"}

        with pytest.raises(AdCPAuthenticationError) as exc_info:
            require_tenant(None, context=sentinel_context)

        assert exc_info.value.context == sentinel_context


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


class TestResolvePrincipalOrRaise:
    """resolve_principal_or_raise (gh-1307) — the shared "look up principal, fail auth if absent" guard.

    Collapses the identical lookup the create/update/delivery media-buy tools
    share. Returns the Principal or raises AdCPAuthenticationError
    (AUTH_REQUIRED), echoing the request context into the error envelope.
    """

    def test_returns_principal_when_found(self):
        from src.core.auth import resolve_principal_or_raise

        principal = MagicMock(principal_id="p1")
        with patch("src.core.auth.get_principal_object", return_value=principal):
            assert resolve_principal_or_raise("p1", tenant_id="t1") is principal

    def test_missing_principal_raises_authentication_error(self):
        from src.core.auth import resolve_principal_or_raise

        with patch("src.core.auth.get_principal_object", return_value=None):
            with pytest.raises(AdCPAuthenticationError, match="ghost") as exc_info:
                resolve_principal_or_raise("ghost", tenant_id="t1")

        assert exc_info.value.error_code == "AUTH_REQUIRED"

    def test_echoes_context_into_error(self):
        from src.core.auth import resolve_principal_or_raise

        ctx = {"request_id": "req-123"}
        with patch("src.core.auth.get_principal_object", return_value=None):
            with pytest.raises(AdCPAuthenticationError) as exc_info:
                resolve_principal_or_raise("ghost", tenant_id="t1", context=ctx)

        assert exc_info.value.context == ctx
