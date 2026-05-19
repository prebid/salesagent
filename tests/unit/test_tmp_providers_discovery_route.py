"""Unit tests for the FastAPI TMP provider discovery route.

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
"""

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


def _make_provider(
    provider_id="uuid-1",
    name="Provider A",
    endpoint="http://si-agent.localhost:3003",
    context_match=True,
    identity_match=True,
    countries=None,
    uid_types=None,
    timeout_ms=200,
    priority=0,
    status="active",
):
    """Create a mock TMPProvider ORM object with to_dict() support."""
    p = MagicMock()
    p.provider_id = provider_id
    p.name = name
    p.endpoint = endpoint
    p.context_match = context_match
    p.identity_match = identity_match
    p.countries = countries
    p.uid_types = uid_types
    p.timeout_ms = timeout_ms
    p.priority = priority
    p.status = status

    def _to_dict(*, include_conditional=True):
        result = {
            "provider_id": provider_id,
            "name": name,
            "endpoint": endpoint,
            "context_match": context_match,
            "identity_match": identity_match,
            "timeout_ms": timeout_ms,
            "priority": priority,
            "status": status,
        }
        if include_conditional:
            if countries:
                result["countries"] = countries
            if uid_types:
                result["uid_types"] = uid_types
        else:
            result["countries"] = countries
            result["uid_types"] = uid_types
        return result

    p.to_dict = _to_dict
    return p


def _make_tenant(tenant_id="si-host"):
    t = MagicMock()
    t.tenant_id = tenant_id
    t.name = "SI Host Tenant"
    return t


def _make_tenant_uow(tenant):
    """Return a mock TenantConfigUoW context manager whose .tenant_config.get_tenant() returns tenant."""
    mock_uow = MagicMock()
    mock_uow.tenant_config = MagicMock()
    mock_uow.tenant_config.get_tenant.return_value = tenant
    mock_uow_cls = MagicMock()
    mock_uow_cls.return_value.__enter__ = MagicMock(return_value=mock_uow)
    mock_uow_cls.return_value.__exit__ = MagicMock(return_value=False)
    return mock_uow_cls


def _make_tmp_uow(providers):
    """Return a mock TMPProviderUoW context manager whose .tmp_providers.list_syncable() returns providers."""
    mock_uow = MagicMock()
    mock_uow.tmp_providers = MagicMock()
    mock_uow.tmp_providers.list_syncable.return_value = providers
    mock_uow_cls = MagicMock()
    mock_uow_cls.return_value.__enter__ = MagicMock(return_value=mock_uow)
    mock_uow_cls.return_value.__exit__ = MagicMock(return_value=False)
    return mock_uow_cls


@pytest.fixture
def client():
    """Create a FastAPI TestClient with the tmp_providers router mounted."""
    from fastapi import FastAPI

    from src.routes.tmp_providers import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


class TestDiscoveryReturnsActiveProviders:
    """GET /tenant/{tenant_id}/tmp-providers/discovery returns active + draining providers."""

    def test_returns_two_active_providers(self, client):
        """Two active providers are returned in the response via repository.list_syncable()."""
        tenant = _make_tenant()
        providers = [
            _make_provider(provider_id="uuid-1", name="Provider A", priority=0, countries=["US"]),
            _make_provider(provider_id="uuid-2", name="Provider B", priority=1, uid_types=["uid2"]),
        ]

        mock_tenant_uow_cls = _make_tenant_uow(tenant)
        mock_tmp_uow_cls = _make_tmp_uow(providers)

        with patch("src.routes.tmp_providers.TenantConfigUoW", mock_tenant_uow_cls):
            with patch("src.routes.tmp_providers.TMPProviderUoW", mock_tmp_uow_cls):
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

        mock_tenant_uow_cls = _make_tenant_uow(tenant)
        mock_tmp_uow_cls = _make_tmp_uow(providers)

        with patch("src.routes.tmp_providers.TenantConfigUoW", mock_tenant_uow_cls):
            with patch("src.routes.tmp_providers.TMPProviderUoW", mock_tmp_uow_cls):
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
        mock_tenant_uow_cls = _make_tenant_uow(None)

        with patch("src.routes.tmp_providers.TenantConfigUoW", mock_tenant_uow_cls):
            response = client.get("/tenant/nonexistent/tmp-providers/discovery")

        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()


class TestDiscoveryEmptyProviders:
    """GET /tenant/{tenant_id}/tmp-providers/discovery returns empty list when no providers."""

    def test_returns_empty_providers_list(self, client):
        """Valid tenant with no active providers returns empty providers array."""
        tenant = _make_tenant()

        mock_tenant_uow_cls = _make_tenant_uow(tenant)
        mock_tmp_uow_cls = _make_tmp_uow([])

        with patch("src.routes.tmp_providers.TenantConfigUoW", mock_tenant_uow_cls):
            with patch("src.routes.tmp_providers.TMPProviderUoW", mock_tmp_uow_cls):
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

        mock_tenant_uow_cls = _make_tenant_uow(tenant)
        mock_tmp_uow_cls = _make_tmp_uow(providers)

        with patch("src.routes.tmp_providers.TenantConfigUoW", mock_tenant_uow_cls):
            with patch("src.routes.tmp_providers.TMPProviderUoW", mock_tmp_uow_cls):
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

        mock_tenant_uow_cls = _make_tenant_uow(tenant)
        mock_tmp_uow_cls = _make_tmp_uow(providers)

        with patch("src.routes.tmp_providers.TenantConfigUoW", mock_tenant_uow_cls):
            with patch("src.routes.tmp_providers.TMPProviderUoW", mock_tmp_uow_cls):
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

        mock_tenant_uow_cls = _make_tenant_uow(tenant)
        mock_tmp_uow_cls = _make_tmp_uow(providers)

        with patch("src.routes.tmp_providers.TenantConfigUoW", mock_tenant_uow_cls):
            with patch("src.routes.tmp_providers.TMPProviderUoW", mock_tmp_uow_cls):
                response = client.get("/tenant/si-host/tmp-providers/discovery")

        assert response.status_code == 200
        names = [p["name"] for p in response.json()["providers"]]
        assert names == ["Alpha", "Beta", "Gamma"]


# ---------------------------------------------------------------------------
# TMP_DISCOVERY_API_KEYS gating tests
# ---------------------------------------------------------------------------


class TestDiscoveryApiKeyAuth:
    """GET /tenant/{tenant_id}/tmp-providers/discovery enforces TMP_DISCOVERY_API_KEYS."""

    def test_open_when_tmp_discovery_api_keys_not_set(self, client):
        """When TMP_DISCOVERY_API_KEYS is unset the endpoint is open (dev mode)."""
        tenant = _make_tenant()
        mock_tenant_uow_cls = _make_tenant_uow(tenant)
        mock_tmp_uow_cls = _make_tmp_uow([])

        with patch("src.routes.tmp_providers.TenantConfigUoW", mock_tenant_uow_cls):
            with patch("src.routes.tmp_providers.TMPProviderUoW", mock_tmp_uow_cls):
                with patch.dict("os.environ", {}, clear=False):
                    import os

                    os.environ.pop("TMP_DISCOVERY_API_KEYS", None)
                    response = client.get("/tenant/si-host/tmp-providers/discovery")

        assert response.status_code == 200

    def test_open_when_tmp_discovery_api_keys_is_empty_string(self, client):
        """When TMP_DISCOVERY_API_KEYS is set to empty string the endpoint is open."""
        tenant = _make_tenant()
        mock_tenant_uow_cls = _make_tenant_uow(tenant)
        mock_tmp_uow_cls = _make_tmp_uow([])

        with patch("src.routes.tmp_providers.TenantConfigUoW", mock_tenant_uow_cls):
            with patch("src.routes.tmp_providers.TMPProviderUoW", mock_tmp_uow_cls):
                with patch.dict("os.environ", {"TMP_DISCOVERY_API_KEYS": ""}):
                    response = client.get("/tenant/si-host/tmp-providers/discovery")

        assert response.status_code == 200

    def test_returns_401_when_no_key_provided_and_keys_configured(self, client):
        """When TMP_DISCOVERY_API_KEYS is set and no key is sent, returns 401."""
        with patch.dict("os.environ", {"TMP_DISCOVERY_API_KEYS": "secret-key-1,secret-key-2"}):
            response = client.get("/tenant/si-host/tmp-providers/discovery")

        assert response.status_code == 401

    def test_returns_401_when_wrong_key_provided(self, client):
        """When TMP_DISCOVERY_API_KEYS is set and a wrong key is sent, returns 401."""
        with patch.dict("os.environ", {"TMP_DISCOVERY_API_KEYS": "correct-key"}):
            response = client.get(
                "/tenant/si-host/tmp-providers/discovery",
                headers={"x-adcp-auth": "wrong-key"},
            )

        assert response.status_code == 401

    def test_accepts_valid_key_via_x_adcp_auth_header(self, client):
        """Valid key in x-adcp-auth header is accepted."""
        tenant = _make_tenant()
        mock_tenant_uow_cls = _make_tenant_uow(tenant)
        mock_tmp_uow_cls = _make_tmp_uow([])

        with patch("src.routes.tmp_providers.TenantConfigUoW", mock_tenant_uow_cls):
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
        mock_tenant_uow_cls = _make_tenant_uow(tenant)
        mock_tmp_uow_cls = _make_tmp_uow([])

        with patch("src.routes.tmp_providers.TenantConfigUoW", mock_tenant_uow_cls):
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
        mock_tenant_uow_cls = _make_tenant_uow(tenant)
        mock_tmp_uow_cls = _make_tmp_uow([])

        with patch("src.routes.tmp_providers.TenantConfigUoW", mock_tenant_uow_cls):
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
        mock_tenant_uow_cls = _make_tenant_uow(tenant)
        mock_tmp_uow_cls = _make_tmp_uow([])

        with patch("src.routes.tmp_providers.TenantConfigUoW", mock_tenant_uow_cls):
            with patch("src.routes.tmp_providers.TMPProviderUoW", mock_tmp_uow_cls):
                with patch.dict("os.environ", {"TMP_DISCOVERY_API_KEYS": "key-a,key-b,key-c"}):
                    response = client.get(
                        "/tenant/si-host/tmp-providers/discovery",
                        headers={"x-adcp-auth": "key-b"},
                    )

        assert response.status_code == 200
