"""Operations management blueprint."""

import logging

from flask import Blueprint, jsonify
from sqlalchemy import select

from src.admin.utils import require_auth, require_tenant_access  # type: ignore[attr-defined]

logger = logging.getLogger(__name__)

# Create blueprint
operations_bp = Blueprint("operations", __name__)


# @operations_bp.route("/targeting", methods=["GET"])
# @require_tenant_access()
# def targeting(tenant_id, **kwargs):
#     """TODO: Extract implementation from admin_ui.py."""
#     # Placeholder implementation - DISABLED: Conflicts with inventory_bp.targeting_browser route
#     return jsonify({"error": "Not yet implemented"}), 501


# @operations_bp.route("/inventory", methods=["GET"])
# @require_tenant_access()
# def inventory(tenant_id, **kwargs):
#     """TODO: Extract implementation from admin_ui.py."""
#     # Placeholder implementation - DISABLED: Conflicts with inventory_bp.inventory_browser route
#     return jsonify({"error": "Not yet implemented"}), 501


@operations_bp.route("/orders", methods=["GET"])
@require_tenant_access()
def orders(tenant_id, **kwargs):
    """TODO: Extract implementation from admin_ui.py."""
    # Placeholder implementation
    return jsonify({"error": "Not yet implemented"}), 501


@operations_bp.route("/reporting", methods=["GET"])
@require_auth()
def reporting(tenant_id):
    """Display GAM reporting dashboard."""
    # Import needed for this function
    from flask import render_template, session

    from src.core.database.database_session import get_db_session
    from src.core.database.models import Tenant

    # Verify tenant access
    if session.get("role") != "super_admin" and session.get("tenant_id") != tenant_id:
        return "Access denied", 403

    with get_db_session() as db_session:
        tenant_obj = db_session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()

        if not tenant_obj:
            return "Tenant not found", 404

        # Convert to dict for template compatibility
        tenant = {
            "tenant_id": tenant_obj.tenant_id,
            "name": tenant_obj.name,
            "ad_server": tenant_obj.ad_server,
            "subdomain": tenant_obj.subdomain,
            "is_active": tenant_obj.is_active,
        }

        # Check if tenant is using Google Ad Manager
        if tenant_obj.ad_server != "google_ad_manager":
            return (
                render_template(
                    "error.html",
                    error_title="GAM Reporting Not Available",
                    error_message=f"This tenant is currently using {tenant_obj.ad_server or 'no ad server'}. GAM Reporting is only available for tenants using Google Ad Manager.",
                    back_url=f"/tenant/{tenant_id}",
                ),
                400,
            )

        return render_template("gam_reporting.html", tenant=tenant)


@operations_bp.route("/workflows", methods=["GET"])
@require_tenant_access()
def workflows(tenant_id, **kwargs):
    """List all workflows and pending approvals."""
    from flask import render_template

    from src.core.database.database_session import get_db_session
    from src.core.database.models import Context, MediaBuy, Tenant, WorkflowStep
    from src.core.database.models import Principal as ModelPrincipal

    with get_db_session() as db:
        # Get tenant
        tenant = db.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
        if not tenant:
            return "Tenant not found", 404

        # Get all workflow steps that need attention
        stmt = (
            select(WorkflowStep)
            .join(Context, WorkflowStep.context_id == Context.context_id)
            .filter(Context.tenant_id == tenant_id, WorkflowStep.status == "pending_approval")
            .order_by(WorkflowStep.created_at.desc())
        )
        pending_steps = db.scalars(stmt).all()

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

        # Format workflow steps for display
        workflows_list = []
        for step in pending_steps:
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
                    "status": step.status,
                    "created_at": step.created_at,
                    "principal_name": principal.name if principal else "Unknown",
                    "request_data": step.request_data,
                }
            )

        return render_template(
            "workflows.html",
            tenant=tenant,
            tenant_id=tenant_id,
            summary=summary,
            workflows=workflows_list,
            media_buys=media_buys,
            tasks=[],  # Deprecated - using workflow_steps now
            audit_logs=[],  # Will be populated if needed
        )


@operations_bp.route("/media-buy/<media_buy_id>", methods=["GET"])
@require_tenant_access()
def media_buy_detail(tenant_id, media_buy_id):
    """View media buy details with workflow status."""
    from flask import render_template

    from src.core.context_manager import ContextManager
    from src.core.database.database_session import get_db_session
    from src.core.database.models import (
        Creative,
        CreativeAssignment,
        MediaBuy,
        MediaPackage,
        Principal,
        Product,
        WorkflowStep,
    )

    try:
        with get_db_session() as db_session:
            media_buy = db_session.scalars(
                select(MediaBuy).filter_by(tenant_id=tenant_id, media_buy_id=media_buy_id)
            ).first()

            if not media_buy:
                return "Media buy not found", 404

            # Get principal info
            principal = None
            if media_buy.principal_id:
                stmt = select(Principal).filter_by(tenant_id=tenant_id, principal_id=media_buy.principal_id)
                principal = db_session.scalars(stmt).first()

            # Get packages for this media buy from MediaPackage table
            stmt = select(MediaPackage).filter_by(media_buy_id=media_buy_id)
            media_packages = db_session.scalars(stmt).all()

            packages = []
            for media_pkg in media_packages:
                # Extract product_id from package_config JSONB
                product_id = media_pkg.package_config.get("product_id")
                product = None
                if product_id:
                    from sqlalchemy.orm import selectinload

                    # Eagerly load pricing_options to avoid DetachedInstanceError in template
                    stmt = select(Product).filter_by(tenant_id=tenant_id, product_id=product_id).options(
                        selectinload(Product.pricing_options)
                    )
                    product = db_session.scalars(stmt).first()

                packages.append(
                    {
                        "package": media_pkg,
                        "product": product,
                    }
                )

            # Get creative assignments for this media buy
            stmt = (
                select(CreativeAssignment, Creative)
                .join(Creative, CreativeAssignment.creative_id == Creative.creative_id)
                .filter(CreativeAssignment.media_buy_id == media_buy_id)
                .filter(CreativeAssignment.tenant_id == tenant_id)
                .order_by(CreativeAssignment.package_id, CreativeAssignment.created_at)
            )
            assignment_results = db_session.execute(stmt).all()

            # Group assignments by package_id
            creative_assignments_by_package = {}
            for assignment, creative in assignment_results:
                pkg_id = assignment.package_id
                if pkg_id not in creative_assignments_by_package:
                    creative_assignments_by_package[pkg_id] = []
                creative_assignments_by_package[pkg_id].append(
                    {
                        "assignment": assignment,
                        "creative": creative,
                    }
                )

            # Get workflow steps associated with this media buy
            ctx_manager = ContextManager()
            workflow_steps = ctx_manager.get_object_lifecycle("media_buy", media_buy_id)

            # Find if there's a pending approval step
            pending_approval_step = None
            for step in workflow_steps:
                if step.get("status") in ["requires_approval", "pending_approval"]:
                    # Get the full workflow step for approval actions
                    stmt = select(WorkflowStep).filter_by(step_id=step["step_id"])
                    pending_approval_step = db_session.scalars(stmt).first()
                    break

            # Get computed readiness state (not just raw database status)
            from src.admin.services.media_buy_readiness_service import MediaBuyReadinessService

            readiness = MediaBuyReadinessService.get_readiness_state(media_buy_id, tenant_id, db_session)
            computed_state = readiness["state"]

            # Determine status message
            status_message = None
            if pending_approval_step:
                status_message = {
                    "type": "approval_required",
                    "message": "This media buy requires manual approval before it can be activated.",
                }
            elif media_buy.status == "pending":
                # Check for other pending reasons (creatives, etc.)
                status_message = {
                    "type": "pending_other",
                    "message": "This media buy is pending. It may be waiting for creatives or other requirements.",
                }

            return render_template(
                "media_buy_detail.html",
                tenant_id=tenant_id,
                media_buy=media_buy,
                principal=principal,
                packages=packages,
                workflow_steps=workflow_steps,
                pending_approval_step=pending_approval_step,
                status_message=status_message,
                creative_assignments_by_package=creative_assignments_by_package,
                computed_state=computed_state,
                readiness=readiness,
            )
    except Exception as e:
        logger.error(f"Error viewing media buy: {e}", exc_info=True)
        return "Error loading media buy", 500


@operations_bp.route("/media-buy/<media_buy_id>/approve", methods=["POST"])
@require_tenant_access()
def approve_media_buy(tenant_id, media_buy_id, **kwargs):
    """Approve a media buy by approving its workflow step."""
    from datetime import UTC, datetime

    from flask import flash, redirect, request, url_for
    from sqlalchemy.orm import attributes

    from src.core.database.database_session import get_db_session
    from src.core.database.models import ObjectWorkflowMapping, WorkflowStep

    try:
        action = request.form.get("action")  # "approve" or "reject"
        reason = request.form.get("reason", "")

        with get_db_session() as db_session:
            # Find the pending approval workflow step for this media buy
            stmt = (
                select(WorkflowStep)
                .join(ObjectWorkflowMapping, WorkflowStep.step_id == ObjectWorkflowMapping.step_id)
                .filter(
                    ObjectWorkflowMapping.object_type == "media_buy",
                    ObjectWorkflowMapping.object_id == media_buy_id,
                    WorkflowStep.status.in_(["requires_approval", "pending_approval"]),
                )
            )
            step = db_session.scalars(stmt).first()

            if not step:
                flash("No pending approval found for this media buy", "warning")
                return redirect(url_for("operations.media_buy_detail", tenant_id=tenant_id, media_buy_id=media_buy_id))

            # Get user info for audit
            from flask import session as flask_session

            user_info = flask_session.get("user", {})
            user_email = user_info.get("email", "system") if isinstance(user_info, dict) else str(user_info)

            if action == "approve":
                step.status = "approved"
                step.updated_at = datetime.now(UTC)

                if not step.comments:
                    step.comments = []
                step.comments.append(
                    {
                        "user": user_email,
                        "timestamp": datetime.now(UTC).isoformat(),
                        "comment": "Approved via media buy detail page",
                    }
                )
                attributes.flag_modified(step, "comments")

                # Get the media buy and update status
                from src.core.database.models import MediaBuy, PushNotificationConfig

                stmt_buy = select(MediaBuy).filter_by(media_buy_id=media_buy_id, tenant_id=tenant_id)
                media_buy = db_session.scalars(stmt_buy).first()

                if media_buy and media_buy.status == "pending_approval":
                    # Check if all creatives are approved before moving to scheduled
                    from src.core.database.models import Creative, CreativeAssignment

                    stmt_assignments = select(CreativeAssignment).filter_by(
                        tenant_id=tenant_id, media_buy_id=media_buy_id
                    )
                    assignments = db_session.scalars(stmt_assignments).all()

                    all_creatives_approved = True
                    if assignments:
                        creative_ids = [a.creative_id for a in assignments]
                        stmt_creatives = select(Creative).filter(
                            Creative.tenant_id == tenant_id, Creative.creative_id.in_(creative_ids)
                        )
                        creatives = db_session.scalars(stmt_creatives).all()

                        # Check if any creatives are not approved
                        for creative in creatives:
                            if creative.status != "approved":
                                all_creatives_approved = False
                                break
                    else:
                        # No creatives assigned yet
                        all_creatives_approved = False

                    # Update status based on creative approval state
                    if all_creatives_approved:
                        media_buy.status = "scheduled"
                    else:
                        # Keep it in a state that shows it needs creative approval
                        # Use "draft" which will be displayed as "needs_approval" or "needs_creatives" by readiness service
                        media_buy.status = "draft"

                    media_buy.approved_at = datetime.now(UTC)
                    media_buy.approved_by = user_email
                    db_session.commit()

                    # Execute adapter creation for approved media buy
                    # This creates the order/line items in GAM (or other adapter)
                    # Uses the same logic as auto-approved media buys
                    from src.core.tools.media_buy_create import execute_approved_media_buy

                    logger.info(f"[APPROVAL] Executing adapter creation for approved media buy {media_buy_id}")
                    success, error_msg = execute_approved_media_buy(media_buy_id, tenant_id)

                    if not success:
                        # Adapter creation failed - update status and show error
                        with get_db_session() as error_session:
                            stmt_error = select(MediaBuy).filter_by(tenant_id=tenant_id, media_buy_id=media_buy_id)
                            error_buy = error_session.scalars(stmt_error).first()
                            if error_buy:
                                error_buy.status = "failed"
                                error_session.commit()

                        flash(f"Media buy approved but adapter creation failed: {error_msg}", "error")
                        return redirect(url_for("operations.media_buy_detail", tenant_id=tenant_id, media_buy_id=media_buy_id))

                    logger.info(f"[APPROVAL] Adapter creation succeeded for {media_buy_id}")

                    # Send webhook notification to buyer
                    stmt_webhook = (
                        select(PushNotificationConfig)
                        .filter_by(tenant_id=tenant_id, principal_id=media_buy.principal_id, is_active=True)
                        .order_by(PushNotificationConfig.created_at.desc())
                    )
                    webhook_config = db_session.scalars(stmt_webhook).first()

                    if webhook_config:
                        import requests

                        webhook_payload = {
                            "event": "media_buy_approved",
                            "media_buy_id": media_buy_id,
                            "buyer_ref": media_buy.buyer_ref,
                            "status": "scheduled",
                            "approved_at": media_buy.approved_at.isoformat(),
                            "approved_by": user_email,
                        }
                        try:
                            requests.post(webhook_config.url, json=webhook_payload, timeout=10)
                            logger.info(f"Sent webhook notification for approved media buy {media_buy_id}")
                        except Exception as webhook_err:
                            logger.warning(f"Failed to send webhook notification: {webhook_err}")

                    flash("Media buy approved and order created successfully", "success")
                else:
                    db_session.commit()
                    flash("Media buy approved successfully", "success")

            elif action == "reject":
                step.status = "rejected"
                step.error_message = reason or "Rejected by administrator"
                step.updated_at = datetime.now(UTC)

                if not step.comments:
                    step.comments = []
                step.comments.append(
                    {
                        "user": user_email,
                        "timestamp": datetime.now(UTC).isoformat(),
                        "comment": f"Rejected: {reason or 'No reason provided'}",
                    }
                )
                attributes.flag_modified(step, "comments")

                db_session.commit()
                flash("Media buy rejected", "info")

            return redirect(url_for("operations.media_buy_detail", tenant_id=tenant_id, media_buy_id=media_buy_id))

    except Exception as e:
        logger.error(f"Error approving/rejecting media buy {media_buy_id}: {e}", exc_info=True)
        flash("Error processing approval", "error")
        return redirect(url_for("operations.media_buy_detail", tenant_id=tenant_id, media_buy_id=media_buy_id))


@operations_bp.route("/webhooks", methods=["GET"])
@require_tenant_access()
def webhooks(tenant_id, **kwargs):
    """Display webhook delivery activity dashboard."""
    from flask import render_template, request

    from src.core.database.database_session import get_db_session
    from src.core.database.models import AuditLog, MediaBuy, Tenant
    from src.core.database.models import Principal as ModelPrincipal

    try:
        with get_db_session() as db:
            # Get tenant
            tenant = db.query(Tenant).filter_by(tenant_id=tenant_id).first()
            if not tenant:
                return "Tenant not found", 404

            # Build query for webhook audit logs
            query = (
                db.query(AuditLog)
                .filter_by(tenant_id=tenant_id, operation="send_delivery_webhook")
                .order_by(AuditLog.timestamp.desc())
            )

            # Filter by media buy if specified
            media_buy_filter = request.args.get("media_buy_id")
            if media_buy_filter:
                query = query.filter(AuditLog.details["media_buy_id"].astext == media_buy_filter)

            # Filter by principal if specified
            principal_filter = request.args.get("principal_id")
            if principal_filter:
                query = query.filter_by(principal_id=principal_filter)

            # Limit results
            limit = int(request.args.get("limit", 100))
            webhook_logs = query.limit(limit).all()

            # Get all media buys for filter dropdown
            media_buys = (
                db.query(MediaBuy).filter_by(tenant_id=tenant_id).order_by(MediaBuy.created_at.desc()).limit(50).all()
            )

            # Get all principals for filter dropdown
            principals = db.query(ModelPrincipal).filter_by(tenant_id=tenant_id).all()

            # Calculate summary stats
            total_webhooks = query.count()
            unique_media_buys = len({log.details.get("media_buy_id") for log in webhook_logs if log.details})

            return render_template(
                "webhooks.html",
                tenant=tenant,
                webhook_logs=webhook_logs,
                media_buys=media_buys,
                principals=principals,
                total_webhooks=total_webhooks,
                unique_media_buys=unique_media_buys,
                media_buy_filter=media_buy_filter,
                principal_filter=principal_filter,
                limit=limit,
            )

    except Exception as e:
        logger.error(f"Error loading webhooks dashboard: {e}", exc_info=True)
        return "Error loading webhooks dashboard", 500
