"""Tests for B2 fix: GAM approval failure must not block the admin request.

When `execute_approved_media_buy` triggers GAM order approval and forecasting is
not ready, the synchronous `approve_order` call was using the 40x15s default
(10-minute block). The fix is to retry once synchronously, then hand off to the
existing `start_order_approval_background` polling service so the admin caller
returns immediately and the buyer is notified via the existing webhook path.

Covers the order-approval slice of the storyboard `inventory_list_no_match`
behaviour (rejected GAM forecast must not strand a MediaBuy in pending_approval).
"""

from unittest.mock import MagicMock, patch

from src.core.schemas import CreateMediaBuySuccess, Principal
from tests.helpers.execute_approved_mocks import (
    make_mock_media_buy,
    make_mock_package,
    make_mock_product,
    make_mock_tenant,
)


def _uow_chain(repo_for_status_update):
    """Build the 3-UoW chain that execute_approved_media_buy opens."""
    tenant = make_mock_tenant(ad_server="google_ad_manager")
    mb = make_mock_media_buy(media_buy_id="mb_b2_001")
    pkg = make_mock_package(media_buy_id="mb_b2_001")
    prod = make_mock_product()

    s1 = MagicMock()
    s1.scalars = MagicMock(
        side_effect=[
            MagicMock(first=MagicMock(return_value=tenant)),
            MagicMock(first=MagicMock(return_value=mb)),
            MagicMock(all=MagicMock(return_value=[pkg])),
            MagicMock(first=MagicMock(return_value=prod)),
        ]
    )

    s2 = MagicMock()
    s2.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))

    uow1 = MagicMock()
    uow1.__enter__ = MagicMock(return_value=uow1)
    uow1.__exit__ = MagicMock(return_value=None)
    uow1.session = s1
    uow1.media_buys = MagicMock()
    uow2 = MagicMock()
    uow2.__enter__ = MagicMock(return_value=uow2)
    uow2.__exit__ = MagicMock(return_value=None)
    uow2.session = s2
    uow2.media_buys = MagicMock()
    uow3 = MagicMock()
    uow3.__enter__ = MagicMock(return_value=uow3)
    uow3.__exit__ = MagicMock(return_value=None)
    uow3.session = MagicMock()
    uow3.media_buys = repo_for_status_update

    return iter([uow1, uow2, uow3])


class TestExecuteApprovedHandsOffToBackgroundOnApprovalFailure:
    """execute_approved_media_buy must not block on GAM forecast unavailability.

    The fix: use max_retries=1 (single fast attempt) then fall through to the
    existing `start_order_approval_background` polling service, which updates
    MediaBuy.status and fires the audit/Slack notification when polling
    terminates.
    """

    def _run(self, approval_return_value):
        principal = Principal(principal_id="principal_1", name="Test", platform_mappings={})
        adapter_response = CreateMediaBuySuccess(media_buy_id="mb_b2_001", packages=[])

        # Adapter with orders_manager whose approve_order returns the parametrized value
        mock_orders_manager = MagicMock()
        mock_orders_manager.approve_order.return_value = approval_return_value
        mock_adapter = MagicMock()
        mock_adapter.orders_manager = mock_orders_manager
        mock_adapter.creatives_manager = None  # no creatives path

        repo3 = MagicMock()
        uow_iter = _uow_chain(repo3)

        with (
            patch("src.core.database.repositories.MediaBuyUoW", side_effect=lambda _: next(uow_iter)),
            patch("src.core.config_loader.set_current_tenant"),
            patch(
                "src.core.config_loader.get_tenant_by_id",
                return_value={"tenant_id": "tenant_1", "adapter_type": "google_ad_manager"},
            ),
            patch("src.core.auth.get_principal_object", return_value=principal),
            patch(
                "src.core.tools.media_buy_create._execute_adapter_media_buy_creation",
                return_value=adapter_response,
            ),
            patch("src.core.tools.media_buy_create._validate_creatives_before_adapter_call"),
            patch("src.core.helpers.adapter_helpers.get_adapter", return_value=mock_adapter),
            patch(
                "src.core.tools.media_buy_create.start_order_approval_background",
                return_value="approval_mb_b2_001_xyz",
            ) as mock_start_bg,
        ):
            from src.core.tools.media_buy_create import execute_approved_media_buy

            result = execute_approved_media_buy("mb_b2_001", "tenant_1")

        return result, mock_orders_manager, mock_start_bg, repo3

    def test_uses_single_retry_not_default_40(self):
        """approve_order must be called with max_retries=1, never the 40-default."""
        _, mock_orders, _, _ = self._run(approval_return_value=True)
        # The kwargs must include max_retries=1
        _, kwargs = mock_orders.approve_order.call_args
        assert (
            kwargs.get("max_retries") == 1
        ), f"Expected max_retries=1, got kwargs={kwargs}. The 40-default would block the admin request for 10 minutes."

    def test_kicks_off_background_polling_when_approve_returns_false(self):
        """On approve_order failure, start_order_approval_background must be invoked."""
        result, _, mock_start_bg, _ = self._run(approval_return_value=False)
        assert mock_start_bg.call_count == 1, (
            f"Expected start_order_approval_background to be called once, "
            f"got {mock_start_bg.call_count} calls. Without background hand-off "
            "the buy is stranded in pending_approval forever."
        )
        success, error = result
        assert success is True, f"Expected (True, None) after handoff, got ({success}, {error!r})"
        assert error is None

    def test_defers_status_write_to_background_when_handed_off(self):
        """When background polling takes over, no synchronous status='active' write.

        The background service owns the terminal transition (active OR failed).
        Writing 'active' synchronously here would race the polling service and
        could mask a real GAM rejection as a successful buy.
        """
        _, _, _, repo3 = self._run(approval_return_value=False)
        assert repo3.update_status.call_count == 0, (
            "Status update must be deferred to background polling when handoff occurs; "
            f"got {repo3.update_status.call_count} calls with args {repo3.update_status.call_args_list}"
        )

    def test_does_not_kick_off_background_when_approve_succeeds(self):
        """When initial approval succeeds, no background polling should start."""
        result, _, mock_start_bg, repo3 = self._run(approval_return_value=True)
        assert mock_start_bg.call_count == 0, "Background polling should only start on initial-approval failure"
        success, error = result
        assert success is True
        assert error is None
        # On synchronous success, the existing 'active' status write must still fire.
        repo3.update_status.assert_called_once_with("mb_b2_001", "active")


class TestMarkApprovalCompleteUpdatesMediaBuyAndAuditLogs:
    """_mark_approval_complete writes MediaBuy.status='active' + fires audit log."""

    def test_writes_media_buy_status_active(self):
        from src.services.order_approval_service import _mark_approval_complete

        mock_repo = MagicMock()
        mock_uow = MagicMock()
        mock_uow.__enter__ = MagicMock(return_value=mock_uow)
        mock_uow.__exit__ = MagicMock(return_value=None)
        mock_uow.media_buys = mock_repo

        sync_job = MagicMock()
        sync_job.progress = {"order_id": "12345"}
        mock_db_session = MagicMock()
        mock_db_session.scalars.return_value.first.return_value = sync_job

        with (
            patch("src.services.order_approval_service.get_db_session") as mock_get_db,
            patch("src.services.order_approval_service.MediaBuyUoW", return_value=mock_uow),
            patch("src.services.order_approval_service.AuditLogger"),
        ):
            mock_get_db.return_value.__enter__.return_value = mock_db_session

            _mark_approval_complete(
                approval_id="approval_12345_test",
                summary={"order_id": "12345", "media_buy_id": "mb_b2_001", "attempts": 2},
                webhook_url=None,
                tenant_id="tenant_1",
                principal_id="principal_1",
                principal_name="Acme Corp",
                media_buy_id="mb_b2_001",
            )

        mock_repo.update_status.assert_called_once_with("mb_b2_001", "active")

    def test_fires_audit_log_with_success_true(self):
        from src.services.order_approval_service import _mark_approval_complete

        sync_job = MagicMock()
        sync_job.progress = {"order_id": "12345"}
        mock_db_session = MagicMock()
        mock_db_session.scalars.return_value.first.return_value = sync_job

        mock_audit_instance = MagicMock()

        with (
            patch("src.services.order_approval_service.get_db_session") as mock_get_db,
            patch("src.services.order_approval_service.MediaBuyUoW"),
            patch(
                "src.services.order_approval_service.AuditLogger", return_value=mock_audit_instance
            ) as mock_audit_cls,
        ):
            mock_get_db.return_value.__enter__.return_value = mock_db_session

            _mark_approval_complete(
                approval_id="approval_12345_test",
                summary={"order_id": "12345", "media_buy_id": "mb_b2_001", "attempts": 2},
                webhook_url=None,
                tenant_id="tenant_1",
                principal_id="principal_1",
                principal_name="Acme Corp",
                media_buy_id="mb_b2_001",
            )

        # AuditLogger constructor called with adapter_name+tenant_id
        mock_audit_cls.assert_called_once_with(adapter_name="google_ad_manager", tenant_id="tenant_1")
        # log_operation called with success=True and order_id in details
        _, kwargs = mock_audit_instance.log_operation.call_args
        assert kwargs.get("success") is True
        assert kwargs.get("operation") == "approve_order"
        assert kwargs.get("principal_id") == "principal_1"
        assert "12345" in str(kwargs.get("details", {})), f"Expected order_id in details, got {kwargs.get('details')}"


class TestMarkApprovalFailedUpdatesMediaBuyAndAuditLogs:
    """_mark_approval_failed writes MediaBuy.status='failed' + fires audit log."""

    def test_writes_media_buy_status_failed(self):
        from src.services.order_approval_service import _mark_approval_failed

        mock_media_buy_repo = MagicMock()
        mock_media_buy_uow = MagicMock()
        mock_media_buy_uow.__enter__ = MagicMock(return_value=mock_media_buy_uow)
        mock_media_buy_uow.__exit__ = MagicMock(return_value=None)
        mock_media_buy_uow.media_buys = mock_media_buy_repo

        sync_job = MagicMock()
        sync_job.progress = {"order_id": "12345", "attempts": 12}

        mock_sync_job_repo = MagicMock()
        mock_sync_job_repo.get.return_value = sync_job
        mock_sync_job_uow = MagicMock()
        mock_sync_job_uow.__enter__ = MagicMock(return_value=mock_sync_job_uow)
        mock_sync_job_uow.__exit__ = MagicMock(return_value=None)
        mock_sync_job_uow.sync_jobs = mock_sync_job_repo

        with (
            patch("src.services.order_approval_service.SyncJobUoW", return_value=mock_sync_job_uow),
            patch("src.services.order_approval_service.MediaBuyUoW", return_value=mock_media_buy_uow),
            patch("src.services.order_approval_service.AuditLogger"),
        ):
            _mark_approval_failed(
                approval_id="approval_12345_test",
                error_message="forecast timeout after 12 attempts",
                webhook_url=None,
                tenant_id="tenant_1",
                principal_id="principal_1",
                principal_name="Acme Corp",
                media_buy_id="mb_b2_001",
            )

        mock_media_buy_repo.update_status.assert_called_once_with("mb_b2_001", "failed")
        # The SyncJob.progress lookup must have happened and its order_id/attempts
        # must have been carried into the audit_details. The mock returns
        # progress={"order_id": "12345", "attempts": 12}, so the lookup is required
        # for those fields to be present.
        mock_sync_job_repo.get.assert_called_with("approval_12345_test")

    def test_fires_audit_log_with_success_false_and_error(self):
        from src.services.order_approval_service import _mark_approval_failed

        sync_job = MagicMock()
        sync_job.progress = {"order_id": "12345", "attempts": 12}

        mock_sync_job_repo = MagicMock()
        mock_sync_job_repo.get.return_value = sync_job
        mock_sync_job_uow = MagicMock()
        mock_sync_job_uow.__enter__ = MagicMock(return_value=mock_sync_job_uow)
        mock_sync_job_uow.__exit__ = MagicMock(return_value=None)
        mock_sync_job_uow.sync_jobs = mock_sync_job_repo

        mock_audit_instance = MagicMock()

        with (
            patch("src.services.order_approval_service.SyncJobUoW", return_value=mock_sync_job_uow),
            patch("src.services.order_approval_service.MediaBuyUoW"),
            patch(
                "src.services.order_approval_service.AuditLogger", return_value=mock_audit_instance
            ) as mock_audit_cls,
        ):
            _mark_approval_failed(
                approval_id="approval_12345_test",
                error_message="forecast timeout after 12 attempts",
                webhook_url=None,
                tenant_id="tenant_1",
                principal_id="principal_1",
                principal_name="Acme Corp",
                media_buy_id="mb_b2_001",
            )

        mock_audit_cls.assert_called_once_with(adapter_name="google_ad_manager", tenant_id="tenant_1")
        _, kwargs = mock_audit_instance.log_operation.call_args
        assert kwargs.get("success") is False
        assert kwargs.get("operation") == "approve_order"
        assert kwargs.get("principal_id") == "principal_1"
        assert kwargs.get("error") == "forecast timeout after 12 attempts"
        # The SyncJob.progress lookup must propagate order_id and attempts into audit_details —
        # without it, the audit log would lose the GAM order_id and the polling attempt count.
        audit_details = kwargs.get("details") or {}
        assert audit_details.get("order_id") == "12345"
        assert audit_details.get("attempts") == 12
