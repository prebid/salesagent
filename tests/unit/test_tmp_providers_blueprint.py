"""Unit tests for TMP Provider admin blueprint.

Covers:
- SSRF validation on add/edit endpoints (check_url_ssrf wiring)
- Input validation (missing name, missing endpoint, invalid timeout_ms, invalid status)
- Identity match validation (countries/uid_types required, uid_type enum)
- CRUD route responses (list, add GET, deactivate, delete, health check)
- TMPProviderUoW used instead of raw DB calls
- @log_admin_action on destructive routes
- TMPProvider.to_dict() serialization (real model, not mock)

Note: Discovery endpoint tests are in test_tmp_providers_discovery_route.py
(the canonical discovery endpoint is the FastAPI route, not Flask).
"""

import os
from unittest.mock import MagicMock, patch

from src.core.database.models import TMPProvider
from tests.unit._tmp_helpers import _make_blueprint_uow, make_super_admin_client


def _make_tmp_provider_client():
    """Create a Flask test client authenticated as super admin for TMP provider endpoints."""
    return make_super_admin_client()


def _make_mock_provider(
    provider_id="test-uuid-1234", name="Test Provider", endpoint="https://provider.example.com/tmp", status="active"
):
    """Create a mock TMPProvider object aligned with provider-registration.json schema.

    Returns a MagicMock (not a real TMPProvider) — used for tests that only need
    attribute access on the provider object (deactivate, delete, health, edit POST).
    Tests that exercise to_dict() use a real TMPProvider via _tmp_helpers._make_provider.
    """
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
        """POST /tmp-providers/add with host.docker.internal URL must redirect with error.

        Uses context_match-only form data (no identity_match) so the SSRF check
        is the sole rejection reason — the test is not accidentally relying on
        identity_match validation firing first.
        """
        client = _make_tmp_provider_client()

        mock_uow_cls, mock_uow = _make_blueprint_uow()
        with patch("src.admin.blueprints.tmp_providers.TMPProviderUoW", mock_uow_cls):
            with patch.dict(os.environ, {"ADCP_AUTH_TEST_MODE": "true"}):
                response = client.post(
                    "/tenant/default/tmp-providers/add",
                    data={
                        "name": "SSRF Test Provider",
                        "endpoint": "http://host.docker.internal:9999",
                        "context_match": "on",
                        # identity_match intentionally omitted — context-only provider
                        # so the SSRF check is the only validation that fires.
                        "timeout_ms": "50",
                    },
                    follow_redirects=False,
                )

        assert response.status_code == 302
        assert "add" in response.headers.get("Location", "")
        mock_uow.tmp_providers.create_from_fields.assert_not_called()

    def test_add_accepts_safe_public_url(self):
        """POST /tmp-providers/add with a safe public URL must proceed past SSRF check."""
        client = _make_tmp_provider_client()

        mock_uow_cls, mock_uow = _make_blueprint_uow()
        with patch("src.admin.blueprints.tmp_providers.TMPProviderUoW", mock_uow_cls):
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
        # create_from_fields is called (not create) — the blueprint uses the
        # factory method symmetric with update_fields on the edit path.
        mock_uow.tmp_providers.create_from_fields.assert_called_once_with(
            name="Safe Provider",
            endpoint="https://provider.example.com/tmp",
            context_match=True,
            identity_match=True,
            countries=["US", "GB"],
            uid_types=["uid2", "id5"],
            properties=None,
            timeout_ms=50,
            priority=0,
            status="active",
            auth_type=None,
            auth_credentials=None,
        )


class TestTMPProviderEditSSRF:
    """SSRF validation is wired into the edit endpoint."""

    def test_edit_rejects_unsafe_url_on_update(self):
        """POST /tmp-providers/<id>/edit updating URL to host.docker.internal must be rejected.

        Uses identity_match=on with countries+uid_types so the SSRF check is the
        sole rejection reason — mirrors the add-path test (test_add_rejects_docker_internal_url)
        which uses context_match-only to isolate SSRF as the sole cause.
        """
        client = _make_tmp_provider_client()

        existing_provider = _make_mock_provider()

        mock_uow_cls, mock_uow = _make_blueprint_uow()
        mock_uow.tmp_providers.get_by_id.return_value = existing_provider
        with patch("src.admin.blueprints.tmp_providers.TMPProviderUoW", mock_uow_cls):
            with patch.dict(os.environ, {"ADCP_AUTH_TEST_MODE": "true"}):
                response = client.post(
                    "/tenant/default/tmp-providers/test-uuid-1234/edit",
                    data={
                        "name": "Existing Provider",
                        "endpoint": "http://host.docker.internal:9999",
                        "context_match": "on",
                        "identity_match": "on",
                        # Provide valid countries+uid_types so identity_match validation
                        # passes and SSRF is the sole rejection reason.
                        "countries": "US,GB",
                        "uid_types": "uid2,id5",
                        "timeout_ms": "50",
                        "status": "active",
                    },
                    follow_redirects=False,
                )

        assert response.status_code == 302
        assert "edit" in response.headers.get("Location", "")
        mock_uow.tmp_providers.update_fields.assert_not_called()

    def test_edit_accepts_safe_public_url(self):
        """POST /tmp-providers/<id>/edit with a safe public URL must succeed.

        Positive counterpart to test_edit_rejects_unsafe_url_on_update — verifies
        that the SSRF guard does not block legitimate public endpoints on the edit path.
        """
        client = _make_tmp_provider_client()

        existing_provider = _make_mock_provider()

        mock_uow_cls, mock_uow = _make_blueprint_uow()
        mock_uow.tmp_providers.get_by_id.return_value = existing_provider
        with patch("src.admin.blueprints.tmp_providers.TMPProviderUoW", mock_uow_cls):
            with patch("src.core.security.url_validator.socket.gethostbyname", return_value="93.184.216.34"):
                with patch.dict(os.environ, {"ADCP_AUTH_TEST_MODE": "true"}):
                    response = client.post(
                        "/tenant/default/tmp-providers/test-uuid-1234/edit",
                        data={
                            "name": "Existing Provider",
                            "endpoint": "https://provider.example.com/tmp",
                            "context_match": "on",
                            "identity_match": "on",
                            "countries": "US,GB",
                            "uid_types": "uid2,id5",
                            "timeout_ms": "50",
                            "status": "active",
                        },
                        follow_redirects=False,
                    )

        assert response.status_code == 302
        assert "tmp-providers" in response.headers.get("Location", "")
        mock_uow.tmp_providers.update_fields.assert_called_once_with(
            "test-uuid-1234",
            name="Existing Provider",
            endpoint="https://provider.example.com/tmp",
            context_match=True,
            identity_match=True,
            countries=["US", "GB"],
            uid_types=["uid2", "id5"],
            properties=None,
            timeout_ms=50,
            priority=0,
            status="active",
            auth_type=None,
        )


class TestTMPProviderInputValidation:
    """Input validation for required fields."""

    def test_add_rejects_missing_endpoint(self):
        """POST /tmp-providers/add without endpoint must redirect with error."""
        client = _make_tmp_provider_client()

        mock_uow_cls, mock_uow = _make_blueprint_uow()
        with patch("src.admin.blueprints.tmp_providers.TMPProviderUoW", mock_uow_cls):
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

        mock_uow_cls, mock_uow = _make_blueprint_uow()
        with patch("src.admin.blueprints.tmp_providers.TMPProviderUoW", mock_uow_cls):
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

    def test_add_rejects_non_numeric_timeout_ms(self):
        """POST /tmp-providers/add with non-numeric timeout_ms must redirect with error."""
        client = _make_tmp_provider_client()

        mock_uow_cls, mock_uow = _make_blueprint_uow()
        with patch("src.admin.blueprints.tmp_providers.TMPProviderUoW", mock_uow_cls):
            with patch.dict(os.environ, {"ADCP_AUTH_TEST_MODE": "true"}):
                response = client.post(
                    "/tenant/default/tmp-providers/add",
                    data={
                        "name": "Test Provider",
                        "endpoint": "https://provider.example.com/tmp",
                        "context_match": "on",
                        "timeout_ms": "not-a-number",
                    },
                    follow_redirects=False,
                )

        assert response.status_code == 302
        assert "add" in response.headers.get("Location", "")

    def test_add_rejects_invalid_status(self):
        """POST /tmp-providers/add with invalid status must redirect with error."""
        client = _make_tmp_provider_client()

        mock_uow_cls, mock_uow = _make_blueprint_uow()
        with patch("src.admin.blueprints.tmp_providers.TMPProviderUoW", mock_uow_cls):
            with patch("src.core.security.url_validator.socket.gethostbyname", return_value="93.184.216.34"):
                with patch.dict(os.environ, {"ADCP_AUTH_TEST_MODE": "true"}):
                    response = client.post(
                        "/tenant/default/tmp-providers/add",
                        data={
                            "name": "Test Provider",
                            "endpoint": "https://provider.example.com/tmp",
                            "context_match": "on",
                            "timeout_ms": "50",
                            "status": "bogus_status",
                        },
                        follow_redirects=False,
                    )

        assert response.status_code == 302
        assert "add" in response.headers.get("Location", "")

    def test_add_passes_status_to_create_from_fields(self):
        """POST /tmp-providers/add with explicit status passes it to create_from_fields."""
        client = _make_tmp_provider_client()

        mock_uow_cls, mock_uow = _make_blueprint_uow()
        with patch("src.admin.blueprints.tmp_providers.TMPProviderUoW", mock_uow_cls):
            with patch("src.core.security.url_validator.socket.gethostbyname", return_value="93.184.216.34"):
                with patch.dict(os.environ, {"ADCP_AUTH_TEST_MODE": "true"}):
                    response = client.post(
                        "/tenant/default/tmp-providers/add",
                        data={
                            "name": "Draining Provider",
                            "endpoint": "https://provider.example.com/tmp",
                            "context_match": "on",
                            "identity_match": "on",
                            "countries": "US",
                            "uid_types": "uid2",
                            "timeout_ms": "50",
                            "status": "draining",
                        },
                        follow_redirects=False,
                    )

        assert response.status_code == 302
        mock_uow.tmp_providers.create_from_fields.assert_called_once_with(
            name="Draining Provider",
            endpoint="https://provider.example.com/tmp",
            context_match=True,
            identity_match=True,
            countries=["US"],
            uid_types=["uid2"],
            properties=None,
            timeout_ms=50,
            priority=0,
            status="draining",
            auth_type=None,
            auth_credentials=None,
        )


class TestTMPProviderIdentityMatchValidation:
    """Identity match validation: countries/uid_types required, uid_type enum enforced."""

    def test_add_rejects_identity_match_without_countries(self):
        """POST /tmp-providers/add with identity_match=on but no countries must redirect with error."""
        client = _make_tmp_provider_client()

        mock_uow_cls, mock_uow = _make_blueprint_uow()
        with patch("src.admin.blueprints.tmp_providers.TMPProviderUoW", mock_uow_cls):
            with patch("src.core.security.url_validator.socket.gethostbyname", return_value="93.184.216.34"):
                with patch.dict(os.environ, {"ADCP_AUTH_TEST_MODE": "true"}):
                    response = client.post(
                        "/tenant/default/tmp-providers/add",
                        data={
                            "name": "No Countries Provider",
                            "endpoint": "https://provider.example.com/tmp",
                            "identity_match": "on",
                            "countries": "",
                            "uid_types": "uid2",
                            "timeout_ms": "50",
                        },
                        follow_redirects=False,
                    )

        assert response.status_code == 302
        assert "add" in response.headers.get("Location", "")
        # Production uses create_from_fields, not create — assert the right method.
        mock_uow.tmp_providers.create_from_fields.assert_not_called()

    def test_add_rejects_identity_match_without_uid_types(self):
        """POST /tmp-providers/add with identity_match=on but no uid_types must redirect with error."""
        client = _make_tmp_provider_client()

        mock_uow_cls, mock_uow = _make_blueprint_uow()
        with patch("src.admin.blueprints.tmp_providers.TMPProviderUoW", mock_uow_cls):
            with patch("src.core.security.url_validator.socket.gethostbyname", return_value="93.184.216.34"):
                with patch.dict(os.environ, {"ADCP_AUTH_TEST_MODE": "true"}):
                    response = client.post(
                        "/tenant/default/tmp-providers/add",
                        data={
                            "name": "No UID Types Provider",
                            "endpoint": "https://provider.example.com/tmp",
                            "identity_match": "on",
                            "countries": "US",
                            "uid_types": "",
                            "timeout_ms": "50",
                        },
                        follow_redirects=False,
                    )

        assert response.status_code == 302
        assert "add" in response.headers.get("Location", "")
        # Production uses create_from_fields, not create — assert the right method.
        mock_uow.tmp_providers.create_from_fields.assert_not_called()

    def test_add_rejects_invalid_uid_type_value(self):
        """POST /tmp-providers/add with invalid uid_type value must redirect with error."""
        client = _make_tmp_provider_client()

        mock_uow_cls, mock_uow = _make_blueprint_uow()
        with patch("src.admin.blueprints.tmp_providers.TMPProviderUoW", mock_uow_cls):
            with patch("src.core.security.url_validator.socket.gethostbyname", return_value="93.184.216.34"):
                with patch.dict(os.environ, {"ADCP_AUTH_TEST_MODE": "true"}):
                    response = client.post(
                        "/tenant/default/tmp-providers/add",
                        data={
                            "name": "Bad UID Provider",
                            "endpoint": "https://provider.example.com/tmp",
                            "identity_match": "on",
                            "countries": "US",
                            "uid_types": "bogus_type",
                            "timeout_ms": "50",
                        },
                        follow_redirects=False,
                    )

        assert response.status_code == 302
        assert "add" in response.headers.get("Location", "")
        # Production uses create_from_fields, not create — assert the right method.
        mock_uow.tmp_providers.create_from_fields.assert_not_called()


class TestTMPProviderDeactivate:
    """Deactivate endpoint sets status='inactive' via repository."""

    def test_deactivate_returns_success_json(self):
        """POST /tmp-providers/<id>/deactivate returns JSON success."""
        client = _make_tmp_provider_client()

        existing_provider = _make_mock_provider()

        mock_uow_cls, mock_uow = _make_blueprint_uow()
        mock_uow.tmp_providers.deactivate.return_value = existing_provider
        with patch("src.admin.blueprints.tmp_providers.TMPProviderUoW", mock_uow_cls):
            with patch.dict(os.environ, {"ADCP_AUTH_TEST_MODE": "true"}):
                response = client.post(
                    "/tenant/default/tmp-providers/test-uuid-1234/deactivate",
                )

        assert response.status_code == 200
        data = response.get_json()
        assert data["success"] is True
        mock_uow.tmp_providers.deactivate.assert_called_once_with("test-uuid-1234")

    def test_deactivate_returns_404_for_missing_provider(self):
        """POST /tmp-providers/<id>/deactivate returns 404 when provider not found."""
        client = _make_tmp_provider_client()

        mock_uow_cls, mock_uow = _make_blueprint_uow()
        mock_uow.tmp_providers.deactivate.return_value = None
        with patch("src.admin.blueprints.tmp_providers.TMPProviderUoW", mock_uow_cls):
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

        mock_uow_cls, mock_uow = _make_blueprint_uow()
        mock_uow.tmp_providers.get_by_id.return_value = existing_provider
        mock_uow.tmp_providers.delete.return_value = True
        with patch("src.admin.blueprints.tmp_providers.TMPProviderUoW", mock_uow_cls):
            with patch.dict(os.environ, {"ADCP_AUTH_TEST_MODE": "true"}):
                response = client.delete(
                    "/tenant/default/tmp-providers/test-uuid-1234/delete",
                )

        assert response.status_code == 200
        data = response.get_json()
        assert data["success"] is True
        mock_uow.tmp_providers.delete.assert_called_once_with("test-uuid-1234")

    def test_delete_returns_404_for_missing_provider(self):
        """DELETE /tmp-providers/<id>/delete returns 404 when provider not found."""
        client = _make_tmp_provider_client()

        mock_uow_cls, mock_uow = _make_blueprint_uow()
        mock_uow.tmp_providers.get_by_id.return_value = None
        with patch("src.admin.blueprints.tmp_providers.TMPProviderUoW", mock_uow_cls):
            with patch.dict(os.environ, {"ADCP_AUTH_TEST_MODE": "true"}):
                response = client.delete(
                    "/tenant/default/tmp-providers/nonexistent-uuid/delete",
                )

        assert response.status_code == 404


class TestTMPProviderHealthCheck:
    """Health check endpoint reads from DB (background scheduler writes health_status)."""

    def test_health_check_returns_healthy_from_db(self):
        """GET /tmp-providers/<id>/health returns healthy when health_status='healthy'."""
        from datetime import UTC, datetime

        client = _make_tmp_provider_client()

        existing_provider = _make_mock_provider()
        existing_provider.health_status = "healthy"
        existing_provider.last_health_checked_at = datetime(2026, 5, 25, 12, 0, 0, tzinfo=UTC)

        mock_uow_cls, mock_uow = _make_blueprint_uow()
        mock_uow.tmp_providers.get_by_id.return_value = existing_provider
        with patch("src.admin.blueprints.tmp_providers.TMPProviderUoW", mock_uow_cls):
            with patch.dict(os.environ, {"ADCP_AUTH_TEST_MODE": "true"}):
                response = client.get(
                    "/tenant/default/tmp-providers/test-uuid-1234/health",
                )

        assert response.status_code == 200
        data = response.get_json()
        assert data["success"] is True
        assert data["status"] == "healthy"
        assert data["last_checked"] is not None

    def test_health_check_returns_unhealthy_from_db(self):
        """GET /tmp-providers/<id>/health returns unhealthy when health_status='unhealthy'."""
        from datetime import UTC, datetime

        client = _make_tmp_provider_client()

        existing_provider = _make_mock_provider()
        existing_provider.health_status = "unhealthy"
        existing_provider.last_health_checked_at = datetime(2026, 5, 25, 12, 0, 0, tzinfo=UTC)

        mock_uow_cls, mock_uow = _make_blueprint_uow()
        mock_uow.tmp_providers.get_by_id.return_value = existing_provider
        with patch("src.admin.blueprints.tmp_providers.TMPProviderUoW", mock_uow_cls):
            with patch.dict(os.environ, {"ADCP_AUTH_TEST_MODE": "true"}):
                response = client.get(
                    "/tenant/default/tmp-providers/test-uuid-1234/health",
                )

        assert response.status_code == 200
        data = response.get_json()
        assert data["success"] is False
        assert data["status"] == "unhealthy"

    def test_health_check_returns_pending_when_never_checked(self):
        """GET /tmp-providers/<id>/health returns pending when health_status is None."""
        client = _make_tmp_provider_client()

        existing_provider = _make_mock_provider()
        existing_provider.health_status = None
        existing_provider.last_health_checked_at = None

        mock_uow_cls, mock_uow = _make_blueprint_uow()
        mock_uow.tmp_providers.get_by_id.return_value = existing_provider
        with patch("src.admin.blueprints.tmp_providers.TMPProviderUoW", mock_uow_cls):
            with patch.dict(os.environ, {"ADCP_AUTH_TEST_MODE": "true"}):
                response = client.get(
                    "/tenant/default/tmp-providers/test-uuid-1234/health",
                )

        assert response.status_code == 200
        data = response.get_json()
        assert data["success"] is True
        assert data["status"] == "pending"


class TestTMPProviderAuthFields:
    """auth_type and auth_credentials are parsed and passed through the add/edit flow."""

    def test_add_passes_auth_type_and_credentials_to_create_from_fields(self):
        """POST /tmp-providers/add with auth_type and auth_credentials passes them to create_from_fields.

        The blueprint now calls ``uow.tmp_providers.create_from_fields(**data)`` instead of
        constructing ``TMPProvider(...)`` inline, so we assert on the repository factory method.
        """
        client = _make_tmp_provider_client()

        mock_uow_cls, mock_uow = _make_blueprint_uow()
        with patch("src.admin.blueprints.tmp_providers.TMPProviderUoW", mock_uow_cls):
            with patch("src.core.security.url_validator.socket.gethostbyname", return_value="93.184.216.34"):
                with patch.dict(os.environ, {"ADCP_AUTH_TEST_MODE": "true"}):
                    response = client.post(
                        "/tenant/default/tmp-providers/add",
                        data={
                            "name": "Auth Provider",
                            "endpoint": "https://provider.example.com/tmp",
                            "context_match": "on",
                            "identity_match": "on",
                            "countries": "US",
                            "uid_types": "uid2",
                            "timeout_ms": "50",
                            "auth_type": "bearer",
                            "auth_credentials": "my-secret-token",
                        },
                        follow_redirects=False,
                    )

        assert response.status_code == 302
        mock_uow.tmp_providers.create_from_fields.assert_called_once_with(
            name="Auth Provider",
            endpoint="https://provider.example.com/tmp",
            context_match=True,
            identity_match=True,
            countries=["US"],
            uid_types=["uid2"],
            properties=None,
            timeout_ms=50,
            priority=0,
            status="active",
            auth_type="bearer",
            auth_credentials="my-secret-token",
        )

    def test_edit_get_includes_auth_fields_in_provider_dict(self):
        """GET /tmp-providers/<id>/edit includes auth_type and auth_credentials in template context.

        Uses a real TMPProvider instance (not a MagicMock) so that to_dict() is
        exercised against the production implementation — avoids the missing-properties
        regression that was caught in review (same pattern as test_tmp_providers_discovery_route.py).
        """
        client = _make_tmp_provider_client()

        # Real TMPProvider instance — to_dict() is the production implementation.
        existing_provider = TMPProvider(
            provider_id="test-uuid-1234",
            tenant_id="default",
            name="Test Provider",
            endpoint="https://provider.example.com/tmp",
            context_match=True,
            identity_match=True,
            countries=["US", "GB"],
            uid_types=["uid2", "id5"],
            properties=None,
            timeout_ms=50,
            priority=0,
            status="active",
            auth_type="bearer",
        )
        # Set auth_credentials via the property so the encryption path is exercised.
        from cryptography.fernet import Fernet

        _key = Fernet.generate_key().decode()
        with patch.dict(os.environ, {"ENCRYPTION_KEY": _key}):
            existing_provider.auth_credentials = "stored-token"

            mock_uow_cls, mock_uow = _make_blueprint_uow()
            mock_tenant = mock_uow.tenant_config.get_tenant.return_value
            mock_uow.tmp_providers.get_by_id.return_value = existing_provider
            with patch("src.admin.blueprints.tmp_providers.TMPProviderUoW", mock_uow_cls):
                with patch("src.admin.blueprints.tmp_providers.render_template") as mock_render:
                    mock_render.return_value = "<html/>"
                    with patch.dict(os.environ, {"ADCP_AUTH_TEST_MODE": "true"}):
                        response = client.get(
                            "/tenant/default/tmp-providers/test-uuid-1234/edit",
                        )

        assert response.status_code == 200
        # Production calls to_dict(include_conditional=False) then overwrites
        # list fields with comma-separated strings and adds auth fields with
        # placeholder masking (credentials are never echoed back to the browser).
        mock_render.assert_called_once_with(
            "tmp_provider_form.html",
            tenant=mock_tenant,
            tenant_id="default",
            tenant_name="Default Tenant",
            provider={
                "provider_id": "test-uuid-1234",
                "name": "Test Provider",
                "endpoint": "https://provider.example.com/tmp",
                "context_match": True,
                "identity_match": True,
                "countries": "US,GB",
                "uid_types": "uid2,id5",
                "properties": "",
                "timeout_ms": 50,
                "priority": 0,
                "status": "active",
                "auth_type": "bearer",
                "auth_credentials": "••••••••",
            },
        )

    def test_edit_post_preserves_existing_credentials_when_empty_submitted(self):
        """POST /tmp-providers/<id>/edit with empty auth_credentials preserves existing value."""
        client = _make_tmp_provider_client()

        existing_provider = _make_mock_provider()
        existing_provider.auth_type = "bearer"
        existing_provider.auth_credentials = "existing-secret"

        mock_uow_cls, mock_uow = _make_blueprint_uow()
        mock_uow.tmp_providers.get_by_id.return_value = existing_provider
        with patch("src.admin.blueprints.tmp_providers.TMPProviderUoW", mock_uow_cls):
            with patch("src.core.security.url_validator.socket.gethostbyname", return_value="93.184.216.34"):
                with patch.dict(os.environ, {"ADCP_AUTH_TEST_MODE": "true"}):
                    response = client.post(
                        "/tenant/default/tmp-providers/test-uuid-1234/edit",
                        data={
                            "name": "Existing Provider",
                            "endpoint": "https://provider.example.com/tmp",
                            "context_match": "on",
                            "identity_match": "on",
                            "countries": "US",
                            "uid_types": "uid2",
                            "timeout_ms": "50",
                            "status": "active",
                            "auth_type": "bearer",
                            "auth_credentials": "",  # empty — should preserve existing
                        },
                        follow_redirects=False,
                    )

        assert response.status_code == 302
        # Production uses update_fields() — verify auth_credentials was NOT
        # included in the kwargs (empty submission preserves existing value).
        mock_uow.tmp_providers.update_fields.assert_called_once_with(
            "test-uuid-1234",
            name="Existing Provider",
            endpoint="https://provider.example.com/tmp",
            context_match=True,
            identity_match=True,
            countries=["US"],
            uid_types=["uid2"],
            properties=None,
            timeout_ms=50,
            priority=0,
            status="active",
            auth_type="bearer",
            # auth_credentials intentionally absent — empty submission preserves existing value
        )

    def test_edit_post_updates_credentials_when_new_value_submitted(self):
        """POST /tmp-providers/<id>/edit with non-empty auth_credentials updates the value."""
        client = _make_tmp_provider_client()

        existing_provider = _make_mock_provider()
        existing_provider.auth_type = "bearer"
        existing_provider.auth_credentials = "old-secret"

        mock_uow_cls, mock_uow = _make_blueprint_uow()
        mock_uow.tmp_providers.get_by_id.return_value = existing_provider
        with patch("src.admin.blueprints.tmp_providers.TMPProviderUoW", mock_uow_cls):
            with patch("src.core.security.url_validator.socket.gethostbyname", return_value="93.184.216.34"):
                with patch.dict(os.environ, {"ADCP_AUTH_TEST_MODE": "true"}):
                    response = client.post(
                        "/tenant/default/tmp-providers/test-uuid-1234/edit",
                        data={
                            "name": "Existing Provider",
                            "endpoint": "https://provider.example.com/tmp",
                            "context_match": "on",
                            "identity_match": "on",
                            "countries": "US",
                            "uid_types": "uid2",
                            "timeout_ms": "50",
                            "status": "active",
                            "auth_type": "bearer",
                            "auth_credentials": "new-secret",
                        },
                        follow_redirects=False,
                    )

        assert response.status_code == 302
        # Production uses update_fields() — verify auth_credentials IS included
        # with the new value when a non-empty credential is submitted.
        mock_uow.tmp_providers.update_fields.assert_called_once_with(
            "test-uuid-1234",
            name="Existing Provider",
            endpoint="https://provider.example.com/tmp",
            context_match=True,
            identity_match=True,
            countries=["US"],
            uid_types=["uid2"],
            properties=None,
            timeout_ms=50,
            priority=0,
            status="active",
            auth_type="bearer",
            auth_credentials="new-secret",
        )


# TestTMPProviderToDict lives in test_tmp_providers_discovery_route.py (the
# canonical home for model contract tests — uses _tmp_helpers._make_provider).
# Keeping a second copy here would require parallel edits on every to_dict()
# change and one copy would inevitably drift (CLAUDE.md DRY invariant).


# ---------------------------------------------------------------------------
# VALID_UID_TYPES guard — pins the frozenset to the pinned AdCP schema enum
# ---------------------------------------------------------------------------


class TestValidUidTypesMatchesPinnedSchema:
    """VALID_UID_TYPES must equal the uid-type enum in the pinned AdCP SDK.

    Authority: ``adcp.types.generated_poc.enums.uid_type.UidType`` (adcp==5.7.0,
    pinned in pyproject.toml).  No ``pytest.skip`` fallback — if the import
    fails the guard must fail loudly so the drift is caught immediately.
    """

    def test_valid_uid_types_matches_pinned_schema(self):
        """VALID_UID_TYPES frozenset equals UidType enum values in the pinned adcp SDK.

        Authority: adcp.types.generated_poc.enums.uid_type.UidType (adcp==5.7.0).
        Schema path in SDK: adcp/types/generated_poc/enums/uid_type.py.
        """
        from adcp.types.generated_poc.enums.uid_type import UidType  # type: ignore[import]

        from src.admin.blueprints.tmp_providers import VALID_UID_TYPES

        sdk_uid_types = frozenset(v.value for v in UidType)

        assert VALID_UID_TYPES == sdk_uid_types, (
            f"VALID_UID_TYPES diverges from the pinned adcp SDK (adcp==5.7.0).\n"
            f"  In SDK but not in VALID_UID_TYPES: {sdk_uid_types - VALID_UID_TYPES}\n"
            f"  In VALID_UID_TYPES but not in SDK: {VALID_UID_TYPES - sdk_uid_types}\n"
            f"  Update VALID_UID_TYPES in src/admin/blueprints/tmp_providers.py to match."
        )
