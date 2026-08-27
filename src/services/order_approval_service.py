"""Background order approval polling service for GAM.

GAM requires time (0-120 seconds) to run inventory forecasting before an order
can be approved. This service polls GAM in the background and notifies via webhook
when approval completes or fails.
"""

import logging
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import select

from src.core.database.database_session import get_db_session
from src.core.database.models import SyncJob
from src.core.thread_registry import ThreadRegistry
from src.core.webhook_validator import reject_unsafe_outbound_webhook_url, webhook_url_for_log

logger = logging.getLogger(__name__)

# Global registry of running approval threads. ThreadRegistry reaps dead
# threads on every read — same defensive cleanup as the sync registry
# (production memory-leak triage #5).
_active_approvals = ThreadRegistry()


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
                    approval_id, "GAM not configured for tenant", webhook_url, tenant_id, principal_id, media_buy_id
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
                    _mark_approval_failed(approval_id, error_msg, webhook_url, tenant_id, principal_id, media_buy_id)
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
        _active_approvals.remove(approval_id)


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
        # Read the progress fields BEFORE the session closes. ``db.commit()``
        # expires every attribute on ``approval_job``, so touching ``.progress``
        # after the ``with`` block raises DetachedInstanceError — which the
        # ``except`` below then swallowed, and the buyer was never told the order
        # had failed at all (salesagent-98t2, reproduced by
        # tests/integration/test_order_approval_webhook_signing.py).
        order_id: str | None = None
        attempts: int | None = None

        with get_db_session() as db:
            stmt = select(SyncJob).where(SyncJob.sync_id == approval_id)
            approval_job = db.scalars(stmt).first()
            if approval_job:
                approval_job.status = "failed"
                approval_job.completed_at = datetime.now(UTC)
                approval_job.error_message = error_message
                db.commit()

                progress = approval_job.progress or {}
                order_id = progress.get("order_id")
                attempts = progress.get("attempts")

        # Send webhook notification
        if webhook_url:
            _send_approval_webhook(
                webhook_url=webhook_url,
                tenant_id=tenant_id,
                principal_id=principal_id,
                media_buy_id=media_buy_id,
                status="failed",
                message=error_message,
                order_id=order_id,
                attempts=attempts,
            )

    except Exception as e:
        logger.error(f"Failed to mark approval failed: {e}")


@dataclass(frozen=True, slots=True)
class ApprovalWebhookAuth:
    """The buyer's registration, PROJECTED to primitives — the row never escapes.

    Structurally satisfies :class:`~src.core.signing.webhook_sender_factory.WebhookAuthConfig`
    (``url`` / ``authentication_type`` / ``authentication_token``), so it is passed as
    ``config=`` to the delivery boundary unchanged, plus ``validation_token`` for the one
    extra header this service adds.

    WHY A PROJECTION AND NOT THE ORM ROW (#1878). The loader used to return the live row
    and carried a paragraph explaining why that was safe: "Detaching it at the end of the
    session is safe — ``get_db_session`` closes without committing, so the loaded columns
    survive; the expiry hazard ... needs a ``commit()``." That is correctness resting on a
    subtle SQLAlchemy behaviour explained in a comment — and it is why the loader could not
    simply move to a unit of work, since ``BaseUoW.__exit__`` DOES commit
    (``uow.py`` :107-108) and no session sets ``expire_on_commit=False`` (``uow.py`` :379),
    which would expire the row before these fields are read.

    Copying the values inside the session removes the hazard rather than documenting it:
    the unit of work may commit and expire whatever it likes, because nothing downstream
    holds anything that can expire.
    """

    url: str
    authentication_type: str | None
    authentication_token: str | None
    validation_token: str | None


def _load_approval_webhook_config(tenant_id: str, principal_id: str, webhook_url: str) -> ApprovalWebhookAuth | None:
    """The buyer's registration for this URL, read through the repository's unit of work.

    That registration is the ONE selector for how this notification is authenticated
    (#1291 C1, salesagent-98t2): it feeds both :func:`_approval_webhook_headers` and the
    delivery boundary's auth-strategy choice, so it is read once here rather than at each
    of those two points.

    Projected to :class:`ApprovalWebhookAuth` INSIDE the unit of work — see that class for
    why the ORM row must not leave it.
    """
    from src.core.database.repositories.uow import PushNotificationConfigUoW

    with PushNotificationConfigUoW(tenant_id) as uow:
        assert uow.push_notification_configs is not None
        row = uow.push_notification_configs.get_active_by_url(principal_id, webhook_url)
        if row is None:
            return None
        return ApprovalWebhookAuth(
            url=row.url,
            authentication_type=row.authentication_type,
            authentication_token=row.authentication_token,
            validation_token=row.validation_token,
        )


def _approval_webhook_headers(config: ApprovalWebhookAuth | None) -> dict[str, str]:
    """The genuinely EXTRA headers for an order-approval webhook POST.

    Neither ``Content-Type`` nor the authentication header belongs here. The
    delivery boundary (``src.core.signing.webhook_sender_factory``, #1291 C1)
    frames the body it serialized, and derives the auth scheme from this same
    ``config`` row — legacy HMAC, legacy bearer (which is where a ``basic``
    registration now lands, since any non-HMAC scheme carrying a credential
    selects the bearer arm), or the RFC 9421 default when no ``authentication``
    block was registered. Setting either header here would authenticate the
    delivery twice, in two disagreeing ways.

    ``validation_token`` is a receiver-side echo, not an auth scheme, so it
    stays a plain extra header.
    """
    headers = {"User-Agent": "AdCP-Sales-Agent/1.0 (Order Approval Notifications)"}
    if config and config.validation_token:
        headers["X-Webhook-Token"] = config.validation_token
    return headers


def _reject_unsafe_approval_webhook_url(webhook_url: str) -> bool:
    """Return True when the order-approval outbound URL fails the SSRF gate."""
    rejected, _error_msg = reject_unsafe_outbound_webhook_url(webhook_url, log=logger, kind="OrderApproval")
    return rejected


def _post_approval_webhook_with_retries(
    webhook_url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
    config: ApprovalWebhookAuth | None,
    tenant_id: str,
) -> None:
    """Deliver the approval payload with retries, authenticated per *config*.

    Serialization, authentication and the POST are ONE act at the signing
    boundary (#1441), so the bytes signed are the bytes sent; ``config`` selects
    the arm. No ``repo`` is passed: this caller holds no session, and
    ``webhook_sender_factory.signing_repo`` opens a short-lived one per
    delivery precisely so senders don't each grow their own.

    Redirects are still never followed — the boundary's ``httpx`` client keeps
    the library default (``follow_redirects=False``), so a 302 to a metadata or
    private address cannot bypass the pre-send SSRF gate.
    """
    from adcp.webhooks import generate_webhook_idempotency_key

    from src.core.signing import deliver_adcp_webhook_sync

    # ONE key per distinct event, reused across this event's retries — a fresh
    # one per attempt would defeat the receiver's dedup.
    idempotency_key = generate_webhook_idempotency_key()

    safe_url = webhook_url_for_log(webhook_url)
    max_retries = 3
    for attempt in range(max_retries):
        try:
            delivery = deliver_adcp_webhook_sync(
                url=webhook_url,
                payload=payload,
                idempotency_key=idempotency_key,
                config=config,
                tenant_id=tenant_id,
                extra_headers=headers,
            )

            if 200 <= delivery.status_code < 300:
                logger.info(
                    "Approval webhook sent to %s (status: %s, attempt: %s)",
                    safe_url,
                    payload.get("status"),
                    attempt + 1,
                )
                return

            logger.warning(
                "Approval webhook to %s returned status %s (attempt: %s/%s)",
                safe_url,
                delivery.status_code,
                attempt + 1,
                max_retries,
            )

        except httpx.TimeoutException:
            logger.warning(
                "Approval webhook to %s timed out (attempt: %s/%s)",
                safe_url,
                attempt + 1,
                max_retries,
            )
        except httpx.RequestError as e:
            logger.warning(
                "Approval webhook to %s failed: %s (attempt: %s/%s)",
                safe_url,
                e,
                attempt + 1,
                max_retries,
            )

        if attempt < max_retries - 1:
            time.sleep(2**attempt)

    logger.error("Failed to send approval webhook to %s after %s attempts", safe_url, max_retries)


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

        if _reject_unsafe_approval_webhook_url(webhook_url):
            return

        config = _load_approval_webhook_config(tenant_id, principal_id, webhook_url)
        _post_approval_webhook_with_retries(
            webhook_url,
            payload,
            _approval_webhook_headers(config),
            config,
            tenant_id,
        )

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
        logger.error(f"Error getting approval status: {e}")
        return None
