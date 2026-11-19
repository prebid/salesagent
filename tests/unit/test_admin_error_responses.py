"""Admin routes answer failures with fixed text, never the exception's own text.

Each test drives the real route through a Flask test client with a super-admin
session, so the decorators and the route's own ``except`` clauses are the ones
production runs.
"""

import re
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from src.core.config import get_settings
from src.services.auth_config_service import DiscoveryUrlRequired
from src.services.background_sync_service import SyncAlreadyRunning
from src.services.gcp_service_account_service import ServiceAccountAlreadyExists, TenantAdapterConfigMissing
from tests.helpers.admin_session import admin_auth_session

_SECRET = "tenant t1 owner a@b.c DETAIL: hunter2"
_REFERENCE_TAIL = re.compile(r"\. The details have been logged \(reference [0-9a-f]{8}\)\.$")


@pytest.fixture
def client():
    from src.admin.app import create_app

    app = create_app({"TESTING": True, "SECRET_KEY": "test-secret", "WTF_CSRF_ENABLED": False})
    test_client = app.test_client()
    admin_auth_session(test_client, "default")
    return test_client


@pytest.mark.parametrize(
    "body",
    [{"data": "{not json"}, {"json": [1]}],
    ids=["malformed-json", "json-array"],
)
def test_ai_connection_test_with_a_bad_body_answers_with_a_reference(client, body):
    """A body that fails before the model is read still gets the generic 400 (dba3b3d6)."""
    response = client.post(
        "/tenant/default/settings/ai/test",
        content_type="application/json",
        **({"data": body["data"]} if "data" in body else {"json": body["json"]}),
    )

    assert response.status_code == 400
    payload = response.get_json()
    assert payload["success"] is False
    assert payload["error"].startswith("AI connection test failed")
    assert _REFERENCE_TAIL.search(payload["error"])


def test_inventory_sync_already_running_names_the_running_sync(client):
    session = MagicMock()
    session.scalars.return_value.first.side_effect = [
        MagicMock(ad_server="google_ad_manager"),
        MagicMock(gam_network_code="123"),
    ]
    refusal = SyncAlreadyRunning("default", "sync_default_1", datetime.now(UTC))

    with (
        patch("src.admin.blueprints.inventory.get_db_session") as db,
        patch("src.admin.blueprints.inventory.start_inventory_sync_background", side_effect=refusal),
    ):
        db.return_value.__enter__.return_value = session
        response = client.post("/api/tenant/default/inventory/sync", json={})

    assert response.status_code == 400
    assert response.get_json() == {"error": SyncAlreadyRunning.MESSAGE, "sync_id": "sync_default_1"}


@pytest.mark.parametrize(
    "refusal",
    [TenantAdapterConfigMissing(_SECRET), ServiceAccountAlreadyExists(_SECRET)],
    ids=["no-adapter-config", "already-exists"],
)
def test_service_account_refusal_says_which_one(client, refusal):
    with (
        patch.object(get_settings().auth, "gcp_project_id", "test-project"),
        patch("src.services.gcp_service_account_service.GCPServiceAccountService") as service,
    ):
        service.return_value.create_service_account_for_tenant.side_effect = refusal
        response = client.post("/tenant/default/gam/create-service-account")

    assert response.status_code == 400
    assert response.get_json() == {"success": False, "error": type(refusal).MESSAGE}


def test_custom_oidc_provider_without_discovery_url_gets_the_refusal(client):
    """The real ``save_oidc_config`` refuses before touching the database."""
    with patch("src.admin.blueprints.oidc.get_or_create_auth_config"):
        response = client.post(
            "/auth/oidc/tenant/default/config",
            json={"provider": "custom", "client_id": "c", "client_secret": "s"},
        )

    assert response.status_code == 400
    assert response.get_json() == {"error": DiscoveryUrlRequired.MESSAGE}


def test_other_oidc_save_failures_get_the_generic_answer(client):
    """A missing ENCRYPTION_KEY is a ValueError too; the app error handler answers it, not a discovery-URL hint."""
    with (
        patch("src.admin.blueprints.oidc.get_or_create_auth_config"),
        patch("src.admin.blueprints.oidc.save_oidc_config", side_effect=ValueError(_SECRET)),
    ):
        response = client.post(
            "/auth/oidc/tenant/default/config",
            json={"provider": "google", "client_id": "c", "client_secret": "s"},
        )

    assert response.status_code == 500
    error = response.get_json()["error"]
    assert error.startswith("An unexpected error occurred")
    assert _REFERENCE_TAIL.search(error)
    assert _SECRET not in error


@pytest.fixture
def failing_client():
    """The real admin app, plus one route that raises, so the app's own error handler answers."""
    from werkzeug.exceptions import NotFound

    from src.admin.app import create_app

    app = create_app({"TESTING": True, "SECRET_KEY": "test-secret", "WTF_CSRF_ENABLED": False})

    def boom():
        raise RuntimeError(_SECRET)

    def missing():
        raise NotFound()

    app.add_url_rule("/_test/boom", "test_boom", boom, methods=["GET", "POST"])
    app.add_url_rule("/_test/missing", "test_missing", missing)
    return app.test_client()


def test_unhandled_error_answers_json_with_a_reference(failing_client):
    response = failing_client.get("/_test/boom", headers={"Accept": "*/*"})

    assert response.status_code == 500
    body = response.get_json()
    assert body["success"] is False
    assert body["error"].startswith("An unexpected error occurred")
    assert _REFERENCE_TAIL.search(body["error"])
    assert _SECRET not in response.get_data(as_text=True)


def test_unhandled_error_in_a_form_post_flashes_and_goes_back(failing_client):
    response = failing_client.post(
        "/_test/boom", headers={"Accept": "text/html", "Referer": "http://localhost/tenant/t1/settings"}
    )

    assert response.status_code == 302
    assert response.headers["Location"] == "/tenant/t1/settings"
    with failing_client.session_transaction() as sess:
        ((category, message),) = sess["_flashes"]
    assert category == "error"
    assert _REFERENCE_TAIL.search(message)
    assert _SECRET not in message


@pytest.mark.parametrize(
    "referer",
    [
        None,
        "https://elsewhere.example/page",
        "http://localhost//evil.example/page",
        "http://localhost/\\evil.example/page",
    ],
    ids=["no-referer", "other-site", "double-slash-path", "backslash-path"],
)
def test_unhandled_error_without_a_same_site_page_renders_the_error_page(failing_client, referer):
    headers = {"Accept": "text/html"} | ({"Referer": referer} if referer else {})
    response = failing_client.get("/_test/boom", headers=headers)

    assert response.status_code == 500
    page = response.get_data(as_text=True)
    assert "The details have been logged (reference " in page
    assert _SECRET not in page


def test_http_exceptions_pass_through(failing_client):
    assert failing_client.get("/_test/missing").status_code == 404


def test_same_site_referrer_keeps_only_path_and_query(failing_client):
    response = failing_client.post(
        "/_test/boom", headers={"Accept": "text/html", "Referer": "http://localhost/tenant/t1/products?page=2"}
    )

    assert response.status_code == 302
    assert response.headers["Location"] == "/tenant/t1/products?page=2"


def test_adapter_config_validation_answers_locations_not_input(client):
    """pydantic's text and its error dicts echo the submitted value, which can be a credential."""
    secret = "sk-live-0123456789abcdef"
    response = client.post(
        "/api/tenant/default/adapter-config",
        json={"adapter_type": "mock", "config": {"manual_approval_required": secret}},
    )

    assert response.status_code == 400
    body = response.get_json()
    assert body["error"] == "Validation error"
    assert [e["loc"] for e in body["errors"]] == [["manual_approval_required"]]
    assert secret not in response.get_data(as_text=True)
