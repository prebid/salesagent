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
