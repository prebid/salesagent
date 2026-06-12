"""Unit tests for the GAM signal coverage scheduler."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from src.services.gam_signal_coverage_scheduler import (
    SIGNAL_COVERAGE_STALE_AFTER,
    GAMSignalCoverageScheduler,
    _list_eligible_tenants,
)
from src.services.gam_signal_coverage_sync import KIND_SIGNAL_COVERAGE, SignalCoverageSyncResult


def _pair(tenant_id: str, adapter_type: str) -> MagicMock:
    pair = MagicMock()
    pair.tenant_id = tenant_id
    pair.adapter_type = adapter_type
    return pair


def _patch_eligibility_layer(monkeypatch, *, pairs, latest_map, has_signals: bool = True) -> None:
    import contextlib

    monkeypatch.setattr(
        "src.services.gam_signal_coverage_scheduler.AdapterConfigAdminRepository.list_all",
        lambda self: pairs,
    )
    monkeypatch.setattr(
        "src.services.gam_signal_coverage_scheduler.SyncJobAdminRepository.latest_for_triples",
        lambda self, triples: latest_map,
    )
    monkeypatch.setattr(
        "src.services.gam_signal_coverage_scheduler.tenant_has_custom_key_value_signals",
        lambda session, tenant_id: has_signals,
    )
    monkeypatch.setattr(
        "src.services.gam_signal_coverage_scheduler.get_db_session",
        lambda: contextlib.nullcontext(MagicMock()),
    )


def test_list_eligible_tenants_only_includes_gam_with_signals(monkeypatch) -> None:
    _patch_eligibility_layer(
        monkeypatch,
        pairs=[_pair("t_gam", "google_ad_manager"), _pair("t_fw", "freewheel")],
        latest_map={},
    )

    assert _list_eligible_tenants(datetime.now(UTC)) == ["t_gam"]


def test_list_eligible_tenants_skips_fresh_completed_run(monkeypatch) -> None:
    now = datetime.now(UTC)
    recent = MagicMock()
    recent.status = "completed"
    recent.completed_at = now - (SIGNAL_COVERAGE_STALE_AFTER - timedelta(minutes=5))

    _patch_eligibility_layer(
        monkeypatch,
        pairs=[_pair("t_gam", "google_ad_manager")],
        latest_map={("t_gam", "google_ad_manager", KIND_SIGNAL_COVERAGE): recent},
    )

    assert _list_eligible_tenants(now) == []


def test_list_eligible_tenants_retries_failed_run(monkeypatch) -> None:
    failed = MagicMock()
    failed.status = "failed"
    failed.completed_at = datetime.now(UTC)

    _patch_eligibility_layer(
        monkeypatch,
        pairs=[_pair("t_gam", "google_ad_manager")],
        latest_map={("t_gam", "google_ad_manager", KIND_SIGNAL_COVERAGE): failed},
    )

    assert _list_eligible_tenants(datetime.now(UTC)) == ["t_gam"]


@pytest.mark.asyncio
async def test_run_once_dispatches_signal_coverage_sync(monkeypatch) -> None:
    monkeypatch.setattr(
        "src.services.gam_signal_coverage_scheduler._list_eligible_tenants",
        lambda now: ["t_gam"],
    )

    calls: list[tuple[str, str]] = []

    def fake_sync(*, tenant_id, triggered_by):
        calls.append((tenant_id, triggered_by))
        return SignalCoverageSyncResult(
            sync_id="sync_t_gam",
            tenant_id=tenant_id,
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
            succeeded=True,
        )

    monkeypatch.setattr(
        "src.services.gam_signal_coverage_scheduler.run_gam_signal_coverage_sync",
        fake_sync,
    )

    scheduler = GAMSignalCoverageScheduler()
    assert await scheduler.run_once() == ["sync_t_gam"]
    assert calls == [("t_gam", "scheduler_signal_coverage")]


@pytest.mark.asyncio
async def test_scheduler_stop_cancels_running_task(monkeypatch) -> None:
    async def long_loop(self):
        while True:
            await asyncio.sleep(60)

    monkeypatch.setattr(GAMSignalCoverageScheduler, "_run", long_loop)

    scheduler = GAMSignalCoverageScheduler()
    await scheduler.start()
    await scheduler.stop()

    assert scheduler.is_running is False
    assert scheduler._task is not None
    assert scheduler._task.cancelled() or scheduler._task.done()
