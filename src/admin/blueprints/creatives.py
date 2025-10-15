"""Creative formats management blueprint for admin UI."""

import json
import logging
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

# TODO: Missing module - these functions need to be implemented
# from creative_formats import discover_creative_formats_from_url, parse_creative_spec


# Placeholder implementations for missing functions
def parse_creative_spec(url):
    """Parse creative specification from URL - placeholder implementation."""
    return {"success": False, "error": "Creative format parsing not yet implemented", "url": url}


def discover_creative_formats_from_url(url):
    """Discover creative formats from URL - placeholder implementation."""
    return []


from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for
from sqlalchemy import select

from src.admin.utils import require_tenant_access
from src.core.database.database_session import get_db_session
from src.core.database.models import Tenant

# CreativeFormat removed - table dropped in migration f2addf453200

logger = logging.getLogger(__name__)

# Create Blueprint
creatives_bp = Blueprint("creatives", __name__)

# Global ThreadPoolExecutor for async AI review (managed lifecycle)
_ai_review_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="ai_review_")
_ai_review_tasks = {}  # task_id -> Future mapping
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


def _call_webhook_for_creative_status(
    webhook_url: str, creative_id: str, status: str, creative_data: dict = None, tenant_id: str = None
):
    """Call webhook to notify about creative status change with retry logic.

    Args:
        webhook_url: URL to POST notification to
        creative_id: Creative ID
        status: New status (approved, rejected, pending)
        creative_data: Optional creative data to include
        tenant_id: Optional tenant ID for signature verification

    Returns:
        bool: True if webhook delivered successfully, False otherwise
    """
    from src.core.webhook_delivery import WebhookDelivery, deliver_webhook_with_retry

    try:
        # Build payload
        payload = {
            "object_type": "creative",
            "object_id": creative_id,
            "status": status,
            "timestamp": datetime.now(UTC).isoformat(),
        }

        if creative_data:
            payload["creative_data"] = creative_data

        headers = {"Content-Type": "application/json"}

        # Get signing secret from tenant
        signing_secret = None
        if tenant_id:
            try:
                with get_db_session() as db_session:
                    stmt = select(Tenant).filter_by(tenant_id=tenant_id)
                    tenant = db_session.scalars(stmt).first()
                    if tenant and hasattr(tenant, "admin_token") and tenant.admin_token:
                        signing_secret = tenant.admin_token
            except Exception as e:
                logger.warning(f"Could not fetch tenant for signature: {e}")

        # Create delivery configuration
        delivery = WebhookDelivery(
            webhook_url=webhook_url,
            payload=payload,
            headers=headers,
            max_retries=3,
            timeout=10,
            signing_secret=signing_secret,
            event_type="creative.status_changed",
            tenant_id=tenant_id,
            object_id=creative_id,
        )

        # Deliver with retry
        success, result = deliver_webhook_with_retry(delivery)

        if success:
            logger.info(
                f"Successfully delivered webhook for creative {creative_id} status={status} "
                f"(attempts={result['attempts']}, delivery_id={result['delivery_id']})"
            )
        else:
            logger.error(
                f"Failed to deliver webhook for creative {creative_id} after {result['attempts']} attempts: "
                f"{result.get('error', 'Unknown error')} (delivery_id={result['delivery_id']})"
            )

        return success

    except Exception as e:
        logger.error(f"Error setting up webhook delivery for creative {creative_id}: {e}", exc_info=True)
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
    from src.core.database.models import Creative, CreativeAssignment, MediaBuy, Principal, Product

    with get_db_session() as db_session:
        # Get tenant
        stmt = select(Tenant).filter_by(tenant_id=tenant_id)
        tenant = db_session.scalars(stmt).first()
        if not tenant:
            return "Tenant not found", 404

        # Get all creatives ordered by status (pending first) then date
        stmt = select(Creative).filter_by(tenant_id=tenant_id).order_by(Creative.status, Creative.created_at.desc())
        creatives = db_session.scalars(stmt).all()

        # Build creative data with context
        creative_list = []
        for creative in creatives:
            # Get principal name
            stmt = select(Principal).filter_by(tenant_id=tenant_id, principal_id=creative.principal_id)
            principal = db_session.scalars(stmt).first()
            principal_name = principal.name if principal else creative.principal_id

            # Get all media buy assignments for this creative
            stmt = select(CreativeAssignment).filter_by(tenant_id=tenant_id, creative_id=creative.creative_id)
            assignments = db_session.scalars(stmt).all()

            # Get media buy details for each assignment
            media_buys = []
            for assignment in assignments:
                stmt = select(MediaBuy).filter_by(media_buy_id=assignment.media_buy_id)
                media_buy = db_session.scalars(stmt).first()
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
                stmt = select(MediaBuy).filter_by(media_buy_id=media_buys[0]["media_buy_id"])
                first_buy = db_session.scalars(stmt).first()
                if first_buy and first_buy.raw_request:
                    packages = first_buy.raw_request.get("packages", [])
                    if packages:
                        product_id = packages[0].get("product_id")
                        if product_id:
                            stmt = select(Product).filter_by(product_id=product_id)
                            product = db_session.scalars(stmt).first()
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

    return render_template(
        "creative_management.html",
        tenant_id=tenant_id,
        tenant_name=tenant.name,
        creatives=creative_list,
        has_ai_review=bool(tenant.gemini_api_key and tenant.creative_review_criteria),
        approval_mode=tenant.approval_mode,
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


@creatives_bp.route("/save", methods=["POST"])
@require_tenant_access()
def save(tenant_id, **kwargs):
    """Save a creative format to the database."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "No data provided"}), 400

        format_id = f"fmt_{uuid.uuid4().hex[:8]}"

        with get_db_session() as db_session:
            # Check if format already exists
            stmt = select(CreativeFormat).filter_by(name=data.get("name"), tenant_id=tenant_id)
            existing = db_session.scalars(stmt).first()

            if existing:
                return jsonify({"error": f"Format '{data.get('name')}' already exists"}), 400

            # Create new format
            creative_format = CreativeFormat(
                format_id=format_id,
                tenant_id=tenant_id,
                name=data.get("name"),
                type=data.get("type"),
                description=data.get("description"),
                width=data.get("width"),
                height=data.get("height"),
                duration_seconds=data.get("duration_seconds"),
                max_file_size_kb=data.get("max_file_size_kb"),
                supported_mime_types=json.dumps(data.get("supported_mime_types", [])),
                is_standard=False,
                source_url=data.get("source_url"),
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )

            db_session.add(creative_format)
            db_session.commit()

            return jsonify({"success": True, "format_id": format_id})

    except Exception as e:
        logger.error(f"Error saving creative format: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@creatives_bp.route("/sync-standard", methods=["POST"])
@require_tenant_access()
def sync_standard(tenant_id, **kwargs):
    """Sync standard formats from adcontextprotocol.org."""
    try:
        # This would normally fetch from the protocol site
        # For now, return success with count of 0
        return jsonify({"success": True, "count": 0, "message": "Standard formats sync not yet implemented"})

    except Exception as e:
        logger.error(f"Error syncing standard formats: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@creatives_bp.route("/discover", methods=["POST"])
@require_tenant_access()
def discover(tenant_id, **kwargs):
    """Discover multiple creative formats from a URL."""
    try:
        data = request.get_json()
        url = data.get("url", "").strip()

        if not url:
            return jsonify({"error": "URL is required"}), 400

        # Discover formats from the URL
        formats = discover_creative_formats_from_url(url)

        if not formats:
            return jsonify({"error": "No creative formats found at the URL"}), 404

        return jsonify({"formats": formats})

    except Exception as e:
        logger.error(f"Error discovering formats: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@creatives_bp.route("/save-multiple", methods=["POST"])
@require_tenant_access()
def save_multiple(tenant_id, **kwargs):
    """Save multiple discovered creative formats to the database."""
    try:
        data = request.get_json()
        formats = data.get("formats", [])

        if not formats:
            return jsonify({"error": "No formats provided"}), 400

        saved_count = 0
        skipped_count = 0
        errors = []

        with get_db_session() as db_session:
            for format_data in formats:
                # Check if format already exists
                stmt = select(CreativeFormat).filter_by(name=format_data.get("name"), tenant_id=tenant_id)
                existing = db_session.scalars(stmt).first()

                if existing:
                    skipped_count += 1
                    continue

                try:
                    format_id = f"fmt_{uuid.uuid4().hex[:8]}"
                    creative_format = CreativeFormat(
                        format_id=format_id,
                        tenant_id=tenant_id,
                        name=format_data.get("name"),
                        type=format_data.get("type"),
                        description=format_data.get("description"),
                        width=format_data.get("width"),
                        height=format_data.get("height"),
                        duration_seconds=format_data.get("duration_seconds"),
                        max_file_size_kb=format_data.get("max_file_size_kb"),
                        supported_mime_types=json.dumps(format_data.get("supported_mime_types", [])),
                        is_standard=False,
                        source_url=format_data.get("source_url"),
                        created_at=datetime.now(UTC),
                        updated_at=datetime.now(UTC),
                    )

                    db_session.add(creative_format)
                    saved_count += 1

                except Exception as e:
                    errors.append(f"Error saving {format_data.get('name')}: {str(e)}")

            db_session.commit()

        return jsonify(
            {
                "success": True,
                "saved": saved_count,
                "skipped": skipped_count,
                "errors": errors,
            }
        )

    except Exception as e:
        logger.error(f"Error saving multiple formats: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@creatives_bp.route("/<format_id>", methods=["GET"])
@require_tenant_access()
def get_format(tenant_id, format_id, **kwargs):
    """Get a specific creative format for editing."""
    with get_db_session() as db_session:
        creative_format = db_session.scalars(select(CreativeFormat).filter_by(format_id=format_id)).first()

        if not creative_format:
            return jsonify({"error": "Format not found"}), 404

        # Check access
        if creative_format.tenant_id and creative_format.tenant_id != tenant_id:
            return jsonify({"error": "Access denied"}), 403

        format_data = {
            "format_id": creative_format.format_id,
            "name": creative_format.name,
            "type": creative_format.type,
            "description": creative_format.description,
            "width": creative_format.width,
            "height": creative_format.height,
            "duration_seconds": creative_format.duration_seconds,
            "max_file_size_kb": creative_format.max_file_size_kb,
            "supported_mime_types": json.loads(creative_format.supported_mime_types or "[]"),
            "is_standard": creative_format.is_standard,
            "source_url": creative_format.source_url,
        }

        return jsonify(format_data)


@creatives_bp.route("/<format_id>/edit", methods=["GET"])
@require_tenant_access()
def edit_format(tenant_id, format_id, **kwargs):
    """Display the edit creative format page."""
    with get_db_session() as db_session:
        creative_format = db_session.scalars(select(CreativeFormat).filter_by(format_id=format_id)).first()

        if not creative_format:
            flash("Format not found", "error")
            return redirect(url_for("creatives.index", tenant_id=tenant_id))

        # Check access
        if creative_format.tenant_id and creative_format.tenant_id != tenant_id:
            flash("Access denied", "error")
            return redirect(url_for("creatives.index", tenant_id=tenant_id))

        # Prepare format data for template
        format_data = {
            "format_id": creative_format.format_id,
            "name": creative_format.name,
            "type": creative_format.type,
            "description": creative_format.description,
            "width": creative_format.width,
            "height": creative_format.height,
            "duration_seconds": creative_format.duration_seconds,
            "max_file_size_kb": creative_format.max_file_size_kb,
            "supported_mime_types": json.loads(creative_format.supported_mime_types or "[]"),
            "is_standard": creative_format.is_standard,
            "source_url": creative_format.source_url,
        }

        # Get tenant name
        tenant = db_session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
        tenant_name = tenant.name if tenant else ""

    return render_template(
        "edit_creative_format.html",
        tenant_id=tenant_id,
        tenant_name=tenant_name,
        format=format_data,
    )


@creatives_bp.route("/<format_id>/update", methods=["POST"])
@require_tenant_access()
def update_format(tenant_id, format_id, **kwargs):
    """Update a creative format."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "No data provided"}), 400

        with get_db_session() as db_session:
            creative_format = db_session.scalars(select(CreativeFormat).filter_by(format_id=format_id)).first()

            if not creative_format:
                return jsonify({"error": "Format not found"}), 404

            # Check access
            if creative_format.tenant_id and creative_format.tenant_id != tenant_id:
                return jsonify({"error": "Access denied"}), 403

            # Don't allow editing standard formats
            if creative_format.is_standard:
                return jsonify({"error": "Cannot edit standard formats"}), 403

            # Update fields
            creative_format.name = data.get("name", creative_format.name)
            creative_format.type = data.get("type", creative_format.type)
            creative_format.description = data.get("description", creative_format.description)
            creative_format.width = data.get("width", creative_format.width)
            creative_format.height = data.get("height", creative_format.height)
            creative_format.duration_seconds = data.get("duration_seconds", creative_format.duration_seconds)
            creative_format.max_file_size_kb = data.get("max_file_size_kb", creative_format.max_file_size_kb)
            creative_format.supported_mime_types = json.dumps(data.get("supported_mime_types", []))
            creative_format.updated_at = datetime.now(UTC)

            db_session.commit()

            return jsonify({"success": True})

    except Exception as e:
        logger.error(f"Error updating creative format: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@creatives_bp.route("/<format_id>/delete", methods=["POST"])
@require_tenant_access()
def delete_format(tenant_id, format_id, **kwargs):
    """Delete a creative format."""
    try:
        with get_db_session() as db_session:
            creative_format = db_session.scalars(select(CreativeFormat).filter_by(format_id=format_id)).first()

            if not creative_format:
                return jsonify({"error": "Format not found"}), 404

            # Check access
            if creative_format.tenant_id and creative_format.tenant_id != tenant_id:
                return jsonify({"error": "Access denied"}), 403

            # Don't allow deleting standard formats
            if creative_format.is_standard:
                return jsonify({"error": "Cannot delete standard formats"}), 403

            db_session.delete(creative_format)
            db_session.commit()

            return jsonify({"success": True})

    except Exception as e:
        logger.error(f"Error deleting creative format: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@creatives_bp.route("/review/<creative_id>/approve", methods=["POST"])
@require_tenant_access()
def approve_creative(tenant_id, creative_id, **kwargs):
    """Approve a creative."""
    from src.core.audit_logger import AuditLogger
    from src.core.database.models import Creative, CreativeReview

    try:
        data = request.get_json() or {}
        approved_by = data.get("approved_by", "admin")

        with get_db_session() as db_session:
            stmt = select(Creative).filter_by(tenant_id=tenant_id, creative_id=creative_id)
            creative = db_session.scalars(stmt).first()

            if not creative:
                return jsonify({"error": "Creative not found"}), 404

            # Check if there was a prior AI review that disagreed
            prior_ai_review = None
            stmt = (
                select(CreativeReview)
                .filter_by(creative_id=creative_id, review_type="ai")
                .order_by(CreativeReview.reviewed_at.desc())
                .limit(1)
            )
            prior_ai_review = db_session.scalars(stmt).first()

            # Check if this is a human override (AI recommended reject, human approved)
            is_override = False
            if prior_ai_review and prior_ai_review.ai_decision in ["rejected", "reject"]:
                is_override = True

            # Create human review record
            review_id = f"review_{uuid.uuid4().hex[:12]}"
            human_review = CreativeReview(
                review_id=review_id,
                creative_id=creative_id,
                tenant_id=tenant_id,
                reviewed_at=datetime.now(UTC),
                review_type="human",
                reviewer_email=approved_by,
                ai_decision=None,
                confidence_score=None,
                policy_triggered=None,
                reason="Human approval",
                recommendations=None,
                human_override=is_override,
                final_decision="approved",
            )
            db_session.add(human_review)

            # Update creative status
            creative.status = "approved"
            creative.approved_at = datetime.now(UTC)
            creative.approved_by = approved_by

            db_session.commit()

            # Find webhook_url from workflow step if it exists
            from src.core.database.models import ObjectWorkflowMapping, WorkflowStep

            stmt = select(ObjectWorkflowMapping).filter_by(object_type="creative", object_id=creative_id)
            mapping = db_session.scalars(stmt).first()

            webhook_url = None
            if mapping:
                stmt = select(WorkflowStep).filter_by(step_id=mapping.step_id)
                workflow_step = db_session.scalars(stmt).first()
                if workflow_step and workflow_step.request_data:
                    webhook_url = workflow_step.request_data.get("webhook_url")

            # Call webhook if configured
            if webhook_url:
                creative_data = {
                    "creative_id": creative.creative_id,
                    "name": creative.name,
                    "format": creative.format,
                    "status": "approved",
                    "approved_by": approved_by,
                    "approved_at": creative.approved_at.isoformat(),
                }
                _call_webhook_for_creative_status(webhook_url, creative_id, "approved", creative_data, tenant_id)

            # Send Slack notification if configured
            stmt_tenant = select(Tenant).filter_by(tenant_id=tenant_id)
            tenant = db_session.scalars(stmt_tenant).first()
            if tenant and tenant.slack_webhook_url:
                from src.services.slack_notifier import get_slack_notifier

                tenant_config = {"features": {"slack_webhook_url": tenant.slack_webhook_url}}
                notifier = get_slack_notifier(tenant_config)

                # Get principal name
                from src.core.database.models import Principal

                stmt_principal = select(Principal).filter_by(tenant_id=tenant_id, principal_id=creative.principal_id)
                principal = db_session.scalars(stmt_principal).first()
                principal_name = principal.name if principal else creative.principal_id

                notifier.send_message(
                    f"✅ Creative approved: {creative.name} ({creative.format}) from {principal_name}"
                )

            # Log audit trail
            audit_logger = AuditLogger(adapter_name="AdminUI", tenant_id=tenant_id)
            audit_logger.log_operation(
                operation="approve_creative",
                principal_name=approved_by,
                principal_id=approved_by,
                adapter_id="admin_ui",
                success=True,
                details={
                    "creative_id": creative_id,
                    "creative_name": creative.name,
                    "format": creative.format,
                    "principal_id": creative.principal_id,
                    "human_override": is_override,
                },
                tenant_id=tenant_id,
            )

            return jsonify({"success": True, "status": "approved"})

    except Exception as e:
        logger.error(f"Error approving creative: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@creatives_bp.route("/review/<creative_id>/reject", methods=["POST"])
@require_tenant_access()
def reject_creative(tenant_id, creative_id, **kwargs):
    """Reject a creative with comments."""
    from src.core.audit_logger import AuditLogger
    from src.core.database.models import Creative, CreativeReview

    try:
        data = request.get_json() or {}
        rejected_by = data.get("rejected_by", "admin")
        rejection_reason = data.get("rejection_reason", "")

        if not rejection_reason:
            return jsonify({"error": "Rejection reason is required"}), 400

        with get_db_session() as db_session:
            stmt = select(Creative).filter_by(tenant_id=tenant_id, creative_id=creative_id)
            creative = db_session.scalars(stmt).first()

            if not creative:
                return jsonify({"error": "Creative not found"}), 404

            # Check if there was a prior AI review that disagreed
            prior_ai_review = None
            stmt = (
                select(CreativeReview)
                .filter_by(creative_id=creative_id, review_type="ai")
                .order_by(CreativeReview.reviewed_at.desc())
                .limit(1)
            )
            prior_ai_review = db_session.scalars(stmt).first()

            # Check if this is a human override (AI recommended approve, human rejected)
            is_override = False
            if prior_ai_review and prior_ai_review.ai_decision in ["approved", "approve"]:
                is_override = True

            # Create human review record
            review_id = f"review_{uuid.uuid4().hex[:12]}"
            human_review = CreativeReview(
                review_id=review_id,
                creative_id=creative_id,
                tenant_id=tenant_id,
                reviewed_at=datetime.now(UTC),
                review_type="human",
                reviewer_email=rejected_by,
                ai_decision=None,
                confidence_score=None,
                policy_triggered=None,
                reason=rejection_reason,
                recommendations=None,
                human_override=is_override,
                final_decision="rejected",
            )
            db_session.add(human_review)

            # Update creative status
            creative.status = "rejected"
            creative.approved_at = datetime.now(UTC)
            creative.approved_by = rejected_by

            # Store rejection reason in data field
            if not creative.data:
                creative.data = {}
            creative.data["rejection_reason"] = rejection_reason
            creative.data["rejected_at"] = datetime.now(UTC).isoformat()

            # Mark data field as modified for JSONB update
            from sqlalchemy.orm import attributes

            attributes.flag_modified(creative, "data")

            db_session.commit()

            # Find webhook_url from workflow step if it exists
            from src.core.database.models import ObjectWorkflowMapping, WorkflowStep

            stmt = select(ObjectWorkflowMapping).filter_by(object_type="creative", object_id=creative_id)
            mapping = db_session.scalars(stmt).first()

            webhook_url = None
            if mapping:
                stmt = select(WorkflowStep).filter_by(step_id=mapping.step_id)
                workflow_step = db_session.scalars(stmt).first()
                if workflow_step and workflow_step.request_data:
                    webhook_url = workflow_step.request_data.get("webhook_url")

            # Call webhook if configured
            if webhook_url:
                creative_data = {
                    "creative_id": creative.creative_id,
                    "name": creative.name,
                    "format": creative.format,
                    "status": "rejected",
                    "rejected_by": rejected_by,
                    "rejection_reason": rejection_reason,
                    "rejected_at": creative.data["rejected_at"],
                }
                _call_webhook_for_creative_status(webhook_url, creative_id, "rejected", creative_data, tenant_id)

            # Send Slack notification if configured
            stmt_tenant = select(Tenant).filter_by(tenant_id=tenant_id)
            tenant = db_session.scalars(stmt_tenant).first()
            if tenant and tenant.slack_webhook_url:
                from src.services.slack_notifier import get_slack_notifier

                tenant_config = {"features": {"slack_webhook_url": tenant.slack_webhook_url}}
                notifier = get_slack_notifier(tenant_config)

                # Get principal name
                from src.core.database.models import Principal

                stmt_principal = select(Principal).filter_by(tenant_id=tenant_id, principal_id=creative.principal_id)
                principal = db_session.scalars(stmt_principal).first()
                principal_name = principal.name if principal else creative.principal_id

                notifier.send_message(
                    f"❌ Creative rejected: {creative.name} ({creative.format}) from {principal_name}\nReason: {rejection_reason}"
                )

            # Log audit trail
            audit_logger = AuditLogger(adapter_name="AdminUI", tenant_id=tenant_id)
            audit_logger.log_operation(
                operation="reject_creative",
                principal_name=rejected_by,
                principal_id=rejected_by,
                adapter_id="admin_ui",
                success=True,
                details={
                    "creative_id": creative_id,
                    "creative_name": creative.name,
                    "format": creative.format,
                    "principal_id": creative.principal_id,
                    "rejection_reason": rejection_reason,
                    "human_override": is_override,
                },
                tenant_id=tenant_id,
            )

            return jsonify({"success": True, "status": "rejected"})

    except Exception as e:
        logger.error(f"Error rejecting creative: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


def _ai_review_creative_async(
    creative_id: str,
    tenant_id: str,
    webhook_url: str | None = None,
    slack_webhook_url: str | None = None,
    principal_name: str | None = None,
):
    """Background task to review creative with AI (thread-safe).

    This function runs in a background thread and:
    1. Creates its own database session (thread-safe)
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

    # Get fresh DB session (thread-safe - each thread gets its own)
    try:
        with get_db_session() as session:
            # Run AI review
            ai_result = _ai_review_creative_impl(
                tenant_id=tenant_id, creative_id=creative_id, db_session=session, promoted_offering=None
            )

            logger.info(f"[AI Review Async] Review completed for {creative_id}: {ai_result['status']}")

            # Update creative status in database
            from src.core.database.models import Creative

            stmt = select(Creative).filter_by(tenant_id=tenant_id, creative_id=creative_id)
            creative = session.scalars(stmt).first()

            if creative:
                creative.status = ai_result["status"]

                # Store AI reasoning in creative data
                if not isinstance(creative.data, dict):
                    creative.data = {}
                creative.data["ai_review"] = {
                    "decision": ai_result["status"],
                    "reason": ai_result.get("reason", ""),
                    "ai_reason": ai_result.get("ai_reason"),  # Actual AI reasoning (if different from summary)
                    "ai_recommendation": ai_result.get("ai_recommendation"),  # AI's original recommendation
                    "confidence": ai_result.get("confidence", "medium"),
                    "reviewed_at": datetime.now(UTC).isoformat(),
                }

                from sqlalchemy.orm import attributes

                attributes.flag_modified(creative, "data")
                session.commit()

                logger.info(f"[AI Review Async] Database updated for {creative_id}: status={ai_result['status']}")

                # Send Slack notification with AI review results if configured
                if slack_webhook_url and principal_name:
                    try:
                        from src.services.slack_notifier import get_slack_notifier

                        tenant_config = {"features": {"slack_webhook_url": slack_webhook_url}}
                        notifier = get_slack_notifier(tenant_config)

                        # Build comprehensive AI review reason
                        ai_review_data = creative.data.get("ai_review", {})
                        ai_review_reason = ai_review_data.get("reason", "")

                        # If there's a separate AI reason (actual AI's reasoning), include it
                        if ai_review_data.get("ai_reason"):
                            ai_review_reason = (
                                f"{ai_review_reason}\n\n*AI's Reasoning:* {ai_review_data.get('ai_reason')}"
                            )

                        # If AI made a different recommendation than final decision, note it
                        if ai_review_data.get("ai_recommendation"):
                            ai_recommendation = ai_review_data.get("ai_recommendation", "").title()
                            ai_review_reason = f"{ai_review_reason}\n\n*AI Recommendation:* {ai_recommendation}"

                        notifier.notify_creative_pending(
                            creative_id=creative_id,  # Use function parameter (str) not ORM attribute
                            principal_name=principal_name,
                            format_type=str(creative.format),  # Cast Column to str for mypy
                            media_buy_id=None,
                            tenant_id=tenant_id,
                            ai_review_reason=ai_review_reason,
                        )
                        logger.info(f"[AI Review Async] Slack notification sent for {creative_id}")
                    except Exception as slack_e:
                        logger.warning(f"[AI Review Async] Failed to send Slack notification: {slack_e}")

                # Call webhook if configured
                if webhook_url:
                    creative_data = {
                        "creative_id": creative.creative_id,
                        "name": creative.name,
                        "format": creative.format,
                        "status": creative.status,
                        "ai_review": creative.data.get("ai_review"),
                    }
                    _call_webhook_for_creative_status(
                        webhook_url, creative_id, creative.status, creative_data, tenant_id
                    )
                    logger.info(f"[AI Review Async] Webhook called for {creative_id}")

            else:
                logger.error(f"[AI Review Async] Creative not found: {creative_id}")

    except Exception as e:
        logger.error(f"[AI Review Async] Error reviewing creative {creative_id}: {e}", exc_info=True)

        # Try to mark creative as pending with error
        try:
            with get_db_session() as session:
                from src.core.database.models import Creative

                stmt = select(Creative).filter_by(tenant_id=tenant_id, creative_id=creative_id)
                creative = session.scalars(stmt).first()

                if creative:
                    creative.status = "pending"
                    if not isinstance(creative.data, dict):
                        creative.data = {}
                    creative.data["ai_review_error"] = {
                        "error": str(e),
                        "timestamp": datetime.now(UTC).isoformat(),
                    }
                    from sqlalchemy.orm import attributes

                    attributes.flag_modified(creative, "data")
                    session.commit()
                    logger.info(f"[AI Review Async] Creative {creative_id} marked as pending due to error")
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


def _create_review_record(db_session, creative_id: str, tenant_id: str, ai_result: dict):
    """Create a CreativeReview record from AI review result.

    Args:
        db_session: Database session
        creative_id: Creative ID
        tenant_id: Tenant ID
        ai_result: Result dict from AI review with keys:
            - status: "approved", "pending", or "rejected"
            - reason: Explanation from AI
            - confidence: "high", "medium", or "low"
            - confidence_score: Float 0.0-1.0
            - policy_triggered: Policy that was triggered
            - ai_recommendation: Optional AI recommendation if different from final
    """
    from src.core.database.models import CreativeReview

    try:
        review_id = f"review_{uuid.uuid4().hex[:12]}"

        review_record = CreativeReview(
            review_id=review_id,
            creative_id=creative_id,
            tenant_id=tenant_id,
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

        db_session.add(review_record)
        db_session.commit()

        logger.debug(f"Created review record {review_id} for creative {creative_id}")

    except Exception as e:
        logger.error(f"Error creating review record for creative {creative_id}: {e}", exc_info=True)
        # Don't fail the review if we can't create the record
        db_session.rollback()


def _ai_review_creative_impl(tenant_id, creative_id, db_session=None, promoted_offering=None):
    """Internal implementation: Run AI review and return dict result.

    Returns dict with keys:
    - status: "approved", "pending", or "rejected"
    - reason: explanation from AI
    - confidence: "high", "medium", or "low"
    - error: error message if failed
    """
    import time

    from sqlalchemy import select

    from src.core.database.models import Creative
    from src.core.metrics import (
        active_ai_reviews,
        ai_review_confidence,
        ai_review_duration,
        ai_review_errors,
        ai_review_total,
    )

    start_time = time.time()
    active_ai_reviews.labels(tenant_id=tenant_id).inc()

    try:
        # Use provided session or create new one
        should_close = False
        if db_session is None:
            db_session = get_db_session().__enter__()
            should_close = True

        try:
            stmt = select(Tenant).filter_by(tenant_id=tenant_id)
            tenant = db_session.scalars(stmt).first()
            if not tenant:
                return {"status": "pending", "error": "Tenant not found", "reason": "Configuration error"}

            if not tenant.gemini_api_key:
                return {
                    "status": "pending",
                    "error": "Gemini API key not configured",
                    "reason": "AI review unavailable - requires manual approval",
                }

            if not tenant.creative_review_criteria:
                return {
                    "status": "pending",
                    "error": "Creative review criteria not configured",
                    "reason": "AI review unavailable - requires manual approval",
                }

            stmt = select(Creative).filter_by(tenant_id=tenant_id, creative_id=creative_id)
            creative = db_session.scalars(stmt).first()

            if not creative:
                return {"status": "pending", "error": "Creative not found", "reason": "Configuration error"}

            # Get media buy and promoted offering if not provided
            if promoted_offering is None:
                promoted_offering = "Unknown"
                if creative.data.get("media_buy_id"):
                    from src.core.database.models import MediaBuy, Product

                    stmt = select(MediaBuy).filter_by(media_buy_id=creative.data["media_buy_id"])
                    media_buy = db_session.scalars(stmt).first()
                    if media_buy and media_buy.raw_request:
                        packages = media_buy.raw_request.get("packages", [])
                        if packages:
                            product_id = packages[0].get("product_id")
                            if product_id:
                                stmt = select(Product).filter_by(product_id=product_id)
                                product = db_session.scalars(stmt).first()
                                if product:
                                    promoted_offering = product.name

            # Build review prompt with three-state instructions
            review_prompt = f"""You are reviewing a creative asset for approval.

Review Criteria:
{tenant.creative_review_criteria}

Creative Details:
- Name: {creative.name}
- Format: {creative.format}
- Promoted Offering: {promoted_offering}
- Creative Data: {json.dumps(creative.data, indent=2)}

Based on the review criteria, determine the appropriate action for this creative.
You MUST respond with one of three decisions:
- APPROVE: Creative clearly meets all criteria
- REQUIRE HUMAN APPROVAL: Unsure or needs human judgment
- REJECT: Creative clearly violates criteria

Respond with a JSON object containing:
{{
    "decision": "APPROVE" or "REQUIRE HUMAN APPROVAL" or "REJECT",
    "reason": "brief explanation of the decision",
    "confidence": "high/medium/low"
}}
"""

            # Call Gemini API
            import google.generativeai as genai

            genai.configure(api_key=tenant.gemini_api_key)
            model = genai.GenerativeModel("gemini-2.5-flash-lite")

            response = model.generate_content(review_prompt)
            response_text = response.text.strip()

            # Parse JSON response
            if response_text.startswith("```json"):
                response_text = response_text[7:]
            if response_text.endswith("```"):
                response_text = response_text[:-3]
            response_text = response_text.strip()

            review_result = json.loads(response_text)

            # Parse confidence as float (map string values to numeric)
            confidence_str = review_result.get("confidence", "medium").lower()
            confidence_map = {"low": 0.3, "medium": 0.6, "high": 0.9}
            confidence_score = confidence_map.get(confidence_str, 0.6)

            # Get AI policy from tenant (with defaults)
            ai_policy_data = tenant.ai_policy if tenant.ai_policy else {}
            # Thresholds represent MINIMUM confidence required for automatic action
            auto_approve_threshold = ai_policy_data.get("auto_approve_threshold", 0.90)  # Need 90%+ to auto-approve
            auto_reject_threshold = ai_policy_data.get("auto_reject_threshold", 0.90)  # Need 90%+ to auto-reject
            sensitive_categories = ai_policy_data.get(
                "always_require_human_for", ["political", "healthcare", "financial"]
            )

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
                    "status": "pending",
                    "reason": f"Category '{creative_category}' requires human review per policy",
                    "confidence": confidence_str,
                    "confidence_score": confidence_score,
                    "policy_triggered": "sensitive_category",
                }
                _create_review_record(
                    db_session,
                    creative_id,
                    tenant_id,
                    result_dict,
                )
                # Record metrics
                ai_review_total.labels(
                    tenant_id=tenant_id, decision="pending", policy_triggered="sensitive_category"
                ).inc()
                ai_review_confidence.labels(tenant_id=tenant_id, decision="pending").observe(confidence_score)
                return result_dict

            # Apply confidence-based thresholds
            decision = review_result.get("decision", "REQUIRE HUMAN APPROVAL").upper()

            if "APPROVE" in decision and "REQUIRE" not in decision:
                # AI wants to approve - check confidence threshold
                if confidence_score >= auto_approve_threshold:
                    result_dict = {
                        "status": "approved",
                        "reason": review_result.get("reason", ""),
                        "confidence": confidence_str,
                        "confidence_score": confidence_score,
                        "policy_triggered": "auto_approve",
                    }
                    _create_review_record(
                        db_session,
                        creative_id,
                        tenant_id,
                        result_dict,
                    )
                    # Record metrics
                    ai_review_total.labels(
                        tenant_id=tenant_id, decision="approved", policy_triggered="auto_approve"
                    ).inc()
                    ai_review_confidence.labels(tenant_id=tenant_id, decision="approved").observe(confidence_score)
                    return result_dict
                else:
                    result_dict = {
                        "status": "pending",
                        "reason": f"AI recommended approval with {confidence_score:.0%} confidence (below {auto_approve_threshold:.0%} threshold). Human review recommended.",
                        "confidence": confidence_str,
                        "confidence_score": confidence_score,
                        "policy_triggered": "low_confidence_approval",
                        "ai_recommendation": "approve",
                        "ai_reason": review_result.get("reason", ""),
                    }
                    _create_review_record(
                        db_session,
                        creative_id,
                        tenant_id,
                        result_dict,
                    )
                    # Record metrics
                    ai_review_total.labels(
                        tenant_id=tenant_id, decision="pending", policy_triggered="low_confidence_approval"
                    ).inc()
                    ai_review_confidence.labels(tenant_id=tenant_id, decision="pending").observe(confidence_score)
                    return result_dict

            elif "REJECT" in decision:
                # AI wants to reject - check confidence threshold
                if confidence_score >= auto_reject_threshold:
                    result_dict = {
                        "status": "rejected",
                        "reason": review_result.get("reason", ""),
                        "confidence": confidence_str,
                        "confidence_score": confidence_score,
                        "policy_triggered": "auto_reject",
                    }
                    _create_review_record(
                        db_session,
                        creative_id,
                        tenant_id,
                        result_dict,
                    )
                    # Record metrics
                    ai_review_total.labels(
                        tenant_id=tenant_id, decision="rejected", policy_triggered="auto_reject"
                    ).inc()
                    ai_review_confidence.labels(tenant_id=tenant_id, decision="rejected").observe(confidence_score)
                    return result_dict
                else:
                    result_dict = {
                        "status": "pending",
                        "reason": f"AI recommended rejection with {confidence_score:.0%} confidence (below {auto_reject_threshold:.0%} threshold). Human review recommended.",
                        "confidence": confidence_str,
                        "confidence_score": confidence_score,
                        "policy_triggered": "uncertain_rejection",
                        "ai_recommendation": "reject",
                        "ai_reason": review_result.get("reason", ""),
                    }
                    _create_review_record(
                        db_session,
                        creative_id,
                        tenant_id,
                        result_dict,
                    )
                    # Record metrics
                    ai_review_total.labels(
                        tenant_id=tenant_id, decision="pending", policy_triggered="uncertain_rejection"
                    ).inc()
                    ai_review_confidence.labels(tenant_id=tenant_id, decision="pending").observe(confidence_score)
                    return result_dict

            # Default: uncertain or "REQUIRE HUMAN APPROVAL"
            result_dict = {
                "status": "pending",
                "reason": "AI could not make confident decision. Human review required.",
                "confidence": confidence_str,
                "confidence_score": confidence_score,
                "policy_triggered": "uncertain",
                "ai_reason": review_result.get("reason", ""),
            }
            _create_review_record(
                db_session,
                creative_id,
                tenant_id,
                result_dict,
            )
            # Record metrics
            ai_review_total.labels(tenant_id=tenant_id, decision="pending", policy_triggered="uncertain").inc()
            ai_review_confidence.labels(tenant_id=tenant_id, decision="pending").observe(confidence_score)
            return result_dict

        finally:
            if should_close:
                db_session.close()

    except Exception as e:
        logger.error(f"Error running AI review: {e}", exc_info=True)
        # Record error metrics
        ai_review_errors.labels(tenant_id=tenant_id, error_type=type(e).__name__).inc()
        return {"status": "pending", "error": str(e), "reason": "AI review failed - requires manual approval"}
    finally:
        # Record duration and decrement active reviews
        duration = time.time() - start_time
        ai_review_duration.labels(tenant_id=tenant_id).observe(duration)
        active_ai_reviews.labels(tenant_id=tenant_id).dec()


@creatives_bp.route("/review/<creative_id>/ai-review", methods=["POST"])
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
