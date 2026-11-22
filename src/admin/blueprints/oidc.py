"""OIDC authentication blueprint.

Handles OAuth/OIDC authentication flows for tenant-specific SSO configuration.
"""

import logging

from authlib.integrations.flask_client import OAuth
from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, request, session, url_for
from sqlalchemy import select
from werkzeug.wrappers import Response

from src.admin.auth_utils import OAUTH_CALLBACK_ERRORS, extract_user_info
from src.admin.utils import require_tenant_access
from src.admin.utils.helpers import (
    TENANT_ACCESS_GRANTED,
    clear_session_identity,
    session_caller_email,
    tenant_access,
)
from src.admin.utils.url_policy import json_error_if_url_blocked
from src.core.database.database_session import get_db_session
from src.core.database.integrity import resolve_or_write
from src.core.database.models import Tenant, TenantAuthConfig, User, UserRole
from src.core.operator_errors import safe_error_message
from src.services.auth_config_service import (
    disable_oidc,
    enable_oidc,
    get_auth_config_summary,
    get_oidc_config_for_auth,
    get_or_create_auth_config,
    get_tenant_redirect_uri,
    mark_oidc_verified,
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
@require_tenant_access()
def get_config(tenant_id: str):
    """Get OIDC configuration summary for a tenant."""
    summary = get_auth_config_summary(tenant_id)
    # Return with config key to match frontend expectations and save response format
    return jsonify({"config": summary, **summary})


@oidc_bp.route("/tenant/<tenant_id>/config", methods=["POST"])
@require_tenant_access(role=UserRole.ADMIN)
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

    # Both are stored now and fetched later — discovery_url by authlib's own
    # server_metadata_url= dereference, logout_url by the browser redirect at
    # logout — so both are graded by ingest-time egress policy.
    if discovery_url and (blocked := json_error_if_url_blocked(discovery_url, "OIDC discovery URL")):
        return blocked

    if logout_url and (blocked := json_error_if_url_blocked(logout_url, "OIDC logout URL")):
        return blocked

    # Require client_secret for new configs (no existing secret)
    existing_config = get_or_create_auth_config(tenant_id)
    if not client_secret and not existing_config.oidc_client_secret_encrypted:
        return jsonify({"error": "client_secret is required for new configuration"}), 400

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


@oidc_bp.route("/tenant/<tenant_id>/enable", methods=["POST"])
@require_tenant_access(role=UserRole.ADMIN)
def enable(tenant_id: str):
    """Enable OIDC authentication for a tenant."""
    if enable_oidc(tenant_id):
        # Verify the change persisted
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
@require_tenant_access(role=UserRole.ADMIN)
def disable(tenant_id: str):
    """Disable OIDC authentication for a tenant."""
    disable_oidc(tenant_id)
    return jsonify(
        {
            "success": True,
            "message": "OIDC authentication disabled.",
        }
    )


@oidc_bp.route("/test/<tenant_id>", methods=["POST"])
@require_tenant_access(role=UserRole.ADMIN)
def test_initiate(tenant_id: str):
    """Initiate a test OAuth flow.

    This starts the OAuth flow but redirects to a test success page instead of
    creating a real session. A successful test enables SSO for the tenant, so only a
    tenant admin may start one, and the callback re-checks that the same admin finishes it.
    """
    with get_db_session() as db_session:
        tenant = db_session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
        if not tenant:
            flash("Tenant not found", "error")
            return redirect(url_for("auth.login"))

        # Get OIDC config (doesn't require enabled, just configured). A read: a tenant
        # with nothing configured has nothing to test, and the test writes nothing.
        config = db_session.scalars(select(TenantAuthConfig).filter_by(tenant_id=tenant_id)).first()
        if not config or not config.oidc_client_id:
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

        # Store test flow state, and who started it: the callback finishes the test only
        # for this same admin, still an admin of this tenant.
        session["oidc_test_flow"] = True
        session["oidc_test_tenant_id"] = tenant_id
        session["oidc_test_initiator"] = session_caller_email()

        # Get redirect URI
        redirect_uri = get_tenant_redirect_uri(tenant)

        logger.info(f"Initiating test OIDC flow for tenant {tenant_id}, redirect_uri={redirect_uri}")

        return oauth.test_oidc.authorize_redirect(redirect_uri)


#: The session keys an SSO test keeps between ``test_initiate`` and the callback.
_TEST_FLOW_KEYS = ("oidc_test_flow", "oidc_test_tenant_id", "oidc_test_initiator")


def _forget_test_flow() -> None:
    """Drop any SSO test state from the session: a callback consumes it, a login starts afresh."""
    for key in _TEST_FLOW_KEYS:
        session.pop(key, None)


def _test_flow_still_authorized(tenant_id: str, initiator: str | None) -> bool:
    """Whether the session finishing an SSO test is the admin who started it, still a tenant admin.

    The test round-trips through the identity provider, so the request that was
    authorized (``test_initiate``) is not the one that writes. This re-runs the decision
    ``require_tenant_access(role=UserRole.ADMIN)`` made, on the session as it is now.
    """
    if not initiator or initiator != session_caller_email():
        return False
    return tenant_access(tenant_id, UserRole.ADMIN, "POST") in TENANT_ACCESS_GRANTED


def _back_from_callback(message: str, tenant_id: str, is_test: bool) -> Response:
    """Flash *message* and return to where the failed flow started.

    A test flow started from the tenant's SSO settings and goes back there; a login goes
    back to the login page.
    """
    flash(message, "error")
    if is_test:
        return redirect(url_for("tenants.tenant_settings", tenant_id=tenant_id))
    return redirect(url_for("auth.login"))


@oidc_bp.route("/callback")
def callback():
    """Handle OAuth callback for both test and production flows."""
    # Check if this is a test flow
    is_test = session.get("oidc_test_flow", False)
    test_initiator = session.get("oidc_test_initiator")
    tenant_id = session.get("oidc_test_tenant_id") or session.get("oidc_login_tenant_id")
    _forget_test_flow()

    logger.info(f"OAuth callback: is_test={is_test}, tenant_id={tenant_id}")

    if not tenant_id:
        flash("Invalid OAuth callback - no tenant context", "error")
        return redirect(url_for("auth.login"))

    if is_test and not _test_flow_still_authorized(tenant_id, test_initiator):
        flash("Only an admin of this tenant can test its SSO configuration", "error")
        logger.warning("OIDC test callback refused for tenant %s: not the admin who started it", tenant_id)
        return redirect(url_for("auth.login"))

    with get_db_session() as db_session:
        tenant = db_session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
        if not tenant:
            flash("Tenant not found", "error")
            return redirect(url_for("auth.login"))

        # Get OIDC config directly from this session to avoid nested session issues
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
        except OAUTH_CALLBACK_ERRORS as e:
            return _back_from_callback(safe_error_message(e, "OAuth authentication failed"), tenant_id, is_test)

    if not token:
        return _back_from_callback("OAuth token exchange failed", tenant_id, is_test)

    # Extract user info
    user_info = extract_user_info(token)
    if not user_info or not user_info.get("email"):
        return _back_from_callback("Could not get user email from OAuth provider", tenant_id, is_test)

    email = user_info["email"]

    if is_test:
        # Test flow - mark the config verified for the redirect URI just exercised, then
        # enable SSO through the same code path the admin-only enable route uses.
        mark_oidc_verified(tenant_id, get_tenant_redirect_uri(tenant))
        if enable_oidc(tenant_id):
            logger.info(f"Verified and enabled SSO for tenant {tenant_id}")
        else:
            logger.warning(f"SSO test for tenant {tenant_id} completed but SSO was not enabled")

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
        user_stmt = select(User).filter_by(email=email.lower(), tenant_id=tenant_id)
        user = db_session.scalars(user_stmt).first()

        if not user:
            # Check if user's domain is authorized - auto-create user if so
            tenant = db_session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
            email_domain = email.lower().split("@")[1] if "@" in email else None
            authorized_domains = tenant.authorized_domains or [] if tenant else []

            if email_domain and email_domain in authorized_domains:
                # Auto-create user from authorized domain
                import uuid
                from datetime import UTC, datetime

                new_user = User(
                    user_id=str(uuid.uuid4()),
                    tenant_id=tenant_id,
                    email=email.lower(),
                    name=user_info.get("name", email),
                    # Anyone on an authorized domain is a tenant admin; invite users
                    # individually (Users page) to give them a narrower role.
                    role=UserRole.ADMIN,
                    is_active=True,
                    created_at=datetime.now(UTC),
                )
                winner = resolve_or_write(
                    db_session,
                    conflict=lambda: db_session.scalars(user_stmt).first(),
                    write=lambda: db_session.add(new_user),
                    constraint="uq_users_tenant_email",
                )
                if winner is not None:
                    # A concurrent login for the same address got its row in
                    # first; adopt it, exactly as the pre-check above would have.
                    user = winner
                else:
                    user = new_user
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

        # Create session. Nothing from an earlier sign-in in this browser survives: this
        # session is bound to this tenant and carries no deployment-wide privilege.
        clear_session_identity()
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

        # Store login state. A test flow abandoned at the provider must not turn this
        # login's callback into a test for whichever tenant it named.
        _forget_test_flow()
        session["oidc_login_tenant_id"] = tenant_id

        redirect_uri = get_tenant_redirect_uri(tenant)
        logger.info(f"Initiating OIDC login for tenant {tenant_id}")

        return oauth.login_oidc.authorize_redirect(redirect_uri)
