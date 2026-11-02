"""Operations management blueprint."""

import asyncio
import logging
from typing import Any

from adcp.types import GeneratedTaskStatus as AdcpTaskStatus

# FIXME(#1388): Package has a local subclass; import from src.core.schemas (Pattern #7/#4).
from adcp.types import Package
from flask import Blueprint, request
from sqlalchemy import select

from src.admin.utils import approve_media_buy_through_writer, require_auth, require_tenant_access
from src.core.database.models import PersistedMediaBuyStatus, PushNotificationConfig
from src.core.database.repositories.media_buy import MediaBuyRepository
from src.core.database.repositories.principal import PrincipalRepository
from src.core.errors.details import RejectionReasonDetails
from src.core.exceptions import AdCPMediaBuyRejectedError
from src.core.schemas import CreateMediaBuyError, CreateMediaBuySuccess, Error
from src.core.tools.media_buy_create import ApprovalOutcome
from src.core.webhooks.delivery import WebhookTaskContext
from src.services.protocol_webhook_service import get_protocol_webhook_service

logger = logging.getLogger(__name__)


def _as_request_dict(value: dict[str, Any] | str | None) -> dict[str, Any]:
    """Narrow JSONType (dict|str|None) to a dict for .get()."""
    return value if isinstance(value, dict) else {}


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
        Product,
        WorkflowStep,
    )

    try:
        with get_db_session() as db_session:
            repo = MediaBuyRepository(db_session, tenant_id)
            media_buy = repo.get_by_id(media_buy_id)

            if not media_buy:
                return "Media buy not found", 404

            # The buy's owner, loaded ONCE: the same stored-ids resolution the approval
            # path uses, so the template's principal and the adapter's identity are one
            # load. A buy whose owner row is gone renders without one.
            from src.core.exceptions import AdCPConfigurationError
            from src.core.resolved_identity import identity_of

            owner = None
            if media_buy.principal_id:
                try:
                    owner = identity_of(tenant_id, media_buy.principal_id)
                except AdCPConfigurationError:
                    owner = None
            principal = owner.principal if owner else None

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

                    from src.core.helpers.adapter_helpers import get_adapter
                    from src.core.schemas import ReportingPeriod

                    if owner:
                        # The operator view acts as the buy's owner, resolved above.
                        adapter = get_adapter(owner)

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


def _media_buy_webhook_task(
    step_data: dict, tenant_id: str, media_buy_id: str, media_buy_data: dict
) -> WebhookTaskContext:
    """The typed delivery context for a media-buy approval/rejection webhook.

    Was a dict of the same four values. Typed now because the delivery seam reads
    task_type/tenant_id/principal_id/media_buy_id for delivery logging and the
    audit trail, and a dict lets a caller omit one silently — which is how the
    admin sync_creatives sender came to write no delivery-log row at all
    (salesagent-pldmk.39). Shared by the approve and reject branches
    (PR #1567 round-2 cleanup).

    task_type carries step_data["tool_name"] VERBATIM. notify() coerces its own
    copy for the SDK payload, so the untrusted DB label never reaches the wire and
    the original still keys the guards (salesagent-yi3s, salesagent-yk7o).
    """
    return WebhookTaskContext(
        task_id=step_data["step_id"],
        task_type=step_data["tool_name"],
        tenant_id=tenant_id,
        principal_id=media_buy_data["principal_id"],
        media_buy_id=media_buy_id,
        sequence_number=1,
        notification_type=None,
    )


@operations_bp.route("/media-buy/<media_buy_id>/approve", methods=["POST"])
@require_tenant_access()
def approve_media_buy(tenant_id, media_buy_id, **kwargs):
    """Approve a media buy by approving its workflow step."""
    from datetime import UTC, datetime

    from flask import flash, redirect, request, url_for

    from src.core.database.database_session import get_db_session
    from src.core.database.models import Context as DBContext
    from src.core.database.models import ObjectWorkflowMapping, WorkflowStep
    from src.core.database.repositories.workflow import WorkflowRepository

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

            # Extract step data to dict to avoid detached instance errors after commit/nested sessions.
            # JSONType columns are typed as dict|str|None; narrow before .get().
            request_data = _as_request_dict(step.request_data)
            step_data = {
                "step_id": step.step_id,
                "context_id": step.context_id,
                "tool_name": step.tool_name,
                "request_data": request_data,
            }

            # Get user info for audit
            from flask import session as flask_session

            user_info = flask_session.get("user", {})
            user_email = user_info.get("email", "system") if isinstance(user_info, dict) else str(user_info)

            approve_repo = MediaBuyRepository(db_session, tenant_id)
            media_buy = approve_repo.get_by_id(media_buy_id)

            # Extract media_buy data to dict to avoid detached instance errors after commit
            media_buy_data = None
            if media_buy:
                # Get push_notification_config from workflow step request_data (same pattern as sync_creatives)
                push_config = request_data.get("push_notification_config") or {}
                media_buy_data = {
                    "principal_id": media_buy.principal_id,
                    "push_notification_url": push_config.get("url"),
                }

            if action == "approve":
                step.status = "approved"
                step.updated_at = datetime.now(UTC)

                # Atomic tenant-scoped append (salesagent-pgqs): the old
                # whole-list write-back erased concurrent comments.
                WorkflowRepository(db_session, tenant_id).append_comment(
                    step.step_id, user=user_email, text="Approved via media buy detail page"
                )

                # Commit the step BEFORE calling the writer. The callee opens nested
                # sessions, and get_db_session()'s exit closes the shared thread-scoped
                # session without committing — which discards whatever this route still
                # had pending. The step's approval and its audit comment must not depend
                # on what the adapter does next: a successful approval that leaves the
                # step reading 'requires_approval' is what the operator sees.
                db_session.commit()

                if media_buy and media_buy.status == "pending_approval":
                    approval = approve_media_buy_through_writer(media_buy_id, tenant_id, approved_by=user_email)

                    if approval.outcome is ApprovalOutcome.HELD_PENDING_CREATIVES or not approval.ok:
                        return redirect(
                            url_for("operations.media_buy_detail", tenant_id=tenant_id, media_buy_id=media_buy_id)
                        )

                    logger.info(f"[APPROVAL] Adapter creation succeeded for {media_buy_id}")

                    # Send webhook notification to buyer
                    webhook_config = None
                    if media_buy_data and media_buy_data["push_notification_url"]:
                        stmt_webhook = (
                            select(PushNotificationConfig)
                            .filter_by(
                                tenant_id=tenant_id,
                                principal_id=media_buy_data["principal_id"],
                                url=media_buy_data["push_notification_url"],
                                is_active=True,
                            )
                            .order_by(PushNotificationConfig.created_at.desc())
                        )
                        webhook_config = db_session.scalars(stmt_webhook).first()

                    if webhook_config and media_buy_data:
                        approve_repo = MediaBuyRepository(db_session, tenant_id)
                        all_packages = approve_repo.get_packages(media_buy_id)

                        # Both columns come off the ApprovalResult, not a re-read. The
                        # writer reports what it wrote; a route that re-reads the row after
                        # the call is the shape that made a detached read possible here.
                        create_media_buy_approved_result = CreateMediaBuySuccess.sync_success(
                            media_buy_id=media_buy_id,
                            message=f"Media buy {media_buy_id} created successfully.",
                            packages=[Package(package_id=x.package_id) for x in all_packages],
                            confirmed_at=approval.confirmed_at,
                            revision=approval.revision,
                        )
                        webhook_task = _media_buy_webhook_task(step_data, tenant_id, media_buy_id, media_buy_data)

                        try:
                            service = get_protocol_webhook_service()
                            asyncio.run(
                                service.notify(
                                    webhook_config,
                                    task=webhook_task,
                                    status=AdcpTaskStatus.completed,
                                    result=create_media_buy_approved_result,
                                )
                            )
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

                # Atomic tenant-scoped append (salesagent-pgqs), as in approve.
                WorkflowRepository(db_session, tenant_id).append_comment(
                    step.step_id, user=user_email, text=f"Rejected: {reason or 'No reason provided'}"
                )

                if media_buy and media_buy.status == "pending_approval":
                    # approve_repo is constructed before the action split, so it is the
                    # repository in scope here too — rejection moves the buy's revision
                    # like any other status change.
                    approve_repo.update_status(media_buy_id, PersistedMediaBuyStatus.REJECTED)

                db_session.commit()

                # Send webhook notification to buyer
                webhook_config = None
                if media_buy_data and media_buy_data["push_notification_url"]:
                    stmt_webhook = (
                        select(PushNotificationConfig)
                        .filter_by(
                            tenant_id=tenant_id,
                            principal_id=media_buy_data["principal_id"],
                            url=media_buy_data["push_notification_url"],
                            is_active=True,
                        )
                        .order_by(PushNotificationConfig.created_at.desc())
                    )
                    webhook_config = db_session.scalars(stmt_webhook).first()

                if webhook_config and media_buy_data:
                    # A rejection is NOT a completed media buy. adcp 6.6 defaults
                    # CreateMediaBuySuccess.status="completed"/confirmed_at, so embedding a
                    # Success here would assert the buy COMPLETED inside a status="rejected"
                    # webhook. Spec 3.1.1 create-media-buy-response.json models a non-success
                    # outcome as the CreateMediaBuyError variant — embed that with the reason.
                    #
                    # Route the code through the typed AdCPSalesAgentError cascade so the buyer sees
                    # the same WIRE code the tool path emits for this event — which is now
                    # MEDIA_BUY_REJECTED itself, not a POLICY_VIOLATION rewrite. Never
                    # hand-pick codes here (PR #1567 round-2 item 1); the class declares it.
                    # The seller's typed reason is OPERATOR DATA, not the buyer-facing
                    # sentence: `message` is a function of the code through CODE_TABLE and so
                    # cannot carry it. It travels in `details` — without this the buyer is told
                    # only "The media buy was declined" and never learns why, which is what the
                    # comment above has always promised ("embed that with the reason").
                    # DERIVED from the exception, not hand-listed. Error.from_exception takes
                    # the code, field, details and retry_after off the error that already knows
                    # them, so the advisory and the transport envelope cannot disagree about the
                    # same failure -- there is one derivation, not a second copy. Reading the
                    # three fields separately did exactly that: once salesagent-3dawm.8 made
                    # suggestion resolve from CODE_TABLE, a hand-built copy that omitted it gave
                    # the buyer MEDIA_BUY_REJECTED *with* the pin's suggestion on the tool path
                    # and *without* it on this webhook -- one code, two behaviours, split by
                    # lane.
                    rejection = AdCPMediaBuyRejectedError(
                        details=RejectionReasonDetails(rejection_reason=reason) if reason else None
                    )
                    create_media_buy_rejected_result = CreateMediaBuyError(
                        status=AdcpTaskStatus.rejected,
                        errors=[Error.from_exception(rejection)],
                        message="Media buy creation encountered 1 error(s).",
                    )
                    webhook_task = _media_buy_webhook_task(step_data, tenant_id, media_buy_id, media_buy_data)

                    try:
                        service = get_protocol_webhook_service()
                        asyncio.run(
                            service.notify(
                                webhook_config,
                                task=webhook_task,
                                status=AdcpTaskStatus.rejected,
                                result=create_media_buy_rejected_result,
                            )
                        )
                        logger.info(f"Sent webhook notification for rejected media buy {media_buy_id}")

                    except Exception as webhook_err:
                        logger.warning(f"Failed to send webhook notification: {webhook_err}")

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
            principals = PrincipalRepository(db, tenant_id).list_all()

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
