"""Policy management blueprint."""

import logging

from flask import Blueprint, redirect, render_template, request, url_for

from src.admin.utils import require_tenant_access
from src.admin.utils.audit_decorator import log_admin_action
from src.core.database.models import UserRole
from src.core.database.repositories.uow import TenantConfigUoW, WorkflowUoW
from src.core.operator_errors import OperatorRefusal, RecordNotFound, TenantNotFound
from src.core.schemas.advertising_policy import AdvertisingPolicy
from src.services.ai.factory import tenant_ai_enabled

logger = logging.getLogger(__name__)

# Create blueprint
policy_bp = Blueprint("policy", __name__)

#: The form's starting point for a tenant that has saved no policy: checks off, no
#: prohibitions of its own, and these baseline lists for the operator to edit. Nothing is
#: stored until the operator saves the form.
UNSAVED_POLICY_FORM = AdvertisingPolicy(
    enabled=False,
    require_manual_review=False,
    prohibited_advertisers=[],
    prohibited_categories=[],
    prohibited_tactics=[],
    default_prohibited_categories=[
        "illegal_content",
        "hate_speech",
        "violence",
        "adult_content",
        "misleading_health_claims",
        "financial_scams",
    ],
    default_prohibited_tactics=[
        "targeting_children_under_13",
        "discriminatory_targeting",
        "deceptive_claims",
        "impersonation",
        "privacy_violations",
    ],
    description=None,
)

#: How many of the tenant's latest policy checks the page lists.
RECENT_POLICY_CHECKS = 20


class PolicyReviewNotFound(RecordNotFound):
    """The step is not in this tenant's pending policy review queue (or another reviewer just settled it)."""

    MESSAGE = "Policy review task not found"


class InvalidReviewAction(OperatorRefusal):
    """The review form posted an action other than approve or reject."""

    MESSAGE = "Choose approve or reject"


@policy_bp.route("/", methods=["GET"])
@require_tenant_access()
def index(tenant_id):
    """Show the tenant's saved advertising policy, its recent policy checks and its review queue.

    A tenant with no saved policy sees the form at :data:`UNSAVED_POLICY_FORM` and a notice
    that no policy is saved.
    """
    with TenantConfigUoW(tenant_id) as uow:
        assert uow.tenant_config is not None and uow.audit_logs is not None
        tenant = uow.tenant_config.get_tenant()
        if tenant is None:
            raise TenantNotFound(tenant_id)
        tenant_name = tenant.name
        policy = tenant.advertising_policy
        # Policy checks run only on the seller's own AI configuration (products.py).
        seller_ai_enabled = tenant_ai_enabled(tenant, platform_fallback=False)
        recent_checks = [
            {
                "timestamp": log.timestamp,
                "principal_id": log.principal_id,
                "success": log.success,
                "status": (log.details or {}).get("policy_status", "unknown"),
                "brief": (log.details or {}).get("brief", ""),
                "reason": (log.details or {}).get("reason", ""),
            }
            for log in uow.audit_logs.list_recent("policy_check", RECENT_POLICY_CHECKS)
        ]

    # The pending policy review queue: the same definition review_task settles.
    with WorkflowUoW(tenant_id) as uow:
        assert uow.workflows is not None
        pending_reviews = [
            {
                "task_id": step.step_id,
                "created_at": step.created_at,
                "brief": (step.request_data or {}).get("brief", ""),
                "advertiser": (step.request_data or {}).get("promoted_offering", ""),
            }
            for step in uow.workflows.list_pending_policy_reviews()
        ]

    return render_template(
        "advertising_policy.html",
        tenant_id=tenant_id,
        tenant_name=tenant_name,
        policy_saved=policy is not None,
        policy=policy or UNSAVED_POLICY_FORM,
        seller_ai_enabled=seller_ai_enabled,
        recent_checks=recent_checks,
        pending_reviews=pending_reviews,
    )


def _form_lines(field_name: str) -> list[str]:
    """The non-empty lines of the form's textarea *field_name*, stripped."""
    return [line.strip() for line in request.form.get(field_name, "").splitlines() if line.strip()]


@policy_bp.route("/update", methods=["POST"])
@require_tenant_access(role=UserRole.ADMIN)
@log_admin_action("update_policy")
def update(tenant_id):
    """Save the form as the tenant's complete advertising policy.

    The form edits every key but ``description``, which keeps its stored value.
    """
    with TenantConfigUoW(tenant_id) as uow:
        assert uow.tenant_config is not None
        tenant = uow.tenant_config.get_tenant()
        if tenant is None:
            raise TenantNotFound(tenant_id)
        stored = tenant.advertising_policy
        uow.tenant_config.save_advertising_policy(
            AdvertisingPolicy(
                enabled=request.form.get("enabled") == "on",
                require_manual_review=request.form.get("require_manual_review") == "on",
                prohibited_advertisers=_form_lines("prohibited_advertisers"),
                prohibited_categories=_form_lines("prohibited_categories"),
                prohibited_tactics=_form_lines("prohibited_tactics"),
                default_prohibited_categories=_form_lines("default_prohibited_categories"),
                default_prohibited_tactics=_form_lines("default_prohibited_tactics"),
                description=stored.description if stored is not None else None,
            )
        )

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
