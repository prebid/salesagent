"""Authentication blueprint for admin UI."""

import json
import logging
import os

from authlib.integrations.flask_client import OAuth
from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, session, url_for

from src.admin.utils import is_super_admin
from src.core.database.database_session import get_db_session
from src.core.database.models import Tenant, User

logger = logging.getLogger(__name__)

# Create Blueprint
auth_bp = Blueprint("auth", __name__)


def init_oauth(app):
    """Initialize OAuth with the Flask app."""
    oauth = OAuth(app)

    # Google OAuth configuration
    client_id = os.environ.get("GOOGLE_CLIENT_ID")
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET")

    # Try to load from file if env vars not set
    if not client_id or not client_secret:
        for filename in [
            "client_secret.json",
            "client_secret_819081116704-kqh8lrv0nvqmu8onqmvnadqtlajbqbbn.apps.googleusercontent.com.json",
        ]:
            # Look in project root (4 levels up from src/admin/blueprints/auth.py)
            filepath = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))), filename
            )
            if os.path.exists(filepath):
                try:
                    with open(filepath) as f:
                        creds = json.load(f)
                        if "web" in creds:
                            client_id = creds["web"]["client_id"]
                            client_secret = creds["web"]["client_secret"]
                            break
                except Exception as e:
                    logger.error(f"Failed to load OAuth credentials from {filepath}: {e}")

    if client_id and client_secret:
        oauth.register(
            name="google",
            client_id=client_id,
            client_secret=client_secret,
            server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
            client_kwargs={"scope": "openid email profile"},
        )
        app.oauth = oauth
        return oauth
    else:
        logger.warning("Google OAuth not configured - authentication will not work")
        return None


@auth_bp.route("/login")
def login():
    """Show login page with tenant context detection."""
    # Extract tenant from Host header for tenant-specific subdomains
    host = request.headers.get("Host", "")
    tenant_context = None
    tenant_name = None

    if ".sales-agent.scope3.com" in host and not host.startswith("admin."):
        # Extract tenant subdomain (e.g., "scribd" from "scribd.sales-agent.scope3.com")
        tenant_subdomain = host.split(".")[0]

        # Look up tenant by subdomain
        with get_db_session() as db_session:
            tenant = db_session.query(Tenant).filter_by(subdomain=tenant_subdomain).first()
            if tenant:
                tenant_context = tenant.tenant_id
                tenant_name = tenant.name
                logger.info(f"Detected tenant context from Host header: {tenant_subdomain} -> {tenant_context}")

    return render_template(
        "login.html",
        test_mode=os.environ.get("ADCP_AUTH_TEST_MODE", "").lower() == "true",
        tenant_context=tenant_context,
        tenant_name=tenant_name,
    )


@auth_bp.route("/tenant/<tenant_id>/login")
def tenant_login(tenant_id):
    """Show tenant-specific login page."""
    # Verify tenant exists
    with get_db_session() as db_session:
        tenant = db_session.query(Tenant).filter_by(tenant_id=tenant_id).first()
        if not tenant:
            abort(404)

    return render_template(
        "login.html",
        tenant_id=tenant_id,
        tenant_name=tenant.name,
        test_mode=os.environ.get("ADCP_AUTH_TEST_MODE", "").lower() == "true",
    )


@auth_bp.route("/auth/google")
def google_auth():
    """Initiate Google OAuth flow with tenant context detection."""
    oauth = current_app.oauth if hasattr(current_app, "oauth") else None
    if not oauth:
        flash("OAuth not configured", "error")
        return redirect(url_for("auth.login"))

    # Capture tenant context from Host header or form data
    host = request.headers.get("Host", "")
    tenant_context = request.args.get("tenant_context")  # From login form

    if not tenant_context and ".sales-agent.scope3.com" in host and not host.startswith("admin."):
        # Extract tenant subdomain from Host header
        tenant_subdomain = host.split(".")[0]
        with get_db_session() as db_session:
            tenant = db_session.query(Tenant).filter_by(subdomain=tenant_subdomain).first()
            if tenant:
                tenant_context = tenant.tenant_id
                logger.info(f"Detected tenant context from Host header: {tenant_subdomain} -> {tenant_context}")

    # Always use base domain for OAuth callback to support dynamic tenants
    if os.environ.get("PRODUCTION") == "true" or ".sales-agent.scope3.com" in request.headers.get("Host", ""):
        # For production, use the base domain as redirect URI
        redirect_uri = "https://sales-agent.scope3.com/admin/auth/google/callback"
    else:
        # Development fallback
        redirect_uri = url_for("auth.google_callback", _external=True)

    # Store originating host and tenant context in session for OAuth callback
    # We can't use custom state parameter due to CSRF validation, so use session instead
    session["oauth_originating_host"] = host
    if tenant_context:
        session["oauth_tenant_context"] = tenant_context

    return oauth.google.authorize_redirect(redirect_uri)


@auth_bp.route("/tenant/<tenant_id>/auth/google")
def tenant_google_auth(tenant_id):
    """Initiate Google OAuth flow for tenant login."""
    oauth = current_app.oauth if hasattr(current_app, "oauth") else None
    if not oauth:
        flash("OAuth not configured", "error")
        return redirect(url_for("auth.tenant_login", tenant_id=tenant_id))

    host = request.headers.get("Host", "")

    # Always use base domain for OAuth callback to support dynamic tenants
    if os.environ.get("PRODUCTION") == "true" or ".sales-agent.scope3.com" in request.headers.get("Host", ""):
        # For production, use the base domain as redirect URI
        redirect_uri = "https://sales-agent.scope3.com/admin/auth/google/callback"
    else:
        # Development fallback
        redirect_uri = url_for("auth.google_callback", _external=True)

    # Store originating host and tenant context in session for OAuth callback
    # We can't use custom state parameter due to CSRF validation, so use session instead
    session["oauth_originating_host"] = host
    session["oauth_tenant_context"] = tenant_id

    return oauth.google.authorize_redirect(redirect_uri)


@auth_bp.route("/auth/google/callback")
def google_callback():
    """Handle Google OAuth callback."""
    oauth = current_app.oauth if hasattr(current_app, "oauth") else None
    if not oauth:
        flash("OAuth not configured", "error")
        return redirect(url_for("auth.login"))

    try:
        token = oauth.google.authorize_access_token()
        if not token:
            flash("Authentication failed", "error")
            return redirect(url_for("auth.login"))

        # Get user info
        user = token.get("userinfo")
        if not user:
            # Try to get user info from ID token
            import jwt

            id_token = token.get("id_token")
            if id_token:
                # Decode without verification since we trust Google's response
                user = jwt.decode(id_token, options={"verify_signature": False})

        if not user or not user.get("email"):
            flash("Could not retrieve user information", "error")
            return redirect(url_for("auth.login"))

        email = user["email"].lower()
        session["user"] = email
        session["user_name"] = user.get("name", email)
        session["user_picture"] = user.get("picture", "")

        # Get originating host and tenant context from session
        originating_host = session.pop("oauth_originating_host", None)
        tenant_id = session.pop("oauth_tenant_context", None)
        if tenant_id:
            # Verify user has access to this tenant
            with get_db_session() as db_session:
                tenant = db_session.query(Tenant).filter_by(tenant_id=tenant_id).first()
                if not tenant:
                    flash("Invalid tenant", "error")
                    return redirect(url_for("auth.login"))

                # Check if user is super admin or has tenant access
                if is_super_admin(email):
                    session["tenant_id"] = tenant_id
                    session["tenant_name"] = tenant.name
                    session["is_super_admin"] = True
                    flash(f"Welcome {user.get('name', email)}! (Super Admin)", "success")

                    # Redirect to tenant-specific subdomain if accessed via subdomain
                    if tenant.subdomain and tenant.subdomain != "localhost":
                        return redirect(f"https://{tenant.subdomain}.sales-agent.scope3.com/admin/")
                    else:
                        return redirect(url_for("tenants.dashboard", tenant_id=tenant_id))

                # Check if user has access to this tenant
                user_record = db_session.query(User).filter_by(email=email, tenant_id=tenant_id, is_active=True).first()

                if user_record:
                    session["tenant_id"] = tenant_id
                    session["tenant_name"] = tenant.name
                    session["is_tenant_admin"] = user_record.is_admin
                    flash(f"Welcome {user.get('name', email)}!", "success")

                    # Redirect to tenant-specific subdomain if accessed via subdomain
                    if tenant.subdomain and tenant.subdomain != "localhost":
                        return redirect(f"https://{tenant.subdomain}.sales-agent.scope3.com/admin/")
                    else:
                        return redirect(url_for("tenants.dashboard", tenant_id=tenant_id))
                else:
                    flash("You don't have access to this tenant", "error")
                    session.clear()
                    return redirect(url_for("auth.tenant_login", tenant_id=tenant_id))

        # Domain-based access control using email domain extraction
        from src.admin.domain_access import ensure_user_in_tenant, get_user_tenant_access

        email_domain = email.split("@")[1] if "@" in email else ""

        # 1. Scope3 super admin check
        if email_domain == "scope3.com" or is_super_admin(email):
            session["is_super_admin"] = True
            session["role"] = "super_admin"
            session["authenticated"] = True
            session["email"] = email
            flash(f"Welcome {user.get('name', email)}! (Super Admin)", "success")

            # Check where the OAuth flow originated from
            if originating_host and originating_host.startswith("admin.") and os.environ.get("PRODUCTION") == "true":
                return redirect("https://admin.sales-agent.scope3.com/admin/")
            elif os.environ.get("PRODUCTION") == "true":
                return redirect("https://admin.sales-agent.scope3.com/admin/")
            else:
                return redirect(url_for("core.index"))

        # 2. Check domain-based and email-based tenant access
        tenant_access = get_user_tenant_access(email)

        if tenant_access["total_access"] == 0:
            # No access
            flash("You don't have access to any tenants. Please contact your administrator.", "error")
            session.clear()
            return redirect(url_for("auth.login"))

        elif tenant_access["total_access"] == 1:
            # Single tenant - direct access
            if tenant_access["domain_tenant"]:
                tenant = tenant_access["domain_tenant"]
                access_type = "domain"
            else:
                tenant = tenant_access["email_tenants"][0]
                access_type = "email"

            # Ensure user record exists (auto-create if needed)
            user_record = ensure_user_in_tenant(email, tenant.tenant_id, role="admin", name=user.get("name"))

            session["tenant_id"] = tenant.tenant_id
            session["tenant_name"] = tenant.name
            session["is_tenant_admin"] = user_record.role == "admin"
            flash(f"Welcome {user.get('name', email)}! ({access_type.title()} Access)", "success")

            # Redirect to tenant-specific subdomain if accessed via subdomain
            if tenant.subdomain and tenant.subdomain != "localhost" and os.environ.get("PRODUCTION") == "true":
                return redirect(f"https://{tenant.subdomain}.sales-agent.scope3.com/admin/")
            else:
                return redirect(url_for("tenants.dashboard", tenant_id=tenant.tenant_id))

        else:
            # Multiple tenants - let user choose
            session["available_tenants"] = []

            if tenant_access["domain_tenant"]:
                session["available_tenants"].append(
                    {
                        "tenant_id": tenant_access["domain_tenant"].tenant_id,
                        "name": tenant_access["domain_tenant"].name,
                        "access_type": "domain",
                        "is_admin": True,  # Domain users get admin access
                    }
                )

            for tenant in tenant_access["email_tenants"]:
                # Check existing user record for role, default to admin
                with get_db_session() as db_session:
                    existing_user = db_session.query(User).filter_by(email=email, tenant_id=tenant.tenant_id).first()
                    is_admin = existing_user.role == "admin" if existing_user else True

                session["available_tenants"].append(
                    {"tenant_id": tenant.tenant_id, "name": tenant.name, "access_type": "email", "is_admin": is_admin}
                )

            return redirect(url_for("auth.select_tenant"))

    except Exception as e:
        logger.error(f"OAuth callback error: {e}", exc_info=True)
        flash("Authentication failed. Please try again.", "error")
        return redirect(url_for("auth.login"))


@auth_bp.route("/auth/select-tenant", methods=["GET", "POST"])
def select_tenant():
    """Allow user to select a tenant when they have access to multiple."""
    if "user" not in session or "available_tenants" not in session:
        return redirect(url_for("auth.login"))

    if request.method == "POST":
        tenant_id = request.form.get("tenant_id")

        # Verify user has access to selected tenant
        for tenant in session["available_tenants"]:
            if tenant["tenant_id"] == tenant_id:
                session["tenant_id"] = tenant_id
                session["tenant_name"] = tenant["name"]
                session["is_tenant_admin"] = tenant["is_admin"]
                session.pop("available_tenants", None)  # Clean up
                flash(f"Welcome to {tenant['name']}!", "success")
                return redirect(url_for("tenants.dashboard", tenant_id=tenant_id))

        flash("Invalid tenant selection", "error")
        return redirect(url_for("auth.select_tenant"))

    return render_template("choose_tenant.html", tenants=session["available_tenants"])


@auth_bp.route("/logout")
def logout():
    """Log out the current user."""
    session.clear()
    flash("You have been logged out", "info")
    return redirect(url_for("auth.login"))


# Test authentication endpoints (only enabled in test mode)
@auth_bp.route("/test/auth", methods=["POST"])
def test_auth():
    """Test authentication endpoint (only works when ADCP_AUTH_TEST_MODE=true)."""
    if os.environ.get("ADCP_AUTH_TEST_MODE", "").lower() != "true":
        abort(404)

    email = request.form.get("email", "").lower()
    password = request.form.get("password")
    tenant_id = request.form.get("tenant_id")

    # Define test users
    test_users = {
        os.environ.get("TEST_SUPER_ADMIN_EMAIL", "test_super_admin@example.com"): {
            "password": os.environ.get("TEST_SUPER_ADMIN_PASSWORD", "test123"),
            "name": "Test Super Admin",
            "role": "super_admin",
        },
        os.environ.get("TEST_TENANT_ADMIN_EMAIL", "test_tenant_admin@example.com"): {
            "password": os.environ.get("TEST_TENANT_ADMIN_PASSWORD", "test123"),
            "name": "Test Tenant Admin",
            "role": "tenant_admin",
        },
        os.environ.get("TEST_TENANT_USER_EMAIL", "test_tenant_user@example.com"): {
            "password": os.environ.get("TEST_TENANT_USER_PASSWORD", "test123"),
            "name": "Test Tenant User",
            "role": "tenant_user",
        },
    }

    # Check if email is a super admin (bypass password check for super admins in test mode)
    if is_super_admin(email) and password == "test123":
        session["test_user"] = email
        session["test_user_name"] = email.split("@")[0].title()
        session["test_user_role"] = "super_admin"
        session["user"] = email  # Store as string for is_super_admin check
        session["user_name"] = email.split("@")[0].title()
        session["is_super_admin"] = True
        session["role"] = "super_admin"
        session["authenticated"] = True
        session["email"] = email

        if tenant_id:
            session["test_tenant_id"] = tenant_id
            return redirect(url_for("tenants.dashboard", tenant_id=tenant_id))
        else:
            return redirect(url_for("core.index"))

    # Check test users
    if email in test_users and test_users[email]["password"] == password:
        user_info = test_users[email]
        session["test_user"] = email
        session["test_user_name"] = user_info["name"]
        session["test_user_role"] = user_info["role"]
        session["user"] = email  # Store as string for consistency
        session["user_name"] = user_info["name"]
        session["role"] = user_info["role"]
        session["authenticated"] = True
        session["email"] = email

        if user_info["role"] == "super_admin":
            session["is_super_admin"] = True

        if tenant_id:
            session["test_tenant_id"] = tenant_id
            return redirect(url_for("tenants.dashboard", tenant_id=tenant_id))
        else:
            return redirect(url_for("core.index"))

    flash("Invalid test credentials", "error")
    return redirect(request.referrer or url_for("auth.login"))


@auth_bp.route("/test/login")
def test_login_form():
    """Show test login form (only works when ADCP_AUTH_TEST_MODE=true)."""
    if os.environ.get("ADCP_AUTH_TEST_MODE", "").lower() != "true":
        abort(404)

    return render_template("login.html", test_mode=True, test_only=True)
