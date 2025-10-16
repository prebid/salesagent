"""Operations management blueprint."""

import logging

from flask import Blueprint, jsonify
from sqlalchemy import select

from src.admin.utils import require_auth, require_tenant_access

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
    """View media buy details."""
    from flask import render_template

    from src.core.database.database_session import get_db_session
    from src.core.database.models import MediaBuy, Principal

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

            return render_template(
                "media_buy_detail.html", tenant_id=tenant_id, media_buy=media_buy, principal=principal
            )
    except Exception as e:
        logger.error(f"Error viewing media buy: {e}", exc_info=True)
        return "Error loading media buy", 500


@operations_bp.route("/media-buy/<media_buy_id>/approve", methods=["GET"])
@require_tenant_access()
def media_buy_media_buy_id_approve(tenant_id, **kwargs):
    """TODO: Extract implementation from admin_ui.py."""
    # Placeholder implementation
    return jsonify({"error": "Not yet implemented"}), 501


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
