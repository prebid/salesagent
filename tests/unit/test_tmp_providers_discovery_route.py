"""Unit tests for the FastAPI TMP provider discovery route and TMPProvider model.

Tests the endpoint:
    GET /tenant/{tenant_id}/tmp-providers/discovery

This is the FastAPI route in src/routes/tmp_providers.py — the canonical
machine-to-machine discovery endpoint polled by the TMP Router every 30 s.

Covers:
- Returns active + draining providers via repository.list_syncable()
- Returns 404 for unknown tenant
- Returns empty list when tenant has no active providers
- Response shape matches TMP Router contract
- Providers ordered by priority ASC, name ASC
- Handles legacy rows with null countries/uid_types
- Fail-closed auth: unset/empty TMP_DISCOVERY_API_KEYS → 503
- Explicit opt-out: TMP_DISCOVERY_API_KEYS=OPEN disables auth
- uow.tenant_config is None → 500 (not an assert)
- TMPProvider.to_dict() serializes both conditional paths correctly
"""

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm.exc import DetachedInstanceError

from src.core.database.models import TMPProvider
from tests.helpers.envelope_assertions import assert_envelope_shape
from tests.unit._tmp_helpers import _make_provider, _make_tmp_uow


def _make_tenant(tenant_id="si-host"):
    t = MagicMock()
    t.tenant_id = tenant_id
    t.name = "SI Host Tenant"
    return t


@pytest.fixture
def client():
    """Create a FastAPI TestClient with the tmp_providers router and AdCPError handler mounted.

    The handler mirrors the production handler in src/app.py exactly:
    ``build_two_layer_error_envelope(exc)`` is returned at the top level (no
    ``"detail"`` wrapper).  Tests assert via ``assert_envelope_shape()`` so
    that deleting this handler from the production app would break the tests.
    """
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse

    from src.core.exceptions import AdCPError, build_two_layer_error_envelope
    from src.routes.tmp_providers import router

    app = FastAPI()
    app.include_router(router)

    @app.exception_handler(AdCPError)
    async def adcp_error_handler(request: Request, exc: AdCPError) -> JSONResponse:
        # Matches src/app.py adcp_error_handler exactly — envelope at top level.
        return JSONResponse(
            status_code=exc.status_code,
            content=build_two_layer_error_envelope(exc),
        )

    return TestClient(app, raise_server_exceptions=False)


class TestDiscoveryReturnsActiveProviders:
    """GET /tenant/{tenant_id}/tmp-providers/discovery returns active + draining providers."""

    def test_returns_two_active_providers(self, client):
        """Two active providers are returned in the response via repository.list_syncable()."""
        tenant = _make_tenant()
        providers = [
            _make_provider(provider_id="uuid-1", name="Provider A", priority=0, countries=["US"]),
            _make_provider(provider_id="uuid-2", name="Provider B", priority=1, uid_types=["uid2"]),
        ]

        mock_tmp_uow_cls = _make_tmp_uow(providers, tenant=tenant)

        with patch("src.routes.tmp_providers.TMPProviderUoW", mock_tmp_uow_cls):
            with patch.dict("os.environ", {"TMP_DISCOVERY_API_KEYS": "OPEN"}):
                response = client.get("/tenant/si-host/tmp-providers/discovery")

        assert response.status_code == 200
        data = response.json()
        assert data["tenant_id"] == "si-host"
        assert len(data["providers"]) == 2
        assert data["providers"][0]["provider_id"] == "uuid-1"
        assert data["providers"][0]["countries"] == ["US"]
        assert data["providers"][1]["provider_id"] == "uuid-2"
        assert data["providers"][1]["uid_types"] == ["uid2"]
        mock_tmp_uow_cls.return_value.__enter__.return_value.tmp_providers.list_syncable.assert_called_once_with()

    def test_includes_draining_providers(self, client):
        """Draining providers are included (router stops new requests but in-flight complete)."""
        tenant = _make_tenant()
        providers = [
            _make_provider(provider_id="uuid-1", status="active"),
            _make_provider(provider_id="uuid-2", status="draining"),
        ]

        mock_tmp_uow_cls = _make_tmp_uow(providers, tenant=tenant)

        with patch("src.routes.tmp_providers.TMPProviderUoW", mock_tmp_uow_cls):
            with patch.dict("os.environ", {"TMP_DISCOVERY_API_KEYS": "OPEN"}):
                response = client.get("/tenant/si-host/tmp-providers/discovery")

        assert response.status_code == 200
        data = response.json()
        assert len(data["providers"]) == 2
        statuses = {p["status"] for p in data["providers"]}
        assert statuses == {"active", "draining"}


class TestDiscoveryTenantNotFound:
    """GET /tenant/{tenant_id}/tmp-providers/discovery returns 404 for unknown tenant."""

    def test_returns_404_for_unknown_tenant(self, client):
        """Unknown tenant_id returns 404 so the router can distinguish from 'no providers'."""
        mock_tmp_uow_cls = _make_tmp_uow([], tenant=None)

        with patch("src.routes.tmp_providers.TMPProviderUoW", mock_tmp_uow_cls):
            with patch.dict("os.environ", {"TMP_DISCOVERY_API_KEYS": "OPEN"}):
                response = client.get("/tenant/nonexistent/tmp-providers/discovery")

        assert response.status_code == 404
        envelope = response.json()
        assert_envelope_shape(envelope, "ACCOUNT_NOT_FOUND", recovery="terminal", message_substr="not found")
        assert envelope["errors"][0]["details"]["suggestion"] == "Provide a valid tenant ID."


class TestDiscoveryEmptyProviders:
    """GET /tenant/{tenant_id}/tmp-providers/discovery returns empty list when no providers."""

    def test_returns_empty_providers_list(self, client):
        """Valid tenant with no active providers returns empty providers array."""
        tenant = _make_tenant()

        mock_tmp_uow_cls = _make_tmp_uow([], tenant=tenant)

        with patch("src.routes.tmp_providers.TMPProviderUoW", mock_tmp_uow_cls):
            with patch.dict("os.environ", {"TMP_DISCOVERY_API_KEYS": "OPEN"}):
                response = client.get("/tenant/si-host/tmp-providers/discovery")

        assert response.status_code == 200
        data = response.json()
        assert data["tenant_id"] == "si-host"
        assert data["providers"] == []


class TestDiscoveryResponseShape:
    """Response shape matches the TMP Router contract."""

    def test_response_contains_all_required_fields(self, client):
        """Each provider entry contains all fields the TMP Router expects."""
        tenant = _make_tenant()
        providers = [
            _make_provider(
                countries=["US", "GB"],
                uid_types=["publisher_first_party", "uid2"],
            ),
        ]

        mock_tmp_uow_cls = _make_tmp_uow(providers, tenant=tenant)

        with patch("src.routes.tmp_providers.TMPProviderUoW", mock_tmp_uow_cls):
            with patch.dict("os.environ", {"TMP_DISCOVERY_API_KEYS": "OPEN"}):
                response = client.get("/tenant/si-host/tmp-providers/discovery")

        assert response.status_code == 200
        entry = response.json()["providers"][0]

        required_fields = {
            "provider_id",
            "name",
            "endpoint",
            "context_match",
            "identity_match",
            "countries",
            "uid_types",
            "timeout_ms",
            "priority",
            "status",
        }
        assert required_fields.issubset(set(entry.keys()))

    def test_null_countries_uid_types_for_legacy_rows(self, client):
        """Legacy rows with null countries/uid_types return null (router treats as 'all')."""
        tenant = _make_tenant()
        providers = [
            _make_provider(countries=None, uid_types=None),
        ]

        mock_tmp_uow_cls = _make_tmp_uow(providers, tenant=tenant)

        with patch("src.routes.tmp_providers.TMPProviderUoW", mock_tmp_uow_cls):
            with patch.dict("os.environ", {"TMP_DISCOVERY_API_KEYS": "OPEN"}):
                response = client.get("/tenant/si-host/tmp-providers/discovery")

        assert response.status_code == 200
        entry = response.json()["providers"][0]
        assert entry["countries"] is None
        assert entry["uid_types"] is None


class TestDiscoveryOrdering:
    """Providers are ordered by priority ASC, name ASC."""

    def test_providers_ordered_by_priority_then_name(self, client):
        """The repository returns providers in priority ASC, name ASC order."""
        tenant = _make_tenant()
        # Simulate DB returning in correct order (priority 0 before 1, alpha within same priority)
        providers = [
            _make_provider(provider_id="uuid-a", name="Alpha", priority=0),
            _make_provider(provider_id="uuid-b", name="Beta", priority=0),
            _make_provider(provider_id="uuid-c", name="Gamma", priority=1),
        ]

        mock_tmp_uow_cls = _make_tmp_uow(providers, tenant=tenant)

        with patch("src.routes.tmp_providers.TMPProviderUoW", mock_tmp_uow_cls):
            with patch.dict("os.environ", {"TMP_DISCOVERY_API_KEYS": "OPEN"}):
                response = client.get("/tenant/si-host/tmp-providers/discovery")

        assert response.status_code == 200
        names = [p["name"] for p in response.json()["providers"]]
        assert names == ["Alpha", "Beta", "Gamma"]


# ---------------------------------------------------------------------------
# TMP_DISCOVERY_API_KEYS gating tests
# ---------------------------------------------------------------------------


class TestDiscoveryApiKeyAuth:
    """GET /tenant/{tenant_id}/tmp-providers/discovery enforces TMP_DISCOVERY_API_KEYS."""

    def test_returns_500_when_tmp_discovery_api_keys_not_set(self, client):
        """When TMP_DISCOVERY_API_KEYS is unset the endpoint returns 500 (fail-closed, operator must act).

        AdCPConfigurationError (500, correctable) is the right error here: the operator
        has to configure the env var; the buyer cannot recover this themselves.

        NOTE: recovery is currently "terminal" (AdCPConfigurationError class default) because
        the global default flip (terminal → correctable) is split into its own PR #1550.
        Once #1550 merges and this branch rebases, update to recovery="correctable".
        """
        import os

        with patch.dict("os.environ", {}, clear=False):
            os.environ.pop("TMP_DISCOVERY_API_KEYS", None)
            response = client.get("/tenant/si-host/tmp-providers/discovery")

        assert response.status_code == 500
        # CONFIGURATION_ERROR maps to SERVICE_UNAVAILABLE on wire.
        # TODO(#1550): update to recovery="correctable" once PR #1550 merges.
        assert_envelope_shape(response.json(), "SERVICE_UNAVAILABLE", recovery="terminal")

    def test_returns_500_when_tmp_discovery_api_keys_is_empty_string(self, client):
        """When TMP_DISCOVERY_API_KEYS is set to empty string the endpoint returns 500 (fail-closed).

        Same as unset: AdCPConfigurationError (500) — operator must act.

        NOTE: recovery is currently "terminal" (AdCPConfigurationError class default) because
        the global default flip (terminal → correctable) is split into its own PR #1550.
        Once #1550 merges and this branch rebases, update to recovery="correctable".
        """
        with patch.dict("os.environ", {"TMP_DISCOVERY_API_KEYS": ""}):
            response = client.get("/tenant/si-host/tmp-providers/discovery")

        assert response.status_code == 500
        # CONFIGURATION_ERROR maps to SERVICE_UNAVAILABLE on wire.
        # TODO(#1550): update to recovery="correctable" once PR #1550 merges.
        assert_envelope_shape(response.json(), "SERVICE_UNAVAILABLE", recovery="terminal")

    def test_open_when_tmp_discovery_api_keys_is_open(self, client):
        """When TMP_DISCOVERY_API_KEYS=OPEN the endpoint is accessible without a key."""
        tenant = _make_tenant()
        mock_tmp_uow_cls = _make_tmp_uow([], tenant=tenant)

        with patch("src.routes.tmp_providers.TMPProviderUoW", mock_tmp_uow_cls):
            with patch.dict("os.environ", {"TMP_DISCOVERY_API_KEYS": "OPEN"}):
                response = client.get("/tenant/si-host/tmp-providers/discovery")

        assert response.status_code == 200

    def test_open_mode_is_case_insensitive(self, client):
        """TMP_DISCOVERY_API_KEYS=open (lowercase) also disables auth."""
        tenant = _make_tenant()
        mock_tmp_uow_cls = _make_tmp_uow([], tenant=tenant)

        with patch("src.routes.tmp_providers.TMPProviderUoW", mock_tmp_uow_cls):
            with patch.dict("os.environ", {"TMP_DISCOVERY_API_KEYS": "open"}):
                response = client.get("/tenant/si-host/tmp-providers/discovery")

        assert response.status_code == 200

    def test_returns_401_when_no_key_provided_and_keys_configured(self, client):
        """When TMP_DISCOVERY_API_KEYS is set and no key is sent, returns 401 with suggestion."""
        with patch.dict("os.environ", {"TMP_DISCOVERY_API_KEYS": "secret-key-1,secret-key-2"}):
            response = client.get("/tenant/si-host/tmp-providers/discovery")

        assert response.status_code == 401
        envelope = response.json()
        assert_envelope_shape(envelope, "AUTH_TOKEN_INVALID", recovery="terminal")
        assert (
            envelope["errors"][0]["details"]["suggestion"]
            == "Provide a valid API key via x-adcp-auth, X-API-Key, or Authorization: Bearer <key>."
        )

    def test_returns_401_when_wrong_key_provided(self, client):
        """When TMP_DISCOVERY_API_KEYS is set and a wrong key is sent, returns 401 with suggestion."""
        with patch.dict("os.environ", {"TMP_DISCOVERY_API_KEYS": "correct-key"}):
            response = client.get(
                "/tenant/si-host/tmp-providers/discovery",
                headers={"x-adcp-auth": "wrong-key"},
            )

        assert response.status_code == 401
        envelope = response.json()
        assert_envelope_shape(envelope, "AUTH_TOKEN_INVALID", recovery="terminal")
        assert (
            envelope["errors"][0]["details"]["suggestion"]
            == "Provide a valid API key via x-adcp-auth, X-API-Key, or Authorization: Bearer <key>."
        )

    def test_accepts_valid_key_via_x_adcp_auth_header(self, client):
        """Valid key in x-adcp-auth header is accepted."""
        tenant = _make_tenant()
        mock_tmp_uow_cls = _make_tmp_uow([], tenant=tenant)

        with patch("src.routes.tmp_providers.TMPProviderUoW", mock_tmp_uow_cls):
            with patch.dict("os.environ", {"TMP_DISCOVERY_API_KEYS": "valid-key"}):
                response = client.get(
                    "/tenant/si-host/tmp-providers/discovery",
                    headers={"x-adcp-auth": "valid-key"},
                )

        assert response.status_code == 200

    def test_accepts_valid_key_via_x_api_key_header(self, client):
        """Valid key in X-API-Key header is accepted."""
        tenant = _make_tenant()
        mock_tmp_uow_cls = _make_tmp_uow([], tenant=tenant)

        with patch("src.routes.tmp_providers.TMPProviderUoW", mock_tmp_uow_cls):
            with patch.dict("os.environ", {"TMP_DISCOVERY_API_KEYS": "valid-key"}):
                response = client.get(
                    "/tenant/si-host/tmp-providers/discovery",
                    headers={"X-API-Key": "valid-key"},
                )

        assert response.status_code == 200

    def test_accepts_valid_key_via_authorization_bearer_header(self, client):
        """Valid key in Authorization: Bearer header is accepted."""
        tenant = _make_tenant()
        mock_tmp_uow_cls = _make_tmp_uow([], tenant=tenant)

        with patch("src.routes.tmp_providers.TMPProviderUoW", mock_tmp_uow_cls):
            with patch.dict("os.environ", {"TMP_DISCOVERY_API_KEYS": "valid-key"}):
                response = client.get(
                    "/tenant/si-host/tmp-providers/discovery",
                    headers={"Authorization": "Bearer valid-key"},
                )

        assert response.status_code == 200

    def test_accepts_one_of_multiple_configured_keys(self, client):
        """Any key from the comma-separated TMP_DISCOVERY_API_KEYS list is accepted."""
        tenant = _make_tenant()
        mock_tmp_uow_cls = _make_tmp_uow([], tenant=tenant)

        with patch("src.routes.tmp_providers.TMPProviderUoW", mock_tmp_uow_cls):
            with patch.dict("os.environ", {"TMP_DISCOVERY_API_KEYS": "key-a,key-b,key-c"}):
                response = client.get(
                    "/tenant/si-host/tmp-providers/discovery",
                    headers={"x-adcp-auth": "key-b"},
                )

        assert response.status_code == 200


# ---------------------------------------------------------------------------
# uow.tenant_config is None guard (replaces the old assert)
# ---------------------------------------------------------------------------


class TestDiscoveryTenantConfigUnavailable:
    """GET /tenant/{tenant_id}/tmp-providers/discovery returns 500 when tenant_config repo is None."""

    def test_returns_503_when_tenant_config_is_none(self, client):
        """If TMPProviderUoW yields uow.tenant_config=None the endpoint returns 503 (service unavailable).

        AdCPServiceUnavailableError (503, transient) is the right error here: the
        repository layer is temporarily unavailable; the buyer should retry.
        """
        mock_uow = MagicMock()
        mock_uow.tenant_config = None  # simulate broken UoW
        mock_uow.tmp_providers = MagicMock()  # unused but present for safety
        mock_uow_cls = MagicMock()
        mock_uow_cls.return_value.__enter__ = MagicMock(return_value=mock_uow)
        mock_uow_cls.return_value.__exit__ = MagicMock(return_value=False)

        with patch("src.routes.tmp_providers.TMPProviderUoW", mock_uow_cls):
            with patch.dict("os.environ", {"TMP_DISCOVERY_API_KEYS": "OPEN"}):
                response = client.get("/tenant/si-host/tmp-providers/discovery")

        assert response.status_code == 503
        # AdCPServiceUnavailableError: recovery=transient (buyer should retry)
        assert_envelope_shape(response.json(), "SERVICE_UNAVAILABLE", recovery="transient")


# ---------------------------------------------------------------------------
# Single-transaction + no-DetachedInstance regression tests
# ---------------------------------------------------------------------------


class TestDiscoverySingleTransactionAndNoDetachedInstance:
    """Regression tests proving the route uses ONE UoW and calls to_dict() inside it.

    Round 11 review fix: the route was refactored from two separate UoW blocks
    (TenantConfigUoW then TMPProviderUoW) to a single TMPProviderUoW block.
    These tests prove:
    1. TMPProviderUoW is constructed exactly once (not twice).
    2. provider.to_dict() is called BEFORE the UoW exits — calling it after
       would raise DetachedInstanceError under real SQLAlchemy
       (expire_on_commit=True is the default).
    """

    class _DetachAfterCloseProvider:
        """Fake provider whose to_dict() raises DetachedInstanceError once the UoW has closed."""

        def __init__(self, closed_flag: list[bool]):
            self._closed_flag = closed_flag

        def _check(self):
            if self._closed_flag[0]:
                raise DetachedInstanceError("Instance is not bound to a Session; attribute access failed")

        def to_dict(self, *, include_conditional: bool = True) -> dict:
            self._check()
            return {
                "provider_id": "fake-uuid",
                "name": "Fake Provider",
                "endpoint": "http://fake:3000",
                "context_match": True,
                "identity_match": True,
                "countries": None,
                "uid_types": None,
                "properties": None,
                "timeout_ms": 200,
                "priority": 0,
                "status": "active",
            }

    def test_tmp_provider_uow_constructed_exactly_once(self, client):
        """TMPProviderUoW is instantiated exactly once — not twice (no separate TenantConfigUoW)."""
        mock_tmp_uow_cls = _make_tmp_uow([], tenant=_make_tenant())

        with patch("src.routes.tmp_providers.TMPProviderUoW", mock_tmp_uow_cls):
            with patch.dict("os.environ", {"TMP_DISCOVERY_API_KEYS": "OPEN"}):
                response = client.get("/tenant/si-host/tmp-providers/discovery")

        assert response.status_code == 200
        # The class must have been called (constructed) exactly once.
        mock_tmp_uow_cls.assert_called_once_with("si-host")

    def test_to_dict_called_before_uow_exits(self, client):
        """provider.to_dict() is called inside the UoW block, not after it closes.

        Uses a fake provider whose to_dict() raises DetachedInstanceError once
        the UoW __exit__ sets a closed_flag. If the route calls to_dict() after
        the block exits, the request would 500; if it calls it inside, it succeeds.
        """
        closed_flag = [False]
        provider = self._DetachAfterCloseProvider(closed_flag)

        mock_uow = MagicMock()
        mock_uow.tmp_providers = MagicMock()
        mock_uow.tmp_providers.list_syncable.return_value = [provider]
        mock_uow.tenant_config = MagicMock()
        mock_uow.tenant_config.get_tenant.return_value = _make_tenant()

        def _mark_closed(*_args):
            closed_flag[0] = True
            return False

        mock_uow_cls = MagicMock()
        mock_uow_cls.return_value.__enter__ = MagicMock(return_value=mock_uow)
        mock_uow_cls.return_value.__exit__ = MagicMock(side_effect=_mark_closed)

        with patch("src.routes.tmp_providers.TMPProviderUoW", mock_uow_cls):
            with patch.dict("os.environ", {"TMP_DISCOVERY_API_KEYS": "OPEN"}):
                # Would raise DetachedInstanceError (→ 500) if to_dict() ran after __exit__.
                response = client.get("/tenant/si-host/tmp-providers/discovery")

        assert response.status_code == 200
        data = response.json()
        assert len(data["providers"]) == 1
        assert data["providers"][0]["provider_id"] == "fake-uuid"


# ---------------------------------------------------------------------------
# TMPProvider.to_dict() unit tests (no DB required)
# ---------------------------------------------------------------------------


class TestTMPProviderToDict:
    """TMPProvider.to_dict() serializes both conditional paths correctly.

    These tests use real TMPProvider instances (no DB session) to ensure the
    production serialization contract is tested directly — not a MagicMock
    reimplementation that can silently diverge.
    """

    def test_include_conditional_true_omits_none_fields(self):
        """include_conditional=True (default) omits countries/uid_types/properties when None."""
        p = _make_provider(countries=None, uid_types=None, properties=None)
        result = p.to_dict(include_conditional=True)
        assert "countries" not in result
        assert "uid_types" not in result
        assert "properties" not in result

    def test_include_conditional_true_includes_non_none_fields(self):
        """include_conditional=True includes countries/uid_types/properties when set."""
        p = _make_provider(countries=["US", "GB"], uid_types=["uid2"], properties=["rid-1"])
        result = p.to_dict(include_conditional=True)
        assert result["countries"] == ["US", "GB"]
        assert result["uid_types"] == ["uid2"]
        assert result["properties"] == ["rid-1"]

    def test_include_conditional_false_always_includes_fields(self):
        """include_conditional=False always includes countries/uid_types/properties (even as None)."""
        p = _make_provider(countries=None, uid_types=None, properties=None)
        result = p.to_dict(include_conditional=False)
        assert "countries" in result
        assert result["countries"] is None
        assert "uid_types" in result
        assert result["uid_types"] is None
        assert "properties" in result
        assert result["properties"] is None

    def test_include_conditional_false_with_values(self):
        """include_conditional=False includes populated countries/uid_types/properties."""
        p = _make_provider(countries=["DE"], uid_types=["id5"], properties=["rid-2", "rid-3"])
        result = p.to_dict(include_conditional=False)
        assert result["countries"] == ["DE"]
        assert result["uid_types"] == ["id5"]
        assert result["properties"] == ["rid-2", "rid-3"]

    def test_core_fields_always_present(self):
        """Core fields are always present regardless of include_conditional."""
        p = _make_provider(
            provider_id="test-uuid",
            name="Test Provider",
            endpoint="http://example.com",
            context_match=False,
            identity_match=True,
            timeout_ms=300,
            priority=2,
            status="draining",
        )
        for include_conditional in (True, False):
            result = p.to_dict(include_conditional=include_conditional)
            assert result["provider_id"] == "test-uuid"
            assert result["name"] == "Test Provider"
            assert result["endpoint"] == "http://example.com"
            assert result["context_match"] is False
            assert result["identity_match"] is True
            assert result["timeout_ms"] == 300
            assert result["priority"] == 2
            assert result["status"] == "draining"

    def test_discovery_endpoint_uses_include_conditional_false(self, client):
        """The discovery endpoint calls to_dict(include_conditional=False) so null fields are explicit."""
        tenant = _make_tenant()
        providers = [_make_provider(countries=None, uid_types=None, properties=None)]

        mock_tmp_uow_cls = _make_tmp_uow(providers, tenant=tenant)

        with patch("src.routes.tmp_providers.TMPProviderUoW", mock_tmp_uow_cls):
            with patch.dict("os.environ", {"TMP_DISCOVERY_API_KEYS": "OPEN"}):
                response = client.get("/tenant/si-host/tmp-providers/discovery")

        assert response.status_code == 200
        entry = response.json()["providers"][0]
        # include_conditional=False → null fields must be present (not omitted)
        assert "countries" in entry
        assert entry["countries"] is None
        assert "uid_types" in entry
        assert entry["uid_types"] is None
        assert "properties" in entry
        assert entry["properties"] is None


# ---------------------------------------------------------------------------
# TMPProvider.auth_credentials encryption round-trip and error contract
# ---------------------------------------------------------------------------


class TestTMPProviderAuthCredentials:
    """TMPProvider.auth_credentials encrypts on write and decrypts on read.

    The property must raise AdCPConfigurationError (not silently return
    plaintext) when decryption fails — a corrupted ciphertext, a key rotation,
    or a tampered row must surface as a hard error so the admin can act.
    """

    def test_round_trip_encrypt_decrypt(self):
        """Setting auth_credentials encrypts; reading it back decrypts to the original value."""
        from cryptography.fernet import Fernet

        key = Fernet.generate_key().decode()
        with patch.dict("os.environ", {"ENCRYPTION_KEY": key}):
            p = TMPProvider()
            p.provider_id = "test-provider-id"
            p.auth_credentials = "super-secret-token"

            # The raw column must NOT be the plaintext value
            assert p._auth_credentials != "super-secret-token"
            assert p._auth_credentials is not None

            # Reading back through the property must return the original value
            assert p.auth_credentials == "super-secret-token"

    def test_none_value_returns_none(self):
        """Setting auth_credentials to None stores None and reads back as None."""
        p = TMPProvider()
        p.provider_id = "test-provider-id"
        p.auth_credentials = None
        assert p._auth_credentials is None
        assert p.auth_credentials is None

    def test_corrupted_ciphertext_raises_adcp_configuration_error(self):
        """A corrupted ciphertext raises AdCPConfigurationError, not a silent plaintext fallback."""
        from cryptography.fernet import Fernet

        from src.core.exceptions import AdCPConfigurationError

        key = Fernet.generate_key().decode()
        with patch.dict("os.environ", {"ENCRYPTION_KEY": key}):
            p = TMPProvider()
            p.provider_id = "test-provider-id"
            # Inject a corrupted ciphertext directly into the backing column
            p._auth_credentials = "not-a-valid-fernet-token"

            with pytest.raises(AdCPConfigurationError) as exc_info:
                _ = p.auth_credentials

        assert "test-provider-id" in str(exc_info.value)

    def test_empty_string_stores_none(self):
        """Setting auth_credentials to empty string stores None (treated as absent)."""
        p = TMPProvider()
        p.provider_id = "test-provider-id"
        p.auth_credentials = ""
        assert p._auth_credentials is None
        assert p.auth_credentials is None
