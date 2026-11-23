"""User management blueprint for admin UI."""

import logging
from datetime import UTC, datetime

from flask import Blueprint, flash, jsonify, redirect, render_template, request, session, url_for
from sqlalchemy import select

from src.admin.auth_utils import is_email_shaped
from src.admin.form_validation import json_object_body, required_values
from src.admin.utils import require_tenant_access
from src.admin.utils.audit_decorator import log_admin_action, record_admin_action_failure
from src.core.database.database_session import get_db_session
from src.core.database.integrity import resolve_or_write
from src.core.database.models import Tenant, TenantAuthConfig, User, UserRole
from src.core.database.repositories.tenant_config import UserChange
from src.core.database.repositories.uow import TenantConfigUoW
from src.core.operator_errors import StateConflict, TenantNotFound

logger = logging.getLogger(__name__)

# Create Blueprint
users_bp = Blueprint("users", __name__, url_prefix="/tenant/<tenant_id>/users")


class SSOSignInRequired(StateConflict):
    """Setup mode is disabled only by an operator signed in through SSO, so the tenant cannot lock itself out."""

    MESSAGE = (
        "You must be logged in via SSO to disable setup mode. "
        "Log out and log back in using 'Sign in with SSO' to verify SSO works."
    )


class DomainAlreadyAuthorized(StateConflict):
    """The tenant already authorizes the domain the request adds."""

    MESSAGE = "Domain already exists"


class SSONotEnabled(StateConflict):
    """Setup mode is disabled only once the tenant's SSO is configured and enabled."""

    MESSAGE = "SSO must be configured and enabled before disabling setup mode"


# What each role may do, for the Users page's role pickers. One entry per UserRole,
# least access first; tests/unit/test_admin_role_permits.py checks it covers them all.
ROLE_DESCRIPTIONS: dict[UserRole, str] = {
    UserRole.VIEWER: "read-only",
    UserRole.MANAGER: "can change settings, products and campaigns",
    UserRole.ADMIN: "can also manage users and sign-in",
}


def _flash_user_change(change: UserChange, success: str) -> None:
    """Tell the operator how a role or active-flag change went, once its transaction has committed.

    A refusal to demote or deactivate the last active admin is also recorded as a failed
    admin action; the handler returns normally, so the audit log would otherwise call it
    a success.
    """
    if change.outcome == "missing_user":
        flash("User not found", "error")
    elif change.outcome == "last_admin":
        message = f"{change.email} is the last active admin of this tenant. Make someone else an admin first."
        flash(message, "error")
        record_admin_action_failure(message)
    else:
        flash(success, "success")


@users_bp.route("")
@require_tenant_access()
def list_users(tenant_id):
    """List users for a tenant."""
    with get_db_session() as db_session:
        tenant = db_session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
        if not tenant:
            flash("Tenant not found", "error")
            return redirect(url_for("core.index"))

        stmt = select(User).filter_by(tenant_id=tenant_id).order_by(User.email)
        users = db_session.scalars(stmt).all()

        users_list = []
        for user in users:
            users_list.append(
                {
                    "user_id": user.user_id,
                    "email": user.email,
                    "name": user.name,
                    "role": user.role,
                    "is_active": user.is_active,
                    "created_at": user.created_at,
                    "last_login": user.last_login,
                }
            )

        # Get authorized domains
        authorized_domains = tenant.authorized_domains or []

        # Get auth config to check if SSO is enabled
        auth_config = db_session.scalars(select(TenantAuthConfig).filter_by(tenant_id=tenant_id)).first()
        oidc_enabled = auth_config.oidc_enabled if auth_config else False
        logger.info(
            f"list_users: tenant={tenant_id}, oidc_enabled={oidc_enabled}, auth_setup_mode={tenant.auth_setup_mode}"
        )

        return render_template(
            "users.html",
            tenant=tenant,
            tenant_id=tenant_id,
            tenant_name=tenant.name,
            users=users_list,
            authorized_domains=authorized_domains,
            auth_setup_mode=tenant.auth_setup_mode,
            oidc_enabled=oidc_enabled,
            role_descriptions=ROLE_DESCRIPTIONS,
        )


@users_bp.route("/add", methods=["POST"])
@require_tenant_access(role=UserRole.ADMIN)
@log_admin_action(
    "add_user", extract_details=lambda r, **kw: {"email": request.form.get("email"), "role": request.form.get("role")}
)
def add_user(tenant_id):
    """Add a new user to the tenant."""
    email = request.form.get("email", "").strip().lower()
    role = request.form.get("role", UserRole.VIEWER)

    if not email:
        flash("Email is required", "error")
        return redirect(url_for("users.list_users", tenant_id=tenant_id))

    if role not in UserRole:
        flash("Invalid role", "error")
        return redirect(url_for("users.list_users", tenant_id=tenant_id))

    # Validate email format
    if not is_email_shaped(email):
        flash("Invalid email format", "error")
        return redirect(url_for("users.list_users", tenant_id=tenant_id))

    with get_db_session() as db_session:
        # Check if user already exists. uq_users_tenant_email is the authority,
        # so this same answer serves the loser of a concurrent add.
        existing_user_stmt = select(User).filter_by(tenant_id=tenant_id, email=email)

        def already_exists():
            existing = db_session.scalars(existing_user_stmt).first()
            if existing:
                flash(f"User {email} already exists", "error")
                return redirect(url_for("users.list_users", tenant_id=tenant_id))
            return None

        # Create new user
        import uuid

        # Use provided name or default to email username
        name = request.form.get("name", "").strip() or email.split("@")[0]

        user = User(
            tenant_id=tenant_id,
            user_id=f"user_{uuid.uuid4().hex[:8]}",
            email=email,
            name=name,
            role=role,
            is_active=True,
            created_at=datetime.now(UTC),
        )

        conflict = resolve_or_write(
            db_session,
            conflict=already_exists,
            write=lambda: db_session.add(user),
            constraint="uq_users_tenant_email",
        )
        if conflict is not None:
            return conflict
        db_session.commit()

        flash(f"User {email} added successfully", "success")

    return redirect(url_for("users.list_users", tenant_id=tenant_id))


@users_bp.route("/<user_id>/toggle", methods=["POST"])
@log_admin_action("toggle_user")
@require_tenant_access(role=UserRole.ADMIN)
def toggle_user(tenant_id, user_id):
    """Toggle user active status. Refuses to deactivate the tenant's last active admin."""
    with TenantConfigUoW(tenant_id) as uow:
        assert uow.tenant_config is not None
        change = uow.tenant_config.toggle_user_active(user_id)
    _flash_user_change(change, f"User {change.email} {'activated' if change.is_active else 'deactivated'}")

    return redirect(url_for("users.list_users", tenant_id=tenant_id))


@users_bp.route("/<user_id>/update_role", methods=["POST"])
@log_admin_action("update_role")
@require_tenant_access(role=UserRole.ADMIN)
def update_role(tenant_id, user_id):
    """Update user role. Refuses to demote the tenant's last active admin."""
    new_role = request.form.get("role", "")
    if new_role not in UserRole:
        flash("Invalid role", "error")
        return redirect(url_for("users.list_users", tenant_id=tenant_id))

    with TenantConfigUoW(tenant_id) as uow:
        assert uow.tenant_config is not None
        change = uow.tenant_config.set_user_role(user_id, UserRole(new_role))
    _flash_user_change(change, f"User {change.email} role updated to {new_role}")

    return redirect(url_for("users.list_users", tenant_id=tenant_id))


def _requested_domain() -> str:
    """The domain the request's body names, lowercased; refused when it names none."""
    [domain] = required_values(json_object_body(), "domain")
    return str(domain).lower()


@users_bp.route("/domains", methods=["POST"])
@require_tenant_access(role=UserRole.ADMIN)
@log_admin_action("add_domain", extract_details=lambda r, **kw: {"domain": request.json.get("domain")})
def add_domain(tenant_id):
    """Add an authorized domain for the tenant."""
    domain = _requested_domain()

    # Atomic append: the membership check rides in the UPDATE's WHERE
    # clause, so a concurrent add can be neither lost nor duplicated.
    with TenantConfigUoW(tenant_id) as uow:
        assert uow.tenant_config is not None
        outcome = uow.tenant_config.add_to_authorized_list("authorized_domains", domain)

    if outcome == "missing_tenant":
        raise TenantNotFound(f"Tenant {tenant_id}")
    if outcome == "duplicate":
        raise DomainAlreadyAuthorized(f"Tenant {tenant_id} already authorizes {domain}")

    return jsonify({"success": True, "domain": domain})


@users_bp.route("/domains", methods=["DELETE"])
@require_tenant_access(role=UserRole.ADMIN)
@log_admin_action("remove_domain", extract_details=lambda r, **kw: {"domain": request.json.get("domain")})
def remove_domain(tenant_id):
    """Remove an authorized domain from the tenant."""
    domain = _requested_domain()

    with TenantConfigUoW(tenant_id) as uow:
        assert uow.tenant_config is not None
        outcome = uow.tenant_config.remove_from_authorized_list("authorized_domains", domain)

    if outcome == "missing_tenant":
        raise TenantNotFound(f"Tenant {tenant_id}")

    # "removed" and "absent" both answer success — removal is idempotent.
    return jsonify({"success": True})


@users_bp.route("/disable-setup-mode", methods=["POST"])
@require_tenant_access(role=UserRole.ADMIN)
@log_admin_action("disable_auth_setup_mode")
def disable_setup_mode(tenant_id):
    """Disable auth setup mode for the tenant.

    Once disabled, test credentials no longer work and only SSO authentication is allowed.
    Requires the user to be logged in via SSO to prevent lockout.
    """
    # Require the user to be logged in via SSO (not test credentials)
    # This ensures they can actually authenticate via SSO before disabling test auth
    if session.get("auth_method") != "oidc":
        raise SSOSignInRequired(f"Disabling setup mode for tenant {tenant_id} without an SSO sign-in")

    with get_db_session() as db_session:
        tenant = db_session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
        if not tenant:
            raise TenantNotFound(f"Tenant {tenant_id}")

        # Check if SSO is configured and enabled
        auth_config = db_session.scalars(select(TenantAuthConfig).filter_by(tenant_id=tenant_id)).first()

        if not auth_config or not auth_config.oidc_enabled:
            raise SSONotEnabled(f"Disabling setup mode for tenant {tenant_id} with SSO not enabled")

        # Disable setup mode
        tenant.auth_setup_mode = False
        db_session.commit()

        logger.info(f"Auth setup mode disabled for tenant {tenant_id} by user {session.get('user')}")
        return jsonify({"success": True, "message": "Setup mode disabled. Only SSO authentication is now allowed."})


@users_bp.route("/enable-setup-mode", methods=["POST"])
@require_tenant_access(role=UserRole.ADMIN)
@log_admin_action("enable_auth_setup_mode")
def enable_setup_mode(tenant_id):
    """Re-enable auth setup mode for the tenant.

    This allows test credentials to work again, useful for troubleshooting.
    """
    with get_db_session() as db_session:
        tenant = db_session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
        if not tenant:
            raise TenantNotFound(f"Tenant {tenant_id}")

        tenant.auth_setup_mode = True
        db_session.commit()

        logger.info(f"Auth setup mode re-enabled for tenant {tenant_id}")
        return jsonify({"success": True, "message": "Setup mode enabled. Test credentials now work."})
