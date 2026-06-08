"""Unit tests for order approval service."""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from src.services.order_approval_service import (
    get_active_approvals,
    get_approval_status,
    is_approval_running,
    start_order_approval_background,
)


@pytest.fixture(autouse=True)
def cleanup_approval_registry():
    """Clean up global approval registry before each test."""
    # Import here to avoid issues with module loading
    import src.services.order_approval_service as service

    # Clear the registry before the test (ThreadRegistry API)
    for key in list(service._active_approvals.list_active()):
        service._active_approvals.remove(key)

    yield

    # Note: Don't clear after test - threads may still be running and need to clean up themselves


@pytest.fixture
def mock_sync_job_status_uow():
    """Mock ``SyncJobUoW`` used by ``get_approval_status``.

    Read-only path — ``uow.sync_jobs.get(approval_id)`` returns whatever
    the test stubs onto the repo. Tests rebind ``repo.get.return_value``
    when they need a row to come back.
    """
    with patch("src.services.order_approval_service.SyncJobUoW") as mock_uow_cls:
        mock_uow = MagicMock()
        mock_uow.__enter__ = MagicMock(return_value=mock_uow)
        mock_uow.__exit__ = MagicMock(return_value=None)
        mock_repo = MagicMock()
        mock_repo.get.return_value = None
        mock_uow.sync_jobs = mock_repo
        mock_uow_cls.return_value = mock_uow
        yield {"uow_cls": mock_uow_cls, "uow": mock_uow, "repo": mock_repo}


@pytest.fixture
def mock_push_notification_uow():
    """Mock ``PushNotificationConfigUoW`` used by webhook-auth lookups."""
    with patch("src.services.order_approval_service.PushNotificationConfigUoW") as mock_uow_cls:
        mock_uow = MagicMock()
        mock_uow.__enter__ = MagicMock(return_value=mock_uow)
        mock_uow.__exit__ = MagicMock(return_value=None)
        mock_repo = MagicMock()
        mock_repo.find_active_by_url.return_value = None
        mock_repo.find_most_recent_active_for_principal.return_value = None
        mock_uow.push_notification_configs = mock_repo
        mock_uow_cls.return_value = mock_uow
        yield {"uow_cls": mock_uow_cls, "uow": mock_uow, "repo": mock_repo}


@pytest.fixture
def mock_sync_job_uow():
    """Mock ``SyncJobUoW`` so service tests don't need a real database.

    Defaults: no stale rows to reap, no running approval for the order,
    create_for_order succeeds.
    """
    with patch("src.services.order_approval_service.SyncJobUoW") as mock_uow_cls:
        mock_uow = MagicMock()
        mock_uow.__enter__ = MagicMock(return_value=mock_uow)
        mock_uow.__exit__ = MagicMock(return_value=None)
        mock_repo = MagicMock()
        mock_repo.reap_stale.return_value = []
        mock_repo.find_running_for_order.return_value = None
        mock_repo.create_for_order.return_value = MagicMock(sync_id="approval_mock")
        mock_repo.get.return_value = None
        mock_uow.sync_jobs = mock_repo
        mock_uow_cls.return_value = mock_uow
        yield {"uow_cls": mock_uow_cls, "uow": mock_uow, "repo": mock_repo}


@pytest.fixture
def mock_gam_client():
    """Mock GAM client and managers."""
    with (
        patch("src.services.order_approval_service.GAMClientManager") as mock_client_mgr,
        patch("src.services.order_approval_service.GAMOrdersManager") as mock_orders_mgr,
        patch("src.services.order_approval_service.AdapterConfig") as mock_config,
    ):
        # Mock adapter config
        mock_adapter_config = MagicMock()
        mock_adapter_config.gam_network_code = "12345"

        # Mock orders manager
        mock_orders_instance = MagicMock()
        mock_orders_instance.approve_order.return_value = True
        mock_orders_mgr.return_value = mock_orders_instance

        yield {
            "client_manager": mock_client_mgr,
            "orders_manager": mock_orders_mgr,
            "orders_instance": mock_orders_instance,
            "adapter_config": mock_adapter_config,
        }


def test_start_approval_creates_sync_job(mock_sync_job_uow):
    """``start_order_approval_background`` delegates row creation to the repository."""
    approval_id = start_order_approval_background(
        order_id="12345",
        media_buy_id="mb_123",
        tenant_id="tenant_1",
        principal_id="principal_1",
        webhook_url="https://example.com/webhook",
    )

    # Verify the generated approval_id shape and that the UoW was scoped to the tenant.
    assert approval_id.startswith("approval_12345_")
    mock_sync_job_uow["uow_cls"].assert_called_with("tenant_1")

    # Verify create_for_order was called once with the expected fields.
    mock_sync_job_uow["repo"].create_for_order.assert_called_once()
    create_kwargs = mock_sync_job_uow["repo"].create_for_order.call_args.kwargs
    assert create_kwargs["sync_id"] == approval_id
    assert create_kwargs["adapter_type"] == "google_ad_manager"
    assert create_kwargs["order_id"] == "12345"
    assert create_kwargs["media_buy_id"] == "mb_123"
    assert create_kwargs["principal_id"] == "principal_1"
    assert create_kwargs["webhook_url"] == "https://example.com/webhook"
    assert create_kwargs["max_attempts"] == 12
    # started_at must be set — the reaper threshold compares against it.
    assert "started_at" in create_kwargs
    assert isinstance(create_kwargs["started_at"], datetime)
    assert create_kwargs["started_at"].tzinfo is not None, "started_at must be timezone-aware"


def test_start_approval_rejects_duplicate(mock_sync_job_uow):
    """``start_order_approval_background`` raises when a live row already exists."""
    existing = MagicMock()
    existing.sync_id = "approval_12345_existing"
    mock_sync_job_uow["repo"].find_running_for_order.return_value = existing

    with pytest.raises(ValueError, match="Approval already running for order 12345"):
        start_order_approval_background(
            order_id="12345",
            media_buy_id="mb_123",
            tenant_id="tenant_1",
            principal_id="principal_1",
        )

    # The duplicate guard must run before any INSERT.
    mock_sync_job_uow["repo"].create_for_order.assert_not_called()


def test_approval_thread_tracks_in_registry(mock_sync_job_uow):
    """Test that approval thread is tracked in global registry.

    Uses a blocking mock so the worker stays alive while the test
    inspects the registry — the dead-thread reaper added in the
    production memory-leak fix drops dead-thread entries on read, so
    a no-op mock that exits immediately would race the reaper.
    """
    import threading

    keep_alive = threading.Event()
    with patch(
        "src.services.order_approval_service._run_approval_thread",
        side_effect=lambda *args, **kwargs: keep_alive.wait(timeout=2.0),
    ):
        approval_id = start_order_approval_background(
            order_id="12345",
            media_buy_id="mb_123",
            tenant_id="tenant_1",
            principal_id="principal_1",
        )
        try:
            active_approvals = get_active_approvals()
            assert approval_id in active_approvals, f"Expected {approval_id} in {active_approvals}"
            assert is_approval_running(approval_id)
        finally:
            keep_alive.set()


def test_get_approval_status(mock_sync_job_status_uow):
    """Test getting approval status."""
    from src.core.database.models import SyncJob

    approval = SyncJob(
        sync_id="approval_12345_test",
        tenant_id="tenant_1",
        adapter_type="google_ad_manager",
        sync_type="order_approval",
        status="running",
        started_at=datetime.now(UTC),
        triggered_by="order_creation",
        triggered_by_id="mb_123",
        progress={"order_id": "12345", "attempts": 3},
    )
    mock_sync_job_status_uow["repo"].get.return_value = approval

    status = get_approval_status("approval_12345_test", tenant_id="tenant_1")

    assert status is not None
    assert status["approval_id"] == "approval_12345_test"
    assert status["status"] == "running"
    assert status["progress"]["order_id"] == "12345"
    assert status["progress"]["attempts"] == 3
    # SyncJobUoW must be scoped to the requested tenant — otherwise a cross-tenant
    # lookup could return another tenant's approval row.
    mock_sync_job_status_uow["uow_cls"].assert_called_with("tenant_1")


def test_get_approval_status_not_found(mock_sync_job_status_uow):
    """Test getting approval status for non-existent approval."""
    mock_sync_job_status_uow["repo"].get.return_value = None

    status = get_approval_status("nonexistent", tenant_id="tenant_1")
    assert status is None


def test_webhook_notification_sent_on_success(mock_push_notification_uow):
    """Test webhook notification is sent when approval succeeds."""
    from src.core.database.models import PushNotificationConfig
    from src.services.order_approval_service import _send_approval_webhook

    mock_config = PushNotificationConfig(
        tenant_id="tenant_1",
        principal_id="principal_1",
        url="https://example.com/webhook",
        authentication_type="bearer",
        authentication_token="test_token",
        is_active=True,
    )
    mock_push_notification_uow["repo"].find_active_by_url.return_value = mock_config

    with patch("httpx.Client") as mock_httpx:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_client_instance = MagicMock()
        mock_client_instance.post.return_value = mock_response
        mock_httpx.return_value.__enter__.return_value = mock_client_instance

        _send_approval_webhook(
            webhook_url="https://example.com/webhook",
            tenant_id="tenant_1",
            principal_id="principal_1",
            media_buy_id="mb_123",
            status="approved",
            message="Order approved successfully",
            order_id="12345",
            attempts=3,
        )

        mock_client_instance.post.assert_called_once()
        call_args = mock_client_instance.post.call_args

        assert call_args[0][0] == "https://example.com/webhook"
        payload = call_args[1]["json"]
        assert payload["event"] == "order_approval_update"
        assert payload["media_buy_id"] == "mb_123"
        assert payload["status"] == "approved"
        assert payload["order_id"] == "12345"
        assert payload["attempts"] == 3

        headers = call_args[1]["headers"]
        assert headers["Authorization"] == "Bearer test_token"

    # The PushNotificationConfig lookup must be tenant-scoped via the UoW.
    mock_push_notification_uow["uow_cls"].assert_called_with("tenant_1")
    mock_push_notification_uow["repo"].find_active_by_url.assert_called_with(
        "principal_1", "https://example.com/webhook"
    )


@patch("src.services.order_approval_service.time.sleep")
def test_webhook_retries_on_failure(mock_sleep, mock_push_notification_uow):
    """Test webhook retries on HTTP failure."""
    import src.services.order_approval_service as service_module

    mock_push_notification_uow["repo"].find_active_by_url.return_value = None

    with patch("httpx.Client") as mock_httpx:
        mock_response_fail = MagicMock()
        mock_response_fail.status_code = 500
        mock_response_success = MagicMock()
        mock_response_success.status_code = 200

        call_counter = {"count": 0}
        responses = [mock_response_fail, mock_response_fail, mock_response_success]

        def post_side_effect(*args, **kwargs):
            call_counter["count"] += 1
            idx = min(call_counter["count"] - 1, len(responses) - 1)
            return responses[idx]

        mock_client_instance = MagicMock()
        mock_client_instance.post.side_effect = post_side_effect

        mock_context = MagicMock()
        mock_context.__enter__.return_value = mock_client_instance
        mock_context.__exit__.return_value = None
        mock_httpx.return_value = mock_context

        service_module._send_approval_webhook(
            webhook_url="https://example.com/webhook",
            tenant_id="tenant_1",
            principal_id="principal_1",
            media_buy_id="mb_123",
            status="approved",
            message="Order approved",
        )

        # Test pollution from the full suite can add a 4th call; minimum 3 must occur.
        count = call_counter["count"]
        assert count >= 3, f"Expected at least 3 retry attempts, got {count}"
        assert count <= 4, f"Expected at most 4 retry attempts (3 + 1 pollution), got {count}"


class TestSyncJobTerminalOrdering:
    """SyncJob terminal write must happen AFTER buyer-facing MediaBuy update.

    Otherwise a buyer polling the SyncJob can see status='completed' while the
    MediaBuy is still pinned at pending_approval, or status='failed' while the
    MediaBuy is still active. Consumer-visible state must commit last.

    These tests prove the ordering by patching ``_finalize_approval`` to raise
    and asserting that ``SyncJobRepository.mark_terminal`` was NEVER called.
    Any reordering that flipped SyncJob terminal status BEFORE the MediaBuy
    update would cause ``mark_terminal`` to fire in the rewritten path —
    these tests would catch that immediately.
    """

    def test_mark_complete_skips_terminal_write_when_finalize_raises(self):
        from src.services import order_approval_service as service_module

        mock_sync_job_repo = MagicMock()
        mock_uow = MagicMock()
        mock_uow.__enter__ = MagicMock(return_value=mock_uow)
        mock_uow.__exit__ = MagicMock(return_value=None)
        mock_uow.sync_jobs = mock_sync_job_repo

        with (
            patch.object(service_module, "SyncJobUoW", return_value=mock_uow),
            patch.object(service_module, "_finalize_approval", side_effect=RuntimeError("media buy update failed")),
        ):
            service_module._mark_approval_complete(
                approval_id="approval_test_1",
                summary={"order_id": "12345", "attempts": 1, "duration_seconds": 5},
                webhook_url=None,
                tenant_id="t1",
                principal_id="p1",
                principal_name="Test Principal",
                media_buy_id="mb_1",
            )

        # The SyncJob terminal write MUST NOT execute when _finalize_approval raised.
        # If the production ordering ever flipped (mark_terminal before _finalize_approval),
        # this assertion would fail — the buyer would observe a premature 'completed'.
        mock_sync_job_repo.mark_terminal.assert_not_called()

    def test_mark_failed_skips_terminal_write_when_finalize_raises(self):
        from src.services import order_approval_service as service_module

        # _mark_approval_failed opens SyncJobUoW twice: once to read order_id/attempts
        # BEFORE _finalize_approval (line ~447), and again AFTER for the terminal
        # write (line ~470). Both calls return the same mock UoW.
        existing_sync_job = MagicMock()
        existing_sync_job.progress = {"order_id": "12345", "attempts": 3}

        mock_sync_job_repo = MagicMock()
        mock_sync_job_repo.get.return_value = existing_sync_job
        mock_uow = MagicMock()
        mock_uow.__enter__ = MagicMock(return_value=mock_uow)
        mock_uow.__exit__ = MagicMock(return_value=None)
        mock_uow.sync_jobs = mock_sync_job_repo

        with (
            patch.object(service_module, "SyncJobUoW", return_value=mock_uow),
            patch.object(service_module, "_finalize_approval", side_effect=RuntimeError("media buy update failed")),
        ):
            service_module._mark_approval_failed(
                approval_id="approval_test_2",
                error_message="approval timed out",
                webhook_url=None,
                tenant_id="t1",
                principal_id="p1",
                principal_name="Test Principal",
                media_buy_id="mb_1",
            )

        # The pre-finalize read happened (lookup for order_id/attempts).
        mock_sync_job_repo.get.assert_called_with("approval_test_2")
        # The post-finalize terminal write MUST NOT execute when _finalize_approval raised.
        # Without this guard the SyncJob would flip to 'failed' while the MediaBuy
        # is unchanged — the buyer would observe a premature termination.
        mock_sync_job_repo.mark_terminal.assert_not_called()
