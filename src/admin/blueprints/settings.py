"""Settings management blueprint.

⚠️ ROUTING NOTICE: This file handles SUPERADMIN settings only!
- URL: /admin/settings
- Function: superadmin_settings()
- The tenant_settings() function in this file is UNUSED - actual tenant settings
  are handled by src/admin/blueprints/tenants.py::settings()
"""

import logging
import os
from datetime import UTC, datetime

from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for

from src.admin.utils import require_auth, require_tenant_access
from src.core.database.database_session import get_db_session
from src.core.database.models import Tenant

logger = logging.getLogger(__name__)

# Create blueprints - separate for superadmin and tenant settings
superadmin_settings_bp = Blueprint("superadmin_settings", __name__)
settings_bp = Blueprint("settings", __name__)


# Superadmin settings routes
@superadmin_settings_bp.route("/settings")
@require_auth(admin_only=True)
def superadmin_settings():
    """Superadmin settings page."""
    # GAM OAuth credentials are now configured via environment variables
    gam_client_id = os.environ.get("GAM_OAUTH_CLIENT_ID", "")
    gam_client_secret = os.environ.get("GAM_OAUTH_CLIENT_SECRET", "")

    # Check if credentials are configured
    gam_configured = bool(gam_client_id and gam_client_secret)

    # Show status of environment configuration
    config_items = {
        "gam_oauth_status": {
            "configured": gam_configured,
            "client_id_prefix": gam_client_id[:20] + "..." if len(gam_client_id) > 20 else gam_client_id,
            "description": "GAM OAuth credentials configured via environment variables",
        },
    }

    return render_template(
        "settings.html",
        config_items=config_items,
        gam_configured=gam_configured,
        gam_client_id_prefix=gam_client_id[:20] + "..." if len(gam_client_id) > 20 else gam_client_id,
    )


@superadmin_settings_bp.route("/settings/update", methods=["POST"])
@require_auth(admin_only=True)
def update_admin_settings():
    """Update superadmin settings."""
    # GAM OAuth credentials are now managed via environment variables only
    # This endpoint is kept for future superadmin configuration needs
    flash("GAM OAuth credentials are now configured via environment variables. No settings to update here.", "info")
    return redirect(url_for("superadmin_settings.superadmin_settings"))


# POST-only routes for updating tenant settings
# GET requests for settings are handled by src/admin/blueprints/tenants.py::settings()


@settings_bp.route("/general", methods=["POST"])
@require_tenant_access()
def update_general(tenant_id):
    """Update general tenant settings."""
    try:
        # Get the tenant name from the form field named "name"
        tenant_name = request.form.get("name", "").strip()

        if not tenant_name:
            flash("Tenant name is required", "error")
            return redirect(url_for("tenants.tenant_settings", tenant_id=tenant_id, section="general"))

        with get_db_session() as db_session:
            tenant = db_session.query(Tenant).filter_by(tenant_id=tenant_id).first()
            if not tenant:
                flash("Tenant not found", "error")
                return redirect(url_for("core.index"))

            # Update tenant with form data
            tenant.name = tenant_name

            # Update virtual_host if provided
            if "virtual_host" in request.form:
                virtual_host = request.form.get("virtual_host", "").strip()
                if virtual_host:
                    # Basic validation for virtual host format
                    # Check for invalid patterns first
                    if ".." in virtual_host or virtual_host.startswith(".") or virtual_host.endswith("."):
                        flash("Virtual host cannot contain consecutive dots or start/end with dots", "error")
                        return redirect(url_for("tenants.tenant_settings", tenant_id=tenant_id, section="general"))

                    # Then check allowed characters
                    if not virtual_host.replace("-", "").replace(".", "").replace("_", "").isalnum():
                        flash(
                            "Virtual host must contain only alphanumeric characters, dots, hyphens, and underscores",
                            "error",
                        )
                        return redirect(url_for("tenants.tenant_settings", tenant_id=tenant_id, section="general"))

                    # Check if virtual host is already in use by another tenant
                    existing_tenant = db_session.query(Tenant).filter_by(virtual_host=virtual_host).first()
                    if existing_tenant and existing_tenant.tenant_id != tenant_id:
                        flash("This virtual host is already in use by another tenant", "error")
                        return redirect(url_for("tenants.tenant_settings", tenant_id=tenant_id, section="general"))

                tenant.virtual_host = virtual_host or None

            # Update other fields if they exist
            if "max_daily_budget" in request.form:
                try:
                    tenant.max_daily_budget = int(request.form.get("max_daily_budget", 10000))
                except ValueError:
                    tenant.max_daily_budget = 10000

            if "enable_axe_signals" in request.form:
                tenant.enable_axe_signals = request.form.get("enable_axe_signals") == "on"
            else:
                tenant.enable_axe_signals = False

            if "human_review_required" in request.form:
                tenant.human_review_required = request.form.get("human_review_required") == "on"
            else:
                tenant.human_review_required = False

            tenant.updated_at = datetime.now(UTC)
            db_session.commit()

            flash("General settings updated successfully", "success")

    except Exception as e:
        logger.error(f"Error updating general settings: {e}", exc_info=True)
        flash(f"Error updating settings: {str(e)}", "error")

    return redirect(url_for("tenants.tenant_settings", tenant_id=tenant_id, section="general"))


@settings_bp.route("/adapter", methods=["POST"])
@require_tenant_access()
def update_adapter(tenant_id):
    """Update the active adapter for a tenant."""
    try:
        # Support both JSON (from our frontend) and form data (from tests)
        if request.is_json:
            new_adapter = request.json.get("adapter")
        else:
            new_adapter = request.form.get("adapter")

        if not new_adapter:
            if request.is_json:
                return jsonify({"success": False, "error": "No adapter selected"}), 400
            flash("No adapter selected", "error")
            return redirect(url_for("tenants.tenant_settings", tenant_id=tenant_id, section="adapter"))

        with get_db_session() as db_session:
            tenant = db_session.query(Tenant).filter_by(tenant_id=tenant_id).first()
            if not tenant:
                if request.is_json:
                    return jsonify({"success": False, "error": "Tenant not found"}), 404
                flash("Tenant not found", "error")
                return redirect(url_for("core.index"))

            # Update or create adapter config
            adapter_config_obj = tenant.adapter_config
            if adapter_config_obj:
                # Update existing config
                adapter_config_obj.adapter_type = new_adapter
            else:
                # Create new config
                from src.core.database.models import AdapterConfig

                adapter_config_obj = AdapterConfig(tenant_id=tenant_id, adapter_type=new_adapter)
                db_session.add(adapter_config_obj)

            # Handle adapter-specific configuration
            if new_adapter == "google_ad_manager":
                if request.is_json:
                    network_code = (
                        request.json.get("gam_network_code", "").strip() if request.json.get("gam_network_code") else ""
                    )
                    manual_approval = request.json.get("gam_manual_approval", False)
                else:
                    network_code = request.form.get("gam_network_code", "").strip()
                    manual_approval = request.form.get("gam_manual_approval") == "on"

                if network_code:
                    adapter_config_obj.gam_network_code = network_code
                adapter_config_obj.gam_manual_approval_required = manual_approval
            elif new_adapter == "mock":
                if request.is_json:
                    dry_run = request.json.get("mock_dry_run", False)
                else:
                    dry_run = request.form.get("mock_dry_run") == "on"
                adapter_config_obj.mock_dry_run = dry_run

            # Update the tenant
            tenant.ad_server = new_adapter
            tenant.updated_at = datetime.now(UTC)
            db_session.commit()

            # Return appropriate response based on request type
            if request.is_json:
                return jsonify({"success": True, "message": f"Adapter changed to {new_adapter}"}), 200

            flash(f"Adapter changed to {new_adapter}", "success")
            return redirect(url_for("tenants.tenant_settings", tenant_id=tenant_id, section="adapter"))

    except Exception as e:
        logger.error(f"Error updating adapter: {e}", exc_info=True)

        if request.is_json:
            return jsonify({"success": False, "error": str(e)}), 400

        flash(f"Error updating adapter: {str(e)}", "error")
        return redirect(url_for("tenants.tenant_settings", tenant_id=tenant_id, section="adapter"))


@settings_bp.route("/slack", methods=["POST"])
@require_tenant_access()
def update_slack(tenant_id):
    """Update Slack integration settings."""
    try:
        webhook_url = request.form.get("slack_webhook_url", "").strip()

        with get_db_session() as db_session:
            tenant = db_session.query(Tenant).filter_by(tenant_id=tenant_id).first()
            if not tenant:
                flash("Tenant not found", "error")
                return redirect(url_for("core.index"))

            # Update Slack webhook
            tenant.slack_webhook_url = webhook_url
            tenant.updated_at = datetime.now(UTC)
            db_session.commit()

            if webhook_url:
                flash("Slack integration updated successfully", "success")
            else:
                flash("Slack integration disabled", "info")

    except Exception as e:
        logger.error(f"Error updating Slack settings: {e}", exc_info=True)
        flash(f"Error updating Slack settings: {str(e)}", "error")

    return redirect(url_for("tenants.tenant_settings", tenant_id=tenant_id, section="integrations"))


@settings_bp.route("/signals", methods=["POST"])
@require_tenant_access()
def update_signals(tenant_id):
    """Update signals discovery agent settings."""

    try:
        # Get form data
        enabled = request.form.get("signals_enabled") == "on"
        upstream_url = request.form.get("signals_upstream_url", "").strip()
        upstream_token = request.form.get("signals_auth_token", "").strip()
        auth_header = request.form.get("signals_auth_header", "x-adcp-auth").strip()
        timeout = int(request.form.get("signals_timeout", "30"))
        forward_promoted_offering = request.form.get("signals_forward_offering") == "on"
        fallback_to_database = request.form.get("signals_fallback") == "on"

        # Validate required fields if enabled
        if enabled and not upstream_url:
            flash("Upstream URL is required when signals discovery is enabled", "error")
            return redirect(url_for("tenants.tenant_settings", tenant_id=tenant_id, section="integrations"))

        # Validate timeout range
        if timeout < 5 or timeout > 120:
            flash("Timeout must be between 5 and 120 seconds", "error")
            return redirect(url_for("tenants.tenant_settings", tenant_id=tenant_id, section="integrations"))

        # Create configuration object
        signals_config = {
            "enabled": enabled,
            "upstream_url": upstream_url,
            "upstream_token": upstream_token,
            "auth_header": auth_header,
            "timeout": timeout,
            "forward_promoted_offering": forward_promoted_offering,
            "fallback_to_database": fallback_to_database,
            "updated_at": datetime.now(UTC).isoformat(),
        }

        with get_db_session() as db_session:
            tenant = db_session.query(Tenant).filter_by(tenant_id=tenant_id).first()
            if not tenant:
                flash("Tenant not found", "error")
                return redirect(url_for("core.index"))

            # Update signals agent configuration
            tenant.signals_agent_config = signals_config
            tenant.updated_at = datetime.now(UTC)
            db_session.commit()

            if enabled:
                flash("Signals discovery agent configured successfully", "success")
            else:
                flash("Signals discovery agent disabled", "info")

    except ValueError as e:
        logger.error(f"Invalid timeout value: {e}")
        flash("Invalid timeout value - must be a number between 5 and 120", "error")
    except Exception as e:
        logger.error(f"Error updating signals settings: {e}", exc_info=True)
        flash(f"Error updating signals settings: {str(e)}", "error")

    return redirect(url_for("tenants.tenant_settings", tenant_id=tenant_id, section="integrations"))


@settings_bp.route("/test_signals", methods=["POST"])
@require_tenant_access()
def test_signals(tenant_id):
    """Test connection to signals discovery agent."""
    import asyncio
    import time

    from fastmcp.client import Client
    from fastmcp.client.transports import StreamableHttpTransport

    try:
        data = request.get_json()
        upstream_url = data.get("upstream_url", "").strip()
        upstream_token = data.get("upstream_token", "").strip()
        auth_header = data.get("auth_header", "x-adcp-auth").strip()

        if not upstream_url:
            return jsonify({"success": False, "error": "Upstream URL is required"}), 400

        async def test_connection():
            """Test the connection to the signals agent."""
            start_time = time.time()

            try:
                # Set up headers
                headers = {}
                if upstream_token:
                    headers[auth_header] = upstream_token

                # Create MCP client
                transport = StreamableHttpTransport(url=upstream_url, headers=headers)
                client = Client(transport=transport)

                async with client:
                    # Try to call a simple test endpoint or get_signals with empty brief
                    try:
                        # First try to get server info/capabilities
                        result = await asyncio.wait_for(client.call_tool("get_signals", {"brief": "test"}), timeout=10)

                        end_time = time.time()
                        response_time = int((end_time - start_time) * 1000)

                        return {
                            "success": True,
                            "server_info": "AdCP Signals Discovery Agent",
                            "response_time": response_time,
                            "signals_count": len(result.get("signals", [])) if result else 0,
                        }

                    except Exception as tool_error:
                        # If get_signals fails, the server might still be reachable
                        # Try a basic connection test
                        end_time = time.time()
                        response_time = int((end_time - start_time) * 1000)

                        # If we got here, at least the transport connected
                        return {
                            "success": True,
                            "server_info": f"Server reachable (tool error: {str(tool_error)[:100]})",
                            "response_time": response_time,
                            "note": "Server connected but get_signals tool may not be available",
                        }

            except Exception as e:
                end_time = time.time()
                response_time = int((end_time - start_time) * 1000)

                return {"success": False, "error": str(e), "response_time": response_time}

        # Run the async test
        result = asyncio.run(test_connection())

        if result["success"]:
            return jsonify(result), 200
        else:
            return jsonify(result), 400

    except Exception as e:
        logger.error(f"Error testing signals connection: {e}", exc_info=True)
        return jsonify({"success": False, "error": f"Test failed: {str(e)}"}), 500


# Domain and Email Management Routes
@settings_bp.route("/domains/add", methods=["POST"])
@require_tenant_access()
def add_authorized_domain(tenant_id):
    """Add domain to tenant's authorized domains list."""
    from src.admin.domain_access import add_authorized_domain as add_domain

    try:
        domain = request.form.get("domain", "").strip().lower()

        if not domain:
            flash("Domain is required", "error")
            return redirect(url_for("tenants.tenant_settings", tenant_id=tenant_id, section="access"))

        # Basic domain validation
        if not domain or "." not in domain or "@" in domain:
            flash("Please enter a valid domain (e.g., company.com)", "error")
            return redirect(url_for("tenants.tenant_settings", tenant_id=tenant_id, section="access"))

        if add_domain(tenant_id, domain):
            flash(f"Domain '{domain}' added successfully", "success")
        else:
            flash(f"Failed to add domain '{domain}'. It may already exist or be restricted.", "error")

    except Exception as e:
        logger.error(f"Error adding domain: {e}", exc_info=True)
        flash("Error adding domain", "error")

    return redirect(url_for("tenants.tenant_settings", tenant_id=tenant_id, section="access"))


@settings_bp.route("/domains/remove", methods=["POST"])
@require_tenant_access()
def remove_authorized_domain(tenant_id):
    """Remove domain from tenant's authorized domains list."""
    from src.admin.domain_access import remove_authorized_domain as remove_domain

    try:
        domain = request.form.get("domain", "").strip().lower()

        if not domain:
            flash("Domain is required", "error")
            return redirect(url_for("tenants.tenant_settings", tenant_id=tenant_id, section="access"))

        if remove_domain(tenant_id, domain):
            flash(f"Domain '{domain}' removed successfully", "success")
        else:
            flash(f"Failed to remove domain '{domain}'", "error")

    except Exception as e:
        logger.error(f"Error removing domain: {e}", exc_info=True)
        flash("Error removing domain", "error")

    return redirect(url_for("tenants.tenant_settings", tenant_id=tenant_id, section="access"))


@settings_bp.route("/emails/add", methods=["POST"])
@require_tenant_access()
def add_authorized_email(tenant_id):
    """Add email to tenant's authorized emails list."""
    from src.admin.domain_access import add_authorized_email as add_email

    try:
        email = request.form.get("email", "").strip().lower()

        if not email:
            flash("Email is required", "error")
            return redirect(url_for("tenants.tenant_settings", tenant_id=tenant_id, section="access"))

        # Basic email validation
        if not email or "@" not in email or "." not in email.split("@")[1]:
            flash("Please enter a valid email address", "error")
            return redirect(url_for("tenants.tenant_settings", tenant_id=tenant_id, section="access"))

        if add_email(tenant_id, email):
            flash(f"Email '{email}' added successfully", "success")
        else:
            flash(f"Failed to add email '{email}'. It may already exist or be restricted.", "error")

    except Exception as e:
        logger.error(f"Error adding email: {e}", exc_info=True)
        flash("Error adding email", "error")

    return redirect(url_for("tenants.tenant_settings", tenant_id=tenant_id, section="access"))


@settings_bp.route("/emails/remove", methods=["POST"])
@require_tenant_access()
def remove_authorized_email(tenant_id):
    """Remove email from tenant's authorized emails list."""
    from src.admin.domain_access import remove_authorized_email as remove_email

    try:
        email = request.form.get("email", "").strip().lower()

        if not email:
            flash("Email is required", "error")
            return redirect(url_for("tenants.tenant_settings", tenant_id=tenant_id, section="access"))

        if remove_email(tenant_id, email):
            flash(f"Email '{email}' removed successfully", "success")
        else:
            flash(f"Failed to remove email '{email}'", "error")

    except Exception as e:
        logger.error(f"Error removing email: {e}", exc_info=True)
        flash("Error removing email", "error")

    return redirect(url_for("tenants.tenant_settings", tenant_id=tenant_id, section="access"))


# Test route for domain access functionality
@settings_bp.route("/access/test", methods=["POST"])
@require_tenant_access()
def test_domain_access(tenant_id):
    """Test email access for this tenant."""
    from src.admin.domain_access import get_user_tenant_access

    try:
        test_email = request.form.get("test_email", "").strip().lower()

        if not test_email:
            flash("Email is required for testing", "error")
            return redirect(url_for("tenants.tenant_settings", tenant_id=tenant_id, section="access"))

        # Test access for this email
        tenant_access = get_user_tenant_access(test_email)

        # Check if this tenant is in the results
        has_access = False
        access_type = None

        if tenant_access["domain_tenant"] and tenant_access["domain_tenant"].tenant_id == tenant_id:
            has_access = True
            access_type = "domain"

        for tenant in tenant_access["email_tenants"]:
            if tenant.tenant_id == tenant_id:
                has_access = True
                access_type = "email"
                break

        if has_access:
            flash(f"✅ Email '{test_email}' would have {access_type} access to this tenant", "success")
        else:
            flash(f"❌ Email '{test_email}' would NOT have access to this tenant", "warning")

    except Exception as e:
        logger.error(f"Error testing domain access: {e}", exc_info=True)
        flash("Error testing email access", "error")

    return redirect(url_for("tenants.tenant_settings", tenant_id=tenant_id, section="access"))
