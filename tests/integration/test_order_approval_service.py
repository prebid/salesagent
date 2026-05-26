"""Integration tests for order approval service against a real PostgreSQL database.

These tests exist as a regression guarantee for the JSONB-merge behavior that
``order_approval_service._update_approval_progress`` relies on. The function
mutates ``SyncJob.progress`` in place via ``dict.update`` and then calls
``flag_modified(approval_job, "progress")`` — without ``flag_modified`` the
JSONB write is invisible to SQLAlchemy's dirty tracker and the commit is a
no-op. That failure mode is undetectable in unit tests because the mocked
session reports the write as successful regardless.

The mutation guarantee: removing ``flag_modified`` from the production code
must make ``test_update_approval_progress_persists_jsonb_merge`` fail. The
test reads the row back in a fresh session to defeat SQLAlchemy's identity
map, which would otherwise mask the missing write.

``TestAdminHandoffChainWritesWebhookUrl`` exercises the admin path's
``lookup_webhook_url → start_order_approval_background → SyncJob`` chain
against real PostgreSQL — without this, the buyer-visible ``webhook_url``
plumbing is mock-only and would silently regress.
"""

from __future__ import annotations

import threading
from unittest.mock import patch

import pytest
from sqlalchemy import select

from src.core.database.database_session import get_db_session
from src.core.database.models import SyncJob
from src.services.order_approval_service import (
    _active_approvals,
    _update_approval_progress,
    lookup_webhook_url,
    start_order_approval_background,
)
from tests.harness._base import IntegrationEnv

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


class _ApprovalServiceEnv(IntegrationEnv):
    """Bare integration env — no external patches; the service hits the real DB."""

    EXTERNAL_PATCHES: dict[str, str] = {}


class TestUpdateApprovalProgressPersistsJSONB:
    """``_update_approval_progress`` must persist merged JSONB updates.

    Covers the ``flag_modified`` invariant for SyncJob.progress writes.
    """

    def test_update_approval_progress_persists_jsonb_merge(self, integration_db):
        """Sequential progress updates must be visible to a fresh session.

        Without ``flag_modified`` on the JSONB column the dirty-tracker
        would skip the UPDATE, the commit would be a no-op, and the
        re-queried row would still show ``attempts=0``.
        """
        from tests.factories import SyncJobFactory, TenantFactory

        with _ApprovalServiceEnv() as env:
            tenant = TenantFactory(tenant_id="tenant_approval_jsonb")
            sync_job = SyncJobFactory(
                tenant=tenant,
                sync_id="approval_jsonb_test",
                status="running",
                progress={
                    "order_id": "order_42",
                    "media_buy_id": "mb_42",
                    "principal_id": "principal_42",
                    "attempts": 0,
                    "max_attempts": 12,
                    "phase": "Starting approval polling",
                },
            )
            env._commit_factory_data()
            approval_id = sync_job.sync_id

        # Simulate two polling iterations updating the JSONB column.
        _update_approval_progress(
            approval_id, "tenant_approval_jsonb", {"attempts": 1, "phase": "Approval attempt 1/12"}
        )
        _update_approval_progress(
            approval_id, "tenant_approval_jsonb", {"attempts": 2, "phase": "Approval attempt 2/12"}
        )

        # Read back in a fresh session — SQLAlchemy's identity map would
        # otherwise mask a missing UPDATE by returning the in-memory object.
        with get_db_session() as verify_session:
            stmt = select(SyncJob).where(SyncJob.sync_id == approval_id)
            row = verify_session.scalars(stmt).first()

        assert row is not None, "SyncJob row vanished between writes"
        assert row.progress is not None, "progress column was nulled"
        assert row.progress["attempts"] == 2, (
            "Final attempts value did not persist. If this assertion fails after a "
            "flag_modified() removal, you removed the mutation guard the test exists "
            "to protect."
        )
        assert row.progress["phase"] == "Approval attempt 2/12"
        # The seed fields must survive the merge — _update_approval_progress
        # calls dict.update, so untouched keys must remain.
        assert row.progress["order_id"] == "order_42"
        assert row.progress["media_buy_id"] == "mb_42"
        assert row.progress["max_attempts"] == 12

    def test_update_approval_progress_seeds_when_progress_is_null(self, integration_db):
        """When ``progress`` starts as NULL the update must seed it.

        Exercises the ``else`` branch of ``_update_approval_progress`` —
        the one that assigns rather than merging — so neither branch
        can regress silently.
        """
        from tests.factories import SyncJobFactory, TenantFactory

        with _ApprovalServiceEnv() as env:
            tenant = TenantFactory(tenant_id="tenant_approval_seed")
            sync_job = SyncJobFactory(
                tenant=tenant,
                sync_id="approval_seed_test",
                status="running",
                progress=None,
            )
            env._commit_factory_data()
            approval_id = sync_job.sync_id

        _update_approval_progress(approval_id, "tenant_approval_seed", {"order_id": "order_seed", "attempts": 1})

        with get_db_session() as verify_session:
            stmt = select(SyncJob).where(SyncJob.sync_id == approval_id)
            row = verify_session.scalars(stmt).first()

        assert row is not None
        assert row.progress is not None
        assert row.progress["order_id"] == "order_seed"
        assert row.progress["attempts"] == 1


class TestAdminHandoffChainWritesWebhookUrl:
    """The admin approval path's webhook URL must flow into the SyncJob.

    This is the real-DB regression test for the chain
    ``lookup_webhook_url(tenant_id, principal_id) → start_order_approval_background(... webhook_url=...) → SyncJobRepository.create_for_order(progress={... webhook_url: ...})``.
    The chain runs in ``src/core/tools/media_buy_create.py`` at the admin handoff
    site. Konstantine's R5 callout: the buyer registered a webhook; if the admin
    approval bypasses ``lookup_webhook_url`` (the original bug), the buyer never
    hears about the result.

    Pin-test: change ``media_buy_create.py`` to pass ``webhook_url=None`` instead
    of ``resolved_webhook_url`` — these tests still pass because they exercise
    the chain directly. The stricter pin-test (executing ``execute_approved_media_buy``
    end-to-end) requires substantial fixture setup; the tighter test below
    proves the chain integrity in isolation and is paired with the unit test in
    ``tests/unit/test_b2_background_approval_polling.py::TestExecuteApprovedHandsOffToBackgroundOnApprovalFailure``.
    """

    @pytest.fixture(autouse=True)
    def _cleanup_approval_registry(self):
        """Clear the global approval-thread registry around each test.

        ``start_order_approval_background`` spawns a daemon thread and registers
        it. We patch ``_run_approval_thread`` to do nothing so no DB calls
        escape the test, but the registration still lands in the global
        registry and could leak across tests.
        """
        for key in list(_active_approvals.list_active()):
            _active_approvals.remove(key)
        yield
        for key in list(_active_approvals.list_active()):
            _active_approvals.remove(key)

    def test_admin_chain_writes_webhook_url_into_sync_job_progress(self, integration_db):
        """When a buyer has an active PushNotificationConfig, the URL lands in the SyncJob."""
        from tests.factories import PrincipalFactory, PushNotificationConfigFactory, TenantFactory

        with _ApprovalServiceEnv() as env:
            tenant = TenantFactory(tenant_id="admin_chain_t1")
            principal = PrincipalFactory(tenant=tenant, principal_id="admin_chain_p1")
            PushNotificationConfigFactory(
                tenant=tenant,
                principal=principal,
                id="admin_chain_webhook",
                url="https://buyer.example/admin-chain-webhook",
                is_active=True,
            )
            env._commit_factory_data()

        # Simulate the admin path's two-step chain (matches media_buy_create.py:1005-1015):
        # 1. Resolve the webhook URL from PushNotificationConfig
        # 2. Pass it into start_order_approval_background
        resolved = lookup_webhook_url("admin_chain_t1", "admin_chain_p1")
        assert (
            resolved == "https://buyer.example/admin-chain-webhook"
        ), "lookup_webhook_url failed to find the active config — chain breaks here"

        # Patch the thread runner so no real GAM polling fires after the SyncJob
        # row is committed. The registration lands in the registry first.
        keep_alive = threading.Event()
        with patch(
            "src.services.order_approval_service._run_approval_thread",
            side_effect=lambda *args, **kwargs: keep_alive.wait(timeout=2.0),
        ):
            try:
                approval_id = start_order_approval_background(
                    order_id="admin_chain_order",
                    media_buy_id="admin_chain_mb",
                    tenant_id="admin_chain_t1",
                    principal_id="admin_chain_p1",
                    principal_name="Admin Chain Tester",
                    webhook_url=resolved,
                )
            finally:
                keep_alive.set()

        # Verify the SyncJob row landed in real PostgreSQL with webhook_url populated.
        with get_db_session() as verify:
            row = verify.scalars(select(SyncJob).where(SyncJob.sync_id == approval_id)).first()

        assert row is not None, "start_order_approval_background did not commit a SyncJob row"
        assert row.tenant_id == "admin_chain_t1"
        assert row.status == "running"
        assert row.progress is not None
        assert row.progress["webhook_url"] == "https://buyer.example/admin-chain-webhook", (
            "webhook_url did not propagate from lookup_webhook_url through start_order_approval_background "
            "into SyncJob.progress — buyer would not receive the approval notification"
        )
        assert row.progress["order_id"] == "admin_chain_order"
        assert row.progress["media_buy_id"] == "admin_chain_mb"
        assert row.progress["principal_id"] == "admin_chain_p1"

    def test_admin_chain_writes_none_when_no_webhook_configured(self, integration_db):
        """When the buyer has not registered a webhook, the SyncJob records URL=None."""
        from tests.factories import PrincipalFactory, TenantFactory

        with _ApprovalServiceEnv() as env:
            tenant = TenantFactory(tenant_id="admin_chain_t2")
            PrincipalFactory(tenant=tenant, principal_id="admin_chain_p2")
            # No PushNotificationConfig — buyer never registered a webhook
            env._commit_factory_data()

        resolved = lookup_webhook_url("admin_chain_t2", "admin_chain_p2")
        assert resolved is None, "lookup_webhook_url returned a URL when none was configured"

        keep_alive = threading.Event()
        with patch(
            "src.services.order_approval_service._run_approval_thread",
            side_effect=lambda *args, **kwargs: keep_alive.wait(timeout=2.0),
        ):
            try:
                approval_id = start_order_approval_background(
                    order_id="admin_chain_order_no_webhook",
                    media_buy_id="admin_chain_mb_no_webhook",
                    tenant_id="admin_chain_t2",
                    principal_id="admin_chain_p2",
                    principal_name="No-Webhook Tester",
                    webhook_url=resolved,
                )
            finally:
                keep_alive.set()

        with get_db_session() as verify:
            row = verify.scalars(select(SyncJob).where(SyncJob.sync_id == approval_id)).first()

        assert row is not None
        assert row.progress is not None
        assert (
            row.progress["webhook_url"] is None
        ), "webhook_url should be None when no PushNotificationConfig exists for the principal"
