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
from typing import Any

from babel import numbers as babel_numbers
from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for
from sqlalchemy import select

from src.admin.utils import require_auth, require_tenant_access
from src.admin.utils.audit_decorator import log_admin_action
from src.core.database.database_session import get_db_session
from src.core.database.models import Tenant

logger = logging.getLogger(__name__)


def is_valid_currency_code(currency_code: str) -> bool:
    """Validate currency code using Babel's currency data.

    Args:
        currency_code: 3-letter ISO 4217 currency code (e.g., "USD", "EUR")

    Returns:
        bool: True if valid currency code, False otherwise
    """
    try:
        # Get the currency name - if Babel returns the code itself, it's not a real currency
        # Real currencies have proper names (e.g., "US Dollar" for USD)
        # Unknown currencies just return the code (e.g., "XYZ" for XYZ)
        name = babel_numbers.get_currency_name(currency_code, locale="en")
        return name != currency_code
    except Exception:
        return False


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

                # Validate currency code using Babel
                is_valid = is_valid_currency_code(currency_code)
                logger.info(f"Currency validation: {currency_code} -> valid={is_valid}")
                if not is_valid:
                    logger.warning(f"Rejecting invalid currency code: {currency_code}")
                    flash(
                        f"Invalid currency code: {currency_code}. Please use a valid ISO 4217 currency code.", "error"
                    )
                    return redirect(url_for("tenants.tenant_settings", tenant_id=tenant_id, section="general"))

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
        "adapter": request.json.get("adapter") if request.is_json and request.json else request.form.get("adapter")
    },
)
def update_adapter(tenant_id):
    """Update the active adapter for a tenant."""
    try:
        # Support both JSON (from our frontend) and form data (from tests)
        if request.is_json:
            new_adapter = request.json.get("adapter")
            logger.info(f"update_adapter received JSON payload: {request.json}")
        else:
            new_adapter = request.form.get("adapter")

        with get_db_session() as db_session:
            tenant = db_session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
            if not tenant:
                if request.is_json:
                    return jsonify({"success": False, "error": "Tenant not found"}), 404
                flash("Tenant not found", "error")
                return redirect(url_for("core.index"))

            # If no adapter specified, use current adapter (for updating config only)
            if not new_adapter:
                new_adapter = tenant.ad_server
                if not new_adapter:
                    if request.is_json:
                        return jsonify({"success": False, "error": "No adapter configured"}), 400
                    flash("No adapter configured", "error")
                    return redirect(url_for("tenants.tenant_settings", tenant_id=tenant_id, section="adapter"))

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

            # Extract AXE keys (adapter-agnostic, work for all adapters)
            if request.is_json:
                # Three separate AXE keys per AdCP spec
                axe_include_key = (
                    request.json.get("axe_include_key", "").strip() if request.json.get("axe_include_key") else None
                )
                axe_exclude_key = (
                    request.json.get("axe_exclude_key", "").strip() if request.json.get("axe_exclude_key") else None
                )
                axe_macro_key = (
                    request.json.get("axe_macro_key", "").strip() if request.json.get("axe_macro_key") else None
                )
            else:
                # Three separate AXE keys per AdCP spec
                axe_include_key = request.form.get("axe_include_key", "").strip() or None
                axe_exclude_key = request.form.get("axe_exclude_key", "").strip() or None
                axe_macro_key = request.form.get("axe_macro_key", "").strip() or None

            # Update AXE keys (adapter-agnostic)
            if axe_include_key is not None:
                adapter_config_obj.axe_include_key = axe_include_key
            if axe_exclude_key is not None:
                adapter_config_obj.axe_exclude_key = axe_exclude_key
            if axe_macro_key is not None:
                adapter_config_obj.axe_macro_key = axe_macro_key

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
                    network_currency = (
                        request.json.get("network_currency", "").strip()[:3].upper()
                        if request.json.get("network_currency")
                        else None
                    )
                    secondary_currencies = request.json.get("secondary_currencies", [])
                    # Validate and sanitize secondary currencies
                    if isinstance(secondary_currencies, list):
                        secondary_currencies = [str(c).strip()[:3].upper() for c in secondary_currencies if c]
                    else:
                        secondary_currencies = []
                    network_timezone = (
                        request.json.get("network_timezone", "").strip()[:100]
                        if request.json.get("network_timezone")
                        else None
                    )

                    # Special handler for "Edit Configuration" action from UI
                    # When action == "edit_config", we want to clear the stored GAM network code
                    # (and associated trafficker ID) so the UI shows the configuration wizard again,
                    # while preserving the existing refresh token.
                    action = request.json.get("action")
                    if action == "edit_config" and adapter_config_obj:
                        adapter_config_obj.gam_network_code = None
                        adapter_config_obj.gam_trafficker_id = None
                else:
                    network_code = request.form.get("gam_network_code", "").strip()
                    refresh_token = request.form.get("gam_refresh_token", "").strip()
                    trafficker_id = request.form.get("gam_trafficker_id", "").strip()
                    order_name_template = request.form.get("order_name_template", "").strip()
                    line_item_name_template = request.form.get("line_item_name_template", "").strip()
                    manual_approval = request.form.get("gam_manual_approval") == "on"
                    # Currency/timezone info not typically sent via form (comes from detect-network)
                    network_currency = None
                    secondary_currencies = []
                    network_timezone = None

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
                # Save detected currency/timezone info from GAM network
                if network_currency:
                    adapter_config_obj.gam_network_currency = network_currency
                if secondary_currencies:
                    adapter_config_obj.gam_secondary_currencies = secondary_currencies
                if network_timezone:
                    adapter_config_obj.gam_network_timezone = network_timezone
                adapter_config_obj.gam_manual_approval_required = manual_approval
            elif new_adapter == "mock":
                if request.is_json:
                    dry_run = request.json.get("mock_dry_run", False)
                    manual_approval = request.json.get("mock_manual_approval", False)
                else:
                    dry_run = request.form.get("mock_dry_run") == "on"
                    manual_approval = request.form.get("mock_manual_approval") == "on"
                adapter_config_obj.mock_dry_run = dry_run
                adapter_config_obj.mock_manual_approval_required = manual_approval

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
    """Update AI services settings (multi-provider configuration)."""
    try:
        provider = request.form.get("ai_provider", "gemini").strip()
        model = request.form.get("ai_model", "").strip()
        api_key = request.form.get("ai_api_key", "").strip()
        logfire_token = request.form.get("logfire_token", "").strip()

        with get_db_session() as db_session:
            tenant = db_session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
            if not tenant:
                flash("Tenant not found", "error")
                return redirect(url_for("core.index"))

            # Build ai_config from existing config (if any) merged with form data
            existing_config = tenant.ai_config or {}

            # Start with existing config to preserve any fields not in form
            new_config = {
                "provider": provider,
                "model": model,
            }

            # Handle API key: use new one if provided, otherwise keep existing
            if api_key:
                new_config["api_key"] = api_key
            elif existing_config.get("api_key"):
                new_config["api_key"] = existing_config["api_key"]
            elif tenant.gemini_api_key and provider == "gemini":
                # Migrate legacy gemini_api_key to new ai_config
                new_config["api_key"] = tenant.gemini_api_key

            # Preserve settings if they exist
            if existing_config.get("settings"):
                new_config["settings"] = existing_config["settings"]

            # Handle Logfire token: use new one if provided, otherwise keep existing
            # Skip placeholder value that indicates existing token
            if logfire_token and logfire_token != "••••••••":
                new_config["logfire_token"] = logfire_token
            elif existing_config.get("logfire_token"):
                new_config["logfire_token"] = existing_config["logfire_token"]

            # Update the tenant
            tenant.ai_config = new_config
            tenant.updated_at = datetime.now(UTC)
            db_session.commit()

            provider_name = provider.title()
            if new_config.get("api_key"):
                flash(f"AI settings saved. {provider_name} ({model}) is now configured.", "success")
            else:
                flash(
                    f"AI provider set to {provider_name}, but no API key configured. AI features will be disabled.",
                    "warning",
                )

    except Exception as e:
        logger.error(f"Error updating AI settings: {e}", exc_info=True)
        flash(f"Error updating AI settings: {str(e)}", "error")

    return redirect(url_for("tenants.tenant_settings", tenant_id=tenant_id, section="integrations"))


@settings_bp.route("/ai/test", methods=["POST"])
@require_tenant_access(api_mode=True)
def test_ai_connection(tenant_id):
    """Test AI connection with current configuration."""
    import asyncio
    import concurrent.futures

    from pydantic import BaseModel
    from pydantic_ai import Agent

    from src.services.ai import AIServiceFactory

    try:
        data = request.get_json() or {}
        provider = data.get("provider", "gemini")
        model = data.get("model", "gemini-2.0-flash")
        api_key = data.get("api_key", "").strip()

        # Build config for testing
        test_config = {"provider": provider, "model": model}

        # Use provided API key, or fall back to saved config
        with get_db_session() as db_session:
            tenant = db_session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
            if not tenant:
                return jsonify({"success": False, "error": "Tenant not found"}), 404

            if api_key:
                test_config["api_key"] = api_key
            elif tenant.ai_config and tenant.ai_config.get("api_key"):
                test_config["api_key"] = tenant.ai_config["api_key"]
            elif tenant.gemini_api_key and provider == "gemini":
                test_config["api_key"] = tenant.gemini_api_key

        if not test_config.get("api_key"):
            return jsonify({"success": False, "error": "No API key provided"}), 400

        # Create factory and set up credentials
        factory = AIServiceFactory()
        model_string = factory.create_model(test_config)

        # Simple test: ask the model to respond with a single word
        class TestResponse(BaseModel):
            word: str

        agent = Agent(model=model_string, output_type=TestResponse, system_prompt="Respond with exactly one word.")

        def run_in_thread():
            """Run async code in a new thread with its own event loop."""
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:

                async def run_test():
                    result = await agent.run("Say hello")
                    # pydantic-ai 1.x uses .output for structured data
                    return result.output

                return loop.run_until_complete(run_test())
            finally:
                loop.close()

        # Run in a separate thread to avoid event loop conflicts
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(run_in_thread)
            response = future.result(timeout=30)

        return jsonify(
            {
                "success": True,
                "message": f"Connection successful! Model responded: {response.word}",
                "provider": provider,
                "model": model,
            }
        )

    except Exception as e:
        error_msg = str(e)
        # Clean up error messages for common issues
        if "expired" in error_msg.lower():
            error_msg = "API key expired. Please renew your API key."
        elif "401" in error_msg or "authentication" in error_msg.lower() or "api_key_invalid" in error_msg.lower():
            error_msg = "Invalid API key. Please check your credentials."
        elif "404" in error_msg or "not found" in error_msg.lower():
            error_msg = f"Model '{model}' not found. Please check the model name."
        elif "rate" in error_msg.lower() or "quota" in error_msg.lower():
            error_msg = "Rate limit exceeded. Please try again later."

        logger.error(f"AI test connection failed: {e}", exc_info=True)
        return jsonify({"success": False, "error": error_msg}), 400


@settings_bp.route("/ai/test-logfire", methods=["POST"])
@require_tenant_access(api_mode=True)
def test_logfire_connection(tenant_id):
    """Test Logfire connection with provided token."""
    try:
        data = request.get_json() or {}
        logfire_token = data.get("logfire_token", "").strip()

        if not logfire_token:
            return jsonify({"success": False, "error": "No Logfire token provided"}), 400

        # Try to configure logfire with the token
        import logfire

        # Create a test configuration
        try:
            # Use send_to_logfire=False for validation, then test with actual send
            logfire.configure(
                token=logfire_token,
                service_name="adcp-sales-agent-test",
            )

            # Send a test span to verify the token works
            with logfire.span("test_connection", _level="info") as span:
                span.set_attribute("test", True)
                span.set_attribute("tenant_id", tenant_id)

            return jsonify(
                {
                    "success": True,
                    "message": "Logfire connection successful! Check your Logfire dashboard for the test span.",
                }
            )

        except Exception as config_error:
            error_msg = str(config_error)
            if "401" in error_msg or "unauthorized" in error_msg.lower() or "invalid" in error_msg.lower():
                error_msg = "Invalid Logfire token. Please check your credentials."
            elif "403" in error_msg or "forbidden" in error_msg.lower():
                error_msg = "Token does not have permission to send data."

            return jsonify({"success": False, "error": error_msg}), 400

    except ImportError:
        return jsonify({"success": False, "error": "Logfire package not installed"}), 400
    except Exception as e:
        logger.error(f"Logfire test connection failed: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 400


@settings_bp.route("/ai/models", methods=["GET"])
@require_tenant_access(api_mode=True)
def get_ai_models(tenant_id):
    """Get available AI models from Pydantic AI.

    Returns models grouped by provider, extracted from pydantic_ai.models.KnownModelName.
    """
    from pydantic_ai.models import KnownModelName

    # Extract all model strings from KnownModelName Literal type
    all_models = KnownModelName.__value__.__args__

    # Group by provider and organize
    by_provider = {}
    for model_string in all_models:
        if ":" not in model_string:
            continue

        provider, model_name = model_string.split(":", 1)

        # Skip test model
        if provider == "test":
            continue

        if provider not in by_provider:
            by_provider[provider] = []
        by_provider[provider].append(model_name)

    # Sort models within each provider
    for provider in by_provider:
        by_provider[provider] = sorted(set(by_provider[provider]))

    # Define provider metadata for UI
    provider_info = {
        "google-gla": {"name": "Google Gemini", "key_url": "https://aistudio.google.com/app/apikey"},
        "anthropic": {"name": "Anthropic Claude", "key_url": "https://console.anthropic.com/settings/keys"},
        "openai": {"name": "OpenAI", "key_url": "https://platform.openai.com/api-keys"},
        "groq": {"name": "Groq", "key_url": "https://console.groq.com/keys"},
        "mistral": {"name": "Mistral AI", "key_url": "https://console.mistral.ai/api-keys"},
        "deepseek": {"name": "DeepSeek", "key_url": "https://platform.deepseek.com/api_keys"},
        "grok": {"name": "xAI Grok", "key_url": "https://console.x.ai"},
        "cohere": {"name": "Cohere", "key_url": "https://dashboard.cohere.com/api-keys"},
        "bedrock": {"name": "AWS Bedrock", "key_url": "https://console.aws.amazon.com/bedrock"},
        "google-vertex": {"name": "Google Vertex AI", "key_url": "https://console.cloud.google.com/vertex-ai"},
        "huggingface": {"name": "Hugging Face", "key_url": "https://huggingface.co/settings/tokens"},
        "cerebras": {"name": "Cerebras", "key_url": "https://cloud.cerebras.ai"},
        "moonshotai": {"name": "Moonshot AI", "key_url": "https://platform.moonshot.cn"},
        "heroku": {"name": "Heroku AI", "key_url": "https://dashboard.heroku.com"},
        # Gateway providers (uses Pydantic AI Gateway)
        "gateway/anthropic": {
            "name": "Gateway: Anthropic",
            "key_url": "https://ai.pydantic.dev/gateway",
            "gateway": True,
        },
        "gateway/openai": {"name": "Gateway: OpenAI", "key_url": "https://ai.pydantic.dev/gateway", "gateway": True},
        "gateway/bedrock": {"name": "Gateway: Bedrock", "key_url": "https://ai.pydantic.dev/gateway", "gateway": True},
        "gateway/google-vertex": {
            "name": "Gateway: Vertex",
            "key_url": "https://ai.pydantic.dev/gateway",
            "gateway": True,
        },
        "gateway/groq": {"name": "Gateway: Groq", "key_url": "https://ai.pydantic.dev/gateway", "gateway": True},
    }

    # Build response with provider info
    result = {}
    for provider, models in by_provider.items():
        info = provider_info.get(provider, {"name": provider.replace("-", " ").title(), "key_url": None})
        result[provider] = {
            "name": info["name"],
            "key_url": info.get("key_url"),
            "gateway": info.get("gateway", False),
            "models": models,
        }

    return jsonify(result)


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


def parse_form_data_to_policy_updates(form_data) -> dict[str, Any]:
    """Parse Flask form data into PolicyService update format.

    Args:
        form_data: Flask request.form or request.get_json() data

    Returns:
        Dict suitable for PolicyService.update_policies()
    """
    from decimal import Decimal

    from src.services.policy_service import CurrencyLimitData

    updates: dict[str, Any] = {}

    # Parse currency limits
    currency_data: dict[str, dict[str, Any]] = {}
    for key in form_data.keys():
        if key.startswith("currency_limits["):
            parts = key.split("[")
            if len(parts) >= 2:
                currency_code = parts[1].rstrip("]")
                if currency_code not in currency_data:
                    currency_data[currency_code] = {}

                if "[min_package_budget]" in key:
                    val_str = form_data.get(key, "").strip()
                    currency_data[currency_code]["min"] = Decimal(val_str) if val_str else None
                elif "[max_daily_package_spend]" in key:
                    val_str = form_data.get(key, "").strip()
                    currency_data[currency_code]["max"] = Decimal(val_str) if val_str else None
                elif "[_delete]" in key:
                    currency_data[currency_code]["_delete"] = form_data.get(key) in ["true", True]

    if currency_data:
        updates["currencies"] = [
            CurrencyLimitData(
                currency_code=code,
                min_package_budget=data.get("min"),
                max_daily_package_spend=data.get("max"),
                _delete=data.get("_delete", False),
            )
            for code, data in currency_data.items()
        ]

    # Parse measurement providers
    # Check if measurement providers section is present in the form
    # Hidden field _measurement_providers_section ensures validation runs even when all providers removed
    has_provider_section = "_measurement_providers_section" in form_data
    has_provider_fields = any(
        key.startswith("provider_name_") or key == "default_measurement_provider" for key in form_data.keys()
    )

    if has_provider_section or has_provider_fields:
        providers = []
        for key in form_data.keys():
            if key.startswith("provider_name_"):
                provider_name = form_data.get(key, "").strip()
                if provider_name:
                    providers.append(provider_name)

        default_provider = form_data.get("default_measurement_provider", "").strip()
        if not default_provider and providers:
            default_provider = providers[0]

        # ALWAYS include measurement_providers in updates if form has provider fields
        # This ensures validation runs even when removing all providers
        updates["measurement_providers"] = {"providers": providers, "default": default_provider}

    # Parse naming templates
    if "order_name_template" in form_data:
        updates["order_name_template"] = form_data.get("order_name_template", "").strip()

    if "line_item_name_template" in form_data:
        updates["line_item_name_template"] = form_data.get("line_item_name_template", "").strip()

    # Parse approval settings
    if "approval_mode" in form_data:
        updates["approval_mode"] = form_data.get("approval_mode", "auto-approve")

    if "creative_review_criteria" in form_data:
        updates["creative_review_criteria"] = form_data.get("creative_review_criteria", "")

    if "creative_auto_approve_threshold" in form_data:
        try:
            updates["creative_auto_approve_threshold"] = float(form_data.get("creative_auto_approve_threshold", 0.9))
        except (ValueError, TypeError):
            pass

    if "creative_auto_reject_threshold" in form_data:
        try:
            updates["creative_auto_reject_threshold"] = float(form_data.get("creative_auto_reject_threshold", 0.1))
        except (ValueError, TypeError):
            pass

    # Parse feature flags
    if "enable_axe_signals" in form_data:
        updates["enable_axe_signals"] = form_data.get("enable_axe_signals") in [True, "true", "on", 1, "1"]

    if "brand_manifest_policy" in form_data:
        policy_value = form_data.get("brand_manifest_policy", "").strip()
        logger.info(f"Parsing brand_manifest_policy: received '{policy_value}'")
        if policy_value in ["public", "require_auth", "require_brand"]:
            updates["brand_manifest_policy"] = policy_value
        else:
            logger.warning(f"Invalid brand_manifest_policy value: '{policy_value}', ignoring")
            # Still include it so PolicyService can validate and reject if needed
            if policy_value:
                updates["brand_manifest_policy"] = policy_value

    # Parse AI policy
    ai_policy_fields = [
        "creative_auto_approve_threshold",
        "creative_auto_reject_threshold",
        "sensitive_categories",
        "learn_from_overrides",
    ]
    if any(field in form_data for field in ai_policy_fields):
        ai_policy = {}

        if "creative_auto_approve_threshold" in form_data:
            try:
                ai_policy["creative_auto_approve_threshold"] = float(
                    form_data.get("creative_auto_approve_threshold", 0.9)
                )
            except (ValueError, TypeError):
                pass

        if "creative_auto_reject_threshold" in form_data:
            try:
                ai_policy["creative_auto_reject_threshold"] = float(
                    form_data.get("creative_auto_reject_threshold", 0.1)
                )
            except (ValueError, TypeError):
                pass

        if "sensitive_categories" in form_data:
            ai_policy["sensitive_categories"] = form_data.get("sensitive_categories", "").strip()

        if "learn_from_overrides" in form_data:
            ai_policy["learn_from_overrides"] = form_data.get("learn_from_overrides") in [True, "true", "on", 1, "1"]
        elif "learn_from_overrides" not in form_data:
            # Checkbox not present means unchecked
            ai_policy["learn_from_overrides"] = False

        if ai_policy:
            updates["ai_policy"] = ai_policy

    # Parse advertising policy
    advertising_policy_fields = [
        "policy_check_enabled",
        "default_prohibited_categories",
        "default_prohibited_tactics",
        "prohibited_categories",
        "prohibited_tactics",
        "prohibited_advertisers",
    ]
    if any(field in form_data for field in advertising_policy_fields):
        advertising_policy: dict[str, Any] = {}

        if "policy_check_enabled" in form_data:
            advertising_policy["enabled"] = form_data.get("policy_check_enabled") in [True, "true", "on", 1, "1"]
        elif "policy_check_enabled" not in form_data:
            # Checkbox not present means unchecked
            advertising_policy["enabled"] = False

        # Parse list fields (newline-separated)
        for field_name in [
            "default_prohibited_categories",
            "default_prohibited_tactics",
            "prohibited_categories",
            "prohibited_tactics",
            "prohibited_advertisers",
        ]:
            if field_name in form_data:
                field_str = form_data.get(field_name, "").strip()
                if field_str:
                    items = [line.strip() for line in field_str.split("\n") if line.strip()]
                    advertising_policy[field_name] = items
                else:
                    advertising_policy[field_name] = []

        if advertising_policy:
            updates["advertising_policy"] = advertising_policy

    return updates


@settings_bp.route("/business-rules", methods=["POST"])
@log_admin_action("update_business_rules")
@require_tenant_access()
def update_business_rules(tenant_id):
    """Update business rules (budget, naming, approvals, features).

    This function uses PolicyService for validation and updates. The service layer
    provides clean, testable business logic with comprehensive validation.
    """
    from src.services.policy_service import PolicyService, ValidationError

    try:
        # Get form data
        data = request.get_json() if request.is_json else request.form

        # Parse form data into PolicyService format
        updates = parse_form_data_to_policy_updates(data)

        # Update policies using service (validates and saves atomically)
        PolicyService.update_policies(tenant_id, updates)

        # Handle human_review_required separately (syncs to adapter configs)
        # This is operational config, not policy, so kept in route handler
        with get_db_session() as db_session:
            tenant = db_session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
            if tenant:
                if "human_review_required" in data:
                    manual_approval_value = data.get("human_review_required") in [True, "true", "on", 1, "1"]
                    tenant.human_review_required = manual_approval_value

                    # Update ALL adapters' manual approval settings
                    if tenant.adapter_config:
                        adapter_type = tenant.adapter_config.adapter_type
                        if adapter_type == "google_ad_manager":
                            tenant.adapter_config.gam_manual_approval_required = manual_approval_value
                        elif adapter_type == "mock":
                            tenant.adapter_config.mock_manual_approval_required = manual_approval_value
                        elif adapter_type == "kevel":
                            tenant.adapter_config.kevel_manual_approval_required = manual_approval_value
                elif not request.is_json:
                    # Checkbox not present in form data means unchecked
                    tenant.human_review_required = False
                    if tenant.adapter_config:
                        adapter_type = tenant.adapter_config.adapter_type
                        if adapter_type == "google_ad_manager":
                            tenant.adapter_config.gam_manual_approval_required = False
                        elif adapter_type == "mock":
                            tenant.adapter_config.mock_manual_approval_required = False
                        elif adapter_type == "kevel":
                            tenant.adapter_config.kevel_manual_approval_required = False

                db_session.commit()

        # Success
        if request.is_json:
            return jsonify({"success": True}), 200

        flash("Business rules updated successfully", "success")
        return redirect(url_for("tenants.tenant_settings", tenant_id=tenant_id))

    except ValidationError as e:
        logger.warning(f"Validation error updating business rules for tenant {tenant_id}: {e.errors}")

        if request.is_json:
            return jsonify({"success": False, "errors": e.errors}), 400

        # Flash each validation error
        for field, error in e.errors.items():
            flash(f"{field}: {error}", "error")
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
    """Generate an Approximated DNS widget token and get DNS target."""
    try:
        import requests

        # Get API key from environment
        approximated_api_key = os.getenv("APPROXIMATED_API_KEY")
        if not approximated_api_key:
            logger.error("APPROXIMATED_API_KEY not configured in environment")
            return jsonify({"success": False, "error": "DNS widget not configured on server"}), 500

        # Get the Approximated proxy IP from environment
        # This is the IP address of your Approximated proxy cluster
        approximated_proxy_ip = os.getenv("APPROXIMATED_PROXY_IP", "37.16.24.200")

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
                return jsonify({"success": True, "token": token_data.get("token"), "proxy_ip": approximated_proxy_ip})
            else:
                logger.error(f"Approximated API error: {response.status_code} - {response.text}")
                return jsonify({"success": False, "error": f"API error: {response.status_code}"}), response.status_code

    except Exception as e:
        logger.error(f"Error generating Approximated token: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500
