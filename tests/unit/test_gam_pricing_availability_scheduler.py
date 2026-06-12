"""Unit tests for scheduled GAM pricing/availability guidance sync."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.services.gam_pricing_availability_scheduler import (
    PRICING_AVAILABILITY_STALE_AFTER,
    GAMPricingAvailabilityScheduler,
    _list_eligible_tenants,
)
from src.services.gam_pricing_availability_sync import KIND_PRICING_AVAILABILITY


def _pair(tenant_id: str, adapter_type: str = "google_ad_manager") -> SimpleNamespace:
    return SimpleNamespace(tenant_id=tenant_id, adapter_type=adapter_type)


def _job(status: str, completed_at: datetime | None = None) -> SimpleNamespace:
    return SimpleNamespace(status=status, completed_at=completed_at)


def test_list_eligible_tenants_includes_gam_tenants_with_inventory_targets() -> None:
    now = datetime(2026, 5, 28, tzinfo=UTC)
    with (
        patch("src.services.gam_pricing_availability_scheduler.get_db_session"),
        patch("src.services.gam_pricing_availability_scheduler.AdapterConfigAdminRepository") as adapter_repo,
        patch("src.services.gam_pricing_availability_scheduler.SyncJobAdminRepository") as sync_repo,
        patch(
            "src.services.gam_pricing_availability_scheduler.tenant_has_pricing_availability_targets"
        ) as has_products,
    ):
        adapter_repo.return_value.list_all.return_value = [_pair("tenant_1"), _pair("tenant_2", "mock")]
        has_products.return_value = True
        sync_repo.return_value.latest_for_triples.return_value = {}

        assert _list_eligible_tenants(now) == ["tenant_1"]
        sync_repo.return_value.latest_for_triples.assert_called_once_with(
            [("tenant_1", "google_ad_manager", KIND_PRICING_AVAILABILITY)]
        )


def test_list_eligible_tenants_skips_fresh_completed_run() -> None:
    now = datetime(2026, 5, 28, tzinfo=UTC)
    with (
        patch("src.services.gam_pricing_availability_scheduler.get_db_session"),
        patch("src.services.gam_pricing_availability_scheduler.AdapterConfigAdminRepository") as adapter_repo,
        patch("src.services.gam_pricing_availability_scheduler.SyncJobAdminRepository") as sync_repo,
        patch(
            "src.services.gam_pricing_availability_scheduler.tenant_has_pricing_availability_targets",
            return_value=True,
        ),
    ):
        adapter_repo.return_value.list_all.return_value = [_pair("tenant_1")]
        sync_repo.return_value.latest_for_triples.return_value = {
            ("tenant_1", "google_ad_manager", KIND_PRICING_AVAILABILITY): _job(
                "completed", now - PRICING_AVAILABILITY_STALE_AFTER / 2
            )
        }

        assert _list_eligible_tenants(now) == []


@pytest.mark.asyncio
async def test_run_once_dispatches_pricing_availability_sync() -> None:
    scheduler = GAMPricingAvailabilityScheduler()
    result = SimpleNamespace(sync_id="sync_123", succeeded=True, errors={})
    with (
        patch("src.services.gam_pricing_availability_scheduler._list_eligible_tenants", return_value=["tenant_1"]),
        patch(
            "src.services.gam_pricing_availability_scheduler.run_gam_pricing_availability_sync", return_value=result
        ) as run_sync,
    ):
        dispatched = await scheduler.run_once(now=datetime(2026, 5, 28, tzinfo=UTC))

    assert dispatched == ["sync_123"]
    run_sync.assert_called_once_with(tenant_id="tenant_1", triggered_by="scheduler_pricing_availability")
