"""Core application routes blueprint."""

import json
import logging
import os
import secrets
import string
from datetime import UTC, datetime

from flask import Blueprint, flash, redirect, render_template, request, send_from_directory, session, url_for
from sqlalchemy import text

from src.admin.utils import require_auth
from src.core.database.database_session import get_db_session
from src.core.database.models import Principal, Tenant

logger = logging.getLogger(__name__)

# Create blueprint
core_bp = Blueprint("core", __name__)


@core_bp.route("/")
@require_auth()
def index():
    """Main index page - redirects based on user role."""
    # Check if user is super admin
    if session.get("role") == "super_admin":
        # Super admin - show all tenants
        with get_db_session() as db_session:
            tenants = db_session.query(Tenant).order_by(Tenant.name).all()
            tenant_list = []
            for tenant in tenants:
                tenant_list.append(
                    {
                        "tenant_id": tenant.tenant_id,
                        "name": tenant.name,
                        "subdomain": tenant.subdomain,
                        "is_active": tenant.is_active,
                        "created_at": tenant.created_at,
                    }
                )
        return render_template("index.html", tenants=tenant_list)

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


@core_bp.route("/create_tenant", methods=["GET", "POST"])
@require_auth(admin_only=True)
def create_tenant():
    """Create a new tenant."""
    if request.method == "GET":
        return render_template("create_tenant.html")

    # Handle POST request
    try:
        # Get form data
        tenant_name = request.form.get("name", "").strip()
        subdomain = request.form.get("subdomain", "").strip()
        ad_server = request.form.get("ad_server", "mock").strip()

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
            existing = db_session.query(Tenant).filter_by(tenant_id=tenant_id).first()
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
            if authorized_emails:
                new_tenant.authorized_emails = json.dumps(
                    [e.strip() for e in authorized_emails.split(",") if e.strip()]
                )

            authorized_domains = request.form.get("authorized_domains", "")
            if authorized_domains:
                new_tenant.authorized_domains = json.dumps(
                    [d.strip() for d in authorized_domains.split(",") if d.strip()]
                )

            db_session.add(new_tenant)

            # Create default principal for the tenant
            default_principal = Principal(
                tenant_id=tenant_id,
                principal_id=f"{tenant_id}_default",
                name=f"{tenant_name} Default Principal",
                access_token=admin_token,  # Use same token for simplicity
                platform_mappings=json.dumps(
                    {"mock": {"advertiser_id": f"default_{tenant_id[:8]}", "advertiser_name": f"{tenant_name} Default"}}
                ),
                created_at=datetime.now(UTC),
            )
            db_session.add(default_principal)

            db_session.commit()

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


@core_bp.route("/mcp-test")
@require_auth(admin_only=True)
def mcp_test():
    """MCP protocol testing interface for super admins."""
    # Get all tenants and their principals
    with get_db_session() as db_session:
        # Get tenants
        tenant_objs = db_session.query(Tenant).filter_by(is_active=True).order_by(Tenant.name).all()
        tenants = []
        for tenant in tenant_objs:
            tenants.append(
                {
                    "tenant_id": tenant.tenant_id,
                    "name": tenant.name,
                    "subdomain": tenant.subdomain,
                }
            )

        # Get all principals with their tenant info
        principal_objs = (
            db_session.query(Principal)
            .join(Tenant)
            .filter(Tenant.is_active)
            .order_by(Tenant.name, Principal.name)
            .all()
        )
        principals = []
        for principal in principal_objs:
            # Get the tenant name via relationship or separate query
            tenant_name = db_session.query(Tenant.name).filter_by(tenant_id=principal.tenant_id).scalar()
            principals.append(
                {
                    "principal_id": principal.principal_id,
                    "name": principal.name,
                    "tenant_id": principal.tenant_id,
                    "access_token": principal.access_token,
                    "tenant_name": tenant_name,
                }
            )

    # Get server URLs - use production URLs if in production, otherwise localhost
    if os.environ.get("PRODUCTION") == "true":
        # In production, both servers are accessible at the main domain
        mcp_server_url = "https://adcp-sales-agent.fly.dev/mcp"  # Remove trailing slash
        a2a_server_url = "https://adcp-sales-agent.fly.dev/a2a/"
    else:
        # In development, use localhost with the configured ports from environment
        # Default to common development ports if not set
        mcp_port = int(os.environ.get("ADCP_SALES_PORT", 8080))
        a2a_port = int(os.environ.get("A2A_PORT", 8091))
        mcp_server_url = f"http://localhost:{mcp_port}/mcp"  # Remove trailing slash
        a2a_server_url = f"http://localhost:{a2a_port}/a2a/"

    return render_template(
        "mcp_test.html",
        tenants=tenants,
        principals=principals,
        server_url=mcp_server_url,  # Keep legacy name for MCP server
        mcp_server_url=mcp_server_url,
        a2a_server_url=a2a_server_url,
    )
