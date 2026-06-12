"""Regression tests for inapplicable GAM-derived streams in tenant status."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from src.admin.services.tenant_status_service import _syncs_block


class _EmptySyncJobRepository:
    def __init__(self, session, tenant_id: str) -> None:
        self.session = session
        self.tenant_id = tenant_id

    def health_inputs_for_stream(self, *, adapter_type: str, sync_type: str) -> list:
        return []


def test_inapplicable_gam_derived_streams_do_not_report_critical_never_run() -> None:
    tenant = SimpleNamespace(tenant_id="tenant_1", created_at=datetime.now(UTC) - timedelta(hours=2))

    with (
        patch("src.admin.services.tenant_status_service.SyncJobRepository", _EmptySyncJobRepository),
        patch("src.admin.services.tenant_status_service.gam_signal_coverage_applicable", return_value=False),
        patch("src.admin.services.tenant_status_service.gam_pricing_availability_applicable", return_value=False),
        patch("src.admin.services.tenant_status_service.gam_reporting_applicable", return_value=False),
    ):
        syncs = _syncs_block(SimpleNamespace(), tenant, "google_ad_manager")

    assert syncs.inventory.status == "never_run"
    assert syncs.inventory.severity == "critical"

    assert syncs.reporting.status == "success"
    assert syncs.reporting.severity == "ok"
    assert syncs.reporting.issue is None
    assert syncs.reporting.item_count == 0

    assert syncs.signal_coverage.status == "success"
    assert syncs.signal_coverage.severity == "ok"
    assert syncs.signal_coverage.issue is None
    assert syncs.signal_coverage.item_count == 0

    assert syncs.pricing_availability.status == "success"
    assert syncs.pricing_availability.severity == "ok"
    assert syncs.pricing_availability.issue is None
    assert syncs.pricing_availability.item_count == 0
