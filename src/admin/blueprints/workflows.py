"""Workflow approval and review blueprint for Admin UI."""

import json
import logging
from datetime import UTC, datetime

from flask import Blueprint, flash, jsonify, redirect, render_template, request, session, url_for
from sqlalchemy import select
from sqlalchemy.orm import attributes

from src.admin.utils import require_tenant_access  # type: ignore[attr-defined]
from src.admin.utils.audit_decorator import log_admin_action
from src.core.database.database_session import get_db_session
from src.core.database.models import Context, WorkflowStep
from src.core.database.models import Principal as ModelPrincipal

logger = logging.getLogger(__name__)

workflows_bp = Blueprint("workflows", __name__)


@workflows_bp.route("/<tenant_id>/workflows")
@require_tenant_access()
def list_workflows(tenant_id, **kwargs):
    """List all workflows and pending approvals."""
    from src.core.database.models import AuditLog, MediaBuy, Tenant

    with get_db_session() as db:
        # Get tenant
        tenant = db.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
        if not tenant:
            return "Tenant not found", 404

        # Get all workflow steps (not just pending approval)
        stmt = (
            select(WorkflowStep)
            .join(Context, WorkflowStep.context_id == Context.context_id)
            .filter(Context.tenant_id == tenant_id)
            .order_by(WorkflowStep.created_at.desc())
        )
        all_steps = db.scalars(stmt).all()

        # Separate pending approval steps for summary
        pending_steps = [s for s in all_steps if s.status == "pending_approval"]

        # Get media buys for context
        stmt = select(MediaBuy).filter_by(tenant_id=tenant_id).order_by(MediaBuy.created_at.desc())
        media_buys = db.scalars(stmt).all()

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
                    "workflow_id": step.workflow_id,
                    "step_name": step.step_name,
                    "step_type": step.step_type,
                    "status": step.status,
                    "created_at": step.created_at,
                    "completed_at": step.completed_at,
                    "principal_name": principal.name if principal else "Unknown",
                    "assigned_to": step.assigned_to,
                    "error_message": step.error_message,
                    "request_data": step.request_data,
                }
            )

        # Get recent audit logs with enriched context
        # Get raw audit logs for the template (it expects AuditLog objects)
        stmt = select(AuditLog).filter(AuditLog.tenant_id == tenant_id).order_by(AuditLog.timestamp.desc()).limit(100)
        audit_logs = db.scalars(stmt).all()

        # Debug logging to understand why audit logs might be empty
        logger.info(f"[workflows] Querying audit logs for tenant_id={tenant_id}")
        logger.info(f"[workflows] Found {len(audit_logs)} audit logs")
        if audit_logs:
            logger.info(
                f"[workflows] Latest audit log: operation={audit_logs[0].operation}, success={audit_logs[0].success}, timestamp={audit_logs[0].timestamp}"
            )
        else:
            # Check if there are ANY audit logs in the database
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
            tasks=workflows_list,  # Using workflow_steps as tasks
            audit_logs=audit_logs,
        )


@workflows_bp.route("/<tenant_id>/workflows/<workflow_id>/steps/<step_id>/review")
@require_tenant_access()
def review_workflow_step(tenant_id, workflow_id, step_id):
    """Show detailed review page for a workflow step requiring approval."""
    with get_db_session() as db:
        # Get the workflow step with context
        stmt = (
            select(WorkflowStep)
            .join(Context, WorkflowStep.context_id == Context.context_id)
            .filter(WorkflowStep.step_id == step_id, Context.tenant_id == tenant_id)
        )
        step = db.scalars(stmt).first()

        if not step:
            flash("Workflow step not found", "error")
            return redirect(url_for("tenants.tenant_dashboard", tenant_id=tenant_id))

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


@workflows_bp.route("/<tenant_id>/workflows/<workflow_id>/steps/<step_id>/approve", methods=["POST"])
@require_tenant_access()
@log_admin_action("approve_workflow_step")
def approve_workflow_step(tenant_id, workflow_id, step_id):
    """Approve a workflow step."""
    try:
        with get_db_session() as db:
            # Get the workflow step
            stmt = (
                select(WorkflowStep)
                .join(Context, WorkflowStep.context_id == Context.context_id)
                .filter(WorkflowStep.step_id == step_id, Context.tenant_id == tenant_id)
            )
            step = db.scalars(stmt).first()

            if not step:
                return jsonify({"error": "Workflow step not found"}), 404

            # Update status
            step.status = "approved"
            step.updated_at = datetime.now(UTC)

            # Add approval comment with authenticated user
            user_info = session.get("user", {})
            user_email = user_info.get("email", "system") if isinstance(user_info, dict) else str(user_info)

            if not step.comments:
                step.comments = []
            step.comments.append(
                {"user": user_email, "timestamp": datetime.now(UTC).isoformat(), "comment": "Approved via admin UI"}
            )
            attributes.flag_modified(step, "comments")

            db.commit()

            # Check if this is a media buy creation workflow step
            # If so, execute the adapter creation (order/line items in GAM)
            from src.core.database.models import MediaBuy, ObjectWorkflowMapping

            stmt_mapping = select(ObjectWorkflowMapping).filter_by(step_id=step_id, object_type="media_buy")
            mapping = db.scalars(stmt_mapping).first()

            logger.info(f"[APPROVAL] Checking for ObjectWorkflowMapping: step_id={step_id}, found={mapping is not None}")
            if mapping:
                logger.info(f"[APPROVAL] Found mapping: object_type={mapping.object_type}, object_id={mapping.object_id}")

            if mapping:
                media_buy_id = mapping.object_id
                logger.info(f"[APPROVAL] Workflow step {step_id} approved for media buy {media_buy_id}")

                # Get the media buy
                stmt_buy = select(MediaBuy).filter_by(media_buy_id=media_buy_id, tenant_id=tenant_id)
                media_buy = db.scalars(stmt_buy).first()

                logger.info(f"[APPROVAL] Media buy lookup: found={media_buy is not None}, status={media_buy.status if media_buy else 'N/A'}")

                if media_buy and media_buy.status == "pending_approval":
                    # Check if all required creatives are approved before executing adapter creation
                    from src.core.database.models import Creative as CreativeModel
                    from src.core.database.models import CreativeAssignment

                    stmt_assignments = select(CreativeAssignment).filter_by(media_buy_id=media_buy_id)
                    assignments = db.scalars(stmt_assignments).all()

                    if assignments:
                        # Get all creative IDs for this media buy
                        creative_ids = [a.creative_id for a in assignments]
                        stmt_creatives = select(CreativeModel).filter(
                            CreativeModel.creative_id.in_(creative_ids)
                        )
                        creatives = db.scalars(stmt_creatives).all()

                        # Check if any required creatives are not approved
                        unapproved_creatives = [
                            c.creative_id for c in creatives
                            if c.status not in ["approved", "active"]
                        ]

                        if unapproved_creatives:
                            logger.warning(
                                f"[APPROVAL] Cannot execute adapter creation yet - "
                                f"{len(unapproved_creatives)} creatives not approved: {unapproved_creatives}"
                            )
                            flash(
                                f"Media buy approved! Waiting for {len(unapproved_creatives)} creative(s) to be approved before creating in GAM.",
                                "info"
                            )
                            # Update status to show it's waiting for creatives
                            media_buy.status = "pending_creatives"
                            db.commit()
                            return jsonify({"success": True}), 200

                    # Execute adapter creation
                    from src.core.tools.media_buy_create import execute_approved_media_buy

                    logger.info(f"[APPROVAL] Executing adapter creation for approved media buy {media_buy_id}")
                    success, error_msg = execute_approved_media_buy(media_buy_id, tenant_id)

                    if not success:
                        logger.error(f"[APPROVAL] Adapter creation failed for {media_buy_id}: {error_msg}")
                        flash(f"Workflow approved but media buy creation failed: {error_msg}", "error")
                        return jsonify({"success": False, "error": error_msg}), 500

                    # Update media buy status
                    media_buy.status = "scheduled"
                    media_buy.approved_at = datetime.now(UTC)
                    media_buy.approved_by = user_email
                    db.commit()

                    logger.info(f"[APPROVAL] Media buy {media_buy_id} successfully created in adapter")
                    flash("Workflow step approved and media buy created successfully", "success")
                else:
                    logger.warning(f"[APPROVAL] Media buy not executed: media_buy={media_buy is not None}, status={media_buy.status if media_buy else 'N/A'}")
                    flash("Workflow step approved successfully", "success")
            else:
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
            # Get the workflow step
            stmt = (
                select(WorkflowStep)
                .join(Context, WorkflowStep.context_id == Context.context_id)
                .filter(WorkflowStep.step_id == step_id, Context.tenant_id == tenant_id)
            )
            step = db.scalars(stmt).first()

            if not step:
                return jsonify({"error": "Workflow step not found"}), 404

            # Update status
            step.status = "rejected"
            step.updated_at = datetime.now(UTC)

            # Add rejection comment with authenticated user
            user_info = session.get("user", {})
            user_email = user_info.get("email", "system") if isinstance(user_info, dict) else str(user_info)

            if not step.comments:
                step.comments = []
            step.comments.append(
                {"user": user_email, "timestamp": datetime.now(UTC).isoformat(), "comment": f"Rejected: {reason}"}
            )
            attributes.flag_modified(step, "comments")

            db.commit()

            flash("Workflow step rejected", "info")
            return jsonify({"success": True}), 200

    except Exception as e:
        logger.error(f"Error rejecting workflow step {step_id}: {e}", exc_info=True)
        return jsonify({"error": "Internal server error"}), 500
