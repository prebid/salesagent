"""Tenant management blueprint for admin UI.

⚠️ ROUTING NOTICE: This file contains the ACTUAL handler for tenant settings!
- URL: /admin/tenant/{id}/settings
- Function: settings()
- DO NOT confuse with src/admin/blueprints/settings.py which handles superadmin settings
"""

import json
import logging
import os
import secrets
import uuid
from datetime import UTC, datetime

from flask import Blueprint, flash, jsonify, redirect, render_template, request, session, url_for
from sqlalchemy import func, select

from src.admin.services import DashboardService
from src.admin.utils import get_tenant_config_from_db, require_auth, require_tenant_access
from src.core.database.database_session import get_db_session
from src.core.database.models import Principal, Tenant, User
from src.core.validation import sanitize_form_data, validate_form_data

logger = logging.getLogger(__name__)

# Create Blueprint
tenants_bp = Blueprint("tenants", __name__, url_prefix="/tenant")


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

            # Get advertiser data for the advertisers section
            from src.core.database.models import Principal

            stmt = select(Principal).filter_by(tenant_id=tenant_id)
            principals = db_session.scalars(stmt).all()
            advertiser_count = len(principals)
            active_advertisers = len(principals)  # For now, assume all are active

            # Get additional variables that the template expects
            last_sync_time = None  # Could be enhanced to track actual sync times

            # Convert adapter_config to dict format for template compatibility
            adapter_config_dict = {}
            if adapter_config_obj:
                adapter_config_dict = {
                    "network_code": adapter_config_obj.gam_network_code or "",
                    "refresh_token": adapter_config_obj.gam_refresh_token or "",
                    "trafficker_id": adapter_config_obj.gam_trafficker_id or "",
                    "application_name": getattr(adapter_config_obj, "gam_application_name", "") or "",
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

            # Get creative formats
            from src.core.database.models import CreativeFormat

            stmt = select(CreativeFormat).filter_by(tenant_id=tenant_id)
            creative_formats = db_session.scalars(stmt).all()

            # Get inventory counts
            from src.core.database.models import GAMInventory

            stmt = select(func.count()).select_from(GAMInventory).filter_by(tenant_id=tenant_id)
            inventory_count = db_session.scalar(stmt) or 0

            stmt = (
                select(func.count()).select_from(GAMInventory).filter_by(tenant_id=tenant_id, inventory_type="ad_unit")
            )
            ad_units_count = db_session.scalar(stmt) or 0

            stmt = (
                select(func.count())
                .select_from(GAMInventory)
                .filter_by(tenant_id=tenant_id, inventory_type="placement")
            )
            placements_count = db_session.scalar(stmt) or 0

            # Get admin port
            admin_port = int(os.environ.get("ADMIN_UI_PORT", 8001))

            # Get currency limits for this tenant
            from src.core.database.models import CurrencyLimit

            stmt = select(CurrencyLimit).filter_by(tenant_id=tenant_id).order_by(CurrencyLimit.currency_code)
            currency_limits = db_session.scalars(stmt).all()

            return render_template(
                "tenant_settings.html",
                tenant=tenant,
                tenant_id=tenant_id,
                section=section or "general",
                active_adapter=active_adapter,
                adapter_config=adapter_config_dict,  # Use dict format
                oauth_configured=oauth_configured,
                last_sync_time=last_sync_time,
                principals=principals,
                advertiser_count=advertiser_count,
                active_advertisers=active_advertisers,
                mcp_port=mcp_port,
                admin_port=admin_port,
                is_production=is_production,
                authorized_domains=authorized_domains,
                authorized_emails=authorized_emails,
                product_count=product_count,
                active_products=active_products,
                draft_products=draft_products,
                creative_formats=creative_formats,
                inventory_count=inventory_count,
                ad_units_count=ad_units_count,
                currency_limits=currency_limits,
                placements_count=placements_count,
            )

    except Exception as e:
        logger.error(f"Error loading tenant settings: {e}", exc_info=True)
        flash("Error loading settings", "error")
        return redirect(url_for("tenants.dashboard", tenant_id=tenant_id))


@tenants_bp.route("/<tenant_id>/update", methods=["POST"])
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
@require_tenant_access()
def update_slack(tenant_id):
    """Update tenant Slack settings."""
    try:
        # Sanitize form data
        form_data = sanitize_form_data(request.form.to_dict())
        webhook_url = form_data.get("slack_webhook_url", "").strip()

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
                    "text": f"🎉 Test message from AdCP Sales Agent for {tenant.name}",
                    "blocks": [
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": f"*Test Notification*\nThis is a test message from the AdCP Sales Agent for *{tenant.name}*.",
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


@tenants_bp.route("/<tenant_id>/update", methods=["POST"])
@require_auth()
def update_tenant(tenant_id):
    """Update tenant configuration."""
    # Check access based on role
    if session.get("role") == "viewer":
        return "Access denied. Viewers cannot update configuration.", 403

    # Check if user is trying to update another tenant
    if session.get("role") in ["admin", "manager", "tenant_admin"] and session.get("tenant_id") != tenant_id:
        return "Access denied. You can only update your own tenant.", 403

    with get_db_session() as db_session:
        try:
            # Get form data for individual fields
            max_daily_budget = request.form.get("max_daily_budget", type=int)
            enable_axe_signals = request.form.get("enable_axe_signals") == "true"
            human_review_required = request.form.get("human_review_required") == "true"

            # Find and update tenant
            tenant = db_session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
            if tenant:
                tenant.max_daily_budget = max_daily_budget
                tenant.enable_axe_signals = enable_axe_signals
                tenant.human_review_required = human_review_required
                tenant.updated_at = datetime.now().isoformat()

                db_session.commit()
                flash("Configuration updated successfully", "success")
            else:
                flash("Tenant not found", "error")

            return redirect(url_for("tenants.dashboard", tenant_id=tenant_id))
        except Exception as e:
            flash(f"Error updating configuration: {str(e)}", "error")
            return redirect(url_for("tenants.dashboard", tenant_id=tenant_id))


@tenants_bp.route("/<tenant_id>/users")
@require_tenant_access()
def list_users(tenant_id):
    """List users for a tenant."""
    try:
        with get_db_session() as db_session:
            tenant = db_session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
            if not tenant:
                flash("Tenant not found", "error")
                return redirect(url_for("core.index"))

            stmt = select(User).filter_by(tenant_id=tenant_id).order_by(User.is_admin.desc(), User.email)
            users = db_session.scalars(stmt).all()

            return render_template(
                "users.html",
                tenant=tenant,
                tenant_id=tenant_id,
                users=users,
            )

    except Exception as e:
        logger.error(f"Error loading users: {e}", exc_info=True)
        flash("Error loading users", "error")
        return redirect(url_for("tenants.dashboard", tenant_id=tenant_id))


@tenants_bp.route("/<tenant_id>/users/add", methods=["POST"])
@require_tenant_access()
def add_user(tenant_id):
    """Add a new user to tenant."""
    try:
        # Sanitize form data
        form_data = sanitize_form_data(request.form.to_dict())

        # Validate form data
        is_valid, errors = validate_form_data(form_data, ["email", "name"])
        if not is_valid:
            for error in errors:
                flash(error, "error")
            return redirect(url_for("tenants.list_users", tenant_id=tenant_id))

        with get_db_session() as db_session:
            # Check if user already exists
            stmt = select(User).filter_by(tenant_id=tenant_id, email=form_data["email"].lower())
            existing = db_session.scalars(stmt).first()
            if existing:
                flash("User already exists", "error")
                return redirect(url_for("tenants.list_users", tenant_id=tenant_id))

            # Create new user
            user = User(
                user_id=str(uuid.uuid4()),
                tenant_id=tenant_id,
                email=form_data["email"].lower(),
                name=form_data["name"],
                is_admin=form_data.get("is_admin") == "on",
                is_active=True,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
            db_session.add(user)
            db_session.commit()

            flash(f"User {user.email} added successfully", "success")

    except Exception as e:
        logger.error(f"Error adding user: {e}", exc_info=True)
        flash("Error adding user", "error")

    return redirect(url_for("tenants.list_users", tenant_id=tenant_id))


@tenants_bp.route("/<tenant_id>/users/<user_id>/toggle", methods=["POST"])
@require_tenant_access()
def toggle_user(tenant_id, user_id):
    """Toggle user active status."""
    try:
        with get_db_session() as db_session:
            user = db_session.scalars(select(User).filter_by(tenant_id=tenant_id, user_id=user_id)).first()
            if not user:
                flash("User not found", "error")
                return redirect(url_for("tenants.list_users", tenant_id=tenant_id))

            user.is_active = not user.is_active
            user.updated_at = datetime.now(UTC)
            db_session.commit()

            status = "activated" if user.is_active else "deactivated"
            flash(f"User {user.email} {status}", "success")

    except Exception as e:
        logger.error(f"Error toggling user: {e}", exc_info=True)
        flash("Error updating user", "error")

    return redirect(url_for("tenants.list_users", tenant_id=tenant_id))


@tenants_bp.route("/<tenant_id>/users/<user_id>/update_role", methods=["POST"])
@require_tenant_access()
def update_user_role(tenant_id, user_id):
    """Update user admin role."""
    try:
        with get_db_session() as db_session:
            user = db_session.scalars(select(User).filter_by(tenant_id=tenant_id, user_id=user_id)).first()
            if not user:
                flash("User not found", "error")
                return redirect(url_for("tenants.list_users", tenant_id=tenant_id))

            user.is_admin = request.form.get("is_admin") == "on"
            user.updated_at = datetime.now(UTC)
            db_session.commit()

            role = "admin" if user.is_admin else "user"
            flash(f"User {user.email} updated to {role}", "success")

    except Exception as e:
        logger.error(f"Error updating user role: {e}", exc_info=True)
        flash("Error updating user", "error")

    return redirect(url_for("tenants.list_users", tenant_id=tenant_id))


@tenants_bp.route("/<tenant_id>/principals/create", methods=["GET", "POST"])
@require_tenant_access()
def create_principal(tenant_id):
    """Create a new principal (advertiser) for the tenant."""
    if request.method == "POST":
        try:
            # Sanitize form data
            form_data = sanitize_form_data(request.form.to_dict())

            # Validate form data
            is_valid, errors = validate_form_data(form_data, ["name"])
            if not is_valid:
                for error in errors:
                    flash(error, "error")
                    return redirect(url_for("tenants.create_principal", tenant_id=tenant_id))

            with get_db_session() as db_session:
                # Create principal
                principal_id = f"principal_{uuid.uuid4().hex[:8]}"
                access_token = secrets.token_urlsafe(32)

                # Build platform mappings based on adapter
                platform_mappings = {}

                # Get tenant to check adapter type
                tenant = db_session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
                if tenant and tenant.adapter_config:
                    adapter_config_obj = tenant.adapter_config

                    if adapter_config_obj.adapter_type == "google_ad_manager":
                        # Get GAM advertiser ID from form
                        gam_advertiser_id = form_data.get("gam_advertiser_id", "").strip()
                        if gam_advertiser_id:
                            platform_mappings["google_ad_manager"] = {
                                "advertiser_id": gam_advertiser_id,
                                "advertiser_name": form_data.get("gam_advertiser_name", ""),
                            }
                        else:
                            # GAM but no advertiser ID provided, use default
                            platform_mappings["google_ad_manager"] = {
                                "advertiser_id": f"gam_{principal_id[:8]}",
                                "advertiser_name": form_data["name"],
                            }
                    elif adapter_config_obj.adapter_type == "mock":
                        # For mock adapter, create a default mapping
                        platform_mappings["mock"] = {
                            "advertiser_id": f"mock_adv_{principal_id[:8]}",
                            "advertiser_name": form_data["name"],
                        }
                    else:
                        # For other adapters, create a basic mapping
                        platform_mappings[adapter_config_obj.adapter_type] = {
                            "advertiser_id": f"{adapter_config_obj.adapter_type}_{principal_id[:8]}",
                            "advertiser_name": form_data["name"],
                        }
                else:
                    # Default to mock if no adapter configured
                    platform_mappings["mock"] = {
                        "advertiser_id": f"mock_adv_{principal_id[:8]}",
                        "advertiser_name": form_data["name"],
                    }

                principal = Principal(
                    principal_id=principal_id,
                    tenant_id=tenant_id,
                    name=form_data["name"],
                    access_token=access_token,
                    platform_mappings=json.dumps(platform_mappings),  # Always provide JSON, even if empty dict
                    created_at=datetime.now(UTC),
                )
                db_session.add(principal)
                db_session.commit()

                flash(f"Advertiser '{principal.name}' created successfully", "success")
                return redirect(url_for("tenants.dashboard", tenant_id=tenant_id))

        except Exception as e:
            logger.error(f"Error creating principal: {e}", exc_info=True)
            flash("Error creating advertiser", "error")
            return redirect(url_for("tenants.create_principal", tenant_id=tenant_id))

    # GET request - show form
    try:
        with get_db_session() as db_session:
            tenant = db_session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
            if not tenant:
                flash("Tenant not found", "error")
                return redirect(url_for("core.index"))

            # Check if GAM is enabled
            gam_enabled = False
            if tenant.adapter_config:
                gam_enabled = tenant.adapter_config.adapter_type == "google_ad_manager"

            return render_template(
                "create_principal.html",
                tenant=tenant,
                tenant_id=tenant_id,
                has_gam=gam_enabled,  # Template expects has_gam not gam_enabled
            )

    except Exception as e:
        logger.error(f"Error loading create principal form: {e}", exc_info=True)
        flash("Error loading form", "error")
        return redirect(url_for("tenants.dashboard", tenant_id=tenant_id))


@tenants_bp.route("/<tenant_id>/principal/<principal_id>/update_mappings", methods=["POST"])
@require_tenant_access()
def update_principal_mappings(tenant_id, principal_id):
    """Update principal platform mappings."""
    try:
        # Sanitize form data
        form_data = sanitize_form_data(request.form.to_dict())

        with get_db_session() as db_session:
            principal = db_session.scalars(
                select(Principal).filter_by(tenant_id=tenant_id, principal_id=principal_id)
            ).first()
            if not principal:
                return jsonify({"error": "Principal not found"}), 404

            # Parse existing mappings (handle both string and dict formats)
            platform_mappings = principal.platform_mappings
            if platform_mappings and isinstance(platform_mappings, str):
                platform_mappings = json.loads(platform_mappings)
            elif not platform_mappings:
                platform_mappings = {}

            # Update mappings based on form data
            for key, value in form_data.items():
                if key.startswith("mapping_"):
                    parts = key.split("_", 2)
                    if len(parts) == 3:
                        platform = parts[1]
                        field = parts[2]
                        if platform not in platform_mappings:
                            platform_mappings[platform] = {}
                        platform_mappings[platform][field] = value

            # Save updated mappings
            principal.platform_mappings = json.dumps(platform_mappings)
            principal.updated_at = datetime.now(UTC)
            db_session.commit()

            return jsonify({"success": True})

    except Exception as e:
        logger.error(f"Error updating principal mappings: {e}", exc_info=True)
        return jsonify({"error": "Internal server error"}), 500


@tenants_bp.route("/<tenant_id>/deactivate", methods=["POST"])
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


@tenants_bp.route("/<tenant_id>/principals/<principal_id>/delete", methods=["DELETE"])
@require_tenant_access()
def delete_principal(tenant_id, principal_id):
    """Delete a principal/advertiser."""
    try:
        with get_db_session() as db_session:
            # Find the principal
            principal = db_session.scalars(
                select(Principal).filter_by(tenant_id=tenant_id, principal_id=principal_id)
            ).first()

            if not principal:
                return jsonify({"error": "Principal not found"}), 404

            # Check if principal has active media buys
            from src.core.database.models import MediaBuy

            stmt = (
                select(func.count())
                .select_from(MediaBuy)
                .filter_by(tenant_id=tenant_id, principal_id=principal_id)
                .where(MediaBuy.status.in_(["active", "pending"]))
            )
            active_buys = db_session.scalar(stmt)

            if active_buys > 0:
                return jsonify({"error": f"Cannot delete principal with {active_buys} active media buys"}), 400

            # Delete the principal
            db_session.delete(principal)
            db_session.commit()

            return jsonify({"success": True, "message": f"Principal {principal.name} deleted successfully"})

    except Exception as e:
        logger.error(f"Error deleting principal {principal_id}: {e}", exc_info=True)
        return jsonify({"error": "Internal server error"}), 500
