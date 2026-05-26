"""Background order approval polling service for GAM.

GAM requires time (0-120 seconds) to run inventory forecasting before an order
can be approved. This service polls GAM in the background and notifies via webhook
when approval completes or fails.
"""

import logging
import threading
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from src.core.audit_logger import AuditLogger
from src.core.database.database_session import get_db_session
from src.core.database.repositories import (
    MediaBuyUoW,
    PushNotificationConfigRepository,
    SyncJobRepository,
    SyncJobUoW,
)
from src.core.thread_registry import ThreadRegistry

# Canonical adapter name shared with src/adapters/google_ad_manager.py — audit-log
# queries filter on this value, so the two must stay in lockstep.
_ADAPTER_NAME = "google_ad_manager"

# Threshold beyond which a running SyncJob is presumed orphaned (process died
# mid-approval). The default polling window is 2 minutes (max_attempts * poll_interval),
# so 10 minutes is ≥5× the maximum legitimate runtime — anything older is a stale row
# blocking re-approval of its order_id.
_STALE_APPROVAL_THRESHOLD = timedelta(minutes=10)

logger = logging.getLogger(__name__)

# Global registry of running approval threads. ThreadRegistry reaps dead
# threads on every read — same defensive cleanup as the sync registry
# (production memory-leak triage #5).
_active_approvals = ThreadRegistry()

# Serialize the duplicate-scan + INSERT against concurrent callers for the
# same order_id. SyncJobRepository's tenant-scoped queries cover the in-memory
# race only within a single process; this lock closes the same-process
# SELECT-then-INSERT window.
_approval_lock = threading.Lock()


def lookup_webhook_url(tenant_id: str, principal_id: str) -> str | None:
    """Resolve an active webhook URL for ``(tenant_id, principal_id)``.

    Returns the URL of the most recently created active ``PushNotificationConfig``,
    or ``None`` if the principal has not registered one. Callers in the
    admin-approval path use this to bridge the gap between a buyer's webhook
    registration and the background polling thread — without it the polling
    thread runs but the buyer never hears about the result.
    """
    try:
        with get_db_session() as db:
            repo = PushNotificationConfigRepository(db, tenant_id)
            config = repo.find_most_recent_active_for_principal(principal_id)
            return config.url if config else None
    except Exception as e:
        logger.warning(
            "Failed to resolve webhook URL: %s",
            e,
            exc_info=True,
            extra={"tenant_id": tenant_id, "principal_id": principal_id},
        )
        return None


def start_order_approval_background(
    order_id: str,
    media_buy_id: str,
    tenant_id: str,
    principal_id: str,
    principal_name: str | None = None,
    webhook_url: str | None = None,
    max_attempts: int = 12,
    poll_interval_seconds: int = 10,
) -> str:
    """Start background order approval polling.

    Args:
        order_id: GAM order ID to approve
        media_buy_id: Associated media buy ID
        tenant_id: Tenant identifier
        principal_id: Principal identifier (machine-readable)
        principal_name: Principal display name for audit logs. Falls back to
            ``principal_id`` when ``None`` — callers should pass the real name
            so audit messages read ``"... for principal 'Acme Corp' ..."``
            instead of ``"... for principal 'principal_42' ..."``.
        webhook_url: Optional webhook URL to notify on completion
        max_attempts: Maximum polling attempts (default: 12 = 2 minutes)
        poll_interval_seconds: Seconds between polling attempts (default: 10)

    Returns:
        approval_id: The approval job ID for tracking progress

    Raises:
        ValueError: If a live approval is already running for this order
    """
    # Hold _approval_lock for the reaper + duplicate scan + INSERT + thread
    # registration. Without this two concurrent calls for the same order_id
    # can both pass the duplicate check, both INSERT a "running" SyncJob,
    # and both start polling threads. Orphaned SyncJobs older than
    # _STALE_APPROVAL_THRESHOLD are flipped to "failed" before the
    # duplicate check so they don't block legitimate re-approval forever.
    with _approval_lock:
        now = datetime.now(UTC)
        approval_id = f"approval_{order_id}_{int(now.timestamp())}"

        with SyncJobUoW(tenant_id) as uow:
            assert uow.sync_jobs is not None
            reaped = uow.sync_jobs.reap_stale(_STALE_APPROVAL_THRESHOLD, now=now)
            for reaped_id in reaped:
                logger.warning("[%s] reaped stale approval (threshold=%s)", reaped_id, _STALE_APPROVAL_THRESHOLD)

            existing = uow.sync_jobs.find_running_for_order(order_id)
            if existing is not None:
                raise ValueError(f"Approval already running for order {order_id}: {existing.sync_id}")

            uow.sync_jobs.create_for_order(
                sync_id=approval_id,
                adapter_type=_ADAPTER_NAME,
                order_id=order_id,
                media_buy_id=media_buy_id,
                principal_id=principal_id,
                webhook_url=webhook_url,
                started_at=now,
                max_attempts=max_attempts,
            )

        thread = threading.Thread(
            target=_run_approval_thread,
            args=(
                approval_id,
                order_id,
                media_buy_id,
                tenant_id,
                principal_id,
                principal_name or principal_id,
                webhook_url,
                max_attempts,
                poll_interval_seconds,
            ),
            daemon=True,
            name=f"approval-{approval_id}",
        )
        _active_approvals.add(approval_id, thread)

    thread.start()
    logger.info(f"Started background approval polling thread: {approval_id}")

    return approval_id


def _run_approval_thread(
    approval_id: str,
    order_id: str,
    media_buy_id: str,
    tenant_id: str,
    principal_id: str,
    principal_name: str,
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

        # Get adapter config via repository
        with get_db_session() as db:
            from src.core.database.repositories.adapter_config import AdapterConfigRepository

            adapter_repo = AdapterConfigRepository(db, tenant_id)
            adapter_config = adapter_repo.find_by_tenant()

            if not adapter_config or not adapter_config.gam_network_code:
                _mark_approval_failed(
                    approval_id,
                    "GAM not configured for tenant",
                    webhook_url,
                    tenant_id,
                    principal_id,
                    principal_name,
                    media_buy_id,
                )
                return

            gam_config = adapter_repo.get_gam_config(adapter_config)

        # Create GAM client
        from src.adapters.gam.client import GAMClientManager

        client_manager = GAMClientManager(gam_config, adapter_config.gam_network_code)
        orders_manager = GAMOrdersManager(client_manager, dry_run=False)

        # Poll GAM approval endpoint
        for attempt in range(1, max_attempts + 1):
            try:
                _update_approval_progress(
                    approval_id,
                    tenant_id,
                    {"attempts": attempt, "phase": f"Approval attempt {attempt}/{max_attempts}"},
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
                        principal_name,
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
                        approval_id,
                        error_msg,
                        webhook_url,
                        tenant_id,
                        principal_id,
                        principal_name,
                        media_buy_id,
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
                        principal_name,
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
                        principal_name,
                        media_buy_id,
                    )
                    return

    except Exception as e:
        logger.error(f"[{approval_id}] Approval polling failed: {e}", exc_info=True)
        _mark_approval_failed(approval_id, str(e), webhook_url, tenant_id, principal_id, principal_name, media_buy_id)

    finally:
        # Remove from active approvals
        _active_approvals.remove(approval_id)


def _update_approval_progress(approval_id: str, tenant_id: str, progress_data: dict[str, Any]):
    """Merge progress_data into the SyncJob.progress JSONB column for the tenant.

    Delegates to SyncJobRepository.merge_progress, which handles the
    flag_modified guard so SQLAlchemy detects the in-place JSONB mutation.
    """
    try:
        with SyncJobUoW(tenant_id) as uow:
            assert uow.sync_jobs is not None
            uow.sync_jobs.merge_progress(approval_id, progress_data)
    except Exception as e:
        logger.warning(
            "Failed to update approval progress: %s",
            e,
            exc_info=True,
            extra={"approval_id": approval_id, "tenant_id": tenant_id},
        )


def _finalize_approval(
    *,
    media_buy_id: str,
    tenant_id: str,
    principal_id: str,
    principal_name: str,
    media_buy_status: str,
    audit_success: bool,
    audit_details: dict[str, Any],
    audit_error: str | None,
    webhook_url: str | None,
    webhook_status: str,
    webhook_message: str,
    webhook_order_id: str | None,
    webhook_attempts: int | None,
) -> None:
    """Advance MediaBuy.status, write audit log, and fire webhook.

    The MediaBuy update is fatal — if it fails the audit log and webhook
    do NOT fire, because otherwise the buyer would receive an "approved"
    notification for a media buy still pinned at pending_approval. The
    audit log and webhook are best-effort (wrapped in try/except).
    """
    with MediaBuyUoW(tenant_id) as uow:
        assert uow.media_buys is not None
        uow.media_buys.update_status(media_buy_id, media_buy_status)

    try:
        audit = AuditLogger(adapter_name=_ADAPTER_NAME, tenant_id=tenant_id)
        audit.log_operation(
            operation="approve_order",
            principal_name=principal_name,
            principal_id=principal_id,
            adapter_id=str(audit_details.get("order_id") or ""),
            success=audit_success,
            error=audit_error,
            details=audit_details,
        )
    except Exception as e:
        logger.error(
            "Failed to write approval audit log: %s",
            e,
            exc_info=True,
            extra={
                "tenant_id": tenant_id,
                "principal_id": principal_id,
                "media_buy_id": media_buy_id,
            },
        )

    if webhook_url:
        _send_approval_webhook(
            webhook_url=webhook_url,
            tenant_id=tenant_id,
            principal_id=principal_id,
            media_buy_id=media_buy_id,
            status=webhook_status,
            message=webhook_message,
            order_id=webhook_order_id,
            attempts=webhook_attempts,
        )


def _flip_sync_job_terminal(
    approval_id: str,
    tenant_id: str,
    *,
    status: str,
    summary: dict[str, Any] | None = None,
    error_message: str | None = None,
) -> None:
    """Write the terminal status on a ``SyncJob`` via the repository.

    Called by ``_mark_approval_complete`` (status="completed", summary=...) and
    ``_mark_approval_failed`` (status="failed", error_message=...). Centralizes
    the ``SyncJobUoW + mark_terminal`` boilerplate so the buyer-facing
    ordering invariant lives in one place.
    """
    with SyncJobUoW(tenant_id) as uow:
        assert uow.sync_jobs is not None
        uow.sync_jobs.mark_terminal(
            approval_id,
            status=status,
            completed_at=datetime.now(UTC),
            summary=summary,
            error_message=error_message,
        )


def _mark_approval_complete(
    approval_id: str,
    summary: dict[str, Any],
    webhook_url: str | None,
    tenant_id: str,
    principal_id: str,
    principal_name: str,
    media_buy_id: str,
):
    """Mark approval as completed, advance MediaBuy.status, audit, and webhook.

    Ordering: MediaBuy update (consumer-visible) commits BEFORE SyncJob flips
    to "completed". If MediaBuy update fails the SyncJob stays at "running" and
    the stale-approval reaper eventually picks it up.
    """
    try:
        _finalize_approval(
            media_buy_id=media_buy_id,
            tenant_id=tenant_id,
            principal_id=principal_id,
            principal_name=principal_name,
            media_buy_status="active",
            audit_success=True,
            audit_details={
                "order_id": summary.get("order_id"),
                "media_buy_id": media_buy_id,
                "attempts": summary.get("attempts"),
                "duration_seconds": summary.get("duration_seconds"),
            },
            audit_error=None,
            webhook_url=webhook_url,
            webhook_status="approved",
            webhook_message="Order approved successfully",
            webhook_order_id=summary.get("order_id"),
            webhook_attempts=summary.get("attempts"),
        )

        _flip_sync_job_terminal(approval_id, tenant_id, status="completed", summary=summary)

    except Exception as e:
        logger.error(
            "Failed to mark approval complete: %s",
            e,
            exc_info=True,
            extra={
                "approval_id": approval_id,
                "tenant_id": tenant_id,
                "principal_id": principal_id,
                "media_buy_id": media_buy_id,
            },
        )


def _mark_approval_failed(
    approval_id: str,
    error_message: str,
    webhook_url: str | None,
    tenant_id: str,
    principal_id: str,
    principal_name: str,
    media_buy_id: str,
):
    """Mark approval as failed, terminalize MediaBuy.status, audit, and webhook.

    Ordering: MediaBuy update (consumer-visible) commits BEFORE SyncJob flips
    to "failed". If MediaBuy update fails the SyncJob stays at "running" and
    the stale-approval reaper eventually picks it up.
    """
    order_id: str | None = None
    attempts: int | None = None
    try:
        with SyncJobUoW(tenant_id) as uow:
            assert uow.sync_jobs is not None
            existing = uow.sync_jobs.get(approval_id)
            if existing and existing.progress:
                order_id = existing.progress.get("order_id")
                attempts = existing.progress.get("attempts")

        _finalize_approval(
            media_buy_id=media_buy_id,
            tenant_id=tenant_id,
            principal_id=principal_id,
            principal_name=principal_name,
            media_buy_status="failed",
            audit_success=False,
            audit_details={
                "order_id": order_id,
                "media_buy_id": media_buy_id,
                "attempts": attempts,
            },
            audit_error=error_message,
            webhook_url=webhook_url,
            webhook_status="failed",
            webhook_message=error_message,
            webhook_order_id=order_id,
            webhook_attempts=attempts,
        )

        _flip_sync_job_terminal(approval_id, tenant_id, status="failed", error_message=error_message)

    except Exception as e:
        logger.error(
            "Failed to mark approval failed: %s",
            e,
            exc_info=True,
            extra={
                "approval_id": approval_id,
                "tenant_id": tenant_id,
                "principal_id": principal_id,
                "media_buy_id": media_buy_id,
            },
        )


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

        payload: dict[str, Any] = {
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
        with get_db_session() as db:
            repo = PushNotificationConfigRepository(db, tenant_id)
            config = repo.find_active_by_url(principal_id, webhook_url)

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
                logger.warning(f"Approval webhook to {webhook_url} timed out (attempt: {attempt + 1}/{max_retries})")
            except httpx.RequestError as e:
                logger.warning(f"Approval webhook to {webhook_url} failed: {e} (attempt: {attempt + 1}/{max_retries})")

            # Wait before retry (exponential backoff)
            if attempt < max_retries - 1:
                time.sleep(2**attempt)

        logger.error(f"Failed to send approval webhook to {webhook_url} after {max_retries} attempts")

    except Exception as e:
        logger.error(f"Error sending approval webhook: {e}", exc_info=True)


def get_active_approvals() -> list[str]:
    """Get list of approval IDs currently running in background threads.

    Reaps dead threads on read so the returned list reflects live state
    even if the worker's ``finally`` cleanup didn't fire.
    """
    return _active_approvals.list_active()


def is_approval_running(approval_id: str) -> bool:
    """Check if an approval is currently running in a background thread.

    Reaps dead threads on read — an approval_id with a dead thread is no
    longer running, so this returns False (and the entry is pruned).
    """
    return _active_approvals.contains(approval_id)


def get_approval_status(approval_id: str, tenant_id: str) -> dict[str, Any] | None:
    """Get current status of an approval job within a tenant.

    Args:
        approval_id: Approval job identifier
        tenant_id: Tenant scope for the lookup. Required so the read is
            scoped properly — passing the wrong tenant returns ``None``
            rather than leaking another tenant's data.

    Returns:
        Dictionary with approval status or ``None`` if no row matches.
    """
    try:
        with get_db_session() as db:
            repo = SyncJobRepository(db, tenant_id)
            approval_job = repo.get(approval_id)

            if not approval_job:
                return None

            started_at_iso = None
            if approval_job.started_at is not None:
                # Handle both datetime and SQLAlchemy DateTime objects
                if hasattr(approval_job.started_at, "isoformat"):
                    started_at_iso = approval_job.started_at.isoformat()
                else:
                    started_at_iso = str(approval_job.started_at)

            completed_at_iso = None
            if approval_job.completed_at is not None:
                # Handle both datetime and SQLAlchemy DateTime objects
                if hasattr(approval_job.completed_at, "isoformat"):
                    completed_at_iso = approval_job.completed_at.isoformat()
                else:
                    completed_at_iso = str(approval_job.completed_at)

            return {
                "approval_id": approval_id,
                "status": approval_job.status,
                "started_at": started_at_iso,
                "completed_at": completed_at_iso,
                "progress": approval_job.progress,
                "error_message": approval_job.error_message,
                "summary": approval_job.summary,
            }
    except Exception as e:
        logger.error(
            "Error getting approval status: %s",
            e,
            exc_info=True,
            extra={"approval_id": approval_id, "tenant_id": tenant_id},
        )
        return None
