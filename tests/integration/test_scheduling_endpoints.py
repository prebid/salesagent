"""Integration tests for the ``/admin/scheduling`` endpoints (#382 Stage 4).

Covers the Flask-routing layer: super-admin gating, JSON payload shape,
Run Now dispatch through the shared orchestrator.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.services.adapter_sync_orchestration import AdapterDoesNotSupportSyncKind
from tests.factories import AdapterConfigFactory, TenantFactory

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


class TestSchedulingIndexRequiresSuperAdmin:
    """Anonymous request → 302 to login; tenant_admin role → 403."""

    def test_anonymous_redirects_to_login(self, admin_client):
        resp = admin_client.get("/admin/scheduling")
        # ``require_auth`` redirects to login when no session.
        assert resp.status_code in (302, 303)
        assert "/login" in resp.headers.get("Location", "")


class TestSchedulingIndexRendersForSuperAdmin:
    def test_page_renders_with_configured_tenant_row(self, authenticated_admin_session, factory_session):
        t = TenantFactory(tenant_id="t_idx", name="Index Co")
        AdapterConfigFactory(tenant=t, adapter_type="freewheel")

        resp = authenticated_admin_session.get("/admin/scheduling")
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert "Adapter Sync Scheduling" in body
        assert "Index Co" in body
        assert "freewheel" in body
        # Both kinds should render — FW supports both.
        assert "inventory" in body
        assert "reporting" in body


class TestJobsApiReturnsMatrixJson:
    def test_jobs_endpoint_returns_rows(self, authenticated_admin_session, factory_session):
        t = TenantFactory(tenant_id="t_json", name="JSON Co")
        AdapterConfigFactory(tenant=t, adapter_type="freewheel")

        resp = authenticated_admin_session.get("/admin/api/scheduling/jobs")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "rows" in data
        rows = [r for r in data["rows"] if r["tenant_id"] == "t_json"]
        assert len(rows) == 2  # inventory + reporting
        kinds = {r["sync_kind"] for r in rows}
        assert kinds == {"inventory", "reporting"}
        assert all(r["never_run"] is True for r in rows)
        assert all("long_running" in r for r in rows)
        assert all("last_run_age_seconds" in r for r in rows)
        assert all("last_run_age_label" in r for r in rows)


class TestRunNowValidatesBody:
    def test_missing_fields_returns_400(self, authenticated_admin_session):
        resp = authenticated_admin_session.post("/admin/api/scheduling/run", json={})
        assert resp.status_code == 400
        assert "required" in resp.get_json()["error"]

    def test_unknown_sync_kind_returns_400(self, authenticated_admin_session):
        resp = authenticated_admin_session.post(
            "/admin/api/scheduling/run",
            json={"tenant_id": "t1", "adapter_type": "freewheel", "sync_kind": "foobar"},
        )
        assert resp.status_code == 400
        assert "sync_kind" in resp.get_json()["error"]


class TestRunNowEnqueuesAsync:
    """The endpoint delegates to ``enqueue_adapter_sync`` which returns
    immediately with a sync_id. The actual adapter work happens on a
    daemon thread (covered by orchestrator tests, not these)."""

    def test_successful_enqueue_returns_202_with_sync_id(self, authenticated_admin_session, factory_session):
        t = TenantFactory(tenant_id="t_run", name="Run Co")
        AdapterConfigFactory(tenant=t, adapter_type="freewheel")

        with patch(
            "src.admin.blueprints.scheduling.enqueue_adapter_sync",
            return_value="sync_test_ok",
        ) as mock_enq:
            resp = authenticated_admin_session.post(
                "/admin/api/scheduling/run",
                json={
                    "tenant_id": "t_run",
                    "adapter_type": "freewheel",
                    "sync_kind": "inventory",
                },
            )

        assert resp.status_code == 202
        body = resp.get_json()
        assert body["sync_id"] == "sync_test_ok"
        assert body["status"] == "queued"
        # Admin identity flows from g.user (test fixture sets "test@example.com")
        # through to ``triggered_by_id`` — proves the audit attribution path.
        assert body["triggered_by_id"] == "test@example.com"
        mock_enq.assert_called_once_with(
            tenant_id="t_run",
            adapter_type="freewheel",
            sync_kind="inventory",
            triggered_by="admin_scheduling_ui",
            triggered_by_id="test@example.com",
        )

    def test_successful_enqueue_reflects_running_job_status(self, authenticated_admin_session, factory_session):
        t = TenantFactory(tenant_id="t_run_status", name="Run Status Co")
        AdapterConfigFactory(tenant=t, adapter_type="google_ad_manager")

        with (
            patch(
                "src.admin.blueprints.scheduling.enqueue_adapter_sync",
                return_value="sync_running",
            ),
            patch(
                "src.admin.blueprints.scheduling._sync_status",
                return_value="running",
            ),
        ):
            resp = authenticated_admin_session.post(
                "/admin/api/scheduling/run",
                json={
                    "tenant_id": "t_run_status",
                    "adapter_type": "google_ad_manager",
                    "sync_kind": "inventory",
                },
            )

        assert resp.status_code == 202
        assert resp.get_json()["status"] == "running"

    def test_already_running_inventory_sync_returns_409(self, authenticated_admin_session, factory_session):
        t = TenantFactory(tenant_id="t_conflict", name="Conflict Co")
        AdapterConfigFactory(tenant=t, adapter_type="google_ad_manager")

        with patch(
            "src.admin.blueprints.scheduling.enqueue_adapter_sync",
            side_effect=ValueError("Sync already running for tenant t_conflict: sync_old"),
        ):
            resp = authenticated_admin_session.post(
                "/admin/api/scheduling/run",
                json={
                    "tenant_id": "t_conflict",
                    "adapter_type": "google_ad_manager",
                    "sync_kind": "inventory",
                },
            )

        assert resp.status_code == 409
        assert resp.get_json()["error"] == "sync_already_running"

    def test_capability_off_returns_400(self, authenticated_admin_session, factory_session):
        t = TenantFactory(tenant_id="t_cap", name="Cap Co")
        AdapterConfigFactory(tenant=t, adapter_type="freewheel")

        with patch(
            "src.admin.blueprints.scheduling.enqueue_adapter_sync",
            side_effect=AdapterDoesNotSupportSyncKind(adapter_type="freewheel", sync_kind="inventory"),
        ):
            resp = authenticated_admin_session.post(
                "/admin/api/scheduling/run",
                json={
                    "tenant_id": "t_cap",
                    "adapter_type": "freewheel",
                    "sync_kind": "inventory",
                },
            )
        assert resp.status_code == 400
        assert "supports_inventory_sync" in resp.get_json()["error"]

    def test_unconfigured_tenant_returns_400(self, authenticated_admin_session, factory_session):
        with patch(
            "src.admin.blueprints.scheduling.enqueue_adapter_sync",
            return_value=None,
        ):
            resp = authenticated_admin_session.post(
                "/admin/api/scheduling/run",
                json={
                    "tenant_id": "t_unknown",
                    "adapter_type": "freewheel",
                    "sync_kind": "inventory",
                },
            )
        assert resp.status_code == 400
        assert "not configured" in resp.get_json()["error"]
