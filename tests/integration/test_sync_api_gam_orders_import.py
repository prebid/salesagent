"""src/admin/sync_api.py's 4 GAMOrdersService imports must actually resolve.

GH #1802: all 4 route handlers do ``from gam_orders_service import
GAMOrdersService`` -- a bare, non-absolute import. No ``gam_orders_service``
module exists at the repo root; the real module is
``src.services.gam_orders_service``. Every route wraps the call in a broad
``except Exception`` that converts the resulting ``ModuleNotFoundError`` into
a generic 500 JSON response, masking the breakage.

Each case below drives the REAL Flask route through a real HTTP request
(``app.test_client()``), not a unit-level call to the import statement
itself -- an AST scan would catch the wrong import spelling but not prove the
route actually works end to end once fixed. The 3 GET routes need no GAM
credentials (they only read previously-synced ``GAMOrder``/``GAMLineItem``
rows from Postgres) and must return 200 with real (empty) results once fixed.
The POST sync route legitimately cannot complete without real GAM credentials
in a test environment, so its case asserts only on the specific regression
(the response no longer blames a missing ``gam_orders_service`` module) --
it may still fail for the unrelated, expected reason of having no live GAM
auth configured.
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

TENANT_ID = "sync_api_gam_import"
API_KEY = "sk-sync-api-gam-import-test"
BAD_IMPORT_MARKER = "gam_orders_service"


@pytest.fixture
def seeded_tenant(integration_db):
    """A committed tenant (+ GAM adapter config) for the sync routes to read."""
    from tests.factories import AdapterConfigFactory, TenantFactory
    from tests.harness._base import IntegrationEnv

    with IntegrationEnv() as env:
        tenant = TenantFactory(tenant_id=TENANT_ID, name="Sync API GAM Import", subdomain="syncapigamimport")
        AdapterConfigFactory(
            tenant=tenant,
            adapter_type="google_ad_manager",
            gam_network_code="123456",
        )
        yield env


@pytest.fixture
def sync_api_client(seeded_tenant, monkeypatch):
    """Client for the sync_api blueprint, authenticated by env-var key.

    Mirrors tests/integration/test_admin_ingest_url_policy.py's
    ``management_api_client`` fixture -- a standalone Flask app registering
    only the blueprint under test, so a failure here can only be this
    blueprint's own bug, not something else in the full admin app.

    ``sync_api.db_session`` is ``src.services.gam_inventory_service``'s
    module-level ``scoped_session``, bound to an ``engine`` created ONCE at
    that module's first import -- whichever ``DATABASE_URL`` was live then,
    not the per-test database ``integration_db`` provisions afterward. Left
    alone, every DB read through this blueprint hits the wrong (often
    nonexistent) database, independent of anything this test file is about.
    Rebinding it here to a fresh engine on the CURRENT ``DATABASE_URL``
    (already pointed at this test's isolated database by ``seeded_tenant``)
    is test-side compensation for that pre-existing gap -- not a production
    fix, out of GH #1802's scope (a bad import, not a session
    lifecycle bug).
    """
    import os

    from flask import Flask
    from sqlalchemy import create_engine
    from sqlalchemy.orm import scoped_session, sessionmaker

    from src.admin import sync_api as sync_api_module
    from src.admin.sync_api import sync_api

    monkeypatch.setattr(
        sync_api_module,
        "db_session",
        scoped_session(sessionmaker(bind=create_engine(os.environ["DATABASE_URL"]))),
    )

    monkeypatch.setenv("SYNC_API_KEY", API_KEY)
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(sync_api, url_prefix="/api/sync")
    return app.test_client()


def _get(client, path: str):
    return client.get(f"/api/sync{path}", headers={"X-API-Key": API_KEY})


def _post(client, path: str):
    return client.post(f"/api/sync{path}", headers={"X-API-Key": API_KEY})


class TestGetTenantOrders:
    """GET /tenant/<id>/orders -- needs no GAM credentials, only a real DB read."""

    def test_route_returns_orders_not_a_missing_module_error(self, sync_api_client):
        response = _get(sync_api_client, f"/tenant/{TENANT_ID}/orders")

        body = response.get_json()
        assert BAD_IMPORT_MARKER not in str(body), f"import still broken: {body}"
        assert response.status_code == 200, body
        assert body == {"total": 0, "orders": []}


class TestGetOrderDetails:
    """GET /tenant/<id>/orders/<order_id> -- no matching order, but the import must resolve."""

    def test_route_returns_not_found_not_a_missing_module_error(self, sync_api_client):
        response = _get(sync_api_client, f"/tenant/{TENANT_ID}/orders/order_does_not_exist")

        body = response.get_json()
        assert BAD_IMPORT_MARKER not in str(body), f"import still broken: {body}"
        assert response.status_code == 404, body
        assert body == {"error": "Order not found"}


class TestGetTenantLineItems:
    """GET /tenant/<id>/line-items -- needs no GAM credentials, only a real DB read."""

    def test_route_returns_line_items_not_a_missing_module_error(self, sync_api_client):
        response = _get(sync_api_client, f"/tenant/{TENANT_ID}/line-items")

        body = response.get_json()
        assert BAD_IMPORT_MARKER not in str(body), f"import still broken: {body}"
        assert response.status_code == 200, body
        assert body == {"total": 0, "line_items": []}


class TestSyncTenantOrders:
    """POST /tenant/<id>/orders/sync -- cannot complete without real GAM credentials.

    Only the specific regression is graded: the failure must no longer be
    caused by a missing ``gam_orders_service`` module. It is legitimate for
    this to still fail for an unrelated reason (no live GAM auth configured
    in a test environment).
    """

    def test_route_fails_for_gam_auth_not_a_missing_module_error(self, sync_api_client):
        response = _post(sync_api_client, f"/tenant/{TENANT_ID}/orders/sync")

        body = response.get_json()
        assert BAD_IMPORT_MARKER not in str(body), f"import still broken: {body}"
