"""Context persistence manager for A2A protocol support."""

import asyncio
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from a2a.types import Task, TaskStatusUpdateEvent
from adcp import create_a2a_webhook_payload, create_mcp_webhook_payload
from adcp.types import McpWebhookPayload, TaskType
from adcp.webhooks import GeneratedTaskStatus


def _coerce_task_type(raw: str | None) -> TaskType | None:
    """Map an arbitrary step ``tool_name`` to a ``TaskType`` enum member.

    adcp 5.0 closed ``TaskType`` to a fixed enum. Workflow steps carry a free-form
    ``tool_name`` that includes values not in the enum (custom approval actions,
    internal review steps, schema events). Returning ``None`` for non-enum
    values tells the caller to skip webhook construction rather than throw a
    ValidationError when ``create_mcp_webhook_payload`` validates the field.
    """
    if not raw:
        return None
    try:
        return TaskType(raw)
    except ValueError:
        return None


from sqlalchemy import select

from src.core.database.database_session import DatabaseManager
from src.core.database.models import Context, ObjectWorkflowMapping, WorkflowStep
from src.core.database.models import Context as DBContext
from src.services.protocol_webhook_service import get_protocol_webhook_service

logger = logging.getLogger(__name__)


class ContextManager(DatabaseManager):
    """Manages persistent context for conversations and tasks.

    Inherits from DatabaseManager for standardized session management.
    """

    def __init__(self):
        super().__init__()

    def create_context(
        self, tenant_id: str, principal_id: str, initial_conversation: list[dict[str, Any]] | None = None
    ) -> Context:
        """Create a new context for asynchronous operations.

        Note: Synchronous operations don't need a context.
        This is only for async/HITL workflows where we need to track conversation.

        Args:
            tenant_id: The tenant ID
            principal_id: The principal ID
            initial_conversation: Optional initial conversation history

        Returns:
            The created Context object
        """
        context_id = f"ctx_{uuid.uuid4().hex[:12]}"

        context = Context(
            context_id=context_id,
            tenant_id=tenant_id,
            principal_id=principal_id,
            conversation_history=initial_conversation or [],
            last_activity_at=datetime.now(UTC),
        )

        try:
            self.session.add(context)
            self.session.commit()
            logger.debug("Created context %s for principal %s", context_id, principal_id)
            # Refresh to get any database-generated values
            self.session.refresh(context)
            # Detach from session
            self.session.expunge(context)
            return context
        except Exception:
            self.session.rollback()
            logger.exception("Failed to create context")
            raise
        finally:
            # DatabaseManager handles session cleanup differently
            pass

    def get_context(self, context_id: str) -> Context | None:
        """Get a context by ID.

        Args:
            context_id: The context ID

        Returns:
            The Context object or None if not found
        """
        session = self.session
        try:
            stmt = select(Context).filter_by(context_id=context_id)

            context = session.scalars(stmt).first()
            if context:
                # Detach from session
                session.expunge(context)
            return context
        finally:
            session.close()

    def get_or_create_context(
        self, tenant_id: str, principal_id: str, context_id: str | None = None, is_async: bool = False
    ) -> Context | None:
        """Get existing context or create new one if needed.

        For synchronous operations, returns None.
        For asynchronous operations, returns or creates a context.

        Args:
            tenant_id: The tenant ID
            principal_id: The principal ID
            context_id: Optional existing context ID
            is_async: Whether this is an async operation needing context

        Returns:
            Context object for async operations, None for sync operations
        """
        if not is_async:
            return None

        if context_id:
            return self.get_context(context_id)
        else:
            return self.create_context(tenant_id, principal_id)

    def update_activity(self, context_id: str) -> None:
        """Update the last activity timestamp for a context.

        Args:
            context_id: The context ID
        """
        try:
            stmt = select(Context).filter_by(context_id=context_id)
            context = self.session.scalars(stmt).first()
            if context:
                context.last_activity_at = datetime.now(UTC)
                self.session.commit()
        finally:
            # DatabaseManager handles session cleanup differently
            pass

    def create_workflow_step(
        self,
        context_id: str,
        step_type: str,  # tool_call, approval, notification, etc.
        owner: str,  # principal, publisher, system - who needs to act
        status: str = "pending",  # pending, in_progress, completed, failed, requires_approval
        tool_name: str | None = None,
        request_data: dict[str, Any] | Any | None = None,
        response_data: dict[str, Any] | None = None,
        assigned_to: str | None = None,
        error_message: str | None = None,
        transaction_details: dict[str, Any] | None = None,
        object_mappings: list[dict[str, str]] | None = None,
        initial_comment: str | None = None,
        request_metadata: dict[str, Any] | None = None,
    ) -> WorkflowStep:
        """Create a workflow step in the database.

        Args:
            context_id: The context ID
            step_type: Type of step (tool_call, approval, etc.)
            owner: Who needs to act (principal=advertiser, publisher=seller, system=automated)
            status: Step status
            tool_name: Optional tool name if this is a tool call
            request_data: Original request data (dict or Pydantic model — serialized at this boundary)
            response_data: Response/result data
            assigned_to: Specific user/system if assigned
            error_message: Error message if failed
            transaction_details: Actual API calls made
            object_mappings: List of objects this step relates to [{object_type, object_id, action}]
            initial_comment: Optional initial comment to add
            request_metadata: Extra metadata to merge into request_data after serialization

        Returns:
            The created WorkflowStep object
        """
        # Serialize Pydantic models at the DB boundary
        from pydantic import BaseModel

        if isinstance(request_data, BaseModel):
            request_data = request_data.model_dump(mode="json")
        if request_metadata and request_data is not None:
            request_data.update(request_metadata)
        step_id = f"step_{uuid.uuid4().hex[:12]}"

        # Initialize comments array with initial comment if provided
        comments = []
        if initial_comment:
            comments.append({"user": "system", "timestamp": datetime.now(UTC).isoformat(), "text": initial_comment})

        step = WorkflowStep(
            step_id=step_id,
            context_id=context_id,
            step_type=step_type,
            owner=owner,
            status=status,
            tool_name=tool_name,
            request_data=request_data if request_data is not None else {},
            response_data=response_data if response_data is not None else {},
            assigned_to=assigned_to,
            error_message=error_message,
            transaction_details=transaction_details if transaction_details is not None else {},
            comments=comments,
            created_at=datetime.now(UTC),
        )

        if status == "completed":
            step.completed_at = datetime.now(UTC)

        session = self.session
        try:
            session.add(step)

            # Create object mappings if provided
            if object_mappings:
                for mapping in object_mappings:
                    obj_mapping = ObjectWorkflowMapping(
                        object_type=mapping["object_type"],
                        object_id=mapping["object_id"],
                        step_id=step_id,
                        action=mapping.get("action", step_type),
                        created_at=datetime.now(UTC),
                    )
                    session.add(obj_mapping)

            session.commit()
            session.refresh(step)
            # Detach from session
            session.expunge(step)
            logger.debug("Created workflow step %s for context %s", step_id, context_id)
            return step
        except Exception:
            session.rollback()
            logger.exception("Failed to create workflow step")
            raise
        finally:
            session.close()

    def update_workflow_step(
        self,
        step_id: str,
        status: str | None = None,
        response_data: dict[str, Any] | None = None,
        error_message: str | None = None,
        transaction_details: dict[str, Any] | None = None,
        add_comment: dict[str, str] | None = None,
        tenant_id: str | None = None,
    ) -> None:
        """Update a workflow step's status and data.

        Args:
            step_id: The step ID
            status: New status
            response_data: Response/result data
            error_message: Error message if failed
            transaction_details: Actual API calls made
            add_comment: Optional comment to add {user, comment}
            tenant_id: Tenant scope — joins through Context for isolation.
                If provided, the step must belong to this tenant or no update occurs.
        """
        session = self.session
        try:
            stmt = select(WorkflowStep).filter_by(step_id=step_id)
            if tenant_id:
                stmt = stmt.join(DBContext).where(DBContext.tenant_id == tenant_id)

            step = session.scalars(stmt).first()
            if step:
                old_status = step.status  # Capture old status before changing

                if status:
                    step.status = status
                    if status in ["completed", "failed"] and not step.completed_at:
                        step.completed_at = datetime.now(UTC)

                if response_data is not None:
                    step.response_data = response_data
                if error_message is not None:
                    step.error_message = error_message
                if transaction_details is not None:
                    step.transaction_details = transaction_details

                if add_comment:
                    # Ensure comments is a list
                    if not isinstance(step.comments, list):
                        step.comments = []
                    # Create a new list to trigger SQLAlchemy change detection
                    new_comments = list(step.comments)
                    new_comments.append(
                        {
                            "user": add_comment.get("user", "system"),
                            "timestamp": datetime.now(UTC).isoformat(),
                            "text": add_comment.get("text", add_comment.get("comment", "")),
                        }
                    )
                    step.comments = new_comments

                session.commit()
                logger.debug(
                    "Updated workflow step %s: %s -> %s",
                    step_id,
                    old_status,
                    step.status,
                )

                # Send push notifications if status changed
                if status and step:
                    self._send_push_notifications(step, status, session)
        finally:
            session.close()

    def mark_human_needed(
        self,
        context_id: str,
        reason: str,
        clarification_details: str | None = None,
    ) -> None:
        """Mark that human intervention is needed for this context.

        Args:
            context_id: The context ID
            reason: Why human review is needed
            clarification_details: Additional details about what needs review
        """
        self.create_workflow_step(
            context_id=context_id,
            step_type="approval",
            owner="publisher",  # Publisher needs to review
            status="requires_approval",
            request_data={
                "reason": reason,
                "details": clarification_details,
                "protocol": "mcp",  # Default to MCP for internal system actions
            },
            initial_comment=reason,
        )

    def get_pending_steps(
        self,
        owner: str | None = None,
        assigned_to: str | None = None,
        tenant_id: str | None = None,
    ) -> list[WorkflowStep]:
        """Get pending workflow steps from the work queue.

        The owner field tells us who needs to act:
        - 'principal': waiting on the advertiser/buyer
        - 'publisher': waiting on the publisher/seller
        - 'system': automated system processing

        Args:
            owner: Filter by owner (principal, publisher, system)
            assigned_to: Filter by specific assignee
            tenant_id: Tenant scope — joins through Context for isolation.
                If provided, only steps belonging to this tenant are returned.

        Returns:
            List of pending WorkflowStep objects
        """
        session = self.session
        try:
            stmt = select(WorkflowStep).where(WorkflowStep.status.in_(["pending", "requires_approval"]))

            if tenant_id:
                stmt = stmt.join(DBContext).where(DBContext.tenant_id == tenant_id)

            if owner:
                stmt = stmt.where(WorkflowStep.owner == owner)
            if assigned_to:
                stmt = stmt.where(WorkflowStep.assigned_to == assigned_to)

            steps = session.scalars(stmt).all()
            # Detach all from session
            for step in steps:
                session.expunge(step)
            return list(steps)
        finally:
            session.close()

    def get_object_lifecycle(
        self, object_type: str, object_id: str, tenant_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Get all workflow steps for an object's lifecycle.

        Args:
            object_type: Type of object (media_buy, creative, product, etc.)
            object_id: The object's ID
            tenant_id: Tenant scope — joins through Context for isolation.
                If provided, only mappings belonging to this tenant are returned.

        Returns:
            List of workflow steps with their details
        """
        session = self.session
        try:
            # Query object mappings to find all related steps, scoped to tenant via Context join
            stmt = (
                select(ObjectWorkflowMapping)
                .join(WorkflowStep)
                .where(
                    ObjectWorkflowMapping.object_type == object_type,
                    ObjectWorkflowMapping.object_id == object_id,
                )
                .order_by(ObjectWorkflowMapping.created_at)
            )
            if tenant_id:
                stmt = stmt.join(DBContext).where(DBContext.tenant_id == tenant_id)

            mappings = session.scalars(stmt).all()

            lifecycle = []
            for mapping in mappings:
                step = mapping.workflow_step
                if step:
                    lifecycle.append(
                        {
                            "step_id": step.step_id,
                            "action": mapping.action,
                            "step_type": step.step_type,
                            "status": step.status,
                            "owner": step.owner,
                            "assigned_to": step.assigned_to,
                            "created_at": step.created_at.isoformat() if step.created_at else None,
                            "completed_at": step.completed_at.isoformat() if step.completed_at else None,
                            "tool_name": step.tool_name,
                            "error_message": step.error_message,
                            "comments": step.comments,
                        }
                    )

            return lifecycle
        finally:
            session.close()

    def add_message(self, context_id: str, role: str, content: str) -> None:
        """Add a message to the conversation history.

        This is for human-readable messages (clarifications, refinements).
        Tool calls and operational steps go in workflow_steps.

        Args:
            context_id: The context ID
            role: Message role (user, assistant, system)
            content: Message content
        """
        session = self.session
        try:
            stmt = select(Context).filter_by(context_id=context_id)

            context = session.scalars(stmt).first()
            if context:
                if not isinstance(context.conversation_history, list):
                    context.conversation_history = []

                context.conversation_history.append(
                    {"role": role, "content": content, "timestamp": datetime.now(UTC).isoformat()}
                )
                context.last_activity_at = datetime.now(UTC)
                session.commit()
        finally:
            session.close()

    def set_tool_state(self, context_id: str, tool_name: str, state: dict[str, Any]) -> None:
        """Set the current tool state in a context.

        This is for tracking partial progress within a tool for HITL scenarios.

        Args:
            context_id: The context ID
            tool_name: The tool name
            state: The tool state
        """
        # For now, we can store this in the latest workflow step's response_data
        # or create a dedicated notification step
        pass

    def get_context_status(self, context_id: str) -> dict[str, Any]:
        """Get the overall status of a context by checking its workflow steps.

        Status is derived from the workflow steps, not stored in context itself.

        Args:
            context_id: The context ID

        Returns:
            Status information derived from workflow steps
        """
        session = self.session
        try:
            stmt = select(WorkflowStep).filter_by(context_id=context_id)
            steps = session.scalars(stmt).all()

            if not steps:
                return {"status": "no_steps", "summary": "No workflow steps created"}

            # Count steps by status
            status_counts = {"pending": 0, "in_progress": 0, "requires_approval": 0, "completed": 0, "failed": 0}

            for step in steps:
                if step.status in status_counts:
                    status_counts[step.status] += 1

            # Determine overall status
            if status_counts["failed"] > 0:
                overall_status = "has_failures"
            elif status_counts["requires_approval"] > 0:
                overall_status = "awaiting_approval"
            elif status_counts["pending"] > 0 or status_counts["in_progress"] > 0:
                overall_status = "pending_steps"
            else:
                overall_status = "all_completed"

            return {"status": overall_status, "counts": status_counts, "total_steps": len(steps)}
        finally:
            session.close()

    def get_contexts_for_principal(self, tenant_id: str, principal_id: str, limit: int = 10) -> list[Context]:
        """Get recent contexts for a principal.

        Args:
            tenant_id: The tenant ID
            principal_id: The principal ID
            limit: Maximum number of contexts to return

        Returns:
            List of Context objects ordered by last activity
        """
        session = self.session
        try:
            stmt = (
                select(Context)
                .filter_by(tenant_id=tenant_id, principal_id=principal_id)
                .order_by(Context.last_activity_at.desc())
                .limit(limit)
            )
            contexts = session.scalars(stmt).all()

            # Detach all from session
            for context in contexts:
                session.expunge(context)
            return list(contexts)
        finally:
            session.close()

    def link_workflow_to_object(
        self,
        step_id: str,
        object_type: str,
        object_id: str,
        action: str | None = None,
        tenant_id: str | None = None,
    ) -> None:
        """Link a workflow step to an object after the step is created.

        This is useful when you need to associate objects with a workflow step
        after the step has already been created.

        Args:
            step_id: The workflow step ID
            object_type: Type of object (media_buy, creative, product, etc.)
            object_id: The object's ID
            action: Optional action being performed (defaults to step_type)
            tenant_id: Tenant scope — joins through Context for isolation.
                If provided, the step must belong to this tenant or no link is created.
        """
        session = self.session
        try:
            # Get the step to use its step_type as default action
            stmt = select(WorkflowStep).filter_by(step_id=step_id)
            if tenant_id:
                stmt = stmt.join(DBContext).where(DBContext.tenant_id == tenant_id)
            step = session.scalars(stmt).first()

            if not step:
                logger.warning("Step %s not found, cannot link object", step_id)
                return

            obj_mapping = ObjectWorkflowMapping(
                object_type=object_type,
                object_id=object_id,
                step_id=step_id,
                action=action or step.step_type,
                created_at=datetime.now(UTC),
            )
            session.add(obj_mapping)
            session.commit()
            logger.debug("Linked %s %s to workflow step %s", object_type, object_id, step_id)
        except Exception:
            session.rollback()
            logger.exception("Failed to link object to workflow")
            raise
        finally:
            session.close()

    def _send_push_notifications(self, step: WorkflowStep, new_status: str, session: Any) -> None:
        """Send push notifications via registered webhooks for workflow step status changes.

        Args:
            step: The workflow step that was updated
            new_status: The new status value
            session: Active database session
        """
        try:
            import requests

            from src.core.database.models import PushNotificationConfig

            # Get object mappings for this step
            stmt = select(ObjectWorkflowMapping).filter_by(step_id=step.step_id)
            mappings = session.scalars(stmt).all()

            if not mappings:
                logger.debug("No object mappings found for step %s", step.step_id)
                return

            # Get context to find tenant_id
            context_stmt = select(Context).filter_by(context_id=step.context_id)
            context = session.scalars(context_stmt).first()
            if not context:
                logger.warning("No context found for step %s", step.step_id)
                return

            tenant_id = context.tenant_id
            principal_id = context.principal_id

            # Workflow callbacks are request-scoped. Durable catalog-change
            # subscriptions live in push_notification_configs with a separate
            # purpose and must not fan out task-status updates.
            from uuid import uuid4

            cfg_dict = (step.request_data or {}).get("push_notification_config") or {}
            url = cfg_dict.get("url")
            if not url:
                logger.debug("No push notification URL present for step %s", step.step_id)
                return

            authentication = cfg_dict.get("authentication") or {}
            schemes = authentication.get("schemes") or []
            auth_type = schemes[0] if isinstance(schemes, list) and schemes else None
            auth_token = authentication.get("credentials")

            context_obj = getattr(step, "context", None)
            derived_tenant_id = tenant_id or (getattr(context_obj, "tenant_id", None))
            derived_principal_id = getattr(context_obj, "principal_id", None)

            push_notification_config = PushNotificationConfig(
                id=cfg_dict.get("id") or f"pnc_{uuid4().hex[:16]}",
                tenant_id=derived_tenant_id,
                principal_id=derived_principal_id,
                url=url,
                authentication_type=auth_type,
                authentication_token=auth_token,
                purpose="async_task",
                is_active=True,
            )
            service = get_protocol_webhook_service()

            # Send notifications for each mapping (media buy, creative, etc.)
            for mapping in mappings:
                logger.debug(
                    "Processing mapping: %s %s action=%s",
                    mapping.object_type,
                    mapping.object_id,
                    mapping.action,
                )

                logger.debug(
                    "Sending webhook to %s for %s %s",
                    push_notification_config.url,
                    mapping.object_type,
                    mapping.object_id,
                )

                # Build webhook payload based on protocol type. Source of
                # truth is ``step.request_data["protocol"]`` —
                # ``create_workflow_step`` merges the caller's
                # ``request_metadata={"protocol": ...}`` into
                # ``request_data`` at persist time (see line 182).
                # ``_create_media_buy_impl`` writes ``identity.protocol``
                # there, which now carries the actual inbound transport
                # via :class:`TransportDetectMiddleware`. Defaults to
                # ``"mcp"`` only when the step pre-dates the middleware
                # (legacy data) or was created outside an HTTP request
                # (admin / lifespan paths). See #202.
                raw_task_type = step.tool_name or mapping.action or ""
                task_type_enum = _coerce_task_type(raw_task_type)
                protocol = (step.request_data or {}).get("protocol", "mcp")
                try:
                    status_enum = GeneratedTaskStatus(new_status)
                except ValueError:
                    status_enum = GeneratedTaskStatus.unknown

                payload: Task | TaskStatusUpdateEvent | McpWebhookPayload
                if protocol == "a2a":
                    payload = create_a2a_webhook_payload(
                        task_id=step.step_id,
                        status=status_enum,
                        context_id=step.context_id,
                        result=step.response_data or {},
                    )
                elif task_type_enum is not None:
                    # adcp 5.0+: create_mcp_webhook_payload returns McpWebhookPayload directly
                    # and requires task_type to be a closed TaskType enum value
                    # (was a free-form ``domain`` string pre-5.0).
                    payload = create_mcp_webhook_payload(
                        step.step_id,
                        status_enum,
                        task_type_enum,
                        result=step.response_data,
                    )
                else:
                    # Non-enum tool_name (internal review action, custom step,
                    # etc.) — MCP webhook spec doesn't model these. Skip the
                    # webhook rather than fail at validation. The buyer-facing
                    # state still updates via the DB; the webhook is best-effort.
                    logger.info(
                        "Skipping MCP webhook for step %s: tool_name=%r is not a TaskType enum member.",
                        step.step_id,
                        raw_task_type,
                    )
                    continue

                metadata: dict[str, Any] = {
                    "task_type": raw_task_type or "unknown",
                    "tenant_id": derived_tenant_id,
                    "principal_id": derived_principal_id,
                }

                try:
                    # If we're already in an event loop, schedule the send; otherwise run it directly
                    try:
                        loop = asyncio.get_running_loop()
                        task = loop.create_task(
                            service.send_notification(
                                push_notification_config=push_notification_config,
                                payload=payload,
                                metadata=metadata,
                            )
                        )

                        def _log_task_result(t: asyncio.Task, config_url: str = push_notification_config.url) -> None:
                            try:
                                t.result()
                                logger.debug("Webhook sent successfully for %s", config_url)
                            except Exception as exc:
                                logger.error("Webhook failed for %s: %s", config_url, exc)

                        task.add_done_callback(_log_task_result)
                    except RuntimeError:
                        # No running loop; safe to run synchronously
                        asyncio.run(
                            service.send_notification(
                                push_notification_config=push_notification_config,
                                payload=payload,
                                metadata=metadata,
                            )
                        )
                        logger.debug("Webhook sent successfully for %s", push_notification_config.url)

                except requests.exceptions.Timeout:
                    logger.error("Webhook timeout for %s", push_notification_config.url)
                except requests.exceptions.RequestException as e:
                    logger.error("Webhook failed for %s: %s", push_notification_config.url, e)

        except Exception:
            logger.exception("Error sending push notifications")
            # Don't fail the workflow update if notifications fail


# Singleton instance getter for compatibility
_context_manager_instance = None


def get_context_manager() -> ContextManager:
    """Get or create singleton ContextManager instance."""
    global _context_manager_instance
    if _context_manager_instance is None:
        _context_manager_instance = ContextManager()
    return _context_manager_instance
