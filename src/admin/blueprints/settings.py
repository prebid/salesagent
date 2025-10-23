"""Settings management blueprint.

⚠️ ROUTING NOTICE: This file handles TENANT MANAGEMENT settings only!
- URL: /admin/settings
- Function: tenant_management_settings()
- The tenant_settings() function in this file is UNUSED - actual tenant settings
  are handled by src/admin/blueprints/tenants.py::settings()
"""

import logging
import os
from datetime import UTC, datetime

from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for
from sqlalchemy import select

from src.admin.utils import require_auth, require_tenant_access
from src.admin.utils.audit_decorator import log_admin_action
from src.core.database.database_session import get_db_session
from src.core.database.models import Tenant

logger = logging.getLogger(__name__)

# Create blueprints - separate for tenant management and tenant settings
tenant_management_settings_bp = Blueprint("tenant_management_settings", __name__)
settings_bp = Blueprint("settings", __name__)


def validate_naming_template(template: str, field_name: str) -> str | None:
    """Validate naming template.

    Returns error message if invalid, None if valid.
    """
    if not template:
        return f"{field_name} cannot be empty"

    if len(template) > 500:
        return f"{field_name} exceeds 500 character limit ({len(template)} chars)"

    # Check for balanced braces
    if template.count("{") != template.count("}"):
        return f"{field_name} has unbalanced braces"

    # Check for empty variable names
    if "{}" in template:
        return f"{field_name} contains empty variable placeholder {{}}"

    return None


def validate_policy_list(
    items: list[str], field_name: str, max_items: int = 100, max_length: int = 500
) -> tuple[list[str], str | None]:
    """Validate and sanitize policy rule lists.

    Args:
        items: List of policy rules to validate
        field_name: Name of the field for error messages
        max_items: Maximum number of items allowed (default: 100)
        max_length: Maximum length per item (default: 500)

    Returns:
        Tuple of (validated_items, error_message)
        If validation passes, error_message is None
    """
    if len(items) > max_items:
        return [], f"{field_name}: Maximum {max_items} items allowed (received {len(items)})"

    validated = []
    for idx, item in enumerate(items):
        # Normalize whitespace (remove control characters, multiple spaces)
        sanitized = " ".join(item.split())

        if len(sanitized) > max_length:
            return [], f"{field_name}: Item {idx + 1} exceeds {max_length} characters: '{sanitized[:50]}...'"

        # Check for potentially dangerous characters (HTML, scripts)
        if any(char in sanitized for char in ["<", ">", "{", "}"]):
            return [], f"{field_name}: Item {idx + 1} contains invalid characters: '{sanitized[:50]}...'"

        if sanitized:  # Only add non-empty after sanitization
            validated.append(sanitized)

    return validated, None


# Tenant management settings routes
@tenant_management_settings_bp.route("/settings")
@require_auth(admin_only=True)
def tenant_management_settings():
    """Tenant management settings page."""
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


@tenant_management_settings_bp.route("/settings/update", methods=["POST"])
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
@log_admin_action("update_general_settings")
def update_general(tenant_id):
    """Update general tenant settings."""
    try:
        # Get the tenant name from the form field named "name"
        tenant_name = request.form.get("name", "").strip()

        if not tenant_name:
            flash("Tenant name is required", "error")
            return redirect(url_for("tenants.tenant_settings", tenant_id=tenant_id, section="general"))

        with get_db_session() as db_session:
            tenant = db_session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
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
                    existing_tenant = db_session.scalars(select(Tenant).filter_by(virtual_host=virtual_host)).first()
                    if existing_tenant and existing_tenant.tenant_id != tenant_id:
                        flash("This virtual host is already in use by another tenant", "error")
                        return redirect(url_for("tenants.tenant_settings", tenant_id=tenant_id, section="general"))

                tenant.virtual_host = virtual_host or None

            # Update currency limits
            from decimal import Decimal, InvalidOperation

            from src.core.database.models import CurrencyLimit

            # Get all existing currency limits
            stmt = select(CurrencyLimit).filter_by(tenant_id=tenant_id)
            existing_limits = {limit.currency_code: limit for limit in db_session.scalars(stmt).all()}

            # Process currency_limits form data
            # Format: currency_limits[USD][min_package_budget], currency_limits[USD][max_daily_package_spend]
            processed_currencies = set()

            for key in request.form.keys():
                if key.startswith("currency_limits["):
                    # Extract currency code from key like "currency_limits[USD][min_package_budget]"
                    parts = key.split("[")
                    if len(parts) >= 2:
                        currency_code = parts[1].rstrip("]")
                        processed_currencies.add(currency_code)

            # Update or create currency limits
            for currency_code in processed_currencies:
                # Check if marked for deletion
                delete_key = f"currency_limits[{currency_code}][_delete]"
                if delete_key in request.form and request.form.get(delete_key) == "true":
                    # Delete this currency limit
                    if currency_code in existing_limits:
                        db_session.delete(existing_limits[currency_code])
                    continue

                # Get min and max values
                min_key = f"currency_limits[{currency_code}][min_package_budget]"
                max_key = f"currency_limits[{currency_code}][max_daily_package_spend]"

                min_value_str = request.form.get(min_key, "").strip()
                max_value_str = request.form.get(max_key, "").strip()

                try:
                    min_value = Decimal(min_value_str) if min_value_str else None
                    max_value = Decimal(max_value_str) if max_value_str else None
                except (ValueError, InvalidOperation):
                    flash(f"Invalid currency limit values for {currency_code}", "error")
                    return redirect(url_for("tenants.tenant_settings", tenant_id=tenant_id, section="general"))

                # Update or create
                if currency_code in existing_limits:
                    # Update existing
                    limit = existing_limits[currency_code]
                    limit.min_package_budget = min_value
                    limit.max_daily_package_spend = max_value
                    limit.updated_at = datetime.now(UTC)
                else:
                    # Create new
                    limit = CurrencyLimit(
                        tenant_id=tenant_id,
                        currency_code=currency_code,
                        min_package_budget=min_value,
                        max_daily_package_spend=max_value,
                    )
                    db_session.add(limit)

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
@log_admin_action(
    "update_adapter",
    extract_details=lambda r, **kw: {
        "adapter": request.json.get("adapter") if request.is_json else request.form.get("adapter")
    },
)
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
            tenant = db_session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
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
                    refresh_token = (
                        request.json.get("gam_refresh_token", "").strip()
                        if request.json.get("gam_refresh_token")
                        else ""
                    )
                    trafficker_id = (
                        request.json.get("gam_trafficker_id", "").strip()
                        if request.json.get("gam_trafficker_id")
                        else ""
                    )
                    order_name_template = (
                        request.json.get("order_name_template", "").strip()
                        if request.json.get("order_name_template")
                        else ""
                    )
                    line_item_name_template = (
                        request.json.get("line_item_name_template", "").strip()
                        if request.json.get("line_item_name_template")
                        else ""
                    )
                    manual_approval = request.json.get("gam_manual_approval", False)
                else:
                    network_code = request.form.get("gam_network_code", "").strip()
                    refresh_token = request.form.get("gam_refresh_token", "").strip()
                    trafficker_id = request.form.get("gam_trafficker_id", "").strip()
                    order_name_template = request.form.get("order_name_template", "").strip()
                    line_item_name_template = request.form.get("line_item_name_template", "").strip()
                    manual_approval = request.form.get("gam_manual_approval") == "on"

                if network_code:
                    adapter_config_obj.gam_network_code = network_code
                if refresh_token:
                    adapter_config_obj.gam_refresh_token = refresh_token
                if trafficker_id:
                    adapter_config_obj.gam_trafficker_id = trafficker_id
                if order_name_template:
                    adapter_config_obj.gam_order_name_template = order_name_template
                if line_item_name_template:
                    adapter_config_obj.gam_line_item_name_template = line_item_name_template
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
@log_admin_action("update_slack")
@require_tenant_access()
def update_slack(tenant_id):
    """Update Slack integration settings."""
    try:
        from src.core.webhook_validator import WebhookURLValidator

        webhook_url = request.form.get("slack_webhook_url", "").strip()
        audit_webhook_url = request.form.get("slack_audit_webhook_url", "").strip()

        # Validate webhook URLs for SSRF protection
        if webhook_url:
            is_valid, error_msg = WebhookURLValidator.validate_webhook_url(webhook_url)
            if not is_valid:
                flash(f"Invalid Slack webhook URL: {error_msg}", "error")
                return redirect(url_for("tenants.tenant_settings", tenant_id=tenant_id, section="integrations"))

        if audit_webhook_url:
            is_valid, error_msg = WebhookURLValidator.validate_webhook_url(audit_webhook_url)
            if not is_valid:
                flash(f"Invalid Slack audit webhook URL: {error_msg}", "error")
                return redirect(url_for("tenants.tenant_settings", tenant_id=tenant_id, section="integrations"))

        with get_db_session() as db_session:
            tenant = db_session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
            if not tenant:
                flash("Tenant not found", "error")
                return redirect(url_for("core.index"))

            # Update Slack webhooks
            tenant.slack_webhook_url = webhook_url if webhook_url else None
            tenant.slack_audit_webhook_url = audit_webhook_url if audit_webhook_url else None
            tenant.updated_at = datetime.now(UTC)
            db_session.commit()

            if webhook_url or audit_webhook_url:
                flash("Slack integration updated successfully", "success")
            else:
                flash("Slack integration disabled", "info")

    except Exception as e:
        logger.error(f"Error updating Slack settings: {e}", exc_info=True)
        flash(f"Error updating Slack settings: {str(e)}", "error")

    return redirect(url_for("tenants.tenant_settings", tenant_id=tenant_id, section="integrations"))


@settings_bp.route("/ai", methods=["POST"])
@log_admin_action("update_ai")
@require_tenant_access()
def update_ai(tenant_id):
    """Update AI services settings (Gemini API key)."""
    try:
        gemini_api_key = request.form.get("gemini_api_key", "").strip()

        with get_db_session() as db_session:
            tenant = db_session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
            if not tenant:
                flash("Tenant not found", "error")
                return redirect(url_for("core.index"))

            # Update Gemini API key (encrypted via property setter)
            if gemini_api_key:
                tenant.gemini_api_key = gemini_api_key
                flash("Gemini API key saved successfully. AI-powered creative review is now enabled.", "success")
            else:
                tenant.gemini_api_key = None
                flash("Gemini API key removed. AI-powered creative review is now disabled.", "warning")

            tenant.updated_at = datetime.now(UTC)
            db_session.commit()

    except Exception as e:
        logger.error(f"Error updating AI settings: {e}", exc_info=True)
        flash(f"Error updating AI settings: {str(e)}", "error")

    return redirect(url_for("tenants.tenant_settings", tenant_id=tenant_id, section="integrations"))


@settings_bp.route("/signals", methods=["POST"])
@log_admin_action("update_signals")
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
            tenant = db_session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
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
@log_admin_action("test_signals")
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
@log_admin_action("add_authorized_domain")
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
@log_admin_action("remove_authorized_domain")
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
@log_admin_action("add_authorized_email")
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
@log_admin_action("remove_authorized_email")
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
@log_admin_action("test_domain_access")
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


@settings_bp.route("/business-rules", methods=["POST"])
@log_admin_action("update_business_rules")
@require_tenant_access()
def update_business_rules(tenant_id):
    """Update business rules (budget, naming, approvals, features)."""
    try:
        # Get form data
        data = request.get_json() if request.is_json else request.form

        with get_db_session() as db_session:
            tenant = db_session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
            if not tenant:
                if request.is_json:
                    return jsonify({"success": False, "error": "Tenant not found"}), 404
                flash("Tenant not found", "error")
                return redirect(url_for("tenants.tenant_settings", tenant_id=tenant_id))

            # Update currency limits (max_daily_budget moved to currency_limits table)
            from decimal import Decimal, InvalidOperation

            from src.core.database.models import CurrencyLimit

            # Get all existing currency limits
            stmt = select(CurrencyLimit).filter_by(tenant_id=tenant_id)
            existing_limits = {limit.currency_code: limit for limit in db_session.scalars(stmt).all()}

            # Process currency_limits form data
            # Format: currency_limits[USD][min_package_budget], currency_limits[USD][max_daily_package_spend]
            processed_currencies = set()

            for key in data.keys():
                if key.startswith("currency_limits["):
                    # Extract currency code from key like "currency_limits[USD][min_package_budget]"
                    parts = key.split("[")
                    if len(parts) >= 2:
                        currency_code = parts[1].rstrip("]")
                        processed_currencies.add(currency_code)

            # Update or create currency limits
            for currency_code in processed_currencies:
                # Check if marked for deletion
                delete_key = f"currency_limits[{currency_code}][_delete]"
                if delete_key in data and data.get(delete_key) in ["true", True]:
                    # Delete this currency limit
                    if currency_code in existing_limits:
                        db_session.delete(existing_limits[currency_code])
                    continue

                # Get min and max values
                min_key = f"currency_limits[{currency_code}][min_package_budget]"
                max_key = f"currency_limits[{currency_code}][max_daily_package_spend]"

                min_value_str = data.get(min_key, "").strip() if data.get(min_key) else ""
                max_value_str = data.get(max_key, "").strip() if data.get(max_key) else ""

                try:
                    min_value = Decimal(min_value_str) if min_value_str else None
                    max_value = Decimal(max_value_str) if max_value_str else None
                except (ValueError, InvalidOperation):
                    if request.is_json:
                        return (
                            jsonify({"success": False, "error": f"Invalid currency limit values for {currency_code}"}),
                            400,
                        )
                    flash(f"Invalid currency limit values for {currency_code}", "error")
                    return redirect(url_for("tenants.tenant_settings", tenant_id=tenant_id, section="business-rules"))

                # Update or create
                if currency_code in existing_limits:
                    # Update existing
                    limit = existing_limits[currency_code]
                    limit.min_package_budget = min_value
                    limit.max_daily_package_spend = max_value
                    limit.updated_at = datetime.now(UTC)
                else:
                    # Create new
                    limit = CurrencyLimit(
                        tenant_id=tenant_id,
                        currency_code=currency_code,
                        min_package_budget=min_value,
                        max_daily_package_spend=max_value,
                    )
                    db_session.add(limit)
            # Update naming templates with validation
            if "order_name_template" in data:
                order_template = data.get("order_name_template", "").strip()
                if order_template:
                    # Validate template
                    validation_error = validate_naming_template(order_template, "Order name template")
                    if validation_error:
                        if request.is_json:
                            return jsonify({"success": False, "error": validation_error}), 400
                        flash(validation_error, "error")
                        return redirect(
                            url_for("tenants.tenant_settings", tenant_id=tenant_id, section="business-rules")
                        )
                    tenant.order_name_template = order_template

            if "line_item_name_template" in data:
                line_item_template = data.get("line_item_name_template", "").strip()
                if line_item_template:
                    # Validate template
                    validation_error = validate_naming_template(line_item_template, "Line item name template")
                    if validation_error:
                        if request.is_json:
                            return jsonify({"success": False, "error": validation_error}), 400
                        flash(validation_error, "error")
                        return redirect(
                            url_for("tenants.tenant_settings", tenant_id=tenant_id, section="business-rules")
                        )
                    tenant.line_item_name_template = line_item_template

            # Update approval workflow
            if "human_review_required" in data:
                tenant.human_review_required = data.get("human_review_required") in [True, "true", "on", 1, "1"]
            elif not request.is_json:
                # Checkbox not present in form data means unchecked
                tenant.human_review_required = False

            # Update creative review settings
            if "approval_mode" in data:
                approval_mode = data.get("approval_mode", "").strip()
                if approval_mode in ["auto-approve", "require-human", "ai-powered"]:
                    tenant.approval_mode = approval_mode

            if "creative_review_criteria" in data:
                creative_review_criteria = data.get("creative_review_criteria")
                if creative_review_criteria is not None:
                    creative_review_criteria = creative_review_criteria.strip()
                    # Allow empty string or set to None if empty
                    tenant.creative_review_criteria = creative_review_criteria if creative_review_criteria else None

            # Update AI policy configuration
            if any(
                key in data
                for key in [
                    "auto_approve_threshold",
                    "auto_reject_threshold",
                    "sensitive_categories",
                    "learn_from_overrides",
                ]
            ):
                # Get existing AI policy or create new dict
                ai_policy = tenant.ai_policy if tenant.ai_policy else {}

                # Update thresholds
                if "auto_approve_threshold" in data:
                    try:
                        threshold = float(data.get("auto_approve_threshold"))
                        if 0.0 <= threshold <= 1.0:
                            ai_policy["auto_approve_threshold"] = threshold
                    except (ValueError, TypeError):
                        pass  # Keep existing value

                if "auto_reject_threshold" in data:
                    try:
                        threshold = float(data.get("auto_reject_threshold"))
                        if 0.0 <= threshold <= 1.0:
                            ai_policy["auto_reject_threshold"] = threshold
                    except (ValueError, TypeError):
                        pass  # Keep existing value

                # Update sensitive categories
                if "sensitive_categories" in data:
                    categories_str = data.get("sensitive_categories", "").strip()
                    if categories_str:
                        # Parse comma-separated list
                        categories = [cat.strip() for cat in categories_str.split(",") if cat.strip()]
                        ai_policy["always_require_human_for"] = categories
                    else:
                        ai_policy["always_require_human_for"] = []

                # Update learn from overrides
                if "learn_from_overrides" in data:
                    ai_policy["learn_from_overrides"] = data.get("learn_from_overrides") in [True, "true", "on", 1, "1"]
                elif not request.is_json:
                    # Checkbox not present means unchecked
                    ai_policy["learn_from_overrides"] = False

                # Save updated policy
                tenant.ai_policy = ai_policy
                # Mark as modified for JSONB update
                from sqlalchemy.orm import attributes

                attributes.flag_modified(tenant, "ai_policy")

            # Update advertising policy configuration
            if any(
                key in data
                for key in [
                    "policy_check_enabled",
                    "default_prohibited_categories",
                    "default_prohibited_tactics",
                    "prohibited_categories",
                    "prohibited_tactics",
                    "prohibited_advertisers",
                ]
            ):
                # Get existing advertising policy or create new dict
                advertising_policy = tenant.advertising_policy if tenant.advertising_policy else {}

                # Update enabled status
                if "policy_check_enabled" in data:
                    advertising_policy["enabled"] = data.get("policy_check_enabled") in [True, "true", "on", 1, "1"]
                elif not request.is_json:
                    # Checkbox not present means unchecked
                    advertising_policy["enabled"] = False

                # Helper function for parsing and validating policy lists
                def parse_and_validate_policy_field(field_name: str, display_name: str) -> list[str]:
                    """Parse and validate a policy field from form data."""
                    field_str = data.get(field_name, "").strip()
                    if not field_str:
                        return []

                    # Parse newline-separated list
                    items = [line.strip() for line in field_str.split("\n") if line.strip()]

                    # Validate
                    validated, error = validate_policy_list(items, display_name)
                    if error:
                        if request.is_json:
                            raise ValueError(error)
                        flash(error, "error")
                        raise ValueError(error)

                    return validated

                # Update all policy fields with validation
                policy_fields = {
                    "default_prohibited_categories": "Baseline Protected Categories",
                    "default_prohibited_tactics": "Baseline Prohibited Tactics",
                    "prohibited_categories": "Additional Prohibited Categories",
                    "prohibited_tactics": "Additional Prohibited Tactics",
                    "prohibited_advertisers": "Blocked Advertisers/Domains",
                }

                for field_name, display_name in policy_fields.items():
                    if field_name in data:
                        try:
                            advertising_policy[field_name] = parse_and_validate_policy_field(field_name, display_name)
                        except ValueError as e:
                            if request.is_json:
                                return jsonify({"success": False, "error": str(e)}), 400
                            return redirect(
                                url_for("tenants.tenant_settings", tenant_id=tenant_id, section="business-rules")
                            )

                # Save updated policy
                tenant.advertising_policy = advertising_policy
                # Mark as modified for JSONB update
                from sqlalchemy.orm import attributes

                attributes.flag_modified(tenant, "advertising_policy")

            # Update features
            if "enable_axe_signals" in data:
                tenant.enable_axe_signals = data.get("enable_axe_signals") in [True, "true", "on", 1, "1"]
            elif not request.is_json:
                # Checkbox not present in form data means unchecked
                tenant.enable_axe_signals = False

            tenant.updated_at = datetime.now(UTC)
            db_session.commit()

            if request.is_json:
                return jsonify({"success": True, "message": "Business rules updated successfully"}), 200

            flash("Business rules updated successfully", "success")
            return redirect(url_for("tenants.tenant_settings", tenant_id=tenant_id, section="business-rules"))

    except Exception as e:
        logger.error(f"Error updating business rules: {e}", exc_info=True)

        if request.is_json:
            return jsonify({"success": False, "error": str(e)}), 500

        flash(f"Error updating business rules: {str(e)}", "error")
        return redirect(url_for("tenants.tenant_settings", tenant_id=tenant_id, section="business-rules"))


@settings_bp.route("/approximated-domain-status", methods=["POST"])
@require_tenant_access()
def check_approximated_domain_status(tenant_id):
    """Check if a domain is registered with Approximated."""
    try:
        import requests

        data = request.get_json()
        domain = data.get("domain")
        if not domain:
            return jsonify({"success": False, "error": "Domain required"}), 400

        approximated_api_key = os.getenv("APPROXIMATED_API_KEY")
        if not approximated_api_key:
            return jsonify({"success": False, "error": "Approximated not configured"}), 500

        # Check domain registration status using correct Approximated API endpoint
        response = requests.get(
            f"https://cloud.approximated.app/api/vhosts/by/incoming/{domain}",
            headers={
                "api-key": approximated_api_key,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            timeout=10,
        )

        if response.status_code == 200:
            response_data = response.json()
            # Approximated API wraps data in 'data' key
            domain_data = response_data.get("data", response_data)

            return jsonify(
                {
                    "success": True,
                    "registered": True,
                    "status": domain_data.get("status"),
                    "tls_enabled": domain_data.get("has_ssl", False),
                    "ssl_active": domain_data.get("status", "").startswith("ACTIVE_SSL"),
                    "target_address": domain_data.get("target_address"),
                }
            )
        elif response.status_code == 404:
            return jsonify({"success": True, "registered": False})
        else:
            logger.error(f"Approximated API error: {response.status_code} - {response.text}")
            return jsonify({"success": False, "error": f"API error: {response.status_code}"}), 500

    except Exception as e:
        logger.error(f"Error checking domain status: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@settings_bp.route("/approximated-register-domain", methods=["POST"])
@require_tenant_access()
@log_admin_action("register_approximated_domain")
def register_approximated_domain(tenant_id):
    """Register a domain with Approximated for TLS and routing."""
    try:
        import requests

        data = request.get_json()
        domain = data.get("domain")
        if not domain:
            return jsonify({"success": False, "error": "Domain required"}), 400

        approximated_api_key = os.getenv("APPROXIMATED_API_KEY")
        if not approximated_api_key:
            return jsonify({"success": False, "error": "Approximated not configured"}), 500

        with get_db_session() as db_session:
            tenant = db_session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
            if not tenant:
                return jsonify({"success": False, "error": "Tenant not found"}), 404

            if tenant.virtual_host != domain:
                return jsonify({"success": False, "error": "Domain must match tenant's virtual_host"}), 400

        # Get backend target address from environment
        backend_url = os.getenv("APPROXIMATED_BACKEND_URL", "adcp-sales-agent.fly.dev")

        # Register domain with Approximated using correct API endpoint
        response = requests.post(
            "https://cloud.approximated.app/api/vhosts",
            headers={
                "api-key": approximated_api_key,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            json={
                "incoming_address": domain,
                "target_address": backend_url,
            },
            timeout=10,
        )

        if response.status_code in (200, 201):
            logger.info(f"✅ Registered domain with Approximated: {domain}")
            return jsonify({"success": True, "message": f"Domain {domain} registered successfully"})
        elif response.status_code == 409:
            # Already exists - that's OK
            logger.info(f"✅ Domain already registered: {domain}")
            return jsonify({"success": True, "message": f"Domain {domain} already registered"})
        else:
            error_msg = f"Approximated API error: {response.status_code} - {response.text}"
            logger.error(error_msg)
            return jsonify({"success": False, "error": error_msg}), response.status_code

    except Exception as e:
        logger.error(f"Error registering domain: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@settings_bp.route("/approximated-unregister-domain", methods=["POST"])
@require_tenant_access()
@log_admin_action("unregister_approximated_domain")
def unregister_approximated_domain(tenant_id):
    """Unregister a domain from Approximated."""
    try:
        import requests

        data = request.get_json()
        domain = data.get("domain")
        if not domain:
            return jsonify({"success": False, "error": "Domain required"}), 400

        approximated_api_key = os.getenv("APPROXIMATED_API_KEY")
        if not approximated_api_key:
            return jsonify({"success": False, "error": "Approximated not configured"}), 500

        # Unregister domain from Approximated using correct API endpoint
        response = requests.delete(
            f"https://cloud.approximated.app/api/vhosts/by/incoming/{domain}",
            headers={
                "api-key": approximated_api_key,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            timeout=10,
        )

        if response.status_code in (200, 204):
            logger.info(f"✅ Unregistered domain from Approximated: {domain}")
            return jsonify({"success": True, "message": f"Domain {domain} unregistered successfully"})
        elif response.status_code == 404:
            # Already gone - that's OK
            logger.info(f"✅ Domain already unregistered: {domain}")
            return jsonify({"success": True, "message": f"Domain {domain} was not registered"})
        else:
            error_msg = f"Approximated API error: {response.status_code} - {response.text}"
            logger.error(error_msg)
            return jsonify({"success": False, "error": error_msg}), response.status_code

    except Exception as e:
        logger.error(f"Error unregistering domain: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@settings_bp.route("/approximated-token", methods=["POST"])
@require_tenant_access()
def get_approximated_token(tenant_id):
    """Generate an Approximated DNS widget token."""
    try:
        import requests

        # Get API key from environment
        approximated_api_key = os.getenv("APPROXIMATED_API_KEY")
        if not approximated_api_key:
            logger.error("APPROXIMATED_API_KEY not configured in environment")
            return jsonify({"success": False, "error": "DNS widget not configured on server"}), 500

        with get_db_session() as db_session:
            tenant = db_session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
            if not tenant:
                return jsonify({"success": False, "error": "Tenant not found"}), 404

            # Request token from Approximated API
            response = requests.get(
                "https://cloud.approximated.app/api/dns/token",
                headers={"api-key": approximated_api_key},
                timeout=10,
            )

            if response.status_code == 200:
                token_data = response.json()
                logger.info(f"Approximated API response: {token_data}")
                return jsonify({"success": True, "token": token_data.get("token")})
            else:
                logger.error(f"Approximated API error: {response.status_code} - {response.text}")
                return jsonify({"success": False, "error": f"API error: {response.status_code}"}), response.status_code

    except Exception as e:
        logger.error(f"Error generating Approximated token: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500
