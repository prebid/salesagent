"""Push notification service for A2A webhook delivery.

Handles sending POST requests to registered webhook URLs when task status changes.
Supports multiple authentication methods (bearer, basic, none).
"""

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import and_, select

from src.core.database.database_session import get_db_session
from src.core.database.models import MediaBuy, ObjectWorkflowMapping, WorkflowStep
from src.core.database.models import PushNotificationConfig as DBPushNotificationConfig

logger = logging.getLogger(__name__)


class PushNotificationService:
    """Service for delivering push notifications to registered webhook URLs."""

    def __init__(self, timeout_seconds: int = 10, max_retries: int = 3):
        """Initialize the push notification service.

        Args:
            timeout_seconds: HTTP request timeout in seconds
            max_retries: Maximum number of retry attempts for failed requests
        """
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries

    async def send_task_status_notification(
        self,
        tenant_id: str,
        principal_id: str,
        task_id: str,
        task_status: str,
        task_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Send task status notification to all registered webhook URLs.

        Args:
            tenant_id: Tenant identifier
            principal_id: Principal identifier
            task_id: Task/media buy identifier
            task_status: Current task status (submitted, working, completed, failed, etc.)
            task_data: Additional task-specific data to include in notification

        Returns:
            Dictionary with delivery results: {
                "sent": int,
                "failed": int,
                "configs": [config_id, ...],
                "errors": {config_id: error_message}
            }
        """
        # Get all active push notification configs for this principal
        with get_db_session() as db:
            # DEBUG: Log the query parameters
            logger.info(
                f"[WEBHOOK DEBUG] Querying push_notification_configs with tenant_id={tenant_id}, principal_id={principal_id}"
            )

            stmt = select(DBPushNotificationConfig).filter_by(
                tenant_id=tenant_id, principal_id=principal_id, is_active=True
            )
            configs = db.scalars(stmt).all()

            # DEBUG: Log query results
            logger.info(f"[WEBHOOK DEBUG] Found {len(configs)} webhook configs")
            for config in configs:
                logger.info(
                    f"[WEBHOOK DEBUG] Config: id={config.id}, url={config.url}, auth_type={config.authentication_type}"
                )

            if not configs:
                logger.warning(
                    f"[WEBHOOK DEBUG] No push notification configs found for tenant {tenant_id}, principal {principal_id}"
                )
                return {"sent": 0, "failed": 0, "configs": [], "errors": {}}

            # Prepare notification payload
            payload = {
                "task_id": task_id,
                "status": task_status,
                "timestamp": datetime.now(UTC).isoformat(),
                "tenant_id": tenant_id,
                "principal_id": principal_id,
            }

            # Include task-specific data if provided
            if task_data:
                payload["data"] = task_data

            # Send to all registered webhooks
            results = {"sent": 0, "failed": 0, "configs": [], "errors": {}}

            for config in configs:
                try:
                    logger.info(f"[WEBHOOK DEBUG] Attempting to deliver webhook to {config.url} for task {task_id}")
                    success = await self._deliver_webhook(config, payload)
                    if success:
                        results["sent"] += 1
                        results["configs"].append(config.id)
                        logger.info(f"[WEBHOOK DEBUG] ✅ Push notification sent to {config.url} for task {task_id}")
                    else:
                        results["failed"] += 1
                        results["errors"][config.id] = "Delivery failed after retries"
                        logger.warning(
                            f"[WEBHOOK DEBUG] ❌ Failed to deliver push notification to {config.url} for task {task_id}"
                        )
                except Exception as e:
                    results["failed"] += 1
                    results["errors"][config.id] = str(e)
                    logger.error(f"[WEBHOOK DEBUG] ❌ Error delivering push notification to {config.url}: {e}")

            return results

    async def send_media_buy_status_notification(
        self,
        media_buy_id: str,
        status: str,
        message: str | None = None,
    ) -> dict[str, Any]:
        """Send media buy status notification.

        Convenience method that looks up media buy details and sends notification.

        Args:
            media_buy_id: Media buy identifier
            status: Current media buy status
            message: Optional message to include

        Returns:
            Dictionary with delivery results
        """
        with get_db_session() as db:
            stmt = select(MediaBuy).filter_by(media_buy_id=media_buy_id)
            media_buy = db.scalars(stmt).first()
            if not media_buy:
                logger.warning(f"Media buy not found: {media_buy_id}")
                return {"sent": 0, "failed": 0, "configs": [], "errors": {"error": "Media buy not found"}}

            task_data = {
                "media_buy_id": media_buy_id,
                "buyer_ref": media_buy.buyer_ref,
                "status": status,
            }

            if message:
                task_data["message"] = message

            return await self.send_task_status_notification(
                tenant_id=media_buy.tenant_id,
                principal_id=media_buy.principal_id,
                task_id=media_buy_id,
                task_status=status,
                task_data=task_data,
            )

    async def _deliver_webhook(
        self,
        config: DBPushNotificationConfig,
        payload: dict[str, Any],
    ) -> bool:
        """Deliver webhook notification with retries and authentication.

        Args:
            config: Push notification configuration
            payload: Notification payload

        Returns:
            True if delivery succeeded, False otherwise
        """
        logger.info(f"[WEBHOOK DEBUG] _deliver_webhook called for URL: {config.url}")
        logger.info(f"[WEBHOOK DEBUG] Payload: {payload}")

        headers = {
            "Content-Type": "application/json",
            "User-Agent": "AdCP-Sales-Agent/1.0 (A2A Push Notifications)",
        }

        # Add authentication headers
        if config.authentication_type == "bearer" and config.authentication_token:
            headers["Authorization"] = f"Bearer {config.authentication_token}"
        elif config.authentication_type == "basic" and config.authentication_token:
            # Assuming token is already base64 encoded username:password
            headers["Authorization"] = f"Basic {config.authentication_token}"

        # Add validation token if provided
        if config.validation_token:
            headers["X-Webhook-Token"] = config.validation_token

        # Attempt delivery with retries
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            for attempt in range(self.max_retries):
                try:
                    response = await client.post(
                        config.url,
                        json=payload,
                        headers=headers,
                    )

                    # Consider 2xx status codes as success
                    if 200 <= response.status_code < 300:
                        logger.info(
                            f"Webhook delivered successfully to {config.url} "
                            f"(status: {response.status_code}, attempt: {attempt + 1})"
                        )
                        return True

                    # Log non-success status codes
                    logger.warning(
                        f"Webhook delivery to {config.url} returned status {response.status_code} "
                        f"(attempt: {attempt + 1}/{self.max_retries})"
                    )

                except httpx.TimeoutException:
                    logger.warning(
                        f"Webhook delivery to {config.url} timed out " f"(attempt: {attempt + 1}/{self.max_retries})"
                    )
                except httpx.RequestError as e:
                    logger.warning(
                        f"Webhook delivery to {config.url} failed with error: {e} "
                        f"(attempt: {attempt + 1}/{self.max_retries})"
                    )

                # Wait before retry (exponential backoff)
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(2**attempt)

            return False

    async def send_workflow_step_notification(
        self,
        workflow_id: str,
        step_id: str,
        step_status: str,
        step_type: str,
    ) -> dict[str, Any]:
        """Send notification for workflow step status change.

        Args:
            workflow_id: Workflow identifier
            step_id: Workflow step identifier
            step_status: Current step status
            step_type: Type of workflow step

        Returns:
            Dictionary with delivery results
        """
        logger.info(
            f"[WEBHOOK DEBUG] 🔍 send_workflow_step_notification called: workflow_id={workflow_id}, step_id={step_id}, status={step_status}"
        )

        with get_db_session() as db:
            # Find the workflow step and associated media buy
            logger.info(f"[WEBHOOK DEBUG] 1️⃣ Querying for WorkflowStep with step_id={step_id}")
            stmt = select(WorkflowStep).filter_by(step_id=step_id)
            step = db.scalars(stmt).first()
            if not step:
                logger.warning(f"[WEBHOOK DEBUG] ❌ EARLY RETURN: Workflow step not found: {step_id}")
                return {"sent": 0, "failed": 0, "configs": [], "errors": {"error": "Workflow step not found"}}
            logger.info(f"[WEBHOOK DEBUG] ✅ Found WorkflowStep: {step_id}, context_id={step.context_id}")

            # Find associated media buy via object_workflow_mappings
            # Note: ObjectWorkflowMapping has step_id, not workflow_id
            # We need to find mappings for steps in this workflow (context_id)

            # Find all workflow steps for this context (workflow_id is actually context_id)
            logger.info(f"[WEBHOOK DEBUG] 2️⃣ Querying for WorkflowSteps with context_id={workflow_id}")
            stmt = select(WorkflowStep).where(WorkflowStep.context_id == workflow_id)
            workflow_steps = db.scalars(stmt).all()

            if not workflow_steps:
                logger.warning(f"[WEBHOOK DEBUG] ❌ EARLY RETURN: No workflow steps found for context {workflow_id}")
                return {"sent": 0, "failed": 0, "configs": [], "errors": {}}
            logger.info(f"[WEBHOOK DEBUG] ✅ Found {len(workflow_steps)} workflow steps for context {workflow_id}")

            # Find media buy mapping for any step in this workflow
            step_ids = [s.step_id for s in workflow_steps]
            logger.info(f"[WEBHOOK DEBUG] 3️⃣ Querying ObjectWorkflowMapping for step_ids={step_ids}")
            stmt = select(ObjectWorkflowMapping).where(
                and_(ObjectWorkflowMapping.step_id.in_(step_ids), ObjectWorkflowMapping.object_type == "media_buy")
            )
            mapping = db.scalars(stmt).first()

            if not mapping:
                logger.warning(f"[WEBHOOK DEBUG] ❌ EARLY RETURN: No media buy associated with workflow {workflow_id}")
                return {"sent": 0, "failed": 0, "configs": [], "errors": {}}
            logger.info(f"[WEBHOOK DEBUG] ✅ Found ObjectWorkflowMapping: media_buy_id={mapping.object_id}")

            media_buy_id = mapping.object_id

            task_data = {
                "workflow_id": workflow_id,
                "step_id": step_id,
                "step_type": step_type,
                "step_status": step_status,
                "media_buy_id": media_buy_id,
            }

            # Map workflow step status to A2A task status
            status_mapping = {
                "pending": "submitted",
                "in_progress": "working",
                "completed": "completed",
                "failed": "failed",
                "requires_approval": "input-required",
            }

            a2a_status = status_mapping.get(step_status, "working")

            # Get principal_id from WorkflowStep's context relationship
            # WorkflowStep -> context_id -> Context.principal_id
            principal_id = step.context.principal_id if step.context else "unknown"

            logger.info(
                f"[WEBHOOK DEBUG] Sending push notification for media_buy {media_buy_id}, tenant={step.context.tenant_id if step.context else 'unknown'}, principal={principal_id}"
            )

            return await self.send_task_status_notification(
                tenant_id=step.context.tenant_id if step.context else "unknown",
                principal_id=principal_id,
                task_id=media_buy_id,
                task_status=a2a_status,
                task_data=task_data,
            )


# Global service instance
push_notification_service = PushNotificationService()
