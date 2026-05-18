"""Tests for InventoryReviewState — repository + bundle save-time sync + dashboard surface (#485).

Covers:

* Repository state machine: pending → in_bundle → pending → explicitly_skipped → in_bundle
* ``coverage_summary`` returns counts for all three statuses, defaulting missing to 0
* Bundle save reconciliation: adding an ad unit to a bundle promotes it;
  removing it demotes back to pending (not auto-skip)
* Dashboard ``get_dashboard_jobs()`` surfaces real coverage when tenant
  is on GAM, ``None`` otherwise
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from src.admin.app import create_app
from src.core.database.models import InventoryReviewState
from src.core.database.repositories.inventory_review_state import (
    InventoryReviewStateRepository,
)
from src.services.inventory_review_state_sync import recompute_in_bundle_status
from src.services.setup_checklist_service import SetupChecklistService
from tests.factories import (
    InventoryProfileFactory,
    InventoryReviewStateFactory,
    TenantFactory,
)

pytestmark = pytest.mark.requires_db


@pytest.fixture(autouse=True)
def _flask_request_context():
    """SetupChecklistService.action_url uses url_for; needs a request ctx."""
    app = create_app({"TESTING": True, "SECRET_KEY": "test", "WTF_CSRF_ENABLED": False})
    with app.test_request_context():
        yield


class TestRepositoryCoverageSummary:
    """``coverage_summary`` returns counts grouped by status."""

    def test_empty_tenant_returns_zero_for_all_statuses(self, factory_session):
        tenant = TenantFactory()

        repo = InventoryReviewStateRepository(factory_session, tenant.tenant_id)
        summary = repo.coverage_summary(adapter="gam", entity_type="ad_unit")

        assert summary == {
            "pending": 0,
            "in_bundle": 0,
            "explicitly_skipped": 0,
            "total": 0,
        }

    def test_counts_split_by_status(self, factory_session):
        tenant = TenantFactory()
        for status, n in [("pending", 3), ("in_bundle", 5), ("explicitly_skipped", 2)]:
            for _ in range(n):
                InventoryReviewStateFactory(tenant=tenant, tenant_id=tenant.tenant_id, status=status)

        repo = InventoryReviewStateRepository(factory_session, tenant.tenant_id)
        summary = repo.coverage_summary(adapter="gam", entity_type="ad_unit")

        assert summary["pending"] == 3
        assert summary["in_bundle"] == 5
        assert summary["explicitly_skipped"] == 2
        assert summary["total"] == 10

    def test_coverage_filtered_by_entity_type(self, factory_session):
        """Ad units and placements are counted separately."""
        tenant = TenantFactory()
        InventoryReviewStateFactory(
            tenant=tenant, tenant_id=tenant.tenant_id, entity_type="ad_unit", status="in_bundle"
        )
        InventoryReviewStateFactory(
            tenant=tenant, tenant_id=tenant.tenant_id, entity_type="placement", status="in_bundle"
        )

        repo = InventoryReviewStateRepository(factory_session, tenant.tenant_id)

        assert repo.coverage_summary(adapter="gam", entity_type="ad_unit")["total"] == 1
        assert repo.coverage_summary(adapter="gam", entity_type="placement")["total"] == 1

    def test_coverage_scoped_to_tenant(self, factory_session):
        tenant_a = TenantFactory()
        tenant_b = TenantFactory()
        InventoryReviewStateFactory(tenant=tenant_a, tenant_id=tenant_a.tenant_id, status="in_bundle")
        InventoryReviewStateFactory(tenant=tenant_b, tenant_id=tenant_b.tenant_id, status="in_bundle")

        a = InventoryReviewStateRepository(factory_session, tenant_a.tenant_id).coverage_summary(
            adapter="gam", entity_type="ad_unit"
        )
        b = InventoryReviewStateRepository(factory_session, tenant_b.tenant_id).coverage_summary(
            adapter="gam", entity_type="ad_unit"
        )

        assert a["in_bundle"] == 1
        assert b["in_bundle"] == 1  # not summed across tenants


class TestSyncInBundleStatus:
    """``sync_in_bundle_status`` reconciles the in_bundle set."""

    def test_promotes_pending_to_in_bundle(self, factory_session):
        tenant = TenantFactory()
        InventoryReviewStateFactory(tenant=tenant, tenant_id=tenant.tenant_id, external_id="ad_1", status="pending")

        repo = InventoryReviewStateRepository(factory_session, tenant.tenant_id)
        repo.sync_in_bundle_status(adapter="gam", entity_type="ad_unit", in_bundle_ids=["ad_1"])
        factory_session.flush()

        summary = repo.coverage_summary(adapter="gam", entity_type="ad_unit")
        assert summary["in_bundle"] == 1
        assert summary["pending"] == 0

    def test_promotes_explicitly_skipped_to_in_bundle(self, factory_session):
        """Operator changes their mind: an explicitly-skipped ad unit gets
        added to a bundle. The bundle reference wins — promote to in_bundle."""
        tenant = TenantFactory()
        InventoryReviewStateFactory(
            tenant=tenant,
            tenant_id=tenant.tenant_id,
            external_id="ad_1",
            status="explicitly_skipped",
        )

        repo = InventoryReviewStateRepository(factory_session, tenant.tenant_id)
        repo.sync_in_bundle_status(adapter="gam", entity_type="ad_unit", in_bundle_ids=["ad_1"])
        factory_session.flush()

        summary = repo.coverage_summary(adapter="gam", entity_type="ad_unit")
        assert summary["in_bundle"] == 1
        assert summary["explicitly_skipped"] == 0

    def test_demotes_orphaned_in_bundle_to_pending(self, factory_session):
        """An ad unit that was in_bundle but no longer referenced demotes
        back to pending — never auto-skipped."""
        tenant = TenantFactory()
        InventoryReviewStateFactory(tenant=tenant, tenant_id=tenant.tenant_id, external_id="ad_1", status="in_bundle")

        repo = InventoryReviewStateRepository(factory_session, tenant.tenant_id)
        repo.sync_in_bundle_status(adapter="gam", entity_type="ad_unit", in_bundle_ids=[])
        factory_session.flush()

        summary = repo.coverage_summary(adapter="gam", entity_type="ad_unit")
        assert summary["in_bundle"] == 0
        assert summary["pending"] == 1
        assert summary["explicitly_skipped"] == 0

    def test_inserts_new_in_bundle_rows(self, factory_session):
        """First time an ad unit is bundled, no prior row exists — insert one."""
        tenant = TenantFactory()
        repo = InventoryReviewStateRepository(factory_session, tenant.tenant_id)

        repo.sync_in_bundle_status(adapter="gam", entity_type="ad_unit", in_bundle_ids=["ad_1", "ad_2"])
        factory_session.flush()

        summary = repo.coverage_summary(adapter="gam", entity_type="ad_unit")
        assert summary["in_bundle"] == 2

    def test_separate_entity_types_isolated(self, factory_session):
        """Reconciling ad_unit doesn't touch placement rows."""
        tenant = TenantFactory()
        InventoryReviewStateFactory(
            tenant=tenant,
            tenant_id=tenant.tenant_id,
            entity_type="placement",
            external_id="p_1",
            status="in_bundle",
        )

        repo = InventoryReviewStateRepository(factory_session, tenant.tenant_id)
        repo.sync_in_bundle_status(adapter="gam", entity_type="ad_unit", in_bundle_ids=[])
        factory_session.flush()

        assert repo.coverage_summary(adapter="gam", entity_type="placement")["in_bundle"] == 1


class TestMarkSkipped:
    def test_mark_skipped_creates_row_when_absent(self, factory_session):
        tenant = TenantFactory()
        repo = InventoryReviewStateRepository(factory_session, tenant.tenant_id)

        repo.mark_skipped(adapter="gam", entity_type="ad_unit", external_id="ad_1", reviewed_by="ops@example.com")
        factory_session.flush()

        row = factory_session.scalars(
            select(InventoryReviewState).where(InventoryReviewState.external_id == "ad_1")
        ).first()
        assert row.status == "explicitly_skipped"
        assert row.reviewed_by == "ops@example.com"
        assert row.reviewed_at is not None

    def test_bulk_skip_writes_multiple(self, factory_session):
        tenant = TenantFactory()
        repo = InventoryReviewStateRepository(factory_session, tenant.tenant_id)

        repo.mark_skipped_bulk(
            adapter="gam",
            entity_type="ad_unit",
            external_ids=["a", "b", "c"],
            reviewed_by="ops@example.com",
        )
        factory_session.flush()

        summary = repo.coverage_summary(adapter="gam", entity_type="ad_unit")
        assert summary["explicitly_skipped"] == 3


class TestBundleSaveTimeSync:
    """``recompute_in_bundle_status`` reconciles the union of all bundles."""

    def test_adding_a_bundle_promotes_referenced_entities(self, factory_session):
        tenant = TenantFactory(ad_server="google_ad_manager")
        # Operator already explicitly skipped ad_1 earlier
        InventoryReviewStateFactory(
            tenant=tenant,
            tenant_id=tenant.tenant_id,
            external_id="ad_1",
            status="explicitly_skipped",
        )
        # New bundle references ad_1 + ad_2
        InventoryProfileFactory(
            tenant=tenant,
            tenant_id=tenant.tenant_id,
            inventory_config={"ad_units": ["ad_1", "ad_2"], "placements": [], "include_descendants": True},
        )

        recompute_in_bundle_status(factory_session, tenant.tenant_id)
        factory_session.flush()

        repo = InventoryReviewStateRepository(factory_session, tenant.tenant_id)
        summary = repo.coverage_summary(adapter="gam", entity_type="ad_unit")
        assert summary["in_bundle"] == 2
        assert summary["explicitly_skipped"] == 0

    def test_no_op_for_untracked_adapter(self, factory_session):
        """Tenants on non-GAM adapters don't write review-state rows yet."""
        tenant = TenantFactory(ad_server="mock")
        InventoryProfileFactory(
            tenant=tenant,
            tenant_id=tenant.tenant_id,
            inventory_config={"ad_units": ["ad_1"], "placements": [], "include_descendants": True},
        )

        recompute_in_bundle_status(factory_session, tenant.tenant_id)
        factory_session.flush()

        # Nothing should have been written
        rows = factory_session.scalars(
            select(InventoryReviewState).where(InventoryReviewState.tenant_id == tenant.tenant_id)
        ).all()
        assert list(rows) == []

    def test_deleting_a_bundle_demotes_orphans(self, factory_session):
        tenant = TenantFactory(ad_server="google_ad_manager")
        profile = InventoryProfileFactory(
            tenant=tenant,
            tenant_id=tenant.tenant_id,
            inventory_config={"ad_units": ["ad_1"], "placements": [], "include_descendants": True},
        )
        # Sync once to mark ad_1 as in_bundle
        recompute_in_bundle_status(factory_session, tenant.tenant_id)
        factory_session.flush()

        # Now delete the bundle and resync
        factory_session.delete(profile)
        recompute_in_bundle_status(factory_session, tenant.tenant_id)
        factory_session.flush()

        repo = InventoryReviewStateRepository(factory_session, tenant.tenant_id)
        summary = repo.coverage_summary(adapter="gam", entity_type="ad_unit")
        assert summary["in_bundle"] == 0
        assert summary["pending"] == 1  # not auto-skipped


class TestDashboardCoverageSurface:
    """``get_dashboard_jobs()`` surfaces real coverage for tracked adapters."""

    def test_gam_tenant_includes_coverage(self, factory_session):
        tenant = TenantFactory(ad_server="google_ad_manager")
        InventoryReviewStateFactory(tenant=tenant, tenant_id=tenant.tenant_id, external_id="ad_1", status="in_bundle")
        InventoryReviewStateFactory(tenant=tenant, tenant_id=tenant.tenant_id, external_id="ad_2", status="pending")

        result = SetupChecklistService(tenant.tenant_id).get_dashboard_jobs()

        bundles_sub = next(s for s in result["jobs"][0]["sub_items"] if s["key"] == "bundles")
        cov = bundles_sub["coverage"]
        assert cov["adapter"] == "gam"
        assert cov["ad_units"]["in_bundle"] == 1
        assert cov["ad_units"]["pending"] == 1
        assert cov["has_synced_inventory"] is True
        assert cov["all_reviewed"] is False

    def test_non_gam_tenant_has_no_coverage(self, factory_session):
        """Mock-adapter tenant: coverage is None — the widget falls back to the placeholder hint."""
        tenant = TenantFactory(ad_server="mock")

        result = SetupChecklistService(tenant.tenant_id).get_dashboard_jobs()

        bundles_sub = next(s for s in result["jobs"][0]["sub_items"] if s["key"] == "bundles")
        assert bundles_sub["coverage"] is None

    def test_all_reviewed_when_no_pending(self, factory_session):
        tenant = TenantFactory(ad_server="google_ad_manager")
        InventoryReviewStateFactory(tenant=tenant, tenant_id=tenant.tenant_id, external_id="ad_1", status="in_bundle")
        InventoryReviewStateFactory(
            tenant=tenant,
            tenant_id=tenant.tenant_id,
            external_id="ad_2",
            status="explicitly_skipped",
        )

        result = SetupChecklistService(tenant.tenant_id).get_dashboard_jobs()

        cov = next(s for s in result["jobs"][0]["sub_items"] if s["key"] == "bundles")["coverage"]
        assert cov["all_reviewed"] is True

    def test_signals_coverage_is_none_pending_486(self, factory_session):
        """Signal coverage lands in #486 — for now signals.coverage is always None."""
        tenant = TenantFactory(ad_server="google_ad_manager")

        result = SetupChecklistService(tenant.tenant_id).get_dashboard_jobs()

        signals_sub = next(s for s in result["jobs"][0]["sub_items"] if s["key"] == "signals")
        assert signals_sub["coverage"] is None
