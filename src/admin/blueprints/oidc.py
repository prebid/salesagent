"""OIDC authentication blueprint.

Handles OAuth/OIDC authentication flows for tenant-specific SSO configuration.
"""

import logging

from authlib.integrations.flask_client import OAuth
from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, request, session, url_for
from sqlalchemy import select

from src.admin.utils import require_tenant_access
from src.core.database.database_session import get_db_session
from src.core.database.models import Tenant, User
from src.services.auth_config_service import (
    disable_oidc,
    enable_oidc,
    get_auth_config_summary,
    get_oidc_config_for_auth,
    get_or_create_auth_config,
    get_tenant_redirect_uri,
    save_oidc_config,
)

logger = logging.getLogger(__name__)

oidc_bp = Blueprint("oidc", __name__, url_prefix="/auth/oidc")


def create_tenant_oauth_client(tenant_id: str):
    """Create an OAuth client for a tenant's OIDC configuration.

    Args:
        tenant_id: The tenant ID

    Returns:
        OAuth client or None if not configured
    """
    config = get_oidc_config_for_auth(tenant_id)
    if not config:
        return None

    # Create a temporary OAuth instance
    oauth = OAuth()
    oauth.init_app(current_app)

    # Register the provider
    oauth.register(
        name=f"tenant_{tenant_id}",
        client_id=config["client_id"],
        client_secret=config["client_secret"],
        server_metadata_url=config["discovery_url"],
        client_kwargs={"scope": config["scopes"]},
    )

    return getattr(oauth, f"tenant_{tenant_id}")


@oidc_bp.route("/tenant/<tenant_id>/config", methods=["GET"])
@require_tenant_access(api_mode=True)
def get_config(tenant_id: str):
    """Get OIDC configuration summary for a tenant."""
    summary = get_auth_config_summary(tenant_id)
    # Return with config key to match frontend expectations and save response format
    return jsonify({"config": summary, **summary})


@oidc_bp.route("/tenant/<tenant_id>/config", methods=["POST"])
@require_tenant_access(api_mode=True)
def save_config(tenant_id: str):
    """Save OIDC configuration for a tenant.

    Expects JSON body with:
    - provider: google, microsoft, or custom
    - client_id: OAuth client ID
    - client_secret: OAuth client secret
    - discovery_url: (optional for known providers) OIDC discovery URL
    - scopes: (optional) OAuth scopes
    - logout_url: (optional) IdP logout URL
    """
    data = request.get_json()
    if not data:
        return jsonify({"error": "Missing request body"}), 400

    provider = data.get("provider")
    client_id = data.get("client_id")
    client_secret = data.get("client_secret")  # Can be empty to keep existing
    discovery_url = data.get("discovery_url")
    scopes = data.get("scopes", "openid email profile")
    logout_url = data.get("logout_url")

    if not provider or not client_id:
        return jsonify({"error": "provider and client_id are required"}), 400

    # Require client_secret for new configs (no existing secret)
    existing_config = get_or_create_auth_config(tenant_id)
    if not client_secret and not existing_config.oidc_client_secret_encrypted:
        return jsonify({"error": "client_secret is required for new configuration"}), 400

    try:
        config = save_oidc_config(
            tenant_id=tenant_id,
            provider=provider,
            client_id=client_id,
            client_secret=client_secret,
            discovery_url=discovery_url,
            scopes=scopes,
            logout_url=logout_url,
        )

        # Get updated summary
        summary = get_auth_config_summary(tenant_id)
        return jsonify(
            {
                "success": True,
                "message": "OIDC configuration saved. Please test the connection before enabling.",
                "config": summary,
            }
        )

    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.error(f"Error saving OIDC config: {e}", exc_info=True)
        return jsonify({"error": "Failed to save configuration"}), 500


@oidc_bp.route("/tenant/<tenant_id>/enable", methods=["POST"])
@require_tenant_access(api_mode=True)
def enable(tenant_id: str):
    """Enable OIDC authentication for a tenant."""
    if enable_oidc(tenant_id):
        # Verify the change persisted
        from src.core.database.models import TenantAuthConfig

        with get_db_session() as db_session:
            config = db_session.scalars(select(TenantAuthConfig).filter_by(tenant_id=tenant_id)).first()
            actual_enabled = config.oidc_enabled if config else False
            logger.info(f"After enable_oidc, oidc_enabled={actual_enabled} for tenant {tenant_id}")

        return jsonify(
            {
                "success": True,
                "message": "OIDC authentication enabled",
                "oidc_enabled": actual_enabled,
            }
        )
    else:
        return jsonify({"error": "Cannot enable OIDC. Please test the configuration first."}), 400


@oidc_bp.route("/tenant/<tenant_id>/disable", methods=["POST"])
@require_tenant_access(api_mode=True)
def disable(tenant_id: str):
    """Disable OIDC authentication for a tenant."""
    disable_oidc(tenant_id)
    return jsonify(
        {
            "success": True,
            "message": "OIDC authentication disabled.",
        }
    )


@oidc_bp.route("/test/<tenant_id>")
def test_initiate(tenant_id: str):
    """Initiate a test OAuth flow.

    This starts the OAuth flow but redirects to a test success page instead of
    creating a real session.
    """
    with get_db_session() as db_session:
        tenant = db_session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
        if not tenant:
            flash("Tenant not found", "error")
            return redirect(url_for("auth.login"))

        # Get OIDC config (doesn't require enabled, just configured)
        config = get_or_create_auth_config(tenant_id)
        if not config.oidc_client_id:
            flash("OIDC not configured for this tenant", "error")
            return redirect(url_for("tenants.tenant_settings", tenant_id=tenant_id))

        # Create OAuth client for this tenant
        oauth = OAuth()
        oauth.init_app(current_app)

        oauth.register(
            name="test_oidc",
            client_id=config.oidc_client_id,
            client_secret=config.oidc_client_secret,
            server_metadata_url=config.oidc_discovery_url,
            client_kwargs={"scope": config.oidc_scopes or "openid email profile"},
        )

        # Store test flow state
        session["oidc_test_flow"] = True
        session["oidc_test_tenant_id"] = tenant_id

        # Get redirect URI
        redirect_uri = get_tenant_redirect_uri(tenant)

        logger.info(f"Initiating test OIDC flow for tenant {tenant_id}, redirect_uri={redirect_uri}")

        return oauth.test_oidc.authorize_redirect(redirect_uri)


@oidc_bp.route("/callback")
def callback():
    """Handle OAuth callback for both test and production flows."""
    try:
        # Check if this is a test flow
        is_test = session.pop("oidc_test_flow", False)
        tenant_id = session.pop("oidc_test_tenant_id", None) or session.get("oidc_login_tenant_id")

        logger.info(f"OAuth callback: is_test={is_test}, tenant_id={tenant_id}")

        if not tenant_id:
            flash("Invalid OAuth callback - no tenant context", "error")
            return redirect(url_for("auth.login"))

        with get_db_session() as db_session:
            tenant = db_session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
            if not tenant:
                flash("Tenant not found", "error")
                return redirect(url_for("auth.login"))

            # Get OIDC config directly from this session to avoid nested session issues
            from src.core.database.models import TenantAuthConfig

            config = db_session.scalars(select(TenantAuthConfig).filter_by(tenant_id=tenant_id)).first()
            if not config or not config.oidc_client_id:
                flash("OIDC not configured", "error")
                return redirect(url_for("auth.login"))

            # Get decrypted secret while session is still active
            client_secret = config.oidc_client_secret
            logger.info(f"OAuth callback: got config for {tenant_id}, client_id={config.oidc_client_id[:20]}...")

            # Create OAuth client - must use same name as the initiate function
            # test_initiate uses "test_oidc", login uses "login_oidc"
            oauth = OAuth()
            oauth.init_app(current_app)

            client_name = "test_oidc" if is_test else "login_oidc"
            oauth.register(
                name=client_name,
                client_id=config.oidc_client_id,
                client_secret=client_secret,
                server_metadata_url=config.oidc_discovery_url,
                client_kwargs={"scope": config.oidc_scopes or "openid email profile"},
            )

            try:
                oauth_client = getattr(oauth, client_name)
                token = oauth_client.authorize_access_token()
                logger.info("OAuth callback: token exchange successful")
            except Exception as e:
                logger.error(f"OAuth token exchange failed: {e}", exc_info=True)
                flash(f"OAuth authentication failed: {e}", "error")
                if is_test:
                    return redirect(url_for("tenants.tenant_settings", tenant_id=tenant_id))
                return redirect(url_for("auth.login"))

        if not token:
            flash("OAuth token exchange failed", "error")
            if is_test:
                return redirect(url_for("tenants.tenant_settings", tenant_id=tenant_id))
            return redirect(url_for("auth.login"))

        # Extract user info
        user_info = extract_user_info(token)
        if not user_info or not user_info.get("email"):
            flash("Could not get user email from OAuth provider", "error")
            if is_test:
                return redirect(url_for("tenants.tenant_settings", tenant_id=tenant_id))
            return redirect(url_for("auth.login"))

        email = user_info["email"]

        if is_test:
            # Test flow - mark config as verified and enable SSO in one transaction
            redirect_uri = get_tenant_redirect_uri(tenant)
            from datetime import UTC, datetime

            from src.core.database.models import TenantAuthConfig

            with get_db_session() as db_session:
                config = db_session.scalars(select(TenantAuthConfig).filter_by(tenant_id=tenant_id)).first()
                if config:
                    config.oidc_verified_at = datetime.now(UTC)
                    config.oidc_verified_redirect_uri = redirect_uri
                    config.oidc_enabled = True
                    config.updated_at = datetime.now(UTC)
                    db_session.commit()
                    logger.info(f"Verified and enabled SSO for tenant {tenant_id}")
                else:
                    logger.warning(f"No auth config found for tenant {tenant_id}")

            return render_template(
                "oidc_test_success.html",
                tenant=tenant,
                tenant_id=tenant_id,
                email=email,
                name=user_info.get("name", email),
            )

        # Production flow - check user exists and create session
        session.pop("oidc_login_tenant_id", None)

        with get_db_session() as db_session:
            user = db_session.scalars(select(User).filter_by(email=email.lower(), tenant_id=tenant_id)).first()

            if not user:
                # Check if user's domain is authorized - auto-create user if so
                tenant = db_session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
                email_domain = email.lower().split("@")[1] if "@" in email else None
                authorized_domains = tenant.authorized_domains or [] if tenant else []

                if email_domain and email_domain in authorized_domains:
                    # Auto-create user from authorized domain
                    import uuid
                    from datetime import UTC, datetime

                    user = User(
                        user_id=str(uuid.uuid4()),
                        tenant_id=tenant_id,
                        email=email.lower(),
                        name=user_info.get("name", email),
                        role="admin",  # RBAC not yet implemented
                        is_active=True,
                        created_at=datetime.now(UTC),
                    )
                    db_session.add(user)
                    db_session.commit()
                    db_session.refresh(user)
                    logger.info(f"Auto-created user {email} from authorized domain {email_domain}")
                else:
                    flash("Access denied. You don't have permission to access this tenant.", "error")
                    logger.warning(f"OIDC login denied: no User record for {email} in tenant {tenant_id}")
                    return redirect(url_for("auth.login"))

            if not user.is_active:
                flash("Your account has been disabled. Please contact your administrator.", "error")
                logger.warning(f"OIDC login denied: user {email} is disabled in tenant {tenant_id}")
                return redirect(url_for("auth.login"))

            # Update user's name from SSO if we got one
            sso_name = user_info.get("name")
            if sso_name and sso_name != user.name:
                user.name = sso_name
                logger.info(f"Updated user {email} name from SSO: {sso_name}")

            # Create session
            session["user"] = user.email
            session["user_name"] = user.name or user.email
            session["tenant_id"] = tenant_id
            session["authenticated"] = True
            session["auth_method"] = "oidc"

            # Update last login
            from datetime import UTC, datetime

            user.last_login = datetime.now(UTC)
            db_session.commit()

            # Extract user info before session closes
            user_display_name = user.name or user.email

        flash(f"Welcome {user_display_name}!", "success")
        # Redirect to original requested URL if available, otherwise dashboard
        next_url = session.pop("login_next_url", None)
        if next_url:
            return redirect(next_url)
        return redirect(url_for("tenants.dashboard", tenant_id=tenant_id))

    except Exception as e:
        logger.error(f"OAuth callback error: {e}", exc_info=True)
        flash(f"Authentication error: {e}", "error")
        return redirect(url_for("auth.login"))


@oidc_bp.route("/login/<tenant_id>")
def login(tenant_id: str):
    """Initiate OIDC login flow for a tenant."""
    with get_db_session() as db_session:
        tenant = db_session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
        if not tenant:
            flash("Tenant not found", "error")
            return redirect(url_for("auth.login"))

        # In setup mode, allow login when OIDC is configured (even if not enabled)
        # This lets users test the full login flow before enabling
        from src.core.database.models import TenantAuthConfig

        auth_config = db_session.scalars(select(TenantAuthConfig).filter_by(tenant_id=tenant_id)).first()

        # Check if OIDC is available
        if not auth_config or not auth_config.oidc_client_id:
            flash("SSO is not available for this tenant", "error")
            return redirect(url_for("auth.login"))

        # If not in setup mode, require OIDC to be enabled
        is_setup_mode = getattr(tenant, "auth_setup_mode", False)
        if not is_setup_mode and not auth_config.oidc_enabled:
            flash("SSO is not available for this tenant", "error")
            return redirect(url_for("auth.login"))

        # Get decrypted secret
        client_secret = auth_config.oidc_client_secret

        # Create OAuth client
        oauth = OAuth()
        oauth.init_app(current_app)

        oauth.register(
            name="login_oidc",
            client_id=auth_config.oidc_client_id,
            client_secret=client_secret,
            server_metadata_url=auth_config.oidc_discovery_url,
            client_kwargs={"scope": auth_config.oidc_scopes or "openid email profile"},
        )

        # Store login state
        session["oidc_login_tenant_id"] = tenant_id

        redirect_uri = get_tenant_redirect_uri(tenant)
        logger.info(f"Initiating OIDC login for tenant {tenant_id}")

        return oauth.login_oidc.authorize_redirect(redirect_uri)


def extract_user_info(token: dict) -> dict | None:
    """Extract user info from OAuth token.

    Handles different provider formats.

    Args:
        token: OAuth token response

    Returns:
        Dict with email, name, picture or None
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

    # Extract email
    email = user.get("email") or user.get("preferred_username") or user.get("upn") or user.get("sub")

    if not email:
        logger.error(f"Could not extract email from user claims: {list(user.keys())}")
        return None

    # Extract name
    name = user.get("name") or user.get("display_name")
    if not name:
        given = user.get("given_name", "")
        family = user.get("family_name", "")
        if given or family:
            name = f"{given} {family}".strip()
    if not name:
        name = email.split("@")[0]

    # Extract picture
    picture = user.get("picture") or user.get("avatar_url") or ""

    return {
        "email": email.lower(),
        "name": name,
        "picture": picture,
    }
