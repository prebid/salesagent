"""
Background approval polling service for GAM orders.

This service handles background polling for GAM order approval when forecasting
is not ready (NO_FORECAST_YET error). It polls GAM periodically, attempts approval,
and sends webhook notifications when approval completes or fails.
"""

import logging
import threading
import time
from datetime import UTC, datetime

from src.core.database.database_session import get_db_session
from src.core.database.repositories import MediaBuyUoW, WorkflowUoW

logger = logging.getLogger(__name__)

# Global registry of running approval polling threads
_active_approval_tasks: dict[str, threading.Thread] = {}
_approval_lock = threading.Lock()


def start_order_approval_polling(
    tenant_id: str,
    order_id: str,
    workflow_step_id: str,
    polling_interval_seconds: int = 30,
    max_polling_duration_minutes: int = 15,
) -> None:
    """
    Start background polling for GAM order approval.

    Args:
        tenant_id: Tenant ID for the order
        order_id: GAM order ID to approve
        workflow_step_id: Workflow step ID for tracking
        polling_interval_seconds: Seconds between polling attempts (default: 30)
        max_polling_duration_minutes: Maximum time to poll before giving up (default: 15)
    """
    thread_id = f"approval_{order_id}_{workflow_step_id}"

    # Check if already running
    with _approval_lock:
        if thread_id in _active_approval_tasks:
            logger.warning(f"Approval polling already running for order {order_id}")
            return

    # Start background thread
    thread = threading.Thread(
        target=_run_approval_polling_thread,
        args=(tenant_id, order_id, workflow_step_id, polling_interval_seconds, max_polling_duration_minutes),
        daemon=True,
        name=f"approval-{workflow_step_id}",
    )

    with _approval_lock:
        _active_approval_tasks[thread_id] = thread

    thread.start()
    logger.info(f"Started background approval polling thread: {thread_id}")


def _run_approval_polling_thread(
    tenant_id: str,
    order_id: str,
    workflow_step_id: str,
    polling_interval_seconds: int,
    max_polling_duration_minutes: int,
):
    """
    Run the approval polling in a background thread.

    This function polls GAM periodically to attempt order approval.
    When approval succeeds, it updates the workflow step and sends a webhook notification.
    If approval fails after max duration, it marks the workflow step as failed.
    """
    thread_id = f"approval_{order_id}_{workflow_step_id}"
    start_time = datetime.now(UTC)
    max_duration_seconds = max_polling_duration_minutes * 60
    attempt = 0

    try:
        logger.info(
            f"[{workflow_step_id}] Starting approval polling for order {order_id} "
            f"(interval={polling_interval_seconds}s, max_duration={max_polling_duration_minutes}m)"
        )

        # Get GAM client manager for approval operations
        from src.adapters.gam.client import GAMClientManager
        from src.adapters.gam.managers.orders import GAMOrdersManager

        orders_manager = None
        try:
            with get_db_session() as db:
                from src.core.database.repositories.adapter_config import AdapterConfigRepository

                adapter_repo = AdapterConfigRepository(db, tenant_id)
                adapter_config = adapter_repo.find_by_tenant()
                if not adapter_config or adapter_config.adapter_type != "google_ad_manager":
                    raise ValueError(f"No GAM adapter config found for tenant {tenant_id}")

                if not adapter_config.gam_network_code:
                    raise ValueError(f"GAM network code not configured for tenant {tenant_id}")

                gam_config = adapter_repo.get_gam_config(adapter_config)

            # Initialize GAM client and orders manager
            client_manager = GAMClientManager(gam_config, adapter_config.gam_network_code)
            orders_manager = GAMOrdersManager(client_manager, dry_run=False)

        except Exception as e:
            logger.error(f"[{workflow_step_id}] Failed to initialize adapter: {e}")
            _mark_approval_failed(tenant_id, workflow_step_id, f"Adapter initialization failed: {e}")
            return

        # Poll GAM for approval readiness
        while True:
            attempt += 1
            elapsed_seconds = (datetime.now(UTC) - start_time).total_seconds()

            # Check if max duration exceeded
            if elapsed_seconds > max_duration_seconds:
                error_msg = (
                    f"Approval polling timed out after {max_polling_duration_minutes} minutes "
                    f"({attempt} attempts). GAM forecasting still not ready."
                )
                logger.error(f"[{workflow_step_id}] {error_msg}")
                _mark_approval_failed(tenant_id, workflow_step_id, error_msg)
                break

            # Update progress
            _update_approval_progress(
                tenant_id,
                workflow_step_id,
                {
                    "attempt": attempt,
                    "elapsed_seconds": int(elapsed_seconds),
                    "max_duration_seconds": max_duration_seconds,
                    "status": "polling",
                },
            )

            # Attempt approval (single retry)
            logger.info(f"[{workflow_step_id}] Approval attempt {attempt} for order {order_id}")
            try:
                approval_success = orders_manager.approve_order(order_id, max_retries=1)

                if approval_success:
                    logger.info(f"[{workflow_step_id}] Order {order_id} approved successfully")
                    _mark_approval_complete(tenant_id, workflow_step_id, order_id, attempt, elapsed_seconds)

                    # Send webhook notification
                    _send_approval_webhook(tenant_id, order_id, workflow_step_id, "completed")
                    break
                else:
                    # Still not ready - continue polling
                    logger.info(
                        f"[{workflow_step_id}] Order {order_id} forecasting not ready yet, "
                        f"will retry in {polling_interval_seconds}s"
                    )

            except Exception as e:
                logger.warning(f"[{workflow_step_id}] Approval attempt {attempt} failed: {e}")
                # Continue polling - error might be transient

            # Wait before next attempt
            time.sleep(polling_interval_seconds)

    except Exception as e:
        logger.error(f"[{workflow_step_id}] Approval polling failed: {e}", exc_info=True)
        _mark_approval_failed(tenant_id, workflow_step_id, str(e))
        _send_approval_webhook(tenant_id, order_id, workflow_step_id, "failed")

    finally:
        # Remove from active tasks
        with _approval_lock:
            _active_approval_tasks.pop(thread_id, None)


def _update_approval_progress(tenant_id: str, workflow_step_id: str, progress_data: dict) -> None:
    """Update workflow step progress in database via WorkflowUoW."""
    try:
        with WorkflowUoW(tenant_id) as uow:
            assert uow.workflows is not None
            step = uow.workflows.get_step_by_id(workflow_step_id)
            if step:
                td = step.transaction_details or {}
                td["progress"] = progress_data
                step.transaction_details = td
            # auto-commits on exit
    except Exception as e:
        logger.warning(f"Failed to update approval progress: {e}")


def _mark_approval_complete(
    tenant_id: str, workflow_step_id: str, order_id: str, attempts: int, elapsed_seconds: float
) -> None:
    """Mark approval as completed in workflow step via WorkflowUoW."""
    try:
        with WorkflowUoW(tenant_id) as uow:
            assert uow.workflows is not None
            step = uow.workflows.update_status(
                workflow_step_id,
                status="completed",
                completed_at=datetime.now(UTC),
                response_data={
                    "status": "completed",
                    "order_id": order_id,
                    "message": f"Order approved successfully after {attempts} attempts ({int(elapsed_seconds)}s)",
                },
            )
            if step:
                step.transaction_details = {
                    "approval_status": "approved",
                    "gam_order_status": "APPROVED",
                    "attempts": attempts,
                    "elapsed_seconds": int(elapsed_seconds),
                    "completed_at": datetime.now(UTC).isoformat(),
                }
            logger.info(f"Marked workflow step {workflow_step_id} as completed")
    except Exception as e:
        logger.error(f"Failed to mark approval complete: {e}")


def _mark_approval_failed(tenant_id: str, workflow_step_id: str, error_message: str) -> None:
    """Mark approval as failed in workflow step via WorkflowUoW."""
    try:
        with WorkflowUoW(tenant_id) as uow:
            assert uow.workflows is not None
            step = uow.workflows.update_status(
                workflow_step_id,
                status="failed",
                error_message=error_message,
                response_data={"status": "failed", "error": error_message},
            )
            if step:
                step.transaction_details = {"approval_status": "failed", "failure_reason": error_message}
            logger.info(f"Marked workflow step {workflow_step_id} as failed")
    except Exception as e:
        logger.error(f"Failed to mark approval failed: {e}")


def _send_approval_webhook(tenant_id: str, order_id: str, workflow_step_id: str, status: str) -> None:
    """Send webhook notification for approval completion/failure."""
    try:
        with MediaBuyUoW(tenant_id) as uow:
            assert uow.media_buys is not None
            media_buy = uow.media_buys.get_by_id(order_id)

            if not media_buy:
                logger.warning(f"No media buy found for order {order_id}, cannot send webhook")
                return

            # TODO: Implement webhook notification once push_notification_config_id is added to MediaBuy model
            # and webhook delivery service has the appropriate function
            logger.info(f"Webhook notification for order {order_id} (status={status}) - not yet implemented")

    except Exception as e:
        logger.error(f"Failed to send approval webhook: {e}", exc_info=True)


def get_active_approval_tasks() -> list[str]:
    """Get list of approval task IDs currently running in background threads."""
    with _approval_lock:
        return list(_active_approval_tasks.keys())


def is_approval_task_running(order_id: str) -> bool:
    """Check if approval polling is running for a specific order."""
    with _approval_lock:
        return any(order_id in task_id for task_id in _active_approval_tasks)
