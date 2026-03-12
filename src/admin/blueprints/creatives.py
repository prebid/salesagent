"""Creative formats management blueprint for admin UI."""

import asyncio
import contextlib
import logging
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any

from a2a.types import Task, TaskStatusUpdateEvent
from adcp import create_a2a_webhook_payload, create_mcp_webhook_payload
from adcp.types import CreativeAction, McpWebhookPayload, SyncCreativeResult, SyncCreativesSuccessResponse
from adcp.types.generated_poc.core.context import ContextObject
from adcp.webhooks import GeneratedTaskStatus

from src.core.database.models import (
    PushNotificationConfig as DBPushNotificationConfig,
)
from src.core.database.repositories.creative import CreativeRepository
from src.services.protocol_webhook_service import get_protocol_webhook_service

# TODO: Missing module - these functions need to be implemented
# from creative_formats import discover_creative_formats_from_url, parse_creative_spec


# Placeholder implementations for missing functions
def parse_creative_spec(url):
    """Parse creative specification from URL - placeholder implementation."""
    return {"success": False, "error": "Creative format parsing not yet implemented", "url": url}


def discover_creative_formats_from_url(url):
    """Discover creative formats from URL - placeholder implementation."""
    return []


from flask import Blueprint, jsonify, redirect, render_template, request, url_for

from src.admin.utils import require_tenant_access
from src.admin.utils.audit_decorator import log_admin_action
from src.core.database.repositories.uow import AdminCreativeUoW

# Note: CreativeFormat table was dropped in migration f2addf453200
# All format-related routes have been removed

logger = logging.getLogger(__name__)

# Create Blueprint
creatives_bp = Blueprint("creatives", __name__)

# Global ThreadPoolExecutor for async AI review (managed lifecycle)
_ai_review_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="ai_review_")
_ai_review_tasks: dict[str, Any] = {}  # task_id -> Future mapping
_ai_review_lock = threading.Lock()  # Protect _ai_review_tasks dict


def _cleanup_completed_tasks():
    """Clean up completed tasks older than 1 hour."""
    import time

    now = time.time()
    with _ai_review_lock:
        completed_tasks = []
        for task_id, task_info in _ai_review_tasks.items():
            if task_info["future"].done() and (now - task_info["created_at"]) > 3600:
                completed_tasks.append(task_id)
        for task_id in completed_tasks:
            del _ai_review_tasks[task_id]
            logger.debug(f"Cleaned up completed AI review task: {task_id}")


def _compute_media_buy_status_from_flight_dates(media_buy) -> str:
    """Compute status based on flight dates: 'active' if within window, else 'scheduled'."""
    now = datetime.now(UTC)

    start_time = None
    if media_buy.start_time:
        raw_start = media_buy.start_time
        start_time = raw_start.replace(tzinfo=UTC) if raw_start.tzinfo is None else raw_start.astimezone(UTC)
    elif media_buy.start_date:
        start_time = datetime.combine(media_buy.start_date, datetime.min.time()).replace(tzinfo=UTC)

    end_time = None
    if media_buy.end_time:
        raw_end = media_buy.end_time
        end_time = raw_end.replace(tzinfo=UTC) if raw_end.tzinfo is None else raw_end.astimezone(UTC)
    elif media_buy.end_date:
        end_time = datetime.combine(media_buy.end_date, datetime.max.time()).replace(tzinfo=UTC)

    # If start time passed and end time not passed, set to active
    if start_time and end_time and now >= start_time and now <= end_time:
        return "active"

    return "scheduled"


async def _call_webhook_for_creative_status(
    creative_id,
    tenant_id: str,
):
    """Send protocol-level push notification for creative status update.

    Creates its own database session via UoW (read-only — no writes needed).
    Checks if all creatives in the sync_creatives task have been reviewed.
    Only fires the webhook when ALL creatives have been reviewed (approved or rejected).

    Returns:
        bool: True if webhook delivered successfully, False otherwise (or if no config found)
    """
    if not tenant_id:
        raise ValueError("tenant_id is required for _call_webhook_for_creative_status")

    from src.core.schemas import CreativeStatusEnum

    try:
        with AdminCreativeUoW(tenant_id) as uow:
            assert uow.workflows is not None
            assert uow.creatives is not None
            mapping = uow.workflows.get_latest_mapping_for_object("creative", creative_id)

            if not mapping:
                logger.debug(f"No workflow mapping found for creative {creative_id}; skipping webhook notification")
                return False

            step = uow.workflows.get_step_by_id(mapping.step_id)
            if not step or not step.request_data:
                logger.debug(
                    f"Workflow step missing or has no request_data for creative {creative_id}; skipping webhook notification"
                )
                return False

            # Get ALL creatives associated with this workflow step
            all_mappings = [m for m in uow.workflows.get_mappings_for_step(step.step_id) if m.object_type == "creative"]

            if not all_mappings:
                logger.debug(f"No creative mappings found for workflow step {step.step_id}")
                return False

            # Get creative statuses for all creatives in this task
            creative_ids = [m.object_id for m in all_mappings]
            all_creatives = uow.creatives.admin_get_by_ids(creative_ids)

            # Check if ANY creative is still pending review
            pending_count = sum(1 for c in all_creatives if c.status == CreativeStatusEnum.pending_review.value)

            if pending_count > 0:
                logger.info(
                    f"Creative {creative_id} reviewed, but {pending_count}/{len(all_creatives)} "
                    f"creatives still pending in task {step.step_id}; not firing webhook yet"
                )
                return False

            # ALL creatives have been reviewed! Build complete result for webhook
            logger.info(f"All {len(all_creatives)} creatives in task {step.step_id} have been reviewed; firing webhook")

            # Build SyncCreativesResponse with all creative results

            creatives: list[SyncCreativeResult] = [
                SyncCreativeResult(
                    creative_id=c.creative_id,
                    platform_id="",  # we need to populate this. Currently not storing any internal id of our own per creative
                    action=CreativeAction.failed if c.status != "approved" else CreativeAction.created,
                    errors=[c.data.get("rejection_reason")] if c.data and c.data.get("rejection_reason") else [],
                )
                for c in all_creatives
            ]

            # Convert context dict to ContextObject if present
            context_data = step.request_data.get("context")
            context_obj: ContextObject | None = None
            if context_data and isinstance(context_data, dict):
                context_obj = ContextObject.model_construct(**context_data)

            complete_result = SyncCreativesSuccessResponse(creatives=creatives, dry_run=False, context=context_obj)

            # build push notification config from step request data
            # this is because we don't store push notification config in the database when creating the creative
            from uuid import uuid4

            cfg_dict = step.request_data.get("push_notification_config") or {}
            url = cfg_dict.get("url")
            if not url:
                logger.error(f"No push notification URL present for creative {creative_id}")
                return False

            authentication = cfg_dict.get("authentication") or {}
            schemes = authentication.get("schemes") or []
            auth_type = schemes[0] if isinstance(schemes, list) and schemes else None
            auth_token = authentication.get("credentials")

            # Derive principal/tenant from the step context if available
            context_obj = getattr(step, "context", None)
            derived_tenant_id = tenant_id or (getattr(context_obj, "tenant_id", None))
            derived_principal_id = getattr(context_obj, "principal_id", None)

            push_notification_config = DBPushNotificationConfig(
                id=cfg_dict.get("id") or f"pnc_{uuid4().hex[:16]}",
                tenant_id=derived_tenant_id,
                principal_id=derived_principal_id,
                url=url,
                authentication_type=auth_type,
                authentication_token=auth_token,
                is_active=True,
            )

            # Extract step attributes before UoW closes (avoid DetachedInstanceError)
            step_tool_name = step.tool_name
            step_step_id = step.step_id
            step_request_data = step.request_data
            step_context_id = step.context_id

        # --- Session closed here; webhook delivery is outside the transaction ---

        service = get_protocol_webhook_service()
        try:
            logger.info(f"tool name: {step_tool_name}")
            logger.info(f"task id: {step_step_id}")
            logger.info(f"task type: {step_tool_name}")
            logger.info("status: completed")
            logger.info(f"result: {complete_result}")
            logger.info("error: None")
            logger.info(f"push_notification_config: {push_notification_config}")

            # Determine protocol type from workflow step request_data
            protocol = step_request_data.get("protocol", "mcp")  # Default to MCP for backward compatibility

            # Create appropriate webhook payload based on protocol
            # Convert result to dict for webhook payload functions
            result_dict = complete_result.model_dump(mode="json")

            payload: Task | TaskStatusUpdateEvent | McpWebhookPayload
            if protocol == "a2a":
                payload = create_a2a_webhook_payload(
                    task_id=step_step_id,
                    status=GeneratedTaskStatus.completed,
                    result=result_dict,
                    context_id=step_context_id,
                )
            else:
                # TODO: Fix in adcp python client - create_mcp_webhook_payload should return
                # McpWebhookPayload instead of dict[str, Any] for proper type safety
                mcp_payload_dict = create_mcp_webhook_payload(step_step_id, GeneratedTaskStatus.completed, result_dict)
                payload = McpWebhookPayload.model_construct(**mcp_payload_dict)

            metadata = {
                "task_type": step_tool_name
                # TODO: @yusuf - check if we were passing principal_id and tenant to this previously
                # TODO: @yusuf - check if we want to make metadata typed
            }

            await service.send_notification(
                push_notification_config=push_notification_config, payload=payload, metadata=metadata
            )

            logger.info(
                f"Successfully sent protocol webhook for sync_creatives task {step_step_id} "
                f"with {len(all_creatives)} reviewed creatives"
            )

            return True
        except Exception as send_e:
            logger.error(f"Failed to send protocol webhook for creative {creative_id}: {send_e}")
            return False

    except Exception as e:
        logger.error(f"Error sending protocol webhook for creative {creative_id}: {e}", exc_info=True)
        return False


@creatives_bp.route("/", methods=["GET"])
@require_tenant_access()
def index(tenant_id, **kwargs):
    """Redirect to unified creative management page."""
    return redirect(url_for("creatives.review_creatives", tenant_id=tenant_id))


@creatives_bp.route("/review", methods=["GET"])
@require_tenant_access()
def review_creatives(tenant_id, **kwargs):
    """Unified creative management: view, review, and manage all creatives."""
    with AdminCreativeUoW(tenant_id) as uow:
        assert uow.creatives is not None
        assert uow.assignments is not None
        assert uow.media_buys is not None
        assert uow.products is not None
        assert uow.tenant_config is not None

        # Get tenant
        tenant = uow.tenant_config.get_tenant()
        if not tenant:
            return "Tenant not found", 404

        # Get all creatives ordered by status (pending first) then date
        creatives = uow.creatives.admin_list_all()

        # Build creative data with context
        creative_list = []
        for creative in creatives:
            # Get principal name
            principal_name = uow.creatives.get_principal_name(creative.principal_id)

            # Get all media buy assignments for this creative
            assignments = uow.assignments.get_by_creative(creative.creative_id)

            # Get media buy details for each assignment
            media_buys = []
            for assignment in assignments:
                media_buy = uow.media_buys.get_by_id(assignment.media_buy_id)
                if media_buy:
                    media_buys.append(
                        {
                            "media_buy_id": media_buy.media_buy_id,
                            "order_name": media_buy.order_name,
                            "package_id": assignment.package_id,
                            "status": media_buy.status,
                            "start_date": media_buy.start_date,
                            "end_date": media_buy.end_date,
                        }
                    )

            # Get promoted offering from first media buy (if any)
            promoted_offering = None
            if media_buys and media_buys[0]:
                first_buy = uow.media_buys.get_by_id(media_buys[0]["media_buy_id"])
                if first_buy and first_buy.raw_request:
                    packages = first_buy.raw_request.get("packages", [])
                    if packages:
                        product_id = packages[0].get("product_id")
                        if product_id:
                            product = uow.products.get_by_id(product_id)
                            if product:
                                promoted_offering = product.name

            creative_list.append(
                {
                    "creative_id": creative.creative_id,
                    "name": creative.name,
                    "format": creative.format,
                    "status": creative.status,
                    "principal_name": principal_name,
                    "principal_id": creative.principal_id,
                    "group_id": creative.group_id,
                    "data": creative.data,
                    "created_at": creative.created_at,
                    "approved_at": creative.approved_at,
                    "approved_by": creative.approved_by,
                    "media_buys": media_buys,
                    "assignment_count": len(media_buys),
                    "promoted_offering": promoted_offering,
                }
            )

        # Extract tenant attributes before UoW closes (avoid DetachedInstanceError)
        tenant_name = tenant.name
        has_ai_review = bool(tenant.gemini_api_key and tenant.creative_review_criteria)
        approval_mode = tenant.approval_mode

    return render_template(
        "creative_management.html",
        tenant_id=tenant_id,
        tenant_name=tenant_name,
        creatives=creative_list,
        has_ai_review=has_ai_review,
        approval_mode=approval_mode,
    )


@creatives_bp.route("/list", methods=["GET"])
@require_tenant_access()
def list_creatives(tenant_id, **kwargs):
    """Redirect to unified creative management page."""
    return redirect(url_for("creatives.review_creatives", tenant_id=tenant_id))


@creatives_bp.route("/add/ai", methods=["GET"])
@require_tenant_access()
def add_ai(tenant_id, **kwargs):
    """Show AI-assisted creative format discovery form."""
    return render_template("creative_format_ai.html", tenant_id=tenant_id)


@creatives_bp.route("/analyze", methods=["POST"])
@log_admin_action("analyze")
@require_tenant_access()
def analyze(tenant_id, **kwargs):
    """Analyze creative format with AI."""
    try:
        url = request.form.get("url", "").strip()
        if not url:
            return jsonify({"error": "URL is required"}), 400

        # Use the creative format parser
        result = parse_creative_spec(url)

        if result.get("error"):
            return jsonify({"error": result["error"]}), 400

        return jsonify(result)

    except Exception as e:
        logger.error(f"Error analyzing creative format: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


def _create_human_review_record(
    creative_repo: CreativeRepository,
    *,
    creative_id: str,
    tenant_id: str,
    principal_id: str,
    reviewer_email: str,
    reason: str,
    is_override: bool,
    final_decision: str,
):
    """Create and add a human CreativeReview record via the repository."""
    from src.core.database.models import CreativeReview

    review_id = f"review_{uuid.uuid4().hex[:12]}"
    human_review = CreativeReview(
        review_id=review_id,
        creative_id=creative_id,
        tenant_id=tenant_id,
        principal_id=principal_id,
        reviewed_at=datetime.now(UTC),
        review_type="human",
        reviewer_email=reviewer_email,
        ai_decision=None,
        confidence_score=None,
        policy_triggered=None,
        reason=reason,
        recommendations=None,
        human_override=is_override,
        final_decision=final_decision,
    )
    creative_repo.create_review(human_review)
    return human_review


def _send_post_commit_side_effects(
    *,
    webhook_data: dict[str, Any],
    slack_data: dict[str, Any],
    audit_data: dict[str, Any],
    operation: str,
    tenant_id: str,
    actor: str,
):
    """Execute post-commit side effects: webhook, Slack notification, audit log.

    All calls are best-effort — failures are logged but do not propagate.

    Args:
        webhook_data: Dict with creative_id/tenant_id for webhook call.
        slack_data: Dict with slack_webhook_url and message.
        audit_data: Dict with details for audit logging.
        operation: Audit operation name (e.g. "approve_creative").
        tenant_id: Tenant scope for audit logger.
        actor: The user who performed the action (for audit principal_name/id).
    """
    from src.core.audit_logger import AuditLogger

    # Send webhook
    if webhook_data:
        asyncio.run(
            _call_webhook_for_creative_status(
                creative_id=webhook_data["creative_id"],
                tenant_id=webhook_data["tenant_id"],
            )
        )

    # Send Slack notification
    if slack_data:
        try:
            from src.services.slack_notifier import get_slack_notifier

            tenant_config = {"features": {"slack_webhook_url": slack_data["slack_webhook_url"]}}
            notifier = get_slack_notifier(tenant_config)
            notifier.send_message(slack_data["message"])
        except Exception as slack_e:
            logger.warning(f"Failed to send Slack notification: {slack_e}")

    # Log audit trail
    if audit_data:
        audit_logger = AuditLogger(adapter_name="AdminUI", tenant_id=tenant_id)
        audit_logger.log_operation(
            operation=operation,
            principal_name=actor,
            principal_id=actor,
            adapter_id="admin_ui",
            success=True,
            details=audit_data,
            tenant_id=tenant_id,
        )


@creatives_bp.route("/review/<creative_id>/approve", methods=["POST"])
@log_admin_action("approve_creative")
@require_tenant_access()
def approve_creative(tenant_id, creative_id, **kwargs):
    """Approve a creative."""
    try:
        data = request.get_json() or {}
        approved_by = data.get("approved_by", "admin")

        # Collect data needed for post-commit side effects
        webhook_data: dict[str, Any] = {}
        slack_data: dict[str, Any] = {}
        audit_data: dict[str, Any] = {}
        media_buy_actions: list[dict[str, Any]] = []

        with AdminCreativeUoW(tenant_id) as uow:
            assert uow.creatives is not None
            assert uow.assignments is not None
            assert uow.media_buys is not None
            assert uow.tenant_config is not None

            creative = uow.creatives.admin_get_by_id(creative_id)

            if not creative:
                return jsonify({"error": "Creative not found"}), 404

            # Check if there was a prior AI review that disagreed
            prior_ai_review = uow.creatives.get_prior_ai_review(creative_id)

            # Check if this is a human override (AI recommended reject, human approved)
            is_override = bool(prior_ai_review and prior_ai_review.ai_decision in ["rejected", "reject"])

            _create_human_review_record(
                uow.creatives,
                creative_id=creative_id,
                tenant_id=tenant_id,
                principal_id=creative.principal_id,
                reviewer_email=approved_by,
                reason="Human approval",
                is_override=is_override,
                final_decision="approved",
            )

            # Update creative status
            creative.status = "approved"
            creative.approved_at = datetime.now(UTC)
            creative.approved_by = approved_by

            # Collect webhook data for post-commit
            webhook_data = {"creative_id": creative_id, "tenant_id": tenant_id}

            # Collect Slack data for post-commit
            tenant = uow.tenant_config.get_tenant()
            if tenant and tenant.slack_webhook_url:
                principal_name = uow.creatives.get_principal_name(creative.principal_id)

                slack_data = {
                    "slack_webhook_url": tenant.slack_webhook_url,
                    "message": f"\u2705 Creative approved: {creative.name} ({creative.format}) from {principal_name}",
                }

            # Collect audit data for post-commit
            audit_data = {
                "creative_id": creative_id,
                "creative_name": creative.name,
                "format": creative.format,
                "principal_id": creative.principal_id,
                "human_override": is_override,
            }

            # Check if this creative approval unblocks any media buys
            assignments = uow.assignments.get_by_creative(creative_id)

            logger.info(
                f"[CREATIVE APPROVAL] Creative {creative_id} approved, checking {len(assignments)} media buy assignments"
            )

            for assignment in assignments:
                media_buy_id = assignment.media_buy_id
                media_buy = uow.media_buys.get_by_id(media_buy_id)

                if not media_buy:
                    continue

                logger.info(f"[CREATIVE APPROVAL] Media buy {media_buy_id} status: {media_buy.status}")

                if media_buy.status in {"pending_creatives", "draft"}:
                    # Get all creative assignments for this media buy
                    all_assignments = uow.assignments.get_by_media_buy(media_buy_id)

                    creative_ids = [a.creative_id for a in all_assignments]
                    all_creatives = uow.creatives.admin_get_by_ids(creative_ids)

                    unapproved_creatives = [
                        c.creative_id for c in all_creatives if c.status not in ["approved", "active"]
                    ]

                    logger.info(
                        f"[CREATIVE APPROVAL] Media buy {media_buy_id} has {len(unapproved_creatives)} unapproved creatives remaining"
                    )

                    if not unapproved_creatives:
                        media_buy_actions.append({"media_buy_id": media_buy_id, "media_buy": media_buy})
                    else:
                        logger.info(
                            f"[CREATIVE APPROVAL] Media buy {media_buy_id} still waiting for {len(unapproved_creatives)} creatives: {unapproved_creatives}"
                        )

            # UoW auto-commits here

        # --- Post-commit side effects (outside transaction) ---
        _send_post_commit_side_effects(
            webhook_data=webhook_data,
            slack_data=slack_data,
            audit_data=audit_data,
            operation="approve_creative",
            tenant_id=tenant_id,
            actor=approved_by,
        )

        # Execute adapter creation for unblocked media buys
        for action in media_buy_actions:
            logger.info(
                f"[CREATIVE APPROVAL] All creatives approved for media buy {action['media_buy_id']}, executing adapter creation"
            )

            from src.core.tools.media_buy_create import execute_approved_media_buy

            success, error_msg = execute_approved_media_buy(action["media_buy_id"], tenant_id)

            if success:
                # Update media buy status in a separate UoW
                with AdminCreativeUoW(tenant_id) as uow2:
                    assert uow2.media_buys is not None
                    mb = uow2.media_buys.get_by_id(action["media_buy_id"])
                    if mb:
                        new_status = _compute_media_buy_status_from_flight_dates(mb)
                        mb.status = new_status
                        mb.approved_at = datetime.now(UTC)
                        mb.approved_by = "system"
                    # auto-commits

                logger.info(f"[CREATIVE APPROVAL] Media buy {action['media_buy_id']} successfully created in adapter")
            else:
                logger.error(f"[CREATIVE APPROVAL] Adapter creation failed for {action['media_buy_id']}: {error_msg}")

        return jsonify({"success": True, "status": "approved"})

    except Exception as e:
        logger.error(f"Error approving creative: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@creatives_bp.route("/review/<creative_id>/reject", methods=["POST"])
@log_admin_action("reject_creative")
@require_tenant_access()
def reject_creative(tenant_id, creative_id, **kwargs):
    """Reject a creative with comments."""
    try:
        data = request.get_json() or {}
        rejected_by = data.get("rejected_by", "admin")
        rejection_reason = data.get("rejection_reason", "")

        if not rejection_reason:
            return jsonify({"error": "Rejection reason is required"}), 400

        # Collect data for post-commit side effects
        webhook_data: dict[str, Any] = {}
        slack_data: dict[str, Any] = {}
        audit_data: dict[str, Any] = {}

        with AdminCreativeUoW(tenant_id) as uow:
            assert uow.creatives is not None
            assert uow.tenant_config is not None

            creative = uow.creatives.admin_get_by_id(creative_id)

            if not creative:
                return jsonify({"error": "Creative not found"}), 404

            # Check if there was a prior AI review that disagreed
            prior_ai_review = uow.creatives.get_prior_ai_review(creative_id)

            # Check if this is a human override (AI recommended approve, human rejected)
            is_override = bool(prior_ai_review and prior_ai_review.ai_decision in ["approved", "approve"])

            _create_human_review_record(
                uow.creatives,
                creative_id=creative_id,
                tenant_id=tenant_id,
                principal_id=creative.principal_id,
                reviewer_email=rejected_by,
                reason=rejection_reason,
                is_override=is_override,
                final_decision="rejected",
            )

            # Update creative status
            creative.status = "rejected"
            creative.approved_at = datetime.now(UTC)
            creative.approved_by = rejected_by

            # Store rejection reason in data field
            if not creative.data:
                creative.data = {}
            creative.data["rejection_reason"] = rejection_reason
            creative.data["rejected_at"] = datetime.now(UTC).isoformat()

            # Flag JSONB field as modified
            uow.creatives.update_data(creative, creative.data)

            # Collect webhook data for post-commit
            webhook_data = {"creative_id": creative_id, "tenant_id": tenant_id}

            # Collect Slack data for post-commit
            tenant = uow.tenant_config.get_tenant()
            if tenant and tenant.slack_webhook_url:
                principal_name = uow.creatives.get_principal_name(creative.principal_id)

                slack_data = {
                    "slack_webhook_url": tenant.slack_webhook_url,
                    "message": f"\u274c Creative rejected: {creative.name} ({creative.format}) from {principal_name}\nReason: {rejection_reason}",
                }

            # Collect audit data for post-commit
            audit_data = {
                "creative_id": creative_id,
                "creative_name": creative.name,
                "format": creative.format,
                "principal_id": creative.principal_id,
                "rejection_reason": rejection_reason,
                "human_override": is_override,
            }

            # UoW auto-commits here

        # --- Post-commit side effects (outside transaction) ---
        _send_post_commit_side_effects(
            webhook_data=webhook_data,
            slack_data=slack_data,
            audit_data=audit_data,
            operation="reject_creative",
            tenant_id=tenant_id,
            actor=rejected_by,
        )

        return jsonify({"success": True, "status": "rejected"})

    except Exception as e:
        logger.error(f"Error rejecting creative: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


async def _ai_review_creative_async(
    creative_id: str,
    tenant_id: str,
    webhook_url: str | None = None,
    slack_webhook_url: str | None = None,
    principal_name: str | None = None,
):
    """Background task to review creative with AI (thread-safe).

    This function runs in a background thread and:
    1. Creates its own database session via UoW (thread-safe)
    2. Calls _ai_review_creative_impl() for the actual review
    3. Updates creative status in database
    4. Sends Slack notification if configured
    5. Calls webhook if configured

    Args:
        creative_id: Creative to review
        tenant_id: Tenant ID
        webhook_url: Optional webhook to call on completion
        slack_webhook_url: Optional Slack webhook for notifications
        principal_name: Principal name for Slack notification
    """
    logger.info(f"[AI Review Async] Starting background review for creative {creative_id}")

    # Collect data for post-commit side effects
    slack_notification_data: dict[str, Any] = {}
    should_call_webhook = False
    creative_format_str = ""

    try:
        with AdminCreativeUoW(tenant_id) as uow:
            assert uow.creatives is not None

            # Run AI review
            ai_result = _ai_review_creative_impl(
                tenant_id=tenant_id, creative_id=creative_id, db_session=uow.session, promoted_offering=None
            )

            logger.info(f"[AI Review Async] Review completed for {creative_id}: {ai_result['status']}")

            # Update creative status in database
            creative = uow.creatives.admin_get_by_id(creative_id)

            if creative:
                creative.status = ai_result["status"]

                # Store AI reasoning in creative data
                if not isinstance(creative.data, dict):
                    creative.data = {}
                creative.data["ai_review"] = {
                    "decision": ai_result["status"],
                    "reason": ai_result.get("reason", ""),
                    "ai_reason": ai_result.get("ai_reason"),
                    "ai_recommendation": ai_result.get("ai_recommendation"),
                    "confidence": ai_result.get("confidence", "medium"),
                    "reviewed_at": datetime.now(UTC).isoformat(),
                }

                uow.creatives.update_data(creative, creative.data)
                creative_format_str = str(creative.format)

                # Collect Slack notification data for post-commit
                if slack_webhook_url and principal_name:
                    ai_review_data = creative.data.get("ai_review", {})
                    ai_review_reason = ai_review_data.get("reason", "")

                    if ai_review_data.get("ai_reason"):
                        ai_review_reason = f"{ai_review_reason}\n\n*AI's Reasoning:* {ai_review_data.get('ai_reason')}"

                    if ai_review_data.get("ai_recommendation"):
                        ai_recommendation = ai_review_data.get("ai_recommendation", "").title()
                        ai_review_reason = f"{ai_review_reason}\n\n*AI Recommendation:* {ai_recommendation}"

                    slack_notification_data = {
                        "slack_webhook_url": slack_webhook_url,
                        "principal_name": principal_name,
                        "ai_review_reason": ai_review_reason,
                    }

                should_call_webhook = bool(webhook_url)
            else:
                logger.error(f"[AI Review Async] Creative not found: {creative_id}")

            # UoW auto-commits here

        logger.info(f"[AI Review Async] Database updated for {creative_id}: status={ai_result['status']}")

        # --- Post-commit side effects ---

        if slack_notification_data:
            try:
                from src.services.slack_notifier import get_slack_notifier

                tenant_config = {"features": {"slack_webhook_url": slack_notification_data["slack_webhook_url"]}}
                notifier = get_slack_notifier(tenant_config)
                notifier.notify_creative_pending(
                    creative_id=creative_id,
                    principal_name=slack_notification_data["principal_name"],
                    format_type=creative_format_str,
                    media_buy_id=None,
                    tenant_id=tenant_id,
                    ai_review_reason=slack_notification_data["ai_review_reason"],
                )
                logger.info(f"[AI Review Async] Slack notification sent for {creative_id}")
            except Exception as slack_e:
                logger.warning(f"[AI Review Async] Failed to send Slack notification: {slack_e}")

        if should_call_webhook:
            asyncio.run(_call_webhook_for_creative_status(creative_id=creative_id, tenant_id=tenant_id))
            logger.info(f"[AI Review Async] Webhook called for {creative_id}")

    except Exception as e:
        logger.error(f"[AI Review Async] Error reviewing creative {creative_id}: {e}", exc_info=True)

        # Try to mark creative as pending with error (separate UoW)
        try:
            with AdminCreativeUoW(tenant_id) as uow:
                assert uow.creatives is not None

                creative = uow.creatives.admin_get_by_id(creative_id)

                if creative:
                    creative.status = "pending_review"
                    if not isinstance(creative.data, dict):
                        creative.data = {}
                    creative.data["ai_review_error"] = {
                        "error": str(e),
                        "timestamp": datetime.now(UTC).isoformat(),
                    }
                    uow.creatives.update_data(creative, creative.data)
                    # UoW auto-commits
                    logger.info(f"[AI Review Async] Creative {creative_id} marked as pending_review due to error")
        except Exception as inner_e:
            logger.error(f"[AI Review Async] Failed to mark creative as pending: {inner_e}")


def get_ai_review_status(task_id: str) -> dict:
    """Get status of an AI review background task.

    Args:
        task_id: Task identifier

    Returns:
        Dict with keys: status (running|completed|failed), result (if completed), error (if failed)
    """
    _cleanup_completed_tasks()

    with _ai_review_lock:
        if task_id not in _ai_review_tasks:
            return {"status": "not_found", "error": "Task ID not found"}

        task_info = _ai_review_tasks[task_id]
        future = task_info["future"]

        if not future.done():
            return {"status": "running", "creative_id": task_info["creative_id"]}

        # Task is done - get result or exception
        try:
            result = future.result()
            return {"status": "completed", "result": result, "creative_id": task_info["creative_id"]}
        except Exception as e:
            return {"status": "failed", "error": str(e), "creative_id": task_info["creative_id"]}


def _create_review_record(
    creative_repo: "CreativeRepository",
    creative_id: str,
    tenant_id: str,
    ai_result: dict,
    principal_id: str | None = None,
):
    """Create a CreativeReview record from AI review result.

    Args:
        creative_repo: CreativeRepository instance (handles DB access)
        creative_id: Creative ID
        tenant_id: Tenant ID
        ai_result: Result dict from AI review with keys:
            - status: "approved", "pending", or "rejected"
            - reason: Explanation from AI
            - confidence: "high", "medium", or "low"
            - confidence_score: Float 0.0-1.0
            - policy_triggered: Policy that was triggered
            - ai_recommendation: Optional AI recommendation if different from final
        principal_id: Principal ID (required for composite FK to creatives)
    """
    from src.core.database.models import CreativeReview

    try:
        review_id = f"review_{uuid.uuid4().hex[:12]}"

        review_record = CreativeReview(
            review_id=review_id,
            creative_id=creative_id,
            tenant_id=tenant_id,
            principal_id=principal_id,
            reviewed_at=datetime.now(UTC),
            review_type="ai",
            reviewer_email=None,
            ai_decision=ai_result.get("ai_recommendation") or ai_result["status"],
            confidence_score=ai_result.get("confidence_score"),
            policy_triggered=ai_result.get("policy_triggered"),
            reason=ai_result.get("reason"),
            recommendations=None,
            human_override=False,
            final_decision=ai_result["status"],
        )

        creative_repo.create_review(review_record)

        logger.debug(f"Created review record {review_id} for creative {creative_id}")

    except Exception as e:
        logger.error(f"Error creating review record for creative {creative_id}: {e}", exc_info=True)
        # Don't fail the review if we can't create the record — let UoW handle rollback


def _ai_review_creative_impl(tenant_id, creative_id, db_session=None, promoted_offering=None):
    """Internal implementation: Run AI review and return dict result.

    When db_session is provided (e.g. from a caller's UoW), uses that session.
    When db_session is None (e.g. from Flask endpoint), creates its own UoW.

    Returns dict with keys:
    - status: "approved", "pending", or "rejected"
    - reason: explanation from AI
    - confidence: "high", "medium", or "low"
    - error: error message if failed
    """
    import time

    from src.core.metrics import (
        active_ai_reviews,
        ai_review_duration,
        ai_review_errors,
    )

    start_time = time.time()
    active_ai_reviews.labels(tenant_id=tenant_id).inc()

    try:
        return _ai_review_creative_impl_inner(
            tenant_id=tenant_id,
            creative_id=creative_id,
            db_session=db_session,
            promoted_offering=promoted_offering,
        )
    except Exception as e:
        logger.error(f"Error running AI review: {e}", exc_info=True)
        # Record error metrics
        ai_review_errors.labels(tenant_id=tenant_id, error_type=type(e).__name__).inc()
        return {"status": "pending_review", "error": str(e), "reason": "AI review failed - requires manual approval"}
    finally:
        # Record duration and decrement active reviews
        duration = time.time() - start_time
        ai_review_duration.labels(tenant_id=tenant_id).observe(duration)
        active_ai_reviews.labels(tenant_id=tenant_id).dec()


def _ai_review_creative_impl_inner(
    tenant_id,
    creative_id,
    db_session,
    promoted_offering,
):
    """Core AI review logic. Extracted to avoid deep nesting from UoW context manager.

    When db_session is provided, uses it directly (caller owns lifecycle).
    When db_session is None, creates an AdminCreativeUoW and uses its session.
    """
    from src.core.database.repositories.creative import CreativeRepository
    from src.core.database.repositories.media_buy import MediaBuyRepository
    from src.core.database.repositories.product import ProductRepository
    from src.core.database.repositories.tenant_config import TenantConfigRepository
    from src.core.metrics import ai_review_confidence, ai_review_total
    from src.services.ai import AIServiceFactory
    from src.services.ai.agents.review_agent import (
        create_review_agent,
        parse_confidence_score,
        review_creative_async,
    )

    cm = AdminCreativeUoW(tenant_id) if db_session is None else contextlib.nullcontext()
    with cm as uow:
        if uow is not None:
            # Use repos from UoW — don't create duplicates
            assert uow.session is not None
            db_session = uow.session
            tenant_config_repo = uow.tenant_config
            creative_repo = uow.creatives
            mb_repo = uow.media_buys
            product_repo = uow.products
        else:
            # Caller owns session — create repos manually
            tenant_config_repo = TenantConfigRepository(db_session, tenant_id)
            creative_repo = CreativeRepository(db_session, tenant_id)
            mb_repo = MediaBuyRepository(db_session, tenant_id)
            product_repo = ProductRepository(db_session, tenant_id)

        tenant = tenant_config_repo.get_tenant()
        if not tenant:
            return {"status": "pending_review", "error": "Tenant not found", "reason": "Configuration error"}

        # Check AI availability - use factory to check tenant + platform config
        factory = AIServiceFactory()

        # Build effective config from tenant settings
        tenant_ai_config = tenant.ai_config if hasattr(tenant, "ai_config") else None

        # Backward compatibility: use gemini_api_key if no ai_config
        if not tenant_ai_config and tenant.gemini_api_key:
            tenant_ai_config = {
                "provider": "gemini",
                "api_key": tenant.gemini_api_key,
            }

        if not factory.is_ai_enabled(tenant_ai_config):
            return {
                "status": "pending_review",
                "error": "AI not configured",
                "reason": "AI review unavailable - requires manual approval",
            }

        if not tenant.creative_review_criteria:
            return {
                "status": "pending_review",
                "error": "Creative review criteria not configured",
                "reason": "AI review unavailable - requires manual approval",
            }

        creative = creative_repo.admin_get_by_id(creative_id)

        if not creative:
            return {"status": "pending_review", "error": "Creative not found", "reason": "Configuration error"}

        # Get media buy and promoted offering if not provided
        if promoted_offering is None:
            promoted_offering = "Unknown"
            if creative.data.get("media_buy_id"):
                media_buy = mb_repo.get_by_id(creative.data["media_buy_id"])
                if media_buy and media_buy.raw_request:
                    packages = media_buy.raw_request.get("packages", [])
                    if packages:
                        product_id = packages[0].get("product_id")
                        if product_id:
                            product = product_repo.get_by_id(product_id)
                            if product:
                                promoted_offering = product.name

        # Create Pydantic AI agent and run review
        model_string = factory.create_model(tenant_ai_config)
        agent = create_review_agent(model_string)

        # Run async agent in a separate thread to avoid event loop conflicts with Flask
        def run_review_in_thread():
            """Run async review code in a new thread with its own event loop."""
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                return loop.run_until_complete(
                    review_creative_async(
                        agent=agent,
                        review_criteria=tenant.creative_review_criteria,
                        creative_name=creative.name,
                        creative_format=creative.format,
                        promoted_offering=promoted_offering,
                        creative_data=creative.data,
                    )
                )
            finally:
                loop.close()

        with ThreadPoolExecutor() as executor:
            future = executor.submit(run_review_in_thread)
            review_result = future.result(timeout=60)

        # Extract results from structured output
        decision = review_result.decision
        confidence_str = review_result.confidence
        confidence_score = parse_confidence_score(confidence_str)

        # Get AI policy from tenant (with defaults)
        ai_policy_data = tenant.ai_policy if tenant.ai_policy else {}
        # Thresholds represent MINIMUM confidence required for automatic action
        auto_approve_threshold = ai_policy_data.get("auto_approve_threshold", 0.90)  # Need 90%+ to auto-approve
        auto_reject_threshold = ai_policy_data.get("auto_reject_threshold", 0.90)  # Need 90%+ to auto-reject
        sensitive_categories = ai_policy_data.get("always_require_human_for", ["political", "healthcare", "financial"])

        # Check if creative is in sensitive category (extract from data or infer from tags)
        creative_category = None
        if creative.data:
            creative_category = creative.data.get("category")
            # Also check tags if available
            if not creative_category and "tags" in creative.data:
                for tag in creative.data.get("tags", []):
                    if tag.lower() in [cat.lower() for cat in sensitive_categories]:
                        creative_category = tag.lower()
                        break

        # Check if this creative requires human review by category
        if creative_category and creative_category.lower() in [cat.lower() for cat in sensitive_categories]:
            result_dict = {
                "status": "pending_review",
                "reason": f"Category '{creative_category}' requires human review per policy",
                "confidence": confidence_str,
                "confidence_score": confidence_score,
                "policy_triggered": "sensitive_category",
            }
            _create_review_record(
                creative_repo,
                creative_id,
                tenant_id,
                result_dict,
                principal_id=creative.principal_id,
            )
            # Record metrics
            ai_review_total.labels(
                tenant_id=tenant_id, decision="pending_review", policy_triggered="sensitive_category"
            ).inc()
            ai_review_confidence.labels(tenant_id=tenant_id, decision="pending_review").observe(confidence_score)
            return result_dict

        # Apply confidence-based thresholds
        # decision is already extracted from review_result.decision above

        if "APPROVE" in decision and "REQUIRE" not in decision:
            # AI wants to approve - check confidence threshold
            if confidence_score >= auto_approve_threshold:
                result_dict = {
                    "status": "approved",
                    "reason": review_result.reason,
                    "confidence": confidence_str,
                    "confidence_score": confidence_score,
                    "policy_triggered": "auto_approve",
                }
                _create_review_record(
                    db_session,
                    creative_id,
                    tenant_id,
                    result_dict,
                    principal_id=creative.principal_id,
                )
                # Record metrics
                ai_review_total.labels(tenant_id=tenant_id, decision="approved", policy_triggered="auto_approve").inc()
                ai_review_confidence.labels(tenant_id=tenant_id, decision="approved").observe(confidence_score)
                return result_dict
            else:
                result_dict = {
                    "status": "pending_review",
                    "reason": f"AI recommended approval with {confidence_score:.0%} confidence (below {auto_approve_threshold:.0%} threshold). Human review recommended.",
                    "confidence": confidence_str,
                    "confidence_score": confidence_score,
                    "policy_triggered": "low_confidence_approval",
                    "ai_recommendation": "approve",
                    "ai_reason": review_result.reason,
                }
                _create_review_record(
                    db_session,
                    creative_id,
                    tenant_id,
                    result_dict,
                    principal_id=creative.principal_id,
                )
                # Record metrics
                ai_review_total.labels(
                    tenant_id=tenant_id, decision="pending_review", policy_triggered="low_confidence_approval"
                ).inc()
                ai_review_confidence.labels(tenant_id=tenant_id, decision="pending_review").observe(confidence_score)
                return result_dict

        elif "REJECT" in decision:
            # AI wants to reject - check confidence threshold
            if confidence_score >= auto_reject_threshold:
                result_dict = {
                    "status": "rejected",
                    "reason": review_result.reason,
                    "confidence": confidence_str,
                    "confidence_score": confidence_score,
                    "policy_triggered": "auto_reject",
                }
                _create_review_record(
                    db_session,
                    creative_id,
                    tenant_id,
                    result_dict,
                    principal_id=creative.principal_id,
                )
                # Record metrics
                ai_review_total.labels(tenant_id=tenant_id, decision="rejected", policy_triggered="auto_reject").inc()
                ai_review_confidence.labels(tenant_id=tenant_id, decision="rejected").observe(confidence_score)
                return result_dict
            else:
                result_dict = {
                    "status": "pending_review",
                    "reason": f"AI recommended rejection with {confidence_score:.0%} confidence (below {auto_reject_threshold:.0%} threshold). Human review recommended.",
                    "confidence": confidence_str,
                    "confidence_score": confidence_score,
                    "policy_triggered": "uncertain_rejection",
                    "ai_recommendation": "reject",
                    "ai_reason": review_result.reason,
                }
                _create_review_record(
                    db_session,
                    creative_id,
                    tenant_id,
                    result_dict,
                    principal_id=creative.principal_id,
                )
                # Record metrics
                ai_review_total.labels(
                    tenant_id=tenant_id, decision="pending_review", policy_triggered="uncertain_rejection"
                ).inc()
                ai_review_confidence.labels(tenant_id=tenant_id, decision="pending_review").observe(confidence_score)
                return result_dict

        # Default: uncertain or "REQUIRE HUMAN APPROVAL"
        result_dict = {
            "status": "pending_review",
            "reason": "AI could not make confident decision. Human review required.",
            "confidence": confidence_str,
            "confidence_score": confidence_score,
            "policy_triggered": "uncertain",
            "ai_reason": review_result.reason,
        }
        _create_review_record(
            creative_repo,
            creative_id,
            tenant_id,
            result_dict,
            principal_id=creative.principal_id,
        )
        # Record metrics
        ai_review_total.labels(tenant_id=tenant_id, decision="pending_review", policy_triggered="uncertain").inc()
        ai_review_confidence.labels(tenant_id=tenant_id, decision="pending_review").observe(confidence_score)
        return result_dict


@creatives_bp.route("/review/<creative_id>/ai-review", methods=["POST"])
@log_admin_action("ai_review_creative")
@require_tenant_access()
def ai_review_creative(tenant_id, creative_id, **kwargs):
    """Flask endpoint wrapper for AI review."""
    result = _ai_review_creative_impl(tenant_id, creative_id)

    if "error" in result:
        return jsonify({"success": False, "error": result["error"]}), 400

    return jsonify(
        {
            "success": True,
            "status": result["status"],
            "reason": result["reason"],
            "confidence": result.get("confidence", "medium"),
        }
    )
