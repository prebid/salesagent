"""Creative formats management blueprint for admin UI."""

import asyncio
import contextlib
import logging
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any

from adcp.types import (
    CreativeAction,
)
from adcp.webhooks import GeneratedTaskStatus

from src.core.database.models import (
    PushNotificationConfig as DBPushNotificationConfig,
)
from src.core.database.repositories.creative import CreativeRepository
from src.core.logging_utils import sanitize_log_value
from src.core.schemas.creative import SyncCreativeResult, SyncCreativesResponse

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

from src.admin.services.media_buy_completion import (
    FinalizeOutcome,
    emit_protocol_result_webhook_async,
    finalize_unblocked_media_buy,
)
from src.admin.utils import echo_context, require_tenant_access
from src.admin.utils.audit_decorator import log_admin_action
from src.core.database.repositories.uow import AdminCreativeUoW
from src.core.tools.media_buy_create import push_creative_to_existing_buy

# Note: CreativeFormat table was dropped in migration f2addf453200
# All format-related routes have been removed

logger = logging.getLogger(__name__)

# Buy statuses where the order is live in the ad server (retroactive creative push, #1038)
_LIVE_BUY_STATUSES: frozenset[str] = frozenset({"active", "scheduled", "paused"})

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


# The flight-window→status decision for creative-unblock finalization now lives in
# the admin service (media_buy_completion.finalize_unblocked_media_buy), computed
# UNDER THE ROW LOCK. See #1544.


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
                logger.debug(
                    "No workflow mapping found for creative %s; skipping webhook notification",
                    sanitize_log_value(creative_id),
                )
                return False

            step = uow.workflows.get_step_by_id(mapping.step_id)
            if not step or not step.request_data:
                logger.debug(
                    "Workflow step missing or has no request_data for creative %s; skipping webhook notification",
                    sanitize_log_value(creative_id),
                )
                return False

            # Get ALL creatives associated with this workflow step
            all_mappings = [m for m in uow.workflows.get_mappings_for_step(step.step_id) if m.object_type == "creative"]

            if not all_mappings:
                logger.debug("No creative mappings found for workflow step %s", sanitize_log_value(step.step_id))
                return False

            # Get creative statuses for all creatives in this task
            creative_ids = [m.object_id for m in all_mappings]
            all_creatives = uow.creatives.admin_get_by_ids(creative_ids)

            # Check if ANY creative is still pending review
            pending_count = sum(1 for c in all_creatives if c.status == CreativeStatusEnum.pending_review.value)

            if pending_count > 0:
                logger.info(
                    "Creative %s reviewed, but %d/%d creatives still pending in task %s; not firing webhook yet",
                    sanitize_log_value(creative_id),
                    pending_count,
                    len(all_creatives),
                    sanitize_log_value(step.step_id),
                )
                return False

            # ALL creatives have been reviewed! Build complete result for webhook
            logger.info(
                "All %d creatives in task %s have been reviewed; firing webhook",
                len(all_creatives),
                sanitize_log_value(step.step_id),
            )

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

            # Echo the buyer's request context (shared helper, also used by the
            # media-buy approve webhook in blueprints/operations.py).
            context_obj = echo_context(step.request_data)

            complete_result = SyncCreativesResponse(creatives=creatives, dry_run=False, context=context_obj)

            # build push notification config from step request data
            # this is because we don't store push notification config in the database when creating the creative
            from uuid import uuid4

            cfg_dict = step.request_data.get("push_notification_config") or {}
            url = cfg_dict.get("url")
            if not url:
                logger.error("No push notification URL present for creative %s", sanitize_log_value(creative_id))
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

        # Route through the shared protocol-webhook emitter — the same payload
        # construction (protocol detection, buyer-facing task-id correlation,
        # untrusted tool_name validation) the media-buy approval routes use.
        # This path already runs inside an event loop, so it awaits the async
        # core directly instead of the asyncio.run wrapper. #1544.
        step_data = {
            "step_id": step_step_id,
            "context_id": step_context_id,
            "tool_name": step_tool_name or "sync_creatives",
            "request_data": step_request_data or {},
        }
        sent = await emit_protocol_result_webhook_async(
            step_data,
            push_notification_config,
            complete_result,
            GeneratedTaskStatus.completed,
            metadata={"task_type": step_tool_name},
        )
        if sent:
            logger.info(
                "Successfully sent protocol webhook for sync_creatives task %s with %d reviewed creatives",
                sanitize_log_value(step_step_id),
                len(all_creatives),
            )
        return sent

    except Exception as e:
        logger.error(
            "Error sending protocol webhook for creative %s: %s",
            sanitize_log_value(creative_id),
            sanitize_log_value(e),
            exc_info=True,
        )
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
        # Same discipline as approve/reject_creative: detail in the sanitized
        # server log, generic message to the client.
        logger.error("Error analyzing creative format: %s", sanitize_log_value(e), exc_info=True)
        return jsonify({"error": "Creative format analysis failed — see server logs for details"}), 500


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
        push_warnings: list[str] = []

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
            # Snapshot IDs as plain strings — ORM objects expire on session close (#1038)
            assignment_buy_ids = [a.media_buy_id for a in assignments]

            logger.info(
                "[CREATIVE APPROVAL] Creative %s approved, checking %s media buy assignments",
                sanitize_log_value(creative_id),
                len(assignments),
            )

            # Snapshot buy statuses here to avoid a second UoW after commit
            assignment_buy_statuses: dict[str, str] = {}
            for assignment in assignments:
                media_buy_id = assignment.media_buy_id
                media_buy = uow.media_buys.get_by_id(media_buy_id)

                if not media_buy:
                    continue

                assignment_buy_statuses[media_buy_id] = media_buy.status
                logger.info(
                    "[CREATIVE APPROVAL] Media buy %s status: %s",
                    sanitize_log_value(media_buy_id),
                    sanitize_log_value(media_buy.status),
                )

                if media_buy.status in {"pending_creatives", "draft"}:
                    # Shared tenant-scoped readiness query (#1544) — same home as the
                    # admin approve gates; this buy has >= 1 assignment (the creative
                    # just approved), so ready_for_finalize == all assigned approved.
                    readiness = uow.assignments.creative_readiness(media_buy_id)

                    logger.info(
                        "[CREATIVE APPROVAL] Media buy %s has %s unapproved creatives remaining",
                        sanitize_log_value(media_buy_id),
                        len(readiness.unapproved_creative_ids),
                    )

                    if readiness.ready_for_finalize:
                        media_buy_actions.append({"media_buy_id": media_buy_id})
                    else:
                        logger.info(
                            "[CREATIVE APPROVAL] Media buy %s still waiting for %s creatives: %s",
                            sanitize_log_value(media_buy_id),
                            len(readiness.unapproved_creative_ids),
                            sanitize_log_value(readiness.unapproved_creative_ids),
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

        # Finalize each unblocked media buy through the shared seam: adapter →
        # flight-derived status COMPUTED UNDER THE ROW LOCK → workflow-step terminal +
        # response artifact → completion webhook. Previously this ran the adapter and
        # set the status but never terminalized the create step or emitted the
        # completion artifact, so async buyers who had been waiting on creative
        # approval never learned their buy went live. approved_at/approved_by are NOT
        # re-stamped here — confirmed_at was recorded at the earlier pending_creatives
        # hold (write-once); this system unblock is not a new approval instant. #1544.
        for action in media_buy_actions:
            media_buy_id = action["media_buy_id"]
            logger.info(
                "[CREATIVE APPROVAL] All creatives approved for media buy %s, finalizing",
                sanitize_log_value(media_buy_id),
            )
            # Session ownership + step lookup + finalize live in the admin service
            # (this blueprint is a scanned business-logic module that must route DB
            # access through repositories, not open get_db_session itself). #1544.
            outcome, error_msg = finalize_unblocked_media_buy(tenant_id, media_buy_id)
            if outcome is FinalizeOutcome.APPLIED:
                logger.info(
                    "[CREATIVE APPROVAL] Media buy %s successfully created in adapter",
                    sanitize_log_value(media_buy_id),
                )
            elif outcome is FinalizeOutcome.NOT_CLAIMED:
                logger.info(
                    "[CREATIVE APPROVAL] Media buy %s already finalized by another request",
                    sanitize_log_value(media_buy_id),
                )
            elif outcome is FinalizeOutcome.RETRYING:
                # #1637: claimed; the reconciler completes it automatically.
                logger.info(
                    "[CREATIVE APPROVAL] Media buy %s finalization deferred: %s",
                    sanitize_log_value(media_buy_id),
                    sanitize_log_value(error_msg),
                )
            else:
                logger.error(
                    "[CREATIVE APPROVAL] Adapter creation failed for %s: %s",
                    sanitize_log_value(media_buy_id),
                    sanitize_log_value(error_msg),
                )

        # Retroactive push for already-live buys (#1038):
        # Buys in pending_creatives/draft were handled above. For buys that are
        # live in the ad server, push this newly-approved creative to the line item.

        # Use the status snapshot from the first loop — no need for a second UoW
        buys_to_push = [
            buy_id for buy_id in assignment_buy_ids if assignment_buy_statuses.get(buy_id) in _LIVE_BUY_STATUSES
        ]

        for buy_id in buys_to_push:
            logger.info(
                "[CREATIVE APPROVAL] Retroactive push: creative %s → live buy %s",
                sanitize_log_value(creative_id),
                sanitize_log_value(buy_id),
            )
            push_success, push_err = push_creative_to_existing_buy(
                creative_id=creative_id,
                media_buy_id=buy_id,
                tenant_id=tenant_id,
            )
            if not push_success:
                # push_err is adapter-returned text — sanitize like every other
                # adapter value in this function (same convention as the finalize
                # arm above). #1544.
                logger.error(
                    "[CREATIVE APPROVAL] Retroactive push failed for creative %s → buy %s: %s",
                    sanitize_log_value(creative_id),
                    sanitize_log_value(buy_id),
                    sanitize_log_value(push_err),
                )
                push_warnings.append(f"Creative push to buy {buy_id} failed — see server logs for details")

        response_body: dict[str, Any] = {"success": True, "status": "approved"}
        if push_warnings:
            response_body["warnings"] = push_warnings
        return jsonify(response_body)

    except Exception as e:
        # Detail stays in the server log (sanitized, with traceback); the client
        # gets a generic message — same information-exposure discipline as the
        # admin approve routes. #1544.
        logger.error("Error approving creative: %s", sanitize_log_value(e), exc_info=True)
        return jsonify({"error": "Creative approval failed — see server logs for details"}), 500


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
        # Same discipline as approve_creative: detail in the sanitized server log,
        # generic message to the client. #1544.
        logger.error("Error rejecting creative: %s", sanitize_log_value(e), exc_info=True)
        return jsonify({"error": "Creative rejection failed — see server logs for details"}), 500


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
        record_ai_review_error,
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
        # Record error metrics (error_type bounded to a fixed enum)
        record_ai_review_error(tenant_id=tenant_id, error=e)
        return {"status": "pending_review", "error": str(e), "reason": "AI review failed - requires manual approval"}
    finally:
        # Record duration and decrement active reviews
        duration = time.time() - start_time
        ai_review_duration.observe(duration)
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
    from src.core.metrics import ai_review_confidence, record_ai_review
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
            record_ai_review(tenant_id=tenant_id, decision="pending_review", policy_triggered="sensitive_category")
            ai_review_confidence.labels(decision="pending_review").observe(confidence_score)
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
                record_ai_review(tenant_id=tenant_id, decision="approved", policy_triggered="auto_approve")
                ai_review_confidence.labels(decision="approved").observe(confidence_score)
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
                record_ai_review(
                    tenant_id=tenant_id, decision="pending_review", policy_triggered="low_confidence_approval"
                )
                ai_review_confidence.labels(decision="pending_review").observe(confidence_score)
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
                record_ai_review(tenant_id=tenant_id, decision="rejected", policy_triggered="auto_reject")
                ai_review_confidence.labels(decision="rejected").observe(confidence_score)
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
                record_ai_review(tenant_id=tenant_id, decision="pending_review", policy_triggered="uncertain_rejection")
                ai_review_confidence.labels(decision="pending_review").observe(confidence_score)
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
        record_ai_review(tenant_id=tenant_id, decision="pending_review", policy_triggered="uncertain")
        ai_review_confidence.labels(decision="pending_review").observe(confidence_score)
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
