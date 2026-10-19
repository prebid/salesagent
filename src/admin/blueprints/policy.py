"""Policy management blueprint."""

import json
import logging
from typing import Any

from flask import Blueprint, jsonify, redirect, render_template, request, session, url_for
from sqlalchemy import select

from src.admin.utils import get_tenant_config_from_db, require_auth
from src.admin.utils.audit_decorator import log_admin_action
from src.core.audit_logger import AuditLogger
from src.core.database.database_session import get_db_session
from src.core.database.models import AuditLog, Context, Tenant, WorkflowStep

logger = logging.getLogger(__name__)

# Create blueprint
policy_bp = Blueprint("policy", __name__)


DEFAULT_PROHIBITED_CATEGORIES = [
    "illegal_content",
    "hate_speech",
    "violence",
    "adult_content",
    "misleading_health_claims",
    "financial_scams",
]
DEFAULT_PROHIBITED_TACTICS = [
    "targeting_children_under_13",
    "discriminatory_targeting",
    "deceptive_claims",
    "impersonation",
    "privacy_violations",
]


def _default_policies() -> dict[str, Any]:
    """Baseline policy every publisher starts with (same shape as ``tenant.advertising_policy``)."""
    return {
        "enabled": True,
        "require_manual_review": False,
        "default_prohibited_categories": list(DEFAULT_PROHIBITED_CATEGORIES),
        "default_prohibited_tactics": list(DEFAULT_PROHIBITED_TACTICS),
        "prohibited_advertisers": [],
        "prohibited_categories": [],
        "prohibited_tactics": [],
    }


def _current_advertising_policy(tenant: Tenant) -> dict[str, Any]:
    """``tenant.advertising_policy`` as a dict (the column is JSON; legacy rows may hold a string)."""
    value = tenant.advertising_policy
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            value = None
    return dict(value) if isinstance(value, dict) else {}


@policy_bp.route("/", methods=["GET"])
@require_auth()
def index(tenant_id):
    """View and manage policy settings for the tenant."""
    # Check access
    if session.get("role") == "viewer":
        return "Access denied", 403

    if session.get("role") == "tenant_admin" and session.get("tenant_id") != tenant_id:
        return "Access denied", 403

    with get_db_session() as db_session:
        # Get tenant info
        tenant = db_session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
        if not tenant:
            return "Tenant not found", 404

        tenant_name = tenant.name

        # Get tenant config using helper function
        config = get_tenant_config_from_db(tenant_id)
        if not config:
            return "Tenant config not found", 404

        # The page edits ``tenant.advertising_policy`` — the column ``get_products``
        # enforces and ``list_authorized_properties`` publishes to buyers. It used to
        # read/write ``policy_settings``, whose validator (PolicySettingsModel) has
        # none of these keys and silently dropped every list on save.
        policy_settings = _default_policies()
        policy_settings.update(_current_advertising_policy(tenant))

        # Get recent policy checks from audit log
        stmt = (
            select(AuditLog)
            .filter_by(tenant_id=tenant_id, operation="policy_check")
            .order_by(AuditLog.timestamp.desc())
            .limit(20)
        )
        audit_logs = db_session.scalars(stmt).all()

        recent_checks = []
        for log in audit_logs:
            details = log.details or {}
            recent_checks.append(
                {
                    "timestamp": log.timestamp,
                    "principal_id": log.principal_id,
                    "success": log.success,
                    "status": details.get("policy_status", "unknown"),
                    "brief": details.get("brief", ""),
                    "reason": details.get("reason", ""),
                }
            )

        # Get pending policy review tasks
        # Query workflow steps instead of tasks (tasks table was removed)
        # WorkflowStep has no tenant_id column — must join through Context for tenant isolation
        from src.core.database.models import Context as DBContext

        pending_reviews = []
        try:
            stmt = (
                select(WorkflowStep)
                .join(DBContext)
                .where(
                    DBContext.tenant_id == tenant_id,
                    WorkflowStep.step_type == "policy_review",
                    WorkflowStep.status == "pending",
                )
                .order_by(WorkflowStep.created_at.desc())
            )
            workflow_steps = db_session.scalars(stmt).all()

            for step in workflow_steps:
                request_data = step.request_data or {}
                pending_reviews.append(
                    {
                        "task_id": step.step_id,
                        "created_at": step.created_at,
                        "brief": request_data.get("brief", ""),
                        "advertiser": request_data.get("promoted_offering", ""),
                    }
                )
        except Exception:
            # WorkflowStep table might not exist in fresh databases
            logger.debug("Could not load workflow steps (table may not exist)", exc_info=True)

    return render_template(
        "policy_settings_comprehensive.html",
        tenant_id=tenant_id,
        tenant_name=tenant_name,
        policy_settings=policy_settings,
        recent_checks=recent_checks,
        pending_reviews=pending_reviews,
    )


@policy_bp.route("/update", methods=["POST"])
@require_auth()
@log_admin_action("update_policy")
def update(tenant_id):
    """Update policy settings for the tenant."""
    # Check access - only admins can update policy
    if session.get("role") not in ["super_admin", "tenant_admin"]:
        return "Access denied", 403

    if session.get("role") == "tenant_admin" and session.get("tenant_id") != tenant_id:
        return "Access denied", 403

    try:
        # Parse the form data for lists
        def parse_textarea_lines(field_name):
            """Parse textarea input into list of non-empty lines."""
            text = request.form.get(field_name, "")
            return [line.strip() for line in text.strip().split("\n") if line.strip()]

        with get_db_session() as db_session:
            tenant = db_session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
            if not tenant:
                return jsonify({"error": "Tenant not found"}), 404

            # Merge over the stored policy so keys this form does not carry
            # (``description``, baseline lists edited on the Settings page) survive.
            advertising_policy = _current_advertising_policy(tenant)
            defaults = _default_policies()
            advertising_policy.update(
                {
                    "enabled": request.form.get("enabled") == "on",
                    "require_manual_review": request.form.get("require_manual_review") == "on",
                    "prohibited_advertisers": parse_textarea_lines("prohibited_advertisers"),
                    "prohibited_categories": parse_textarea_lines("prohibited_categories"),
                    "prohibited_tactics": parse_textarea_lines("prohibited_tactics"),
                    # Baseline lists are read-only here; keep whatever is stored.
                    "default_prohibited_categories": advertising_policy.get(
                        "default_prohibited_categories", defaults["default_prohibited_categories"]
                    ),
                    "default_prohibited_tactics": advertising_policy.get(
                        "default_prohibited_tactics", defaults["default_prohibited_tactics"]
                    ),
                }
            )
            tenant.advertising_policy = advertising_policy
            db_session.commit()

        return redirect(url_for("policy.index", tenant_id=tenant_id))

    except Exception as e:
        return f"Error: {e}", 400


@policy_bp.route("/rules", methods=["GET", "POST"])
@require_auth()
def rules(tenant_id):
    """Redirect old policy rules URL to new comprehensive policy settings page."""
    return redirect(url_for("policy.index", tenant_id=tenant_id))


@policy_bp.route("/review/<task_id>", methods=["GET", "POST"])
@require_auth()
@log_admin_action("review_policy_task")
def review_task(tenant_id, task_id):
    """Review and approve/reject a policy review task."""
    # Check access
    if session.get("role") == "viewer":
        return "Access denied", 403

    if session.get("role") == "tenant_admin" and session.get("tenant_id") != tenant_id:
        return "Access denied", 403

    with get_db_session() as db_session:
        if request.method == "POST":
            # Handle approval/rejection
            action = request.form.get("action")
            notes = request.form.get("notes", "")

            try:
                # Get the workflow step (tenant-scoped via Context join)
                stmt = (
                    select(WorkflowStep)
                    .join(Context, WorkflowStep.context_id == Context.context_id)
                    .filter(Context.tenant_id == tenant_id, WorkflowStep.step_id == task_id)
                )
                step = db_session.scalars(stmt).first()

                if not step:
                    return "Task not found", 404

                # Update status based on action
                if action == "approve":
                    step.status = "completed"
                    step.response_data = {"approved": True, "notes": notes}
                elif action == "reject":
                    step.status = "failed"
                    step.response_data = {"approved": False, "notes": notes}

                db_session.commit()

                # Log the action
                audit_logger = AuditLogger()
                audit_logger.log(
                    tenant_id=tenant_id,
                    operation="policy_review",
                    principal_id=session.get("user"),
                    success=True,
                    details={"task_id": task_id, "action": action, "notes": notes},
                )

                return redirect(url_for("policy.index", tenant_id=tenant_id))

            except Exception as e:
                logger.error(f"Error updating policy task: {e}")
                return f"Error: {e}", 500

        # GET request - show review form
        try:
            stmt = (
                select(WorkflowStep)
                .join(Context, WorkflowStep.context_id == Context.context_id)
                .filter(Context.tenant_id == tenant_id, WorkflowStep.step_id == task_id)
            )
            step = db_session.scalars(stmt).first()

            if not step:
                return "Task not found", 404

            request_data = step.request_data or {}

            return render_template(
                "policy_review.html",
                tenant_id=tenant_id,
                task_id=task_id,
                task_details=request_data,
                created_at=step.created_at,
            )

        except Exception as e:
            logger.error(f"Error loading policy task: {e}")
            return f"Error: {e}", 500
