"""Tenant management blueprint for admin UI.

⚠️ ROUTING NOTICE: This file contains the ACTUAL handler for tenant settings!
- URL: /admin/tenant/{id}/settings
- Function: settings()
- DO NOT confuse with src/admin/blueprints/settings.py which handles superadmin settings
"""

import json
import logging
import os
import re
from datetime import UTC, datetime

from babel import numbers as babel_numbers
from flask import Blueprint, flash, jsonify, redirect, render_template, request, session, url_for
from sqlalchemy import select

from src.admin.services import DashboardService
from src.admin.utils import get_tenant_config_from_db, require_tenant_access
from src.admin.utils.audit_decorator import log_admin_action
from src.admin.utils.embedded_capabilities import capability_owned_response, publisher_owns
from src.admin.utils.embedded_mode_auth import is_embedded_view
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
            logger.debug("Could not resolve currency %s", code, exc_info=True)
            continue

    return currencies


@tenants_bp.route("/<tenant_id>", strict_slashes=False)
@require_tenant_access()
def dashboard(tenant_id):
    """Show tenant dashboard using single data source pattern.

    ``strict_slashes=False`` accepts both ``/tenant/<id>`` and
    ``/tenant/<id>/`` — important for reverse-proxy callers (Scope3
    Storefront) that may generate URLs with trailing slashes. Without
    this flag Flask 404s on the trailing variant.
    """
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

        # Ledger dashboard data — masthead + 3-column pipeline + chart +
        # attention rail + activity ledger. Bundled in one service call
        # so the template never reaches back for piecemeal queries.
        ledger = dashboard_service.get_ledger_dashboard()

        # Get tenant config for features
        config = get_tenant_config_from_db(tenant_id)
        features = config.get("features", {})

        # Get setup checklist status + dashboard jobs (#471).
        # Hygiene checklist gates orders entirely (SSO/AAO/currency/ad server).
        # Dashboard jobs show the three ongoing operator surfaces — discovery,
        # composition, delivery — always visible (not a setup sequence).
        setup_status = None
        dashboard_jobs = None
        try:
            checklist_service = SetupChecklistService(tenant_id)
            setup_status = checklist_service.get_setup_status()
            dashboard_jobs = checklist_service.get_dashboard_jobs()
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
            # Ledger dashboard bundle (masthead, incoming, running, pipeline,
            # revenue_chart, needs_attention, activity_ledger)
            ledger=ledger,
            # Setup checklist + dashboard jobs
            setup_status=setup_status,
            dashboard_jobs=dashboard_jobs,
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


_PROMOTED_SECTION_REDIRECTS = {
    # Sprint 7 Phase 2 + 3 entity promotions. Old deep-links like
    # ``/settings/publishers`` or ``/settings/signing-keys`` would silently
    # render the default account section now that the in-page sections
    # are gone — give them a clean redirect to the new standalone page
    # instead. Each entry is ``section_slug → endpoint_name``.
    "publishers": "publisher_partners.publishers_page",
    "signing-keys": "tenants.signing_keys_page",
    "business-rules": "settings.policies_page",
    # Cover the new endpoint slugs too — a user typing
    # ``/settings/policies`` (no trailing slash) hits this section
    # route instead of the standalone page; redirect it cleanly.
    "policies": "settings.policies_page",
    "integrations": "settings.integrations_page",
    # Sprint 7 Phase 3 (#437): Products + Inventory in-page tabs are gone.
    # Products is in primary top nav; Inventory's sync UI lives at /inventory.
    "products": "products.list_products",
    "inventory": "inventory.inventory_browser",
}


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
    if section and section in _PROMOTED_SECTION_REDIRECTS:
        return redirect(url_for(_PROMOTED_SECTION_REDIRECTS[section], tenant_id=tenant_id))
    try:
        with get_db_session() as db_session:
            from sqlalchemy import select

            stmt = select(Tenant).filter_by(tenant_id=tenant_id)
            tenant = db_session.scalars(stmt).first()
            if not tenant:
                flash("Tenant not found", "error")
                return redirect(url_for("core.index"))

            # Phase 4d: every remaining tenant_settings.html subsection is either
            # promoted out, hard-hidden, or platform-managed on embedded. Render
            # the standard locked page instead of the legacy multi-section UI.
            if is_embedded_view(tenant):
                return render_template("_embedded_locked_page.html", tenant=tenant), 200

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
            from src.core.database.models import Principal

            stmt = select(Principal).filter_by(tenant_id=tenant_id)
            principals = db_session.scalars(stmt).all()
            advertiser_count = len(principals)
            active_advertisers = len(principals)  # For now, assume all are active

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

            # All services (MCP, A2A, Admin) run on the same unified port
            admin_port = int(os.environ.get("ADCP_SALES_PORT", 8080)) if not is_production else None
            a2a_port = admin_port

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

            script_name = request.script_root or request.environ.get("SCRIPT_NAME", "")

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
                currency_limits=currency_limits,
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
@require_tenant_access(role=("admin",))
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
            return redirect(url_for("tenants.tenant_settings", tenant_id=tenant_id))

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

    return redirect(url_for("tenants.tenant_settings", tenant_id=tenant_id))


@tenants_bp.route("/<tenant_id>/update_slack", methods=["POST"])
@log_admin_action("update_slack")
@require_tenant_access(role=("admin",))
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
                return redirect(url_for("tenants.tenant_settings", tenant_id=tenant_id, section="slack"))

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

    return redirect(url_for("tenants.tenant_settings", tenant_id=tenant_id, section="slack"))


@tenants_bp.route("/<tenant_id>/test_slack", methods=["POST"])
@log_admin_action("test_slack")
@require_tenant_access(role=("admin",), allow_embedded_writes=True)
def test_slack(tenant_id):
    """Test Slack webhook.

    Read-only probe — sends a test message to the configured webhook and
    never writes to tenant state — so it opts into the embedded-write gate.
    """
    if not publisher_owns("slack"):
        return capability_owned_response("slack")
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
@require_tenant_access(role=("admin",))
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


# Kid format the salesagent accepts for filename use. The adcp library's
# ``_default_kid`` returns shapes like ``adcp-ed25519-20260508-abcd``;
# this regex matches that and rejects anything that could escape the
# signing-keys directory if a future library release widens the format.
_SIGNING_KID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")


def _embedded_signing_blocked(tenant_id: str) -> bool:
    """Return True if signing-key writes should be rejected for this tenant.

    Sprint 7 Phase 4c: signing keys are dead code on embedded tenants —
    the salesagent doesn't issue webhooks under its own domain there,
    the storefront signs. There's no "publisher" answer; reject all
    writes regardless of capability flag.
    """
    from src.core.database.repositories.tenant_config import TenantConfigRepository

    with get_db_session() as db_session:
        tenant = TenantConfigRepository(db_session, tenant_id).get_tenant()
        return tenant is not None and is_embedded_view(tenant)


def _verify_request_same_origin() -> bool:
    """Reject signing-key POSTs from third-party origins.

    Browsers always send ``Origin`` on cross-origin POSTs and
    ``Referer`` on most form submissions; an attacker on
    ``evil.example.com`` cannot forge either. The cookie's ``SameSite``
    is set to ``None`` in production for OAuth flow reasons, so
    SameSite alone is not a CSRF defense — Origin/Referer is.
    Implemented inline (not Flask-WTF) to keep the dependency surface
    small; see issue #32 for app-wide CSRF.
    """
    candidate = request.headers.get("Origin") or request.headers.get("Referer") or ""
    if not candidate:
        return False
    expected = request.host_url.rstrip("/")
    return candidate == expected or candidate.startswith(expected + "/")


@tenants_bp.route("/<tenant_id>/signing-keys/", methods=["GET"])
@require_tenant_access()
def signing_keys_page(tenant_id):
    """Render the standalone Signing Keys page (Sprint 7 Phase 2).

    Promoted out of ``tenant_settings.html`` into a Configure → Workspace
    peer page. Hard-hidden on embedded tenants per Sprint 7 Phase 4c —
    the salesagent doesn't issue webhooks under its own domain there,
    the storefront signs. No "publisher" answer; reject all reads.
    """
    from src.core.database.repositories import TenantSigningCredentialRepository
    from src.core.database.repositories.tenant_config import TenantConfigRepository

    with get_db_session() as db_session:
        tenant = TenantConfigRepository(db_session, tenant_id).get_tenant()
        if not tenant:
            flash("Tenant not found", "error")
            return redirect(url_for("core.index"))

        if is_embedded_view(tenant):
            return "Signing keys are not available on embedded tenants.", 403

        cred_repo = TenantSigningCredentialRepository(db_session, tenant_id)
        signing_credentials = []
        for purpose in ("webhook-signing", "request-signing-as-buyer"):
            signing_credentials.extend(cred_repo.list_for_purpose(purpose, include_inactive=True))

        return render_template(
            "signing_keys.html",
            tenant=tenant,
            signing_credentials=signing_credentials,
        )


@tenants_bp.route("/<tenant_id>/signing-keys/generate", methods=["POST"])
@log_admin_action(
    "generate_webhook_signing_key",
    extract_details=lambda r, **kw: {"key_id": getattr(r, "_generated_kid", None)},
)
@require_tenant_access(role=("admin",))
def generate_webhook_signing_key(tenant_id):
    """Generate an Ed25519 keypair for webhook signing.

    Writes the PEM under ``WEBHOOK_SIGNING_KEYS_DIR`` (atomically
    created with mode 0600 via ``os.open(..., O_EXCL)``) and inserts a
    ``TenantSigningCredential`` row with ``is_active=True``. If a
    previous active credential exists, it is rotated out in the same
    transaction so the partial unique index
    ``ux_tenant_signing_credentials_active`` invariant holds.

    The session listener in ``src.services.webhook_signing`` evicts
    the per-process snapshot cache on commit; cross-replica caches
    converge within the 5-min TTL window.

    Embedded tenants don't issue webhooks under the salesagent's own
    domain — the storefront signs. Reject the write (Sprint 7 Phase 4c).
    """
    from adcp.signing.keygen import generate_signing_keypair

    from src.core.database.repositories import TenantSigningCredentialRepository
    from src.services.webhook_signing import _resolve_signing_keys_dir

    if _embedded_signing_blocked(tenant_id):
        return "Signing keys are not available on embedded tenants.", 403

    redirect_resp = redirect(url_for("tenants.signing_keys_page", tenant_id=tenant_id))

    if not _verify_request_same_origin():
        flash("Request blocked: cross-origin POST refused.", "error")
        return redirect_resp

    try:
        pem_bytes, jwk = generate_signing_keypair(alg="ed25519", purpose="webhook-signing")
        kid = jwk.get("kid")
        if not kid or not _SIGNING_KID_RE.fullmatch(kid):
            logger.error(f"Generated kid {kid!r} failed validation against {_SIGNING_KID_RE.pattern!r}")
            flash("Internal error generating signing key (invalid kid). See logs.", "error")
            return redirect_resp

        keys_dir = _resolve_signing_keys_dir()
        keys_dir.mkdir(parents=True, exist_ok=True)
        pem_path = (keys_dir / f"{tenant_id}-{kid}.pem").resolve()
        # Containment check: even after symlink/relative-path collapse,
        # the resolved path must still live under keys_dir. Belt-and-
        # suspenders alongside the kid regex.
        if not pem_path.is_relative_to(keys_dir.resolve()):
            logger.error(f"Computed PEM path {pem_path} escapes keys_dir {keys_dir}")
            flash("Internal error generating signing key (path traversal). See logs.", "error")
            return redirect_resp

        # Atomic create with mode 0600 baked in — never exists transiently
        # at a wider mode. ``O_EXCL`` refuses to overwrite, so a kid
        # collision (essentially impossible given 4 hex chars of entropy
        # in adcp's _default_kid) fails loudly instead of silently
        # clobbering a still-active key.
        fd = os.open(str(pem_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(fd, pem_bytes)
        finally:
            os.close(fd)

        with get_db_session() as db_session:
            repo = TenantSigningCredentialRepository(db_session, tenant_id=tenant_id)
            existing = repo.get_active("webhook-signing")
            if existing is not None:
                repo.rotate_out("webhook-signing", existing.key_id)
            repo.create(
                purpose="webhook-signing",
                backend="local_pem",
                backend_ref=str(pem_path),
                public_jwk=jwk,
                key_id=kid,
            )
            db_session.commit()

        # Stash the kid on the response so ``log_admin_action``'s
        # ``extract_details`` can record it in the audit row.
        redirect_resp._generated_kid = kid
        flash(
            f"Generated new webhook-signing keypair (kid={kid}). "
            "Publish the public JWK below to your JWKS endpoint so buyers can verify.",
            "success",
        )
    except Exception:
        logger.exception(f"Error generating webhook signing key for {tenant_id}")
        flash("Error generating signing key. See logs for details.", "error")
    return redirect_resp


@tenants_bp.route("/<tenant_id>/signing-keys/<key_id>/rotate-out", methods=["POST"])
@log_admin_action(
    "rotate_out_webhook_signing_key",
    extract_details=lambda r, **kw: {"key_id": kw.get("key_id")},
)
@require_tenant_access(role=("admin",))
def rotate_out_webhook_signing_key(tenant_id, key_id):
    """Mark a webhook-signing credential inactive.

    The salesagent stops signing with this kid immediately (cache
    evicted by the session listener on commit). The PEM file on disk
    is intentionally NOT deleted — buyers may still receive webhooks
    referencing the old kid in flight, and the verifier-side JWKS
    can take time to drop the entry.

    Embedded tenants: rejected — see ``generate_webhook_signing_key``.
    """
    from src.core.database.repositories import TenantSigningCredentialRepository

    if _embedded_signing_blocked(tenant_id):
        return "Signing keys are not available on embedded tenants.", 403

    redirect_resp = redirect(url_for("tenants.signing_keys_page", tenant_id=tenant_id))

    if not _verify_request_same_origin():
        flash("Request blocked: cross-origin POST refused.", "error")
        return redirect_resp

    try:
        with get_db_session() as db_session:
            repo = TenantSigningCredentialRepository(db_session, tenant_id=tenant_id)
            ok = repo.rotate_out("webhook-signing", key_id)
            if not ok:
                flash(f"No webhook-signing credential found with kid={key_id!r}.", "error")
                return redirect_resp
            db_session.commit()

        flash(f"Rotated out webhook-signing kid={key_id}. Generate a replacement to resume signing.", "success")
    except Exception:
        logger.exception(f"Error rotating out signing key {key_id} for {tenant_id}")
        flash("Error rotating out signing key. See logs for details.", "error")
    return redirect_resp


@tenants_bp.route("/<tenant_id>/sync-gam-orders", methods=["POST"])
@require_tenant_access(role=("admin",))
def sync_gam_orders(tenant_id, **kwargs):
    from flask import flash, redirect, url_for

    from src.services.gam_advertisers_sync import _build_gam_client_for_tenant
    from src.services.gam_orders_service import GAMOrdersService

    try:
        with get_db_session() as db_session:
            summary = GAMOrdersService(db_session).sync_tenant_orders(
                tenant_id, _build_gam_client_for_tenant(tenant_id)
            )
        orders = summary.get("orders", {}).get("total", 0)
        line_items = summary.get("line_items", {}).get("total", 0)
        flash(f"GAM orders synced: {orders} orders, {line_items} line items.", "success")
    except Exception as e:
        logger.error("GAM orders sync failed for tenant %s: %s", tenant_id, e, exc_info=True)
        flash(f"GAM orders sync failed: {e}", "error")
    return redirect(url_for("tenants.media_buys_list", tenant_id=tenant_id))


@tenants_bp.route("/<tenant_id>/media-buys", methods=["GET"])
@require_tenant_access()
def media_buys_list(tenant_id):
    """List media buys with optional status filter."""
    from src.admin.services.media_buy_readiness_service import MediaBuyReadinessService
    from src.core.database.models import Product
    from src.core.database.repositories import MediaBuyRepository

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
            repo = MediaBuyRepository(db_session, tenant_id)
            all_media_buys = repo.list_all_ordered_by_created()

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
@require_tenant_access(role=("admin",))
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
@require_tenant_access(role=("admin",))
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
@require_tenant_access(role=("admin",))
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
