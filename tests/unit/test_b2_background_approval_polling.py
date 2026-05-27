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

import pytest

from src.core.schemas import CreateMediaBuySuccess, Principal
from tests.helpers.execute_approved_mocks import (
    make_mock_media_buy,
    make_mock_package,
    make_mock_product,
    make_mock_tenant,
)


def _uow_chain(repo_for_status_update):
    """Build the 4-UoW chain that execute_approved_media_buy opens on the success path.

    Order matches production (success-path reads):
      uow1     — tenant + media_buy + packages + product reads (lines 612)
      uow_plids — platform_order_id / platform_line_item_id persistence (lines 919-928,
                   added by #1337). _persist_adapter_package_ids calls
                   ``media_buys.get_packages(media_buy_id)`` — returning ``[]`` short-circuits
                   the body.
      uow2     — creatives upload (line 934)
      uow3     — terminal MediaBuy.status='active' update (line 1076). Only opened on the
                   sync-success branch; the handoff branch defers to background polling.
    """
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

    # uow_plids (added by #1337): returns an empty package list so _persist_adapter_package_ids
    # iterates zero rows and exits cleanly. The persistence path is exercised in its own tests.
    plids_repo = MagicMock()
    plids_repo.get_packages = MagicMock(return_value=[])
    uow_plids = MagicMock()
    uow_plids.__enter__ = MagicMock(return_value=uow_plids)
    uow_plids.__exit__ = MagicMock(return_value=None)
    uow_plids.session = MagicMock()
    uow_plids.media_buys = plids_repo

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

    return iter([uow1, uow_plids, uow2, uow3])


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
        max_retries = kwargs.get("max_retries")
        msg = (
            f"Expected max_retries=1, got kwargs={kwargs}. The 40-default would block the admin request for 10 minutes."
        )
        assert max_retries == 1, msg

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


class TestExecuteApprovedPinsResolvedWebhookUrlIntoBackgroundCall:
    """The admin handoff chain must forward the resolved webhook URL.

    Pin-test for the chain ``lookup_webhook_url(tenant_id, principal_id)``
    -> ``start_order_approval_background(..., webhook_url=resolved_webhook_url)``
    at ``src/core/tools/media_buy_create.py``. Mutating that line to pass
    ``webhook_url=None`` (or to drop the kwarg) must make this test fail —
    otherwise the buyer registered a webhook but the polling thread fires
    notifications into the void.
    """

    def test_webhook_url_from_lookup_pins_into_start_background_call(self):
        principal = Principal(principal_id="principal_1", name="Test", platform_mappings={})
        adapter_response = CreateMediaBuySuccess(media_buy_id="mb_b2_001", packages=[])

        mock_orders_manager = MagicMock()
        mock_orders_manager.approve_order.return_value = False  # forces handoff
        mock_adapter = MagicMock()
        mock_adapter.orders_manager = mock_orders_manager
        mock_adapter.creatives_manager = None

        repo3 = MagicMock()
        uow_iter = _uow_chain(repo3)

        resolved_url = "https://buyer.example/registered-webhook"

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
                "src.core.tools.media_buy_create.lookup_webhook_url",
                return_value=resolved_url,
            ) as mock_lookup,
            patch(
                "src.core.tools.media_buy_create.start_order_approval_background",
                return_value="approval_mb_b2_001_xyz",
            ) as mock_start_bg,
        ):
            from src.core.tools.media_buy_create import execute_approved_media_buy

            success, error = execute_approved_media_buy("mb_b2_001", "tenant_1")

        assert success is True, f"Expected handoff success, got {error!r}"
        # lookup must be invoked with the tenant + principal of the approval target.
        mock_lookup.assert_called_once_with("tenant_1", "principal_1")
        # The single critical pin: the URL the lookup returned MUST be forwarded as
        # the webhook_url kwarg into start_order_approval_background. Mutating
        # media_buy_create.py to pass ``webhook_url=None`` or drop the kwarg
        # must fail this test — otherwise the buyer's webhook is silently lost.
        assert mock_start_bg.call_count == 1
        kwargs = mock_start_bg.call_args.kwargs
        assert kwargs.get("webhook_url") == resolved_url, (
            f"Background handoff dropped the resolved webhook URL. "
            f"Expected webhook_url={resolved_url!r}, got webhook_url={kwargs.get('webhook_url')!r}. "
            "Buyer registered a webhook but the polling thread will not notify them."
        )


class TestFinalizeApprovalAtomicity:
    """``_finalize_approval`` enforces a three-tier atomicity contract.

    Three branches with distinct durability requirements:

    1. **MediaBuy update is fatal.** If ``MediaBuyUoW.update_status`` raises,
       the operation MUST hard-fail — audit + webhook MUST NOT fire, and the
       exception MUST propagate so the SyncJob terminal write skips and the
       stale-approval reaper later picks it up. Otherwise the buyer would
       receive an "approved" notification for a media buy still pinned at
       ``pending_approval``.
    2. **Audit logging is best-effort.** An audit logger crash MUST NOT abort
       the operation. The webhook MUST still fire (the buyer is the
       customer; an internal audit miss is a follow-up triage item).
    3. **Webhook fires after audit logged.** Ordering matters for log
       correlation — the audit row exists before the buyer receives the
       notification keyed on it.

    These three tests pin the contract. Mutating ``_finalize_approval`` to
    catch the MediaBuy exception silently, or to skip the webhook when
    audit raises, must fail at least one of these tests.
    """

    def _build_mocks(self):
        from src.services import order_approval_service as service_module

        mock_media_buy_repo = MagicMock()
        mock_media_buy_uow = MagicMock()
        mock_media_buy_uow.__enter__ = MagicMock(return_value=mock_media_buy_uow)
        mock_media_buy_uow.__exit__ = MagicMock(return_value=None)
        mock_media_buy_uow.media_buys = mock_media_buy_repo
        return service_module, mock_media_buy_repo, mock_media_buy_uow

    def test_audit_failure_does_not_block_webhook(self):
        """When ``AuditLogger.log_operation`` raises, the webhook must still fire.

        Audit logging is best-effort — the buyer (webhook recipient) is the
        customer of the operation, the audit row is for internal triage.
        Failing the buyer notification because the audit log crashed would
        invert the priority.
        """
        service_module, _, mock_media_buy_uow = self._build_mocks()

        mock_audit_instance = MagicMock()
        mock_audit_instance.log_operation.side_effect = RuntimeError("audit backend unreachable")

        with (
            patch.object(service_module, "MediaBuyUoW", return_value=mock_media_buy_uow),
            patch.object(service_module, "AuditLogger", return_value=mock_audit_instance),
            patch.object(service_module, "_send_approval_webhook") as mock_webhook,
        ):
            service_module._finalize_approval(
                media_buy_id="mb_b2_001",
                tenant_id="tenant_1",
                principal_id="principal_1",
                principal_name="Acme Corp",
                media_buy_status="active",
                audit_success=True,
                audit_details={"order_id": "12345", "attempts": 1},
                audit_error=None,
                webhook_url="https://buyer.example/hook",
                webhook_status="approved",
                webhook_message="Order approved",
                webhook_order_id="12345",
                webhook_attempts=1,
            )

        # Audit raised — verify webhook still fired with the buyer-facing payload.
        mock_audit_instance.log_operation.assert_called_once_with(
            operation="approve_order",
            principal_name="Acme Corp",
            principal_id="principal_1",
            adapter_id="12345",
            success=True,
            error=None,
            details={"order_id": "12345", "attempts": 1},
        )
        mock_webhook.assert_called_once_with(
            webhook_url="https://buyer.example/hook",
            tenant_id="tenant_1",
            principal_id="principal_1",
            media_buy_id="mb_b2_001",
            status="approved",
            message="Order approved",
            order_id="12345",
            attempts=1,
        )

    def test_media_buy_update_failure_propagates_and_skips_audit_and_webhook(self):
        """When ``MediaBuyUoW.update_status`` raises, propagate and skip downstream.

        The MediaBuy row is the consumer-visible state. If it failed to flip,
        no audit row should be written claiming the operation succeeded, and
        no buyer webhook should fire claiming the order is approved/failed.
        The caller (``_mark_approval_complete`` / ``_mark_approval_failed``)
        is responsible for catching the propagated exception and skipping the
        SyncJob terminal write — that contract is enforced separately by
        ``test_mark_complete_skips_terminal_write_when_finalize_raises``.
        """
        service_module, mock_media_buy_repo, mock_media_buy_uow = self._build_mocks()
        mock_media_buy_repo.update_status.side_effect = RuntimeError("postgres connection lost")

        mock_audit_instance = MagicMock()

        with (
            patch.object(service_module, "MediaBuyUoW", return_value=mock_media_buy_uow),
            patch.object(service_module, "AuditLogger", return_value=mock_audit_instance),
            patch.object(service_module, "_send_approval_webhook") as mock_webhook,
            pytest.raises(RuntimeError, match="postgres connection lost"),
        ):
            service_module._finalize_approval(
                media_buy_id="mb_b2_001",
                tenant_id="tenant_1",
                principal_id="principal_1",
                principal_name="Acme Corp",
                media_buy_status="active",
                audit_success=True,
                audit_details={"order_id": "12345"},
                audit_error=None,
                webhook_url="https://buyer.example/hook",
                webhook_status="approved",
                webhook_message="Order approved",
                webhook_order_id="12345",
                webhook_attempts=1,
            )

        # MediaBuy update was attempted but raised — neither audit nor webhook
        # should have been touched. A silent swallow of the MediaBuy failure
        # would let one or both of these mocks be called.
        mock_media_buy_repo.update_status.assert_called_once_with("mb_b2_001", "active")
        mock_audit_instance.log_operation.assert_not_called()
        mock_webhook.assert_not_called()

    def test_webhook_fires_after_audit_log_committed(self):
        """Ordering: audit log MUST run before webhook fires.

        Buyer-facing notifications reference the audit row via ``audit_details``.
        Firing the webhook before the audit row exists would surface a notification
        the operator cannot trace back. The test pins call order using a shared
        sequence captured by both mocks.
        """
        service_module, _, mock_media_buy_uow = self._build_mocks()
        mock_audit_instance = MagicMock()

        call_order: list[str] = []
        mock_audit_instance.log_operation.side_effect = lambda **_: call_order.append("audit")

        def webhook_recorder(**_):
            call_order.append("webhook")

        with (
            patch.object(service_module, "MediaBuyUoW", return_value=mock_media_buy_uow),
            patch.object(service_module, "AuditLogger", return_value=mock_audit_instance),
            patch.object(service_module, "_send_approval_webhook", side_effect=webhook_recorder),
        ):
            service_module._finalize_approval(
                media_buy_id="mb_b2_001",
                tenant_id="tenant_1",
                principal_id="principal_1",
                principal_name="Acme Corp",
                media_buy_status="failed",
                audit_success=False,
                audit_details={"order_id": "12345", "attempts": 3},
                audit_error="timed out after 3 attempts",
                webhook_url="https://buyer.example/hook",
                webhook_status="failed",
                webhook_message="timed out",
                webhook_order_id="12345",
                webhook_attempts=3,
            )

        assert call_order == ["audit", "webhook"], (
            f"Expected audit -> webhook ordering, got {call_order}. "
            "Webhook firing before audit would surface buyer notifications "
            "for which no audit trail yet exists."
        )


class TestMarkApprovalCompleteUpdatesMediaBuyAndAuditLogs:
    """_mark_approval_complete writes MediaBuy.status='active' + fires audit log."""

    def test_writes_media_buy_status_active(self):
        from src.services.order_approval_service import _mark_approval_complete

        mock_repo = MagicMock()
        mock_uow = MagicMock()
        mock_uow.__enter__ = MagicMock(return_value=mock_uow)
        mock_uow.__exit__ = MagicMock(return_value=None)
        mock_uow.media_buys = mock_repo

        mock_sync_uow = MagicMock()
        mock_sync_uow.__enter__ = MagicMock(return_value=mock_sync_uow)
        mock_sync_uow.__exit__ = MagicMock(return_value=None)
        mock_sync_uow.sync_jobs = MagicMock()

        with (
            patch("src.services.order_approval_service.SyncJobUoW", return_value=mock_sync_uow),
            patch("src.services.order_approval_service.MediaBuyUoW", return_value=mock_uow),
            patch("src.services.order_approval_service.AuditLogger"),
        ):
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

        mock_sync_uow = MagicMock()
        mock_sync_uow.__enter__ = MagicMock(return_value=mock_sync_uow)
        mock_sync_uow.__exit__ = MagicMock(return_value=None)
        mock_sync_uow.sync_jobs = MagicMock()

        mock_audit_instance = MagicMock()

        with (
            patch("src.services.order_approval_service.SyncJobUoW", return_value=mock_sync_uow),
            patch("src.services.order_approval_service.MediaBuyUoW"),
            patch(
                "src.services.order_approval_service.AuditLogger", return_value=mock_audit_instance
            ) as mock_audit_cls,
        ):
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
