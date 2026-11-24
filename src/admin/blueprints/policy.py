"""Policy management blueprint."""

import logging

from flask import Blueprint, jsonify, redirect, render_template, request, url_for
from sqlalchemy import select

from src.admin.utils import get_tenant_config_from_db, require_tenant_access
from src.admin.utils.audit_decorator import log_admin_action
from src.core.database.database_session import get_db_session
from src.core.database.models import AuditLog, Tenant, UserRole
from src.core.database.repositories.uow import WorkflowUoW
from src.core.database.repositories.workflow import WorkflowRepository
from src.core.json_validators import PolicySettingsModel
from src.core.operator_errors import OperatorRefusal, RecordNotFound

logger = logging.getLogger(__name__)

# Create blueprint
policy_bp = Blueprint("policy", __name__)


class PolicyReviewNotFound(RecordNotFound):
    """The step is not in this tenant's pending policy review queue (or another reviewer just settled it)."""

    MESSAGE = "Policy review task not found"


class InvalidReviewAction(OperatorRefusal):
    """The review form posted an action other than approve or reject."""

    MESSAGE = "Choose approve or reject"


@policy_bp.route("/", methods=["GET"])
@require_tenant_access()
def index(tenant_id):
    """View and manage policy settings for the tenant."""
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

        # The stored policy over the baseline every tenant starts with
        policy_settings = PolicySettingsModel.model_validate(config.get("policy_settings") or {})

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

        # The pending policy review queue: the same definition review_task settles.
        pending_reviews = []
        workflow_steps = WorkflowRepository(db_session, tenant_id).list_pending_policy_reviews()

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

    return render_template(
        "policy_settings_comprehensive.html",
        tenant_id=tenant_id,
        tenant_name=tenant_name,
        policy_settings=policy_settings,
        recent_checks=recent_checks,
        pending_reviews=pending_reviews,
    )


@policy_bp.route("/update", methods=["POST"])
@require_tenant_access(role=UserRole.ADMIN)
@log_admin_action("update_policy")
def update(tenant_id):
    """Update policy settings for the tenant."""
    # Get current config
    config = get_tenant_config_from_db(tenant_id)
    if not config:
        return jsonify({"error": "Tenant not found"}), 404

    # Parse the form data for lists
    def parse_textarea_lines(field_name):
        """Parse textarea input into list of non-empty lines."""
        text = request.form.get(field_name, "")
        return [line.strip() for line in text.strip().split("\n") if line.strip()]

    # The form's keys over the stored row; a key the tenant never saved keeps its default
    policy_settings = {
        **config.get("policy_settings", {}),
        "enabled": request.form.get("enabled") == "on",
        "require_manual_review": request.form.get("require_manual_review") == "on",
        "prohibited_advertisers": parse_textarea_lines("prohibited_advertisers"),
        "prohibited_categories": parse_textarea_lines("prohibited_categories"),
        "prohibited_tactics": parse_textarea_lines("prohibited_tactics"),
    }

    # Update database
    with get_db_session() as db_session:
        tenant = db_session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
        if tenant:
            tenant.policy_settings = policy_settings
            db_session.commit()

    return redirect(url_for("policy.index", tenant_id=tenant_id))


@policy_bp.route("/rules", methods=["GET", "POST"])
@require_tenant_access()
def rules(tenant_id):
    """Redirect old policy rules URL to new comprehensive policy settings page."""
    return redirect(url_for("policy.index", tenant_id=tenant_id))


@policy_bp.route("/review/<task_id>", methods=["GET", "POST"])
@require_tenant_access()
@log_admin_action("review_policy_task", extract_details=lambda r, **kw: {"task_id": kw.get("task_id")})
def review_task(tenant_id, task_id):
    """Review and approve/reject a pending policy review task.

    Only a ``policy_review`` step still ``pending`` is reachable here; any other step of
    the tenant (a media buy or creative approval, an ad server step) answers 404, so this
    route cannot settle a decision that belongs to another workflow. Any user of the tenant
    may read the form; the POST that decides needs a manager (``role_permits``), as the
    media buy, creative and workflow approval routes do.
    """
    with WorkflowUoW(tenant_id) as uow:
        assert uow.workflows is not None
        step = uow.workflows.get_pending_policy_review(task_id)
        if step is None:
            raise PolicyReviewNotFound(task_id)

        if request.method == "POST":
            action = request.form.get("action")
            if action not in ("approve", "reject"):
                raise InvalidReviewAction(action)
            # Conditional on the queue, so a step another reviewer settled meanwhile is not rewritten.
            if not uow.workflows.settle_pending_policy_review(
                task_id, approved=action == "approve", notes=request.form.get("review_notes", "")
            ):
                raise PolicyReviewNotFound(task_id)
            return redirect(url_for("policy.index", tenant_id=tenant_id))

        return render_template(
            "policy_review.html",
            tenant_id=tenant_id,
            task_id=task_id,
            details=step.request_data or {},
            created_at=step.created_at,
        )
