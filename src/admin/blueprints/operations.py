"""Operations management blueprint."""

import asyncio
import logging

from adcp.types import GeneratedTaskStatus as AdcpTaskStatus
from flask import Blueprint, request
from sqlalchemy import select

from src.admin.services.media_buy_completion import emit_media_buy_completion
from src.admin.utils import require_auth, require_tenant_access
from src.core.database.repositories.media_buy import MediaBuyRepository
from src.core.media_buy_flight import lifecycle_status_for_window, resolve_flight_window_utc

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


# @operations_bp.route("/orders", methods=["GET"]) - DISABLED: Conflicts with inventory.orders_browser
# @operations_bp.route("/workflows", methods=["GET"]) - DISABLED: Conflicts with workflows.list_workflows


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
                    back_url=f"{request.script_root}/tenant/{tenant_id}",
                ),
                400,
            )

        return render_template("gam_reporting.html", tenant=tenant)


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
        Principal,
        Product,
        WorkflowStep,
    )

    try:
        with get_db_session() as db_session:
            repo = MediaBuyRepository(db_session, tenant_id)
            media_buy = repo.get_by_id(media_buy_id)

            if not media_buy:
                return "Media buy not found", 404

            # Get principal info
            principal = None
            if media_buy.principal_id:
                stmt = select(Principal).filter_by(tenant_id=tenant_id, principal_id=media_buy.principal_id)
                principal = db_session.scalars(stmt).first()

            # Get packages for this media buy from MediaPackage table
            media_packages = repo.get_packages(media_buy_id)

            packages = []
            for media_pkg in media_packages:
                # Extract product_id from package_config JSONB
                product_id = media_pkg.package_config.get("product_id")
                product = None
                if product_id:
                    from sqlalchemy.orm import selectinload

                    # Eagerly load pricing_options to avoid DetachedInstanceError in template
                    stmt = (
                        select(Product)
                        .filter_by(tenant_id=tenant_id, product_id=product_id)
                        .options(selectinload(Product.pricing_options))
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

            # Get workflow steps associated with this media buy (tenant-scoped)
            ctx_manager = ContextManager()
            workflow_steps = ctx_manager.get_object_lifecycle("media_buy", media_buy_id, tenant_id=tenant_id)

            # Find if there's a pending approval step
            pending_approval_step = None
            for step in workflow_steps:
                if step.get("status") in ["requires_approval", "pending_approval"]:
                    # Get the full workflow step for approval actions (tenant-scoped via Context join)
                    from src.core.database.models import Context as DBContext

                    stmt = (
                        select(WorkflowStep)
                        .join(DBContext)
                        .where(DBContext.tenant_id == tenant_id, WorkflowStep.step_id == step["step_id"])
                    )
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

            # Fetch delivery metrics if media buy is active or completed
            delivery_metrics = None
            if media_buy.status in ["active", "approved", "completed"]:
                try:
                    from datetime import UTC, datetime, timedelta

                    from src.core.config_loader import set_current_tenant
                    from src.core.database.models import Tenant
                    from src.core.helpers.adapter_helpers import get_adapter
                    from src.core.schemas import Principal as PrincipalSchema
                    from src.core.schemas import ReportingPeriod

                    # Get adapter for this principal
                    if principal:
                        # Set tenant context before calling get_adapter (required for adapter initialization)
                        tenant = db_session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
                        if tenant:
                            set_current_tenant(
                                {
                                    "tenant_id": tenant_id,
                                    "ad_server": tenant.ad_server or "mock",
                                }
                            )

                        # Convert SQLAlchemy model to Pydantic schema (get_adapter expects schema)
                        principal_schema = PrincipalSchema(
                            principal_id=principal.principal_id,
                            name=principal.name,
                            platform_mappings=principal.platform_mappings or {},
                        )
                        adapter = get_adapter(principal_schema, dry_run=False)

                        # Calculate date range (last 7 days or campaign duration) - always use UTC
                        end_date = datetime.now(UTC)
                        seven_days_ago = datetime.now(UTC) - timedelta(days=7)

                        # Convert media_buy.start_date (date) to datetime with UTC timezone
                        mb_start = media_buy.start_date
                        if mb_start:
                            # Convert date to datetime (start of day) with UTC timezone
                            mb_start = datetime.combine(mb_start, datetime.min.time()).replace(tzinfo=UTC)

                        start_date = max(mb_start if mb_start else seven_days_ago, seven_days_ago)

                        reporting_period = ReportingPeriod(start=start_date, end=end_date)

                        # Fetch delivery metrics from adapter
                        delivery_response = adapter.get_media_buy_delivery(
                            media_buy_id=media_buy_id, date_range=reporting_period, today=datetime.now(UTC)
                        )

                        delivery_metrics = {
                            "impressions": delivery_response.totals.impressions,
                            "spend": delivery_response.totals.spend,
                            "clicks": delivery_response.totals.clicks,
                            "ctr": delivery_response.totals.ctr,
                            "currency": delivery_response.currency,
                            "by_package": delivery_response.by_package,
                        }
                except Exception as e:
                    logger.warning(f"Could not fetch delivery metrics for {media_buy_id}: {e}")
                    # Continue without metrics - don't fail the whole page

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
                delivery_metrics=delivery_metrics,
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
    from src.core.database.models import Context as DBContext
    from src.core.database.models import ObjectWorkflowMapping, WorkflowStep

    try:
        action = request.form.get("action")  # "approve" or "reject"
        reason = request.form.get("reason", "")

        with get_db_session() as db_session:
            # Find the pending approval workflow step for this media buy (tenant-scoped via Context join)
            stmt = (
                select(WorkflowStep)
                .join(ObjectWorkflowMapping, WorkflowStep.step_id == ObjectWorkflowMapping.step_id)
                .join(DBContext)
                .filter(
                    DBContext.tenant_id == tenant_id,
                    ObjectWorkflowMapping.object_type == "media_buy",
                    ObjectWorkflowMapping.object_id == media_buy_id,
                    WorkflowStep.status.in_(["requires_approval", "pending_approval"]),
                )
            )
            step = db_session.scalars(stmt).first()

            if not step:
                flash("No pending approval found for this media buy", "warning")
                return redirect(url_for("operations.media_buy_detail", tenant_id=tenant_id, media_buy_id=media_buy_id))

            # Extract step data to dict to avoid detached instance errors after commit/nested sessions
            step_data = {
                "step_id": step.step_id,
                "context_id": step.context_id,
                "tool_name": step.tool_name,
                "request_data": step.request_data or {},
            }

            # Get user info for audit
            from flask import session as flask_session

            user_info = flask_session.get("user", {})
            user_email = user_info.get("email", "system") if isinstance(user_info, dict) else str(user_info)

            media_buy_repo = MediaBuyRepository(db_session, tenant_id)
            media_buy = media_buy_repo.get_by_id(media_buy_id)

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

                    # Compute the target status based on creative approval state.
                    # The window→status decision lives in the business layer
                    # (media_buy_flight); the route only orchestrates resolve →
                    # decide → persist. See #1544.
                    if all_creatives_approved:
                        new_status = lifecycle_status_for_window(
                            datetime.now(UTC), *resolve_flight_window_utc(media_buy)
                        )
                    else:
                        # Keep it in a state that shows it needs creative approval
                        # Use "draft" which will be displayed as "needs_approval" or "needs_creatives" by readiness service
                        new_status = "draft"

                    # Route the transition through the repository seam so the
                    # persisted revision bumps and approved_at/approved_by are
                    # stamped in one place (AdCP 3.1.0-beta.3 revision counter + confirmed_at
                    # confirmation instant). Direct ``.status``/``.approved_at``
                    # writes here would skip the bump — see #1544 review.
                    media_buy_repo.update_status_or_raise(
                        media_buy_id,
                        new_status,
                        approved_at=datetime.now(UTC),
                        approved_by=user_email,
                    )
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
                            error_repo = MediaBuyRepository(error_session, tenant_id)
                            error_buy = error_repo.update_status(media_buy_id, "failed")
                            if error_buy:
                                error_session.commit()

                        flash(f"Media buy approved but adapter creation failed: {error_msg}", "error")
                        return redirect(
                            url_for("operations.media_buy_detail", tenant_id=tenant_id, media_buy_id=media_buy_id)
                        )

                    logger.info(f"[APPROVAL] Adapter creation succeeded for {media_buy_id}")

                    # Emit the completion artifact to the buyer (shared helper — the
                    # workflow and creative-unblock routes reuse the same lookup +
                    # emission). No-op when the buy has no push config. See #1544.
                    emit_media_buy_completion(
                        db_session,
                        tenant_id,
                        media_buy_repo.get_by_id(media_buy_id),
                        media_buy_repo.get_packages(media_buy_id),
                        step_data,
                        AdcpTaskStatus.completed,
                    )

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

                if media_buy and media_buy.status == "pending_approval":
                    # Route through the repository seam so the persisted revision
                    # bumps on this state change (AdCP 3.1.0-beta.3 revision) — see #1544.
                    media_buy_repo.update_status_or_raise(media_buy_id, "rejected")

                db_session.commit()

                # Emit the rejection notification to the buyer. rejection_reason is a
                # spec MUST on the seller rejection webhook (pinned beta.3
                # specification.mdx) — carry the reason recorded on the workflow step
                # (default when the admin left it blank). No-op without push config. #1544.
                emit_media_buy_completion(
                    db_session,
                    tenant_id,
                    media_buy_repo.get_by_id(media_buy_id),
                    media_buy_repo.get_packages(media_buy_id),
                    step_data,
                    AdcpTaskStatus.rejected,
                    rejection_reason=reason or "Rejected by administrator",
                )

                flash("Media buy rejected", "info")

            return redirect(url_for("operations.media_buy_detail", tenant_id=tenant_id, media_buy_id=media_buy_id))

    except Exception as e:
        logger.error(f"Error approving/rejecting media buy {media_buy_id}: {e}", exc_info=True)
        flash("Error processing approval", "error")
        return redirect(url_for("operations.media_buy_detail", tenant_id=tenant_id, media_buy_id=media_buy_id))


@operations_bp.route("/media-buy/<media_buy_id>/trigger-delivery-webhook", methods=["POST"])
@require_tenant_access()
def trigger_delivery_webhook(tenant_id, media_buy_id, **kwargs):
    """Trigger a delivery report webhook for a media buy manually."""
    from flask import flash, redirect, url_for

    from src.services.delivery_webhook_scheduler import get_delivery_webhook_scheduler

    try:
        # Trigger webhook using scheduler - pass IDs to avoid detached instance errors
        scheduler = get_delivery_webhook_scheduler()
        success = asyncio.run(scheduler.trigger_report_for_media_buy_by_id(media_buy_id, tenant_id))

        if success:
            flash("Delivery webhook triggered successfully", "success")
        else:
            flash("Failed to trigger delivery webhook. Check logs or configuration.", "warning")

        return redirect(url_for("operations.media_buy_detail", tenant_id=tenant_id, media_buy_id=media_buy_id))

    except Exception as e:
        logger.error(f"Error triggering delivery webhook for {media_buy_id}: {e}", exc_info=True)
        flash("Error triggering delivery webhook", "error")
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
