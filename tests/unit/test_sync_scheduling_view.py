"""Unit tests for the scheduling view assembly (#382 Stage 4).

DB-touching tests live in
``tests/integration/test_sync_scheduling_view.py``. These cover the
pure-Python parts: capability filtering, stale verdict logic, dict
serialization.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

from src.core.database.repositories.adapter_config import TenantAdapterRow
from src.services.adapter_sync_orchestration import KIND_INVENTORY, KIND_REPORTING
from src.services.sync_scheduling_view import (
    DEFAULT_INVENTORY_WARNING,
    DEFAULT_REPORTING_WARNING,
    FRESHNESS_CRITICAL,
    FRESHNESS_OK,
    FRESHNESS_WARNING,
    LONG_RUNNING_CRITICAL_AFTER,
    LONG_RUNNING_WARNING_AFTER,
    SchedulingRow,
    _build_row,
    _capability_flag,
    _format_age_seconds,
    build_scheduling_matrix,
)


class TestCapabilityFlag:
    """``_capability_flag`` looks at the adapter class's declared
    AdapterCapabilities — instantiating the adapter would require
    credentials we don't have on the admin path."""

    def test_freewheel_supports_both(self):
        assert _capability_flag("freewheel", KIND_INVENTORY) is True
        assert _capability_flag("freewheel", KIND_REPORTING) is True

    def test_gam_supports_inventory_only(self):
        # GAM async inventory sync goes through background_sync_service;
        # reporting isn't a separate sync — line-item stats ride with inventory.
        assert _capability_flag("google_ad_manager", KIND_INVENTORY) is True
        assert _capability_flag("google_ad_manager", KIND_REPORTING) is False

    def test_mock_supports_neither(self):
        assert _capability_flag("mock", KIND_INVENTORY) is False
        assert _capability_flag("mock", KIND_REPORTING) is False

    def test_unknown_adapter_returns_false(self):
        assert _capability_flag("nonexistent", KIND_INVENTORY) is False


class TestBuildRowNeverRun:
    """A supported triple with no SyncJob → ``never_run=True`` and
    ``freshness='critical'`` (action needed — admin should kick off a sync)."""

    def test_never_run_is_critical(self):
        row = _build_row(
            tenant_id="t1",
            tenant_name="Tenant One",
            adapter_type="freewheel",
            sync_kind=KIND_INVENTORY,
            job=None,
            now=datetime.now(UTC),
        )
        assert row.never_run is True
        assert row.freshness == FRESHNESS_CRITICAL
        assert row.stale is True  # back-compat alias
        assert row.last_status is None
        assert row.last_sync_id is None


class TestBuildRowFreshness:
    """Three-state verdict: ``ok`` / ``warning`` / ``critical``.

    Only ``completed`` runs can be ``ok``. Failures and queued/running
    rows default to ``warning`` or ``critical`` depending on prior state.
    """

    def _job(self, *, status, completed_at, started_at=None):
        job = MagicMock()
        job.status = status
        job.completed_at = completed_at
        job.started_at = started_at or completed_at
        job.sync_id = "sync_abc"
        job.error_message = None
        return job

    def test_recent_completed_inventory_is_ok(self):
        now = datetime.now(UTC)
        row = _build_row(
            tenant_id="t1",
            tenant_name="T",
            adapter_type="freewheel",
            sync_kind=KIND_INVENTORY,
            job=self._job(status="completed", completed_at=now - timedelta(hours=1)),
            now=now,
        )
        assert row.freshness == FRESHNESS_OK
        assert row.stale is False
        assert row.never_run is False

    def test_completed_past_warning_but_before_critical_is_warning(self):
        # Just past the default adapter warning, still inside the default
        # adapter critical window.
        now = datetime.now(UTC)
        row = _build_row(
            tenant_id="t1",
            tenant_name="T",
            adapter_type="freewheel",
            sync_kind=KIND_INVENTORY,
            job=self._job(
                status="completed",
                completed_at=now - (DEFAULT_INVENTORY_WARNING + timedelta(minutes=1)),
            ),
            now=now,
        )
        assert row.freshness == FRESHNESS_WARNING

    def test_inventory_freshness_respects_tenant_cadence(self):
        now = datetime.now(UTC)
        row = _build_row(
            tenant_id="t1",
            tenant_name="T",
            adapter_type="google_ad_manager",
            sync_kind=KIND_INVENTORY,
            job=self._job(status="completed", completed_at=now - timedelta(minutes=150)),
            now=now,
            sync_cadence_minutes=120,
        )
        assert row.freshness == FRESHNESS_WARNING

    def test_completed_past_critical_is_critical(self):
        now = datetime.now(UTC)
        row = _build_row(
            tenant_id="t1",
            tenant_name="T",
            adapter_type="freewheel",
            sync_kind=KIND_INVENTORY,
            job=self._job(
                status="completed",
                completed_at=now - timedelta(hours=96),  # past 72h critical
            ),
            now=now,
        )
        assert row.freshness == FRESHNESS_CRITICAL

    def test_reporting_warning_at_2h_critical_at_6h(self):
        now = datetime.now(UTC)
        fresh = _build_row(
            tenant_id="t1",
            tenant_name="T",
            adapter_type="freewheel",
            sync_kind=KIND_REPORTING,
            job=self._job(status="completed", completed_at=now - timedelta(minutes=90)),
            now=now,
        )
        warn = _build_row(
            tenant_id="t1",
            tenant_name="T",
            adapter_type="freewheel",
            sync_kind=KIND_REPORTING,
            job=self._job(
                status="completed",
                completed_at=now - (DEFAULT_REPORTING_WARNING + timedelta(minutes=1)),
            ),
            now=now,
        )
        critical = _build_row(
            tenant_id="t1",
            tenant_name="T",
            adapter_type="freewheel",
            sync_kind=KIND_REPORTING,
            job=self._job(status="completed", completed_at=now - timedelta(hours=8)),
            now=now,
        )
        assert fresh.freshness == FRESHNESS_OK
        assert warn.freshness == FRESHNESS_WARNING
        assert critical.freshness == FRESHNESS_CRITICAL

    def test_failed_run_is_critical(self):
        # Even a recently-failed run doesn't refresh the cache — the
        # underlying data is whatever it was before. Critical.
        now = datetime.now(UTC)
        row = _build_row(
            tenant_id="t1",
            tenant_name="T",
            adapter_type="freewheel",
            sync_kind=KIND_REPORTING,
            job=self._job(status="failed", completed_at=now - timedelta(minutes=5)),
            now=now,
        )
        assert row.freshness == FRESHNESS_CRITICAL

    def test_running_is_warning_not_critical(self):
        # In-flight is soft-stale, not red.
        now = datetime.now(UTC)
        job = MagicMock()
        job.status = "running"
        job.started_at = now - timedelta(minutes=5)
        job.completed_at = None
        job.sync_id = "sync_abc"
        job.error_message = None
        row = _build_row(
            tenant_id="t1",
            tenant_name="T",
            adapter_type="freewheel",
            sync_kind=KIND_INVENTORY,
            job=job,
            now=now,
        )
        assert row.freshness == FRESHNESS_WARNING
        assert row.long_running is False
        assert row.last_run_age_seconds is not None

    def test_running_past_warning_threshold_is_marked_long_running(self):
        now = datetime.now(UTC)
        job = MagicMock()
        job.status = "running"
        job.started_at = now - (LONG_RUNNING_WARNING_AFTER + timedelta(seconds=1))
        job.completed_at = None
        job.sync_id = "sync_long"
        job.error_message = None

        row = _build_row(
            tenant_id="t1",
            tenant_name="T",
            adapter_type="freewheel",
            sync_kind=KIND_INVENTORY,
            job=job,
            now=now,
        )

        assert row.freshness == FRESHNESS_WARNING
        assert row.long_running is True
        assert row.last_run_age_label == "30m"
        assert row.notes == "sync has been in flight longer than expected"

    def test_running_past_critical_threshold_is_critical(self):
        now = datetime.now(UTC)
        job = MagicMock()
        job.status = "running"
        job.started_at = now - (LONG_RUNNING_CRITICAL_AFTER + timedelta(seconds=1))
        job.completed_at = None
        job.sync_id = "sync_wedged"
        job.error_message = None

        row = _build_row(
            tenant_id="t1",
            tenant_name="T",
            adapter_type="freewheel",
            sync_kind=KIND_INVENTORY,
            job=job,
            now=now,
        )

        assert row.freshness == FRESHNESS_CRITICAL
        assert row.long_running is True
        assert row.last_run_age_label == "1h"

    def test_pending_counts_as_in_flight_for_long_running_detection(self):
        now = datetime.now(UTC)
        job = MagicMock()
        job.status = "pending"
        job.started_at = now - (LONG_RUNNING_CRITICAL_AFTER + timedelta(minutes=5))
        job.completed_at = None
        job.sync_id = "sync_pending"
        job.error_message = None

        row = _build_row(
            tenant_id="t1",
            tenant_name="T",
            adapter_type="freewheel",
            sync_kind=KIND_INVENTORY,
            job=job,
            now=now,
        )

        assert row.freshness == FRESHNESS_CRITICAL
        assert row.long_running is True

    def test_gam_row_gets_bundled_notes(self):
        now = datetime.now(UTC)
        row = _build_row(
            tenant_id="t1",
            tenant_name="T",
            adapter_type="google_ad_manager",
            sync_kind=KIND_INVENTORY,
            job=None,
            now=now,
        )
        assert row.notes is not None
        assert "bundled" in row.notes.lower()


class TestSchedulingRowToDict:
    def test_dict_shape_matches_api_contract(self):
        now = datetime.now(UTC)
        row = SchedulingRow(
            tenant_id="t1",
            tenant_name="Tenant One",
            adapter_type="freewheel",
            sync_kind=KIND_INVENTORY,
            supported=True,
            last_status="completed",
            last_started_at=now - timedelta(minutes=10),
            last_completed_at=now - timedelta(minutes=8),
            last_sync_id="sync_abc",
            last_error_message=None,
            freshness=FRESHNESS_OK,
            never_run=False,
            last_run_age_seconds=600,
            last_run_age_label="10m",
            long_running=False,
            long_running_threshold_seconds=1800,
        )
        d = row.to_dict()
        assert d["tenant_id"] == "t1"
        assert d["sync_kind"] == "inventory"
        assert d["freshness"] == "ok"
        assert d["stale"] is False  # back-compat alias
        assert d["never_run"] is False
        assert d["last_sync_id"] == "sync_abc"
        assert d["freshness_age_seconds"] is not None
        assert d["freshness_age_seconds"] >= 0
        assert d["last_run_age_seconds"] is not None
        assert d["last_run_age_label"] is not None
        assert d["long_running"] is False
        assert d["long_running_threshold_seconds"] == 1800
        assert d["notes"] is None

    def test_age_formatting_matches_api_labels(self):
        assert _format_age_seconds(None) is None
        assert _format_age_seconds(42) == "42s"
        assert _format_age_seconds(20 * 60) == "20m"
        assert _format_age_seconds(65 * 60) == "1h 5m"
        assert _format_age_seconds(49 * 60 * 60) == "2d 1h"


class TestBuildMatrixSkipsUnsupportedKinds:
    """When a tenant's adapter declares ``supports_reporting_sync=False``
    (e.g. GAM), the matrix only contains an inventory row for it — the
    page doesn't fabricate a fake "reporting" slot.

    Driven by the AdapterCapabilities flag, not by config flags."""

    def test_gam_tenant_only_yields_inventory_row(self, monkeypatch):
        session = MagicMock()

        def fake_list_all(self):
            return [TenantAdapterRow(tenant_id="tg", tenant_name="G", adapter_type="google_ad_manager")]

        def fake_latest_for_triples(self, triples):
            return {}

        monkeypatch.setattr(
            "src.services.sync_scheduling_view.AdapterConfigAdminRepository.list_all",
            fake_list_all,
        )
        monkeypatch.setattr(
            "src.services.sync_scheduling_view.SyncJobAdminRepository.latest_for_triples",
            fake_latest_for_triples,
        )

        rows = build_scheduling_matrix(session)
        assert len(rows) == 1
        assert rows[0].sync_kind == KIND_INVENTORY
        assert rows[0].adapter_type == "google_ad_manager"
        assert rows[0].never_run is True
