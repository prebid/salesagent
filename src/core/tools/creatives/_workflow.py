"""Workflow steps, notifications, and audit logging for creative sync."""

import logging
from typing import Any

from adcp import PushNotificationConfig
from adcp.types.generated_poc.core.context import ContextObject
from fastmcp.exceptions import ToolError
from sqlalchemy import select

from src.core.audit_logger import get_audit_logger
from src.core.database.database_session import get_db_session
from src.core.schemas import CreativeStatusEnum
from src.core.tool_context import ToolContext

logger = logging.getLogger(__name__)


def _create_sync_workflow_steps(
    creatives_needing_approval: list[dict[str, Any]],
    principal_id: str,
    tenant: dict[str, Any],
    approval_mode: str,
    push_notification_config: PushNotificationConfig | dict | None,
    context: ContextObject | dict | None,
    ctx: Any,
) -> None:
    """Create workflow steps for creatives requiring approval.

    Creates a persistent async context and one workflow step per creative,
    plus ``ObjectWorkflowMapping`` records linking each creative to its step.
    """
    from src.core.context_manager import get_context_manager
    from src.core.database.models import ObjectWorkflowMapping

    ctx_manager = get_context_manager()

    # Ensure principal_id is available (should always be set by this point)
    if principal_id is None:
        raise ToolError("Principal ID required for workflow creation")

    # Get or create persistent context for this operation
    # is_async=True because we're creating workflow steps that need tracking
    persistent_ctx = ctx_manager.get_or_create_context(
        principal_id=principal_id, tenant_id=tenant["tenant_id"], is_async=True
    )

    if persistent_ctx is None:
        raise ToolError("Failed to create workflow context")

    with get_db_session() as session:
        for creative_info in creatives_needing_approval:
            # Build appropriate comment based on status
            status = creative_info.get("status", CreativeStatusEnum.pending_review.value)
            if status == CreativeStatusEnum.rejected.value:
                comment = (
                    f"Creative '{creative_info['name']}' (format: {creative_info['format']}) was rejected by AI review"
                )
            elif status == CreativeStatusEnum.pending_review.value:
                if approval_mode == "ai-powered":
                    comment = f"Creative '{creative_info['name']}' (format: {creative_info['format']}) requires human review per AI recommendation"
                else:
                    comment = f"Creative '{creative_info['name']}' (format: {creative_info['format']}) requires manual approval"
            else:
                comment = f"Creative '{creative_info['name']}' (format: {creative_info['format']}) requires review"

            # Create workflow step for creative approval
            request_data_for_workflow = {
                "creative_id": creative_info["creative_id"],
                "format": creative_info["format"],
                "name": creative_info["name"],
                "status": status,
                "approval_mode": approval_mode,
            }
            # Store push_notification_config if provided for async notification
            # Engine's _pydantic_json_serializer handles Pydantic models in JSONB automatically
            if push_notification_config:
                request_data_for_workflow["push_notification_config"] = push_notification_config

            # Store context if provided (for echoing back in webhook)
            if context:
                request_data_for_workflow["context"] = context

            # Store protocol type for webhook payload creation
            # ToolContext = A2A, Context (FastMCP) = MCP
            request_data_for_workflow["protocol"] = "a2a" if isinstance(ctx, ToolContext) else "mcp"

            step = ctx_manager.create_workflow_step(
                context_id=persistent_ctx.context_id,
                step_type="creative_approval",
                owner="publisher",
                status="requires_approval",
                tool_name="sync_creatives",
                request_data=request_data_for_workflow,
                initial_comment=comment,
            )

            # Create ObjectWorkflowMapping to link creative to workflow step
            # This is CRITICAL for webhook delivery when creative is approved
            mapping = ObjectWorkflowMapping(
                step_id=step.step_id,
                object_type="creative",
                object_id=creative_info["creative_id"],
                action="approval_required",
            )
            session.add(mapping)

        session.commit()
        logger.info(f"📋 Created {len(creatives_needing_approval)} workflow steps for creative approval")


def _send_creative_notifications(
    creatives_needing_approval: list[dict[str, Any]],
    tenant: dict[str, Any],
    approval_mode: str,
    principal_id: str | None,
) -> None:
    """Send Slack notifications for creatives requiring human review.

    Only sends for ``require-human`` approval mode.  For ``ai-powered`` mode,
    notifications are sent asynchronously after AI review completes.
    """
    # Note: For ai-powered mode, notifications are sent AFTER AI review completes (with AI reasoning)
    # Only send immediate notifications for require-human mode or existing creatives with AI review results
    logger.info(
        f"Checking Slack notification: creatives={len(creatives_needing_approval)}, webhook={tenant.get('slack_webhook_url')}, approval_mode={approval_mode}"
    )
    if not (creatives_needing_approval and tenant.get("slack_webhook_url") and approval_mode == "require-human"):
        return

    from src.services.slack_notifier import get_slack_notifier

    logger.info(f"Sending Slack notifications for {len(creatives_needing_approval)} creatives (require-human mode)")
    tenant_config = {"features": {"slack_webhook_url": tenant["slack_webhook_url"]}}
    notifier = get_slack_notifier(tenant_config)

    for creative_info in creatives_needing_approval:
        status = creative_info.get("status", CreativeStatusEnum.pending_review.value)
        ai_review_reason = creative_info.get("ai_review_reason")

        # Ensure required fields are strings
        creative_id_str = str(creative_info.get("creative_id", "unknown"))
        format_str = str(creative_info.get("format", "unknown"))
        principal_name_str = str(principal_id) if principal_id else "unknown"

        if status == CreativeStatusEnum.rejected.value:
            # For rejected creatives, send a different notification
            # TODO: Add notify_creative_rejected method to SlackNotifier
            notifier.notify_creative_pending(
                creative_id=creative_id_str,
                principal_name=principal_name_str,
                format_type=format_str,
                media_buy_id=None,
                tenant_id=tenant["tenant_id"],
                ai_review_reason=ai_review_reason,
            )
        else:
            # For pending creatives (human review required)
            notifier.notify_creative_pending(
                creative_id=creative_id_str,
                principal_name=principal_name_str,
                format_type=format_str,
                media_buy_id=None,
                tenant_id=tenant["tenant_id"],
                ai_review_reason=ai_review_reason,
            )


def _audit_log_sync(
    tenant: dict[str, Any],
    principal_id: str | None,
    synced_creatives: list,
    failed_creatives: list[dict[str, Any]],
    assignment_list: list,
    creative_ids: list[str] | None,
    dry_run: bool,
    created_count: int,
    updated_count: int,
    unchanged_count: int,
    failed_count: int,
    creatives_needing_approval: list[dict[str, Any]],
) -> None:
    """Write audit log entries for a sync_creatives operation.

    Writes two audit entries: one at the AdCP level (always) and one at the
    sync_creatives level (only when the principal is found in the database).
    """
    audit_logger = get_audit_logger("AdCP", tenant["tenant_id"])

    # Build error message from failed creatives
    error_message = None
    if failed_creatives:
        error_lines = []
        for fc in failed_creatives[:5]:  # Limit to first 5 errors to avoid huge messages
            creative_id = fc.get("creative_id", "unknown")
            error_text = fc.get("error", "Unknown error")
            error_lines.append(f"{creative_id}: {error_text}")
        error_message = "; ".join(error_lines)
        if len(failed_creatives) > 5:
            error_message += f" (and {len(failed_creatives) - 5} more)"

    # Ensure principal_id is string for audit logging
    principal_id_str = str(principal_id) if principal_id else "unknown"

    audit_logger.log_operation(
        operation="sync_creatives",
        principal_name=principal_id_str,
        principal_id=principal_id_str,
        adapter_id="N/A",
        success=len(failed_creatives) == 0,
        error=error_message,
        details={
            "synced_count": len(synced_creatives),
            "failed_count": len(failed_creatives),
            "assignment_count": len(assignment_list),
            "creative_ids_filter": creative_ids,
            "dry_run": dry_run,
        },
    )

    # Log audit trail for sync_creatives operation (with principal name from DB)
    try:
        with get_db_session() as audit_session:
            from src.core.database.models import Principal as DBPrincipal

            # Get principal info for audit log
            principal_stmt = select(DBPrincipal).filter_by(tenant_id=tenant["tenant_id"], principal_id=principal_id)
            principal = audit_session.scalars(principal_stmt).first()

            if principal:
                # Create audit logger and log the operation
                audit_logger = get_audit_logger("sync_creatives", tenant["tenant_id"])
                audit_logger.log_operation(
                    operation="sync_creatives",
                    principal_name=principal.name,
                    principal_id=principal_id_str,
                    adapter_id=principal_id_str,  # Use principal_id as adapter_id for consistency
                    success=(failed_count == 0),
                    details={
                        "created_count": created_count,
                        "updated_count": updated_count,
                        "unchanged_count": unchanged_count,
                        "failed_count": failed_count,
                        "assignment_count": len(assignment_list) if assignment_list else 0,
                        "approval_required_count": len(creatives_needing_approval),
                        "dry_run": dry_run,
                        "creative_ids_filter": creative_ids,
                    },
                    tenant_id=tenant["tenant_id"],
                )
    except Exception as e:
        # Don't fail the operation if audit logging fails
        logger.warning(f"Failed to write audit log for sync_creatives: {e}")
