"""Unit tests for TMP Provider admin blueprint.

Covers:
- SSRF validation on add/edit endpoints (check_url_ssrf wiring)
- Input validation (missing name, missing endpoint)
- CRUD route responses (list, add GET, deactivate, delete, health check)
- Discovery endpoint (GET /tmp-providers/discovery)
- Repository pattern (TMPProviderRepository used instead of raw DB calls)
"""

import os
from unittest.mock import MagicMock, patch

import pytest


def _make_tmp_provider_client():
    """Create a Flask test client authenticated as super admin for TMP provider endpoints."""
    from src.admin.app import create_app

    app = create_app({"TESTING": True, "SECRET_KEY": "test-secret", "WTF_CSRF_ENABLED": False})
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["test_user"] = "test_super_admin@example.com"
        sess["test_user_role"] = "super_admin"
        sess["authenticated"] = True
    return client


def _mock_db_with_tenant(mock_db, tenant_id="default"):
    """Wire mock_db so handlers can query Tenant."""
    mock_tenant = MagicMock()
    mock_tenant.tenant_id = tenant_id
    mock_tenant.name = "Default Tenant"
    mock_session = MagicMock()
    mock_session.scalars.return_value.first.return_value = mock_tenant
    mock_session.scalars.return_value.all.return_value = []
    mock_db.return_value.__enter__ = MagicMock(return_value=mock_session)
    mock_db.return_value.__exit__ = MagicMock(return_value=False)
    return mock_session


def _make_mock_provider(provider_id="test-uuid-1234", name="Test Provider",
                        endpoint="https://provider.example.com/tmp",
                        status="active"):
    """Create a mock TMPProvider object aligned with provider-registration.json schema."""
    provider = MagicMock()
    provider.provider_id = provider_id
    provider.name = name
    provider.endpoint = endpoint
    provider.context_match = True
    provider.identity_match = True
    provider.countries = ["US", "GB"]
    provider.uid_types = ["uid2", "id5"]
    provider.properties = None
    provider.timeout_ms = 50
    provider.priority = 0
    provider.status = status
    return provider


class TestTMPProviderAddSSRF:
    """SSRF validation is wired into the add endpoint."""

    def test_add_rejects_docker_internal_url(self):
        """POST /tmp-providers/add with host.docker.internal URL must redirect with error."""
        client = _make_tmp_provider_client()

        with patch("src.admin.blueprints.tmp_providers_bp.get_db_session") as mock_db:
            _mock_db_with_tenant(mock_db)
            with patch("src.admin.blueprints.tmp_providers_bp.TMPProviderRepository"):
                with patch.dict(os.environ, {"ADCP_AUTH_TEST_MODE": "true"}):
                    response = client.post(
                        "/tenant/default/tmp-providers/add",
                        data={
                            "name": "SSRF Test Provider",
                            "endpoint": "http://host.docker.internal:9999",
                            "context_match": "on",
                            "identity_match": "on",
                            "timeout_ms": "50",
                        },
                        follow_redirects=False,
                    )

        assert response.status_code == 302
        assert "add" in response.headers.get("Location", "")

    def test_add_accepts_safe_public_url(self):
        """POST /tmp-providers/add with a safe public URL must proceed past SSRF check."""
        client = _make_tmp_provider_client()

        with patch("src.admin.blueprints.tmp_providers_bp.get_db_session") as mock_db:
            mock_session = _mock_db_with_tenant(mock_db)
            mock_repo = MagicMock()
            with patch("src.admin.blueprints.tmp_providers_bp.TMPProviderRepository", return_value=mock_repo):
                with patch("src.core.security.url_validator.socket.gethostbyname", return_value="93.184.216.34"):
                    with patch.dict(os.environ, {"ADCP_AUTH_TEST_MODE": "true"}):
                        response = client.post(
                            "/tenant/default/tmp-providers/add",
                            data={
                                "name": "Safe Provider",
                                "endpoint": "https://provider.example.com/tmp",
                                "context_match": "on",
                                "identity_match": "on",
                                "countries": "US,GB",
                                "uid_types": "uid2,id5",
                                "timeout_ms": "50",
                            },
                            follow_redirects=False,
                        )

        # Must redirect to list (success) — not back to add form
        assert response.status_code == 302
        assert "add" not in response.headers.get("Location", "")
        mock_repo.create.assert_called_once()
        mock_session.commit.assert_called_once()


class TestTMPProviderEditSSRF:
    """SSRF validation is wired into the edit endpoint."""

    def test_edit_rejects_unsafe_url_on_update(self):
        """POST /tmp-providers/<id>/edit updating URL to host.docker.internal must be rejected."""
        client = _make_tmp_provider_client()

        existing_provider = _make_mock_provider()

        mock_session = MagicMock()
        mock_session.scalars.return_value.first.return_value = existing_provider

        mock_repo = MagicMock()
        mock_repo.get_by_id.return_value = existing_provider

        with patch("src.admin.blueprints.tmp_providers_bp.get_db_session") as mock_db:
            mock_db.return_value.__enter__ = MagicMock(return_value=mock_session)
            mock_db.return_value.__exit__ = MagicMock(return_value=False)
            with patch("src.admin.blueprints.tmp_providers_bp.TMPProviderRepository", return_value=mock_repo):
                with patch.dict(os.environ, {"ADCP_AUTH_TEST_MODE": "true"}):
                    response = client.post(
                        "/tenant/default/tmp-providers/test-uuid-1234/edit",
                        data={
                            "name": "Existing Provider",
                            "endpoint": "http://host.docker.internal:9999",
                            "context_match": "on",
                            "identity_match": "on",
                            "timeout_ms": "50",
                            "status": "active",
                        },
                        follow_redirects=False,
                    )

        assert response.status_code == 302
        assert "edit" in response.headers.get("Location", "")
        mock_repo.update_fields.assert_not_called()
        mock_session.commit.assert_not_called()


class TestTMPProviderInputValidation:
    """Input validation for required fields."""

    def test_add_rejects_missing_endpoint(self):
        """POST /tmp-providers/add without endpoint must redirect with error."""
        client = _make_tmp_provider_client()

        with patch("src.admin.blueprints.tmp_providers_bp.get_db_session") as mock_db:
            _mock_db_with_tenant(mock_db)
            with patch("src.admin.blueprints.tmp_providers_bp.TMPProviderRepository"):
                with patch.dict(os.environ, {"ADCP_AUTH_TEST_MODE": "true"}):
                    response = client.post(
                        "/tenant/default/tmp-providers/add",
                        data={
                            "name": "No Endpoint Provider",
                            "endpoint": "",
                            "context_match": "on",
                            "timeout_ms": "50",
                        },
                        follow_redirects=False,
                    )

        assert response.status_code == 302
        assert "add" in response.headers.get("Location", "")

    def test_add_rejects_missing_name(self):
        """POST /tmp-providers/add without name must redirect with error."""
        client = _make_tmp_provider_client()

        with patch("src.admin.blueprints.tmp_providers_bp.get_db_session") as mock_db:
            _mock_db_with_tenant(mock_db)
            with patch("src.admin.blueprints.tmp_providers_bp.TMPProviderRepository"):
                with patch("src.core.security.url_validator.socket.gethostbyname", return_value="93.184.216.34"):
                    with patch.dict(os.environ, {"ADCP_AUTH_TEST_MODE": "true"}):
                        response = client.post(
                            "/tenant/default/tmp-providers/add",
                            data={
                                "name": "",
                                "endpoint": "https://provider.example.com/tmp",
                                "context_match": "on",
                                "timeout_ms": "50",
                            },
                            follow_redirects=False,
                        )

        assert response.status_code == 302
        assert "add" in response.headers.get("Location", "")


class TestTMPProviderDeactivate:
    """Deactivate endpoint sets status='inactive' via repository."""

    def test_deactivate_returns_success_json(self):
        """POST /tmp-providers/<id>/deactivate returns JSON success."""
        client = _make_tmp_provider_client()

        existing_provider = _make_mock_provider()

        mock_session = MagicMock()
        mock_repo = MagicMock()
        mock_repo.deactivate.return_value = existing_provider

        with patch("src.admin.blueprints.tmp_providers_bp.get_db_session") as mock_db:
            mock_db.return_value.__enter__ = MagicMock(return_value=mock_session)
            mock_db.return_value.__exit__ = MagicMock(return_value=False)
            with patch("src.admin.blueprints.tmp_providers_bp.TMPProviderRepository", return_value=mock_repo):
                with patch.dict(os.environ, {"ADCP_AUTH_TEST_MODE": "true"}):
                    response = client.post(
                        "/tenant/default/tmp-providers/test-uuid-1234/deactivate",
                    )

        assert response.status_code == 200
        data = response.get_json()
        assert data["success"] is True
        mock_repo.deactivate.assert_called_once_with("test-uuid-1234")
        mock_session.commit.assert_called_once()

    def test_deactivate_returns_404_for_missing_provider(self):
        """POST /tmp-providers/<id>/deactivate returns 404 when provider not found."""
        client = _make_tmp_provider_client()

        mock_session = MagicMock()
        mock_repo = MagicMock()
        mock_repo.deactivate.return_value = None

        with patch("src.admin.blueprints.tmp_providers_bp.get_db_session") as mock_db:
            mock_db.return_value.__enter__ = MagicMock(return_value=mock_session)
            mock_db.return_value.__exit__ = MagicMock(return_value=False)
            with patch("src.admin.blueprints.tmp_providers_bp.TMPProviderRepository", return_value=mock_repo):
                with patch.dict(os.environ, {"ADCP_AUTH_TEST_MODE": "true"}):
                    response = client.post(
                        "/tenant/default/tmp-providers/nonexistent-uuid/deactivate",
                    )

        assert response.status_code == 404
        data = response.get_json()
        assert "error" in data


class TestTMPProviderDelete:
    """Delete endpoint hard-deletes a provider via repository."""

    def test_delete_returns_success_json(self):
        """DELETE /tmp-providers/<id>/delete returns JSON success."""
        client = _make_tmp_provider_client()

        existing_provider = _make_mock_provider()

        mock_session = MagicMock()
        mock_repo = MagicMock()
        mock_repo.get_by_id.return_value = existing_provider
        mock_repo.delete.return_value = True

        with patch("src.admin.blueprints.tmp_providers_bp.get_db_session") as mock_db:
            mock_db.return_value.__enter__ = MagicMock(return_value=mock_session)
            mock_db.return_value.__exit__ = MagicMock(return_value=False)
            with patch("src.admin.blueprints.tmp_providers_bp.TMPProviderRepository", return_value=mock_repo):
                with patch.dict(os.environ, {"ADCP_AUTH_TEST_MODE": "true"}):
                    response = client.delete(
                        "/tenant/default/tmp-providers/test-uuid-1234/delete",
                    )

        assert response.status_code == 200
        data = response.get_json()
        assert data["success"] is True
        mock_repo.delete.assert_called_once_with("test-uuid-1234")
        mock_session.commit.assert_called_once()

    def test_delete_returns_404_for_missing_provider(self):
        """DELETE /tmp-providers/<id>/delete returns 404 when provider not found."""
        client = _make_tmp_provider_client()

        mock_session = MagicMock()
        mock_repo = MagicMock()
        mock_repo.get_by_id.return_value = None

        with patch("src.admin.blueprints.tmp_providers_bp.get_db_session") as mock_db:
            mock_db.return_value.__enter__ = MagicMock(return_value=mock_session)
            mock_db.return_value.__exit__ = MagicMock(return_value=False)
            with patch("src.admin.blueprints.tmp_providers_bp.TMPProviderRepository", return_value=mock_repo):
                with patch.dict(os.environ, {"ADCP_AUTH_TEST_MODE": "true"}):
                    response = client.delete(
                        "/tenant/default/tmp-providers/nonexistent-uuid/delete",
                    )

        assert response.status_code == 404


class TestTMPProviderHealthCheck:
    """Health check endpoint calls provider.endpoint/health."""

    def test_health_check_returns_healthy(self):
        """GET /tmp-providers/<id>/health returns healthy when endpoint responds 200."""
        client = _make_tmp_provider_client()

        existing_provider = _make_mock_provider()

        mock_session = MagicMock()
        mock_repo = MagicMock()
        mock_repo.get_by_id.return_value = existing_provider

        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch("src.admin.blueprints.tmp_providers_bp.get_db_session") as mock_db:
            mock_db.return_value.__enter__ = MagicMock(return_value=mock_session)
            mock_db.return_value.__exit__ = MagicMock(return_value=False)
            with patch("src.admin.blueprints.tmp_providers_bp.TMPProviderRepository", return_value=mock_repo):
                with patch("src.admin.blueprints.tmp_providers_bp.requests.get", return_value=mock_response) as mock_get:
                    with patch.dict(os.environ, {"ADCP_AUTH_TEST_MODE": "true"}):
                        response = client.get(
                            "/tenant/default/tmp-providers/test-uuid-1234/health",
                        )

        assert response.status_code == 200
        data = response.get_json()
        assert data["success"] is True
        assert data["status"] == "healthy"
        mock_get.assert_called_once_with(
            "https://provider.example.com/tmp/health", timeout=5, allow_redirects=False
        )

    def test_health_check_returns_unhealthy_on_non_200(self):
        """GET /tmp-providers/<id>/health returns unhealthy when endpoint responds non-200."""
        client = _make_tmp_provider_client()

        existing_provider = _make_mock_provider()

        mock_session = MagicMock()
        mock_repo = MagicMock()
        mock_repo.get_by_id.return_value = existing_provider

        mock_response = MagicMock()
        mock_response.status_code = 503

        with patch("src.admin.blueprints.tmp_providers_bp.get_db_session") as mock_db:
            mock_db.return_value.__enter__ = MagicMock(return_value=mock_session)
            mock_db.return_value.__exit__ = MagicMock(return_value=False)
            with patch("src.admin.blueprints.tmp_providers_bp.TMPProviderRepository", return_value=mock_repo):
                with patch("src.admin.blueprints.tmp_providers_bp.requests.get", return_value=mock_response):
                    with patch.dict(os.environ, {"ADCP_AUTH_TEST_MODE": "true"}):
                        response = client.get(
                            "/tenant/default/tmp-providers/test-uuid-1234/health",
                        )

        assert response.status_code == 200
        data = response.get_json()
        assert data["success"] is False
        assert "503" in data["status"]

    def test_health_check_returns_error_on_connection_failure(self):
        """GET /tmp-providers/<id>/health returns error when endpoint is unreachable."""
        import requests as req_lib

        client = _make_tmp_provider_client()

        existing_provider = _make_mock_provider()

        mock_session = MagicMock()
        mock_repo = MagicMock()
        mock_repo.get_by_id.return_value = existing_provider

        with patch("src.admin.blueprints.tmp_providers_bp.get_db_session") as mock_db:
            mock_db.return_value.__enter__ = MagicMock(return_value=mock_session)
            mock_db.return_value.__exit__ = MagicMock(return_value=False)
            with patch("src.admin.blueprints.tmp_providers_bp.TMPProviderRepository", return_value=mock_repo):
                with patch(
                    "src.admin.blueprints.tmp_providers_bp.requests.get",
                    side_effect=req_lib.ConnectionError("Connection refused"),
                ):
                    with patch.dict(os.environ, {"ADCP_AUTH_TEST_MODE": "true"}):
                        response = client.get(
                            "/tenant/default/tmp-providers/test-uuid-1234/health",
                        )

        assert response.status_code == 200
        data = response.get_json()
        assert data["success"] is False
        assert "error" in data


class TestTMPProviderDiscovery:
    """Discovery endpoint returns active providers as JSON for the Go TMP Router.

    The discovery endpoint uses the tenant_id from the URL path (blueprint
    prefix), NOT from a query parameter.  This prevents cross-tenant
    enumeration.
    """

    def test_discovery_returns_active_providers(self):
        """GET /tmp-providers/discovery returns active providers (tenant from URL path)."""
        client = _make_tmp_provider_client()

        provider1 = _make_mock_provider(provider_id="uuid-1", name="Provider A")
        provider2 = _make_mock_provider(provider_id="uuid-2", name="Provider B")

        mock_session = MagicMock()
        mock_repo = MagicMock()
        mock_repo.list_active.return_value = [provider1, provider2]

        with patch("src.admin.blueprints.tmp_providers_bp.get_db_session") as mock_db:
            mock_db.return_value.__enter__ = MagicMock(return_value=mock_session)
            mock_db.return_value.__exit__ = MagicMock(return_value=False)
            with patch("src.admin.blueprints.tmp_providers_bp.TMPProviderRepository", return_value=mock_repo):
                response = client.get("/tenant/default/tmp-providers/discovery")

        assert response.status_code == 200
        data = response.get_json()
        assert isinstance(data, list)
        assert len(data) == 2
        assert data[0]["provider_id"] == "uuid-1"
        assert data[0]["name"] == "Provider A"
        assert data[0]["status"] == "active"
        assert data[1]["provider_id"] == "uuid-2"
        mock_repo.list_active.assert_called_once()

    def test_discovery_returns_empty_list_when_no_active_providers(self):
        """GET /tmp-providers/discovery returns [] when no active providers."""
        client = _make_tmp_provider_client()

        mock_session = MagicMock()
        mock_repo = MagicMock()
        mock_repo.list_active.return_value = []

        with patch("src.admin.blueprints.tmp_providers_bp.get_db_session") as mock_db:
            mock_db.return_value.__enter__ = MagicMock(return_value=mock_session)
            mock_db.return_value.__exit__ = MagicMock(return_value=False)
            with patch("src.admin.blueprints.tmp_providers_bp.TMPProviderRepository", return_value=mock_repo):
                response = client.get("/tenant/default/tmp-providers/discovery")

        assert response.status_code == 200
        data = response.get_json()
        assert data == []

    def test_discovery_includes_expected_fields(self):
        """Discovery response includes all fields needed by the Go TMP Router."""
        client = _make_tmp_provider_client()

        provider = _make_mock_provider()

        mock_session = MagicMock()
        mock_repo = MagicMock()
        mock_repo.list_active.return_value = [provider]

        with patch("src.admin.blueprints.tmp_providers_bp.get_db_session") as mock_db:
            mock_db.return_value.__enter__ = MagicMock(return_value=mock_session)
            mock_db.return_value.__exit__ = MagicMock(return_value=False)
            with patch("src.admin.blueprints.tmp_providers_bp.TMPProviderRepository", return_value=mock_repo):
                response = client.get("/tenant/default/tmp-providers/discovery")

        assert response.status_code == 200
        data = response.get_json()
        entry = data[0]
        # Core fields always present per provider-registration.json schema
        required_fields = {"provider_id", "name", "endpoint", "context_match",
                           "identity_match", "timeout_ms", "priority", "status"}
        assert required_fields.issubset(set(entry.keys()))
        # Conditional fields present because mock has countries/uid_types set
        assert "countries" in entry
        assert "uid_types" in entry
        assert entry["countries"] == ["US", "GB"]
        assert entry["uid_types"] == ["uid2", "id5"]
        # properties is None on mock, so should NOT be in response
        assert "properties" not in entry
