"""Workflow approval and review blueprint for Admin UI."""

import json
import logging

from flask import Blueprint, flash, jsonify, redirect, render_template, request, session, url_for
from sqlalchemy import select

from src.admin.services.media_buy_completion import (
    MEDIA_BUY_ALREADY_DECIDED_MESSAGE,
    WORKFLOW_STEP_ALREADY_DECIDED_MESSAGE,
    FinalizeOutcome,
    claim_pending_creatives_hold,
    finalize_media_buy_rejection,
    finalize_pending_media_buy_approval,
)
from src.admin.utils import require_tenant_access
from src.admin.utils.audit_decorator import log_admin_action
from src.core.database.database_session import get_db_session
from src.core.database.models import (
    MEDIA_BUY_FINALIZING_STATUS,
    WORKFLOW_STEP_TERMINAL_STATUSES,
    Context,
    is_media_buy_approvable,
)
from src.core.database.models import Principal as ModelPrincipal
from src.core.database.repositories import MediaBuyRepository
from src.core.database.repositories.workflow import WorkflowRepository
from src.core.logging_utils import sanitize_log_value

logger = logging.getLogger(__name__)

workflows_bp = Blueprint("workflows", __name__)


@workflows_bp.route("/<tenant_id>/workflows")
@require_tenant_access()
def list_workflows(tenant_id, **kwargs):
    """List all workflows and pending approvals."""
    from src.core.database.models import AuditLog, Tenant

    with get_db_session() as db:
        # Get tenant
        tenant = db.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
        if not tenant:
            return "Tenant not found", 404

        # Get all workflow steps via repository (tenant-scoped)
        workflow_repo = WorkflowRepository(db, tenant_id)
        all_steps = workflow_repo.get_all_steps()

        # Separate pending approval steps for summary
        pending_steps = [s for s in all_steps if s.status == "pending_approval"]

        # Get media buys for context
        media_buy_repo = MediaBuyRepository(db, tenant_id)
        media_buys = media_buy_repo.list_all_ordered_by_created()

        # Build summary stats
        summary = {
            "active_buys": len([mb for mb in media_buys if mb.status == "active"]),
            "pending_tasks": len(pending_steps),
            "completed_today": 0,  # TODO: Calculate from workflow history
            "total_spend": sum(mb.budget or 0 for mb in media_buys if mb.status == "active"),
        }

        # Format all workflow steps for display in tasks tab
        workflows_list = []
        for step in all_steps:
            context = db.scalars(select(Context).filter_by(context_id=step.context_id)).first()
            principal = None
            if context and context.principal_id:
                principal = db.scalars(
                    select(ModelPrincipal).filter_by(principal_id=context.principal_id, tenant_id=tenant_id)
                ).first()

            workflows_list.append(
                {
                    "step_id": step.step_id,
                    "context_id": step.context_id,
                    "step_type": step.step_type,
                    "tool_name": step.tool_name,
                    "status": step.status,
                    "created_at": step.created_at,
                    "completed_at": step.completed_at,
                    "principal_name": principal.name if principal else "Unknown",
                    "assigned_to": step.assigned_to,
                    "error_message": step.error_message,
                    "request_data": step.request_data,
                }
            )

        # Get recent audit logs
        stmt = select(AuditLog).filter(AuditLog.tenant_id == tenant_id).order_by(AuditLog.timestamp.desc()).limit(100)
        audit_logs = db.scalars(stmt).all()

        logger.info(f"[workflows] Querying audit logs for tenant_id={tenant_id}")
        logger.info(f"[workflows] Found {len(audit_logs)} audit logs")
        if audit_logs:
            logger.info(
                f"[workflows] Latest audit log: operation={audit_logs[0].operation}, success={audit_logs[0].success}, timestamp={audit_logs[0].timestamp}"
            )
        else:
            all_logs_stmt = select(AuditLog).order_by(AuditLog.timestamp.desc()).limit(5)
            all_logs = db.scalars(all_logs_stmt).all()
            logger.warning(
                f"[workflows] No audit logs for tenant {tenant_id}, but found {len(all_logs)} logs total in database"
            )
            if all_logs:
                logger.warning(f"[workflows] Sample log tenant_ids: {[log.tenant_id for log in all_logs]}")

        return render_template(
            "workflows.html",
            tenant=tenant,
            tenant_id=tenant_id,
            summary=summary,
            workflows=workflows_list,
            media_buys=media_buys,
            tasks=workflows_list,
            audit_logs=audit_logs,
        )


@workflows_bp.route("/<tenant_id>/workflows/<workflow_id>/steps/<step_id>/review")
@require_tenant_access()
def review_workflow_step(tenant_id, workflow_id, step_id):
    """Show detailed review page for a workflow step requiring approval."""
    with get_db_session() as db:
        # Get the workflow step via repository (tenant-scoped)
        workflow_repo = WorkflowRepository(db, tenant_id)
        step = workflow_repo.get_step_by_id(step_id)

        if not step:
            flash("Workflow step not found", "error")
            return redirect(url_for("tenants.dashboard", tenant_id=tenant_id))

        # Get the context for tenant/principal info
        context = db.scalars(select(Context).filter_by(context_id=step.context_id)).first()

        # Get principal info
        principal = None
        if context and context.principal_id:
            principal = db.scalars(
                select(ModelPrincipal).filter_by(principal_id=context.principal_id, tenant_id=tenant_id)
            ).first()

        # Parse request data
        request_data = step.request_data if step.request_data else {}

        # Format the data for display
        formatted_request = json.dumps(request_data, indent=2)

        return render_template(
            "workflow_review.html",
            tenant_id=tenant_id,
            workflow_id=workflow_id,
            step=step,
            context=context,
            principal=principal,
            request_data=request_data,
            formatted_request=formatted_request,
        )


def _approval_in_progress_response():
    """202: the decision is claimed / in flight and completes automatically."""
    return jsonify(
        {
            "success": True,
            "pending": True,
            "message": "Approval in progress — completes automatically",
        }
    ), 202


def _hold_for_unapproved_creatives(db, tenant_id: str, media_buy_id: str, user_email: str):
    """Park the approved buy at ``pending_creatives`` while creatives are unapproved.

    Returns a ``(response, status)`` pair when the approve request is fully
    handled here (unapproved creatives — hold claimed, or lost to a concurrent
    decision), or ``None`` when every required creative is approved and the
    caller should finalize.
    """
    from src.core.database.models import Creative as CreativeModel
    from src.core.database.models import CreativeAssignment

    stmt_assignments = select(CreativeAssignment).filter_by(media_buy_id=media_buy_id)
    assignments = db.scalars(stmt_assignments).all()
    if not assignments:
        return None

    creative_ids = [a.creative_id for a in assignments]
    stmt_creatives = select(CreativeModel).filter(CreativeModel.creative_id.in_(creative_ids))
    creatives = db.scalars(stmt_creatives).all()
    unapproved_creatives = [c.creative_id for c in creatives if c.status not in ["approved", "active"]]
    if not unapproved_creatives:
        return None

    logger.warning(
        f"[APPROVAL] Cannot execute adapter creation yet - "
        f"{len(unapproved_creatives)} creatives not approved: {unapproved_creatives}"
    )
    # Shared single-winner CLAIM on pending_approval → pending_creatives, so a
    # concurrent approve/reject that already decided the buy is not overwritten.
    # The flash is queued ONLY after the claim is won — a lost claim must not
    # tell the operator the buy was approved. #1544.
    if not claim_pending_creatives_hold(db, tenant_id, media_buy_id=media_buy_id, approved_by=user_email):
        return jsonify({"success": False, "error": MEDIA_BUY_ALREADY_DECIDED_MESSAGE}), 409
    flash(
        f"Media buy approved! Waiting for {len(unapproved_creatives)} creative(s) to be approved before creating in GAM.",
        "info",
    )
    return jsonify({"success": True}), 200


def _finalize_and_render(db, tenant_id: str, *, media_buy_id: str, step_data: dict, user_email: str):
    """Finalize via the shared seam and translate the outcome to a route response.

    ``step_data`` is the pre-commit snapshot of the workflow step (it carries
    ``step_id``). Returns a ``(response, status)`` pair for the NOT_CLAIMED /
    ADAPTER_FAILED / RETRYING outcomes, or ``None`` on success (caller falls
    through to 200).
    """
    # Creatives ready → finalize atomically via the shared seam (same helper the
    # operations approve route uses): the approval instant + flight-derived
    # lifecycle status COMPUTED UNDER THE ROW LOCK, then the adapter, the
    # workflow-step terminal + response artifact, and the completion webhook. The
    # prior code stamped approved_at AFTER the adapter (so confirmed_at recorded
    # adapter-completion) and left the step at "approved" with no artifact — the
    # finalizer fixes both. See #1544.
    logger.info("[APPROVAL] Finalizing approved media buy %s", sanitize_log_value(media_buy_id))
    outcome, error_msg = finalize_pending_media_buy_approval(
        db,
        tenant_id,
        media_buy_id=media_buy_id,
        step_id=step_data["step_id"],
        step_data=step_data,
        approved_by=user_email,
    )

    if outcome is FinalizeOutcome.NOT_CLAIMED:
        logger.info(
            "[APPROVAL] Media buy %s already decided by another request",
            sanitize_log_value(media_buy_id),
        )
        return jsonify({"success": False, "error": MEDIA_BUY_ALREADY_DECIDED_MESSAGE}), 409
    if outcome is FinalizeOutcome.ADAPTER_FAILED:
        logger.error(f"[APPROVAL] Adapter creation failed for {media_buy_id}: {error_msg}")
        flash(f"Workflow approved but media buy creation failed: {error_msg}", "error")
        return jsonify({"success": False, "error": error_msg}), 500
    if outcome is FinalizeOutcome.RETRYING:
        # #1637: approval claimed; the ad-server order completes automatically
        # via the reconciler.
        logger.info(
            "[APPROVAL] Media buy %s finalization deferred: %s",
            sanitize_log_value(media_buy_id),
            sanitize_log_value(error_msg),
        )
        return _approval_in_progress_response()

    logger.info(f"[APPROVAL] Media buy {media_buy_id} successfully created in adapter")
    flash("Workflow step approved and media buy created successfully", "success")
    return None


@workflows_bp.route("/<tenant_id>/workflows/<workflow_id>/steps/<step_id>/approve", methods=["POST"])
@require_tenant_access()
@log_admin_action("approve_workflow_step")
def approve_workflow_step(tenant_id, workflow_id, step_id):
    """Approve a workflow step."""
    try:
        with get_db_session() as db:
            # Get and update the workflow step via repository (tenant-scoped)
            workflow_repo = WorkflowRepository(db, tenant_id)

            user_info = session.get("user", {})
            user_email = user_info.get("email", "system") if isinstance(user_info, dict) else str(user_info)

            # Read-only fetch — do NOT pre-write status="approved". The workflow step is
            # a decision record owned by the single-winner claim: for a media-buy step it
            # is terminalized to "completed" ONLY by the claim winner (finalizer). A
            # premature "approved" write would revert a step already decided by a
            # competing approve/reject and make durable tasks/get report WORKING. #1544.
            step = workflow_repo.get_by_step_id(step_id)
            if not step:
                return jsonify({"error": "Workflow step not found"}), 404

            # A DECIDED (terminal) step is immutable — a replayed approve after a prior
            # rejection/completion must not revert it. #1544.
            if step.status in WORKFLOW_STEP_TERMINAL_STATUSES:
                return jsonify({"success": False, "error": WORKFLOW_STEP_ALREADY_DECIDED_MESSAGE}), 409

            # Snapshot step fields to a dict before commit (the ORM instance may
            # expire after commit/nested sessions). Used for the completion webhook.
            step_data = {
                "step_id": step.step_id,
                "context_id": step.context_id,
                "tool_name": step.tool_name,
                "request_data": step.request_data or {},
            }

            # Check if this is a media buy creation workflow step
            mappings = workflow_repo.get_mappings_for_step(step_id)
            mapping = next((m for m in mappings if m.object_type == "media_buy"), None)

            logger.info(
                f"[APPROVAL] Checking for ObjectWorkflowMapping: step_id={step_id}, found={mapping is not None}"
            )
            if mapping:
                logger.info(
                    f"[APPROVAL] Found mapping: object_type={mapping.object_type}, object_id={mapping.object_id}"
                )

            if mapping:
                media_buy_id = mapping.object_id
                logger.info(f"[APPROVAL] Workflow step {step_id} approved for media buy {media_buy_id}")

                # Get the media buy
                media_buy_repo = MediaBuyRepository(db, tenant_id)
                media_buy = media_buy_repo.get_by_id(media_buy_id)

                logger.info(
                    f"[APPROVAL] Media buy lookup: found={media_buy is not None}, status={media_buy.status if media_buy else 'N/A'}"
                )

                if media_buy and is_media_buy_approvable(media_buy):
                    # Check if all required creatives are approved before executing
                    # adapter creation; unapproved creatives park the buy at
                    # pending_creatives (single-winner claim) instead of finalizing.
                    held = _hold_for_unapproved_creatives(db, tenant_id, media_buy_id, user_email)
                    if held is not None:
                        return held

                    rendered = _finalize_and_render(
                        db,
                        tenant_id,
                        media_buy_id=media_buy_id,
                        step_data=step_data,
                        user_email=user_email,
                    )
                    if rendered is not None:
                        return rendered
                elif media_buy is not None and media_buy.status == MEDIA_BUY_FINALIZING_STATUS:
                    # Plain in-flight ``finalizing`` (a live lease owner is completing the
                    # decision) — NOT approvable, NOT terminal. Do not claim success:
                    # report the in-progress state (same 202 vocabulary as RETRYING). #1544.
                    logger.info(
                        "[APPROVAL] Media buy %s finalization already in flight",
                        sanitize_log_value(media_buy_id),
                    )
                    return _approval_in_progress_response()
                else:
                    # The mapped buy is no longer pending_approval (already decided, or
                    # gone). Do NOT write the step — the step follows the buy decision and
                    # must not be reverted. Idempotent success. #1544.
                    logger.warning(
                        f"[APPROVAL] Media buy not executed: media_buy={media_buy is not None}, status={media_buy.status if media_buy else 'N/A'}"
                    )
                    flash("Workflow step approved successfully", "success")
            else:
                # Plain (non-media-buy) workflow step — no buy decision to own, so mark the
                # step approved directly.
                workflow_repo.update_status(step_id, status="approved")
                db.commit()
                flash("Workflow step approved successfully", "success")

            return jsonify({"success": True}), 200

    except Exception as e:
        logger.error(f"Error approving workflow step {step_id}: {e}", exc_info=True)
        return jsonify({"error": "Internal server error"}), 500


@workflows_bp.route("/<tenant_id>/workflows/<workflow_id>/steps/<step_id>/reject", methods=["POST"])
@require_tenant_access()
@log_admin_action("reject_workflow_step")
def reject_workflow_step(tenant_id, workflow_id, step_id):
    """Reject a workflow step with a reason."""
    try:
        data = request.get_json() or {}
        reason = data.get("reason", "No reason provided")

        with get_db_session() as db:
            # Get and update the workflow step via repository (tenant-scoped)
            workflow_repo = WorkflowRepository(db, tenant_id)

            step = workflow_repo.get_by_step_id(step_id)
            if not step:
                return jsonify({"error": "Workflow step not found"}), 404

            # A DECIDED (terminal) step is immutable — a replayed reject after a prior
            # completion/rejection must not overwrite it. #1544.
            if step.status in WORKFLOW_STEP_TERMINAL_STATUSES:
                return jsonify({"success": False, "error": WORKFLOW_STEP_ALREADY_DECIDED_MESSAGE}), 409

            # Snapshot step fields to a dict before commit (the rejection webhook
            # reads them post-commit, when the ORM instance may have expired).
            step_data = {
                "step_id": step.step_id,
                "context_id": step.context_id,
                "tool_name": step.tool_name,
                "request_data": step.request_data or {},
            }

            mappings = workflow_repo.get_mappings_for_step(step_id)
            mapping = next((m for m in mappings if m.object_type == "media_buy"), None)

            if mapping is not None:
                # The step OWNS a media-buy decision — the reject must go through the
                # single-winner claim, asserting the OBSERVED source status. If the buy
                # is no longer in a rejectable state (a concurrent approve won, or it was
                # already decided) the claim loses → 409 and the step is left untouched,
                # so we never pair an active/decided buy with a rejected task. #1544.
                media_buy = MediaBuyRepository(db, tenant_id).get_by_id(mapping.object_id)
                if media_buy is None or media_buy.status not in ("pending_approval", "pending_creatives"):
                    return jsonify({"success": False, "error": MEDIA_BUY_ALREADY_DECIDED_MESSAGE}), 409
                outcome = finalize_media_buy_rejection(
                    db,
                    tenant_id,
                    media_buy_id=mapping.object_id,
                    step_id=step_id,
                    step_data=step_data,
                    reason=reason,
                    expected_status=media_buy.status,
                )
                if outcome is FinalizeOutcome.NOT_CLAIMED:
                    return jsonify({"success": False, "error": MEDIA_BUY_ALREADY_DECIDED_MESSAGE}), 409
            else:
                # No mapped media buy — a plain workflow step; record the rejection only.
                workflow_repo.update_status(step_id, status="rejected", error_message=reason)
                db.commit()

            flash("Workflow step rejected", "info")
            return jsonify({"success": True}), 200

    except Exception as e:
        logger.error(f"Error rejecting workflow step {step_id}: {e}", exc_info=True)
        return jsonify({"error": "Internal server error"}), 500
