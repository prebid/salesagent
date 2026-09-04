"""Unit tests for the TMP Provider admin blueprint — what this LAYER adds.

Registration *validity* (which values are legal) belongs to
``TMPProviderRegistration`` and is graded in
``tests/unit/test_tmp_provider_registration.py``. This file grades only what the
blueprint itself owns:

- one proof that a rejected registration flashes, bounces, and does not write,
  driven by ``@parametrize`` over the invalid payloads — this used to be eight
  byte-identical bodies each re-enumerating an invariant the record's suite now
  owns (#1197 review)
- CSV/checkbox/int form shape reaching ``create_from_fields`` / ``update_fields``
- the credential-preservation rule on edit, and the masked (never echoed) render
- the render kwargs of the list and add-GET branches
- the two error helpers' 500 / flash+redirect shapes
- CRUD route responses (deactivate, delete, health check) and their JSON bodies

Note: Discovery endpoint tests are in test_tmp_providers_discovery_route.py
(the canonical discovery endpoint is the FastAPI route, not Flask).
"""

import os
from unittest.mock import patch

import pytest

from src.admin.blueprints.tmp_providers import _form_render_context
from src.core.database.models import TMPProvider
from src.core.schemas.tmp_provider import VALID_STATUSES, VALID_UID_TYPES
from tests.helpers.admin_client import make_super_admin_client
from tests.unit._tmp_helpers import make_blueprint_uow, make_mock_provider


def _make_tmp_provider_client():
    """Create a Flask test client authenticated as super admin for TMP provider endpoints."""
    return make_super_admin_client()


# A public hostname is fine again: the registration verdict is the seam's
# DNS-FREE one (EgressPolicy.check_registration), so no test needs a resolver
# stub or an IP literal to get a deterministic answer.
_SAFE_ENDPOINT = "https://provider.example.com/tmp"

# One valid add payload; each rejection case overrides exactly one thing, so a
# failure names the rejected field rather than a setup mistake.
_VALID_ADD_FORM: dict[str, str] = {
    "name": "Test Provider",
    "endpoint": _SAFE_ENDPOINT,
    "context_match": "on",
    "timeout_ms": "50",
}

# WHICH values are invalid is the record's contract (graded in
# test_tmp_provider_registration.py). This list exists only to drive the ONE
# layer-level claim below across the rejection paths a form can produce —
# including the two the record cannot see (non-numeric int, unsafe URL).
_REJECTED_ADD_FORMS: list[tuple[str, dict[str, str]]] = [
    ("ssrf_endpoint", {"endpoint": "http://host.docker.internal:9999"}),
    ("missing_endpoint", {"endpoint": ""}),
    ("missing_name", {"name": ""}),
    ("non_numeric_timeout", {"timeout_ms": "not-a-number"}),
    ("invalid_status", {"status": "bogus_status"}),
    ("identity_match_without_countries", {"identity_match": "on", "countries": "", "uid_types": "uid2"}),
    ("identity_match_without_uid_types", {"identity_match": "on", "countries": "US", "uid_types": ""}),
    ("invalid_uid_type", {"identity_match": "on", "countries": "US", "uid_types": "bogus_type"}),
    ("lowercase_country", {"identity_match": "on", "countries": "usa", "uid_types": "uid2"}),
]


def _post_add(form: dict[str, str], mock_uow_cls) -> object:
    """POST the add form with DNS stubbed public, returning the response."""
    client = _make_tmp_provider_client()
    with (
        patch("src.admin.blueprints.tmp_providers.TMPProviderUoW", mock_uow_cls),
        patch.dict(os.environ, {"ADCP_AUTH_TEST_MODE": "true"}),
    ):
        return client.post("/tenant/default/tmp-providers/add", data=form, follow_redirects=False)


class TestRejectedRegistrationIsFlashedAndNotWritten:
    """The layer's rejection contract, proved once over every rejection path.

    A rejected registration must (a) bounce back to the add form and (b) NOT
    persist. The bounce alone is not enough: a regression that flashes,
    redirects AND writes the row would pass on the redirect assertions, which is
    why ``create_from_fields.assert_not_called()`` is here rather than in a
    comment.
    """

    @pytest.mark.parametrize("form_overrides", [pytest.param(o, id=name) for name, o in _REJECTED_ADD_FORMS])
    def test_rejected_add_flashes_bounces_and_does_not_write(self, form_overrides):
        mock_uow_cls, mock_uow = make_blueprint_uow()

        response = _post_add({**_VALID_ADD_FORM, **form_overrides}, mock_uow_cls)

        assert response.status_code == 302
        assert "add" in response.headers.get("Location", "")
        mock_uow.tmp_providers.create_from_fields.assert_not_called()


class TestTMPProviderAddSSRF:
    """SSRF validation is wired into the add endpoint."""

    def test_add_accepts_safe_public_url(self):
        """POST /tmp-providers/add with a safe public URL must proceed past SSRF check."""
        client = _make_tmp_provider_client()

        mock_uow_cls, mock_uow = make_blueprint_uow()
        with patch("src.admin.blueprints.tmp_providers.TMPProviderUoW", mock_uow_cls):
            with patch.dict(os.environ, {"ADCP_AUTH_TEST_MODE": "true"}):
                response = client.post(
                    "/tenant/default/tmp-providers/add",
                    data={
                        "name": "Safe Provider",
                        "endpoint": _SAFE_ENDPOINT,
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

        existing_provider = make_mock_provider()

        mock_uow_cls, mock_uow = make_blueprint_uow()
        mock_uow.tmp_providers.get_by_id.return_value = existing_provider
        with patch("src.admin.blueprints.tmp_providers.TMPProviderUoW", mock_uow_cls):
            with patch.dict(os.environ, {"ADCP_AUTH_TEST_MODE": "true"}):
                response = client.post(
                    "/tenant/default/tmp-providers/prov_test_1234/edit",
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

        existing_provider = make_mock_provider()

        mock_uow_cls, mock_uow = make_blueprint_uow()
        mock_uow.tmp_providers.get_by_id.return_value = existing_provider
        with patch("src.admin.blueprints.tmp_providers.TMPProviderUoW", mock_uow_cls):
            with patch.dict(os.environ, {"ADCP_AUTH_TEST_MODE": "true"}):
                response = client.post(
                    "/tenant/default/tmp-providers/prov_test_1234/edit",
                    data={
                        "name": "Existing Provider",
                        "endpoint": _SAFE_ENDPOINT,
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
            "prov_test_1234",
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

    def test_add_passes_status_to_create_from_fields(self):
        """POST /tmp-providers/add with explicit status passes it to create_from_fields."""
        client = _make_tmp_provider_client()

        mock_uow_cls, mock_uow = make_blueprint_uow()
        with patch("src.admin.blueprints.tmp_providers.TMPProviderUoW", mock_uow_cls):
            with patch.dict(os.environ, {"ADCP_AUTH_TEST_MODE": "true"}):
                response = client.post(
                    "/tenant/default/tmp-providers/add",
                    data={
                        "name": "Draining Provider",
                        "endpoint": _SAFE_ENDPOINT,
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


class TestTMPProviderDeactivate:
    """Deactivate endpoint sets status='inactive' via repository."""

    def test_deactivate_returns_success_json(self):
        """POST /tmp-providers/<id>/deactivate returns JSON success."""
        client = _make_tmp_provider_client()

        existing_provider = make_mock_provider()

        mock_uow_cls, mock_uow = make_blueprint_uow()
        mock_uow.tmp_providers.deactivate.return_value = existing_provider
        with patch("src.admin.blueprints.tmp_providers.TMPProviderUoW", mock_uow_cls):
            with patch.dict(os.environ, {"ADCP_AUTH_TEST_MODE": "true"}):
                response = client.post(
                    "/tenant/default/tmp-providers/prov_test_1234/deactivate",
                )

        assert response.status_code == 200
        data = response.get_json()
        assert data["success"] is True
        mock_uow.tmp_providers.deactivate.assert_called_once_with("prov_test_1234")

    def test_deactivate_returns_404_for_missing_provider(self):
        """POST /tmp-providers/<id>/deactivate returns 404 when provider not found."""
        client = _make_tmp_provider_client()

        mock_uow_cls, mock_uow = make_blueprint_uow()
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

        existing_provider = make_mock_provider()

        mock_uow_cls, mock_uow = make_blueprint_uow()
        mock_uow.tmp_providers.get_by_id.return_value = existing_provider
        mock_uow.tmp_providers.delete.return_value = True
        with patch("src.admin.blueprints.tmp_providers.TMPProviderUoW", mock_uow_cls):
            with patch.dict(os.environ, {"ADCP_AUTH_TEST_MODE": "true"}):
                response = client.delete(
                    "/tenant/default/tmp-providers/prov_test_1234/delete",
                )

        assert response.status_code == 200
        data = response.get_json()
        assert data["success"] is True
        mock_uow.tmp_providers.delete.assert_called_once_with("prov_test_1234")

    def test_delete_returns_404_for_missing_provider(self):
        """DELETE /tmp-providers/<id>/delete returns 404 when provider not found."""
        client = _make_tmp_provider_client()

        mock_uow_cls, mock_uow = make_blueprint_uow()
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

        existing_provider = make_mock_provider()
        existing_provider.health_status = "healthy"
        existing_provider.last_health_checked_at = datetime(2026, 5, 25, 12, 0, 0, tzinfo=UTC)

        mock_uow_cls, mock_uow = make_blueprint_uow()
        mock_uow.tmp_providers.get_by_id.return_value = existing_provider
        with patch("src.admin.blueprints.tmp_providers.TMPProviderUoW", mock_uow_cls):
            with patch.dict(os.environ, {"ADCP_AUTH_TEST_MODE": "true"}):
                response = client.get(
                    "/tenant/default/tmp-providers/prov_test_1234/health",
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

        existing_provider = make_mock_provider()
        existing_provider.health_status = "unhealthy"
        existing_provider.last_health_checked_at = datetime(2026, 5, 25, 12, 0, 0, tzinfo=UTC)

        mock_uow_cls, mock_uow = make_blueprint_uow()
        mock_uow.tmp_providers.get_by_id.return_value = existing_provider
        with patch("src.admin.blueprints.tmp_providers.TMPProviderUoW", mock_uow_cls):
            with patch.dict(os.environ, {"ADCP_AUTH_TEST_MODE": "true"}):
                response = client.get(
                    "/tenant/default/tmp-providers/prov_test_1234/health",
                )

        assert response.status_code == 200
        data = response.get_json()
        # `success` means "request served" — the SAME meaning it has on the
        # deactivate/delete siblings — and never the health verdict. It used to
        # flip meaning between the two branches of this route while the JS read it
        # as the verdict, which reported a never-probed provider as healthy
        # (#1197 review).
        assert data["success"] is True
        assert data["status"] == "unhealthy"

    def test_health_check_returns_pending_when_never_checked(self):
        """GET /tmp-providers/<id>/health returns pending when health_status is None."""
        client = _make_tmp_provider_client()

        existing_provider = make_mock_provider()
        existing_provider.health_status = None
        existing_provider.last_health_checked_at = None

        mock_uow_cls, mock_uow = make_blueprint_uow()
        mock_uow.tmp_providers.get_by_id.return_value = existing_provider
        with patch("src.admin.blueprints.tmp_providers.TMPProviderUoW", mock_uow_cls):
            with patch.dict(os.environ, {"ADCP_AUTH_TEST_MODE": "true"}):
                response = client.get(
                    "/tenant/default/tmp-providers/prov_test_1234/health",
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

        mock_uow_cls, mock_uow = make_blueprint_uow()
        with patch("src.admin.blueprints.tmp_providers.TMPProviderUoW", mock_uow_cls):
            with patch.dict(os.environ, {"ADCP_AUTH_TEST_MODE": "true"}):
                response = client.post(
                    "/tenant/default/tmp-providers/add",
                    data={
                        "name": "Auth Provider",
                        "endpoint": _SAFE_ENDPOINT,
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

    def test_edit_post_preserves_existing_credentials_when_empty_submitted(self):
        """POST /tmp-providers/<id>/edit with empty auth_credentials preserves existing value."""
        client = _make_tmp_provider_client()

        existing_provider = make_mock_provider()
        existing_provider.auth_type = "bearer"
        existing_provider.auth_credentials = "existing-secret"

        mock_uow_cls, mock_uow = make_blueprint_uow()
        mock_uow.tmp_providers.get_by_id.return_value = existing_provider
        with patch("src.admin.blueprints.tmp_providers.TMPProviderUoW", mock_uow_cls):
            with patch.dict(os.environ, {"ADCP_AUTH_TEST_MODE": "true"}):
                response = client.post(
                    "/tenant/default/tmp-providers/prov_test_1234/edit",
                    data={
                        "name": "Existing Provider",
                        "endpoint": _SAFE_ENDPOINT,
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
            "prov_test_1234",
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

        existing_provider = make_mock_provider()
        existing_provider.auth_type = "bearer"
        existing_provider.auth_credentials = "old-secret"

        mock_uow_cls, mock_uow = make_blueprint_uow()
        mock_uow.tmp_providers.get_by_id.return_value = existing_provider
        with patch("src.admin.blueprints.tmp_providers.TMPProviderUoW", mock_uow_cls):
            with patch.dict(os.environ, {"ADCP_AUTH_TEST_MODE": "true"}):
                response = client.post(
                    "/tenant/default/tmp-providers/prov_test_1234/edit",
                    data={
                        "name": "Existing Provider",
                        "endpoint": _SAFE_ENDPOINT,
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
            "prov_test_1234",
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


# TestTMPProviderSerializers lives in test_tmp_providers_discovery_route.py (the
# canonical home for model contract tests — uses _tmp_helpers._make_provider).
# Keeping a second copy here would require parallel edits on every serializer
# change and one copy would inevitably drift (CLAUDE.md DRY invariant).


class TestListPageRendersTheRealTemplate:
    """The list page is graded by rendering it, not by asserting render kwargs.

    Every HTML-rendering route here used to be tested with
    ``patch("…tmp_providers.render_template")``, so the assertions were on the
    mapping handed to a stand-in renderer and ``templates/tmp_providers.html``
    was rendered by no test at all. That is how the auth badge shipped broken: the
    template reads ``provider.auth_type``, the view shape did not carry it, and an
    exact-kwargs assertion with ``auth_type`` visibly absent passed. The sibling
    admin suites already assert on ``response.data`` (#1197 review).
    """

    @staticmethod
    def _provider(**overrides) -> TMPProvider:
        from datetime import UTC, datetime

        fields = {
            "provider_id": "prov_list_1",
            "tenant_id": "default",
            "name": "Listed Provider",
            "endpoint": _SAFE_ENDPOINT,
            "context_match": True,
            "identity_match": False,
            "countries": None,
            "uid_types": None,
            "properties": None,
            "timeout_ms": 50,
            "priority": 0,
            "status": "active",
            "auth_type": None,
        }
        fields.update(overrides)
        provider = TMPProvider(**fields)
        provider.created_at = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
        return provider

    def _render_list(self, provider: TMPProvider) -> str:
        client = _make_tmp_provider_client()
        mock_uow_cls, mock_uow = make_blueprint_uow()
        mock_uow.tmp_providers.list_all.return_value = [provider]

        with (
            patch("src.admin.blueprints.tmp_providers.TMPProviderUoW", mock_uow_cls),
            patch.dict(os.environ, {"ADCP_AUTH_TEST_MODE": "true"}),
        ):
            response = client.get("/tenant/default/tmp-providers/")

        assert response.status_code == 200
        return response.data.decode()

    def test_credentialed_provider_renders_the_auth_badge(self):
        """The defect this test exists for: a provider WITH auth must not read "No Auth"."""
        html = self._render_list(self._provider(auth_type="bearer"))

        assert "🔑 Auth" in html
        assert "No Auth" not in html

    def test_uncredentialed_provider_renders_the_no_auth_badge(self):
        html = self._render_list(self._provider(auth_type=None))

        assert "No Auth" in html
        assert "🔑 Auth" not in html

    def test_active_provider_is_offered_the_deactivate_action(self):
        html = self._render_list(self._provider(status="active"))

        assert 'onclick="deactivateProvider(' in html

    def test_row_values_reach_the_page(self):
        """The row the operator reads — name, endpoint, status — is on the page."""
        html = self._render_list(self._provider(status="draining"))

        assert "Listed Provider" in html
        assert _SAFE_ENDPOINT in html
        assert "Draining" in html

    def test_deactivate_is_offered_per_the_status_mapping(self):
        """Which statuses can be deactivated comes from the mapping, not the template.

        A draining provider IS deactivatable (draining → inactive is a real
        transition the repository accepts); an inactive one is not, because there is
        nothing to deactivate. Asserted on the button's onclick, not the function
        name — the JS helper is always defined, only its invocation is conditional.
        """
        from src.admin.blueprints.tmp_providers import _status_presentation

        presentation = _status_presentation()
        for status, presented in presentation.items():
            html = self._render_list(self._provider(status=status))
            offered = 'onclick="deactivateProvider(' in html
            assert offered is bool(presented["deactivatable"]), (
                f"status {status!r}: Deactivate offered={offered}, mapping says {presented['deactivatable']}"
            )

    def test_an_unrecognized_status_renders_as_itself(self):
        """A status the SDK enum grows renders as its own value, not as "Inactive".

        The hand-written active/draining/else branch labelled every unknown status
        "Inactive" while the edit form — rendering from the same enum — offered it
        (#1197 review).
        """
        html = self._render_list(self._provider(status="quiescing"))

        assert "quiescing" in html
        assert "Inactive" not in html


class TestEditPageRendersTheRealTemplate:
    """The edit form is graded by rendering it too."""

    def test_selected_status_and_credential_mask_are_rendered(self):
        provider = TMPProvider(
            provider_id="prov_edit_1",
            tenant_id="default",
            name="Edit Provider",
            endpoint=_SAFE_ENDPOINT,
            context_match=True,
            identity_match=True,
            countries=["US", "GB"],
            uid_types=["uid2"],
            properties=None,
            timeout_ms=250,
            priority=1,
            status="draining",
            auth_type="bearer",
        )
        from cryptography.fernet import Fernet

        key = Fernet.generate_key().decode()
        with patch.dict(os.environ, {"ENCRYPTION_KEY": key}):
            provider.auth_credentials = "stored-token"

            client = _make_tmp_provider_client()
            mock_uow_cls, mock_uow = make_blueprint_uow()
            mock_uow.tmp_providers.get_by_id.return_value = provider

            with (
                patch("src.admin.blueprints.tmp_providers.TMPProviderUoW", mock_uow_cls),
                patch.dict(os.environ, {"ADCP_AUTH_TEST_MODE": "true"}),
            ):
                response = client.get("/tenant/default/tmp-providers/prov_edit_1/edit")

        assert response.status_code == 200
        html = response.data.decode()

        # The stored status is the selected option — rendered from VALID_STATUSES.
        assert 'value="draining" selected' in html
        # A stored credential is reported as PRESENT and never echoed.
        assert "(set — leave blank to keep)" in html
        assert "stored-token" not in html
        # CSV round-trip of the conditional arrays.
        assert "US,GB" in html
        # The numeric bounds come from the record, not from hand-typed attributes.
        assert 'min="5"' in html
        assert 'max="5000"' in html


class TestAddGetRendersTheEmptyForm:
    """The add-GET branch renders the form with no provider and both vocabularies.

    The vocabularies are the point: passing them is what keeps the UI's uid-type
    and status lists in step with the SDK-derived enums instead of the stale
    hand-typed copies the template used to carry (#1197 review).
    """

    def test_renders_the_empty_form_with_the_enum_vocabularies(self):
        """The add page renders, with every uid type and status offered.

        A real render, not an assertion on the mapping handed to a patched
        renderer: the vocabularies exist to keep the UI in step with the SDK enums,
        and only rendering shows that they reach the page (#1197 review).
        """
        client = _make_tmp_provider_client()
        mock_uow_cls, _mock_uow = make_blueprint_uow()

        with (
            patch("src.admin.blueprints.tmp_providers.TMPProviderUoW", mock_uow_cls),
            patch.dict(os.environ, {"ADCP_AUTH_TEST_MODE": "true"}),
        ):
            response = client.get("/tenant/default/tmp-providers/add")

        assert response.status_code == 200
        html = response.data.decode()

        # Every SDK uid type is documented on the page — including the two the
        # hand-written template list had gone stale on.
        for uid_type in VALID_UID_TYPES:
            assert uid_type in html, f"{uid_type} is missing from the add form"
        assert "rampid_derived" in html
        assert "world_id_nullifier" in html
        # Only the schemes the outbound call implements are offered.
        assert "api_key" not in html
        # An add form has no provider, so no credential-present hint.
        assert "(set — leave blank to keep)" not in html

    def test_the_rendered_vocabularies_cover_the_whole_enum(self):
        """Every SDK uid type and status reaches the template context.

        Pins the fix for the specific drift found in review: the template's
        hand-written list was missing ``rampid_derived`` and
        ``world_id_nullifier``, so the form rejected values the enum accepts.
        """
        context = _form_render_context()

        assert set(context["valid_uid_types"]) == set(VALID_UID_TYPES)
        assert set(context["valid_statuses"]) == set(VALID_STATUSES)


class TestEditGetSurvivesAnUnreadableCredential:
    """A rotated/corrupt ciphertext must render the form, not a 500.

    ``has_auth_credentials`` exists precisely so the edit render never goes
    through the decrypting getter. Swapping it back for ``auth_credentials``
    turns this page into a 500 for every operator whose encryption key was
    rotated — and until this test, that swap failed nothing (#1197 review).
    """

    def test_edit_get_renders_for_a_corrupt_credential(self):
        provider = TMPProvider(
            provider_id="prov_corrupt_1",
            tenant_id="default",
            name="Rotated Provider",
            endpoint=_SAFE_ENDPOINT,
            context_match=True,
            identity_match=False,
            countries=None,
            uid_types=None,
            properties=None,
            timeout_ms=50,
            priority=0,
            status="active",
            auth_type="bearer",
        )
        # The raw column, deliberately not written through the encrypting setter:
        # this is the on-disk state after a key rotation.
        provider._auth_credentials = "not-a-valid-fernet-token"

        client = _make_tmp_provider_client()
        mock_uow_cls, mock_uow = make_blueprint_uow()
        mock_uow.tmp_providers.get_by_id.return_value = provider

        with (
            patch("src.admin.blueprints.tmp_providers.TMPProviderUoW", mock_uow_cls),
            patch.dict(os.environ, {"ADCP_AUTH_TEST_MODE": "true"}),
        ):
            response = client.get("/tenant/default/tmp-providers/prov_corrupt_1/edit")

        # The page renders rather than 500ing, and reports the credential as set
        # without ever decrypting it.
        assert response.status_code == 200
        html = response.data.decode()
        assert "(set — leave blank to keep)" in html
        assert "not-a-valid-fernet-token" not in html


class TestErrorHelpers:
    """The two extracted error helpers' response shapes.

    Six call sites route through them, and until now deleting an entire
    ``except`` block failed nothing (#1197 review). Driven through a real route
    (an exception raised inside the handler) rather than by calling the helper
    directly, so the wiring is graded too.
    """

    def test_json_route_failure_returns_500_with_the_action_in_the_body(self):
        """``_log_and_500``: JSON 500 naming the action, from the deactivate route."""
        client = _make_tmp_provider_client()
        mock_uow_cls, mock_uow = make_blueprint_uow()
        mock_uow.tmp_providers.deactivate.side_effect = RuntimeError("db exploded")

        with (
            patch("src.admin.blueprints.tmp_providers.TMPProviderUoW", mock_uow_cls),
            patch.dict(os.environ, {"ADCP_AUTH_TEST_MODE": "true"}),
        ):
            response = client.post("/tenant/default/tmp-providers/prov_1/deactivate")

        assert response.status_code == 500
        assert response.get_json() == {"error": "Error deactivating TMP provider"}

    def test_flash_route_failure_redirects_to_tenant_settings(self):
        """``_log_flash_and_redirect``: flash + redirect, from the list route."""
        client = _make_tmp_provider_client()
        mock_uow_cls, mock_uow = make_blueprint_uow()
        mock_uow.tmp_providers.list_all.side_effect = RuntimeError("db exploded")

        with (
            patch("src.admin.blueprints.tmp_providers.TMPProviderUoW", mock_uow_cls),
            patch.dict(os.environ, {"ADCP_AUTH_TEST_MODE": "true"}),
        ):
            response = client.get("/tenant/default/tmp-providers/", follow_redirects=False)
            with client.session_transaction() as session:
                flashes = session.get("_flashes", [])

        assert response.status_code == 302
        assert "/settings" in response.headers["Location"]
        assert ("error", "Error loading TMP providers") in flashes


class TestAdminViewShape:
    """``_admin_view`` builds every key the list template reads.

    The direct counterpart to the rendered-page tests above: those prove the badge
    and the row values reach the HTML, this pins the mapping itself — including the
    two keys whose absence produced the "⚠️ No Auth" defect on every row
    (#1197 review).
    """

    def test_carries_the_auth_badge_inputs_and_the_null_conditionals(self):
        from datetime import UTC, datetime

        from src.admin.blueprints.tmp_providers import _admin_view

        provider = TMPProvider(
            provider_id="prov_view_1",
            tenant_id="default",
            name="Viewed Provider",
            endpoint=_SAFE_ENDPOINT,
            context_match=True,
            identity_match=False,
            countries=None,
            uid_types=None,
            properties=None,
            timeout_ms=50,
            priority=0,
            status="active",
            auth_type="bearer",
        )
        provider.created_at = datetime(2026, 3, 1, tzinfo=UTC)

        view = _admin_view(provider)

        # The badge inputs — the keys the ORM-side mapper used to omit.
        assert view["auth_type"] == "bearer"
        assert view["has_auth_credentials"] is False
        # `name` is carried here (it is NOT on the machine wire), and the three
        # conditional arrays are present as None rather than omitted: the list view
        # distinguishes "no restriction" from "not shown".
        assert view["name"] == "Viewed Provider"
        assert view["countries"] is None
        assert view["uid_types"] is None
        assert view["properties"] is None
        assert view["created_at"] == datetime(2026, 3, 1, tzinfo=UTC)
