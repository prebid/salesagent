"""User management blueprint for admin UI."""

import json
import logging
from datetime import UTC, datetime

from flask import Blueprint, flash, jsonify, redirect, render_template, request, session, url_for
from sqlalchemy import select

from src.admin.utils import require_tenant_access
from src.admin.utils.audit_decorator import log_admin_action
from src.core.database.database_session import get_db_session
from src.core.database.models import Tenant, TenantAuthConfig, User

logger = logging.getLogger(__name__)

# Create Blueprint
users_bp = Blueprint("users", __name__, url_prefix="/tenant/<tenant_id>/users")


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
        )


@users_bp.route("/add", methods=["POST"])
@require_tenant_access()
@log_admin_action(
    "add_user", extract_details=lambda r, **kw: {"email": request.form.get("email"), "role": request.form.get("role")}
)
def add_user(tenant_id):
    """Add a new user to the tenant."""
    try:
        email = request.form.get("email", "").strip().lower()
        role = request.form.get("role", "viewer")

        if not email:
            flash("Email is required", "error")
            return redirect(url_for("users.list_users", tenant_id=tenant_id))

        # Validate email format
        import re

        if not re.match(r"[^@]+@[^@]+\.[^@]+", email):
            flash("Invalid email format", "error")
            return redirect(url_for("users.list_users", tenant_id=tenant_id))

        with get_db_session() as db_session:
            # Check if user already exists
            existing = db_session.scalars(select(User).filter_by(tenant_id=tenant_id, email=email)).first()
            if existing:
                flash(f"User {email} already exists", "error")
                return redirect(url_for("users.list_users", tenant_id=tenant_id))

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

            db_session.add(user)
            db_session.commit()

            flash(f"User {email} added successfully", "success")

    except Exception as e:
        logger.error(f"Error adding user: {e}", exc_info=True)
        flash(f"Error adding user: {str(e)}", "error")

    return redirect(url_for("users.list_users", tenant_id=tenant_id))


@users_bp.route("/<user_id>/toggle", methods=["POST"])
@log_admin_action("toggle_user")
@require_tenant_access()
def toggle_user(tenant_id, user_id):
    """Toggle user active status."""
    try:
        with get_db_session() as db_session:
            user = db_session.scalars(select(User).filter_by(tenant_id=tenant_id, user_id=user_id)).first()
            if not user:
                flash("User not found", "error")
                return redirect(url_for("users.list_users", tenant_id=tenant_id))

            user.is_active = not user.is_active
            db_session.commit()

            status = "activated" if user.is_active else "deactivated"
            flash(f"User {user.email} {status}", "success")

    except Exception as e:
        logger.error(f"Error toggling user: {e}", exc_info=True)
        flash(f"Error toggling user: {str(e)}", "error")

    return redirect(url_for("users.list_users", tenant_id=tenant_id))


@users_bp.route("/<user_id>/update_role", methods=["POST"])
@log_admin_action("update_role")
@require_tenant_access()
def update_role(tenant_id, user_id):
    """Update user role."""
    try:
        new_role = request.form.get("role")
        if not new_role or new_role not in ["admin", "manager", "viewer"]:
            flash("Invalid role", "error")
            return redirect(url_for("users.list_users", tenant_id=tenant_id))

        with get_db_session() as db_session:
            user = db_session.scalars(select(User).filter_by(tenant_id=tenant_id, user_id=user_id)).first()
            if not user:
                flash("User not found", "error")
                return redirect(url_for("users.list_users", tenant_id=tenant_id))

            user.role = new_role
            db_session.commit()

            flash(f"User {user.email} role updated to {new_role}", "success")

    except Exception as e:
        logger.error(f"Error updating user role: {e}", exc_info=True)
        flash(f"Error updating role: {str(e)}", "error")

    return redirect(url_for("users.list_users", tenant_id=tenant_id))


@users_bp.route("/domains", methods=["POST"])
@require_tenant_access()
@log_admin_action("add_domain", extract_details=lambda r, **kw: {"domain": request.json.get("domain")})
def add_domain(tenant_id):
    """Add an authorized domain for the tenant."""
    try:
        data = request.json
        domain = data.get("domain", "").strip().lower()

        if not domain:
            return jsonify({"success": False, "error": "Domain is required"}), 400

        # Basic domain validation
        if "." not in domain or domain.startswith(".") or domain.endswith("."):
            return jsonify({"success": False, "error": "Invalid domain format"}), 400

        with get_db_session() as db_session:
            tenant = db_session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
            if not tenant:
                return jsonify({"success": False, "error": "Tenant not found"}), 404

            # Get current domains
            domains = tenant.authorized_domains or []
            if isinstance(domains, str):
                domains = json.loads(domains)
            domains = list(domains)

            # Check if already exists
            if domain in domains:
                return jsonify({"success": False, "error": "Domain already exists"}), 400

            # Add domain
            domains.append(domain)
            tenant.authorized_domains = domains
            db_session.commit()

            return jsonify({"success": True, "domain": domain})

    except Exception as e:
        logger.error(f"Error adding domain: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@users_bp.route("/domains", methods=["DELETE"])
@require_tenant_access()
@log_admin_action("remove_domain", extract_details=lambda r, **kw: {"domain": request.json.get("domain")})
def remove_domain(tenant_id):
    """Remove an authorized domain from the tenant."""
    try:
        data = request.json
        domain = data.get("domain", "").strip().lower()

        if not domain:
            return jsonify({"success": False, "error": "Domain is required"}), 400

        with get_db_session() as db_session:
            tenant = db_session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
            if not tenant:
                return jsonify({"success": False, "error": "Tenant not found"}), 404

            # Get current domains
            domains = tenant.authorized_domains or []
            if isinstance(domains, str):
                domains = json.loads(domains)
            domains = list(domains)

            # Remove domain
            if domain in domains:
                domains.remove(domain)
                tenant.authorized_domains = domains
                db_session.commit()

            return jsonify({"success": True})

    except Exception as e:
        logger.error(f"Error removing domain: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@users_bp.route("/disable-setup-mode", methods=["POST"])
@require_tenant_access()
@log_admin_action("disable_auth_setup_mode")
def disable_setup_mode(tenant_id):
    """Disable auth setup mode for the tenant.

    Once disabled, test credentials no longer work and only SSO authentication is allowed.
    Requires the user to be logged in via SSO to prevent lockout.
    """
    try:
        # Require the user to be logged in via SSO (not test credentials)
        # This ensures they can actually authenticate via SSO before disabling test auth
        auth_method = session.get("auth_method")
        if auth_method != "oidc":
            return (
                jsonify(
                    {
                        "success": False,
                        "error": "You must be logged in via SSO to disable setup mode. "
                        "Log out and log back in using 'Sign in with SSO' to verify SSO works.",
                    }
                ),
                403,
            )

        with get_db_session() as db_session:
            tenant = db_session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
            if not tenant:
                return jsonify({"success": False, "error": "Tenant not found"}), 404

            # Check if SSO is configured and enabled
            auth_config = db_session.scalars(select(TenantAuthConfig).filter_by(tenant_id=tenant_id)).first()

            if not auth_config or not auth_config.oidc_enabled:
                return (
                    jsonify(
                        {"success": False, "error": "SSO must be configured and enabled before disabling setup mode"}
                    ),
                    400,
                )

            # Disable setup mode
            tenant.auth_setup_mode = False
            db_session.commit()

            logger.info(f"Auth setup mode disabled for tenant {tenant_id} by user {session.get('user')}")
            return jsonify({"success": True, "message": "Setup mode disabled. Only SSO authentication is now allowed."})

    except Exception as e:
        logger.error(f"Error disabling setup mode: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@users_bp.route("/enable-setup-mode", methods=["POST"])
@require_tenant_access()
@log_admin_action("enable_auth_setup_mode")
def enable_setup_mode(tenant_id):
    """Re-enable auth setup mode for the tenant.

    This allows test credentials to work again, useful for troubleshooting.
    """
    try:
        with get_db_session() as db_session:
            tenant = db_session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
            if not tenant:
                return jsonify({"success": False, "error": "Tenant not found"}), 404

            tenant.auth_setup_mode = True
            db_session.commit()

            logger.info(f"Auth setup mode re-enabled for tenant {tenant_id}")
            return jsonify({"success": True, "message": "Setup mode enabled. Test credentials now work."})

    except Exception as e:
        logger.error(f"Error enabling setup mode: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500
