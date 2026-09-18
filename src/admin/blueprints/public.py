"""Public routes blueprint for self-service tenant signup."""

import logging
from datetime import UTC, datetime
from decimal import Decimal

from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from sqlalchemy import select

from src.admin.blueprints.core import get_tenant_from_hostname
from src.core.database.database_session import get_db_session
from src.core.database.integrity import resolve_or_write
from src.core.database.models import AdapterConfig, CurrencyLimit, Tenant, User
from src.core.database.repositories import TenantLookupRepository
from src.core.database.repositories.principal import PrincipalRepository

logger = logging.getLogger(__name__)

# Create blueprint (no authentication required)
public_bp = Blueprint("public", __name__)


@public_bp.route("/signup")
def landing():
    """Public landing page for self-service signup."""
    # Signup belongs at the main site, so a host that BELONGS to a tenant goes to that
    # tenant's login instead. Which tenant a host names is the admin plane's one lookup;
    # this used to re-derive it from the header ladder and a raw select of its own.
    if get_tenant_from_hostname():
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

    return render_template(
        "signup_onboarding.html",
        user_email=user_email,
        user_name=user_name,
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
        adapter_type = request.form.get("adapter", "mock").strip()

        # Validation
        if not publisher_name:
            flash("Publisher name is required", "error")
            return redirect(url_for("public.signup_onboarding"))

        # Generate random subdomain and tenant ID (prevents subdomain squatting)
        # Format: 8 character hex (e.g., "a7f3d92b")
        import uuid

        tenant_id = str(uuid.uuid4())
        subdomain = tenant_id[:8]  # Use first 8 chars of UUID as subdomain

        # Ensure uniqueness (extremely rare collision, but check anyway)
        with get_db_session() as db_session:
            if TenantLookupRepository(db_session).find_by_id_or_subdomain(tenant_id, subdomain):
                # Collision detected (astronomically rare), retry with new UUID
                tenant_id = str(uuid.uuid4())
                subdomain = tenant_id[:8]

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
                auto_approve_format_ids=["display_300x250", "display_728x90"],
                # Access control
                authorized_emails=[user_email.lower()],
                authorized_domains=[email_domain] if email_domain else None,
                # Default policy settings
                policy_settings={
                    "enabled": True,
                    "require_manual_review": False,
                    "prohibited_advertisers": [],
                    "prohibited_categories": [],
                    "prohibited_tactics": [],
                },
            )
            # Both ids are freshly generated above (a uuid4 and its first 8 chars), so a
            # collision here would be a generator accident, not two users racing for the
            # same name — the SELECT above is a paranoia fast path, not a claim check.
            # The user-facing subdomain race is answered in tenant_management_api.
            db_session.add(new_tenant)  # structural-guard: uniqueness-index-verdict - generated ids, no user race

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

            # The mock adapter needs no additional configuration.

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

            # Adopt an existing user for this tenant rather than inserting.
            # uq_users_tenant_email is the authority, so the same adoption serves
            # the loser of a concurrent signup — and the savepoint keeps the
            # recovery from discarding the tenant, adapter config and currency
            # limit staged above (a session-wide rollback here would, and the
            # default principal added below carries an FK to the tenant).
            existing_user_stmt = select(User).filter_by(tenant_id=tenant_id, email=user_email.lower())

            def adopt_existing_user():
                existing_user = db_session.scalars(existing_user_stmt).first()
                if existing_user:
                    existing_user.last_login = now
                    existing_user.is_active = True
                    logger.info(f"User {user_email} already exists for tenant {tenant_id}, updating last_login")
                    return existing_user
                return None

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
            adopted = resolve_or_write(
                db_session,
                conflict=adopt_existing_user,
                write=lambda: db_session.add(admin_user),
                constraint="uq_users_tenant_email",
            )
            if adopted is None:
                logger.info(f"Created new user {user_email} for tenant {tenant_id}")

            # Create default principal (for testing/demo purposes). Its token is shown once,
            # on the completion page; the row keeps the hash.
            default_principal, demo_token = PrincipalRepository(db_session, tenant_id).issue(
                principal_id=f"{tenant_id}_default",
                name=f"{publisher_name} Demo Principal",
                platform_mappings={
                    "mock": {
                        "advertiser_id": f"default_{tenant_id[:8]}",
                        "advertiser_name": f"{publisher_name} Demo",
                    }
                },
                created_at=now,
            )

            db_session.commit()

            # Clear signup flow session flags
            session.pop("signup_flow", None)
            session.pop("signup_step", None)

            # Set tenant context in session
            session["tenant_id"] = tenant_id
            session["is_tenant_admin"] = True
            session["role"] = "admin"

            logger.info(f"New tenant self-provisioned: {tenant_id} by {user_email}")

            # The plaintext token is rendered into THIS response body and stored nowhere.
            # A flash would put it in the signed session cookie, so it would leave the
            # server in a Set-Cookie header — and in production that cookie is
            # HTTPONLY=False, SAMESITE="None" and shared across subdomains
            # (src/admin/app.py:126-136), which makes a flashed credential readable by
            # any script on the domain and sent on cross-site requests. So there is no
            # flash, no session stash and no redirect: rendering here is what keeps the
            # credential in the response body alone.
            return render_template("signup_complete.html", tenant=new_tenant, api_token=demo_token)

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
