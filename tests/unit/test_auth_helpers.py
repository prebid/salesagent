"""Unit tests for shared API key auth helper.

Tests the extracted auth helper that both tenant_management_api.py
and sync_api.py delegate to.

"""

from unittest.mock import MagicMock, patch

import pytest

# (Retired) TestRequirePrincipalId and TestRequireTenant graded two helpers in
# src/core/auth.py that every _impl called at entry: one returned identity.principal_id or
# raised AUTH_MISSING, the other did the same for identity.tenant. Both are deleted
# (see the note at the end of src/core/auth.py). They re-checked inside every protected
# tool what the resolver had already decided, which made them a SECOND minting site for
# the refusal, and the branch they guarded was unreachable. The type carries the decision
# now: a protected implementation takes ``ResolvedIdentity``, whose principal and tenant
# are not optional, and reads them directly; a public one takes ``PublicIdentity`` and
# branches on ``identity.principal``. The refusal itself is minted only by the resolver
# (ruff-boundary.toml bans the two auth errors elsewhere) and graded on the wire.
#
# What remains in this module is the operator API-key helper, which is unrelated and live.


class _FakeConfigStore:
    """Stands in for TenantManagementConfigRepository over a dict.

    The repository's own interface, so what these tests grade is what auth_helpers
    hands the store and what it asks back — not a mock's call log. Keyed the way the
    real rows are keyed, via the shared ``prefix_config_key``, so a change to that
    derivation breaks here too.
    """

    rows: dict[str, str] = {}

    def __init__(self, session=None):
        pass

    def api_key_digest(self, config_key):
        return self.rows.get(config_key)

    def api_key_prefix(self, config_key):
        from src.core.database.repositories.tenant_management_config import prefix_config_key

        return self.rows.get(prefix_config_key(config_key))

    def store_api_key(self, config_key, *, digest, prefix, description):
        from src.core.database.repositories.tenant_management_config import prefix_config_key

        self.rows[config_key] = digest
        self.rows[prefix_config_key(config_key)] = prefix


@pytest.fixture
def fake_config_store():
    """auth_helpers wired to an in-memory store, with its session context neutralized."""
    _FakeConfigStore.rows = {}
    with (
        patch("src.admin.auth_helpers.TenantManagementConfigRepository", _FakeConfigStore),
        patch("src.admin.auth_helpers.get_db_session") as mock_db,
    ):
        # A session whose only job is to be commit-able; the store above is the state.
        mock_db.return_value.__enter__ = MagicMock(return_value=MagicMock())
        mock_db.return_value.__exit__ = MagicMock(return_value=False)
        yield _FakeConfigStore.rows


class TestStoredApiKeyIsHashed:
    """The operator API key is minted once, stored as sha256, and matched by hash.

    Same treatment as a principal token (salesagent-3cs7o.7): the row used to hold the
    plaintext and hand it back on every call, so anyone who could read the table — or
    call the initializer — held a working credential.
    """

    def test_mint_stores_sha256_and_prefix_and_returns_the_plaintext_once(self, fake_config_store):
        import hashlib

        from src.admin.auth_helpers import mint_stored_api_key

        key = mint_stored_api_key("test_config_key", "a description")

        assert key.startswith("sk_")
        assert fake_config_store["test_config_key"] == hashlib.sha256(key.encode("utf-8")).hexdigest()
        assert fake_config_store["test_config_key_prefix"] == key[:12]
        # The plaintext exists in the return value and NOWHERE in the store. The prefix
        # row is a 12-character head, which is not the key and cannot be presented as one.
        assert key not in fake_config_store.values()

    def test_matching_hashes_the_presented_key_rather_than_comparing_a_stored_plaintext(self, fake_config_store):
        from src.admin.auth_helpers import api_key_matches, mint_stored_api_key

        key = mint_stored_api_key("test_config_key", "a description")

        assert api_key_matches(key, None, "test_config_key") is True
        assert api_key_matches("sk_not-the-key", None, "test_config_key") is False
        # The stored digest itself is not a credential: presenting it does not authenticate.
        assert api_key_matches(fake_config_store["test_config_key"], None, "test_config_key") is False

    def test_rotation_invalidates_the_previous_key(self, fake_config_store):
        from src.admin.auth_helpers import api_key_matches, mint_stored_api_key

        first = mint_stored_api_key("test_config_key", "a description")
        second = mint_stored_api_key("test_config_key", "a description")

        assert first != second
        assert api_key_matches(first, None, "test_config_key") is False
        assert api_key_matches(second, None, "test_config_key") is True

    def test_the_settings_value_wins_and_is_compared_as_the_plaintext_it_is(self, fake_config_store):
        from src.admin.auth_helpers import api_key_matches, mint_stored_api_key

        stored = mint_stored_api_key("test_config_key", "a description")

        # An operator-supplied deployment secret is a plaintext this process was handed,
        # not a row this application minted, so it is compared directly — and it wins.
        assert api_key_matches("env-key", "env-key", "test_config_key") is True
        assert api_key_matches(stored, "env-key", "test_config_key") is False

    def test_configured_check_answers_existence_without_recovering_a_key(self, fake_config_store):
        from src.admin.auth_helpers import api_key_is_configured, api_key_prefix, mint_stored_api_key

        assert api_key_is_configured(None, "test_config_key") is False
        assert api_key_prefix("test_config_key") is None

        key = mint_stored_api_key("test_config_key", "a description")

        assert api_key_is_configured(None, "test_config_key") is True
        assert api_key_prefix("test_config_key") == key[:12]


class TestRequireApiKeyAuth:
    """Test the decorator factory.

    ``require_api_key_auth`` takes ``setting=``: the name of the ``AuthSettings`` field
    carrying the key, read per request off the settings object. It took ``env_var=`` and
    read ``os.environ`` directly until the environment collapsed to one reader
    (``src/core/config.py``), which is why these cases set the SETTING rather than a
    variable -- the variable an operator sets is that field's name upper-cased, and the
    503 body still names it.
    """

    def test_missing_header_returns_401(self):
        """Request without the auth header returns 401."""
        from src.admin.auth_helpers import require_api_key_auth

        decorator = require_api_key_auth(setting="sync_api_key", config_key="test_key", header="X-Test-Key")

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
        """When no key is configured anywhere, returns 503 naming the variable."""
        from src.admin.auth_helpers import require_api_key_auth
        from src.core.config import get_settings

        decorator = require_api_key_auth(setting="sync_api_key", config_key="nonexistent", header="X-Test-Key")

        @decorator
        def protected_view():
            return {"data": "secret"}, 200

        from flask import Flask

        app = Flask(__name__)
        app.add_url_rule("/test", view_func=protected_view)

        with (
            patch.object(get_settings().auth, "sync_api_key", None),
            patch("src.admin.auth_helpers.get_db_session") as mock_db,
        ):
            mock_session = MagicMock()
            mock_session.scalars.return_value.first.return_value = None
            mock_db.return_value.__enter__ = MagicMock(return_value=mock_session)
            mock_db.return_value.__exit__ = MagicMock(return_value=False)

            with app.test_client() as client:
                resp = client.get("/test", headers={"X-Test-Key": "any-key"})
                assert resp.status_code == 503
                assert "SYNC_API_KEY" in resp.get_json()["error"]

    def test_valid_key_passes_through(self):
        """Correct key allows request through."""
        from src.admin.auth_helpers import require_api_key_auth
        from src.core.config import get_settings

        decorator = require_api_key_auth(setting="sync_api_key", config_key="test_key", header="X-Test-Key")

        @decorator
        def protected_view():
            return {"data": "secret"}, 200

        from flask import Flask

        app = Flask(__name__)
        app.add_url_rule("/test", view_func=protected_view)

        with patch.object(get_settings().auth, "sync_api_key", "correct-key"):
            with app.test_client() as client:
                resp = client.get("/test", headers={"X-Test-Key": "correct-key"})
                assert resp.status_code == 200

    def test_wrong_key_returns_401(self):
        """Incorrect key returns 401."""
        from src.admin.auth_helpers import require_api_key_auth
        from src.core.config import get_settings

        decorator = require_api_key_auth(setting="sync_api_key", config_key="test_key", header="X-Test-Key")

        @decorator
        def protected_view():
            return {"data": "secret"}, 200

        from flask import Flask

        app = Flask(__name__)
        app.add_url_rule("/test", view_func=protected_view)

        with patch.object(get_settings().auth, "sync_api_key", "correct-key"):
            with app.test_client() as client:
                resp = client.get("/test", headers={"X-Test-Key": "wrong-key"})
                assert resp.status_code == 401
