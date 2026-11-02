"""Principals (Advertisers) management blueprint for admin UI."""

import json
import logging
import uuid
from datetime import UTC, datetime

from adcp.types import AuthenticationScheme
from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for
from sqlalchemy import func, select

from src.admin.services import DashboardService
from src.admin.utils import require_tenant_access
from src.admin.utils.audit_decorator import log_admin_action, record_admin_action_failure
from src.core.database.database_session import get_db_session
from src.core.database.models import MediaBuy, PushNotificationConfig, Tenant
from src.core.database.repositories.principal import PrincipalRepository
from src.core.database.repositories.uow import PushNotificationConfigUoW
from src.core.exceptions import AdCPValidationError
from src.core.webhook_validator import webhook_url_for_log
from src.core.webhooks.registration import accept_push_notification_primitives

# The form's "no authentication" choice. Defined ONCE and handed to the template
# (see manage_webhooks) so the rendered <option value> and the comparison below
# cannot drift apart — the authenticated choices come from AuthenticationScheme
# for the same reason.
NO_AUTHENTICATION = "none"

logger = logging.getLogger(__name__)

# Create Blueprint (url_prefix is set during registration in app.py)
principals_bp = Blueprint("principals", __name__)


@principals_bp.route("/principals")
@require_tenant_access()
def list_principals(tenant_id):
    """List all principals (advertisers) for a tenant."""
    try:
        with get_db_session() as db_session:
            tenant = db_session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
            if not tenant:
                flash("Tenant not found", "error")
                return redirect(url_for("core.index"))

            principals = PrincipalRepository(db_session, tenant_id).list_all()

            # Convert to dict format for template
            principals_list = []
            for principal in principals:
                # Count media buys for this principal
                stmt = (
                    select(func.count())
                    .select_from(MediaBuy)
                    .filter_by(tenant_id=tenant_id, principal_id=principal.principal_id)
                )
                media_buy_count = db_session.scalar(stmt)

                # Handle both string (SQLite) and dict (PostgreSQL JSONB) formats
                mappings = principal.platform_mappings
                if mappings and isinstance(mappings, str):
                    mappings = json.loads(mappings)
                elif not mappings:
                    mappings = {}

                principal_dict = {
                    "principal_id": principal.principal_id,
                    "name": principal.name,
                    "token_prefix": principal.token_prefix,
                    "platform_mappings": mappings,
                    "media_buy_count": media_buy_count,
                    "created_at": principal.created_at,
                }
                principals_list.append(principal_dict)

            # Get dashboard metrics that the template expects
            dashboard_service = DashboardService(tenant_id)
            metrics = dashboard_service.get_dashboard_metrics()

            # Get recent media buys that the template expects
            recent_media_buys = dashboard_service.get_recent_media_buys(limit=10)

            # Get chart data that the template expects
            chart_data_dict = dashboard_service.get_chart_data()

            # Get tenant config for features
            from src.admin.utils import get_tenant_config_from_db

            config = get_tenant_config_from_db(tenant_id)
            features = config.get("features", {})

            # The template expects this to be under the 'advertisers' key
            # since principals are advertisers in the UI
            return render_template(
                "tenant_dashboard.html",
                tenant=tenant,
                tenant_id=tenant_id,
                advertisers=principals_list,
                # Template variables to match main dashboard
                active_campaigns=metrics.get("live_buys", 0),
                total_spend=metrics.get("total_revenue", 0),
                principals_count=metrics.get("total_advertisers", 0),
                products_count=metrics.get("products_count", 0),
                recent_buys=recent_media_buys,
                recent_media_buys=recent_media_buys,
                features=features,
                # Chart data
                revenue_data=json.dumps(metrics["revenue_data"]),
                chart_labels=chart_data_dict["labels"],
                chart_data=chart_data_dict["data"],
                # Metrics object
                metrics=metrics,
                show_advertisers_tab=True,
            )

    except Exception as e:
        logger.error(f"Error listing principals: {e}", exc_info=True)
        flash("Error loading advertisers", "error")
        return redirect(url_for("core.index"))


@principals_bp.route("/principals/create", methods=["GET", "POST"])
@require_tenant_access()
@log_admin_action(
    "create_principal",
    extract_details=lambda r, **kw: {"name": request.form.get("name")} if request.method == "POST" else {},
)
def create_principal(tenant_id):
    """Create a new principal (advertiser) for a tenant."""
    if request.method == "GET":
        # Get tenant info for GAM configuration
        with get_db_session() as db_session:
            tenant = db_session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
            if not tenant:
                flash("Tenant not found", "error")
                return redirect(url_for("core.index"))

            # Check if GAM is configured (uses centralized tenant.is_gam_tenant property)
            has_gam = tenant.is_gam_tenant

            return render_template(
                "create_principal.html",
                tenant_id=tenant_id,
                tenant_name=tenant.name,
                has_gam=has_gam,
            )

    # POST - Create the principal
    try:
        principal_name = request.form.get("name", "").strip()
        if not principal_name:
            flash("Principal name is required", "error")
            return redirect(request.url)

        principal_id = f"prin_{uuid.uuid4().hex[:8]}"

        # Build platform mappings
        platform_mappings = {}

        # GAM advertiser mapping
        gam_advertiser_id = request.form.get("gam_advertiser_id", "").strip()
        if gam_advertiser_id:
            # Validate it's numeric (GAM expects integer company IDs)
            try:
                int(gam_advertiser_id)
            except (ValueError, TypeError):
                flash(
                    f"GAM Advertiser ID must be numeric (got: '{gam_advertiser_id}'). "
                    "Please select a valid advertiser from the dropdown.",
                    "error",
                )
                return redirect(request.url)

            platform_mappings["google_ad_manager"] = {
                "advertiser_id": gam_advertiser_id,
                "enabled": True,
            }

        # Mock adapter mapping (for testing)
        if request.form.get("enable_mock"):
            platform_mappings["mock"] = {
                "advertiser_id": f"mock_{principal_id}",
                "enabled": True,
            }

        with get_db_session() as db_session:
            # No duplicate-name pre-check: advertiser names are human-readable
            # labels, not keys. AdCP 3.1.1 attaches uniqueness only to ids
            # (core/account.json: name is "Human-readable account name", e.g.
            # "Acme c/o Pinnacle"), and no index backs the tenant+name pair, so a
            # pre-check here read as a guarantee it could not hold under
            # concurrency. Identity is (tenant_id, principal_id).

            # The token is minted here and shown ONCE, in the flash below; the row keeps
            # its hash. An operator who loses it rotates it.
            principal, token = PrincipalRepository(db_session, tenant_id).issue(
                principal_id=principal_id,
                name=principal_name,
                platform_mappings=platform_mappings,  # JSONType handles serialization
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
            db_session.commit()

            flash(
                f"Advertiser '{principal_name}' created. Its API token, shown only now: {token}",
                "success",
            )
            return redirect(url_for("tenants.tenant_settings", tenant_id=tenant_id, section="advertisers"))

    except Exception as e:
        logger.error(f"Error creating principal: {e}", exc_info=True)
        flash("Error creating advertiser", "error")
        return redirect(request.url)


@principals_bp.route("/principals/<principal_id>/edit", methods=["GET", "POST"])
@require_tenant_access()
@log_admin_action(
    "edit_principal",
    extract_details=lambda r, **kw: {"principal_id": kw.get("principal_id")} if request.method == "POST" else {},
)
def edit_principal(tenant_id, principal_id):
    """Edit an existing principal - reuses create_principal.html template."""
    if request.method == "GET":
        with get_db_session() as db_session:
            tenant = db_session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
            if not tenant:
                flash("Tenant not found", "error")
                return redirect(url_for("core.index"))

            principal = PrincipalRepository(db_session, tenant_id).get(principal_id)
            if not principal:
                flash("Advertiser not found", "error")
                return redirect(url_for("tenants.dashboard", tenant_id=tenant_id))

            # Check if GAM is configured (uses centralized tenant.is_gam_tenant property)
            has_gam = tenant.is_gam_tenant

            # Extract existing GAM advertiser ID if present
            existing_gam_id = None
            if principal.platform_mappings:
                mappings = principal.platform_mappings if isinstance(principal.platform_mappings, dict) else {}
                gam_mapping = mappings.get("google_ad_manager", {})
                existing_gam_id = gam_mapping.get("advertiser_id")

            return render_template(
                "create_principal.html",
                tenant_id=tenant_id,
                tenant_name=tenant.name,
                has_gam=has_gam,
                edit_mode=True,
                principal=principal,
                existing_gam_id=existing_gam_id,
            )

    # POST - Update the principal
    try:
        with get_db_session() as db_session:
            principal = PrincipalRepository(db_session, tenant_id).get(principal_id)
            if not principal:
                flash("Advertiser not found", "error")
                return redirect(url_for("tenants.dashboard", tenant_id=tenant_id))

            # Update name if provided
            principal_name = request.form.get("name", "").strip()
            if principal_name:
                principal.name = principal_name

            # Build platform mappings from scratch (don't preserve old mappings)
            platform_mappings = {}

            # GAM advertiser mapping
            gam_advertiser_id = request.form.get("gam_advertiser_id", "").strip()
            if gam_advertiser_id:
                try:
                    int(gam_advertiser_id)
                except (ValueError, TypeError):
                    flash("GAM Advertiser ID must be numeric", "error")
                    return redirect(request.url)

                platform_mappings["google_ad_manager"] = {
                    "advertiser_id": gam_advertiser_id,
                    "enabled": True,
                }

            principal.platform_mappings = platform_mappings
            principal.updated_at = datetime.now(UTC)
            db_session.commit()

            flash(f"Advertiser '{principal.name}' updated successfully", "success")
            return redirect(url_for("tenants.tenant_settings", tenant_id=tenant_id, section="advertisers"))

    except Exception as e:
        logger.error(f"Error updating principal: {e}", exc_info=True)
        flash("Error updating advertiser", "error")
        return redirect(request.url)


@principals_bp.route("/principal/<principal_id>", methods=["GET"])
@require_tenant_access()
def get_principal(tenant_id, principal_id):
    """Get principal details including platform mappings (API endpoint)."""
    try:
        with get_db_session() as db_session:
            principal = PrincipalRepository(db_session, tenant_id).get(principal_id)

            if not principal:
                return jsonify({"error": "Principal not found"}), 404

            # Parse platform mappings (handle both string and dict formats)
            if principal.platform_mappings:
                if isinstance(principal.platform_mappings, str):
                    mappings = json.loads(principal.platform_mappings)
                else:
                    mappings = principal.platform_mappings
            else:
                mappings = {}

            return jsonify(
                {
                    "success": True,
                    "principal": {
                        "principal_id": principal.principal_id,
                        "name": principal.name,
                        "token_prefix": principal.token_prefix,
                        "platform_mappings": mappings,
                        "created_at": principal.created_at.isoformat() if principal.created_at else None,
                    },
                }
            )

    except Exception as e:
        logger.error(f"Error getting principal {principal_id}: {e}", exc_info=True)
        return jsonify({"error": f"Failed to get principal: {str(e)}"}), 500


@principals_bp.route("/principal/<principal_id>/rotate-token", methods=["POST"])
# Authorization OUTSIDE the audit decorator, per the convention in
# src/admin/utils/audit_decorator.py's module docstring. require_tenant_access RETURNS a
# redirect/401 rather than raising, and log_admin_action only records a failure when the
# wrapped function RAISES — so with the audit decorator outermost an unauthenticated POST
# wrote an audit row saying this rotation succeeded. Graded by
# tests/integration/test_audit_decorator_auth_order.py. The other 54 sites carrying the
# inverted order, and the ast-grep rule that would refuse it, are GH #2110.
@require_tenant_access()
@log_admin_action("rotate_principal_token")
def rotate_token(tenant_id, principal_id):
    """Replace the principal's API token. The new token is in the response, once.

    The stored hash is the only record of a token, so a lost token cannot be shown again;
    rotation is the recovery. The old token stops resolving on commit.
    """
    try:
        with get_db_session() as db_session:
            principal = PrincipalRepository(db_session, tenant_id).get(principal_id)
            if not principal:
                return jsonify({"error": "Principal not found"}), 404

            token = principal.rotate_token()
            principal.updated_at = datetime.now(UTC)
            db_session.commit()
            return jsonify({"success": True, "token": token, "token_prefix": principal.token_prefix})

    except Exception as e:
        logger.error(f"Error rotating token for principal {principal_id}: {e}", exc_info=True)
        return jsonify({"error": "Failed to rotate token"}), 500


@principals_bp.route("/principal/<principal_id>/update_mappings", methods=["POST"])
@log_admin_action("update_mappings")
@require_tenant_access()
def update_mappings(tenant_id, principal_id):
    """Update principal platform mappings."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "Invalid request"}), 400

        platform_mappings = data.get("platform_mappings", {})

        # Validate GAM advertiser_id if present
        if "google_ad_manager" in platform_mappings:
            gam_config = platform_mappings["google_ad_manager"]
            advertiser_id = gam_config.get("advertiser_id") or gam_config.get("company_id")

            if advertiser_id:
                # Validate it's numeric (GAM expects integer company IDs)
                try:
                    int(advertiser_id)
                except (ValueError, TypeError):
                    return (
                        jsonify(
                            {
                                "error": f"GAM Advertiser ID must be numeric (got: '{advertiser_id}'). "
                                "Please select a valid advertiser from the dropdown."
                            }
                        ),
                        400,
                    )

        with get_db_session() as db_session:
            principal = PrincipalRepository(db_session, tenant_id).get(principal_id)

            if not principal:
                return jsonify({"error": "Principal not found"}), 404

            # Update mappings - JSONType handles serialization
            principal.platform_mappings = platform_mappings
            principal.updated_at = datetime.now(UTC)
            db_session.commit()

            return jsonify(
                {
                    "success": True,
                    "message": "Platform mappings updated successfully",
                }
            )

    except Exception as e:
        logger.error(f"Error updating principal mappings: {e}", exc_info=True)
        return jsonify({"error": "Failed to update mappings"}), 500


@principals_bp.route("/api/gam/get-advertisers", methods=["POST"])
@log_admin_action("get_gam_advertisers")
@require_tenant_access()
def get_gam_advertisers(tenant_id):
    """Get list of advertisers from GAM for a tenant.

    Request body (JSON):
        search: Optional search query to filter by name (uses LIKE '%query%')
        limit: Maximum results to return (default: 500, max: 500)
        fetch_all: If true, fetches ALL advertisers with pagination (can be slow)

    Performance Notes:
        - For networks with 1000+ advertisers, use 'search' to filter results
        - fetch_all=true can take 5-10 seconds for networks with thousands of advertisers
        - Default behavior (limit=500) is fast but may not return all advertisers
    """
    try:
        from src.adapters.google_ad_manager import GoogleAdManager

        # Get request parameters
        data = request.get_json() or {}
        search_query = data.get("search")
        limit = data.get("limit", 500)
        fetch_all = data.get("fetch_all", False)

        # Get tenant configuration
        with get_db_session() as db_session:
            tenant = db_session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
            if not tenant:
                return jsonify({"error": "Tenant not found"}), 404

            # Check if GAM is configured (uses centralized tenant.is_gam_tenant property)
            gam_enabled = tenant.is_gam_tenant

            # Debug logging to help troubleshoot
            logger.info(
                f"GAM API detection for tenant {tenant_id}: "
                f"ad_server={tenant.ad_server}, "
                f"has_adapter_config={tenant.adapter_config is not None}, "
                f"adapter_type={tenant.adapter_config.adapter_type if tenant.adapter_config else None}, "
                f"gam_enabled={gam_enabled}, "
                f"search={search_query}, limit={limit}, fetch_all={fetch_all}"
            )

            if not gam_enabled:
                logger.warning(f"GAM not enabled for tenant {tenant_id}")
                return jsonify({"error": "Google Ad Manager not configured"}), 400

            # Initialize GAM adapter with adapter config
            try:
                # Import Principal model
                from src.core.schemas import Principal

                # Create a mock principal for GAM initialization
                # Need dummy advertiser_id for GAM adapter validation, even though get_advertisers() doesn't use it
                mock_principal = Principal(
                    principal_id="system",
                    name="System",
                    platform_mappings={
                        "google_ad_manager": {
                            "advertiser_id": "system_temp_advertiser_id",  # Dummy ID for validation only
                            "advertiser_name": "System (temp)",
                        }
                    },
                )

                # Build GAM config from AdapterConfig
                if not tenant.adapter_config or not tenant.adapter_config.gam_network_code:
                    return jsonify({"error": "GAM network code not configured for this tenant"}), 400

                # Use build_gam_config_from_adapter to handle both OAuth and service account
                from src.adapters.gam import build_gam_config_from_adapter

                gam_config = build_gam_config_from_adapter(tenant.adapter_config)

                adapter = GoogleAdManager(
                    config=gam_config,
                    principal=mock_principal,
                    network_code=tenant.adapter_config.gam_network_code,
                    advertiser_id=None,
                    trafficker_id=tenant.adapter_config.gam_trafficker_id,
                    tenant_id=tenant_id,
                )

                # Get advertisers (companies) from GAM with filtering support
                advertisers = adapter.orders_manager.get_advertisers(
                    search_query=search_query, limit=limit, fetch_all=fetch_all
                )

                return jsonify(
                    {
                        "success": True,
                        "advertisers": advertisers,
                        "count": len(advertisers),
                        "search": search_query,
                        "fetch_all": fetch_all,
                    }
                )

            except Exception as gam_error:
                logger.error(f"GAM API error: {gam_error}")
                return jsonify({"error": f"Failed to fetch advertisers: {str(gam_error)}"}), 500

    except Exception as e:
        logger.error(f"Error getting GAM advertisers: {e}", exc_info=True)
        return jsonify({"error": "Internal server error"}), 500


@principals_bp.route("/api/principal/<principal_id>/config", methods=["GET"])
@require_tenant_access()
def get_principal_config(tenant_id, principal_id):
    """Get principal configuration including platform mappings for testing UI."""
    try:
        with get_db_session() as db_session:
            principal = PrincipalRepository(db_session, tenant_id).get(principal_id)

            if not principal:
                return jsonify({"error": "Principal not found"}), 404

            # Parse platform mappings
            platform_mappings = (
                json.loads(principal.platform_mappings)
                if isinstance(principal.platform_mappings, str)
                else principal.platform_mappings
            )

            return jsonify(
                {
                    "principal_id": principal.principal_id,
                    "name": principal.name,
                    "platform_mappings": platform_mappings,
                }
            )

    except Exception as e:
        logger.error(f"Error getting principal config: {e}", exc_info=True)
        return jsonify({"error": "Internal server error"}), 500


@principals_bp.route("/api/principal/<principal_id>/testing-config", methods=["POST"])
@log_admin_action("save_testing_config")
@require_tenant_access()
def save_testing_config(tenant_id, principal_id):
    """Save testing configuration (HITL settings) for a mock adapter principal."""
    try:
        data = request.get_json()
        if not data or "hitl_config" not in data:
            return jsonify({"error": "Missing hitl_config in request"}), 400

        hitl_config = data["hitl_config"]

        with get_db_session() as db_session:
            principal = PrincipalRepository(db_session, tenant_id).get(principal_id)

            if not principal:
                return jsonify({"error": "Principal not found"}), 404

            # Parse existing platform mappings
            platform_mappings = (
                json.loads(principal.platform_mappings)
                if isinstance(principal.platform_mappings, str)
                else principal.platform_mappings or {}
            )

            # Ensure mock adapter exists
            if "mock" not in platform_mappings:
                platform_mappings["mock"] = {"advertiser_id": f"mock_{principal_id}", "enabled": True}

            # Update hitl_config
            platform_mappings["mock"]["hitl_config"] = hitl_config

            # Save back to database - JSONType handles serialization
            principal.platform_mappings = platform_mappings
            principal.updated_at = datetime.now(UTC)
            db_session.commit()

            logger.info(f"Updated testing config for principal {principal_id} in tenant {tenant_id}")

            return jsonify({"success": True, "message": "Testing configuration saved successfully"})

    except Exception as e:
        logger.error(f"Error saving testing config: {e}", exc_info=True)
        return jsonify({"error": "Failed to save testing configuration"}), 500


@principals_bp.route("/principals/<principal_id>/webhooks", methods=["GET"])
@require_tenant_access()
def manage_webhooks(tenant_id, principal_id):
    """Manage webhook configurations for a principal."""
    try:
        with get_db_session() as db_session:
            principal = PrincipalRepository(db_session, tenant_id).get(principal_id)
            if not principal:
                flash("Principal not found", "error")
                return redirect(url_for("principals.list_principals", tenant_id=tenant_id))

            # Get all webhooks for this principal
            webhooks = db_session.scalars(
                select(PushNotificationConfig).filter_by(tenant_id=tenant_id, principal_id=principal_id)
            ).all()

            return render_template(
                "webhook_management.html",
                tenant_id=tenant_id,
                principal=principal,
                webhooks=webhooks,
                # The SPELLING comes from the pinned enum, never from a literal:
                # hardcoding it is how "hmac_sha256" — which nothing in src/
                # compares against — became the value this form posted. Only
                # HMAC-SHA256 is offered: Bearer is a member of the enum but this
                # form has no field to collect a bearer token, and adding one is a
                # feature, not part of fixing a route that never persisted a row.
                auth_schemes=[AuthenticationScheme.HMAC_SHA256],
                no_authentication=NO_AUTHENTICATION,
            )

    except Exception as e:
        logger.error(f"Error loading webhook management: {e}", exc_info=True)
        flash(f"Error loading webhooks: {str(e)}", "error")
        return redirect(url_for("principals.list_principals", tenant_id=tenant_id))


def _redirect_to_webhooks(tenant_id, principal_id):
    return redirect(url_for("principals.manage_webhooks", tenant_id=tenant_id, principal_id=principal_id))


@principals_bp.route("/principals/<principal_id>/webhooks/register", methods=["POST"])
@log_admin_action("register_webhook")
@require_tenant_access()
def register_webhook(tenant_id, principal_id):
    """Register a new webhook for a principal."""
    try:
        auth_type = request.form.get("auth_type", "none")

        # ONE gate, not two. This route used to run redirect_if_url_blocked here
        # AND accept_push_notification_primitives below, so a webhook URL was
        # judged twice by two different verdicts -- and the two did not agree:
        # the first resolves DNS (via resolve_for_dial), the second does not.
        #
        # DELIBERATE TRADE, and it is a real one: a hostname that RESOLVES into a
        # private range but is not a literal IP was refused here and is now
        # accepted at registration. It is still refused before any bytes leave --
        # the egress seam resolves again and IP-pins at DIAL time, which is the
        # resolution that actually governs the connection, and the one a
        # registration-time check cannot make binding anyway (DNS can change
        # between the two moments). What this removes is a second, weaker,
        # non-binding answer that made the admin form disagree with every
        # protocol surface about the same URL.
        # The scheme is whatever the form posted, and the form's option values are
        # rendered from AuthenticationScheme itself (webhook_management.html), so it
        # is already the pinned spelling. Nothing is translated here on purpose: a
        # route-side lookup table is exactly the drift that put a fifth spelling in
        # the database once already (GH #1894).
        scheme = None if auth_type == NO_AUTHENTICATION else auth_type
        credentials = request.form.get("hmac_secret") if scheme else None

        # The gate produces the VALUE; the repository takes the value. This route
        # builds no ORM model: a registration reaches the database only as a
        # ValidatedWebhookRegistration, so a config that skipped the ingest
        # preconditions cannot be written from here (Epic D). It used to construct
        # PushNotificationConfig(config_id=/auth_type=/auth_config=) — none of them
        # columns — so every registration raised TypeError and the broad except
        # below rendered it as a validation-looking flash.
        #
        # This gate also SUBSUMES the hand-written form checks that used to stand
        # here (URL present, HMAC secret present, secret at least 32 characters):
        # the pinned schema states each of them once
        # (core/push-notification-config.json requires `credentials` whenever an
        # `authentication` block is present, with minLength 32), so restating them
        # in this route would only be somewhere for the two to drift apart.
        registration = accept_push_notification_primitives(
            request.form.get("url"),
            scheme,
            credentials,
            field_prefix="webhook",
        )

        # The PRIMARY KEY decides the race, not a pre-check SELECT. register_admin_webhook
        # derives a deterministic id from (tenant, principal, url) and resolves a lost
        # insert race into the same polite answer the pre-check would have given, so two
        # concurrent registrations of one URL cannot leave two active rows behind (which
        # would make the sender deliver twice). Because the id is derived from the URL, a
        # SOFT-DELETED row for that URL is found by id and REACTIVATED rather than
        # duplicated, and a LIVE row is returned untouched -- its stored credential is
        # never silently rotated by a re-submit.
        #
        # The RECEIPT travels whole; this route destructures nothing. The repository
        # projects it through ValidatedWebhookRegistration.to_columns(), so the stored
        # url is str(config.url) off a pydantic AnyUrl -- normalized (host lowercased,
        # trailing slash added, default port stripped) -- and the duplicate lookup and
        # the derived id are keyed on that same normalized string the write uses. A
        # route that re-flattened the value into url=/authentication_type=/
        # authentication_token= kwargs would be re-deriving that key here, where a
        # non-canonical spelling inserts a second active row for the same URL, and
        # would put a credential read back at a call site
        # (tests/unit/test_architecture_no_inline_webhook_auth_resolution.py).
        #
        # to_columns() puts the credential in authentication_token. webhook_secret is
        # passed None EXPLICITLY -- the repository's preserve-if-not-passed sentinel
        # means omitting it would leave a pre-existing value standing -- because the
        # egress seam signs from (authentication_type, authentication_token) only:
        # webhook_secret has no reader left in src/, so a secret written there would be
        # a registration the operator is told is signed and that goes out unsigned
        # (GH #1894).
        with PushNotificationConfigUoW(tenant_id) as uow:
            assert uow.push_notification_configs is not None
            existing = uow.push_notification_configs.register_admin_webhook(
                registration,
                principal_id=principal_id,
                webhook_secret=None,
            )

        if existing is not None:
            flash("Webhook URL already registered for this principal", "warning")
            return _redirect_to_webhooks(tenant_id, principal_id)

        # registration.url through webhook_url_for_log, not the form value:
        # two separate rules, both already written down elsewhere in the tree.
        # (1) What is STORED is the normalized AnyUrl, so the raw string is
        # not what an operator would be reading back. The form value has no
        # name in this handler at all now -- it is consumed by the gate.
        # (2) A webhook URL is rendered into a log record in exactly ONE way,
        # by webhook_url_for_log (scheme+host+path, never credentials). The
        # sibling registration path already logs this same attribute of this
        # same type that way (media_buy_create.py), and the type uses it in its
        # own __repr__. AnyUrl normalization strips a newline but PRESERVES
        # ?token=... and user:pw@ -- so without this call an operator-registered
        # webhook carrying a bearer token in its query string would write that
        # token into the admin log at INFO.
        logger.info(
            "Registered webhook %s for principal %r in tenant %r",
            webhook_url_for_log(registration.url),
            principal_id,
            tenant_id,
        )
        flash("Webhook registered successfully", "success")
        return _redirect_to_webhooks(tenant_id, principal_id)

    # Only the gate's own refusals are operator-facing. Anything else is a defect
    # in this handler and must reach the logs as a 500 rather than being flashed:
    # a broad `except Exception` here is what let a route that never persisted a
    # single row look like a validation problem for months.
    except AdCPValidationError as e:
        logger.warning("Rejected webhook registration for principal %r: %s", principal_id, e)
        # The operator gets a flash, not a 500 — so this returns normally, which
        # means the audit decorator would otherwise record the refusal as a
        # SUCCESSFUL admin action. Say the action failed explicitly.
        record_admin_action_failure(e)
        flash(f"Error registering webhook: {str(e)}", "error")
        return _redirect_to_webhooks(tenant_id, principal_id)


@principals_bp.route("/principals/<principal_id>/webhooks/<config_id>/delete", methods=["POST"])
@log_admin_action("delete_webhook")
@require_tenant_access()
def delete_webhook(tenant_id, principal_id, config_id):
    """Delete a webhook configuration.

    This route never worked. It filtered on ``config_id``, which is not a column
    on ``PushNotificationConfig`` -- the primary key is ``id`` -- so every call
    raised ``InvalidRequestError``, and a broad ``except`` rendered that
    programming error as an operator flash. The template passes ``webhook.id``,
    so it was dead for every row, not only soft-deleted ones.

    The lookup goes through the repository, which is what removes the
    opportunity to hand-write a filter against a column that does not exist.
    ``PushNotificationConfigRepository.delete`` IS that lookup
    (``get_by_id(..., active_only=False)``) plus the delete, so this route holds
    no session of its own and no broad ``except``: a defect here reaches the
    logs as a real 500 instead of being reported to the operator as though it
    were a condition of their request.
    """
    with PushNotificationConfigUoW(tenant_id) as uow:
        assert uow.push_notification_configs is not None
        deleted = uow.push_notification_configs.delete(config_id, principal_id)

    if not deleted:
        flash("Webhook not found", "error")
        return _redirect_to_webhooks(tenant_id, principal_id)

    logger.info("Deleted webhook %r for principal %r in tenant %r", config_id, principal_id, tenant_id)
    flash("Webhook deleted successfully", "success")
    return _redirect_to_webhooks(tenant_id, principal_id)


@principals_bp.route("/principals/<principal_id>/webhooks/<config_id>/toggle", methods=["POST"])
@log_admin_action("toggle_webhook")
@require_tenant_access()
def toggle_webhook(tenant_id, principal_id, config_id):
    """Toggle webhook active status.

    Carried the same never-working ``config_id`` filter as :func:`delete_webhook`
    and the same broad ``except`` that turned the resulting programming error
    into a JSON 500. Both are gone: the lookup is the repository's
    (``toggle_active`` finds the row by primary key regardless of its current
    ``is_active``), and a defect in this handler now reaches the logs as a real
    500 instead of being reported to the operator as though it were a condition
    of their request.

    ``toggle_active`` returns the NEW value rather than the row, so the response
    is built from a plain bool and never reads an attribute off an instance whose
    session has already closed.
    """
    with PushNotificationConfigUoW(tenant_id) as uow:
        assert uow.push_notification_configs is not None
        new_state = uow.push_notification_configs.toggle_active(config_id, principal_id)

    if new_state is None:
        return jsonify({"error": "Webhook not found"}), 404

    logger.info(
        "Toggled webhook %r to %s for principal %r",
        config_id,
        "active" if new_state else "inactive",
        principal_id,
    )
    return jsonify({"success": True, "is_active": new_state})


@principals_bp.route("/principals/<principal_id>/delete", methods=["DELETE", "POST"])
@log_admin_action("delete_principal")
@require_tenant_access()
def delete_principal(tenant_id, principal_id):
    """Delete a principal (advertiser)."""
    try:
        with get_db_session() as db_session:
            # Find the principal
            principal = PrincipalRepository(db_session, tenant_id).get(principal_id)

            if not principal:
                return jsonify({"error": "Principal not found"}), 404

            principal_name = principal.name

            # Delete the principal (cascades to related records)
            db_session.delete(principal)
            db_session.commit()

            logger.info(f"Deleted principal {principal_id} ({principal_name}) from tenant {tenant_id}")

            return jsonify({"success": True, "message": f"Principal '{principal_name}' deleted successfully"})

    except Exception as e:
        logger.error(f"Error deleting principal: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500
