"""Core application routes blueprint."""

import json
import logging
import os
import secrets
import string
from datetime import UTC, datetime

from flask import (
    Blueprint,
    Response,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)
from sqlalchemy import select, text

from src.admin.utils import require_auth
from src.admin.utils.audit_decorator import log_admin_action
from src.core.database.database_session import get_db_session
from src.core.database.models import Tenant
from src.core.domain_config import (
    extract_subdomain_from_host,
    is_sales_agent_domain,
)

logger = logging.getLogger(__name__)

# Create blueprint
core_bp = Blueprint("core", __name__)


def get_tenant_from_hostname():
    """Extract tenant from hostname for tenant-specific subdomains."""
    host = request.headers.get("Host", "")

    # Check for Approximated routing headers first
    # Approximated sends Apx-Incoming-Host with the original requested domain
    approximated_host = request.headers.get("Apx-Incoming-Host")
    if approximated_host and not approximated_host.startswith("admin."):
        # Approximated handles all external routing - look up tenant by virtual_host
        with get_db_session() as db_session:
            tenant = db_session.scalars(select(Tenant).filter_by(virtual_host=approximated_host)).first()
            return tenant

    # Fallback to direct domain routing
    if is_sales_agent_domain(host) and not host.startswith("admin."):
        tenant_subdomain = extract_subdomain_from_host(host)
        with get_db_session() as db_session:
            tenant = db_session.scalars(select(Tenant).filter_by(subdomain=tenant_subdomain)).first()
            return tenant
    return None


def render_super_admin_index():
    """Render the super admin index page showing all tenants.

    This is extracted as a helper to avoid redirect loops when admin_index()
    needs to show the same content without redirecting to index().
    """
    from datetime import timedelta

    from sqlalchemy import func
    from sqlalchemy.orm import joinedload

    from src.core.database.models import MediaBuy
    from src.core.tenant_status import is_tenant_ad_server_configured
    from src.services.setup_checklist_service import SetupChecklistService

    with get_db_session() as db_session:
        # Pagination
        page = request.args.get("page", 1, type=int)
        per_page = request.args.get("per_page", 50, type=int)
        per_page = min(per_page, 100)  # Max 100 per page

        # Filters
        config_filter = request.args.get("configured", "all")  # all, configured, not-configured
        activity_filter = request.args.get("activity", "all")  # all, has-activity, no-activity

        # Helper function to apply filters consistently
        def apply_tenant_filters(query, config_filter, activity_filter):
            """Apply configuration and activity filters to tenant query."""
            # Configuration filter
            if config_filter == "configured":
                query = query.where(Tenant.ad_server.isnot(None), Tenant.ad_server != "")
            elif config_filter == "not-configured":
                query = query.where((Tenant.ad_server.is_(None)) | (Tenant.ad_server == ""))

            # Activity filter (use EXISTS for better performance than subquery)
            if activity_filter == "has-activity":
                query = query.where(
                    select(MediaBuy.media_buy_id).where(MediaBuy.tenant_id == Tenant.tenant_id).exists()
                )
            elif activity_filter == "no-activity":
                query = query.where(
                    ~select(MediaBuy.media_buy_id).where(MediaBuy.tenant_id == Tenant.tenant_id).exists()
                )

            return query

        # Build base query with filters
        count_stmt = select(func.count()).select_from(Tenant).where(Tenant.is_active == True)  # noqa: E712
        count_stmt = apply_tenant_filters(count_stmt, config_filter, activity_filter)

        total_tenants = db_session.scalar(count_stmt) or 0

        # Calculate pagination
        total_pages = (total_tenants + per_page - 1) // per_page if total_tenants > 0 else 1
        offset = (page - 1) * per_page

        # Eager load adapter_config to avoid N+1 queries
        stmt = select(Tenant).options(joinedload(Tenant.adapter_config)).filter_by(is_active=True).order_by(Tenant.name)

        # Apply same filters to main query
        stmt = apply_tenant_filters(stmt, config_filter, activity_filter)

        stmt = stmt.limit(per_page).offset(offset)
        tenants = db_session.scalars(stmt).all()

        # Bulk fetch setup status for all tenants on this page (single query per metric)
        tenant_ids = [t.tenant_id for t in tenants]
        setup_statuses = SetupChecklistService.get_bulk_setup_status(tenant_ids)

        # Bulk fetch media buy counts (total and recent) for all tenants on this page
        thirty_days_ago = datetime.now(UTC) - timedelta(days=30)

        # Total media buy counts per tenant
        total_buys_stmt = (
            select(MediaBuy.tenant_id, func.count())
            .where(MediaBuy.tenant_id.in_(tenant_ids))
            .group_by(MediaBuy.tenant_id)
        )
        total_buys_counts = dict(db_session.execute(total_buys_stmt).all())

        # Recent media buy counts per tenant (last 30 days)
        recent_buys_stmt = (
            select(MediaBuy.tenant_id, func.count())
            .where(MediaBuy.tenant_id.in_(tenant_ids))
            .where(MediaBuy.created_at >= thirty_days_ago)
            .group_by(MediaBuy.tenant_id)
        )
        recent_buys_counts = dict(db_session.execute(recent_buys_stmt).all())

        tenant_list = []
        for tenant in tenants:
            # Check if configured
            is_configured = is_tenant_ad_server_configured(tenant.tenant_id)

            # Get counts from bulk queries (default to 0 if tenant has no buys)
            total_buys_count = total_buys_counts.get(tenant.tenant_id, 0)
            recent_buys_count = recent_buys_counts.get(tenant.tenant_id, 0)

            # Get setup status from bulk query results
            setup_status = setup_statuses.get(tenant.tenant_id)

            tenant_list.append(
                {
                    "tenant_id": tenant.tenant_id,
                    "name": tenant.name,
                    "subdomain": tenant.subdomain,
                    "virtual_host": tenant.virtual_host,
                    "is_active": tenant.is_active,
                    "created_at": tenant.created_at,
                    "ad_server": tenant.ad_server,
                    "is_configured": is_configured,
                    "recent_buys_count": recent_buys_count,
                    "total_buys_count": total_buys_count,
                    "has_activity": total_buys_count > 0,
                    "setup_status": setup_status,
                }
            )

    # Get environment info for URL generation
    is_production = os.environ.get("PRODUCTION") == "true"
    mcp_port = int(os.environ.get("ADCP_SALES_PORT", 8080)) if not is_production else None

    return render_template(
        "index.html",
        tenants=tenant_list,
        mcp_port=mcp_port,
        is_production=is_production,
        page=page,
        per_page=per_page,
        total_pages=total_pages,
        total_tenants=total_tenants,
        config_filter=config_filter,
        activity_filter=activity_filter,
    )


@core_bp.route("/")
def index():
    """Main index page - shows landing page or redirects based on mode."""
    from src.core.config_loader import is_single_tenant_mode

    # Check if this is actually an /admin/ request that had its prefix stripped by CustomProxyFix.
    # When request.script_root is "/admin", it means the request came via /admin/ path
    # (e.g., tenant.your-domain.com/admin/). In this case, delegate to admin_index().
    if request.script_root == "/admin":
        return admin_index()

    # Single-tenant mode: root URL ALWAYS shows the landing page (public API info)
    # Admin UI is only accessible at /admin/
    if is_single_tenant_mode():
        with get_db_session() as db_session:
            tenant = db_session.scalars(select(Tenant).filter_by(tenant_id="default")).first()
        if tenant:
            from src.landing.landing_page import generate_tenant_landing_page

            # Build effective host from request
            effective_host = request.headers.get("X-Forwarded-Host", request.host)

            # Use virtual_host if configured
            if tenant.virtual_host:
                effective_host = tenant.virtual_host

            # Convert tenant to dict for landing page generator
            tenant_dict = {
                "tenant_id": tenant.tenant_id,
                "name": tenant.name,
                "subdomain": tenant.subdomain,
                "virtual_host": tenant.virtual_host,
            }
            html_content = generate_tenant_landing_page(tenant_dict, effective_host)
            return Response(html_content, mimetype="text/html")
        # No default tenant yet - redirect to login to set up
        return redirect(url_for("auth.login"))

    # Multi-tenant mode below - behavior depends on authentication
    if "user" not in session:
        # Multi-tenant mode - use centralized routing logic
        from src.core.domain_routing import route_landing_page

        result = route_landing_page(dict(request.headers))

        logger.info(
            f"[LANDING DEBUG] Routing decision: type={result.type}, host={result.effective_host}, "
            f"tenant={'yes' if result.tenant else 'no'}"
        )

        # Admin domain should go to login
        if result.type == "admin":
            logger.info("[LANDING DEBUG] Admin domain detected, redirecting to login")
            return redirect(url_for("auth.login"))

        # Custom domain or subdomain with tenant - show agent landing page
        if result.type in ("custom_domain", "subdomain") and result.tenant:
            logger.info(f"[LANDING DEBUG] Tenant found ({result.type}), showing agent landing page")
            from src.landing.landing_page import generate_tenant_landing_page

            # The condition above ensures tenant is not None
            assert result.tenant is not None, "Tenant must be present for custom_domain/subdomain routing"
            html_content = generate_tenant_landing_page(result.tenant, result.effective_host)
            return Response(html_content, mimetype="text/html")

        # No tenant found - show signup landing
        logger.info("[LANDING DEBUG] No tenant found, redirecting to signup")
        return redirect(url_for("public.landing"))

    # Check if we're on a tenant-specific subdomain
    tenant = get_tenant_from_hostname()
    if tenant:
        # Redirect to tenant dashboard with tenant_id
        return redirect(url_for("tenants.dashboard", tenant_id=tenant.tenant_id))

    # Check if user is super admin (multi-tenant only)
    if session.get("role") == "super_admin":
        # Super admin - show all active tenants with configuration and activity status
        return render_super_admin_index()

    elif session.get("role") in ["tenant_admin", "tenant_user"]:
        # Tenant admin/user - redirect to their tenant dashboard
        tenant_id = session.get("tenant_id")
        if tenant_id:
            return redirect(url_for("tenants.dashboard", tenant_id=tenant_id))
        else:
            return "No tenant associated with your account", 403

    else:
        # Unknown role
        return "Access denied", 403


@core_bp.route("/admin/")
@core_bp.route("/admin")
def admin_index():
    """Admin UI entry point - requires authentication."""
    from src.core.config_loader import is_single_tenant_mode

    logger.debug("admin_index hit, session_keys=%s, has_user=%s", list(session.keys()), "user" in session)

    # Single-tenant mode: always redirect to default tenant dashboard
    # (the @require_tenant_access decorator handles auth redirect)
    if is_single_tenant_mode():
        return redirect(url_for("tenants.dashboard", tenant_id="default"))

    # Multi-tenant mode: check authentication
    if "user" not in session:
        # If on a tenant subdomain, redirect to tenant-specific login
        # This ensures tenant OIDC config is checked before global OAuth
        tenant = get_tenant_from_hostname()
        if tenant:
            return redirect(url_for("auth.tenant_login", tenant_id=tenant.tenant_id))
        return redirect(url_for("auth.login"))

    # Multi-tenant mode: check for tenant context or show tenant selector
    if session.get("role") == "super_admin":
        # Super admin - delegate directly to index() handler instead of redirecting
        # Redirecting to core.index would create an infinite loop because index()
        # delegates back to admin_index() when script_root == "/admin"
        return render_super_admin_index()

    # Regular user - check for tenant context
    tenant_id = session.get("tenant_id")
    if tenant_id:
        return redirect(url_for("tenants.dashboard", tenant_id=tenant_id))

    # No tenant context - show tenant selector
    return redirect(url_for("auth.select_tenant"))


@core_bp.route("/debug/headers")
def debug_headers():
    """Debug endpoint to inspect all incoming headers (for Approximated routing testing)."""
    headers_dict = dict(request.headers)
    detected_tenant = get_tenant_from_hostname()

    debug_info = {
        "all_headers": headers_dict,
        "detected_tenant": (
            {
                "tenant_id": detected_tenant.tenant_id if detected_tenant else None,
                "name": detected_tenant.name if detected_tenant else None,
                "subdomain": detected_tenant.subdomain if detected_tenant else None,
                "virtual_host": detected_tenant.virtual_host if detected_tenant else None,
            }
            if detected_tenant
            else None
        ),
        "routing_analysis": {
            "host_header": request.headers.get("Host"),
            "apx_incoming_host": request.headers.get("Apx-Incoming-Host"),
            "x_forwarded_host": request.headers.get("X-Forwarded-Host"),
            "x_original_host": request.headers.get("X-Original-Host"),
            "x_forwarded_for": request.headers.get("X-Forwarded-For"),
            "user_agent": request.headers.get("User-Agent"),
        },
        "request_info": {
            "remote_addr": request.remote_addr,
            "url": request.url,
            "path": request.path,
            "method": request.method,
        },
    }

    return jsonify(debug_info)


@core_bp.route("/health")
def health():
    """Health check endpoint."""
    try:
        with get_db_session() as db_session:
            db_session.execute(text("SELECT 1"))
            return "OK", 200
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return f"Database connection failed: {str(e)}", 500


@core_bp.route("/health/config")
def health_config():
    """Configuration health check endpoint."""
    try:
        from src.core.startup import validate_startup_requirements

        validate_startup_requirements()
        return (
            jsonify(
                {
                    "status": "healthy",
                    "service": "admin-ui",
                    "component": "configuration",
                    "message": "All configuration validation passed",
                }
            ),
            200,
        )
    except Exception as e:
        logger.error(f"Configuration health check failed: {e}")
        return (
            jsonify({"status": "unhealthy", "service": "admin-ui", "component": "configuration", "error": str(e)}),
            500,
        )


@core_bp.route("/metrics")
def metrics():
    """Prometheus metrics endpoint."""
    from src.core.metrics import get_metrics_text

    return get_metrics_text(), 200, {"Content-Type": "text/plain; charset=utf-8"}


@core_bp.route("/create_tenant", methods=["GET", "POST"])
@require_auth(admin_only=True)
@log_admin_action("create_tenant")
def create_tenant():
    """Create a new tenant."""
    if request.method == "GET":
        return render_template("create_tenant.html")

    # Handle POST request
    try:
        # Get form data
        tenant_name = request.form.get("name", "").strip()
        subdomain = request.form.get("subdomain", "").strip()
        ad_server = request.form.get("ad_server", "").strip() or None  # Default to None, not mock

        if not tenant_name:
            flash("Tenant name is required", "error")
            return render_template("create_tenant.html")

        # Generate tenant ID if not provided
        if not subdomain:
            subdomain = tenant_name.lower().replace(" ", "_").replace("-", "_")
            # Remove non-alphanumeric characters
            subdomain = "".join(c for c in subdomain if c.isalnum() or c == "_")

        tenant_id = f"tenant_{subdomain}"

        # Generate admin token
        admin_token = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(32))

        with get_db_session() as db_session:
            # Check if tenant already exists
            existing = db_session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
            if existing:
                flash(f"Tenant with ID {tenant_id} already exists", "error")
                return render_template("create_tenant.html")

            # Create new tenant
            new_tenant = Tenant(
                tenant_id=tenant_id,
                name=tenant_name,
                subdomain=subdomain,
                is_active=True,
                ad_server=ad_server,
                admin_token=admin_token,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
                # Set default measurement provider (Publisher Ad Server)
                measurement_providers={"providers": ["Publisher Ad Server"], "default": "Publisher Ad Server"},
            )

            # Set default configuration based on ad server
            if ad_server == "google_ad_manager":
                # GAM requires additional configuration
                new_tenant.gam_network_code = request.form.get("gam_network_code", "")
                new_tenant.gam_refresh_token = request.form.get("gam_refresh_token", "")

            # Set feature flags
            new_tenant.max_daily_budget = float(request.form.get("max_daily_budget", "10000"))
            new_tenant.enable_axe_signals = "enable_axe_signals" in request.form
            new_tenant.human_review_required = "human_review_required" in request.form

            # Set authorization settings
            authorized_emails = request.form.get("authorized_emails", "")
            email_list = [e.strip() for e in authorized_emails.split(",") if e.strip()]

            # Automatically add the creator's email to authorized list
            creator_email = session.get("user")
            if creator_email and creator_email not in email_list:
                email_list.append(creator_email)

            if email_list:
                new_tenant.authorized_emails = json.dumps(email_list)

            authorized_domains = request.form.get("authorized_domains", "")
            if authorized_domains:
                new_tenant.authorized_domains = json.dumps(
                    [d.strip() for d in authorized_domains.split(",") if d.strip()]
                )

            db_session.add(new_tenant)
            db_session.commit()

            # Note: No default principal created - principals must be added manually
            # to map to real advertiser accounts in the ad server (GAM, Kevel, etc.)

            flash(f"Tenant '{tenant_name}' created successfully!", "success")
            return redirect(url_for("tenants.dashboard", tenant_id=tenant_id))

    except Exception as e:
        logger.error(f"Error creating tenant: {e}", exc_info=True)
        flash(f"Error creating tenant: {str(e)}", "error")
        return render_template("create_tenant.html")


@core_bp.route("/static/<path:path>")
def send_static(path):
    """Serve static files."""
    return send_from_directory("static", path)


@core_bp.route("/admin/tenant/<tenant_id>/reactivate", methods=["POST"])
@require_auth(admin_only=True)
@log_admin_action("reactivate_tenant")
def reactivate_tenant(tenant_id):
    """Reactivate a deactivated tenant (super admin only)."""
    try:
        # Verify super admin
        if session.get("role") != "super_admin":
            flash("Only super admins can reactivate tenants", "error")
            return redirect(url_for("core.index"))

        with get_db_session() as db_session:
            tenant = db_session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()

            if not tenant:
                flash("Tenant not found", "error")
                return redirect(url_for("core.index"))

            # Already active?
            if tenant.is_active:
                flash(f"Tenant '{tenant.name}' is already active", "warning")
                return redirect(url_for("core.index"))

            # Reactivate tenant
            tenant.is_active = True
            tenant.updated_at = datetime.now(UTC)
            db_session.commit()

            logger.info(
                f"Tenant {tenant_id} ({tenant.name}) reactivated by super admin {session.get('user', 'unknown')}"
            )

            flash(f"Sales agent '{tenant.name}' has been reactivated successfully", "success")
            return redirect(url_for("core.index"))

    except Exception as e:
        logger.error(f"Error reactivating tenant {tenant_id}: {e}", exc_info=True)
        flash(f"Error reactivating sales agent: {str(e)}", "error")
        return redirect(url_for("core.index"))
