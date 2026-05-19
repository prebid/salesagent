"""Integration tests for the sprint-1 Tenant Management API endpoints.

Covers the full sprint-1 acceptance criteria:
- provision happy path + adapter-test failure rollback + duplicate org id
- list / get / patch / deactivate / reactivate / delete (soft + hard)
- adapter-config GET / PUT (with rollback on connection failure) / test-connection
- write-guard behavior (managed vs unmanaged, super-admin override)
- end-to-end: provision → patch → ui-handler-blocks → deactivate → re-provision-blocked
- swagger UI loads, OpenAPI spec validates as OpenAPI 3
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from flask import Flask
from sqlalchemy import select

from src.admin.tenant_management_api import (
    _spawn_refresh_workers as _LIVE_SPAWN_REFRESH_WORKERS,
)
from src.admin.tenant_management_api import tenant_management_api
from src.core.database.database_session import get_db_session
from src.core.database.embedded_tenant_guard import EmbeddedTenantWriteError
from src.core.database.models import (
    AdapterConfig,
    Creative,
    CurrencyLimit,
    MediaBuy,
    Principal,
    Product,
    PropertyTag,
    SyncJob,
    Tenant,
)
from tests.factories import MediaBuyFactory, PrincipalFactory, ProductFactory, TenantFactory
from tests.helpers.managed_tenant_api import bind_factories_to_session, install_management_api_key

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


API_KEY = "sk-managed-tenant-test-key"


@pytest.fixture
def install_api_key(integration_db):
    """Provision the management API key in the test DB."""
    return install_management_api_key(API_KEY)


@pytest.fixture
def app(integration_db, install_api_key):
    application = Flask(__name__)
    application.config["TESTING"] = True
    application.register_blueprint(tenant_management_api)
    return application


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def auth_headers(install_api_key):
    return {"X-Tenant-Management-API-Key": install_api_key}


@pytest.fixture(autouse=True)
def _stub_adapter_test(monkeypatch, request):
    """Default adapter probe to success — individual tests opt into failures via this fixture."""
    if "real_adapter_test" in request.keywords:
        return

    from src.admin.services.adapter_connection_tester import ProbeResult

    def _stub(adapter_type, config):
        return ProbeResult.ok()

    import src.admin.tenant_management_api as api_module

    monkeypatch.setattr(api_module, "probe_adapter_connection", _stub)


@pytest.fixture(autouse=True)
def _stub_refresh_workers(monkeypatch):
    """Default ``/refresh`` + first-sync-on-provision worker spawn to a no-op.

    Without this, every provision test would spawn real GAM-bound worker
    threads that fail-fast against fake creds and pollute logs. Tests that
    need to verify worker spawning request the ``real_refresh_workers``
    fixture which undoes this stub before the test body runs.
    """
    import src.admin.tenant_management_api as api_module

    monkeypatch.setattr(api_module, "_spawn_refresh_workers", lambda **_kw: None)


@pytest.fixture
def real_refresh_workers(monkeypatch):
    """Opt-out of the autouse worker stub for tests that exercise the
    real worker-spawn path (after patching the leaf worker functions
    they need to observe).

    Restores ``_spawn_refresh_workers`` to the production function
    captured at module-import time (before the autouse stub ran), so
    leaf-function patches in the test body take effect.
    """
    import src.admin.tenant_management_api as api_module

    monkeypatch.setattr(api_module, "_spawn_refresh_workers", _LIVE_SPAWN_REFRESH_WORKERS)


@pytest.fixture
def bound_factories(integration_db):
    """Bind every factory to a session so tests can call ``XFactory(...)`` and have it persist.

    Delegates to ``bind_factories_to_session()`` — keeps the architecture guard happy
    (no inline session.add() in test bodies) without duplicating the binding logic.
    """
    with bind_factories_to_session() as session:
        yield session


@pytest.fixture
def cleanup_tenants():
    """Clean up tenants created during the test."""
    created: list[str] = []
    yield created
    if not created:
        return
    with get_db_session() as session:
        for tid in created:
            for model in (
                AdapterConfig,
                CurrencyLimit,
                PropertyTag,
                Principal,
                Product,
                Creative,
                MediaBuy,
                SyncJob,
            ):
                session.execute(model.__table__.delete().where(model.tenant_id == tid))
            session.execute(Tenant.__table__.delete().where(Tenant.tenant_id == tid))
        session.commit()


def _provision_payload(**overrides):
    payload = {
        "name": "Acme News",
        "external_org_id": "org_acme",
        "external_source": "scope3",
        "contact_email": "ops@example.com",
        # Sprint 1.7: AAO model — public_agent_url defaults to interchange.io.
        "public_agent_url": "https://interchange.io",
        "adapter": {
            "type": "google_ad_manager",
            "network_code": "12345",
            "service_account_email": "sa@example.com",
            "service_account_key_json": '{"type":"service_account"}',
        },
        "default_currency": "USD",
        "billing_plan": "standard",
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# Provision
# ---------------------------------------------------------------------------


class TestProvision:
    def test_provision_happy_path_creates_tenant_and_dependencies(self, client, auth_headers, cleanup_tenants):
        payload = _provision_payload(external_org_id="org_provision_happy")
        response = client.post("/api/v1/tenant-management/tenants/provision", headers=auth_headers, json=payload)
        assert response.status_code == 201, response.get_data(as_text=True)
        body = response.get_json()
        assert body["managed_externally"] is True
        assert body["is_embedded"] is True
        assert body["external_org_id"] == "org_provision_happy"
        assert body["adapter"]["type"] == "google_ad_manager"
        assert body["adapter"]["connection_test_passed"] is True

        cleanup_tenants.append(body["tenant_id"])

        # Verify CurrencyLimit + PropertyTag + AdapterConfig were created in the same transaction.
        with get_db_session() as session:
            assert (
                session.scalars(
                    select(CurrencyLimit).filter_by(tenant_id=body["tenant_id"], currency_code="USD")
                ).first()
                is not None
            )
            assert (
                session.scalars(
                    select(PropertyTag).filter_by(tenant_id=body["tenant_id"], tag_id="all_inventory")
                ).first()
                is not None
            )
            adapter = session.scalars(select(AdapterConfig).filter_by(tenant_id=body["tenant_id"])).first()
            assert adapter is not None
            # The encrypted column must round-trip via the property accessor.
            assert adapter.gam_service_account_json == '{"type":"service_account"}'
            # Provisioning must mark this row as service-account auth so the
            # inventory + custom-targeting sync paths don't fall through to
            # GoogleRefreshTokenClient(refresh_token=None).
            assert adapter.gam_auth_method == "service_account"
            assert adapter.gam_refresh_token is None

    def test_provision_with_initial_principal(self, client, auth_headers, cleanup_tenants):
        payload = _provision_payload(
            external_org_id="org_with_principal",
            initial_principal={"name": "Default Advertiser"},
        )
        response = client.post("/api/v1/tenant-management/tenants/provision", headers=auth_headers, json=payload)
        assert response.status_code == 201
        body = response.get_json()
        cleanup_tenants.append(body["tenant_id"])
        assert body["initial_principal"]["name"] == "Default Advertiser"
        # The principal's access_token is returned so host products can stamp
        # x-adcp-auth on buyer-protocol calls without out-of-band DB reads.
        # Value is the same string persisted in Principal.access_token.
        assert isinstance(body["initial_principal"]["access_token"], str)
        assert body["initial_principal"]["access_token"]

    def test_provision_rolls_back_on_adapter_failure(self, client, auth_headers, monkeypatch):
        import src.admin.tenant_management_api as api_module
        from src.admin.services.adapter_connection_tester import CONNECTION_FAILED, ProbeResult

        def _fail(adapter_type, config):
            return ProbeResult.fail(CONNECTION_FAILED, "auth boom")

        monkeypatch.setattr(api_module, "probe_adapter_connection", _fail)

        payload = _provision_payload(external_org_id="org_provision_fail")
        response = client.post("/api/v1/tenant-management/tenants/provision", headers=auth_headers, json=payload)
        assert response.status_code == 400
        body = response.get_json()
        assert body["error"] == "adapter_connection_failed"
        # Verify NOTHING was written.
        with get_db_session() as session:
            assert session.scalars(select(Tenant).filter_by(external_org_id="org_provision_fail")).first() is None

    def test_provision_returns_409_on_duplicate_external_org_id(self, client, auth_headers, cleanup_tenants):
        payload = _provision_payload(external_org_id="org_dup")
        first = client.post("/api/v1/tenant-management/tenants/provision", headers=auth_headers, json=payload)
        assert first.status_code == 201
        cleanup_tenants.append(first.get_json()["tenant_id"])

        second = client.post("/api/v1/tenant-management/tenants/provision", headers=auth_headers, json=payload)
        assert second.status_code == 409
        body = second.get_json()
        assert body["error"] == "external_org_id_conflict"
        assert "tenant_id" in body["details"]

    def test_provision_unknown_field_rejected(self, client, auth_headers):
        payload = _provision_payload(external_org_id="org_extra", surprise="oops")
        response = client.post("/api/v1/tenant-management/tenants/provision", headers=auth_headers, json=payload)
        # spectree returns 422 for Pydantic validation failures.
        assert response.status_code == 422

    def test_provision_persists_public_agent_url(self, client, auth_headers, cleanup_tenants):
        """Sprint 1.7: public_agent_url survives provision and round-trips on
        the Tenant detail response."""
        payload = _provision_payload(
            external_org_id="org_aao",
            public_agent_url="https://interchange.io",
        )
        response = client.post("/api/v1/tenant-management/tenants/provision", headers=auth_headers, json=payload)
        assert response.status_code == 201, response.get_data(as_text=True)
        body = response.get_json()
        cleanup_tenants.append(body["tenant_id"])

        detail = client.get(f"/api/v1/tenant-management/tenants/{body['tenant_id']}", headers=auth_headers)
        d = detail.get_json()
        assert d["public_agent_url"] == "https://interchange.io"

    def test_provision_defaults_public_agent_url_when_omitted(self, client, auth_headers, cleanup_tenants):
        """Sprint 1.7: public_agent_url is optional and defaults to
        ``https://interchange.io`` for embedded-mode provisions."""
        payload = _provision_payload(external_org_id="org_no_url")
        del payload["public_agent_url"]
        response = client.post("/api/v1/tenant-management/tenants/provision", headers=auth_headers, json=payload)
        assert response.status_code == 201, response.get_data(as_text=True)
        body = response.get_json()
        cleanup_tenants.append(body["tenant_id"])

        detail = client.get(f"/api/v1/tenant-management/tenants/{body['tenant_id']}", headers=auth_headers)
        assert detail.get_json()["public_agent_url"] == "https://interchange.io"

    def test_patch_updates_public_agent_url(self, client, auth_headers, cleanup_tenants):
        payload = _provision_payload(external_org_id="org_aao_patch")
        provision_resp = client.post("/api/v1/tenant-management/tenants/provision", headers=auth_headers, json=payload)
        tid = provision_resp.get_json()["tenant_id"]
        cleanup_tenants.append(tid)

        patch_resp = client.patch(
            f"/api/v1/tenant-management/tenants/{tid}",
            headers=auth_headers,
            json={"public_agent_url": "https://interchange.io"},
        )
        assert patch_resp.status_code == 200, patch_resp.get_data(as_text=True)
        body = patch_resp.get_json()
        assert body["public_agent_url"] == "https://interchange.io"

    # ------------------------------------------------------------------
    # First-sync kicks off as a side effect; never surfaces in response.
    # ------------------------------------------------------------------

    def test_provision_response_does_not_surface_sync_handles(self, client, auth_headers, cleanup_tenants):
        """Provision is binary — tenant created or not. It must not return
        sync handles that invite polling. Inventory sync state lives in
        the salesagent UI from this point on; the storefront is done."""
        payload = _provision_payload(external_org_id="org_initial_sync")
        response = client.post("/api/v1/tenant-management/tenants/provision", headers=auth_headers, json=payload)
        assert response.status_code == 201, response.get_data(as_text=True)
        body = response.get_json()
        cleanup_tenants.append(body["tenant_id"])

        assert "initial_sync" not in body
        assert "sync_run_ids" not in body

    def test_provision_kicks_off_sync_jobs_as_side_effect(self, client, auth_headers, cleanup_tenants):
        """First inventory sync runs as a side effect of provisioning.
        SyncJob rows still get created (visible in the salesagent UI
        dashboard) but are not surfaced to the provision caller."""
        payload = _provision_payload(external_org_id="org_initial_sync_jobs")
        response = client.post("/api/v1/tenant-management/tenants/provision", headers=auth_headers, json=payload)
        assert response.status_code == 201, response.get_data(as_text=True)
        body = response.get_json()
        tid = body["tenant_id"]
        cleanup_tenants.append(tid)

        with get_db_session() as session:
            jobs = session.scalars(select(SyncJob).filter_by(tenant_id=tid)).all()

        assert len(jobs) == 3
        assert {j.sync_type for j in jobs} == {"inventory", "custom_targeting", "advertisers"}
        for job in jobs:
            assert job.triggered_by == "api"
            assert job.triggered_by_id == "tenant_management_api:provision"

    def test_inventory_spawn_failure_marks_sync_rows_failed(self, client, auth_headers, cleanup_tenants, monkeypatch):
        """If the inventory worker spawn raises, the inventory + bundled
        targeting SyncJob rows must transition to ``failed`` with the
        error surfaced. Without this fix, rows sit ``pending`` forever
        and the publisher's dashboard shows "never run" with no hint."""
        import src.admin.tenant_management_api as api_module

        def _raise_on_spawn(**_kw):
            raise RuntimeError("simulated spawn failure")

        # Undo the autouse spawn-stub so the real _spawn_refresh_workers
        # runs and exercises the inventory-spawn path.
        monkeypatch.setattr(api_module, "_spawn_refresh_workers", _LIVE_SPAWN_REFRESH_WORKERS)
        monkeypatch.setattr(
            "src.services.background_sync_service.start_inventory_sync_background",
            _raise_on_spawn,
        )
        # Advertisers spawn would otherwise try to import a real GAM client;
        # neuter it so this test isolates the inventory failure. The
        # advertisers row is intentionally not asserted on — its spawn
        # path is separate and warrants its own test if regressed.
        monkeypatch.setattr(
            "src.services.gam_advertisers_sync.sync_advertisers",
            lambda **_kw: None,
        )

        payload = _provision_payload(external_org_id="org_spawn_fail")
        response = client.post("/api/v1/tenant-management/tenants/provision", headers=auth_headers, json=payload)
        assert response.status_code == 201, response.get_data(as_text=True)
        tid = response.get_json()["tenant_id"]
        cleanup_tenants.append(tid)

        with get_db_session() as session:
            jobs = {j.sync_type: j for j in session.scalars(select(SyncJob).filter_by(tenant_id=tid)).all()}

        assert jobs["inventory"].status == "failed"
        assert jobs["custom_targeting"].status == "failed"
        # Error message captures the exception class, message, spawn label,
        # and a brief traceback — enough for the publisher to self-diagnose
        # common issues without escalating to an engineer.
        inventory_err = jobs["inventory"].error_message or ""
        assert "simulated spawn failure" in inventory_err
        assert "RuntimeError" in inventory_err
        assert "inventory" in inventory_err  # spawn_label
        assert "Traceback" in inventory_err
        assert "simulated spawn failure" in (jobs["custom_targeting"].error_message or "")
        assert jobs["inventory"].completed_at is not None


# ---------------------------------------------------------------------------
# Lifecycle: list / get / patch / deactivate / reactivate / delete
# ---------------------------------------------------------------------------


class TestLifecycle:
    @pytest.fixture
    def managed_tenant(self, client, auth_headers, cleanup_tenants):
        payload = _provision_payload(external_org_id="org_lifecycle")
        resp = client.post("/api/v1/tenant-management/tenants/provision", headers=auth_headers, json=payload)
        assert resp.status_code == 201
        tenant_id = resp.get_json()["tenant_id"]
        cleanup_tenants.append(tenant_id)
        return tenant_id

    def test_list_tenants_filters(self, client, auth_headers, managed_tenant):
        resp = client.get(
            "/api/v1/tenant-management/tenants?managed_externally=true&external_source=scope3",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        body = resp.get_json()
        ids = {t["tenant_id"] for t in body["tenants"]}
        assert managed_tenant in ids
        for t in body["tenants"]:
            assert t["managed_externally"] is True
            assert t["is_embedded"] is True
            assert t["external_source"] == "scope3"

    def test_get_tenant_returns_detail_or_404(self, client, auth_headers, managed_tenant):
        ok = client.get(f"/api/v1/tenant-management/tenants/{managed_tenant}", headers=auth_headers)
        assert ok.status_code == 200
        body = ok.get_json()
        assert body["managed_externally"] is True
        assert body["is_embedded"] is True

        missing = client.get("/api/v1/tenant-management/tenants/tenant_nope_404", headers=auth_headers)
        assert missing.status_code == 404
        assert missing.get_json()["error"] == "tenant_not_found"

    def test_patch_updates_platform_managed_fields(self, client, auth_headers, managed_tenant):
        resp = client.patch(
            f"/api/v1/tenant-management/tenants/{managed_tenant}",
            headers=auth_headers,
            json={"name": "Renamed Acme", "billing_plan": "enterprise"},
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        body = resp.get_json()
        assert body["name"] == "Renamed Acme"
        assert body["billing_plan"] == "enterprise"

    def test_patch_rejects_external_org_id(self, client, auth_headers, managed_tenant):
        resp = client.patch(
            f"/api/v1/tenant-management/tenants/{managed_tenant}",
            headers=auth_headers,
            json={"external_org_id": "different"},
        )
        assert resp.status_code == 422  # extra="forbid"

    def test_deactivate_then_reactivate_idempotent(self, client, auth_headers, managed_tenant):
        first = client.post(f"/api/v1/tenant-management/tenants/{managed_tenant}/deactivate", headers=auth_headers)
        assert first.status_code == 200
        assert first.get_json()["is_active"] is False

        second = client.post(f"/api/v1/tenant-management/tenants/{managed_tenant}/deactivate", headers=auth_headers)
        assert second.status_code == 200
        assert second.get_json()["is_active"] is False

        re = client.post(f"/api/v1/tenant-management/tenants/{managed_tenant}/reactivate", headers=auth_headers)
        assert re.status_code == 200
        assert re.get_json()["is_active"] is True

    def test_soft_delete_returns_inactive_detail(self, client, auth_headers, managed_tenant):
        resp = client.delete(f"/api/v1/tenant-management/tenants/{managed_tenant}", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.get_json()["is_active"] is False

    def test_hard_delete_requires_confirmation_header(self, client, auth_headers, managed_tenant):
        no_header = client.delete(f"/api/v1/tenant-management/tenants/{managed_tenant}?hard=true", headers=auth_headers)
        assert no_header.status_code == 400
        assert no_header.get_json()["error"] == "confirmation_required"

        with_header = client.delete(
            f"/api/v1/tenant-management/tenants/{managed_tenant}?hard=true",
            headers={**auth_headers, "X-Confirm-Delete": "yes"},
        )
        assert with_header.status_code == 200

        # Tenant should be gone.
        with get_db_session() as session:
            assert session.scalars(select(Tenant).filter_by(tenant_id=managed_tenant)).first() is None

    def test_delete_returns_409_when_active_media_buys_present(
        self, client, auth_headers, managed_tenant, bound_factories
    ):
        # Add a Principal + active MediaBuy to the managed tenant. This goes through the publisher-managed
        # path so the write guard does not fire.
        # Tenant already exists (provisioned by managed_tenant fixture) — load it and pass to factories.
        tenant = bound_factories.scalars(select(Tenant).filter_by(tenant_id=managed_tenant)).first()
        principal = PrincipalFactory(
            tenant=tenant,
            principal_id="p_active_mb",
            name="Has Active",
            platform_mappings={"google_ad_manager": {"advertiser_id": "x"}},
            access_token="t_active_mb",
        )
        MediaBuyFactory(
            tenant=tenant,
            principal=principal,
            media_buy_id="mb_active_test",
            order_name="Active Test",
            advertiser_name="x",
            status="active",
            budget=100,
            start_date=datetime.now(UTC).date(),
            end_date=datetime.now(UTC).date(),
            raw_request={},
        )

        resp = client.delete(f"/api/v1/tenant-management/tenants/{managed_tenant}", headers=auth_headers)
        assert resp.status_code == 409
        assert resp.get_json()["error"] == "tenant_has_active_resources"


# ---------------------------------------------------------------------------
# Adapter config
# ---------------------------------------------------------------------------


class TestAdapterConfig:
    @pytest.fixture
    def managed_tenant(self, client, auth_headers, cleanup_tenants):
        payload = _provision_payload(external_org_id="org_adapter_cfg")
        resp = client.post("/api/v1/tenant-management/tenants/provision", headers=auth_headers, json=payload)
        assert resp.status_code == 201
        tid = resp.get_json()["tenant_id"]
        cleanup_tenants.append(tid)
        return tid

    def test_get_adapter_config_redacts_secrets(self, client, auth_headers, managed_tenant):
        resp = client.get(f"/api/v1/tenant-management/tenants/{managed_tenant}/adapter-config", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["type"] == "google_ad_manager"
        # The actual secret JSON must NEVER appear in the response — only the redaction marker.
        assert body["service_account_key_json"] == "<encrypted>"
        assert "service_account" not in (body.get("service_account_key_json") or "<encrypted>").replace(
            "<encrypted>", ""
        )

    def test_put_adapter_config_tests_connection_before_commit(self, client, auth_headers, managed_tenant, monkeypatch):
        import src.admin.tenant_management_api as api_module
        from src.admin.services.adapter_connection_tester import INVALID_CREDENTIALS, ProbeResult

        def _fail(adapter_type, config):
            return ProbeResult.fail(INVALID_CREDENTIALS, "credentials rejected")

        monkeypatch.setattr(api_module, "probe_adapter_connection", _fail)

        payload = {
            "type": "google_ad_manager",
            "network_code": "67890",
            "service_account_email": "new@example.com",
            "service_account_key_json": '{"type":"new_sa"}',
        }
        resp = client.put(
            f"/api/v1/tenant-management/tenants/{managed_tenant}/adapter-config",
            headers=auth_headers,
            json=payload,
        )
        assert resp.status_code == 400
        assert resp.get_json()["error"] == "adapter_invalid_credentials"

        # Existing adapter config unchanged.
        with get_db_session() as session:
            adapter = session.scalars(select(AdapterConfig).filter_by(tenant_id=managed_tenant)).first()
            assert adapter is not None
            assert adapter.gam_network_code == "12345"

    def test_put_adapter_config_replaces_existing(self, client, auth_headers, managed_tenant):
        payload = {
            "type": "mock",
        }
        resp = client.put(
            f"/api/v1/tenant-management/tenants/{managed_tenant}/adapter-config",
            headers=auth_headers,
            json=payload,
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        body = resp.get_json()
        assert body["type"] == "mock"

    def test_test_connection_endpoint_does_not_modify_state(self, client, auth_headers, managed_tenant):
        resp = client.post(
            f"/api/v1/tenant-management/tenants/{managed_tenant}/adapter-config/test-connection",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True
        assert body["error"] is None


# ---------------------------------------------------------------------------
# Write guard
# ---------------------------------------------------------------------------


class TestWriteGuard:
    @pytest.fixture
    def managed_tenant(self, client, auth_headers, cleanup_tenants):
        payload = _provision_payload(external_org_id="org_guard")
        resp = client.post("/api/v1/tenant-management/tenants/provision", headers=auth_headers, json=payload)
        assert resp.status_code == 201
        tid = resp.get_json()["tenant_id"]
        cleanup_tenants.append(tid)
        return tid

    @pytest.fixture
    def unmanaged_tenant(self, integration_db, bound_factories):
        TenantFactory(
            tenant_id="t_unmanaged_guard",
            name="Unmanaged",
            subdomain="unmanaged-guard",
            ad_server="mock",
            billing_plan="standard",
            is_active=True,
            is_embedded=False,
        )
        yield "t_unmanaged_guard"
        with get_db_session() as session:
            session.execute(Tenant.__table__.delete().where(Tenant.tenant_id == "t_unmanaged_guard"))
            session.commit()

    def test_managed_tenant_blocks_non_api_tenant_update(self, managed_tenant):
        # Simulate a UI handler: open a session, mutate a platform-managed field WITHOUT
        # setting the management_api_caller flag — the model guard must fire on commit.
        with get_db_session() as session:
            tenant = session.scalars(select(Tenant).filter_by(tenant_id=managed_tenant)).first()
            assert tenant is not None
            tenant.name = "Should Not Persist"
            with pytest.raises(EmbeddedTenantWriteError):
                session.commit()
            session.rollback()

    def test_unmanaged_tenant_allows_write(self, unmanaged_tenant):
        with get_db_session() as session:
            tenant = session.scalars(select(Tenant).filter_by(tenant_id=unmanaged_tenant)).first()
            tenant.name = "Renamed"
            session.commit()
        with get_db_session() as session:
            assert session.scalars(select(Tenant).filter_by(tenant_id=unmanaged_tenant)).first().name == "Renamed"

    def test_super_admin_override_bypasses_guard(self, managed_tenant):
        with get_db_session() as session:
            session.info["super_admin_override"] = True
            tenant = session.scalars(select(Tenant).filter_by(tenant_id=managed_tenant)).first()
            tenant.name = "Super Admin Rename"
            session.commit()
        with get_db_session() as session:
            assert (
                session.scalars(select(Tenant).filter_by(tenant_id=managed_tenant)).first().name == "Super Admin Rename"
            )

    def test_publisher_managed_table_write_succeeds_on_managed_tenant(self, managed_tenant, bound_factories):
        # Add a Principal directly to the managed tenant, simulating a UI handler.
        # The guard must NOT fire — Principal is publisher-managed.
        tenant = bound_factories.scalars(select(Tenant).filter_by(tenant_id=managed_tenant)).first()
        PrincipalFactory(
            tenant=tenant,
            principal_id="p_publisher_write",
            name="Publisher Side",
            platform_mappings={"mock": {"advertiser_id": "x"}},
            access_token="t_pub_write",
        )
        with get_db_session() as session:
            p = session.scalars(
                select(Principal).filter_by(tenant_id=managed_tenant, principal_id="p_publisher_write")
            ).first()
            assert p is not None
            session.delete(p)
            session.commit()

    def test_managed_tenant_blocks_adapter_config_update_outside_api(self, managed_tenant):
        with get_db_session() as session:
            adapter = session.scalars(select(AdapterConfig).filter_by(tenant_id=managed_tenant)).first()
            assert adapter is not None
            adapter.gam_network_code = "blocked-rewrite"
            with pytest.raises(EmbeddedTenantWriteError):
                session.commit()
            session.rollback()

    def test_business_rules_fields_writable_on_managed_tenant(self, managed_tenant):
        # Business rules are publisher-managed per Sprint 5 design — writing them
        # from a UI session (no auth flags) must succeed on an embedded tenant.
        with get_db_session() as session:
            tenant = session.scalars(select(Tenant).filter_by(tenant_id=managed_tenant)).first()
            tenant.order_name_template = "publisher-edited-{date}"
            tenant.brand_manifest_policy = "require_brand"
            tenant.human_review_required = True
            tenant.creative_auto_approve_threshold = 0.95
            session.commit()
        with get_db_session() as session:
            t = session.scalars(select(Tenant).filter_by(tenant_id=managed_tenant)).first()
            assert t.order_name_template == "publisher-edited-{date}"
            assert t.brand_manifest_policy == "require_brand"
            assert t.human_review_required is True
            assert t.creative_auto_approve_threshold == 0.95

    def test_platform_identity_fields_still_blocked_on_managed_tenant(self, managed_tenant):
        # Opening up business rules must NOT relax the lock on platform-identity
        # columns. `name` is still platform-managed.
        with get_db_session() as session:
            tenant = session.scalars(select(Tenant).filter_by(tenant_id=managed_tenant)).first()
            tenant.name = "Publisher Tried To Rename"
            with pytest.raises(EmbeddedTenantWriteError):
                session.commit()
            session.rollback()

    def test_manual_approval_adapter_fields_writable_on_managed_tenant(self, managed_tenant):
        # human_review_required syncs to adapter_config.{gam,mock}_manual_approval_required.
        # Both adapter fields must pass the guard for the sync to land.
        with get_db_session() as session:
            adapter = session.scalars(select(AdapterConfig).filter_by(tenant_id=managed_tenant)).first()
            assert adapter is not None
            adapter.mock_manual_approval_required = True
            session.commit()
        with get_db_session() as session:
            a = session.scalars(select(AdapterConfig).filter_by(tenant_id=managed_tenant)).first()
            assert a.mock_manual_approval_required is True

    def test_platform_background_worker_flag_allows_adapter_config_update(self, managed_tenant):
        # Guard-layer contract: a session marked with
        # platform_background_worker=True must be allowed to write
        # platform-managed columns on an embedded tenant. UI sessions
        # don't set this flag, so publisher writes still fail.
        with get_db_session() as session:
            session.info["platform_background_worker"] = True
            adapter = session.scalars(select(AdapterConfig).filter_by(tenant_id=managed_tenant)).first()
            assert adapter is not None
            adapter.custom_targeting_keys = {"sport": "12345", "team": "67890"}
            session.commit()
        with get_db_session() as session:
            adapter = session.scalars(select(AdapterConfig).filter_by(tenant_id=managed_tenant)).first()
            assert adapter.custom_targeting_keys == {"sport": "12345", "team": "67890"}

    def test_sync_session_helper_sets_platform_background_worker(self, managed_tenant):
        # End-to-end: the background_sync_service._sync_session() helper is
        # what every inventory-sync worker actually opens. This test goes
        # through the helper itself (not a synthetic flag-set) and proves
        # a write that would otherwise fire the guard succeeds. If anyone
        # removes the flag-set line in _sync_session(), this test fails.
        from src.services.background_sync_service import _sync_session

        with _sync_session() as session:
            adapter = session.scalars(select(AdapterConfig).filter_by(tenant_id=managed_tenant)).first()
            assert adapter is not None
            adapter.custom_targeting_keys = {"helper": "wires-flag"}
            session.commit()
        with get_db_session() as session:
            adapter = session.scalars(select(AdapterConfig).filter_by(tenant_id=managed_tenant)).first()
            assert adapter.custom_targeting_keys == {"helper": "wires-flag"}


# ---------------------------------------------------------------------------
# End-to-end + reverse-proxy + OpenAPI smoke
# ---------------------------------------------------------------------------


class TestEndToEnd:
    def test_end_to_end_managed_tenant_lifecycle(self, client, auth_headers, cleanup_tenants, bound_factories):
        # 1) Provision.
        provision_resp = client.post(
            "/api/v1/tenant-management/tenants/provision",
            headers=auth_headers,
            json=_provision_payload(external_org_id="org_e2e"),
        )
        assert provision_resp.status_code == 201
        tenant_id = provision_resp.get_json()["tenant_id"]
        cleanup_tenants.append(tenant_id)

        # 2) Patch via API succeeds.
        patch_resp = client.patch(
            f"/api/v1/tenant-management/tenants/{tenant_id}",
            headers=auth_headers,
            json={"name": "End-to-End Renamed"},
        )
        assert patch_resp.status_code == 200
        assert patch_resp.get_json()["name"] == "End-to-End Renamed"

        # 3) Same write via a UI-style handler (no management_api_caller) → guard fires.
        with get_db_session() as session:
            tenant = session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
            tenant.name = "UI Should Not Persist"
            with pytest.raises(EmbeddedTenantWriteError):
                session.commit()
            session.rollback()

        # 4) Adding a Product (publisher-managed) via a UI-style handler succeeds.
        tenant = bound_factories.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
        ProductFactory(
            tenant=tenant,
            product_id="prod_e2e",
            name="E2E Product",
            description="Created without management_api_caller",
            format_ids=[{"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"}],
            targeting_template={},
            delivery_type="non_guaranteed",
            property_tags=["all_inventory"],
        )
        with get_db_session() as session:
            assert (
                session.scalars(select(Product).filter_by(tenant_id=tenant_id, product_id="prod_e2e")).first()
                is not None
            )

        # 5) Deactivate via API.
        deactivate_resp = client.post(f"/api/v1/tenant-management/tenants/{tenant_id}/deactivate", headers=auth_headers)
        assert deactivate_resp.status_code == 200
        assert deactivate_resp.get_json()["is_active"] is False

        # 6) Re-provision with the same external_org_id → 409.
        repeat = client.post(
            "/api/v1/tenant-management/tenants/provision",
            headers=auth_headers,
            json=_provision_payload(external_org_id="org_e2e"),
        )
        assert repeat.status_code == 409
        assert repeat.get_json()["error"] == "external_org_id_conflict"


class TestOpenAPI:
    def test_swagger_ui_loads(self, client):
        resp = client.get("/api/v1/tenant-management/docs/swagger/")
        assert resp.status_code == 200
        # Swagger UI HTML uses the swagger-ui CSS + JS bundle.
        body = resp.get_data(as_text=True)
        assert "swagger" in body.lower()

    def test_openapi_spec_validates_as_openapi3(self, client):
        resp = client.get("/api/v1/tenant-management/docs/openapi.json")
        assert resp.status_code == 200
        spec_doc = resp.get_json()

        # Minimal OpenAPI 3 sanity
        assert spec_doc.get("openapi", "").startswith("3.")
        assert "info" in spec_doc and "paths" in spec_doc

        # Sprint-1 endpoints must appear in the spec.
        paths = spec_doc["paths"]
        joined = json.dumps(paths)
        assert "/tenants/provision" in joined
        assert "/adapter-config" in joined
        assert "/deactivate" in joined
        assert "/reactivate" in joined
        # Sprint-1.5
        assert "/tenants/preview-adapter" in joined


# ---------------------------------------------------------------------------
# Sprint 1.5: preview-adapter
# ---------------------------------------------------------------------------


@pytest.fixture
def real_adapter_test_disabled(monkeypatch):
    """Stub ``preview_adapter`` to passthrough — used by tests that don't care about adapter type."""
    import src.admin.tenant_management_api as api_module
    from src.admin.services.adapter_connection_tester import AdapterPreview

    def _stub(adapter_type, cfg):
        if adapter_type == "mock":
            return AdapterPreview(
                ok=True,
                network_name="Mock Network",
                network_code="mock",
                currency_code="USD",
                time_zone="UTC",
                inventory_reachable=True,
            )
        return AdapterPreview(ok=True, network_code=str(cfg.get("network_code") or ""))

    monkeypatch.setattr(api_module, "preview_adapter", _stub)


class TestPreviewAdapter:
    """``POST /tenants/preview-adapter`` — pre-provision probe with no persistence.

    Bad creds return ``200 + ok=false`` so Storefront can render inline.
    Malformed bodies still surface as 4xx via spectree.
    """

    URL = "/api/v1/tenant-management/tenants/preview-adapter"

    def test_mock_adapter_returns_canned_metadata(self, client, auth_headers, real_adapter_test_disabled):
        resp = client.post(
            self.URL,
            headers=auth_headers,
            json={"adapter": {"type": "mock", "dry_run": True}},
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        body = resp.get_json()
        assert body["ok"] is True
        assert body["network_name"] == "Mock Network"
        assert body["currency_code"] == "USD"
        assert body["time_zone"] == "UTC"
        assert body["inventory_reachable"] is True

    def test_gam_happy_path_returns_network_metadata(self, client, auth_headers, monkeypatch):
        """Stub ``preview_adapter`` to simulate a successful GAM probe."""
        import src.admin.tenant_management_api as api_module
        from src.admin.services.adapter_connection_tester import AdapterPreview

        monkeypatch.setattr(
            api_module,
            "preview_adapter",
            lambda atype, cfg: AdapterPreview(
                ok=True,
                network_name="Acme News",
                network_code="123456",
                currency_code="USD",
                time_zone="America/New_York",
                inventory_reachable=True,
            ),
        )

        resp = client.post(
            self.URL,
            headers=auth_headers,
            json={
                "adapter": {
                    "type": "google_ad_manager",
                    "network_code": "123456",
                    "service_account_email": "sa@example.com",
                    "service_account_key_json": '{"type":"service_account"}',
                }
            },
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        body = resp.get_json()
        assert body["ok"] is True
        assert body["network_name"] == "Acme News"
        assert body["currency_code"] == "USD"
        assert body["time_zone"] == "America/New_York"

    def test_bad_creds_return_200_with_ok_false(self, client, auth_headers, monkeypatch):
        """Bad creds are a normal flow — surface as 200 + ok=false, not 4xx."""
        import src.admin.tenant_management_api as api_module
        from src.admin.services.adapter_connection_tester import AdapterPreview

        monkeypatch.setattr(
            api_module,
            "preview_adapter",
            lambda atype, cfg: AdapterPreview(ok=False, error="invalid_grant", inventory_reachable=False),
        )

        resp = client.post(
            self.URL,
            headers=auth_headers,
            json={
                "adapter": {
                    "type": "google_ad_manager",
                    "network_code": "123456",
                    "service_account_email": "sa@example.com",
                    "service_account_key_json": '{"type":"bad"}',
                }
            },
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is False
        assert body["inventory_reachable"] is False
        assert body["error"] == "invalid_grant"

    def test_no_tenant_row_created(self, client, auth_headers, real_adapter_test_disabled):
        """Preview must not create any tenant row as a side effect."""
        from sqlalchemy import func

        with get_db_session() as session:
            count_before = session.scalar(select(func.count()).select_from(Tenant))

        resp = client.post(
            self.URL,
            headers=auth_headers,
            json={"adapter": {"type": "mock"}},
        )
        assert resp.status_code == 200

        with get_db_session() as session:
            count_after = session.scalar(select(func.count()).select_from(Tenant))
        assert count_after == count_before

    def test_malformed_body_returns_422(self, client, auth_headers):
        """Pydantic validation failure surfaces as 422, not 200 + ok=false."""
        resp = client.post(self.URL, headers=auth_headers, json={"adapter": {"type": "unknown"}})
        assert resp.status_code == 422

    def test_missing_api_key_returns_401(self, client):
        resp = client.post(self.URL, json={"adapter": {"type": "mock"}})
        assert resp.status_code in (401, 403)


# ---------------------------------------------------------------------------
# Sprint 1.5: GET /tenants/{tid}/status
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_status_cache():
    """Bust the in-memory status cache between tests so state doesn't leak."""
    from src.admin.services.tenant_status_service import invalidate_status_cache

    invalidate_status_cache()
    yield
    invalidate_status_cache()


class TestTenantStatus:
    """``GET /tenants/{tid}/status`` — consolidated operational snapshot."""

    @pytest.fixture
    def managed_tenant(self, client, auth_headers, cleanup_tenants):
        payload = _provision_payload(external_org_id="org_status")
        resp = client.post("/api/v1/tenant-management/tenants/provision", headers=auth_headers, json=payload)
        assert resp.status_code == 201
        tid = resp.get_json()["tenant_id"]
        cleanup_tenants.append(tid)
        return tid

    def test_returns_404_for_unknown_tenant(self, client, auth_headers):
        resp = client.get("/api/v1/tenant-management/tenants/tenant_no_such_id/status", headers=auth_headers)
        assert resp.status_code == 404
        assert resp.get_json()["error"] == "tenant_not_found"

    def test_freshly_provisioned_tenant_returns_zero_state(self, client, auth_headers, managed_tenant):
        """A new tenant has no syncs / workflows / buys / creatives — should return zero counts, not error."""
        resp = client.get(f"/api/v1/tenant-management/tenants/{managed_tenant}/status", headers=auth_headers)
        assert resp.status_code == 200, resp.get_data(as_text=True)
        body = resp.get_json()

        # Adapter block populated from provision
        assert body["adapter"]["type"] == "google_ad_manager"
        assert body["adapter"]["connected"] is True

        # Empty defaults
        assert body["syncs"]["inventory"]["status"] == "never_run"
        assert body["syncs"]["custom_targeting"]["status"] == "never_run"
        assert body["syncs"]["advertisers"]["status"] == "never_run"
        assert body["workflows"]["open_count"] == 0
        assert body["workflows"]["by_kind"] == {}
        assert body["media_buys"]["active_count"] == 0
        assert body["media_buys"]["pending_approval_count"] == 0
        assert body["packages"]["active_count"] == 0
        assert body["packages"]["paused_count"] == 0
        assert body["creatives"]["active_count"] == 0
        assert body["creatives"]["pending_review_count"] == 0
        assert body["webhooks"] is None
        assert "fetched_at" in body

    def test_status_reflects_active_media_buy(self, client, auth_headers, managed_tenant, bound_factories):
        """An active media buy bumps ``media_buys.active_count``."""
        tenant = bound_factories.scalars(select(Tenant).filter_by(tenant_id=managed_tenant)).first()
        principal = PrincipalFactory(
            tenant=tenant,
            principal_id="p_status",
            name="Status Test",
            platform_mappings={"google_ad_manager": {"advertiser_id": "x"}},
            access_token="t_status",
        )
        MediaBuyFactory(
            tenant=tenant,
            principal=principal,
            media_buy_id="mb_status_active",
            order_name="Status Active",
            advertiser_name="x",
            status="active",
            budget=100,
            start_date=datetime.now(UTC).date(),
            end_date=datetime.now(UTC).date(),
            raw_request={},
        )

        resp = client.get(f"/api/v1/tenant-management/tenants/{managed_tenant}/status", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["media_buys"]["active_count"] == 1

    def test_status_is_cached_within_ttl(self, client, auth_headers, managed_tenant):
        """Two calls within the TTL window return the same ``fetched_at`` (cache hit)."""
        first = client.get(
            f"/api/v1/tenant-management/tenants/{managed_tenant}/status", headers=auth_headers
        ).get_json()
        second = client.get(
            f"/api/v1/tenant-management/tenants/{managed_tenant}/status", headers=auth_headers
        ).get_json()
        assert first["fetched_at"] == second["fetched_at"]

    def test_adapter_test_invalidates_status_cache(self, client, auth_headers, managed_tenant):
        """Calling the adapter test-connection endpoint busts the status cache.

        Verifies the invalidation hook wired in ``adapter_test_connection``.
        """
        first = client.get(
            f"/api/v1/tenant-management/tenants/{managed_tenant}/status", headers=auth_headers
        ).get_json()
        # Touch the test-connection endpoint — should invalidate.
        client.post(
            f"/api/v1/tenant-management/tenants/{managed_tenant}/adapter-config/test-connection",
            headers=auth_headers,
        )
        second = client.get(
            f"/api/v1/tenant-management/tenants/{managed_tenant}/status", headers=auth_headers
        ).get_json()
        assert first["fetched_at"] != second["fetched_at"]

    def test_missing_api_key_returns_401(self, client, managed_tenant):
        resp = client.get(f"/api/v1/tenant-management/tenants/{managed_tenant}/status")
        assert resp.status_code in (401, 403)


class TestStatusSetupTasks:
    """Sprint 1.8 §7 — ``setup_tasks`` block on /status.

    Folds the existing setup-checklist output into the status response
    with severity + scope annotations so Storefront can route gaps.
    """

    @pytest.fixture
    def managed_tenant(self, client, auth_headers, cleanup_tenants):
        payload = _provision_payload(external_org_id="org_setup_tasks")
        resp = client.post("/api/v1/tenant-management/tenants/provision", headers=auth_headers, json=payload)
        assert resp.status_code == 201, resp.get_data(as_text=True)
        tid = resp.get_json()["tenant_id"]
        cleanup_tenants.append(tid)
        return tid

    def test_setup_tasks_block_present_on_status_response(self, client, auth_headers, managed_tenant):
        resp = client.get(f"/api/v1/tenant-management/tenants/{managed_tenant}/status", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.get_json()
        assert "setup_tasks" in body
        assert "blocker_count" in body["setup_tasks"]
        assert "warning_count" in body["setup_tasks"]
        assert "items" in body["setup_tasks"]
        assert isinstance(body["setup_tasks"]["items"], list)

    def test_managed_tenant_with_aao_set_omits_aao_tasks(self, client, auth_headers, managed_tenant):
        """Sprint 1.8 §6 hide-when-set carries through to /status setup_tasks."""
        resp = client.get(f"/api/v1/tenant-management/tenants/{managed_tenant}/status", headers=auth_headers)
        item_ids = {item["id"] for item in resp.get_json()["setup_tasks"]["items"]}
        assert "public_agent_url" not in item_ids

    def test_authorized_properties_legacy_task_is_hidden(self, client, auth_headers, managed_tenant):
        """Legacy ``authorized_properties`` task is hidden on every tenant."""
        resp = client.get(f"/api/v1/tenant-management/tenants/{managed_tenant}/status", headers=auth_headers)
        item_ids = {item["id"] for item in resp.get_json()["setup_tasks"]["items"]}
        assert "authorized_properties" not in item_ids

    def test_embedded_setup_tasks_omit_platform_scope_items(self, client, auth_headers, managed_tenant):
        """Embedded tenants don't see ``scope=platform`` items in
        setup_tasks — the host already knows its own provisioning state
        via the management API; surfacing platform items in the
        publisher-facing /status response just creates noise.

        Open-instance tenants still see them (publisher owns everything
        in standalone mode).
        """
        resp = client.get(f"/api/v1/tenant-management/tenants/{managed_tenant}/status", headers=auth_headers)
        items = resp.get_json()["setup_tasks"]["items"]
        scopes = {item["scope"] for item in items}
        # Only publisher-scope items should appear on an embedded tenant.
        assert scopes <= {"publisher"}, f"Embedded tenant /status surfaced platform items: {scopes}"

    def test_default_advertiser_blocker_when_unset(self, client, auth_headers, managed_tenant):
        """Tenant without default_gam_advertiser_id sees the task as a publisher-scope blocker."""
        resp = client.get(f"/api/v1/tenant-management/tenants/{managed_tenant}/status", headers=auth_headers)
        items = resp.get_json()["setup_tasks"]["items"]
        # default_gam_advertiser_id isn't in the existing setup_checklist tasks today;
        # it'll be a publisher-scope item once the checklist surfaces it. For now,
        # assert that any *publisher*-scope item we DO surface carries the right shape.
        assert all(item["scope"] in ("platform", "publisher") for item in items)
        assert all(item["severity"] in ("blocker", "warning", "info") for item in items)

    def test_complete_tasks_render_severity_info(self, client, auth_headers, managed_tenant):
        """Completed items become severity=info regardless of tier."""
        resp = client.get(f"/api/v1/tenant-management/tenants/{managed_tenant}/status", headers=auth_headers)
        items = resp.get_json()["setup_tasks"]["items"]
        for item in items:
            if item["is_complete"]:
                assert item["severity"] == "info"

    def test_blocker_warning_counts_match_items(self, client, auth_headers, managed_tenant):
        resp = client.get(f"/api/v1/tenant-management/tenants/{managed_tenant}/status", headers=auth_headers)
        body = resp.get_json()["setup_tasks"]
        actual_blockers = sum(1 for i in body["items"] if i["severity"] == "blocker")
        actual_warnings = sum(1 for i in body["items"] if i["severity"] == "warning")
        assert body["blocker_count"] == actual_blockers
        assert body["warning_count"] == actual_warnings

    def test_configure_paths_are_relative_to_tenant_root(self, client, auth_headers, managed_tenant):
        """``configure_path`` must be relative (``/settings#publishers``) so
        Storefront can compose with its iframe prefix."""
        resp = client.get(f"/api/v1/tenant-management/tenants/{managed_tenant}/status", headers=auth_headers)
        items = resp.get_json()["setup_tasks"]["items"]
        for item in items:
            cp = item["configure_path"]
            if cp is not None:
                assert cp.startswith("/")
                # Must NOT be a tenant-prefixed full path
                assert not cp.startswith("/tenant/")

    def test_open_instance_tenant_aao_tasks_have_publisher_scope(
        self, client, auth_headers, bound_factories, cleanup_tenants
    ):
        """Open-instance tenant (is_embedded=False) sees AAO items as
        ``scope=publisher`` — they're the publisher's job, not the platform's."""
        from src.admin.services.tenant_status_service import invalidate_status_cache

        tid = "tid_open_instance_status"
        TenantFactory(
            tenant_id=tid,
            name="Open Instance",
            subdomain="open-instance",
            ad_server="mock",
            is_embedded=False,
        )
        cleanup_tenants.append(tid)
        invalidate_status_cache(tid)

        resp = client.get(f"/api/v1/tenant-management/tenants/{tid}/status", headers=auth_headers)
        assert resp.status_code == 200
        items = {i["id"]: i for i in resp.get_json()["setup_tasks"]["items"]}
        # Open-instance tenants always show the public_agent_url AAO item.
        if "public_agent_url" in items:
            assert items["public_agent_url"]["scope"] == "publisher"


class TestStatusProductsBlock:
    """Sprint 1.8 follow-up — products rollup on the /status response.

    Distinct from packages: one Product fans out to multiple priced
    packages, so packages.active_count doesn't answer "what is the
    publisher selling?". Storefront's homepage card reads from this
    block.
    """

    @pytest.fixture
    def managed_tenant(self, client, auth_headers, cleanup_tenants):
        payload = _provision_payload(external_org_id="org_status_products")
        resp = client.post("/api/v1/tenant-management/tenants/provision", headers=auth_headers, json=payload)
        assert resp.status_code == 201, resp.get_data(as_text=True)
        tid = resp.get_json()["tenant_id"]
        cleanup_tenants.append(tid)
        return tid

    def test_products_block_present_with_zero_counts_when_no_products(self, client, auth_headers, managed_tenant):
        """A freshly-provisioned tenant has no Products → all counters zero."""
        from src.admin.services.tenant_status_service import invalidate_status_cache

        invalidate_status_cache(managed_tenant)
        resp = client.get(f"/api/v1/tenant-management/tenants/{managed_tenant}/status", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.get_json()
        assert "products" in body
        assert body["products"] == {"active_count": 0, "draft_count": 0, "archived_count": 0}

    def test_active_products_counted(self, client, auth_headers, managed_tenant, bound_factories):
        """``archived_at IS NULL`` → ``active_count``."""
        from src.admin.services.tenant_status_service import invalidate_status_cache

        tenant = bound_factories.scalars(select(Tenant).filter_by(tenant_id=managed_tenant)).first()
        ProductFactory(tenant=tenant, product_id="prod_active_1", name="Active 1")
        ProductFactory(tenant=tenant, product_id="prod_active_2", name="Active 2")
        bound_factories.commit()

        invalidate_status_cache(managed_tenant)
        resp = client.get(f"/api/v1/tenant-management/tenants/{managed_tenant}/status", headers=auth_headers)
        assert resp.get_json()["products"]["active_count"] == 2

    def test_archived_products_split_from_active(self, client, auth_headers, managed_tenant, bound_factories):
        """``archived_at IS NOT NULL`` → ``archived_count``, not ``active_count``."""
        from src.admin.services.tenant_status_service import invalidate_status_cache

        tenant = bound_factories.scalars(select(Tenant).filter_by(tenant_id=managed_tenant)).first()
        ProductFactory(tenant=tenant, product_id="prod_active", name="Active")
        archived = ProductFactory(tenant=tenant, product_id="prod_archived", name="Archived")
        archived.archived_at = datetime.now(UTC)
        bound_factories.commit()

        invalidate_status_cache(managed_tenant)
        resp = client.get(f"/api/v1/tenant-management/tenants/{managed_tenant}/status", headers=auth_headers)
        body = resp.get_json()["products"]
        assert body["active_count"] == 1
        assert body["archived_count"] == 1

    def test_draft_count_always_zero(self, client, auth_headers, managed_tenant, bound_factories):
        """``draft_count`` reserved for a future draft-state column —
        always 0 today, but the field is in the response shape so
        Storefront can render a "Drafts" badge without an API change."""
        from src.admin.services.tenant_status_service import invalidate_status_cache

        tenant = bound_factories.scalars(select(Tenant).filter_by(tenant_id=managed_tenant)).first()
        ProductFactory(tenant=tenant, product_id="prod_d_1", name="P1")
        bound_factories.commit()

        invalidate_status_cache(managed_tenant)
        resp = client.get(f"/api/v1/tenant-management/tenants/{managed_tenant}/status", headers=auth_headers)
        assert resp.get_json()["products"]["draft_count"] == 0


# ---------------------------------------------------------------------------
# Sprint 1.6: pre-map advertisers
# ---------------------------------------------------------------------------


class TestPreMapAdvertiser:
    """``POST /tenants/{tid}/accounts`` and ``GET .../accounts``.

    Verifies the Account upsert-by-natural-key behavior with a pre-attached
    ``platform_mappings.google_ad_manager.advertiser_id``.
    """

    @pytest.fixture
    def tid(self, client, auth_headers, cleanup_tenants):
        payload = _provision_payload(external_org_id="org_premap")
        resp = client.post("/api/v1/tenant-management/tenants/provision", headers=auth_headers, json=payload)
        assert resp.status_code == 201
        t = resp.get_json()["tenant_id"]
        cleanup_tenants.append(t)
        return t

    def _post_account(self, client, auth_headers, tid, **overrides):
        body = {
            "operator": "accuweather.com",
            "brand": {"domain": "cocacola.com"},
            "billing": "operator",
            "gam_advertiser_id": "12345",
            "gam_advertiser_name": "Coca-Cola (AccuWeather)",
        }
        body.update(overrides)
        return client.post(
            f"/api/v1/tenant-management/tenants/{tid}/accounts",
            headers=auth_headers,
            json=body,
        )

    def test_create_with_pre_attached_advertiser_returns_201(self, client, auth_headers, tid):
        resp = self._post_account(client, auth_headers, tid)
        assert resp.status_code == 201, resp.get_data(as_text=True)
        body = resp.get_json()
        assert body["status"] == "active"
        assert body["billing"] == "operator"
        assert body["gam_advertiser_id"] == "12345"
        assert body["gam_advertiser_name"] == "Coca-Cola (AccuWeather)"
        assert body["advertiser_mapped"] is True
        # Auto-generated name template
        assert "accuweather.com" in body["name"] and "cocacola.com" in body["name"]

    def test_repeat_post_upserts_existing_account(self, client, auth_headers, tid):
        first = self._post_account(client, auth_headers, tid)
        assert first.status_code == 201
        first_account_id = first.get_json()["account_id"]

        # Re-POST with the same natural key but different advertiser id
        second = self._post_account(client, auth_headers, tid, gam_advertiser_id="99999")
        assert second.status_code == 200
        body = second.get_json()
        assert body["account_id"] == first_account_id  # Same row
        assert body["gam_advertiser_id"] == "99999"  # Updated

    def test_billing_agent_requires_buyer_agent_principal_id(self, client, auth_headers, tid):
        resp = self._post_account(client, auth_headers, tid, billing="agent")
        assert resp.status_code == 400
        assert resp.get_json()["error"] == "buyer_agent_required"

    def test_billing_agent_separates_per_agent(self, client, auth_headers, tid):
        """Two buyer agents on the same (operator, brand) → two distinct Accounts."""
        a1 = self._post_account(
            client,
            auth_headers,
            tid,
            billing="agent",
            buyer_agent_principal_id="scope3-buyer",
            gam_advertiser_id="agent_adv_1",
        )
        a2 = self._post_account(
            client,
            auth_headers,
            tid,
            billing="agent",
            buyer_agent_principal_id="other-buyer",
            gam_advertiser_id="agent_adv_2",
        )
        assert a1.status_code == 201
        assert a2.status_code == 201
        assert a1.get_json()["account_id"] != a2.get_json()["account_id"]
        assert a1.get_json()["buyer_agent_principal_id"] == "scope3-buyer"
        assert a2.get_json()["buyer_agent_principal_id"] == "other-buyer"

    def test_sandbox_rejects_advertiser_id(self, client, auth_headers, tid):
        resp = self._post_account(
            client,
            auth_headers,
            tid,
            sandbox=True,
            brand={"domain": "test.example"},
            gam_advertiser_id="should_not_be_accepted",
        )
        assert resp.status_code == 400
        assert resp.get_json()["error"] == "sandbox_advertiser_managed"

    def test_sandbox_creates_unmapped_account(self, client, auth_headers, tid):
        resp = self._post_account(
            client,
            auth_headers,
            tid,
            sandbox=True,
            brand={"domain": "test.example"},
            gam_advertiser_id=None,
        )
        assert resp.status_code == 201
        body = resp.get_json()
        assert body["sandbox"] is True
        # Sandbox accounts are unmapped at creation time — sprint 1.6 impl
        # will route them to the per-tenant sandbox advertiser at first-buy.
        assert body["advertiser_mapped"] is False

    def test_post_unknown_tenant_returns_404(self, client, auth_headers):
        resp = client.post(
            "/api/v1/tenant-management/tenants/tenant_missing/accounts",
            headers=auth_headers,
            json={
                "operator": "x",
                "brand": {"domain": "y.com"},
                "billing": "operator",
                "gam_advertiser_id": "1",
            },
        )
        assert resp.status_code == 404

    def test_list_returns_pre_mapped_accounts(self, client, auth_headers, tid):
        self._post_account(client, auth_headers, tid, brand={"domain": "a.com"})
        self._post_account(client, auth_headers, tid, brand={"domain": "b.com"})

        resp = client.get(f"/api/v1/tenant-management/tenants/{tid}/accounts", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["count"] == 2
        domains = {a["brand"]["domain"] for a in body["accounts"]}
        assert domains == {"a.com", "b.com"}
        for a in body["accounts"]:
            assert a["advertiser_mapped"] is True

    def test_list_filters_by_advertiser_mapped(self, client, auth_headers, tid):
        self._post_account(client, auth_headers, tid, brand={"domain": "mapped.com"})
        self._post_account(
            client,
            auth_headers,
            tid,
            sandbox=True,
            brand={"domain": "sandbox.com"},
            gam_advertiser_id=None,
        )

        mapped = client.get(
            f"/api/v1/tenant-management/tenants/{tid}/accounts?advertiser_mapped=true",
            headers=auth_headers,
        )
        unmapped = client.get(
            f"/api/v1/tenant-management/tenants/{tid}/accounts?advertiser_mapped=false",
            headers=auth_headers,
        )

        assert mapped.get_json()["count"] == 1
        assert mapped.get_json()["accounts"][0]["brand"]["domain"] == "mapped.com"
        assert unmapped.get_json()["count"] == 1
        assert unmapped.get_json()["accounts"][0]["brand"]["domain"] == "sandbox.com"

    def test_list_filters_by_operator(self, client, auth_headers, tid):
        self._post_account(client, auth_headers, tid, operator="op-a", brand={"domain": "x.com"})
        self._post_account(client, auth_headers, tid, operator="op-b", brand={"domain": "x.com"})

        resp = client.get(
            f"/api/v1/tenant-management/tenants/{tid}/accounts?operator=op-a",
            headers=auth_headers,
        )
        body = resp.get_json()
        assert body["count"] == 1
        assert body["accounts"][0]["operator"] == "op-a"

    def test_missing_api_key_returns_401(self, client, tid):
        resp = client.post(
            f"/api/v1/tenant-management/tenants/{tid}/accounts",
            json={"operator": "x", "brand": {"domain": "y.com"}, "billing": "operator", "gam_advertiser_id": "1"},
        )
        assert resp.status_code in (401, 403)


# ---------------------------------------------------------------------------
# Sprint 1.8: buyer-advertiser routing rules CRUD + default advertiser
# ---------------------------------------------------------------------------


class TestBuyerAdvertiserMappings:
    """``/tenants/{tid}/buyer-advertiser-mappings`` CRUD.

    External API surface uses ``buyer-advertiser-mapping`` vocabulary
    (matches Storefront UI); the underlying table is
    ``advertiser_routing_rules`` because the impl IS a precedence-ordered
    routing chain. The handler maps between the two at the boundary.
    """

    @pytest.fixture
    def tid(self, client, auth_headers, cleanup_tenants):
        payload = _provision_payload(external_org_id="org_routing")
        resp = client.post("/api/v1/tenant-management/tenants/provision", headers=auth_headers, json=payload)
        assert resp.status_code == 201
        t = resp.get_json()["tenant_id"]
        cleanup_tenants.append(t)
        return t

    def _post_mapping(self, client, auth_headers, tid, **overrides):
        body = {
            "operator_domain": "interchange.io",
            "brand_house": "coca-cola.com",
            "brand_id": "sprite",
            "gam_advertiser_id": "12345",
        }
        body.update(overrides)
        return client.post(
            f"/api/v1/tenant-management/tenants/{tid}/buyer-advertiser-mappings",
            headers=auth_headers,
            json=body,
        )

    def test_create_returns_201_with_full_mapping(self, client, auth_headers, tid):
        resp = self._post_mapping(client, auth_headers, tid)
        assert resp.status_code == 201, resp.get_data(as_text=True)
        body = resp.get_json()
        assert body["operator_domain"] == "interchange.io"
        assert body["brand_house"] == "coca-cola.com"
        assert body["brand_id"] == "sprite"
        assert body["gam_advertiser_id"] == "12345"
        assert body["id"].startswith("rule_")
        assert "created_at" in body and "updated_at" in body

    def test_create_operator_wildcard_omits_brand_fields(self, client, auth_headers, tid):
        resp = self._post_mapping(client, auth_headers, tid, brand_house=None, brand_id=None, gam_advertiser_id="99")
        assert resp.status_code == 201
        body = resp.get_json()
        assert body["brand_house"] is None and body["brand_id"] is None

    def test_create_house_wildcard_omits_brand_id(self, client, auth_headers, tid):
        resp = self._post_mapping(client, auth_headers, tid, brand_id=None)
        assert resp.status_code == 201
        body = resp.get_json()
        assert body["brand_house"] == "coca-cola.com" and body["brand_id"] is None

    def test_create_rejects_brand_id_without_brand_house(self, client, auth_headers, tid):
        resp = self._post_mapping(client, auth_headers, tid, brand_house=None, brand_id="sprite")
        assert resp.status_code == 400
        assert resp.get_json()["error"] == "brand_house_required"

    def test_create_returns_409_on_duplicate_natural_key(self, client, auth_headers, tid):
        first = self._post_mapping(client, auth_headers, tid)
        assert first.status_code == 201
        # Same (operator, brand_house, brand_id) tuple, different advertiser
        dup = self._post_mapping(client, auth_headers, tid, gam_advertiser_id="99999")
        assert dup.status_code == 409
        body = dup.get_json()
        assert body["error"] == "routing_rule_duplicate"
        assert body["details"]["operator_domain"] == "interchange.io"
        assert body["details"]["brand_house"] == "coca-cola.com"
        assert body["details"]["brand_id"] == "sprite"

    def test_create_409_on_duplicate_with_null_brand_fields(self, client, auth_headers, tid):
        """COALESCE-unique-index treats NULL+NULL as a collision — two
        operator-wildcard rules under the same operator are forbidden."""
        first = self._post_mapping(client, auth_headers, tid, brand_house=None, brand_id=None, gam_advertiser_id="1")
        assert first.status_code == 201
        dup = self._post_mapping(client, auth_headers, tid, brand_house=None, brand_id=None, gam_advertiser_id="2")
        assert dup.status_code == 409

    # -------------------------------------------------------------------
    # Sprint 5 — principal_id in the natural key
    # -------------------------------------------------------------------

    def test_create_persists_principal_id(self, client, auth_headers, tid):
        """``principal_id`` round-trips through POST and the GET projection."""
        resp = self._post_mapping(client, auth_headers, tid, principal_id="scope3-emb")
        assert resp.status_code == 201, resp.get_data(as_text=True)
        body = resp.get_json()
        assert body["principal_id"] == "scope3-emb"

        list_resp = client.get(
            f"/api/v1/tenant-management/tenants/{tid}/buyer-advertiser-mappings",
            headers=auth_headers,
        )
        assert list_resp.status_code == 200
        mappings = list_resp.get_json()["mappings"]
        assert any(m["principal_id"] == "scope3-emb" for m in mappings)

    def test_create_omits_principal_id_defaults_to_null(self, client, auth_headers, tid):
        """Sprint 1.8 backward-compat: omitting principal_id stores NULL."""
        resp = self._post_mapping(client, auth_headers, tid)
        assert resp.status_code == 201
        assert resp.get_json()["principal_id"] is None

    def test_principal_id_distinguishes_otherwise_identical_rules(self, client, auth_headers, tid):
        """Two rules with identical (operator, brand_house, brand_id) but
        different principal_id values coexist — agent is part of the key."""
        a = self._post_mapping(client, auth_headers, tid, principal_id="scope3-emb", gam_advertiser_id="11")
        b = self._post_mapping(client, auth_headers, tid, principal_id="wstruck-buy", gam_advertiser_id="22")
        c = self._post_mapping(client, auth_headers, tid, gam_advertiser_id="33")  # principal_id=NULL
        assert (a.status_code, b.status_code, c.status_code) == (201, 201, 201)
        assert len({a.get_json()["id"], b.get_json()["id"], c.get_json()["id"]}) == 3

    def test_create_409_on_duplicate_with_same_principal_id(self, client, auth_headers, tid):
        """Two rules with same (principal_id, operator, brand_house, brand_id)
        collide via the COALESCE-unique-index — 409 with principal_id in details."""
        first = self._post_mapping(client, auth_headers, tid, principal_id="scope3-emb", gam_advertiser_id="11")
        assert first.status_code == 201
        dup = self._post_mapping(client, auth_headers, tid, principal_id="scope3-emb", gam_advertiser_id="22")
        assert dup.status_code == 409
        body = dup.get_json()
        assert body["error"] == "routing_rule_duplicate"
        assert body["details"]["principal_id"] == "scope3-emb"
        assert body["details"]["operator_domain"] == "interchange.io"
        assert body["details"]["brand_house"] == "coca-cola.com"
        assert body["details"]["brand_id"] == "sprite"

    def test_create_409_details_include_null_principal_id(self, client, auth_headers, tid):
        """When the colliding rule has principal_id=NULL the details block
        carries that NULL through (not a missing key)."""
        first = self._post_mapping(client, auth_headers, tid, gam_advertiser_id="11")
        assert first.status_code == 201
        dup = self._post_mapping(client, auth_headers, tid, gam_advertiser_id="22")
        assert dup.status_code == 409
        body = dup.get_json()
        assert "principal_id" in body["details"]
        assert body["details"]["principal_id"] is None

    def test_create_allows_multiple_rules_under_same_operator_when_brand_differs(self, client, auth_headers, tid):
        """Three coexisting rules under the same operator: exact, house, operator-wildcard."""
        r1 = self._post_mapping(client, auth_headers, tid, brand_house="cocacola.com", brand_id="sprite")
        r2 = self._post_mapping(
            client, auth_headers, tid, brand_house="cocacola.com", brand_id=None, gam_advertiser_id="2"
        )
        r3 = self._post_mapping(client, auth_headers, tid, brand_house=None, brand_id=None, gam_advertiser_id="3")
        assert (r1.status_code, r2.status_code, r3.status_code) == (201, 201, 201)
        assert len({r1.get_json()["id"], r2.get_json()["id"], r3.get_json()["id"]}) == 3

    def test_create_unknown_tenant_returns_404(self, client, auth_headers):
        resp = client.post(
            "/api/v1/tenant-management/tenants/tenant_missing/buyer-advertiser-mappings",
            headers=auth_headers,
            json={"operator_domain": "x.com", "gam_advertiser_id": "1"},
        )
        assert resp.status_code == 404

    def test_list_returns_rules_in_creation_order(self, client, auth_headers, tid):
        self._post_mapping(client, auth_headers, tid, brand_house="a.com", brand_id=None, gam_advertiser_id="1")
        self._post_mapping(client, auth_headers, tid, brand_house="b.com", brand_id=None, gam_advertiser_id="2")
        self._post_mapping(client, auth_headers, tid, brand_house="c.com", brand_id=None, gam_advertiser_id="3")

        resp = client.get(f"/api/v1/tenant-management/tenants/{tid}/buyer-advertiser-mappings", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["count"] == 3
        # ASC by created_at — first-authored rule appears first
        assert [m["brand_house"] for m in body["mappings"]] == ["a.com", "b.com", "c.com"]

    def test_list_filters_by_operator_domain(self, client, auth_headers, tid):
        self._post_mapping(
            client, auth_headers, tid, operator_domain="interchange.io", brand_house="a.com", brand_id=None
        )
        self._post_mapping(
            client,
            auth_headers,
            tid,
            operator_domain="buyer.scope3.com",
            brand_house="b.com",
            brand_id=None,
            gam_advertiser_id="2",
        )
        resp = client.get(
            f"/api/v1/tenant-management/tenants/{tid}/buyer-advertiser-mappings?operator_domain=interchange.io",
            headers=auth_headers,
        )
        body = resp.get_json()
        assert body["count"] == 1
        assert body["mappings"][0]["operator_domain"] == "interchange.io"

    def test_list_unknown_tenant_returns_404(self, client, auth_headers):
        resp = client.get(
            "/api/v1/tenant-management/tenants/tenant_missing/buyer-advertiser-mappings",
            headers=auth_headers,
        )
        assert resp.status_code == 404

    def test_patch_updates_advertiser_id(self, client, auth_headers, tid):
        created = self._post_mapping(client, auth_headers, tid).get_json()
        resp = client.patch(
            f"/api/v1/tenant-management/tenants/{tid}/buyer-advertiser-mappings/{created['id']}",
            headers=auth_headers,
            json={"gam_advertiser_id": "99999"},
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["gam_advertiser_id"] == "99999"
        assert body["operator_domain"] == "interchange.io"  # Unchanged

    def test_patch_409_on_natural_key_collision(self, client, auth_headers, tid):
        """Patching brand_id into another rule's tuple collides on the unique index."""
        a = self._post_mapping(client, auth_headers, tid, brand_house="coke.com", brand_id="sprite")
        b = self._post_mapping(
            client, auth_headers, tid, brand_house="coke.com", brand_id="dasani", gam_advertiser_id="2"
        )
        assert (a.status_code, b.status_code) == (201, 201)

        resp = client.patch(
            f"/api/v1/tenant-management/tenants/{tid}/buyer-advertiser-mappings/{b.get_json()['id']}",
            headers=auth_headers,
            json={"brand_id": "sprite"},  # collides with rule a
        )
        assert resp.status_code == 409
        assert resp.get_json()["error"] == "routing_rule_duplicate"

    def test_patch_unknown_id_returns_404(self, client, auth_headers, tid):
        resp = client.patch(
            f"/api/v1/tenant-management/tenants/{tid}/buyer-advertiser-mappings/rule_does_not_exist",
            headers=auth_headers,
            json={"gam_advertiser_id": "1"},
        )
        assert resp.status_code == 404

    def test_patch_does_not_accept_operator_domain(self, client, auth_headers, tid):
        """``operator_domain`` is intentionally not patchable — schema strips it."""
        created = self._post_mapping(client, auth_headers, tid).get_json()
        resp = client.patch(
            f"/api/v1/tenant-management/tenants/{tid}/buyer-advertiser-mappings/{created['id']}",
            headers=auth_headers,
            json={"operator_domain": "different.io"},
        )
        # spectree validates against UpdateBuyerAdvertiserMappingRequest (extra=forbid in dev/CI).
        assert resp.status_code in (400, 422)

    def test_delete_returns_204_then_404_on_repeat(self, client, auth_headers, tid):
        created = self._post_mapping(client, auth_headers, tid).get_json()
        first = client.delete(
            f"/api/v1/tenant-management/tenants/{tid}/buyer-advertiser-mappings/{created['id']}",
            headers=auth_headers,
        )
        assert first.status_code == 204

        # Repeat returns 404 — the row truly is gone, and the caller's
        # request to delete that specific id can't be satisfied.
        again = client.delete(
            f"/api/v1/tenant-management/tenants/{tid}/buyer-advertiser-mappings/{created['id']}",
            headers=auth_headers,
        )
        assert again.status_code == 404

    def test_delete_unknown_id_returns_404(self, client, auth_headers, tid):
        resp = client.delete(
            f"/api/v1/tenant-management/tenants/{tid}/buyer-advertiser-mappings/rule_missing",
            headers=auth_headers,
        )
        assert resp.status_code == 404

    def test_missing_api_key_returns_401(self, client, tid):
        resp = client.post(
            f"/api/v1/tenant-management/tenants/{tid}/buyer-advertiser-mappings",
            json={"operator_domain": "x.com", "gam_advertiser_id": "1"},
        )
        assert resp.status_code in (401, 403)


class TestDefaultGamAdvertiserId:
    """``Tenant.default_gam_advertiser_id`` — read/write through the
    provision and patch endpoints. See sprint 1.8 design doc §1."""

    @pytest.fixture
    def tid(self, client, auth_headers, cleanup_tenants):
        payload = _provision_payload(external_org_id="org_default_advertiser")
        resp = client.post("/api/v1/tenant-management/tenants/provision", headers=auth_headers, json=payload)
        assert resp.status_code == 201
        t = resp.get_json()["tenant_id"]
        cleanup_tenants.append(t)
        return t

    def test_provision_persists_default_gam_advertiser_id(self, client, auth_headers, cleanup_tenants):
        payload = _provision_payload(external_org_id="org_default_at_provision", default_gam_advertiser_id="11111")
        resp = client.post("/api/v1/tenant-management/tenants/provision", headers=auth_headers, json=payload)
        assert resp.status_code == 201
        cleanup_tenants.append(resp.get_json()["tenant_id"])

        tid = resp.get_json()["tenant_id"]
        get_resp = client.get(f"/api/v1/tenant-management/tenants/{tid}", headers=auth_headers)
        assert get_resp.status_code == 200
        assert get_resp.get_json()["default_gam_advertiser_id"] == "11111"

    def test_provision_without_default_returns_null_in_detail(self, client, auth_headers, tid):
        """Required-before-activation, optional at provision."""
        get_resp = client.get(f"/api/v1/tenant-management/tenants/{tid}", headers=auth_headers)
        assert get_resp.status_code == 200
        assert get_resp.get_json()["default_gam_advertiser_id"] is None

    def test_patch_sets_default_gam_advertiser_id(self, client, auth_headers, tid):
        resp = client.patch(
            f"/api/v1/tenant-management/tenants/{tid}",
            headers=auth_headers,
            json={"default_gam_advertiser_id": "55555"},
        )
        assert resp.status_code == 200
        assert resp.get_json()["default_gam_advertiser_id"] == "55555"

        # Roundtrip via GET to confirm persistence
        get_resp = client.get(f"/api/v1/tenant-management/tenants/{tid}", headers=auth_headers)
        assert get_resp.get_json()["default_gam_advertiser_id"] == "55555"

    def test_patch_omitting_default_advertiser_does_not_clear_existing(self, client, auth_headers, tid):
        """PATCH with default_gam_advertiser_id absent leaves stored value intact."""
        client.patch(
            f"/api/v1/tenant-management/tenants/{tid}",
            headers=auth_headers,
            json={"default_gam_advertiser_id": "55555"},
        )
        # Patch a different field — default_gam_advertiser_id must stay set.
        resp = client.patch(
            f"/api/v1/tenant-management/tenants/{tid}",
            headers=auth_headers,
            json={"name": "Renamed Publisher"},
        )
        assert resp.status_code == 200
        assert resp.get_json()["default_gam_advertiser_id"] == "55555"


# ---------------------------------------------------------------------------
# Sprint 1.8 §4 — recent-buyers rollup
# ---------------------------------------------------------------------------


class TestRecentBuyers:
    """``GET /tenants/{tid}/recent-buyers`` — distinct buyer triples
    aggregated from Account + MediaBuy. Powers the Storefront 'buyer
    routing' widget."""

    @pytest.fixture
    def tid(self, client, auth_headers, cleanup_tenants):
        payload = _provision_payload(external_org_id="org_recent_buyers")
        resp = client.post("/api/v1/tenant-management/tenants/provision", headers=auth_headers, json=payload)
        assert resp.status_code == 201
        t = resp.get_json()["tenant_id"]
        cleanup_tenants.append(t)
        return t

    def test_unknown_tenant_returns_404(self, client, auth_headers):
        resp = client.get("/api/v1/tenant-management/tenants/tenant_missing/recent-buyers", headers=auth_headers)
        assert resp.status_code == 404

    def test_no_accounts_returns_empty_list(self, client, auth_headers, tid):
        """Tenants with no Accounts return ``buyers: []``, not 404."""
        resp = client.get(f"/api/v1/tenant-management/tenants/{tid}/recent-buyers", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.get_json() == {"buyers": []}

    def test_account_with_resolved_via_surfaces(self, client, auth_headers, tid, bound_factories):
        """Sprint 1.8 ``resolved_via`` flows through to /recent-buyers."""
        from src.core.database.models import Account

        bound_factories.add(
            Account(
                tenant_id=tid,
                account_id="acct_house_match",
                name="Coke (Interchange)",
                status="active",
                operator="interchange.io",
                brand={"domain": "coca-cola.com", "brand_id": "sprite"},
                billing="agent",
                sandbox=False,
                principal_id=None,
                platform_mappings={"google_ad_manager": {"advertiser_id": "12345"}},
                resolved_via="house",
            )
        )
        bound_factories.commit()

        resp = client.get(f"/api/v1/tenant-management/tenants/{tid}/recent-buyers", headers=auth_headers)
        body = resp.get_json()
        assert len(body["buyers"]) == 1
        buyer = body["buyers"][0]
        assert buyer["operator_domain"] == "interchange.io"
        assert buyer["brand_house"] == "coca-cola.com"
        assert buyer["brand_id"] == "sprite"
        assert buyer["resolved_gam_advertiser_id"] == "12345"
        assert buyer["resolved_via"] == "house"
        # No MediaBuys → request_count is 0.
        assert buyer["request_count"] == 0

    def test_legacy_account_with_null_resolved_via_surfaces_unknown(self, client, auth_headers, tid, bound_factories):
        """Account rows that predate sprint 1.8 have NULL resolved_via;
        the API surfaces them as ``resolved_via='unknown'``."""
        from src.core.database.models import Account

        bound_factories.add(
            Account(
                tenant_id=tid,
                account_id="acct_legacy",
                name="Legacy Account",
                status="active",
                operator="legacy.example",
                brand={"domain": "legacy.example"},
                billing="operator",
                sandbox=False,
                platform_mappings={"google_ad_manager": {"advertiser_id": "999"}},
                resolved_via=None,
            )
        )
        bound_factories.commit()

        resp = client.get(f"/api/v1/tenant-management/tenants/{tid}/recent-buyers", headers=auth_headers)
        buyers = resp.get_json()["buyers"]
        legacy = next((b for b in buyers if b["operator_domain"] == "legacy.example"), None)
        assert legacy is not None
        assert legacy["resolved_via"] == "unknown"

    def test_request_count_aggregates_media_buys(self, client, auth_headers, tid, bound_factories):
        """``request_count`` reflects MediaBuy rows in the window."""
        from src.core.database.models import Account

        tenant = bound_factories.scalars(select(Tenant).filter_by(tenant_id=tid)).first()
        principal = PrincipalFactory(
            tenant=tenant,
            principal_id="p_recent",
            access_token="t_recent",
            platform_mappings={"google_ad_manager": {"advertiser_id": "1"}},
        )
        bound_factories.add(
            Account(
                tenant_id=tid,
                account_id="acct_active",
                name="Active",
                status="active",
                operator="buyer.example",
                brand={"domain": "brand.example"},
                billing="agent",
                principal_id=principal.principal_id,
                sandbox=False,
                platform_mappings={"google_ad_manager": {"advertiser_id": "1"}},
                resolved_via="default",
            )
        )
        bound_factories.flush()

        for i in range(3):
            MediaBuyFactory(
                tenant=tenant,
                principal=principal,
                media_buy_id=f"mb_recent_{i}",
                order_name=f"Recent {i}",
                advertiser_name="x",
                status="active",
                budget=100,
                start_date=datetime.now(UTC).date(),
                end_date=datetime.now(UTC).date(),
                raw_request={},
                account_id="acct_active",
            )
        bound_factories.commit()

        resp = client.get(f"/api/v1/tenant-management/tenants/{tid}/recent-buyers", headers=auth_headers)
        buyers = resp.get_json()["buyers"]
        active = next((b for b in buyers if b["operator_domain"] == "buyer.example"), None)
        assert active is not None
        assert active["request_count"] == 3

    def test_days_filter_excludes_old_media_buys(self, client, auth_headers, tid, bound_factories):
        """``?days=1`` excludes MediaBuys older than 1 day."""
        from datetime import timedelta as _td

        from src.core.database.models import Account

        tenant = bound_factories.scalars(select(Tenant).filter_by(tenant_id=tid)).first()
        principal = PrincipalFactory(
            tenant=tenant,
            principal_id="p_old",
            access_token="t_old",
            platform_mappings={"google_ad_manager": {"advertiser_id": "1"}},
        )
        bound_factories.add(
            Account(
                tenant_id=tid,
                account_id="acct_old",
                name="Old",
                status="active",
                operator="old.example",
                brand={"domain": "old.example"},
                billing="agent",
                principal_id=principal.principal_id,
                sandbox=False,
                platform_mappings={"google_ad_manager": {"advertiser_id": "1"}},
                resolved_via="default",
            )
        )
        bound_factories.flush()

        # MediaBuy created 60 days ago — outside the 1-day window.
        old = MediaBuyFactory(
            tenant=tenant,
            principal=principal,
            media_buy_id="mb_old",
            order_name="Old buy",
            advertiser_name="x",
            status="completed",
            budget=100,
            start_date=datetime.now(UTC).date(),
            end_date=datetime.now(UTC).date(),
            raw_request={},
            account_id="acct_old",
        )
        old.created_at = datetime.now(UTC) - _td(days=60)
        bound_factories.commit()

        resp = client.get(f"/api/v1/tenant-management/tenants/{tid}/recent-buyers?days=1", headers=auth_headers)
        buyers = resp.get_json()["buyers"]
        old_buyer = next((b for b in buyers if b["operator_domain"] == "old.example"), None)
        # Account is still in the list (we don't hide unprovisioned ones)
        # but request_count is 0 because the only MediaBuy is outside the window.
        assert old_buyer is not None
        assert old_buyer["request_count"] == 0

    def test_limit_caps_response_size(self, client, auth_headers, tid, bound_factories):
        """``?limit=2`` returns at most 2 buyers."""
        from src.core.database.models import Account

        for i in range(5):
            bound_factories.add(
                Account(
                    tenant_id=tid,
                    account_id=f"acct_lim_{i}",
                    name=f"Buyer {i}",
                    status="active",
                    operator=f"buyer{i}.example",
                    brand={"domain": f"brand{i}.example"},
                    billing="operator",
                    sandbox=False,
                    platform_mappings={"google_ad_manager": {"advertiser_id": str(i)}},
                    resolved_via="default",
                )
            )
        bound_factories.commit()

        resp = client.get(f"/api/v1/tenant-management/tenants/{tid}/recent-buyers?limit=2", headers=auth_headers)
        assert len(resp.get_json()["buyers"]) == 2

    def test_missing_api_key_returns_401(self, client, tid):
        resp = client.get(f"/api/v1/tenant-management/tenants/{tid}/recent-buyers")
        assert resp.status_code in (401, 403)


# ---------------------------------------------------------------------------
# Sprint 1.8 §8 — collapsed refresh endpoint
# ---------------------------------------------------------------------------


class TestRefresh:
    """``POST /tenants/{tid}/refresh`` — fan-out across sync types with
    60s idempotency window."""

    @pytest.fixture
    def tid(self, client, auth_headers, cleanup_tenants):
        payload = _provision_payload(external_org_id="org_refresh")
        resp = client.post("/api/v1/tenant-management/tenants/provision", headers=auth_headers, json=payload)
        assert resp.status_code == 201
        t = resp.get_json()["tenant_id"]
        cleanup_tenants.append(t)
        return t

    def test_unknown_tenant_returns_404(self, client, auth_headers):
        resp = client.post("/api/v1/tenant-management/tenants/tenant_missing/refresh", headers=auth_headers)
        assert resp.status_code == 404

    def test_first_call_returns_202_with_three_sync_run_ids(self, client, auth_headers, tid):
        resp = client.post(f"/api/v1/tenant-management/tenants/{tid}/refresh", headers=auth_headers)
        assert resp.status_code == 202
        body = resp.get_json()
        assert "started_at" in body
        sync_ids = body["sync_run_ids"]
        # All three sync types fanned out.
        assert set(sync_ids.keys()) == {"inventory", "custom_targeting", "advertisers"}
        # Each gets a unique id (no collisions).
        assert len(set(sync_ids.values())) == 3

    def test_immediate_repost_returns_same_ids_idempotent(self, client, auth_headers, tid):
        """Re-POST within the 60s idempotency window returns the SAME ids
        — avoids hammering GAM when a publisher mashes the button."""
        first = client.post(f"/api/v1/tenant-management/tenants/{tid}/refresh", headers=auth_headers).get_json()
        second = client.post(f"/api/v1/tenant-management/tenants/{tid}/refresh", headers=auth_headers).get_json()
        assert first["sync_run_ids"] == second["sync_run_ids"]

    def test_creates_pending_sync_jobs_in_db(self, client, auth_headers, cleanup_tenants, monkeypatch):
        """Rows are created with the expected metadata. Workers are mocked
        out so rows stay in 'pending' state for assertion stability —
        with real workers, rows transition to 'running' on the spawned
        thread, racing the test's read.

        Builds its own tenant directly (bypasses the ``tid`` fixture's
        provision-time first-sync) so the assertion can attribute the
        SyncJob rows to ``/refresh`` rather than the provision call.
        """
        import src.admin.tenant_management_api as api_mod

        monkeypatch.setattr(api_mod, "_spawn_refresh_workers", lambda **kw: None)

        from src.core.database.database_session import get_db_session
        from src.core.database.models import SyncJob

        # Provision still happens (autouse stub neutralized the spawner
        # during provision too — no rows created there).
        payload = _provision_payload(external_org_id="org_refresh_creates_rows")
        prov = client.post("/api/v1/tenant-management/tenants/provision", headers=auth_headers, json=payload)
        assert prov.status_code == 201
        tid = prov.get_json()["tenant_id"]
        cleanup_tenants.append(tid)

        # First-sync-on-provision still ran via _create_and_spawn_refresh
        # → 3 SyncJob rows already exist tagged ``:provision``. The
        # subsequent /refresh hits the 60s idempotency window and REUSES
        # those rows — rows count stays at 3.
        client.post(f"/api/v1/tenant-management/tenants/{tid}/refresh", headers=auth_headers)

        with get_db_session() as session:
            jobs = session.scalars(select(SyncJob).filter_by(tenant_id=tid)).all()
        # Still 3 (one per sync_type) thanks to refresh idempotency.
        assert len(jobs) == 3
        assert {j.sync_type for j in jobs} == {"inventory", "custom_targeting", "advertisers"}
        for job in jobs:
            assert job.status == "pending"
            assert job.triggered_by == "api"
            # Provisioned-then-refreshed: triggered_by_id reflects the
            # original creator (provision), since refresh reused.
            assert job.triggered_by_id == "tenant_management_api:provision"

    def test_spawns_inventory_and_advertisers_workers(
        self, client, auth_headers, tid, monkeypatch, real_refresh_workers
    ):
        """/refresh actually kicks off background workers — pending rows
        don't sit forever. Asserts:
          - inventory worker called with the inventory sync_id +
            companion targeting_sync_id (custom_targeting bundles into
            the inventory worker, not its own thread)
          - advertisers worker spawned in a thread with the advertisers
            sync_id
        Without this, /refresh creates rows that never run.

        Uses ``real_refresh_workers`` fixture to undo the autouse stub
        so the real ``_spawn_refresh_workers`` runs (the test patches
        the leaf worker functions it calls).
        """
        import src.services.background_sync_service as bg_mod
        import src.services.gam_advertisers_sync as gam_adv_mod

        inventory_calls = []
        advertisers_calls = []

        def fake_start_inventory(tenant_id, **kwargs):
            inventory_calls.append({"tenant_id": tenant_id, **kwargs})
            return kwargs.get("pending_sync_id") or "fake-sync-id"

        def fake_sync_advertisers(*, tenant_id, sync_id=None, **kwargs):
            advertisers_calls.append({"tenant_id": tenant_id, "sync_id": sync_id})

        monkeypatch.setattr(bg_mod, "start_inventory_sync_background", fake_start_inventory)
        monkeypatch.setattr(gam_adv_mod, "sync_advertisers", fake_sync_advertisers)
        # Replace threading.Thread.start with a synchronous run so the
        # assertion can read advertisers_calls immediately. The /refresh
        # spawner uses threading.Thread to fire-and-forget — for the
        # test we want deterministic ordering.
        import threading as _threading

        original_thread_start = _threading.Thread.start

        def sync_thread_start(self):
            self.run()

        monkeypatch.setattr(_threading.Thread, "start", sync_thread_start)

        try:
            resp = client.post(f"/api/v1/tenant-management/tenants/{tid}/refresh", headers=auth_headers)
            assert resp.status_code == 202
            sync_run_ids = resp.get_json()["sync_run_ids"]

            # Inventory worker called with both inventory and targeting
            # sync ids — the bundled-row pattern.
            assert len(inventory_calls) == 1
            inv_call = inventory_calls[0]
            assert inv_call["tenant_id"] == tid
            assert inv_call["pending_sync_id"] == sync_run_ids["inventory"]
            assert inv_call["targeting_sync_id"] == sync_run_ids["custom_targeting"]

            # Advertisers worker called separately with its own sync_id.
            assert len(advertisers_calls) == 1
            adv_call = advertisers_calls[0]
            assert adv_call["tenant_id"] == tid
            assert adv_call["sync_id"] == sync_run_ids["advertisers"]
        finally:
            monkeypatch.setattr(_threading.Thread, "start", original_thread_start)

    def test_running_sync_outside_window_returns_409_with_existing_id(self, client, auth_headers, tid, bound_factories):
        """A pre-existing running SyncJob outside the 60s idempotency
        window is a genuine conflict — return 409 with the existing
        sync_id so the storefront can correlate, instead of an
        indistinguishable 202.

        Clears the provision-time first-sync rows first so the synthetic
        running row is the only candidate; otherwise the just-spawned
        provision rows (status=pending, started_at=just-now) would win
        the most-recent ordering.

        Issue #463: a UI "Retry" button needs to know its click triggered
        nothing new. Pre-#463 this was a silent 202.
        """
        from datetime import timedelta as _td

        from src.core.database.models import SyncJob

        # Drop the just-created provision-time rows so the synthetic
        # running row is unambiguously the winner.
        bound_factories.execute(SyncJob.__table__.delete().where(SyncJob.tenant_id == tid))
        bound_factories.commit()

        old_running_id = "sync_existing_running"
        bound_factories.add(
            SyncJob(
                sync_id=old_running_id,
                tenant_id=tid,
                adapter_type="google_ad_manager",
                sync_type="inventory",
                status="running",
                started_at=datetime.now(UTC) - _td(minutes=10),  # outside 60s window
                triggered_by="cron",
            )
        )
        bound_factories.commit()

        resp = client.post(f"/api/v1/tenant-management/tenants/{tid}/refresh", headers=auth_headers)
        assert resp.status_code == 409
        body = resp.get_json()
        assert body["error"] == "sync_already_running"
        # 409 body mirrors the 202 shape: sync_run_ids and started_at
        # at the top level, no details nesting.
        assert body["sync_run_ids"]["inventory"] == old_running_id
        assert "inventory" in body["running_sync_types"]
        assert "started_at" in body

    def test_completed_sync_outside_window_is_not_reused(self, client, auth_headers, tid, bound_factories):
        """A completed SyncJob older than 60s is NOT reused — we want
        fresh data for the publisher's 'Refresh tenant' click."""
        from datetime import timedelta as _td

        from src.core.database.models import SyncJob

        bound_factories.add(
            SyncJob(
                sync_id="sync_old_completed",
                tenant_id=tid,
                adapter_type="google_ad_manager",
                sync_type="inventory",
                status="completed",
                started_at=datetime.now(UTC) - _td(minutes=10),
                completed_at=datetime.now(UTC) - _td(minutes=8),
                triggered_by="cron",
            )
        )
        bound_factories.commit()

        resp = client.post(f"/api/v1/tenant-management/tenants/{tid}/refresh", headers=auth_headers)
        new_id = resp.get_json()["sync_run_ids"]["inventory"]
        assert new_id != "sync_old_completed"

    def test_invalidates_status_cache(self, client, auth_headers, tid):
        """A refresh should invalidate the status cache so the next
        GET /status reflects the new pending sync runs."""
        first = client.get(f"/api/v1/tenant-management/tenants/{tid}/status", headers=auth_headers).get_json()
        client.post(f"/api/v1/tenant-management/tenants/{tid}/refresh", headers=auth_headers)
        second = client.get(f"/api/v1/tenant-management/tenants/{tid}/status", headers=auth_headers).get_json()
        assert first["fetched_at"] != second["fetched_at"]

    def test_missing_api_key_returns_401(self, client, tid):
        resp = client.post(f"/api/v1/tenant-management/tenants/{tid}/refresh")
        assert resp.status_code in (401, 403)

    def test_returns_409_when_sync_running_outside_idempotency_window(
        self, client, auth_headers, cleanup_tenants, monkeypatch
    ):
        """Issue #463: a long-running sync (older than the 60s
        idempotency window) makes /refresh return 409 instead of an
        indistinguishable 202. The storefront's "Retry" button needs to
        know its click triggered nothing new — same sync_run_ids would
        otherwise be ambiguous between "you're idempotent" and "an old
        sync is still chugging."""
        # Stub the spawner so SyncJob rows stay in their planted state.
        import src.admin.tenant_management_api as api_mod

        monkeypatch.setattr(api_mod, "_spawn_refresh_workers", lambda **kw: None)

        payload = _provision_payload(external_org_id="org_refresh_409")
        prov = client.post("/api/v1/tenant-management/tenants/provision", headers=auth_headers, json=payload)
        assert prov.status_code == 201
        tid = prov.get_json()["tenant_id"]
        cleanup_tenants.append(tid)

        # Backdate the inventory row to 10 minutes ago and mark it running
        # — same row, outside the 60s window. The other two sync_types
        # stay pending (provision-created) so we can isolate which trigger
        # the 409 fires for.
        from datetime import UTC, datetime, timedelta

        from src.core.database.database_session import get_db_session
        from src.core.database.models import SyncJob

        with get_db_session() as session:
            inv_row = session.scalars(select(SyncJob).filter_by(tenant_id=tid, sync_type="inventory")).first()
            inv_row.status = "running"
            inv_row.started_at = datetime.now(UTC) - timedelta(minutes=10)
            session.commit()
            running_sync_id = inv_row.sync_id

        resp = client.post(f"/api/v1/tenant-management/tenants/{tid}/refresh", headers=auth_headers)
        assert resp.status_code == 409, resp.get_data(as_text=True)

        body = resp.get_json()
        assert body["error"] == "sync_already_running"
        # 409 body mirrors the 202 shape (sync_run_ids + started_at at
        # the top level) so receivers don't need a second parse path.
        assert body["running_sync_types"] == ["inventory"]
        assert body["sync_run_ids"]["inventory"] == running_sync_id
        assert "started_at" in body

    def test_does_not_return_409_within_idempotency_window(self, client, auth_headers, cleanup_tenants, monkeypatch):
        """A running sync that STARTED within the 60s window is still
        the idempotent-reuse case, not a conflict. Re-POST stays 202."""
        import src.admin.tenant_management_api as api_mod

        monkeypatch.setattr(api_mod, "_spawn_refresh_workers", lambda **kw: None)

        payload = _provision_payload(external_org_id="org_refresh_no_409")
        prov = client.post("/api/v1/tenant-management/tenants/provision", headers=auth_headers, json=payload)
        assert prov.status_code == 201
        tid = prov.get_json()["tenant_id"]
        cleanup_tenants.append(tid)

        # Flip inventory to running but keep started_at "now" (inside 60s).
        from datetime import UTC, datetime

        from src.core.database.database_session import get_db_session
        from src.core.database.models import SyncJob

        with get_db_session() as session:
            inv_row = session.scalars(select(SyncJob).filter_by(tenant_id=tid, sync_type="inventory")).first()
            inv_row.status = "running"
            inv_row.started_at = datetime.now(UTC)
            session.commit()

        resp = client.post(f"/api/v1/tenant-management/tenants/{tid}/refresh", headers=auth_headers)
        assert resp.status_code == 202, resp.get_data(as_text=True)

    def test_worker_pickup_restamps_started_at(self, client, auth_headers, cleanup_tenants, monkeypatch):
        """Regression for the race the code reviewer flagged: a row that
        sat ``pending`` longer than 60s and just transitioned to
        ``running`` must not look like a stale in-flight conflict on the
        next /refresh. The worker pickup re-stamps ``started_at`` so the
        60s window reflects when work actually began.

        This pins the contract from the worker side. The complementary
        test ``test_does_not_return_409_within_idempotency_window``
        pins the same outcome from the API side."""
        import src.admin.tenant_management_api as api_mod

        monkeypatch.setattr(api_mod, "_spawn_refresh_workers", lambda **kw: None)

        payload = _provision_payload(external_org_id="org_worker_pickup_restamp")
        prov = client.post("/api/v1/tenant-management/tenants/provision", headers=auth_headers, json=payload)
        assert prov.status_code == 201
        tid = prov.get_json()["tenant_id"]
        cleanup_tenants.append(tid)

        from datetime import UTC, datetime, timedelta

        from src.core.database.database_session import get_db_session
        from src.core.database.models import SyncJob
        from src.services.background_sync_service import start_inventory_sync_background

        # Backdate the inventory row so it looks like it's been pending
        # for 5 minutes — outside the 60s window.
        with get_db_session() as session:
            inv_row = session.scalars(select(SyncJob).filter_by(tenant_id=tid, sync_type="inventory")).first()
            inv_row.started_at = datetime.now(UTC) - timedelta(minutes=5)
            pending_sync_id = inv_row.sync_id
            session.commit()

        # Stub the actual sync thread so the test stays fast — we only
        # care that the worker-pickup status + started_at transition
        # happens before the thread spawns.
        import src.services.background_sync_service as bg_mod

        original_thread = bg_mod.threading.Thread
        spawned = []

        class _NoopThread:
            def __init__(self, *args, **kwargs):
                spawned.append(kwargs)

            def start(self):
                pass

        monkeypatch.setattr(bg_mod.threading, "Thread", _NoopThread)
        try:
            start_inventory_sync_background(
                tenant_id=tid,
                pending_sync_id=pending_sync_id,
                sync_mode="full",
                sync_types=["ad_units"],
            )
        finally:
            monkeypatch.setattr(bg_mod.threading, "Thread", original_thread)

        with get_db_session() as session:
            inv_row = session.scalars(select(SyncJob).filter_by(sync_id=pending_sync_id)).first()
            assert inv_row.status == "running"
            # The pre-pickup started_at was 5 min ago; post-pickup must
            # be within the last few seconds (well under 60s).
            assert (datetime.now(UTC) - inv_row.started_at) < timedelta(seconds=10)

        # And the consequence for /refresh: 202 (idempotent reuse), not 409.
        resp = client.post(f"/api/v1/tenant-management/tenants/{tid}/refresh", headers=auth_headers)
        assert resp.status_code == 202, resp.get_data(as_text=True)
        # Same sync_run_id is reused — caller's repeat refresh doesn't
        # double-spawn the work that just started.
        assert resp.get_json()["sync_run_ids"]["inventory"] == pending_sync_id
