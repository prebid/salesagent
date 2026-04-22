"""Operations management blueprint."""

import asyncio
import logging

from adcp import create_a2a_webhook_payload, create_mcp_webhook_payload
from adcp.types import CreateMediaBuySuccessResponse, Package
from adcp.types import GeneratedTaskStatus as AdcpTaskStatus
from adcp.types.generated_poc.media_buy.create_media_buy_async_response_input_required import (
    CreateMediaBuyInputRequired,
)
from adcp.types.generated_poc.media_buy.create_media_buy_async_response_input_required import (
    Reason as CreateMediaBuyInputRequiredReason,
)
from flask import Blueprint, request
from sqlalchemy import select

from src.admin.utils import require_auth, require_tenant_access
from src.core.database.models import PushNotificationConfig
from src.core.database.repositories.media_buy import MediaBuyRepository
from src.services.protocol_webhook_service import get_protocol_webhook_service

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
    """Approve a media buy by approving its workflow step.

    Session lifecycle (close-outer-before-adapter pattern, matching creatives.py:607-639):

    Phase 1 (session 1): Load workflow step + media buy, validate, mark step approved/rejected,
        set media_buy.approved_at/approved_by (approve) or status="rejected" (reject), decide
        (a) whether to call the adapter and (b) what AdCP task-status the downstream webhook
        should report. Commit and close.

    Phase 2 (no session held): Call execute_approved_media_buy() if the adapter path applies.
        That function opens its own MediaBuyUoW and commits media_buy.status="active".
        Holding the outer session across this call (as the prior implementation did) makes the
        outer session's stale ORM instance divergent from the adapter's write under bare
        sessionmaker — a lost-update hazard. Closing the outer session first eliminates it.

    Phase 3 (session 2): Re-open a fresh session to send the webhook notification (reads only).
        Fires only when ``webhook_status`` was set during Phase 1.

    Webhook dispatch matrix
    -----------------------

    Per-branch webhook semantics, driven by a single ``webhook_status`` variable set in Phase 1:

    =================================================  ================  ============================================
    Branch                                             webhook_status    Result payload schema
    =================================================  ================  ============================================
    approve + pending + all_creatives_approved + OK    ``completed``     ``CreateMediaBuySuccessResponse``
    approve + pending + all_creatives_approved + FAIL  ``None``          (early return before Phase 3)
    approve + pending + creatives NOT approved          ``input_required``  ``CreateMediaBuyInputRequired(APPROVAL_REQUIRED)``
    approve + step-level ack (status != pending)       ``None``          (no webhook)
    reject                                             ``rejected``      ``CreateMediaBuySuccessResponse``
    =================================================  ================  ============================================

    ``input_required`` is the exact AdCP A2A ``TaskStatus`` for "workflow step complete but
    waiting on buyer input (approved creatives)". Sending ``completed`` here — as main did
    after running the adapter anyway on incomplete creatives — misleads the buyer into thinking
    the media buy is live. A2A serializes ``input_required`` as the hyphenated wire string
    ``input-required``; MCP serializes it as ``input_required``.

    Note on latent upstream gap (NOT addressed here): on the happy path, ``execute_approved_media_buy``
    itself does NOT emit a ``create_media_buy → completed`` webhook — the buyer infers media-buy
    liveness from the separate ``sync_creatives`` webhook chain. File P2 follow-up to close that
    loop. Similarly the adapter-fail branch emits no webhook at all (buyer never sees failure);
    file P3 follow-up.
    """
    from datetime import UTC, datetime

    from flask import flash, redirect, request, url_for
    from sqlalchemy.orm import attributes

    from src.core.database.database_session import get_db_session
    from src.core.database.models import Context as DBContext
    from src.core.database.models import ObjectWorkflowMapping, WorkflowStep

    try:
        action = request.form.get("action")  # "approve" or "reject"
        reason = request.form.get("reason", "")

        # Get user info for audit (same flask session read for every branch)
        from flask import session as flask_session

        user_info = flask_session.get("user", {})
        user_email = user_info.get("email", "system") if isinstance(user_info, dict) else str(user_info)

        # -------- Phase 1: outer session #1 — validate + mark step + commit -------------
        step_data: dict | None = None
        media_buy_data: dict | None = None
        call_adapter = False  # True only on approve + all creatives approved + media_buy pending
        # Per-branch webhook semantics. None = no webhook. The "completed" default was the
        # historical silent bug — a step-level ack would have emitted completed without any
        # adapter call. Default to None; each branch sets the correct status explicitly.
        webhook_status: AdcpTaskStatus | None = None
        flash_on_success = "Media buy approved successfully"  # default for approve-without-adapter

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

            # Extract step data to dict so it survives beyond this session
            step_data = {
                "step_id": step.step_id,
                "context_id": step.context_id,
                "tool_name": step.tool_name,
                "request_data": step.request_data or {},
            }

            approve_repo = MediaBuyRepository(db_session, tenant_id)
            media_buy = approve_repo.get_by_id(media_buy_id)

            # Extract media_buy data to dict so it survives beyond this session
            if media_buy:
                push_config = step.request_data.get("push_notification_config") or {} if step.request_data else {}
                media_buy_data = {
                    "principal_id": media_buy.principal_id,
                    "buyer_ref": media_buy.buyer_ref,
                    "push_notification_url": push_config.get("url"),
                }

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
                    # Check if all creatives are approved before scheduling adapter execution
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

                    # Record approver metadata on the outer session (always committed here).
                    # The media_buy.status transition is owned by the adapter path
                    # (execute_approved_media_buy → "active") when all creatives are approved;
                    # otherwise we set status="draft" here so the readiness service surfaces it.
                    media_buy.approved_at = datetime.now(UTC)
                    media_buy.approved_by = user_email

                    if all_creatives_approved:
                        # Defer status write to Phase 2's adapter, which sets "active".
                        # (Previously the outer session wrote "scheduled"/"active"/"completed"
                        # here and committed, but the inner MediaBuyUoW always overwrote it
                        # to "active" — the outer write was effectively dead code under
                        # scoped_session and a lost-update hazard under bare sessionmaker.)
                        call_adapter = True
                        webhook_status = AdcpTaskStatus.completed
                    else:
                        # No adapter call in this branch — status stays "draft" to indicate
                        # the media buy is still waiting on creatives. Notify the buyer with
                        # input_required (the AdCP A2A TaskStatus for "workflow step done
                        # but waiting on buyer input" — in this case, approved creatives).
                        media_buy.status = "draft"
                        webhook_status = AdcpTaskStatus.input_required
                        flash_on_success = "Media buy approved successfully"
                else:
                    # No state transition — just a step-level approval ack.
                    # No webhook: matches main's behavior; a step-level ack is operator-
                    # internal workflow bookkeeping with no buyer-visible state change.
                    flash_on_success = "Media buy approved successfully"

                db_session.commit()

            elif action == "reject":
                webhook_status = AdcpTaskStatus.rejected

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
                    media_buy.status = "rejected"
                    attributes.flag_modified(media_buy, "status")

                db_session.commit()
                flash_on_success = "Media buy rejected"

            else:
                # Unknown action — don't commit, fall through to the redirect.
                return redirect(url_for("operations.media_buy_detail", tenant_id=tenant_id, media_buy_id=media_buy_id))

        # -------- Phase 2: call adapter with no outer session held ----------------------
        if call_adapter:
            from src.core.tools.media_buy_create import execute_approved_media_buy

            logger.info(f"[APPROVAL] Executing adapter creation for approved media buy {media_buy_id}")
            success, error_msg = execute_approved_media_buy(media_buy_id, tenant_id)

            if not success:
                # Adapter creation failed — mark media buy "failed" in a fresh session.
                with get_db_session() as error_session:
                    error_repo = MediaBuyRepository(error_session, tenant_id)
                    error_buy = error_repo.update_status(media_buy_id, "failed")
                    if error_buy:
                        error_session.commit()

                flash(f"Media buy approved but adapter creation failed: {error_msg}", "error")
                return redirect(url_for("operations.media_buy_detail", tenant_id=tenant_id, media_buy_id=media_buy_id))

            logger.info(f"[APPROVAL] Adapter creation succeeded for {media_buy_id}")
            flash_on_success = "Media buy approved and order created successfully"

        # -------- Phase 3: fresh outer session #2 — send webhook ------------------------
        # Kept inline (rather than extracted) so the raw select(PushNotificationConfig)
        # below stays inside the approve_media_buy allowlist entry for the
        # test_architecture_no_raw_select guard. Extracting it to a helper would add
        # a new (file, function) entry to the allowlist, which the guard disallows.
        #
        # Gate: fire only when Phase 1 set a webhook_status. No-op branches
        # (step-level ack, no push URL, no webhook config match) fall through.
        if webhook_status is not None and media_buy_data and step_data and media_buy_data.get("push_notification_url"):
            with get_db_session() as webhook_session:
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
                webhook_config = webhook_session.scalars(stmt_webhook).first()

                if webhook_config:
                    # Branch the result payload by webhook_status. `input_required` uses
                    # the canonical `CreateMediaBuyInputRequired` shape (with a Reason
                    # enum) rather than `CreateMediaBuySuccessResponse` — the latter
                    # implies an operational media buy, which a buyer waiting on
                    # creatives does not have. Builders below accept `dict[str, Any]`
                    # so wire serialization works for either shape; spec-validating
                    # buyers will see the correct discriminated-union variant.
                    webhook_result: CreateMediaBuyInputRequired | CreateMediaBuySuccessResponse
                    if webhook_status == AdcpTaskStatus.input_required:
                        webhook_result = CreateMediaBuyInputRequired(
                            reason=CreateMediaBuyInputRequiredReason.APPROVAL_REQUIRED,
                        )
                    else:
                        webhook_repo = MediaBuyRepository(webhook_session, tenant_id)
                        all_packages = webhook_repo.get_packages(media_buy_id)
                        webhook_result = CreateMediaBuySuccessResponse(
                            media_buy_id=media_buy_id,
                            buyer_ref=media_buy_data["buyer_ref"],
                            packages=[Package(package_id=x.package_id) for x in all_packages],
                            context={},  # TODO: @yusuf - please fix this, like we've fixed in the creative approval
                        )

                    metadata = {
                        "task_type": step_data["tool_name"],
                        # TODO: @yusuf - check if we were passing principal_id and tenant to this previously
                        # TODO: @yusuf - check if we want to make metadata typed
                    }

                    # Determine protocol type from workflow step request_data (default MCP for back-compat)
                    protocol = step_data["request_data"].get("protocol", "mcp")

                    if protocol == "a2a":
                        webhook_payload = create_a2a_webhook_payload(
                            task_id=step_data["step_id"],
                            status=webhook_status,
                            result=webhook_result,
                            context_id=step_data["context_id"],
                        )
                    else:
                        webhook_payload = create_mcp_webhook_payload(
                            task_id=step_data["step_id"],
                            result=webhook_result,
                            status=webhook_status,
                        )

                    try:
                        service = get_protocol_webhook_service()
                        asyncio.run(
                            service.send_notification(
                                push_notification_config=webhook_config,
                                payload=webhook_payload,
                                metadata=metadata,
                            )
                        )
                        logger.info(f"Sent webhook notification for media buy {media_buy_id}")
                    except Exception as webhook_err:
                        logger.warning(f"Failed to send webhook notification: {webhook_err}")

        flash(flash_on_success, "success" if action == "approve" else "info")
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
