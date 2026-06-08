"""Integration tests for ``SyncJobRepository`` against real PostgreSQL.

Covers the contracts that the order-approval polling flow depends on:
the ``flag_modified`` JSONB-merge guard, tenant isolation on every read
and write, terminal-status writes, the staleness reaper, and the
duplicate-order scan.

Writes go through ``SyncJobUoW`` so commits happen at the transaction
boundary. Verification reads use a fresh ``get_db_session()`` to defeat
SQLAlchemy's identity map.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from src.core.database.database_session import get_db_session
from src.core.database.models import SyncJob
from src.core.database.repositories import SyncJobUoW
from tests.harness._base import IntegrationEnv

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


class _SetupEnv(IntegrationEnv):
    """Bare integration env — used only for factory-based test data setup."""

    EXTERNAL_PATCHES: dict[str, str] = {}


def _setup_tenants_with_sync_jobs(setup_fn) -> None:
    """Execute ``setup_fn(env)`` inside a ``_SetupEnv`` and commit factory data."""
    with _SetupEnv() as env:
        setup_fn(env)
        env._commit_factory_data()


class TestMergeProgress:
    def test_merges_jsonb_via_flag_modified(self, integration_db):
        """``merge_progress`` mutates progress in place and persists via flag_modified."""
        from tests.factories import SyncJobFactory, TenantFactory

        def setup(env):
            tenant = TenantFactory(tenant_id="merge_t1")
            SyncJobFactory(
                tenant=tenant,
                sync_id="merge_sync",
                progress={"order_id": "ord_1", "attempts": 0, "phase": "start"},
            )

        _setup_tenants_with_sync_jobs(setup)

        with SyncJobUoW("merge_t1") as uow:
            assert uow.sync_jobs is not None
            uow.sync_jobs.merge_progress("merge_sync", {"attempts": 3, "phase": "polling"})

        with get_db_session() as verify:
            row = verify.scalars(select(SyncJob).where(SyncJob.sync_id == "merge_sync")).first()
        assert row is not None
        assert row.progress == {"order_id": "ord_1", "attempts": 3, "phase": "polling"}

    def test_seeds_when_progress_is_null(self, integration_db):
        """``merge_progress`` seeds the column when progress is NULL."""
        from tests.factories import SyncJobFactory, TenantFactory

        def setup(env):
            tenant = TenantFactory(tenant_id="seed_t1")
            SyncJobFactory(tenant=tenant, sync_id="seed_sync", progress=None)

        _setup_tenants_with_sync_jobs(setup)

        with SyncJobUoW("seed_t1") as uow:
            assert uow.sync_jobs is not None
            uow.sync_jobs.merge_progress("seed_sync", {"order_id": "ord_x", "attempts": 1})

        with get_db_session() as verify:
            row = verify.scalars(select(SyncJob).where(SyncJob.sync_id == "seed_sync")).first()
        assert row is not None
        assert row.progress == {"order_id": "ord_x", "attempts": 1}

    def test_returns_none_for_missing_row(self, integration_db):
        """``merge_progress`` returns ``None`` when no row matches in the tenant."""
        from tests.factories import TenantFactory

        def setup(env):
            TenantFactory(tenant_id="missing_t1")

        _setup_tenants_with_sync_jobs(setup)

        with SyncJobUoW("missing_t1") as uow:
            assert uow.sync_jobs is not None
            result = uow.sync_jobs.merge_progress("nonexistent", {"attempts": 1})

        assert result is None

    def test_does_not_leak_across_tenants(self, integration_db):
        """``merge_progress`` cannot touch another tenant's SyncJob."""
        from tests.factories import SyncJobFactory, TenantFactory

        def setup(env):
            t1 = TenantFactory(tenant_id="iso_merge_t1")
            t2 = TenantFactory(tenant_id="iso_merge_t2")
            SyncJobFactory(tenant=t1, sync_id="iso_sync_a", progress={"order_id": "ord_t1", "attempts": 0})
            SyncJobFactory(tenant=t2, sync_id="iso_sync_b", progress={"order_id": "ord_t2", "attempts": 0})

        _setup_tenants_with_sync_jobs(setup)

        with SyncJobUoW("iso_merge_t1") as uow:
            assert uow.sync_jobs is not None
            # T1's repo trying to update T2's sync_id returns None
            result = uow.sync_jobs.merge_progress("iso_sync_b", {"attempts": 99})

        assert result is None, "Tenant 1's repo found tenant 2's SyncJob — isolation broken"

        with get_db_session() as verify:
            row_t2 = verify.scalars(select(SyncJob).where(SyncJob.sync_id == "iso_sync_b")).first()
        assert row_t2 is not None and row_t2.progress["attempts"] == 0, (
            "Tenant 2's SyncJob was modified by tenant 1 — isolation broken"
        )


class TestMarkTerminal:
    def test_marks_completed_with_dict_summary(self, integration_db):
        """``mark_terminal`` serializes a dict summary as JSON."""
        from tests.factories import SyncJobFactory, TenantFactory

        completed_at = datetime(2026, 5, 26, 12, 0, tzinfo=UTC)

        def setup(env):
            tenant = TenantFactory(tenant_id="term_t1")
            SyncJobFactory(tenant=tenant, sync_id="term_sync", status="running")

        _setup_tenants_with_sync_jobs(setup)

        with SyncJobUoW("term_t1") as uow:
            assert uow.sync_jobs is not None
            uow.sync_jobs.mark_terminal(
                "term_sync",
                status="completed",
                completed_at=completed_at,
                summary={"order_id": "ord_done", "attempts": 4},
            )

        with get_db_session() as verify:
            row = verify.scalars(select(SyncJob).where(SyncJob.sync_id == "term_sync")).first()
        assert row is not None
        assert row.status == "completed"
        assert row.completed_at.replace(tzinfo=UTC) == completed_at
        assert json.loads(row.summary) == {"order_id": "ord_done", "attempts": 4}

    def test_marks_failed_with_error_message(self, integration_db):
        """``mark_terminal`` records the failure message."""
        from tests.factories import SyncJobFactory, TenantFactory

        def setup(env):
            tenant = TenantFactory(tenant_id="fail_t1")
            SyncJobFactory(tenant=tenant, sync_id="fail_sync", status="running")

        _setup_tenants_with_sync_jobs(setup)

        with SyncJobUoW("fail_t1") as uow:
            assert uow.sync_jobs is not None
            uow.sync_jobs.mark_terminal(
                "fail_sync",
                status="failed",
                completed_at=datetime.now(UTC),
                error_message="GAM rejected the order",
            )

        with get_db_session() as verify:
            row = verify.scalars(select(SyncJob).where(SyncJob.sync_id == "fail_sync")).first()
        assert row is not None
        assert row.status == "failed"
        assert row.error_message == "GAM rejected the order"


class TestFindRunningForOrder:
    def test_finds_running_for_order(self, integration_db):
        """``find_running_for_order`` returns the matching running SyncJob."""
        from tests.factories import SyncJobFactory, TenantFactory

        def setup(env):
            tenant = TenantFactory(tenant_id="find_t1")
            SyncJobFactory(
                tenant=tenant,
                sync_id="find_a",
                status="running",
                progress={"order_id": "ord_target", "attempts": 1},
            )
            SyncJobFactory(
                tenant=tenant,
                sync_id="find_b",
                status="running",
                progress={"order_id": "ord_other", "attempts": 1},
            )

        _setup_tenants_with_sync_jobs(setup)

        with SyncJobUoW("find_t1") as uow:
            assert uow.sync_jobs is not None
            row = uow.sync_jobs.find_running_for_order("ord_target")
            assert row is not None
            sync_id = row.sync_id

        assert sync_id == "find_a"

    def test_does_not_return_completed_rows(self, integration_db):
        """``find_running_for_order`` skips terminal rows."""
        from tests.factories import SyncJobFactory, TenantFactory

        def setup(env):
            tenant = TenantFactory(tenant_id="find_terminal_t1")
            SyncJobFactory(
                tenant=tenant,
                sync_id="find_done",
                status="completed",
                progress={"order_id": "ord_done", "attempts": 4},
            )

        _setup_tenants_with_sync_jobs(setup)

        with SyncJobUoW("find_terminal_t1") as uow:
            assert uow.sync_jobs is not None
            row = uow.sync_jobs.find_running_for_order("ord_done")

        assert row is None

    def test_does_not_leak_across_tenants(self, integration_db):
        """``find_running_for_order`` only returns rows for the configured tenant."""
        from tests.factories import SyncJobFactory, TenantFactory

        def setup(env):
            TenantFactory(tenant_id="iso_find_t1")
            t2 = TenantFactory(tenant_id="iso_find_t2")
            SyncJobFactory(
                tenant=t2,
                sync_id="iso_find_other_tenant",
                status="running",
                progress={"order_id": "ord_shared", "attempts": 1},
            )

        _setup_tenants_with_sync_jobs(setup)

        with SyncJobUoW("iso_find_t1") as uow:
            assert uow.sync_jobs is not None
            row = uow.sync_jobs.find_running_for_order("ord_shared")

        assert row is None, "Cross-tenant find_running_for_order should return None"


class TestReapStale:
    def test_reaps_stale_running_rows(self, integration_db):
        """``reap_stale`` flips rows older than the threshold to ``failed``."""
        from tests.factories import SyncJobFactory, TenantFactory

        now = datetime(2026, 5, 26, 12, 0, tzinfo=UTC)

        def setup(env):
            tenant = TenantFactory(tenant_id="reap_t1")
            SyncJobFactory(
                tenant=tenant,
                sync_id="reap_old",
                status="running",
                started_at=now - timedelta(minutes=30),
                progress={"order_id": "ord_old"},
            )
            SyncJobFactory(
                tenant=tenant,
                sync_id="reap_fresh",
                status="running",
                started_at=now - timedelta(seconds=5),
                progress={"order_id": "ord_fresh"},
            )

        _setup_tenants_with_sync_jobs(setup)

        with SyncJobUoW("reap_t1") as uow:
            assert uow.sync_jobs is not None
            reaped = uow.sync_jobs.reap_stale(timedelta(minutes=10), now=now)

        assert reaped == ["reap_old"]

        with get_db_session() as verify:
            old_row = verify.scalars(select(SyncJob).where(SyncJob.sync_id == "reap_old")).first()
            fresh_row = verify.scalars(select(SyncJob).where(SyncJob.sync_id == "reap_fresh")).first()
        assert old_row is not None and old_row.status == "failed"
        assert old_row.error_message is not None
        assert "presumed dead" in old_row.error_message
        assert fresh_row is not None and fresh_row.status == "running"

    def test_does_not_reap_other_tenants(self, integration_db):
        """``reap_stale`` does not touch rows belonging to other tenants."""
        from tests.factories import SyncJobFactory, TenantFactory

        now = datetime(2026, 5, 26, 12, 0, tzinfo=UTC)

        def setup(env):
            TenantFactory(tenant_id="reap_iso_t1")
            t2 = TenantFactory(tenant_id="reap_iso_t2")
            SyncJobFactory(
                tenant=t2,
                sync_id="reap_other_tenant",
                status="running",
                started_at=now - timedelta(hours=2),
                progress={"order_id": "ord_other"},
            )

        _setup_tenants_with_sync_jobs(setup)

        with SyncJobUoW("reap_iso_t1") as uow:
            assert uow.sync_jobs is not None
            reaped = uow.sync_jobs.reap_stale(timedelta(minutes=10), now=now)

        assert reaped == []

        with get_db_session() as verify:
            row = verify.scalars(select(SyncJob).where(SyncJob.sync_id == "reap_other_tenant")).first()
        assert row is not None and row.status == "running", (
            "reap_stale leaked across tenants — tenant 1 reaped tenant 2's stale row"
        )


class TestCreateForOrder:
    def test_creates_running_sync_job_with_canonical_progress(self, integration_db):
        """``create_for_order`` writes a running row with the canonical progress dict."""
        from tests.factories import TenantFactory

        started_at = datetime(2026, 5, 26, 12, 0, tzinfo=UTC)

        def setup(env):
            TenantFactory(tenant_id="create_t1")

        _setup_tenants_with_sync_jobs(setup)

        with SyncJobUoW("create_t1") as uow:
            assert uow.sync_jobs is not None
            row = uow.sync_jobs.create_for_order(
                sync_id="create_sync",
                adapter_type="google_ad_manager",
                order_id="ord_new",
                media_buy_id="mb_new",
                principal_id="principal_new",
                webhook_url="https://example.com/wh",
                started_at=started_at,
                max_attempts=12,
            )
            assert row.sync_id == "create_sync"

        with get_db_session() as verify:
            persisted = verify.scalars(select(SyncJob).where(SyncJob.sync_id == "create_sync")).first()
        assert persisted is not None
        assert persisted.tenant_id == "create_t1"
        assert persisted.status == "running"
        assert persisted.sync_type == "order_approval"
        assert persisted.adapter_type == "google_ad_manager"
        assert persisted.progress == {
            "order_id": "ord_new",
            "media_buy_id": "mb_new",
            "principal_id": "principal_new",
            "webhook_url": "https://example.com/wh",
            "attempts": 0,
            "max_attempts": 12,
            "phase": "Starting approval polling",
        }


class TestSyncJobUoW:
    def test_uow_commits_on_clean_exit(self, integration_db):
        """``SyncJobUoW`` commits writes on clean exit."""
        from tests.factories import TenantFactory

        def setup(env):
            TenantFactory(tenant_id="uow_t1")

        _setup_tenants_with_sync_jobs(setup)

        started_at = datetime(2026, 5, 26, 12, 0, tzinfo=UTC)
        with SyncJobUoW("uow_t1") as uow:
            assert uow.sync_jobs is not None
            uow.sync_jobs.create_for_order(
                sync_id="uow_sync",
                adapter_type="google_ad_manager",
                order_id="ord_uow",
                media_buy_id="mb_uow",
                principal_id="principal_uow",
                webhook_url=None,
                started_at=started_at,
                max_attempts=6,
            )

        with get_db_session() as verify:
            row = verify.scalars(select(SyncJob).where(SyncJob.sync_id == "uow_sync")).first()
        assert row is not None
        assert row.status == "running"

    def test_uow_rolls_back_on_exception(self, integration_db):
        """``SyncJobUoW`` rolls back when the body raises."""
        from tests.factories import TenantFactory

        def setup(env):
            TenantFactory(tenant_id="uow_rb_t1")

        _setup_tenants_with_sync_jobs(setup)

        started_at = datetime(2026, 5, 26, 12, 0, tzinfo=UTC)
        with pytest.raises(RuntimeError):
            with SyncJobUoW("uow_rb_t1") as uow:
                assert uow.sync_jobs is not None
                uow.sync_jobs.create_for_order(
                    sync_id="uow_rb_sync",
                    adapter_type="google_ad_manager",
                    order_id="ord_rb",
                    media_buy_id="mb_rb",
                    principal_id="principal_rb",
                    webhook_url=None,
                    started_at=started_at,
                    max_attempts=6,
                )
                raise RuntimeError("force rollback")

        with get_db_session() as verify:
            row = verify.scalars(select(SyncJob).where(SyncJob.sync_id == "uow_rb_sync")).first()
        assert row is None, "UoW failed to roll back — SyncJob row leaked after exception"
