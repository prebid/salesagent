"""Tenant management blueprint for admin UI.

⚠️ ROUTING NOTICE: This file contains the ACTUAL handler for tenant settings!
- URL: /admin/tenant/{id}/settings
- Function: settings()
- DO NOT confuse with src/admin/blueprints/settings.py which handles superadmin settings
"""

import json
import logging
import os
from datetime import UTC, datetime

from babel import numbers as babel_numbers
from flask import Blueprint, flash, jsonify, redirect, render_template, request, session, url_for
from sqlalchemy import func, select

from src.admin.services import DashboardService
from src.admin.utils import get_tenant_config_from_db, require_tenant_access
from src.admin.utils.audit_decorator import log_admin_action
from src.core.config_loader import is_single_tenant_mode
from src.core.database.database_session import get_db_session
from src.core.database.models import Principal, Tenant
from src.core.domain_config import get_sales_agent_domain
from src.core.validation import sanitize_form_data, validate_form_data
from src.services.setup_checklist_service import SetupChecklistService

logger = logging.getLogger(__name__)

# Create Blueprint
tenants_bp = Blueprint("tenants", __name__, url_prefix="/tenant")


def get_available_currencies():
    """Get list of all ISO 4217 currency codes with names from Babel.

    Returns:
        list: List of dicts with 'code' and 'name' keys, sorted by code
        Example: [{'code': 'USD', 'name': 'US Dollar'}, ...]
    """
    currencies = []
    # Get all currency codes from Babel's currency data
    from babel.core import get_global

    currency_data = get_global("currency_names")
    for code in sorted(currency_data.keys()):
        try:
            name = babel_numbers.get_currency_name(code, locale="en")
            if name:  # Skip if no English name available
                currencies.append({"code": code, "name": name})
        except Exception:
            # Skip currencies that can't be resolved
            continue

    return currencies


@tenants_bp.route("/<tenant_id>")
@require_tenant_access()
def dashboard(tenant_id):
    """Show tenant dashboard using single data source pattern."""
    try:
        # Use DashboardService for all dashboard data (SINGLE DATA SOURCE PATTERN)
        dashboard_service = DashboardService(tenant_id)
        tenant = dashboard_service.get_tenant()

        if not tenant:
            flash("Tenant not found", "error")
            return redirect(url_for("core.index"))

        # Get all metrics from centralized service
        metrics = dashboard_service.get_dashboard_metrics()

        # Get recent media buys
        recent_buys = dashboard_service.get_recent_media_buys(limit=10)

        # Get chart data
        chart_data_dict = dashboard_service.get_chart_data()

        # Get tenant config for features
        config = get_tenant_config_from_db(tenant_id)
        features = config.get("features", {})

        # Get setup checklist status
        # Show widget always (users can access recommended tasks even after critical complete)
        setup_status = None
        try:
            checklist_service = SetupChecklistService(tenant_id)
            setup_status = checklist_service.get_setup_status()
        except Exception as e:
            logger.warning(f"Failed to load setup checklist: {e}")

        return render_template(
            "tenant_dashboard.html",
            tenant=tenant,
            tenant_id=tenant_id,
            # Legacy template variables (calculated by service)
            active_campaigns=metrics["live_buys"],
            total_spend=metrics["total_revenue"],
            principals_count=metrics["total_advertisers"],
            products_count=metrics["products_count"],
            recent_buys=recent_buys,
            recent_media_buys=recent_buys,  # Same data, different name for template
            features=features,
            # Chart data
            revenue_data=json.dumps(metrics["revenue_data"]),
            chart_labels=chart_data_dict["labels"],
            chart_data=chart_data_dict["data"],
            # Metrics object (single source of truth)
            metrics=metrics,
            # Setup checklist
            setup_status=setup_status,
        )

    except Exception as e:
        import traceback

        error_detail = traceback.format_exc()
        logger.error(f"Error loading tenant dashboard: {e}\nFull traceback:\n{error_detail}")
        # Secure error handling - show safe errors to users, log full details
        error_str = str(e).lower()
        sensitive_keywords = [
            "database",
            "connection",
            "password",
            "secret",
            "key",
            "token",
            "postgresql",
            "psycopg2",
            "sqlalchemy",
            "alembic",
            "psql",
            "host=",
            "port=",
            "user=",
            "dbname=",
            "sslmode=",
        ]

        # Check if error contains sensitive information
        if any(keyword in error_str for keyword in sensitive_keywords):
            flash("Dashboard temporarily unavailable - please contact administrator", "error")
        else:
            # Safe to show user-friendly errors (validation, not found, etc.)
            flash(f"Dashboard Error: {str(e)}", "error")

        # Always log full details for debugging (only visible to administrators)
        logger.error(f"Dashboard traceback: {error_detail}")
        return redirect(url_for("core.index"))


@tenants_bp.route("/<tenant_id>/setup-checklist")
@require_tenant_access()
def setup_checklist(tenant_id):
    """Show full setup checklist page."""
    try:
        with get_db_session() as session:
            stmt = select(Tenant).filter_by(tenant_id=tenant_id)
            tenant = session.scalars(stmt).first()

            if not tenant:
                flash("Tenant not found", "error")
                return redirect(url_for("core.index"))

            # Get setup status
            checklist_service = SetupChecklistService(tenant_id)
            setup_status = checklist_service.get_setup_status()

            return render_template(
                "setup_checklist.html", tenant=tenant, tenant_id=tenant_id, setup_status=setup_status
            )

    except Exception as e:
        logger.error(f"Error loading setup checklist: {e}")
        flash(f"Error loading setup checklist: {str(e)}", "error")
        return redirect(url_for("tenants.dashboard", tenant_id=tenant_id))


@tenants_bp.route("/<tenant_id>/settings")
@tenants_bp.route("/<tenant_id>/settings/<section>")
@require_tenant_access()
def tenant_settings(tenant_id, section=None):
    """Show tenant settings page.

    ⚠️ IMPORTANT: This is the ACTUAL handler for /admin/tenant/{id}/settings URLs.
    Function renamed from settings() to tenant_settings() for clarity.

    This function handles the main tenant settings UI including:
    - Adapter selection and configuration
    - GAM OAuth status
    - Template rendering with active_adapter variable
    """
    try:
        with get_db_session() as db_session:
            from sqlalchemy import select

            stmt = select(Tenant).filter_by(tenant_id=tenant_id)
            tenant = db_session.scalars(stmt).first()
            if not tenant:
                flash("Tenant not found", "error")
                return redirect(url_for("core.index"))

            # Get adapter config
            adapter_config_obj = tenant.adapter_config

            # Get active adapter - this was missing!
            active_adapter = None
            if tenant.ad_server:
                active_adapter = tenant.ad_server
            elif adapter_config_obj and adapter_config_obj.adapter_type:
                active_adapter = adapter_config_obj.adapter_type

            # Get OAuth status for GAM
            oauth_configured = False
            if adapter_config_obj and adapter_config_obj.adapter_type == "google_ad_manager":
                oauth_configured = bool(adapter_config_obj.gam_refresh_token)

            # Check if GAM OAuth environment variables are configured
            gam_oauth_configured = bool(
                os.environ.get("GAM_OAUTH_CLIENT_ID") and os.environ.get("GAM_OAUTH_CLIENT_SECRET")
            )

            # Get advertiser data for the advertisers section
            from src.core.database.models import GAMInventory, Principal

            stmt = select(Principal).filter_by(tenant_id=tenant_id)
            principals = db_session.scalars(stmt).all()
            advertiser_count = len(principals)
            active_advertisers = len(principals)  # For now, assume all are active

            # Check for running sync jobs
            from src.core.database.models import SyncJob

            running_sync = None
            if active_adapter == "google_ad_manager":
                stmt = (
                    select(SyncJob)
                    .filter_by(tenant_id=tenant_id, status="running", sync_type="inventory")
                    .order_by(SyncJob.started_at.desc())
                )
                running_sync = db_session.scalars(stmt).first()

            # Get last sync time from most recently updated inventory item
            last_sync_time = None
            print(f"DEBUG: Checking last sync time - active_adapter: {active_adapter}")
            if active_adapter == "google_ad_manager":
                stmt = (
                    select(GAMInventory.updated_at)
                    .filter_by(tenant_id=tenant_id)
                    .order_by(GAMInventory.updated_at.desc())
                    .limit(1)
                )
                last_updated = db_session.scalar(stmt)
                print(f"DEBUG: Last inventory update for tenant {tenant_id}: {last_updated}")
                if last_updated:
                    from datetime import datetime

                    # Format as human-readable time
                    now = datetime.now(UTC)
                    diff = now - last_updated.replace(tzinfo=UTC)
                    if diff.total_seconds() < 60:
                        last_sync_time = "Just now"
                    elif diff.total_seconds() < 3600:
                        mins = int(diff.total_seconds() / 60)
                        last_sync_time = f"{mins} minute{'s' if mins != 1 else ''} ago"
                    elif diff.total_seconds() < 86400:
                        hours = int(diff.total_seconds() / 3600)
                        last_sync_time = f"{hours} hour{'s' if hours != 1 else ''} ago"
                    else:
                        last_sync_time = last_updated.strftime("%b %d, %Y at %I:%M %p")

            # Convert adapter_config to dict format for template compatibility
            adapter_config_dict = {}
            if adapter_config_obj:
                adapter_config_dict = {
                    "network_code": adapter_config_obj.gam_network_code or "",
                    "refresh_token": adapter_config_obj.gam_refresh_token or "",
                    "trafficker_id": adapter_config_obj.gam_trafficker_id or "",
                    "application_name": getattr(adapter_config_obj, "gam_application_name", "") or "",
                    "service_account_email": adapter_config_obj.gam_service_account_email or "",
                    "network_currency": adapter_config_obj.gam_network_currency or "",
                    "secondary_currencies": adapter_config_obj.gam_secondary_currencies or [],
                    "network_timezone": adapter_config_obj.gam_network_timezone or "",
                }

            # Get environment info for URL generation
            is_production = os.environ.get("PRODUCTION") == "true"
            mcp_port = int(os.environ.get("ADCP_SALES_PORT", 8080)) if not is_production else None

            # JSON fields are automatically deserialized by JSONType
            # These are now guaranteed to be lists (or None) from the database
            authorized_domains = tenant.authorized_domains or []
            authorized_emails = tenant.authorized_emails or []

            # Get product counts
            from src.core.database.models import Product

            stmt = select(Product).filter_by(tenant_id=tenant_id)
            products = db_session.scalars(stmt).all()
            product_count = len(products)
            # Note: Product model doesn't have status field
            active_products = product_count  # All products are considered active
            draft_products = 0  # No draft status tracking

            # Creative formats removed - table dropped in migration f2addf453200
            # Formats are now fetched from creative agents via AdCP (not stored in DB)
            # Template section also removed - no longer passed to template

            # Get inventory counts
            from src.core.database.models import GAMInventory

            try:
                stmt = select(func.count()).select_from(GAMInventory).filter_by(tenant_id=tenant_id)
                inventory_count = db_session.scalar(stmt) or 0

                stmt = (
                    select(func.count())
                    .select_from(GAMInventory)
                    .filter_by(tenant_id=tenant_id, inventory_type="ad_unit")
                )
                ad_units_count = db_session.scalar(stmt) or 0

                stmt = (
                    select(func.count())
                    .select_from(GAMInventory)
                    .filter_by(tenant_id=tenant_id, inventory_type="placement")
                )
                placements_count = db_session.scalar(stmt) or 0

                stmt = (
                    select(func.count())
                    .select_from(GAMInventory)
                    .filter_by(tenant_id=tenant_id, inventory_type="custom_targeting_key")
                )
                custom_targeting_keys_count = db_session.scalar(stmt) or 0

                stmt = (
                    select(func.count())
                    .select_from(GAMInventory)
                    .filter_by(tenant_id=tenant_id, inventory_type="custom_targeting_value")
                )
                custom_targeting_values_count = db_session.scalar(stmt) or 0
            except Exception as e:
                # Table may not exist or query may fail - rollback and gracefully handle
                logger.warning(f"Could not load inventory counts: {e}")
                db_session.rollback()
                inventory_count = 0
                ad_units_count = 0
                placements_count = 0
                custom_targeting_keys_count = 0
                custom_targeting_values_count = 0

            # Get admin port
            admin_port = int(os.environ.get("ADMIN_UI_PORT", 8001))
            # Get A2A port (for agent cards)
            a2a_port = int(os.environ.get("A2A_PORT", 8091)) if not is_production else None

            # Get currency limits for this tenant
            from src.core.database.models import CurrencyLimit

            stmt = select(CurrencyLimit).filter_by(tenant_id=tenant_id).order_by(CurrencyLimit.currency_code)
            currency_limits = db_session.scalars(stmt).all()

            # Check for Gemini API key (tenant-specific only - no environment fallback in production)
            has_gemini_key = bool(tenant.gemini_api_key)

            # Get AI configuration for template
            ai_config = tenant.ai_config or {}
            current_provider = ai_config.get("provider", "")
            current_model = ai_config.get("model", "")
            has_logfire = bool(ai_config.get("logfire_token"))

            # Get setup checklist status
            setup_status = None
            try:
                checklist_service = SetupChecklistService(tenant_id)
                setup_status = checklist_service.get_setup_status()
            except Exception as e:
                logger.warning(f"Failed to load setup checklist: {e}")

            # Get script_name for production URL handling
            script_name = "/admin" if is_production else ""

            # Get available currencies from Babel
            available_currencies = get_available_currencies()

            return render_template(
                "tenant_settings.html",
                tenant=tenant,
                has_gemini_key=has_gemini_key,
                current_provider=current_provider,
                current_model=current_model,
                has_logfire=has_logfire,
                tenant_id=tenant_id,
                section=section or "general",
                active_adapter=active_adapter,
                adapter_config=adapter_config_dict,  # Use dict format
                oauth_configured=oauth_configured,
                gam_oauth_configured=gam_oauth_configured,  # Environment check for GAM OAuth
                last_sync_time=last_sync_time,
                running_sync=running_sync,  # Pass running sync info
                principals=principals,
                advertiser_count=advertiser_count,
                active_advertisers=active_advertisers,
                mcp_port=mcp_port,
                a2a_port=a2a_port,
                admin_port=admin_port,
                is_production=is_production,
                script_name=script_name,
                sales_agent_domain=get_sales_agent_domain(),
                authorized_domains=authorized_domains,
                authorized_emails=authorized_emails,
                product_count=product_count,
                active_products=active_products,
                draft_products=draft_products,
                inventory_count=inventory_count,
                ad_units_count=ad_units_count,
                currency_limits=currency_limits,
                placements_count=placements_count,
                custom_targeting_keys_count=custom_targeting_keys_count,
                custom_targeting_values_count=custom_targeting_values_count,
                setup_status=setup_status,
                available_currencies=available_currencies,  # Currency list from Babel
                single_tenant_mode=is_single_tenant_mode(),
            )

    except Exception as e:
        logger.error(f"Error loading tenant settings: {e}", exc_info=True)
        flash("Error loading settings", "error")
        return redirect(url_for("tenants.dashboard", tenant_id=tenant_id))


@tenants_bp.route("/<tenant_id>/update", methods=["POST"])
@log_admin_action("update")
@require_tenant_access()
def update(tenant_id):
    """Update tenant settings."""
    try:
        # Sanitize form data
        form_data = sanitize_form_data(request.form.to_dict())

        # Validate form data
        is_valid, errors = validate_form_data(form_data, ["name", "subdomain"])
        if not is_valid:
            for error in errors:
                flash(error, "error")
            return redirect(url_for("tenants.settings", tenant_id=tenant_id))

        with get_db_session() as db_session:
            tenant = db_session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
            if not tenant:
                flash("Tenant not found", "error")
                return redirect(url_for("core.index"))

            # Update tenant
            tenant.name = form_data.get("name", tenant.name)
            tenant.subdomain = form_data.get("subdomain", tenant.subdomain)
            tenant.billing_plan = form_data.get("billing_plan", tenant.billing_plan)
            tenant.updated_at = datetime.now(UTC)

            db_session.commit()
            flash("Tenant settings updated successfully", "success")

    except Exception as e:
        logger.error(f"Error updating tenant: {e}", exc_info=True)
        flash("Error updating tenant", "error")

    return redirect(url_for("tenants.settings", tenant_id=tenant_id))


@tenants_bp.route("/<tenant_id>/update_slack", methods=["POST"])
@log_admin_action("update_slack")
@require_tenant_access()
def update_slack(tenant_id):
    """Update tenant Slack settings."""
    try:
        from src.core.webhook_validator import WebhookURLValidator

        # Sanitize form data
        form_data = sanitize_form_data(request.form.to_dict())
        webhook_url = form_data.get("slack_webhook_url", "").strip()

        # Validate webhook URL for SSRF protection
        if webhook_url:
            is_valid, error_msg = WebhookURLValidator.validate_webhook_url(webhook_url)
            if not is_valid:
                flash(f"Invalid Slack webhook URL: {error_msg}", "error")
                return redirect(url_for("tenants.settings", tenant_id=tenant_id, section="slack"))

        with get_db_session() as db_session:
            tenant = db_session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
            if not tenant:
                flash("Tenant not found", "error")
                return redirect(url_for("core.index"))

            # Update Slack webhook
            tenant.slack_webhook_url = webhook_url if webhook_url else None
            tenant.updated_at = datetime.now(UTC)

            db_session.commit()
            flash("Slack settings updated successfully", "success")

    except Exception as e:
        logger.error(f"Error updating Slack settings: {e}", exc_info=True)
        flash("Error updating Slack settings", "error")

    return redirect(url_for("tenants.settings", tenant_id=tenant_id, section="slack"))


@tenants_bp.route("/<tenant_id>/test_slack", methods=["POST"])
@log_admin_action("test_slack")
@require_tenant_access()
def test_slack(tenant_id):
    """Test Slack webhook."""
    try:
        with get_db_session() as db_session:
            tenant = db_session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
            if not tenant:
                return jsonify({"success": False, "error": "Tenant not found"}), 404

            if not tenant.slack_webhook_url:
                return jsonify({"success": False, "error": "No Slack webhook configured"}), 400

            # Send test message
            import requests

            response = requests.post(
                tenant.slack_webhook_url,
                json={
                    "text": f"🎉 Test message from Prebid Sales Agent for {tenant.name}",
                    "blocks": [
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": f"*Test Notification*\nThis is a test message from the Prebid Sales Agent for *{tenant.name}*.",
                            },
                        },
                        {
                            "type": "context",
                            "elements": [
                                {
                                    "type": "mrkdwn",
                                    "text": f"Sent at {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}",
                                }
                            ],
                        },
                    ],
                },
                timeout=5,
            )

            if response.status_code == 200:
                return jsonify({"success": True, "message": "Test message sent successfully"})
            else:
                return (
                    jsonify(
                        {"success": False, "error": f"Slack returned status {response.status_code}: {response.text}"}
                    ),
                    400,
                )

    except requests.exceptions.RequestException as e:
        logger.error(f"Error testing Slack webhook: {e}")
        return jsonify({"success": False, "error": str(e)}), 500
    except Exception as e:
        logger.error(f"Unexpected error testing Slack: {e}", exc_info=True)
        return jsonify({"success": False, "error": "Internal server error"}), 500


@tenants_bp.route("/<tenant_id>/deactivate", methods=["POST"])
@log_admin_action("deactivate_tenant")
@require_tenant_access()
def deactivate_tenant(tenant_id):
    """Deactivate (soft delete) a tenant."""
    try:
        # Get confirmation from form
        confirm_name = request.form.get("confirm_name", "").strip()

        with get_db_session() as db_session:
            tenant = db_session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()

            if not tenant:
                flash("Tenant not found", "error")
                return redirect(url_for("core.index"))

            # Verify name matches
            if confirm_name != tenant.name:
                flash("Confirmation name did not match. Deactivation cancelled.", "error")
                return redirect(url_for("tenants.tenant_settings", tenant_id=tenant_id, section="danger-zone"))

            # Already inactive?
            if not tenant.is_active:
                flash("This sales agent is already deactivated.", "warning")
                return redirect(url_for("tenants.tenant_settings", tenant_id=tenant_id, section="danger-zone"))

            # Deactivate tenant (soft delete)
            tenant.is_active = False
            tenant.updated_at = datetime.now(UTC)
            db_session.commit()

            # Log to application logs
            logger.info(f"Tenant {tenant_id} ({tenant.name}) deactivated by user {session.get('user', 'unknown')}")

            # Create audit log entry for compliance
            from src.core.audit_logger import AuditLogger

            try:
                audit_logger = AuditLogger(tenant_id)
                audit_logger.log_security_event(
                    event_type="tenant_deactivation",
                    severity="critical",
                    user_email=session.get("user", "unknown"),
                    details={
                        "tenant_name": tenant.name,
                        "deactivated_at": datetime.now(UTC).isoformat(),
                        "deactivated_by": session.get("user", "unknown"),
                    },
                )
            except Exception as e:
                # Don't fail deactivation if audit logging fails
                logger.error(f"Failed to create audit log for deactivation: {e}")

            # Clear session and redirect to login
            session.clear()
            flash(
                f"Sales agent '{tenant.name}' has been deactivated. "
                "All data is preserved. Contact support to reactivate.",
                "success",
            )
            return redirect(url_for("auth.login"))

    except Exception as e:
        logger.error(f"Error deactivating tenant {tenant_id}: {e}", exc_info=True)
        flash(f"Error deactivating sales agent: {str(e)}", "error")
        return redirect(url_for("tenants.tenant_settings", tenant_id=tenant_id, section="danger-zone"))


@tenants_bp.route("/<tenant_id>/media-buys", methods=["GET"])
@require_tenant_access()
def media_buys_list(tenant_id):
    """List media buys with optional status filter."""
    from src.admin.services.media_buy_readiness_service import MediaBuyReadinessService
    from src.core.database.models import MediaBuy, Product

    try:
        # Get status filter from query params
        status_filter = request.args.get("status")

        with get_db_session() as db_session:
            # Get tenant
            tenant = db_session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
            if not tenant:
                flash("Tenant not found", "error")
                return redirect(url_for("core.index"))

            # Get all media buys
            stmt = select(MediaBuy).filter_by(tenant_id=tenant_id).order_by(MediaBuy.created_at.desc())
            all_media_buys = db_session.scalars(stmt).all()

            # Calculate readiness state for each and filter
            media_buys_with_state = []
            for media_buy in all_media_buys:
                readiness = MediaBuyReadinessService.get_readiness_state(
                    media_buy.media_buy_id, tenant_id, session=db_session
                )

                # Apply status filter if specified
                if status_filter and readiness["state"] != status_filter:
                    continue

                # Get principal name
                principal = None
                if media_buy.principal_id:
                    stmt = select(Principal).filter_by(tenant_id=tenant_id, principal_id=media_buy.principal_id)
                    principal = db_session.scalars(stmt).first()

                # Get product names from packages
                product_names = []
                if media_buy.raw_request and "packages" in media_buy.raw_request:
                    for package in media_buy.raw_request["packages"]:
                        product_id = package.get("product_id")
                        if product_id:
                            stmt = select(Product).filter_by(product_id=product_id)
                            product = db_session.scalars(stmt).first()
                            if product:
                                product_names.append(product.name)

                media_buys_with_state.append(
                    {
                        "media_buy": media_buy,
                        "readiness_state": readiness["state"],
                        "is_ready": readiness["is_ready_to_activate"],
                        "principal_name": principal.name if principal else "Unknown",
                        "product_names": product_names,
                        "packages_ready": readiness["packages_with_creatives"],
                        "packages_total": readiness["packages_total"],
                        "blocking_issues": readiness.get("blocking_issues", []),
                    }
                )

            return render_template(
                "media_buys_list.html",
                tenant=tenant,
                tenant_id=tenant_id,
                media_buys=media_buys_with_state,
                status_filter=status_filter,
            )

    except Exception as e:
        logger.error(f"Error listing media buys for tenant {tenant_id}: {e}", exc_info=True)
        flash(f"Error loading media buys: {str(e)}", "error")
        return redirect(url_for("tenants.dashboard", tenant_id=tenant_id))


@tenants_bp.route("/<tenant_id>/settings/auth")
@require_tenant_access()
def auth_settings(tenant_id):
    """Redirect to users page for authentication management."""
    # SSO config moved to users page
    return redirect(url_for("users.list_users", tenant_id=tenant_id))


# Constants for favicon upload
ALLOWED_FAVICON_EXTENSIONS = {"ico", "png", "svg", "jpg", "jpeg"}
MAX_FAVICON_SIZE = 1 * 1024 * 1024  # 1MB


def _get_favicon_upload_dir() -> str:
    """Get the favicon upload directory path."""
    # Get the project root (where static/ lives)
    current_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(current_dir)))
    return os.path.join(project_root, "static", "favicons")


def _allowed_favicon_file(filename: str) -> bool:
    """Check if the file extension is allowed for favicons."""
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_FAVICON_EXTENSIONS


def _is_safe_favicon_path(base_dir: str, tenant_id: str) -> bool:
    """Validate that the tenant favicon path doesn't escape the base directory."""
    tenant_dir = os.path.join(base_dir, tenant_id)
    resolved_base = os.path.realpath(base_dir)
    resolved_tenant = os.path.realpath(tenant_dir)
    return resolved_tenant.startswith(resolved_base + os.sep)


def _is_valid_favicon_url(url: str) -> bool:
    """Validate that a favicon URL is safe (HTTP/HTTPS only, no javascript: etc.)."""
    if not url:
        return True  # Empty URL is valid (clears the favicon)
    url_lower = url.lower()
    # Only allow HTTP and HTTPS URLs
    if not (url_lower.startswith("http://") or url_lower.startswith("https://")):
        return False
    # Block javascript: and data: schemes that could be obfuscated
    if "javascript:" in url_lower or "data:" in url_lower:
        return False
    return True


@tenants_bp.route("/<tenant_id>/upload_favicon", methods=["POST"])
@log_admin_action("upload_favicon")
@require_tenant_access()
def upload_favicon(tenant_id):
    """Upload a custom favicon for the tenant."""
    try:
        if "favicon" not in request.files:
            flash("No file selected", "error")
            return redirect(url_for("tenants.tenant_settings", tenant_id=tenant_id, section="account"))

        file = request.files["favicon"]
        if file.filename == "":
            flash("No file selected", "error")
            return redirect(url_for("tenants.tenant_settings", tenant_id=tenant_id, section="account"))

        if not file.filename or not _allowed_favicon_file(file.filename):
            flash(f"Invalid file type. Allowed: {', '.join(ALLOWED_FAVICON_EXTENSIONS)}", "error")
            return redirect(url_for("tenants.tenant_settings", tenant_id=tenant_id, section="account"))

        # Check file size
        file.seek(0, 2)  # Seek to end
        file_size = file.tell()
        file.seek(0)  # Reset to beginning

        if file_size > MAX_FAVICON_SIZE:
            flash(f"File too large. Maximum size: {MAX_FAVICON_SIZE // 1024}KB", "error")
            return redirect(url_for("tenants.tenant_settings", tenant_id=tenant_id, section="account"))

        # Create tenant-specific favicon directory
        upload_dir = _get_favicon_upload_dir()
        if not _is_safe_favicon_path(upload_dir, tenant_id):
            logger.error(f"Path traversal attempt detected for tenant: {tenant_id}")
            flash("Invalid tenant ID", "error")
            return redirect(url_for("tenants.tenant_settings", tenant_id=tenant_id, section="account"))
        tenant_favicon_dir = os.path.join(upload_dir, tenant_id)
        os.makedirs(tenant_favicon_dir, exist_ok=True)

        # Get the file extension
        ext = file.filename.rsplit(".", 1)[1].lower()
        # Save as favicon.{ext} in the tenant's directory
        filename = f"favicon.{ext}"
        filepath = os.path.join(tenant_favicon_dir, filename)

        # Remove any existing favicon files for this tenant
        for old_ext in ALLOWED_FAVICON_EXTENSIONS:
            old_file = os.path.join(tenant_favicon_dir, f"favicon.{old_ext}")
            if os.path.exists(old_file):
                os.remove(old_file)

        # Save the new favicon
        file.save(filepath)

        # Update the tenant's favicon_url in the database
        with get_db_session() as db_session:
            tenant = db_session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
            if tenant:
                # Store as a relative URL path
                tenant.favicon_url = f"/static/favicons/{tenant_id}/{filename}"
                tenant.updated_at = datetime.now(UTC)
                db_session.commit()

        flash("Favicon uploaded successfully", "success")

    except Exception as e:
        logger.error(f"Error uploading favicon for tenant {tenant_id}: {e}", exc_info=True)
        flash("Error uploading favicon. Please try again.", "error")

    return redirect(url_for("tenants.tenant_settings", tenant_id=tenant_id, section="account"))


@tenants_bp.route("/<tenant_id>/update_favicon_url", methods=["POST"])
@log_admin_action("update_favicon_url")
@require_tenant_access()
def update_favicon_url(tenant_id):
    """Update the tenant's favicon URL (for external URLs)."""
    try:
        form_data = sanitize_form_data(request.form.to_dict())
        favicon_url = form_data.get("favicon_url", "").strip()

        # Validate URL is safe (HTTP/HTTPS only)
        if favicon_url and not _is_valid_favicon_url(favicon_url):
            flash("Invalid favicon URL. Only HTTP and HTTPS URLs are allowed.", "error")
            return redirect(url_for("tenants.tenant_settings", tenant_id=tenant_id, section="account"))

        with get_db_session() as db_session:
            tenant = db_session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
            if not tenant:
                flash("Tenant not found", "error")
                return redirect(url_for("core.index"))

            # Clear or update the favicon URL
            tenant.favicon_url = favicon_url if favicon_url else None
            tenant.updated_at = datetime.now(UTC)
            db_session.commit()

            if favicon_url:
                flash("Favicon URL updated successfully", "success")
            else:
                flash("Favicon URL cleared - using default favicon", "success")

    except Exception as e:
        logger.error(f"Error updating favicon URL for tenant {tenant_id}: {e}", exc_info=True)
        flash("Error updating favicon URL. Please try again.", "error")

    return redirect(url_for("tenants.tenant_settings", tenant_id=tenant_id, section="account"))


@tenants_bp.route("/<tenant_id>/remove_favicon", methods=["POST"])
@log_admin_action("remove_favicon")
@require_tenant_access()
def remove_favicon(tenant_id):
    """Remove the tenant's custom favicon."""
    try:
        with get_db_session() as db_session:
            tenant = db_session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
            if not tenant:
                flash("Tenant not found", "error")
                return redirect(url_for("core.index"))

            # If it's an uploaded file, try to delete it
            if tenant.favicon_url and tenant.favicon_url.startswith("/static/favicons/"):
                upload_dir = _get_favicon_upload_dir()
                if _is_safe_favicon_path(upload_dir, tenant_id):
                    tenant_favicon_dir = os.path.join(upload_dir, tenant_id)
                    for ext in ALLOWED_FAVICON_EXTENSIONS:
                        filepath = os.path.join(tenant_favicon_dir, f"favicon.{ext}")
                        if os.path.exists(filepath):
                            os.remove(filepath)
                else:
                    logger.error(f"Path traversal attempt detected for tenant: {tenant_id}")

            # Clear the favicon URL
            tenant.favicon_url = None
            tenant.updated_at = datetime.now(UTC)
            db_session.commit()

        flash("Favicon removed - using default favicon", "success")

    except Exception as e:
        logger.error(f"Error removing favicon for tenant {tenant_id}: {e}", exc_info=True)
        flash("Error removing favicon. Please try again.", "error")

    return redirect(url_for("tenants.tenant_settings", tenant_id=tenant_id, section="account"))
