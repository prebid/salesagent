"""Public routes blueprint for self-service tenant signup."""

import json
import logging
import secrets
import string
from datetime import UTC, datetime
from decimal import Decimal

from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from sqlalchemy import or_, select

from src.core.database.database_session import get_db_session
from src.core.database.models import AdapterConfig, CurrencyLimit, Principal, Tenant, User

logger = logging.getLogger(__name__)

# Create blueprint (no authentication required)
public_bp = Blueprint("public", __name__)

# Reserved subdomains that cannot be used for tenant signup
RESERVED_SUBDOMAINS = {
    "admin",
    "www",
    "api",
    "mcp",
    "a2a",
    "app",
    "staging",
    "dev",
    "test",
    "prod",
    "production",
}


@public_bp.route("/signup")
def landing():
    """Public landing page for self-service signup."""
    # Only allow signup on main domain, not tenant subdomains
    host = request.headers.get("Host", "")
    approximated_host = request.headers.get("Apx-Incoming-Host")

    # Check if we're on a tenant subdomain
    with get_db_session() as db_session:
        # Check Approximated host first
        if approximated_host:
            tenant = db_session.scalars(select(Tenant).filter_by(virtual_host=approximated_host)).first()
            if tenant:
                # On a tenant domain - redirect to login instead
                flash("Signup is only available at the main site.", "info")
                return redirect(url_for("auth.login"))

        # Check subdomain routing
        if ".sales-agent.scope3.com" in host and not host.startswith("admin."):
            tenant_subdomain = host.split(".")[0]
            if tenant_subdomain and tenant_subdomain != "sales-agent":
                tenant = db_session.scalars(select(Tenant).filter_by(subdomain=tenant_subdomain)).first()
                if tenant:
                    # On a tenant subdomain - redirect to login instead
                    flash("Signup is only available at the main site.", "info")
                    return redirect(url_for("auth.login"))

    # If user is already authenticated, redirect to their dashboard
    if "user" in session:
        # Check if they already have a tenant
        if session.get("tenant_id"):
            return redirect(url_for("tenants.dashboard", tenant_id=session["tenant_id"]))
        # Super admin - redirect to main index
        if session.get("is_super_admin"):
            return redirect(url_for("core.index"))

    return render_template("landing.html")


@public_bp.route("/signup/start")
def signup_start():
    """Initiate Google OAuth for signup flow."""
    # Set signup context in session
    session["signup_flow"] = True
    session["signup_step"] = "oauth"

    # Redirect to Google OAuth
    return redirect(url_for("auth.google_auth"))


@public_bp.route("/signup/onboarding")
def signup_onboarding():
    """Onboarding wizard after Google OAuth (authenticated)."""
    # Verify signup flow is active
    if not session.get("signup_flow"):
        flash("Invalid signup session. Please start again.", "error")
        return redirect(url_for("public.landing"))

    # Verify user is authenticated
    if "user" not in session:
        flash("You must sign in with Google to continue.", "error")
        session["signup_flow"] = True  # Maintain signup context
        return redirect(url_for("public.signup_start"))

    # Get user info from session
    user_email = session.get("user")
    user_name = session.get("user_name", "")

    # Extract domain from email for subdomain suggestion
    email_domain = user_email.split("@")[1] if "@" in user_email else ""
    suggested_subdomain = email_domain.split(".")[0].lower() if email_domain else ""

    # Clean subdomain suggestion (remove invalid characters)
    suggested_subdomain = "".join(c for c in suggested_subdomain if c.isalnum() or c == "-")

    return render_template(
        "signup_onboarding.html",
        user_email=user_email,
        user_name=user_name,
        suggested_subdomain=suggested_subdomain,
    )


@public_bp.route("/signup/provision", methods=["POST"])
def provision_tenant():
    """Provision new tenant from signup form."""
    # Verify signup flow is active
    if not session.get("signup_flow"):
        flash("Invalid signup session. Please start again.", "error")
        return redirect(url_for("public.landing"))

    # Verify user is authenticated
    if "user" not in session:
        flash("You must be signed in to create a tenant.", "error")
        return redirect(url_for("public.signup_start"))

    try:
        # Get form data
        publisher_name = request.form.get("publisher_name", "").strip()
        subdomain = request.form.get("subdomain", "").strip().lower()
        adapter_type = request.form.get("adapter", "mock").strip()

        # Validation
        if not publisher_name:
            flash("Publisher name is required", "error")
            return redirect(url_for("public.signup_onboarding"))

        if not subdomain:
            flash("Subdomain is required", "error")
            return redirect(url_for("public.signup_onboarding"))

        # Validate subdomain format (alphanumeric + hyphens only)
        if not all(c.isalnum() or c == "-" for c in subdomain):
            flash("Subdomain can only contain letters, numbers, and hyphens", "error")
            return redirect(url_for("public.signup_onboarding"))

        # Check if subdomain is reserved
        if subdomain in RESERVED_SUBDOMAINS:
            flash(f"Subdomain '{subdomain}' is reserved and cannot be used", "error")
            return redirect(url_for("public.signup_onboarding"))

        # Check if subdomain already exists
        with get_db_session() as db_session:
            stmt = select(Tenant).filter(or_(Tenant.subdomain == subdomain, Tenant.tenant_id == subdomain))
            existing_tenant = db_session.scalars(stmt).first()

            if existing_tenant:
                flash(f"Subdomain '{subdomain}' is already taken. Please choose another.", "error")
                return redirect(url_for("public.signup_onboarding"))

        # Generate tenant ID and admin token
        tenant_id = f"{subdomain}"
        admin_token = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(32))

        # Get user info from session
        user_email = session.get("user")
        user_name = session.get("user_name", user_email.split("@")[0].title())
        email_domain = user_email.split("@")[1] if "@" in user_email else ""

        # Create tenant
        with get_db_session() as db_session:
            now = datetime.now(UTC)

            # Create tenant record
            new_tenant = Tenant(
                tenant_id=tenant_id,
                name=publisher_name,
                subdomain=subdomain,
                ad_server=adapter_type,
                is_active=True,
                billing_plan="standard",
                created_at=now,
                updated_at=now,
                # Configuration
                enable_axe_signals=True,
                human_review_required=True,
                admin_token=admin_token,
                auto_approve_formats=json.dumps(["display_300x250", "display_728x90"]),
                # Access control
                authorized_emails=json.dumps([user_email.lower()]),
                authorized_domains=json.dumps([email_domain]) if email_domain else None,
                # Default policy settings
                policy_settings=json.dumps(
                    {
                        "enabled": True,
                        "require_manual_review": False,
                        "prohibited_advertisers": [],
                        "prohibited_categories": [],
                        "prohibited_tactics": [],
                    }
                ),
            )
            db_session.add(new_tenant)

            # Create adapter configuration
            adapter_config = AdapterConfig(tenant_id=tenant_id, adapter_type=adapter_type)

            # Adapter-specific configuration
            if adapter_type == "google_ad_manager":
                # Check if GAM OAuth was completed
                if session.get("gam_oauth_completed"):
                    adapter_config.gam_refresh_token = session.pop("gam_refresh_token")
                    adapter_config.gam_network_code = session.pop("gam_network_code", None)
                    session.pop("gam_oauth_completed", None)
                else:
                    # GAM will be configured later through settings
                    pass

            elif adapter_type == "kevel":
                # Get Kevel credentials from form
                kevel_network_id = request.form.get("kevel_network_id", "").strip()
                kevel_api_key = request.form.get("kevel_api_key", "").strip()

                if kevel_network_id and kevel_api_key:
                    adapter_config.kevel_network_id = kevel_network_id
                    adapter_config.kevel_api_key = kevel_api_key
                else:
                    flash("Kevel configuration incomplete. You can configure it later in settings.", "warning")

            elif adapter_type == "mock":
                # Mock adapter needs no additional configuration
                adapter_config.mock_dry_run = False

            db_session.add(adapter_config)

            # Create default currency limit (USD with $10,000 daily budget)
            currency_limit = CurrencyLimit(
                tenant_id=tenant_id,
                currency_code="USD",
                max_daily_package_spend=Decimal("10000.00"),
                min_package_budget=Decimal("100.00"),
            )
            db_session.add(currency_limit)

            # Create or update admin user
            import uuid

            from sqlalchemy.exc import IntegrityError

            # Check if user already exists for this tenant
            stmt = select(User).filter_by(tenant_id=tenant_id, email=user_email.lower())
            existing_user = db_session.scalars(stmt).first()

            if existing_user:
                # Update existing user's last login
                existing_user.last_login = now
                existing_user.is_active = True
                logger.info(f"User {user_email} already exists for tenant {tenant_id}, updating last_login")
            else:
                # Create new user record for this tenant
                admin_user = User(
                    user_id=str(uuid.uuid4()),
                    tenant_id=tenant_id,
                    email=user_email.lower(),
                    name=user_name,
                    role="admin",
                    is_active=True,
                    created_at=now,
                    last_login=now,
                )
                db_session.add(admin_user)
                try:
                    db_session.flush()  # Flush to detect constraint violations before full commit
                    logger.info(f"Created new user {user_email} for tenant {tenant_id}")
                except IntegrityError:
                    # Race condition: another request created the user simultaneously
                    db_session.rollback()
                    logger.warning(
                        f"User {user_email} was created concurrently for tenant {tenant_id}, updating instead"
                    )
                    stmt = select(User).filter_by(tenant_id=tenant_id, email=user_email.lower())
                    existing_user = db_session.scalars(stmt).first()
                    if existing_user:
                        existing_user.last_login = now
                        existing_user.is_active = True

            # Create default principal (for testing/demo purposes)
            default_principal = Principal(
                tenant_id=tenant_id,
                principal_id=f"{tenant_id}_default",
                name=f"{publisher_name} Demo Principal",
                access_token=admin_token,
                platform_mappings=json.dumps(
                    {
                        "mock": {
                            "advertiser_id": f"default_{tenant_id[:8]}",
                            "advertiser_name": f"{publisher_name} Demo",
                        }
                    }
                ),
                created_at=now,
            )
            db_session.add(default_principal)

            db_session.commit()

            # Clear signup flow session flags
            session.pop("signup_flow", None)
            session.pop("signup_step", None)

            # Set tenant context in session
            session["tenant_id"] = tenant_id
            session["is_tenant_admin"] = True
            session["role"] = "admin"

            logger.info(f"New tenant self-provisioned: {tenant_id} by {user_email}")

            # Redirect to completion page
            return redirect(url_for("public.signup_complete", tenant_id=tenant_id))

    except Exception as e:
        logger.error(f"Error provisioning tenant: {e}", exc_info=True)
        flash(f"Error creating your account: {str(e)}", "error")
        return redirect(url_for("public.signup_onboarding"))


@public_bp.route("/signup/complete")
def signup_complete():
    """Signup completion page with next steps."""
    tenant_id = request.args.get("tenant_id")

    # Get tenant info
    with get_db_session() as db_session:
        tenant = db_session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
        if not tenant:
            flash("Tenant not found", "error")
            return redirect(url_for("public.landing"))

    return render_template("signup_complete.html", tenant=tenant)
