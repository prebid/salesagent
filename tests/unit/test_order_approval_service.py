"""Unit tests for order approval service."""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import httpx
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
def mock_db_session():
    """Mock database session."""
    with patch("src.services.order_approval_service.get_db_session") as mock_session:
        mock_db = MagicMock()
        mock_session.return_value.__enter__.return_value = mock_db
        mock_db.scalars.return_value.first.return_value = None  # No existing approval
        mock_db.scalars.return_value.all.return_value = []
        yield mock_db


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


def test_start_approval_creates_sync_job(mock_db_session):
    """Test that starting approval creates a SyncJob record."""
    from src.core.database.models import SyncJob

    approval_id = start_order_approval_background(
        order_id="12345",
        media_buy_id="mb_123",
        tenant_id="tenant_1",
        principal_id="principal_1",
        webhook_url="https://example.com/webhook",
    )

    # Verify sync job was created
    assert approval_id.startswith("approval_12345_")
    mock_db_session.add.assert_called_once()

    # Check the sync job was created with correct fields
    sync_job_call = mock_db_session.add.call_args[0][0]
    assert isinstance(sync_job_call, SyncJob)
    assert sync_job_call.sync_type == "order_approval"
    assert sync_job_call.status == "running"
    assert sync_job_call.tenant_id == "tenant_1"
    assert sync_job_call.progress["order_id"] == "12345"
    assert sync_job_call.progress["media_buy_id"] == "mb_123"
    assert sync_job_call.progress["webhook_url"] == "https://example.com/webhook"


def test_start_approval_rejects_duplicate(mock_db_session):
    """Test that starting approval for same order fails."""
    from src.core.database.models import SyncJob

    # Mock existing approval for this order
    existing_approval = SyncJob(
        sync_id="approval_12345_existing",
        tenant_id="tenant_1",
        adapter_type="google_ad_manager",
        sync_type="order_approval",
        status="running",
        started_at=datetime.now(UTC),
        triggered_by="order_creation",
        triggered_by_id="mb_123",
        progress={"order_id": "12345"},
    )
    mock_db_session.scalars.return_value.all.return_value = [existing_approval]

    with pytest.raises(ValueError, match="Approval already running for order 12345"):
        start_order_approval_background(
            order_id="12345",
            media_buy_id="mb_123",
            tenant_id="tenant_1",
            principal_id="principal_1",
        )


def test_approval_thread_tracks_in_registry(mock_db_session):
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


def test_get_approval_status(mock_db_session):
    """Test getting approval status."""
    from src.core.database.models import SyncJob

    # Mock existing approval
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
    mock_db_session.scalars.return_value.first.return_value = approval

    status = get_approval_status("approval_12345_test")

    assert status is not None
    assert status["approval_id"] == "approval_12345_test"
    assert status["status"] == "running"
    assert status["progress"]["order_id"] == "12345"
    assert status["progress"]["attempts"] == 3


def test_get_approval_status_not_found(mock_db_session):
    """Test getting approval status for non-existent approval."""
    mock_db_session.scalars.return_value.first.return_value = None

    status = get_approval_status("nonexistent")
    assert status is None


def test_webhook_notification_sent_on_success():
    """Test webhook notification is sent when approval succeeds."""
    from src.services.order_approval_service import _send_approval_webhook
    from tests.helpers.webhook_wire import capture_outbound_webhooks, constructed_http_clients

    # The client the sender builds is spied THROUGH the wire capture: the capture
    # rebinds httpx.Client/AsyncClient to inject its transport, so the spy has to be
    # installed first for the capture to wrap it.
    # The loader now opens a UNIT OF WORK and returns a PROJECTION, so it is patched at
    # its own seam rather than through a session it no longer opens (#1878). The
    # get_db_session patch still stands for the signing path's own reads.
    with (
        patch("src.services.order_approval_service.get_db_session") as mock_db,
        patch("src.services.order_approval_service._load_approval_webhook_config") as mock_load,
        constructed_http_clients() as built,
        capture_outbound_webhooks() as captured,
    ):
        # Mock push notification config
        mock_db_instance = MagicMock()
        mock_db.return_value.__enter__.return_value = mock_db_instance

        from src.core.database.models import PushNotificationConfig

        mock_config = PushNotificationConfig(
            tenant_id="tenant_1",
            principal_id="principal_1",
            url="https://example.com/webhook",
            authentication_type="bearer",
            authentication_token="test_token",
            is_active=True,
        )
        # Answered on BOTH shapes so the test grades the sender, not the query style:
        # PushNotificationConfigRepository.list_active_by_principal reads `.all()`, the
        # direct `select(PushNotificationConfig)` in the signing path reads `.first()`.
        mock_db_instance.scalars.return_value.all.return_value = [mock_config]
        mock_db_instance.scalars.return_value.first.return_value = mock_config

        from src.services.order_approval_service import ApprovalWebhookAuth

        mock_load.return_value = ApprovalWebhookAuth(
            url="https://example.com/webhook",
            authentication_type="bearer",
            authentication_token="test_token",
            validation_token=None,
        )

        # The loader now opens a UNIT OF WORK and returns a PROJECTION, so it is patched
        # at its own seam rather than through a session it no longer opens (#1878). The
        # get_db_session patch above still stands for the signing path's own reads.

        # Send webhook
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

        # Verify HTTP POST was made — graded on the bytes and headers that would
        # have gone on the socket, not on a mock's call args.
        assert len(captured) == 1
        request = captured[0]

        # Check webhook payload
        assert request.url == "https://example.com/webhook"
        payload = request.payload
        assert payload["event"] == "order_approval_update"
        assert payload["media_buy_id"] == "mb_123"
        assert payload["status"] == "approved"
        assert payload["order_id"] == "12345"
        assert payload["attempts"] == 3

        # Check authentication header — the buyer registered `bearer`, so the
        # boundary must select the legacy token mode and NOT sign (#1291 C1).
        assert request.headers["authorization"] == "Bearer test_token"
        assert "signature-input" not in request.headers

        # The delivering client must refuse redirects — an open redirect would walk this
        # POST, Authorization header and all, to whatever host the receiver names — and
        # must not hang waiting on it.
        assert built, "no HTTP client was constructed for the approval webhook"
        assert all(client.follow_redirects is False for client in built)
        assert all(client.timeout == httpx.Timeout(10.0) for client in built)


def test_approval_webhook_rejects_metadata_url_without_post():
    """Order-approval sender must share the outbound SSRF gate (no open redirect)."""
    from src.services.order_approval_service import _send_approval_webhook
    from tests.helpers.webhook_wire import capture_outbound_webhooks

    with (
        patch("src.services.order_approval_service.get_db_session") as mock_db,
        capture_outbound_webhooks() as captured,
    ):
        mock_db_instance = MagicMock()
        mock_db.return_value.__enter__.return_value = mock_db_instance
        mock_db_instance.scalars.return_value.first.return_value = None
        mock_db_instance.scalars.return_value.all.return_value = []

        _send_approval_webhook(
            webhook_url="http://169.254.169.254/latest/meta-data/",
            tenant_id="tenant_1",
            principal_id="principal_1",
            media_buy_id="mb_123",
            status="approved",
            message="Order approved successfully",
        )

        # Nothing reached the socket on ANY client — stronger than "httpx.Client was
        # never constructed", which a delivery path that moved to the async signing
        # client would satisfy vacuously. The link-local literal is refused by the real
        # gate (no DNS involved), so the gate itself is graded rather than mocked out.
        assert captured == []


@patch("src.services.order_approval_service.time.sleep")
def test_webhook_retries_on_failure(mock_sleep):
    """Test webhook retries on HTTP failure."""
    import src.services.order_approval_service as service_module
    from tests.helpers.webhook_wire import capture_outbound_webhooks

    # The receiver fails twice, then accepts.
    with (
        patch.object(service_module, "get_db_session") as mock_db,
        # No registration for this URL — patched at the loader's own seam, which now
        # opens a unit of work rather than the session this test mocks (#1878).
        patch.object(service_module, "_load_approval_webhook_config", return_value=None),
        capture_outbound_webhooks(status_codes=(500, 500, 200)) as captured,
    ):
        # Mock DB — no auth config, on both the repository (`.all()`) and the direct
        # `select()` (`.first()`) read shapes.
        mock_db_instance = MagicMock()
        mock_db.return_value.__enter__.return_value = mock_db_instance
        mock_db_instance.scalars.return_value.first.return_value = None
        mock_db_instance.scalars.return_value.all.return_value = []

        # Send webhook
        service_module._send_approval_webhook(
            webhook_url="https://example.com/webhook",
            tenant_id="tenant_1",
            principal_id="principal_1",
            media_buy_id="mb_123",
            status="approved",
            message="Order approved",
        )

        # EXACTLY three: two refusals and the acceptance. This was `>= 3 and <= 4` with
        # the comment "may see 4 calls ... 3 + 1 pollution" — a bound widened to tolerate
        # another test's delivery landing in the capture window. The capture is scoped to
        # this test's own traffic now (GH #2055), so the tolerance is no longer needed and
        # an extra delivery is a defect again rather than an expected nuisance.
        assert len(captured) == 3, f"Expected exactly 3 retry attempts, got {len(captured)}"

        # Every retry carries the SAME idempotency_key, so the receiver dedupes the
        # event rather than processing it three times.
        assert len({request.payload["idempotency_key"] for request in captured}) == 1
