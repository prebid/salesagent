"""Background order approval polling service for GAM.

GAM requires time (0-120 seconds) to run inventory forecasting before an order
can be approved. This service polls GAM in the background and notifies via webhook
when approval completes or fails.
"""

import logging
import threading
import time
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from src.core.database.database_session import get_db_session
from src.core.database.models import SyncJob

logger = logging.getLogger(__name__)

# Global registry of running approval threads
_active_approvals: dict[str, threading.Thread] = {}
_approval_lock = threading.Lock()


def start_order_approval_background(
    order_id: str,
    media_buy_id: str,
    tenant_id: str,
    principal_id: str,
    webhook_url: str | None = None,
    max_attempts: int = 12,
    poll_interval_seconds: int = 10,
) -> str:
    """Start background order approval polling.

    Args:
        order_id: GAM order ID to approve
        media_buy_id: Associated media buy ID
        tenant_id: Tenant identifier
        principal_id: Principal identifier
        webhook_url: Optional webhook URL to notify on completion
        max_attempts: Maximum polling attempts (default: 12 = 2 minutes)
        poll_interval_seconds: Seconds between polling attempts (default: 10)

    Returns:
        approval_id: The approval job ID for tracking progress

    Raises:
        ValueError: If an approval is already running for this order
    """
    # Check if approval already running
    with get_db_session() as db:
        stmt = select(SyncJob).where(
            SyncJob.sync_type == "order_approval",
            SyncJob.status == "running",
        )
        existing_approvals = db.scalars(stmt).all()

        # Check if any existing approval is for this order
        for approval in existing_approvals:
            if approval.progress and approval.progress.get("order_id") == order_id:
                raise ValueError(f"Approval already running for order {order_id}: {approval.sync_id}")

        # Create new approval job
        approval_id = f"approval_{order_id}_{int(datetime.now(UTC).timestamp())}"

        approval_job = SyncJob(
            sync_id=approval_id,
            tenant_id=tenant_id,
            adapter_type="google_ad_manager",
            sync_type="order_approval",
            status="running",
            started_at=datetime.now(UTC),
            triggered_by="order_creation",
            triggered_by_id=media_buy_id,
            progress={
                "order_id": order_id,
                "media_buy_id": media_buy_id,
                "principal_id": principal_id,
                "webhook_url": webhook_url,
                "attempts": 0,
                "max_attempts": max_attempts,
                "phase": "Starting approval polling",
            },
        )
        db.add(approval_job)
        db.commit()

    # Start background thread
    thread = threading.Thread(
        target=_run_approval_thread,
        args=(
            approval_id,
            order_id,
            media_buy_id,
            tenant_id,
            principal_id,
            webhook_url,
            max_attempts,
            poll_interval_seconds,
        ),
        daemon=True,
        name=f"approval-{approval_id}",
    )

    with _approval_lock:
        _active_approvals[approval_id] = thread

    thread.start()
    logger.info(f"Started background approval polling thread: {approval_id}")

    return approval_id


def _run_approval_thread(
    approval_id: str,
    order_id: str,
    media_buy_id: str,
    tenant_id: str,
    principal_id: str,
    webhook_url: str | None,
    max_attempts: int,
    poll_interval_seconds: int,
):
    """Run the actual approval polling in a background thread.

    This function runs in a separate thread and polls GAM every 10 seconds
    for up to 2 minutes (12 attempts) to approve the order. Updates the SyncJob
    record as it progresses.
    """
    try:
        logger.info(f"[{approval_id}] Starting order approval polling for order {order_id}")

        # Import here to avoid circular dependencies
        from src.adapters.gam.managers.orders import GAMOrdersManager
        from src.core.database.models import AdapterConfig

        # Get adapter config
        with get_db_session() as db:
            stmt = select(AdapterConfig).filter_by(tenant_id=tenant_id, adapter_type="google_ad_manager")
            adapter_config = db.scalars(stmt).first()

            if not adapter_config or not adapter_config.gam_network_code:
                _mark_approval_failed(
                    approval_id, "GAM not configured for tenant", webhook_url, tenant_id, principal_id, media_buy_id
                )
                return

        # Create GAM client
        from src.adapters.gam.client import GAMClientManager

        client_manager = GAMClientManager(adapter_config)
        orders_manager = GAMOrdersManager(client_manager, dry_run=False)

        # Poll GAM approval endpoint
        for attempt in range(1, max_attempts + 1):
            try:
                _update_approval_progress(
                    approval_id, {"attempts": attempt, "phase": f"Approval attempt {attempt}/{max_attempts}"}
                )

                logger.info(f"[{approval_id}] Approval attempt {attempt}/{max_attempts} for order {order_id}")

                # Attempt approval
                success = orders_manager.approve_order(order_id, max_retries=1)

                if success:
                    # Approval succeeded
                    _mark_approval_complete(
                        approval_id,
                        {
                            "order_id": order_id,
                            "media_buy_id": media_buy_id,
                            "attempts": attempt,
                            "duration_seconds": attempt * poll_interval_seconds,
                        },
                        webhook_url,
                        tenant_id,
                        principal_id,
                        media_buy_id,
                    )
                    logger.info(f"[{approval_id}] Order {order_id} approved after {attempt} attempts")
                    return

                # Check if we should retry
                if attempt < max_attempts:
                    logger.info(
                        f"[{approval_id}] Approval not ready yet, waiting {poll_interval_seconds}s before retry"
                    )
                    time.sleep(poll_interval_seconds)
                else:
                    # Max attempts reached
                    error_msg = f"Order approval failed after {max_attempts} attempts (2 minutes). GAM forecasting may still be in progress."
                    _mark_approval_failed(
                        approval_id, error_msg, webhook_url, tenant_id, principal_id, media_buy_id
                    )
                    return

            except Exception as e:
                error_str = str(e)

                # Check for non-retryable errors
                if "NO_FORECAST_YET" not in error_str and "ForecastingError" not in error_str:
                    # Non-retryable error
                    _mark_approval_failed(
                        approval_id,
                        f"Non-retryable error: {error_str}",
                        webhook_url,
                        tenant_id,
                        principal_id,
                        media_buy_id,
                    )
                    return

                # Retryable error - continue polling
                if attempt < max_attempts:
                    logger.warning(f"[{approval_id}] Retryable error: {error_str}, will retry")
                    time.sleep(poll_interval_seconds)
                else:
                    # Max attempts reached
                    _mark_approval_failed(
                        approval_id,
                        f"Order approval timed out after {max_attempts} attempts: {error_str}",
                        webhook_url,
                        tenant_id,
                        principal_id,
                        media_buy_id,
                    )
                    return

    except Exception as e:
        logger.error(f"[{approval_id}] Approval polling failed: {e}", exc_info=True)
        _mark_approval_failed(approval_id, str(e), webhook_url, tenant_id, principal_id, media_buy_id)

    finally:
        # Remove from active approvals
        with _approval_lock:
            _active_approvals.pop(approval_id, None)


def _update_approval_progress(approval_id: str, progress_data: dict[str, Any]):
    """Update approval job progress in database."""
    try:
        with get_db_session() as db:
            stmt = select(SyncJob).where(SyncJob.sync_id == approval_id)
            approval_job = db.scalars(stmt).first()
            if approval_job:
                # Merge with existing progress
                if approval_job.progress:
                    approval_job.progress.update(progress_data)
                else:
                    approval_job.progress = progress_data
                db.commit()
    except Exception as e:
        logger.warning(f"Failed to update approval progress: {e}")


def _mark_approval_complete(
    approval_id: str,
    summary: dict[str, Any],
    webhook_url: str | None,
    tenant_id: str,
    principal_id: str,
    media_buy_id: str,
):
    """Mark approval as completed and send webhook notification."""
    try:
        with get_db_session() as db:
            import json

            stmt = select(SyncJob).where(SyncJob.sync_id == approval_id)
            approval_job = db.scalars(stmt).first()
            if approval_job:
                approval_job.status = "completed"
                approval_job.completed_at = datetime.now(UTC)
                approval_job.summary = json.dumps(summary) if summary else None
                db.commit()

        # Send webhook notification
        if webhook_url:
            _send_approval_webhook(
                webhook_url=webhook_url,
                tenant_id=tenant_id,
                principal_id=principal_id,
                media_buy_id=media_buy_id,
                status="approved",
                message="Order approved successfully",
                order_id=summary.get("order_id"),
                attempts=summary.get("attempts"),
            )

    except Exception as e:
        logger.error(f"Failed to mark approval complete: {e}")


def _mark_approval_failed(
    approval_id: str,
    error_message: str,
    webhook_url: str | None,
    tenant_id: str,
    principal_id: str,
    media_buy_id: str,
):
    """Mark approval as failed and send webhook notification."""
    try:
        with get_db_session() as db:
            stmt = select(SyncJob).where(SyncJob.sync_id == approval_id)
            approval_job = db.scalars(stmt).first()
            if approval_job:
                approval_job.status = "failed"
                approval_job.completed_at = datetime.now(UTC)
                approval_job.error_message = error_message
                db.commit()

        # Send webhook notification
        if webhook_url:
            _send_approval_webhook(
                webhook_url=webhook_url,
                tenant_id=tenant_id,
                principal_id=principal_id,
                media_buy_id=media_buy_id,
                status="failed",
                message=error_message,
                order_id=approval_job.progress.get("order_id") if approval_job and approval_job.progress else None,
                attempts=approval_job.progress.get("attempts") if approval_job and approval_job.progress else None,
            )

    except Exception as e:
        logger.error(f"Failed to mark approval failed: {e}")


def _send_approval_webhook(
    webhook_url: str,
    tenant_id: str,
    principal_id: str,
    media_buy_id: str,
    status: str,
    message: str,
    order_id: str | None = None,
    attempts: int | None = None,
):
    """Send webhook notification for approval status update.

    Args:
        webhook_url: Webhook URL to POST to
        tenant_id: Tenant identifier
        principal_id: Principal identifier
        media_buy_id: Media buy identifier
        status: Approval status (approved, failed)
        message: Status message
        order_id: GAM order ID (if available)
        attempts: Number of polling attempts (if available)
    """
    try:
        import httpx

        payload = {
            "event": "order_approval_update",
            "media_buy_id": media_buy_id,
            "status": status,
            "message": message,
            "timestamp": datetime.now(UTC).isoformat(),
            "tenant_id": tenant_id,
            "principal_id": principal_id,
        }

        if order_id:
            payload["order_id"] = order_id
        if attempts is not None:
            payload["attempts"] = attempts

        # Get webhook authentication from push notification config
        from src.core.database.models import PushNotificationConfig

        with get_db_session() as db:
            stmt = select(PushNotificationConfig).filter_by(
                tenant_id=tenant_id, principal_id=principal_id, url=webhook_url, is_active=True
            )
            config = db.scalars(stmt).first()

        headers = {
            "Content-Type": "application/json",
            "User-Agent": "AdCP-Sales-Agent/1.0 (Order Approval Notifications)",
        }

        # Add authentication if configured
        if config:
            if config.authentication_type == "bearer" and config.authentication_token:
                headers["Authorization"] = f"Bearer {config.authentication_token}"
            elif config.authentication_type == "basic" and config.authentication_token:
                headers["Authorization"] = f"Basic {config.authentication_token}"

            if config.validation_token:
                headers["X-Webhook-Token"] = config.validation_token

        # Send webhook with retries
        max_retries = 3
        for attempt in range(max_retries):
            try:
                with httpx.Client(timeout=10.0) as client:
                    response = client.post(webhook_url, json=payload, headers=headers)

                    if 200 <= response.status_code < 300:
                        logger.info(
                            f"Approval webhook sent to {webhook_url} (status: {status}, attempt: {attempt + 1})"
                        )
                        return

                    logger.warning(
                        f"Approval webhook to {webhook_url} returned status {response.status_code} (attempt: {attempt + 1}/{max_retries})"
                    )

            except httpx.TimeoutException:
                logger.warning(
                    f"Approval webhook to {webhook_url} timed out (attempt: {attempt + 1}/{max_retries})"
                )
            except httpx.RequestError as e:
                logger.warning(
                    f"Approval webhook to {webhook_url} failed: {e} (attempt: {attempt + 1}/{max_retries})"
                )

            # Wait before retry (exponential backoff)
            if attempt < max_retries - 1:
                time.sleep(2**attempt)

        logger.error(f"Failed to send approval webhook to {webhook_url} after {max_retries} attempts")

    except Exception as e:
        logger.error(f"Error sending approval webhook: {e}", exc_info=True)


def get_active_approvals() -> list[str]:
    """Get list of approval IDs currently running in background threads."""
    with _approval_lock:
        return list(_active_approvals.keys())


def is_approval_running(approval_id: str) -> bool:
    """Check if an approval is currently running in a background thread."""
    with _approval_lock:
        return approval_id in _active_approvals


def get_approval_status(approval_id: str) -> dict[str, Any] | None:
    """Get current status of an approval job.

    Args:
        approval_id: Approval job identifier

    Returns:
        Dictionary with approval status or None if not found
    """
    try:
        with get_db_session() as db:
            stmt = select(SyncJob).where(SyncJob.sync_id == approval_id)
            approval_job = db.scalars(stmt).first()

            if not approval_job:
                return None

            return {
                "approval_id": approval_id,
                "status": approval_job.status,
                "started_at": approval_job.started_at.isoformat() if approval_job.started_at else None,
                "completed_at": approval_job.completed_at.isoformat() if approval_job.completed_at else None,
                "progress": approval_job.progress,
                "error_message": approval_job.error_message,
                "summary": approval_job.summary,
            }
    except Exception as e:
        logger.error(f"Error getting approval status: {e}")
        return None
