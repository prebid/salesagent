"""Authentication blueprint for admin UI."""

import json
import logging
import os

from authlib.integrations.flask_client import OAuth
from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, session, url_for
from sqlalchemy import select

from src.admin.utils import is_super_admin  # type: ignore[attr-defined]
from src.core.database.database_session import get_db_session
from src.core.database.models import Tenant
from src.core.domain_config import (
    extract_subdomain_from_host,
    get_oauth_redirect_uri,
    get_sales_agent_url,
    get_super_admin_domain,
    is_sales_agent_domain,
)

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
    # Extract tenant from headers (Approximated routing or direct Host header)
    host = request.headers.get("Host", "")
    tenant_context = None
    tenant_name = None

    # Check for Approximated routing headers first
    # Approximated sends Apx-Incoming-Host with the original requested domain
    approximated_host = request.headers.get("Apx-Incoming-Host")
    if approximated_host:
        # Approximated provides the original requested domain - look up tenant by virtual_host
        with get_db_session() as db_session:
            tenant = db_session.scalars(select(Tenant).filter_by(virtual_host=approximated_host)).first()
            if tenant:
                tenant_context = tenant.tenant_id
                tenant_name = tenant.name
                logger.info(
                    f"Detected tenant context from Approximated headers: {approximated_host} -> {tenant_context}"
                )

    # Fallback to direct domain routing
    if not tenant_context:
        tenant_subdomain = None
        if is_sales_agent_domain(host) and not host.startswith("admin."):
            # Extract tenant subdomain from configured domain
            tenant_subdomain = extract_subdomain_from_host(host)

        if tenant_subdomain:
            # Look up tenant by subdomain
            with get_db_session() as db_session:
                tenant = db_session.scalars(select(Tenant).filter_by(subdomain=tenant_subdomain)).first()
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
        tenant = db_session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
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
    """Initiate Google OAuth flow - simplified central login."""
    oauth = current_app.oauth if hasattr(current_app, "oauth") else None
    if not oauth:
        flash("OAuth not configured", "error")
        return redirect(url_for("auth.login"))

    # Get redirect URI - must match what's configured in Google OAuth credentials
    # Note: In production with nginx, the path is /admin/auth/google/callback
    # but Flask only knows about /auth/google/callback

    # Debug: Log request context
    logger.info(f"OAuth initiation - Request URL: {request.url}")
    logger.info(f"OAuth initiation - Request host: {request.host}")
    logger.info(f"OAuth initiation - Request scheme: {request.scheme}")

    redirect_uri = os.environ.get("GOOGLE_OAUTH_REDIRECT_URI")
    if redirect_uri:
        logger.info(f"Using GOOGLE_OAUTH_REDIRECT_URI from env: {redirect_uri}")
    else:
        # Build the URL with /admin prefix for nginx routing
        base_url = url_for("auth.google_callback", _external=True)
        logger.info(f"Generated base URL: {base_url}")

        # If the base URL doesn't already have /admin, prepend it
        if "/admin/" not in base_url:
            redirect_uri = base_url.replace("/auth/google/callback", "/admin/auth/google/callback")
            logger.info(f"Added /admin prefix, final URI: {redirect_uri}")
        else:
            redirect_uri = base_url
            logger.info(f"URL already has /admin prefix: {redirect_uri}")

    logger.warning(f"========== FINAL OAuth redirect URI: {redirect_uri} ==========")

    # Simple OAuth flow - no tenant context preservation needed
    return oauth.google.authorize_redirect(redirect_uri)


@auth_bp.route("/tenant/<tenant_id>/auth/google")
def tenant_google_auth(tenant_id):
    """Initiate Google OAuth flow for tenant login."""
    oauth = current_app.oauth if hasattr(current_app, "oauth") else None
    if not oauth:
        flash("OAuth not configured", "error")
        return redirect(url_for("auth.tenant_login", tenant_id=tenant_id))

    host = request.headers.get("Host", "")

    # Always use the registered OAuth redirect URI for Google (no modifications allowed)
    if os.environ.get("PRODUCTION") == "true":
        # For production, always use the exact registered redirect URI
        redirect_uri = get_oauth_redirect_uri()
    else:
        # Development fallback
        redirect_uri = url_for("auth.google_callback", _external=True)

    # Store originating host and tenant context in session for OAuth callback
    session["oauth_originating_host"] = host

    # Store external domain and tenant context in session for OAuth callback
    # Note: This works for same-domain OAuth but has limitations for cross-domain scenarios
    approximated_host = request.headers.get("Apx-Incoming-Host")

    if approximated_host:
        session["oauth_external_domain"] = approximated_host
        logger.info(f"Stored external domain for OAuth redirect: {approximated_host}")

    session["oauth_tenant_context"] = tenant_id

    # Let Authlib manage the state parameter for CSRF protection
    return oauth.google.authorize_redirect(redirect_uri)


@auth_bp.route("/auth/google/callback")
def google_callback():
    """Handle Google OAuth callback - simplified version."""
    # Log immediately when callback is hit
    logger.warning("========== GOOGLE OAUTH CALLBACK HIT ==========")
    logger.warning(f"Request URL: {request.url}")
    logger.warning(f"Request args: {dict(request.args)}")
    logger.warning(f"Session keys at start: {list(session.keys())}")

    oauth = current_app.oauth if hasattr(current_app, "oauth") else None
    if not oauth:
        logger.error("OAuth not configured!")
        flash("OAuth not configured", "error")
        return redirect(url_for("auth.login"))

    try:
        logger.info("Attempting OAuth token exchange...")
        try:
            token = oauth.google.authorize_access_token()
            logger.info(f"Token exchange result: {token is not None}")
        except Exception as auth_error:
            logger.error(
                f"Authlib error during token exchange: {type(auth_error).__name__}: {auth_error}", exc_info=True
            )
            flash(f"Authentication error: {str(auth_error)}", "error")
            return redirect(url_for("auth.login"))

        if not token:
            logger.error("OAuth token exchange failed - authorize_access_token() returned None")
            logger.error(f"Request args: {dict(request.args)}")
            logger.error(f"Session keys: {list(session.keys())}")
            flash("Authentication failed. Please try again.", "error")
            return redirect(url_for("auth.login"))

        # Get user info
        user = token.get("userinfo")
        if not user:
            # Try to get user info from ID token
            import jwt

            id_token = token.get("id_token")
            if id_token:
                user = jwt.decode(id_token, options={"verify_signature": False})

        if not user or not user.get("email"):
            flash("Could not retrieve user information", "error")
            return redirect(url_for("auth.login"))

        email = user["email"].lower()
        session["user"] = email
        session["user_name"] = user.get("name", email)
        session["user_picture"] = user.get("picture", "")

        # Check if user is super admin FIRST (before signup flow check)
        # Super admins should never be redirected to signup/onboarding
        email_domain = email.split("@")[1] if "@" in email else ""
        super_admin_domain = get_super_admin_domain()
        if email_domain == super_admin_domain or is_super_admin(email):
            session["is_super_admin"] = True
            session["role"] = "super_admin"
            # Clear any signup flow state for super admins
            session.pop("signup_flow", None)
            session.pop("signup_step", None)
            flash(f"Welcome {user.get('name', email)}! (Super Admin)", "success")
            return redirect(url_for("core.index"))

        # Check if this is a signup flow (only for non-super-admin users)
        if session.get("signup_flow"):
            # Redirect to onboarding wizard for new tenant signup
            flash(f"Welcome {user.get('name', email)}!", "success")
            return redirect(url_for("public.signup_onboarding"))

        # Unified flow: Always show tenant selector (with option to create new tenant)
        # No distinction between signup and login - keeps UX simple and consistent
        from src.admin.domain_access import get_user_tenant_access

        # Get all accessible tenants
        tenant_access = get_user_tenant_access(email)

        # Build tenant list for selector (empty list is fine - user can create new tenant)
        # Use a dict to track tenants by tenant_id to avoid duplicates
        tenant_dict = {}

        if tenant_access["domain_tenant"]:
            domain_tenant = tenant_access["domain_tenant"]
            tenant_dict[domain_tenant.tenant_id] = {
                "tenant_id": domain_tenant.tenant_id,
                "name": domain_tenant.name,
                "subdomain": domain_tenant.subdomain,
                "is_admin": True,  # Domain users get admin access
            }

        for tenant in tenant_access["email_tenants"]:
            # Skip if already added via domain access
            if tenant.tenant_id in tenant_dict:
                continue

            # Check existing user record for role, default to admin
            with get_db_session() as db_session:
                from sqlalchemy import select

                from src.core.database.models import User

                stmt = select(User).filter_by(email=email, tenant_id=tenant.tenant_id)
                existing_user = db_session.scalars(stmt).first()
                is_admin = existing_user.role == "admin" if existing_user else True

            tenant_dict[tenant.tenant_id] = {
                "tenant_id": tenant.tenant_id,
                "name": tenant.name,
                "subdomain": tenant.subdomain,
                "is_admin": is_admin,
            }

        # Convert dict to list for session
        session["available_tenants"] = list(tenant_dict.values())

        # Always show tenant selector (includes "Create New Tenant" option)
        flash(f"Welcome {user.get('name', email)}!", "success")
        return redirect(url_for("auth.select_tenant"))

    except Exception as e:
        logger.error(f"[OAUTH_DEBUG] OAuth callback error: {type(e).__name__}: {e}", exc_info=True)
        logger.error(f"[OAUTH_DEBUG] Request args: {dict(request.args)}")
        logger.error(f"[OAUTH_DEBUG] Session keys: {list(session.keys())}")
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
                # Ensure User record exists in the database
                # This is critical for require_tenant_access decorator to work
                from src.admin.domain_access import ensure_user_in_tenant

                email = session["user"]
                user_name = session.get("user_name", email.split("@")[0].title())
                role = "admin" if tenant["is_admin"] else "viewer"

                try:
                    ensure_user_in_tenant(email, tenant_id, role=role, name=user_name)
                    logger.info(f"Ensured User record exists for {email} in tenant {tenant_id}")
                except Exception as e:
                    logger.error(f"Failed to create User record for {email} in tenant {tenant_id}: {e}")
                    flash("Error setting up user access. Please contact support.", "error")
                    return redirect(url_for("auth.select_tenant"))

                session["tenant_id"] = tenant_id
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


# GAM OAuth Flow endpoints
@auth_bp.route("/auth/gam/authorize/<tenant_id>")
def gam_authorize(tenant_id):
    """Initiate GAM OAuth flow for tenant."""
    # Verify tenant exists and user has access
    with get_db_session() as db_session:
        tenant = db_session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
        if not tenant:
            flash("Tenant not found", "error")
            return redirect(url_for("auth.login"))

    # Check OAuth configuration
    oauth = current_app.oauth if hasattr(current_app, "oauth") else None
    if not oauth:
        flash("OAuth not configured. Please contact your administrator.", "error")
        return redirect(url_for("tenants.settings", tenant_id=tenant_id))

    try:
        # Get GAM OAuth configuration
        from src.core.config import get_gam_oauth_config

        try:
            gam_config = get_gam_oauth_config()
            if not gam_config.client_id or not gam_config.client_secret:
                raise ValueError("GAM OAuth credentials not configured")
        except Exception as config_error:
            logger.error(f"GAM OAuth configuration error: {config_error}")
            flash(f"GAM OAuth not properly configured: {str(config_error)}", "error")
            return redirect(url_for("tenants.tenant_settings", tenant_id=tenant_id))

        # Store tenant context for callback
        session["gam_oauth_tenant_id"] = tenant_id
        session["gam_oauth_originating_host"] = request.headers.get("Host", "")

        # Store external domain context if available
        approximated_host = request.headers.get("Apx-Incoming-Host")
        if approximated_host:
            session["gam_oauth_external_domain"] = approximated_host
            logger.info(f"Stored external domain for GAM OAuth redirect: {approximated_host}")

        # Determine callback URI
        if os.environ.get("PRODUCTION") == "true":
            callback_uri = f"{get_sales_agent_url()}/admin/auth/gam/callback"
        else:
            callback_uri = url_for("auth.gam_callback", _external=True)

        # Log the callback URI for debugging
        logger.info(f"Initiating GAM OAuth flow for tenant {tenant_id} with callback_uri: {callback_uri}")

        # Build authorization URL with GAM-specific scope
        auth_url = (
            "https://accounts.google.com/o/oauth2/v2/auth?"
            f"client_id={gam_config.client_id}&"
            f"redirect_uri={callback_uri}&"
            "scope=https://www.googleapis.com/auth/dfp&"
            "response_type=code&"
            "access_type=offline&"
            "prompt=consent&"  # Force consent to get refresh token
            f"state={tenant_id}"
        )

        logger.debug(f"GAM OAuth authorization URL (redacted): {auth_url.split('client_id=')[0]}client_id=REDACTED...")
        return redirect(auth_url)

    except Exception as e:
        logger.error(f"Error initiating GAM OAuth for tenant {tenant_id}: {e}")
        flash(f"Error starting OAuth flow: {str(e)}", "error")
        return redirect(url_for("tenants.settings", tenant_id=tenant_id))


@auth_bp.route("/auth/gam/callback")
def gam_callback():
    """Handle GAM OAuth callback and store refresh token."""
    try:
        # Get authorization code and state
        code = request.args.get("code")
        state = request.args.get("state")
        error = request.args.get("error")

        # Log all callback parameters for debugging
        logger.info(f"GAM OAuth callback received - code present: {bool(code)}, state: {state}, error: {error}")
        logger.debug(f"GAM OAuth callback full args: {dict(request.args)}")

        if error:
            error_description = request.args.get("error_description", "No description provided")
            logger.error(f"GAM OAuth error: {error} - {error_description}")
            flash(f"OAuth authorization failed: {error_description}", "error")
            return redirect(url_for("auth.login"))

        if not code:
            flash("No authorization code received", "error")
            return redirect(url_for("auth.login"))

        # Get tenant context from session
        tenant_id = session.pop("gam_oauth_tenant_id", state)
        originating_host = session.pop("gam_oauth_originating_host", None)
        external_domain = session.pop("gam_oauth_external_domain", None)

        if not tenant_id:
            flash("Invalid OAuth state - no tenant context", "error")
            return redirect(url_for("auth.login"))

        # Get GAM OAuth configuration
        from src.core.config import get_gam_oauth_config

        gam_config = get_gam_oauth_config()

        # Determine callback URI (must match the one used in authorization)
        if os.environ.get("PRODUCTION") == "true":
            callback_uri = f"{get_sales_agent_url()}/admin/auth/gam/callback"
        else:
            callback_uri = url_for("auth.gam_callback", _external=True)

        # Exchange authorization code for tokens
        import requests

        logger.info(f"Exchanging authorization code for tokens - tenant: {tenant_id}, callback_uri: {callback_uri}")
        logger.debug(f"Token exchange request - client_id: {gam_config.client_id[:20]}...")

        token_response = requests.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": gam_config.client_id,
                "client_secret": gam_config.client_secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": callback_uri,
            },
        )

        if not token_response.ok:
            error_details = (
                token_response.json()
                if token_response.headers.get("content-type", "").startswith("application/json")
                else {"raw": token_response.text}
            )
            logger.error(f"Token exchange failed: status={token_response.status_code}, details={error_details}")

            # Provide user-friendly error messages based on common issues
            error_description = error_details.get("error_description", "")
            if "redirect_uri_mismatch" in str(error_details):
                flash("OAuth configuration error: Redirect URI mismatch. Please contact your administrator.", "error")
            elif "invalid_grant" in str(error_details):
                flash("Authorization code expired or invalid. Please try again.", "error")
            elif "invalid_client" in str(error_details):
                flash("Invalid OAuth credentials. Please contact your administrator.", "error")
            else:
                flash(
                    f"Failed to exchange authorization code for tokens: {error_description or 'Unknown error'}", "error"
                )

            return redirect(url_for("tenants.tenant_settings", tenant_id=tenant_id))

        token_data = token_response.json()
        refresh_token = token_data.get("refresh_token")

        if not refresh_token:
            logger.error("No refresh token in OAuth response")
            flash("No refresh token received. Please try again or contact support.", "error")
            return redirect(url_for("tenants.settings", tenant_id=tenant_id))

        # Store refresh token in tenant's adapter config
        with get_db_session() as db_session:
            from src.core.database.models import AdapterConfig

            tenant = db_session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
            if not tenant:
                flash("Tenant not found", "error")
                return redirect(url_for("auth.login"))

            # Get or create adapter config
            adapter_config = db_session.scalars(select(AdapterConfig).filter_by(tenant_id=tenant_id)).first()
            if not adapter_config:
                adapter_config = AdapterConfig(tenant_id=tenant_id, adapter_type="google_ad_manager")
                db_session.add(adapter_config)

            # Store the refresh token
            adapter_config.gam_refresh_token = refresh_token

            # Also update tenant's ad_server field
            tenant.ad_server = "google_ad_manager"

            db_session.commit()

        logger.info(f"GAM OAuth completed successfully for tenant {tenant_id}")
        flash("Google Ad Manager OAuth setup completed successfully! Your refresh token has been saved.", "success")

        # Try to auto-detect network information
        try:
            # Import the detect network logic from GAM blueprint

            # Note: We can't directly call detect_gam_network here as it expects a POST request
            # The user will need to use the "Auto-detect Network" button in the UI
            flash("Next step: Use the 'Auto-detect Network' button to complete your GAM configuration.", "info")
        except Exception as detect_error:
            logger.warning(f"Could not suggest auto-detect: {detect_error}")

        # Redirect back to tenant settings
        if external_domain and os.environ.get("PRODUCTION") == "true":
            return redirect(f"https://{external_domain}/admin/tenant/{tenant_id}/settings")
        elif originating_host and os.environ.get("PRODUCTION") == "true":
            return redirect(f"https://{originating_host}/admin/tenant/{tenant_id}/settings")
        else:
            return redirect(url_for("tenants.tenant_settings", tenant_id=tenant_id))

    except Exception as e:
        logger.error(f"Error in GAM OAuth callback: {e}", exc_info=True)
        flash("OAuth callback failed. Please try again.", "error")
        return redirect(url_for("auth.login"))
