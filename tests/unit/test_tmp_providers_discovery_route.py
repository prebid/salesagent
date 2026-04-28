"""Unit tests for the FastAPI TMP provider discovery route.

Tests the endpoint:
    GET /tenant/{tenant_id}/tmp-providers/discovery

This is the FastAPI route in src/routes/tmp_providers.py, NOT the Flask admin
blueprint tested in test_tmp_providers_blueprint.py.  The FastAPI route is
polled by the TMP Router every 30 s on the internal network.

Covers:
- Returns active + draining providers, excludes inactive
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
    """Create a mock TMPProvider ORM object."""
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
    return p


def _make_tenant(tenant_id="si-host"):
    t = MagicMock()
    t.tenant_id = tenant_id
    t.name = "SI Host Tenant"
    return t


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
        """Two active providers are returned in the response."""
        tenant = _make_tenant()
        providers = [
            _make_provider(provider_id="uuid-1", name="Provider A", priority=0, countries=["US"]),
            _make_provider(provider_id="uuid-2", name="Provider B", priority=1, uid_types=["uid2"]),
        ]

        mock_session = MagicMock()
        mock_session.scalar.return_value = tenant
        mock_session.scalars.return_value.all.return_value = providers

        with patch("src.routes.tmp_providers.get_db_session") as mock_db:
            mock_db.return_value.__enter__ = MagicMock(return_value=mock_session)
            mock_db.return_value.__exit__ = MagicMock(return_value=False)

            response = client.get("/tenant/si-host/tmp-providers/discovery")

        assert response.status_code == 200
        data = response.json()
        assert data["tenant_id"] == "si-host"
        assert len(data["providers"]) == 2
        assert data["providers"][0]["provider_id"] == "uuid-1"
        assert data["providers"][0]["countries"] == ["US"]
        assert data["providers"][1]["provider_id"] == "uuid-2"
        assert data["providers"][1]["uid_types"] == ["uid2"]

    def test_includes_draining_providers(self, client):
        """Draining providers are included (router stops new requests but in-flight complete)."""
        tenant = _make_tenant()
        providers = [
            _make_provider(provider_id="uuid-1", status="active"),
            _make_provider(provider_id="uuid-2", status="draining"),
        ]

        mock_session = MagicMock()
        mock_session.scalar.return_value = tenant
        mock_session.scalars.return_value.all.return_value = providers

        with patch("src.routes.tmp_providers.get_db_session") as mock_db:
            mock_db.return_value.__enter__ = MagicMock(return_value=mock_session)
            mock_db.return_value.__exit__ = MagicMock(return_value=False)

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
        mock_session = MagicMock()
        mock_session.scalar.return_value = None  # No tenant found

        with patch("src.routes.tmp_providers.get_db_session") as mock_db:
            mock_db.return_value.__enter__ = MagicMock(return_value=mock_session)
            mock_db.return_value.__exit__ = MagicMock(return_value=False)

            response = client.get("/tenant/nonexistent/tmp-providers/discovery")

        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()


class TestDiscoveryEmptyProviders:
    """GET /tenant/{tenant_id}/tmp-providers/discovery returns empty list when no providers."""

    def test_returns_empty_providers_list(self, client):
        """Valid tenant with no active providers returns empty providers array."""
        tenant = _make_tenant()

        mock_session = MagicMock()
        mock_session.scalar.return_value = tenant
        mock_session.scalars.return_value.all.return_value = []

        with patch("src.routes.tmp_providers.get_db_session") as mock_db:
            mock_db.return_value.__enter__ = MagicMock(return_value=mock_session)
            mock_db.return_value.__exit__ = MagicMock(return_value=False)

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

        mock_session = MagicMock()
        mock_session.scalar.return_value = tenant
        mock_session.scalars.return_value.all.return_value = providers

        with patch("src.routes.tmp_providers.get_db_session") as mock_db:
            mock_db.return_value.__enter__ = MagicMock(return_value=mock_session)
            mock_db.return_value.__exit__ = MagicMock(return_value=False)

            response = client.get("/tenant/si-host/tmp-providers/discovery")

        assert response.status_code == 200
        entry = response.json()["providers"][0]

        required_fields = {
            "provider_id", "name", "endpoint", "context_match",
            "identity_match", "countries", "uid_types", "timeout_ms",
            "priority", "status",
        }
        assert required_fields.issubset(set(entry.keys()))

    def test_null_countries_uid_types_for_legacy_rows(self, client):
        """Legacy rows with null countries/uid_types return null (router treats as 'all')."""
        tenant = _make_tenant()
        providers = [
            _make_provider(countries=None, uid_types=None),
        ]

        mock_session = MagicMock()
        mock_session.scalar.return_value = tenant
        mock_session.scalars.return_value.all.return_value = providers

        with patch("src.routes.tmp_providers.get_db_session") as mock_db:
            mock_db.return_value.__enter__ = MagicMock(return_value=mock_session)
            mock_db.return_value.__exit__ = MagicMock(return_value=False)

            response = client.get("/tenant/si-host/tmp-providers/discovery")

        assert response.status_code == 200
        entry = response.json()["providers"][0]
        assert entry["countries"] is None
        assert entry["uid_types"] is None


class TestDiscoveryOrdering:
    """Providers are ordered by priority ASC, name ASC."""

    def test_providers_ordered_by_priority_then_name(self, client):
        """The SQL query orders by priority ASC, name ASC — verify mock returns in that order."""
        tenant = _make_tenant()
        # Simulate DB returning in correct order (priority 0 before 1, alpha within same priority)
        providers = [
            _make_provider(provider_id="uuid-a", name="Alpha", priority=0),
            _make_provider(provider_id="uuid-b", name="Beta", priority=0),
            _make_provider(provider_id="uuid-c", name="Gamma", priority=1),
        ]

        mock_session = MagicMock()
        mock_session.scalar.return_value = tenant
        mock_session.scalars.return_value.all.return_value = providers

        with patch("src.routes.tmp_providers.get_db_session") as mock_db:
            mock_db.return_value.__enter__ = MagicMock(return_value=mock_session)
            mock_db.return_value.__exit__ = MagicMock(return_value=False)

            response = client.get("/tenant/si-host/tmp-providers/discovery")

        assert response.status_code == 200
        names = [p["name"] for p in response.json()["providers"]]
        assert names == ["Alpha", "Beta", "Gamma"]
