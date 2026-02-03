"""Authentication blueprint for admin UI.

Supports multiple OAuth providers via generic OIDC configuration:
- Google (default)
- Any OIDC-compliant provider (Okta, Auth0, Azure AD, Keycloak, etc.)

Configuration priority:
1. Generic OIDC (OAUTH_DISCOVERY_URL set) - uses any OIDC provider
2. Google OAuth (GOOGLE_CLIENT_ID set) - uses Google specifically
3. Legacy file-based Google credentials (client_secret.json)
"""

import json
import logging
import os

from authlib.integrations.flask_client import OAuth
from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, session, url_for
from sqlalchemy import select

from src.admin.utils import is_super_admin
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

# Well-known OIDC discovery URLs for common providers
OIDC_PROVIDERS = {
    "google": "https://accounts.google.com/.well-known/openid-configuration",
    "microsoft": "https://login.microsoftonline.com/common/v2.0/.well-known/openid-configuration",
    "okta": None,  # Requires tenant: https://{tenant}.okta.com/.well-known/openid-configuration
    "auth0": None,  # Requires tenant: https://{tenant}.auth0.com/.well-known/openid-configuration
    "keycloak": None,  # Requires server/realm: https://{server}/realms/{realm}/.well-known/openid-configuration
}


def get_oauth_provider_name():
    """Get the configured OAuth provider name."""
    return os.environ.get("OAUTH_PROVIDER", "google").lower()


def get_oauth_config():
    """Get OAuth configuration from environment.

    Returns tuple of (client_id, client_secret, discovery_url, scopes) or (None, None, None, None).

    Configuration options (in priority order):
    1. Generic OIDC: OAUTH_DISCOVERY_URL + OAUTH_CLIENT_ID + OAUTH_CLIENT_SECRET
    2. Named provider: OAUTH_PROVIDER + OAUTH_CLIENT_ID + OAUTH_CLIENT_SECRET
    3. Google-specific: GOOGLE_CLIENT_ID + GOOGLE_CLIENT_SECRET
    4. Legacy file: client_secret.json
    """
    # Option 1: Full generic OIDC configuration
    discovery_url = os.environ.get("OAUTH_DISCOVERY_URL")
    client_id = os.environ.get("OAUTH_CLIENT_ID")
    client_secret = os.environ.get("OAUTH_CLIENT_SECRET")
    scopes = os.environ.get("OAUTH_SCOPES", "openid email profile")

    if discovery_url and client_id and client_secret:
        logger.info(f"Using generic OIDC provider with discovery URL: {discovery_url}")
        return client_id, client_secret, discovery_url, scopes

    # Option 2: Named provider with generic credentials
    provider = get_oauth_provider_name()
    if client_id and client_secret and provider in OIDC_PROVIDERS:
        provider_url = OIDC_PROVIDERS.get(provider)
        if provider_url:
            logger.info(f"Using {provider} OAuth provider")
            return client_id, client_secret, provider_url, scopes
        else:
            logger.warning(f"Provider '{provider}' requires OAUTH_DISCOVERY_URL to be set")

    # Option 3: Google-specific environment variables (backwards compatible)
    google_client_id = os.environ.get("GOOGLE_CLIENT_ID")
    google_client_secret = os.environ.get("GOOGLE_CLIENT_SECRET")

    if google_client_id and google_client_secret:
        logger.info("Using Google OAuth (legacy GOOGLE_CLIENT_ID configuration)")
        return google_client_id, google_client_secret, OIDC_PROVIDERS["google"], "openid email profile"

    # Option 4: Legacy file-based credentials
    for filename in [
        "client_secret.json",
        "client_secret_819081116704-kqh8lrv0nvqmu8onqmvnadqtlajbqbbn.apps.googleusercontent.com.json",
    ]:
        filepath = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))), filename)
        if os.path.exists(filepath):
            try:
                with open(filepath) as f:
                    creds = json.load(f)
                    if "web" in creds:
                        logger.info(f"Using Google OAuth credentials from {filename}")
                        return (
                            creds["web"]["client_id"],
                            creds["web"]["client_secret"],
                            OIDC_PROVIDERS["google"],
                            "openid email profile",
                        )
            except Exception as e:
                logger.error(f"Failed to load OAuth credentials from {filepath}: {e}")

    return None, None, None, None


def extract_user_info(token):
    """Extract user info from OAuth token, handling different provider formats.

    Different OIDC providers return user info in different claim formats:
    - Google: email, name, picture
    - Microsoft: email OR preferred_username, name, picture
    - Okta: email, name, picture (or custom claims)
    - Auth0: email, name, picture
    - Keycloak: email, preferred_username, name, picture

    Returns dict with normalized keys: email, name, picture
    """
    import jwt

    user = token.get("userinfo")

    if not user:
        # Try to decode from ID token
        id_token = token.get("id_token")
        if id_token:
            try:
                user = jwt.decode(id_token, options={"verify_signature": False})
            except Exception as e:
                logger.warning(f"Failed to decode ID token: {e}")
                return None

    if not user:
        return None

    # Extract email - try multiple claim names
    email = (
        user.get("email")
        or user.get("preferred_username")
        or user.get("upn")  # Microsoft UPN
        or user.get("sub")  # Fallback to subject
    )

    if not email:
        logger.error(f"Could not extract email from user claims: {list(user.keys())}")
        return None

    # Extract name - try multiple claim names
    name = user.get("name") or user.get("display_name")
    if not name:
        # Try constructing from given/family names
        given = user.get("given_name", "")
        family = user.get("family_name", "")
        if given or family:
            name = f"{given} {family}".strip()
    if not name:
        # Fallback to email prefix
        name = email.split("@")[0]

    # Extract picture - try multiple claim names
    picture = user.get("picture") or user.get("avatar_url") or user.get("photo") or ""

    return {
        "email": email.lower(),
        "name": name,
        "picture": picture,
    }


def init_oauth(app):
    """Initialize OAuth with the Flask app.

    Supports generic OIDC providers via OAUTH_DISCOVERY_URL or
    Google OAuth via GOOGLE_CLIENT_ID for backwards compatibility.
    """
    oauth = OAuth(app)

    client_id, client_secret, discovery_url, scopes = get_oauth_config()

    if client_id and client_secret and discovery_url:
        # Register as 'oidc' for generic providers, but keep 'google' name for compatibility
        # This allows the same code path for all providers
        oauth.register(
            name="google",  # Keep name for route compatibility
            client_id=client_id,
            client_secret=client_secret,
            server_metadata_url=discovery_url,
            client_kwargs={"scope": scopes},
        )
        app.oauth = oauth
        app.oauth_provider = get_oauth_provider_name()
        logger.info(f"OAuth initialized with provider: {app.oauth_provider}")
        return oauth
    else:
        logger.warning(
            "OAuth not configured - authentication will not work. "
            "Set OAUTH_DISCOVERY_URL + OAUTH_CLIENT_ID + OAUTH_CLIENT_SECRET for generic OIDC, "
            "or GOOGLE_CLIENT_ID + GOOGLE_CLIENT_SECRET for Google OAuth."
        )
        return None


@auth_bp.route("/login")
def login():
    """Show login page or redirect to OAuth provider.

    If OAuth is configured and not in test mode, redirects directly to OAuth.
    Otherwise shows the login page with test mode buttons.

    For multi-tenant deployments, detects tenant from subdomain and checks
    for tenant-specific OIDC configuration first.
    """
    logger.debug("login route hit, has_user=%s, args=%s", "user" in session, dict(request.args))

    # Capture 'next' parameter for redirect after login
    next_url = request.args.get("next")
    if next_url:
        session["login_next_url"] = next_url

    # Don't auto-redirect if user just logged out
    just_logged_out = request.args.get("logged_out") == "1"
    logger.debug("just_logged_out=%s", just_logged_out)

    # Check if OAuth is configured via environment (fallback for all tenants)
    client_id, client_secret, discovery_url, _ = get_oauth_config()
    oauth_configured = bool(client_id and client_secret and discovery_url)

    # Determine test_mode from env var only
    # tenant.auth_setup_mode is only used when NO global OAuth is configured
    test_mode = os.environ.get("ADCP_AUTH_TEST_MODE", "").lower() == "true"

    from src.core.config_loader import is_single_tenant_mode

    oidc_enabled = False
    oidc_configured = False
    tenant_context = None
    tenant_name = None

    # Extract tenant from headers FIRST (before any redirects)
    # This is needed for multi-tenant subdomain routing
    host = request.headers.get("Host", "")

    # Check for Approximated routing headers first
    approximated_host = request.headers.get("Apx-Incoming-Host")
    if approximated_host:
        with get_db_session() as db_session:
            tenant = db_session.scalars(select(Tenant).filter_by(virtual_host=approximated_host)).first()
            if tenant:
                tenant_context = tenant.tenant_id
                tenant_name = tenant.name
                # Only use auth_setup_mode if no global OAuth configured
                if not oauth_configured and hasattr(tenant, "auth_setup_mode") and tenant.auth_setup_mode:
                    test_mode = True
                logger.info(
                    f"Detected tenant context from Approximated headers: {approximated_host} -> {tenant_context}"
                )

    # Fallback to direct domain routing (subdomain detection)
    if not tenant_context:
        tenant_subdomain = None
        if is_sales_agent_domain(host) and not host.startswith("admin."):
            tenant_subdomain = extract_subdomain_from_host(host)

        if tenant_subdomain:
            with get_db_session() as db_session:
                tenant = db_session.scalars(select(Tenant).filter_by(subdomain=tenant_subdomain)).first()
                if tenant:
                    tenant_context = tenant.tenant_id
                    tenant_name = tenant.name
                    # Only use auth_setup_mode if no global OAuth configured
                    if not oauth_configured and hasattr(tenant, "auth_setup_mode") and tenant.auth_setup_mode:
                        test_mode = True
                    logger.info(f"Detected tenant context from Host header: {tenant_subdomain} -> {tenant_context}")

    # Check for tenant-specific OIDC configuration (multi-tenant or single-tenant)
    if tenant_context:
        # For detected tenant, check if it has OIDC configured
        from src.core.database.models import TenantAuthConfig

        with get_db_session() as db_session:
            config = db_session.scalars(select(TenantAuthConfig).filter_by(tenant_id=tenant_context)).first()
            if config and config.oidc_client_id:
                oidc_configured = True
                oidc_enabled = config.oidc_enabled

        # If tenant has OIDC enabled, redirect to tenant OIDC login
        if oidc_enabled and not test_mode and not just_logged_out:
            return redirect(url_for("oidc.login", tenant_id=tenant_context))

        # Fall back to global OAuth for this tenant
        if oauth_configured and not just_logged_out:
            return redirect(url_for("auth.tenant_google_auth", tenant_id=tenant_context))

    elif is_single_tenant_mode():
        # Single-tenant mode: check default tenant's OIDC config
        from src.core.database.models import TenantAuthConfig

        with get_db_session() as db_session:
            tenant = db_session.scalars(select(Tenant).filter_by(tenant_id="default")).first()
            config = db_session.scalars(select(TenantAuthConfig).filter_by(tenant_id="default")).first()
            if config and config.oidc_client_id:
                oidc_configured = True
                oidc_enabled = config.oidc_enabled
            # Only use auth_setup_mode in single-tenant mode if no global OAuth
            if not oauth_configured and tenant and hasattr(tenant, "auth_setup_mode") and tenant.auth_setup_mode:
                test_mode = True

        if oidc_enabled and not test_mode and not just_logged_out:
            return redirect(url_for("oidc.login", tenant_id="default"))

    # Fall back to global OAuth if configured (for admin domain or unknown tenants)
    if oauth_configured and not just_logged_out:
        return redirect(url_for("auth.google_auth"))

    # Show login page (test mode or OAuth not configured)
    # Pass oidc_enabled=True if OIDC is configured (so user can test it)
    # The actual oidc_enabled flag controls whether it's the only option
    return render_template(
        "login.html",
        test_mode=test_mode,
        oauth_configured=oauth_configured,
        oidc_enabled=oidc_configured,  # Show SSO button if configured (not just enabled)
        tenant_context=tenant_context,
        tenant_name=tenant_name,
        tenant_id=tenant_context if tenant_context else ("default" if is_single_tenant_mode() else None),
        single_tenant_mode=is_single_tenant_mode(),
    )


@auth_bp.route("/tenant/<tenant_id>/login")
def tenant_login(tenant_id):
    """Show tenant-specific login page or redirect to OAuth provider."""
    # Don't auto-redirect if user just logged out
    just_logged_out = request.args.get("logged_out") == "1"

    # Capture 'next' parameter for redirect after login
    next_url = request.args.get("next")
    if next_url:
        session["login_next_url"] = next_url

    # Check if global OAuth is configured (fallback for all tenants)
    client_id, client_secret, discovery_url, _ = get_oauth_config()
    oauth_configured = bool(client_id and client_secret and discovery_url)

    # Verify tenant exists and get auth_setup_mode
    with get_db_session() as db_session:
        tenant = db_session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
        if not tenant:
            abort(404)
        tenant_name = tenant.name

        # Determine test_mode:
        # - ADCP_AUTH_TEST_MODE env var enables test mode globally
        # - tenant.auth_setup_mode enables test mode for this tenant ONLY if no global OAuth
        #   (for multi-tenant with global OAuth, tenants use global OAuth, not setup mode)
        test_mode = os.environ.get("ADCP_AUTH_TEST_MODE", "").lower() == "true"
        if not test_mode and not oauth_configured:
            # No global OAuth - use tenant's auth_setup_mode (for single-tenant SSO setup)
            test_mode = tenant.auth_setup_mode if hasattr(tenant, "auth_setup_mode") else True

        # Check if tenant-specific OIDC is configured and enabled
        from src.services.auth_config_service import get_oidc_config_for_auth

        oidc_config = get_oidc_config_for_auth(tenant_id)
        oidc_enabled = bool(oidc_config)

    # If OIDC is enabled and not in test mode and not just logged out, redirect directly to OIDC
    if oidc_enabled and not test_mode and not just_logged_out:
        return redirect(url_for("oidc.login", tenant_id=tenant_id))

    # If OAuth is configured and not just logged out, redirect directly to OAuth
    # (global OAuth is the fallback for tenants without their own OIDC)
    if oauth_configured and not just_logged_out:
        return redirect(url_for("auth.tenant_google_auth", tenant_id=tenant_id))

    from src.core.config_loader import is_single_tenant_mode

    return render_template(
        "login.html",
        tenant_id=tenant_id,
        tenant_name=tenant_name,
        test_mode=test_mode,
        oauth_configured=oauth_configured,
        oidc_enabled=oidc_enabled,
        single_tenant_mode=is_single_tenant_mode(),
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

    # Debug: Log session cookie configuration
    logger.debug(
        "session config: SECURE=%s, SAMESITE=%s",
        current_app.config.get("SESSION_COOKIE_SECURE"),
        current_app.config.get("SESSION_COOKIE_SAMESITE"),
    )

    redirect_uri = os.environ.get("GOOGLE_OAUTH_REDIRECT_URI")
    if redirect_uri:
        logger.info(f"Using GOOGLE_OAUTH_REDIRECT_URI from env: {redirect_uri}")
    else:
        # Build the URL
        base_url = url_for("auth.google_callback", _external=True)
        logger.info(f"Generated base URL: {base_url}")

        # Only add /admin prefix in production mode with nginx (not in Docker standalone)
        # SKIP_NGINX=true indicates Docker standalone mode without nginx reverse proxy
        skip_nginx = os.environ.get("SKIP_NGINX", "").lower() == "true"
        production = os.environ.get("PRODUCTION", "").lower() == "true"

        if not skip_nginx and production and "/admin/" not in base_url:
            # Production with nginx: add /admin prefix for nginx routing
            redirect_uri = base_url.replace("/auth/google/callback", "/admin/auth/google/callback")
            logger.info(f"Added /admin prefix for nginx, final URI: {redirect_uri}")
        else:
            # Docker standalone or development: use the base URL as-is
            redirect_uri = base_url
            logger.info(f"Using base URL without /admin prefix: {redirect_uri}")

    logger.debug("OAuth redirect URI: %s", redirect_uri)

    # Clear any existing session to start fresh for OAuth
    # This ensures we don't have conflicting session state
    # Preserve signup flow state before clearing
    signup_flow = session.get("signup_flow")
    signup_step = session.get("signup_step")

    session.clear()

    # Restore signup flow state (needed for new user registration)
    if signup_flow:
        session["signup_flow"] = signup_flow
    if signup_step:
        session["signup_step"] = signup_step

    # Simple OAuth flow - no tenant context preservation needed
    response = oauth.google.authorize_redirect(redirect_uri)

    # Log what's in the session after Authlib stores the state
    logger.debug("session keys after authorize_redirect: %s", list(session.keys()))

    # CRITICAL FIX: Authlib's authorize_redirect() returns a redirect response,
    # but this response bypasses Flask's normal session-saving mechanism.
    # We must explicitly save the session to the response.
    # Flask's session interface saves the session when processing the response
    # through save_session(), but redirect responses from Authlib don't trigger this.
    session.modified = True
    # Explicitly call Flask's session save mechanism on the response
    current_app.session_interface.save_session(current_app, session, response)

    # Log the Set-Cookie header that will be sent
    set_cookie_header = response.headers.get("Set-Cookie", "")
    logger.warning(f"Set-Cookie header (first 200 chars): {set_cookie_header[:200] if set_cookie_header else 'NONE'}")

    return response


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
    response = oauth.google.authorize_redirect(redirect_uri)

    # CRITICAL FIX: Explicitly save session to response (same fix as google_auth)
    # Authlib's authorize_redirect() bypasses Flask's normal session-saving mechanism
    session.modified = True
    current_app.session_interface.save_session(current_app, session, response)

    return response


@auth_bp.route("/auth/google/callback")
def google_callback():
    """Handle Google OAuth callback - simplified version."""
    logger.debug("google_callback hit, args=%s", dict(request.args))

    oauth = current_app.oauth if hasattr(current_app, "oauth") else None
    if not oauth:
        logger.error("OAuth not configured!")
        flash("OAuth not configured", "error")
        return redirect(url_for("auth.login"))

    # Get tenant context from session (stored during OAuth initiation)
    tenant_context = session.get("oauth_tenant_context")

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
            # Preserve tenant context on error - redirect to tenant login to avoid redirect loop
            if tenant_context:
                return redirect(url_for("auth.tenant_login", tenant_id=tenant_context, logged_out=1))
            return redirect(url_for("auth.login", logged_out=1))

        if not token:
            logger.error("OAuth token exchange failed - authorize_access_token() returned None")
            flash("Authentication failed. Please try again.", "error")
            # Preserve tenant context on error
            if tenant_context:
                return redirect(url_for("auth.tenant_login", tenant_id=tenant_context, logged_out=1))
            return redirect(url_for("auth.login", logged_out=1))

        # Extract user info using provider-agnostic helper
        user = extract_user_info(token)

        if not user or not user.get("email"):
            flash("Could not retrieve user information from OAuth provider", "error")
            # Preserve tenant context on error
            if tenant_context:
                return redirect(url_for("auth.tenant_login", tenant_id=tenant_context, logged_out=1))
            return redirect(url_for("auth.login", logged_out=1))

        email = user["email"]
        session["user"] = email
        session["user_name"] = user.get("name", email)
        session["user_picture"] = user.get("picture", "")

        # Mark session as modified to ensure it's saved
        session.modified = True
        logger.info("User authenticated: %s", email)

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
            session.modified = True
            logger.warning(
                f"========== SUPER ADMIN detected, redirecting to core.index. "
                f"Session keys: {list(session.keys())} =========="
            )
            # Check for saved redirect URL
            next_url = session.pop("login_next_url", None)
            if next_url:
                return redirect(next_url)
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

        # Process user_tenants first (primary authorization method via User records)
        for tenant in tenant_access.get("user_tenants", []):
            with get_db_session() as db_session:
                from sqlalchemy import select

                from src.core.database.models import User

                stmt = select(User).filter_by(email=email, tenant_id=tenant.tenant_id)
                existing_user = db_session.scalars(stmt).first()
                is_admin = existing_user.role == "admin" if existing_user else False

            tenant_dict[tenant.tenant_id] = {
                "tenant_id": tenant.tenant_id,
                "name": tenant.name,
                "subdomain": tenant.subdomain,
                "is_admin": is_admin,
            }

        # Process domain_tenant (bulk org access)
        if tenant_access["domain_tenant"]:
            domain_tenant = tenant_access["domain_tenant"]
            if domain_tenant.tenant_id not in tenant_dict:
                tenant_dict[domain_tenant.tenant_id] = {
                    "tenant_id": domain_tenant.tenant_id,
                    "name": domain_tenant.name,
                    "subdomain": domain_tenant.subdomain,
                    "is_admin": True,  # Domain users get admin access
                }

        # Process email_tenants (legacy backwards compatibility)
        for tenant in tenant_access["email_tenants"]:
            # Skip if already added via user record or domain access
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

        # In single-tenant mode, auto-select the tenant (skip selection screen)
        from src.core.config_loader import is_single_tenant_mode

        if is_single_tenant_mode() and len(session["available_tenants"]) == 1:
            # Auto-select the only tenant
            tenant = session["available_tenants"][0]
            tenant_id = tenant["tenant_id"]

            # Ensure User record exists
            from src.admin.domain_access import ensure_user_in_tenant

            user_name = session.get("user_name", email.split("@")[0].title())
            role = "admin" if tenant.get("is_admin") else "viewer"

            try:
                ensure_user_in_tenant(email, tenant_id, role=role, name=user_name)
            except Exception as e:
                logger.error(f"Failed to create User record for {email} in tenant {tenant_id}: {e}")

            session["tenant_id"] = tenant_id
            session["is_tenant_admin"] = tenant.get("is_admin", True)
            session.pop("available_tenants", None)
            flash(f"Welcome {user.get('name', email)}!", "success")
            # Check for saved redirect URL
            next_url = session.pop("login_next_url", None)
            if next_url:
                return redirect(next_url)
            return redirect(url_for("tenants.dashboard", tenant_id=tenant_id))

        # Multi-tenant mode or multiple tenants: show tenant selector
        flash(f"Welcome {user.get('name', email)}!", "success")
        logger.debug("redirecting to select_tenant, tenants=%d", len(session.get("available_tenants", [])))
        session.modified = True

        # Create response and explicitly save session to ensure cookie is set
        from flask import make_response

        response = make_response(redirect(url_for("auth.select_tenant")))

        return response

    except Exception as e:
        logger.error("OAuth callback error: %s: %s", type(e).__name__, e, exc_info=True)
        flash("Authentication failed. Please try again.", "error")
        # Preserve tenant context on error to avoid redirect loop
        if tenant_context:
            return redirect(url_for("auth.tenant_login", tenant_id=tenant_context, logged_out=1))
        return redirect(url_for("auth.login", logged_out=1))


@auth_bp.route("/auth/select-tenant", methods=["GET", "POST"])
def select_tenant():
    """Allow user to select a tenant when they have access to multiple."""
    logger.debug("select_tenant hit, has_user=%s, has_tenants=%s", "user" in session, "available_tenants" in session)

    if "user" not in session or "available_tenants" not in session:
        logger.debug("redirecting to login (missing session data)")
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
                # Check for saved redirect URL
                next_url = session.pop("login_next_url", None)
                if next_url:
                    return redirect(next_url)
                return redirect(url_for("tenants.dashboard", tenant_id=tenant_id))

        flash("Invalid tenant selection", "error")
        return redirect(url_for("auth.select_tenant"))

    from src.core.config_loader import is_single_tenant_mode

    return render_template(
        "choose_tenant.html",
        tenants=session["available_tenants"],
        is_single_tenant=is_single_tenant_mode(),
    )


@auth_bp.route("/logout")
def logout():
    """Log out the current user."""
    # Get tenant_id before clearing session to check for IdP logout URL
    tenant_id = session.get("tenant_id")
    idp_logout_url = None

    if tenant_id:
        from src.core.database.models import TenantAuthConfig

        with get_db_session() as db_session:
            config = db_session.scalars(select(TenantAuthConfig).filter_by(tenant_id=tenant_id)).first()
            if config and config.oidc_logout_url:
                idp_logout_url = config.oidc_logout_url

    session.clear()

    # If IdP logout URL is configured, redirect there
    if idp_logout_url:
        # The IdP logout URL should redirect back to our login page after logout
        # Some IdPs support a post_logout_redirect_uri parameter
        return redirect(idp_logout_url)

    flash("You have been logged out", "info")
    # Add logged_out param to prevent auto-redirect to SSO
    return redirect(url_for("auth.login", logged_out=1))


# Test authentication endpoints (only enabled in test mode)
@auth_bp.route("/test/auth", methods=["POST"])
def test_auth():
    """Test authentication endpoint.

    Works when:
    - ADCP_AUTH_TEST_MODE=true (global override), OR
    - The requested tenant has auth_setup_mode=True (per-tenant setting)
    """
    email = request.form.get("email", "").lower()
    password = request.form.get("password")
    tenant_id = request.form.get("tenant_id")

    # In single-tenant mode, default to "default" tenant if not specified
    from src.core.config_loader import is_single_tenant_mode

    if is_single_tenant_mode() and not tenant_id:
        tenant_id = "default"

    # Check if test auth is allowed
    env_test_mode = os.environ.get("ADCP_AUTH_TEST_MODE", "").lower() == "true"
    tenant_setup_mode = False

    if tenant_id:
        with get_db_session() as db_session:
            tenant = db_session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
            if tenant and hasattr(tenant, "auth_setup_mode"):
                tenant_setup_mode = tenant.auth_setup_mode

    # Allow if env var is set OR tenant is in setup mode
    if not env_test_mode and not tenant_setup_mode:
        abort(404)

    # Check for saved redirect URL from login page
    next_url = session.get("login_next_url")

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
            session["tenant_id"] = tenant_id  # Set tenant_id for authorization checks
            # Use saved redirect URL if available
            if next_url:
                session.pop("login_next_url", None)
                return redirect(next_url)
            return redirect(url_for("tenants.dashboard", tenant_id=tenant_id))
        else:
            # Use saved redirect URL if available
            if next_url:
                session.pop("login_next_url", None)
                return redirect(next_url)
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
            session["tenant_id"] = tenant_id  # Set tenant_id for authorization checks
            # Use saved redirect URL if available
            if next_url:
                session.pop("login_next_url", None)
                return redirect(next_url)
            return redirect(url_for("tenants.dashboard", tenant_id=tenant_id))
        else:
            # Use saved redirect URL if available
            if next_url:
                session.pop("login_next_url", None)
                return redirect(next_url)
            return redirect(url_for("core.index"))

    flash("Invalid test credentials", "error")
    return redirect(request.referrer or url_for("auth.login"))


@auth_bp.route("/test/login")
def test_login_form():
    """Show test login form.

    Works when ADCP_AUTH_TEST_MODE=true as a global override.
    For per-tenant setup mode, use /tenant/<tenant_id>/login instead.
    """
    if os.environ.get("ADCP_AUTH_TEST_MODE", "").lower() != "true":
        abort(404)

    from src.core.config_loader import is_single_tenant_mode

    return render_template("login.html", test_mode=True, test_only=True, single_tenant_mode=is_single_tenant_mode())


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
