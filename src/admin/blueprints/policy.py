"""Policy management blueprint."""

import json
import logging

from flask import Blueprint, jsonify, redirect, render_template, request, session, url_for
from sqlalchemy import select

from src.admin.utils import get_tenant_config_from_db, require_auth
from src.core.audit_logger import AuditLogger
from src.core.database.database_session import get_db_session
from src.core.database.models import AuditLog, Context, Tenant, WorkflowStep

logger = logging.getLogger(__name__)

# Create blueprint
policy_bp = Blueprint("policy", __name__)


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

        # Define default policies that all publishers start with
        default_policies = {
            "enabled": True,
            "require_manual_review": False,
            "default_prohibited_categories": [
                "illegal_content",
                "hate_speech",
                "violence",
                "adult_content",
                "misleading_health_claims",
                "financial_scams",
            ],
            "default_prohibited_tactics": [
                "targeting_children_under_13",
                "discriminatory_targeting",
                "deceptive_claims",
                "impersonation",
                "privacy_violations",
            ],
            "prohibited_advertisers": [],
            "prohibited_categories": [],
            "prohibited_tactics": [],
        }

        # Get tenant policy settings, using defaults where not specified
        tenant_policies = config.get("policy_settings", {})
        policy_settings = default_policies.copy()
        policy_settings.update(tenant_policies)

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
            details = json.loads(log.details) if log.details else {}
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
        pending_reviews = []
        try:
            stmt = (
                select(WorkflowStep)
                .filter_by(tenant_id=tenant_id, step_type="policy_review", status="pending")
                .order_by(WorkflowStep.created_at.desc())
            )
            workflow_steps = session.scalars(stmt).all()

            for step in workflow_steps:
                details = json.loads(step.data) if step.data else {}
                pending_reviews.append(
                    {
                        "task_id": step.step_id,
                        "created_at": step.created_at,
                        "brief": details.get("brief", ""),
                        "advertiser": details.get("promoted_offering", ""),
                    }
                )
        except:
            # WorkflowStep table might not exist
            pass

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
def update(tenant_id):
    """Update policy settings for the tenant."""
    # Check access - only admins can update policy
    if session.get("role") not in ["super_admin", "tenant_admin"]:
        return "Access denied", 403

    if session.get("role") == "tenant_admin" and session.get("tenant_id") != tenant_id:
        return "Access denied", 403

    try:
        # Get current config
        config = get_tenant_config_from_db(tenant_id)
        if not config:
            return jsonify({"error": "Tenant not found"}), 404

        # Parse the form data for lists
        def parse_textarea_lines(field_name):
            """Parse textarea input into list of non-empty lines."""
            text = request.form.get(field_name, "")
            return [line.strip() for line in text.strip().split("\n") if line.strip()]

        # Update policy settings
        policy_settings = {
            "enabled": request.form.get("enabled") == "on",
            "require_manual_review": request.form.get("require_manual_review") == "on",
            "prohibited_advertisers": parse_textarea_lines("prohibited_advertisers"),
            "prohibited_categories": parse_textarea_lines("prohibited_categories"),
            "prohibited_tactics": parse_textarea_lines("prohibited_tactics"),
            # Keep default policies (they don't change from form)
            "default_prohibited_categories": config.get("policy_settings", {}).get(
                "default_prohibited_categories",
                [
                    "illegal_content",
                    "hate_speech",
                    "violence",
                    "adult_content",
                    "misleading_health_claims",
                    "financial_scams",
                ],
            ),
            "default_prohibited_tactics": config.get("policy_settings", {}).get(
                "default_prohibited_tactics",
                [
                    "targeting_children_under_13",
                    "discriminatory_targeting",
                    "deceptive_claims",
                    "impersonation",
                    "privacy_violations",
                ],
            ),
        }

        config["policy_settings"] = policy_settings

        # Update database
        with get_db_session() as db_session:
            tenant = db_session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
            if tenant:
                tenant.policy_settings = json.dumps(policy_settings)
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
                # Get the workflow step
                stmt = (
                    select(WorkflowStep)
                    .join(Context, WorkflowStep.context_id == Context.context_id)
                    .filter(Context.tenant_id == tenant_id, WorkflowStep.step_id == task_id)
                )
                step = session.scalars(stmt).first()

                if not step:
                    return "Task not found", 404

                # Update status based on action
                if action == "approve":
                    step.status = "completed"
                    step.result = json.dumps({"approved": True, "notes": notes})
                elif action == "reject":
                    step.status = "failed"
                    step.result = json.dumps({"approved": False, "notes": notes})

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
            step = session.scalars(stmt).first()

            if not step:
                return "Task not found", 404

            details = json.loads(step.data) if step.data else {}

            return render_template(
                "policy_review.html",
                tenant_id=tenant_id,
                task_id=task_id,
                task_details=details,
                created_at=step.created_at,
            )

        except Exception as e:
            logger.error(f"Error loading policy task: {e}")
            return f"Error: {e}", 500
