"""Inventory and orders management blueprint."""

import json
import logging

from flask import Blueprint, jsonify, render_template, request, session
from sqlalchemy import String, func, or_, select

from src.admin.utils import get_tenant_config_from_db, require_auth, require_tenant_access  # type: ignore[attr-defined]
from src.admin.utils.audit_decorator import log_admin_action
from src.core.database.database_session import get_db_session
from src.core.database.models import GAMInventory, GAMOrder, MediaBuy, Principal, Tenant

logger = logging.getLogger(__name__)

# Create blueprint
inventory_bp = Blueprint("inventory", __name__)


@inventory_bp.route("/tenant/<tenant_id>/targeting")
@require_tenant_access()
def targeting_browser(tenant_id):
    """Display targeting browser page."""

    with get_db_session() as db_session:
        tenant = db_session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
        row = (tenant.tenant_id, tenant.name) if tenant else None
        if not row:
            return "Tenant not found", 404

    tenant = {"tenant_id": row[0], "name": row[1]}

    return render_template(
        "targeting_browser.html",
        tenant=tenant,
        tenant_id=tenant_id,
        tenant_name=row[1],
    )


@inventory_bp.route("/api/tenant/<tenant_id>/targeting/all", methods=["GET"])
@require_tenant_access(api_mode=True)
def get_targeting_data(tenant_id):
    """Get all targeting data (custom targeting keys, audience segments, labels) from database."""
    logger.info(f"Targeting data request for tenant: {tenant_id}")
    try:
        with get_db_session() as db_session:
            from src.core.database.models import GAMInventory

            # Query custom targeting keys
            custom_keys_stmt = select(GAMInventory).where(
                GAMInventory.tenant_id == tenant_id,
                GAMInventory.inventory_type == "custom_targeting_key",
            )
            custom_keys_rows = db_session.scalars(custom_keys_stmt).all()

            # Query audience segments
            audience_segments_stmt = select(GAMInventory).where(
                GAMInventory.tenant_id == tenant_id,
                GAMInventory.inventory_type == "audience_segment",
            )
            audience_segments_rows = db_session.scalars(audience_segments_stmt).all()

            # Query labels
            labels_stmt = select(GAMInventory).where(
                GAMInventory.tenant_id == tenant_id,
                GAMInventory.inventory_type == "label",
            )
            labels_rows = db_session.scalars(labels_stmt).all()

            # Get last sync time from most recent inventory item
            last_sync_stmt = (
                select(GAMInventory.last_synced)
                .where(GAMInventory.tenant_id == tenant_id)
                .order_by(GAMInventory.last_synced.desc())
                .limit(1)
            )
            last_sync = db_session.scalar(last_sync_stmt)

            # Transform to frontend format
            custom_keys = []
            for row in custom_keys_rows:
                metadata = row.inventory_metadata or {}
                # Ensure metadata is a dict (handle both None and non-dict cases)
                if not isinstance(metadata, dict):
                    logger.warning(f"Invalid metadata for custom_targeting_key {row.inventory_id}: {type(metadata)}")
                    metadata = {}

                custom_keys.append(
                    {
                        "id": row.inventory_id,
                        "name": row.name,
                        "display_name": metadata.get("display_name", row.name),  # Fallback to name
                        "status": row.status or "UNKNOWN",  # Handle None status
                        "type": metadata.get("type", "UNKNOWN"),  # Provide default type
                    }
                )

            audiences = []
            for row in audience_segments_rows:
                metadata = row.inventory_metadata or {}
                # Ensure metadata is a dict
                if not isinstance(metadata, dict):
                    logger.warning(f"Invalid metadata for audience_segment {row.inventory_id}: {type(metadata)}")
                    metadata = {}

                audiences.append(
                    {
                        "id": row.inventory_id,
                        "name": row.name,
                        "description": metadata.get("description"),
                        "status": row.status or "UNKNOWN",
                        "size": metadata.get("size"),
                    }
                )

            labels = []
            for row in labels_rows:
                metadata = row.inventory_metadata or {}
                # Ensure metadata is a dict
                if not isinstance(metadata, dict):
                    logger.warning(f"Invalid metadata for label {row.inventory_id}: {type(metadata)}")
                    metadata = {}

                labels.append(
                    {
                        "id": row.inventory_id,
                        "name": row.name,
                        "description": metadata.get("description"),
                        "is_active": row.status == "ACTIVE",
                    }
                )

            return jsonify(
                {
                    "customKeys": custom_keys,
                    "audiences": audiences,
                    "labels": labels,
                    "last_sync": last_sync.isoformat() if last_sync else None,
                }
            )

    except Exception as e:
        logger.error(f"Error fetching targeting data for tenant {tenant_id}: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@inventory_bp.route("/api/tenant/<tenant_id>/targeting/values/<key_id>", methods=["GET"])
@require_tenant_access(api_mode=True)
def get_targeting_values(tenant_id, key_id):
    """Get custom targeting values for a specific key by querying GAM in real-time.

    Since inventory sync doesn't fetch values by default (for performance),
    this endpoint queries GAM directly to get fresh values on-demand.

    Args:
        tenant_id: Tenant identifier
        key_id: Custom targeting key ID

    Returns:
        JSON array of custom targeting values with their metadata
    """
    try:
        with get_db_session() as db_session:
            from src.core.database.models import GAMInventory, Tenant

            # Get tenant and verify it has GAM configured
            tenant = db_session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
            if not tenant:
                return jsonify({"error": "Tenant not found"}), 404

            # Verify key exists in our database
            key_stmt = select(GAMInventory).where(
                GAMInventory.tenant_id == tenant_id,
                GAMInventory.inventory_type == "custom_targeting_key",
                GAMInventory.inventory_id == key_id,
            )
            key_row = db_session.scalars(key_stmt).first()

            if not key_row:
                return jsonify({"error": "Custom targeting key not found"}), 404

            # Query GAM in real-time for values
            adapter_config = tenant.adapter_config
            logger.info(f"Fetching targeting values for tenant={tenant_id}, key_id={key_id}")
            logger.debug(f"Adapter config exists: {adapter_config is not None}")
            if adapter_config:
                logger.debug(
                    f"Network code: {adapter_config.gam_network_code}, "
                    f"auth_method: {adapter_config.gam_auth_method}, "
                    f"has refresh token: {bool(adapter_config.gam_refresh_token)}, "
                    f"has service account: {bool(adapter_config.gam_service_account_json)}"
                )

            if not adapter_config:
                logger.error(f"No adapter configured for tenant {tenant_id}")
                return jsonify({"error": "No adapter configured for this tenant"}), 400
            if not adapter_config.gam_network_code:
                logger.error(f"GAM network code not configured for tenant {tenant_id}")
                return jsonify({"error": "GAM network code not configured"}), 400

            # Check for EITHER OAuth or Service Account authentication
            has_oauth = bool(adapter_config.gam_refresh_token)
            has_service_account = bool(adapter_config.gam_service_account_json)

            if not has_oauth and not has_service_account:
                logger.error(f"No GAM authentication configured for tenant {tenant_id}")
                return (
                    jsonify({"error": "GAM authentication not configured. Please connect to GAM in tenant settings."}),
                    400,
                )

            # Initialize GAM adapter to query values
            import os
            import tempfile

            from google.oauth2 import service_account as google_service_account
            from googleads import ad_manager, oauth2

            from src.adapters.gam_inventory_discovery import GAMInventoryDiscovery

            # Create authentication client based on configured method
            if has_service_account:
                logger.debug(f"Using service account authentication for tenant {tenant_id}")
                # Write service account JSON to temp file
                service_account_json = adapter_config.gam_service_account_json
                with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
                    f.write(service_account_json)
                    temp_key_path = f.name

                try:
                    # Create service account credentials
                    credentials = google_service_account.Credentials.from_service_account_file(
                        temp_key_path, scopes=["https://www.googleapis.com/auth/dfp"]
                    )
                    # Wrap in GoogleCredentialsClient for AdManagerClient compatibility
                    oauth2_client = oauth2.GoogleCredentialsClient(credentials)
                    # Create Ad Manager client with service account
                    gam_ad_manager_client = ad_manager.AdManagerClient(
                        oauth2_client, "AdCP Sales Agent", network_code=adapter_config.gam_network_code
                    )
                finally:
                    # Clean up temp file
                    import os as os_module

                    try:
                        os_module.unlink(temp_key_path)
                    except Exception as e:
                        logger.warning(f"Failed to delete temp service account file: {e}")
            else:
                logger.debug(f"Using OAuth authentication for tenant {tenant_id}")
                # Create OAuth client
                oauth2_client = oauth2.GoogleRefreshTokenClient(
                    client_id=os.environ.get("GAM_OAUTH_CLIENT_ID"),
                    client_secret=os.environ.get("GAM_OAUTH_CLIENT_SECRET"),
                    refresh_token=adapter_config.gam_refresh_token,
                )
                # Create Ad Manager client
                gam_ad_manager_client = ad_manager.AdManagerClient(
                    oauth2_client, "AdCP Sales Agent", network_code=adapter_config.gam_network_code
                )

            # Create inventory discovery instance
            gam_client = GAMInventoryDiscovery(client=gam_ad_manager_client, tenant_id=tenant_id)

            # Fetch values from GAM (max 1000 to avoid timeout)
            gam_values = gam_client.discover_custom_targeting_values_for_key(key_id, max_values=1000)

            # Transform to frontend format
            values = []
            for gam_value in gam_values:
                values.append(
                    {
                        "id": gam_value.id,
                        "name": gam_value.name,
                        "display_name": gam_value.display_name or gam_value.name,
                        "match_type": gam_value.match_type or "EXACT",
                        "status": gam_value.status or "ACTIVE",
                        "key_id": key_id,
                        "key_name": key_row.name,
                    }
                )

            return jsonify({"values": values, "count": len(values)})

    except Exception as e:
        logger.error(f"Error fetching targeting values for key {key_id}: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@inventory_bp.route("/tenant/<tenant_id>/inventory")
@require_tenant_access()
def inventory_browser(tenant_id):
    """Display inventory browser page."""

    with get_db_session() as db_session:
        tenant = db_session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
        row = (tenant.tenant_id, tenant.name) if tenant else None
        if not row:
            return "Tenant not found", 404

        # Check adapter type - inventory browser is only for GAM
        adapter_type = tenant.ad_server or "mock"
        if adapter_type != "google_ad_manager":
            from flask import flash, redirect, url_for

            flash(
                f"Inventory browser is only available for Google Ad Manager. "
                f"Your tenant is using the '{adapter_type}' adapter which does not require inventory sync.",
                "info",
            )
            return redirect(url_for("tenants.tenant_settings", tenant_id=tenant_id))

    tenant = {"tenant_id": row[0], "name": row[1]}

    # Get inventory type from query param
    inventory_type = request.args.get("type", "all")

    return render_template(
        "inventory_browser.html",
        tenant=tenant,
        tenant_id=tenant_id,
        tenant_name=row[1],
        inventory_type=inventory_type,
    )


@inventory_bp.route("/tenant/<tenant_id>/orders")
@require_auth()
def orders_browser(tenant_id):
    """Display GAM orders browser page."""
    # Check access
    if session.get("role") != "super_admin" and session.get("tenant_id") != tenant_id:
        return "Access denied", 403

    with get_db_session() as db_session:
        tenant = db_session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
        if not tenant:
            return "Tenant not found", 404

        # Get GAM orders from database
        stmt = select(GAMOrder).filter_by(tenant_id=tenant_id).order_by(GAMOrder.updated_at.desc())
        orders = db_session.scalars(stmt).all()

        # Calculate summary stats
        total_orders = len(orders)
        active_orders = sum(1 for o in orders if o.status == "ACTIVE")

        # Get total revenue from media buys
        stmt = select(func.sum(MediaBuy.budget)).filter_by(tenant_id=tenant_id)
        total_revenue = db_session.scalar(stmt) or 0

        return render_template(
            "orders_browser.html",
            tenant=tenant,
            tenant_id=tenant_id,
            orders=orders,
            total_orders=total_orders,
            active_orders=active_orders,
            total_revenue=total_revenue,
        )


@inventory_bp.route("/api/tenant/<tenant_id>/sync/orders", methods=["POST"])
@log_admin_action("sync_orders")
@require_tenant_access(api_mode=True)
def sync_orders(tenant_id):
    """Sync GAM orders for a tenant."""
    try:
        with get_db_session() as db_session:
            tenant = db_session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()

            if not tenant:
                return jsonify({"error": "Tenant not found"}), 404

            # Get GAM configuration from adapter_config
            adapter_config = tenant.adapter_config

            if not adapter_config or not adapter_config.gam_network_code or not adapter_config.gam_refresh_token:
                return (
                    jsonify(
                        {
                            "error": "Please connect your GAM account before trying to sync inventory. Go to Ad Server settings to configure GAM."
                        }
                    ),
                    400,
                )

            # Import GAM sync functionality
            from src.adapters.gam_order_sync import sync_gam_orders

            # Perform sync
            result = sync_gam_orders(
                tenant_id=tenant_id,
                network_code=adapter_config.gam_network_code,
                refresh_token=adapter_config.gam_refresh_token,
            )

            return jsonify(result)

    except Exception as e:
        logger.error(f"Error syncing orders: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@inventory_bp.route("/api/tenant/<tenant_id>/orders", methods=["GET"])
@require_tenant_access(api_mode=True)
def get_orders(tenant_id):
    """Get orders for a tenant."""
    try:
        with get_db_session() as db_session:
            # Get filter parameters
            status = request.args.get("status")
            advertiser = request.args.get("advertiser")

            # Build query
            stmt = select(GAMOrder).filter_by(tenant_id=tenant_id)

            if status:
                stmt = stmt.filter_by(status=status)
            if advertiser:
                stmt = stmt.filter_by(advertiser_name=advertiser)

            # Get orders
            orders = db_session.scalars(stmt.order_by(GAMOrder.updated_at.desc())).all()

            # Convert to JSON
            orders_data = []
            for order in orders:
                orders_data.append(
                    {
                        "order_id": order.order_id,
                        "name": order.name,
                        "status": order.status,
                        "advertiser_name": order.advertiser_name,
                        "trafficker_name": order.trafficker_name,
                        "total_impressions_delivered": order.total_impressions_delivered,
                        "total_clicks_delivered": order.total_clicks_delivered,
                        "total_ctr": order.total_ctr,
                        "start_date": order.start_date.isoformat() if order.start_date else None,
                        "end_date": order.end_date.isoformat() if order.end_date else None,
                        "updated_at": order.updated_at.isoformat() if order.updated_at else None,
                    }
                )

            return jsonify(
                {
                    "orders": orders_data,
                    "total": len(orders_data),
                }
            )

    except Exception as e:
        logger.error(f"Error getting orders: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@inventory_bp.route("/api/tenant/<tenant_id>/orders/<order_id>", methods=["GET"])
@require_tenant_access(api_mode=True)
def get_order_details(tenant_id, order_id):
    """Get details for a specific order."""
    try:
        with get_db_session() as db_session:
            order = db_session.scalars(select(GAMOrder).filter_by(tenant_id=tenant_id, order_id=order_id)).first()

            if not order:
                return jsonify({"error": "Order not found"}), 404

            # Get line items count (would need GAMLineItem model)
            # stmt = select(GAMLineItem).filter_by(
            #     tenant_id=tenant_id,
            #     order_id=order_id
            # )
            # line_items_count = db_session.scalar(select(func.count()).select_from(stmt.subquery()))

            return jsonify(
                {
                    "order": {
                        "order_id": order.order_id,
                        "name": order.name,
                        "status": order.status,
                        "advertiser_id": order.advertiser_id,
                        "advertiser_name": order.advertiser_name,
                        "trafficker_id": order.trafficker_id,
                        "trafficker_name": order.trafficker_name,
                        "salesperson_name": order.salesperson_name,
                        "total_impressions_delivered": order.total_impressions_delivered,
                        "total_clicks_delivered": order.total_clicks_delivered,
                        "total_ctr": order.total_ctr,
                        "start_date": order.start_date.isoformat() if order.start_date else None,
                        "end_date": order.end_date.isoformat() if order.end_date else None,
                        "created_at": order.created_at.isoformat() if order.created_at else None,
                        "updated_at": order.updated_at.isoformat() if order.updated_at else None,
                        # "line_items_count": line_items_count,
                    }
                }
            )

    except Exception as e:
        logger.error(f"Error getting order details: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@inventory_bp.route("/tenant/<tenant_id>/check-inventory-sync")
@require_auth()
def check_inventory_sync(tenant_id):
    """Check if GAM inventory has been synced for this tenant."""
    # Check access
    if session.get("role") != "super_admin" and session.get("tenant_id") != tenant_id:
        return jsonify({"error": "Access denied"}), 403

    try:
        with get_db_session() as db_session:
            # Count inventory items
            inventory_count = db_session.scalar(
                select(func.count()).select_from(GAMInventory).filter_by(tenant_id=tenant_id)
            )

            has_inventory = inventory_count > 0

            # Get last sync time if available
            last_sync = None
            if has_inventory:
                stmt = (
                    select(GAMInventory)
                    .filter(GAMInventory.tenant_id == tenant_id)
                    .order_by(GAMInventory.created_at.desc())
                )
                latest = db_session.scalars(stmt).first()
                if latest and latest.created_at:
                    last_sync = latest.created_at.isoformat()

            return jsonify(
                {
                    "has_inventory": has_inventory,
                    "inventory_count": inventory_count,
                    "last_sync": last_sync,
                }
            )

    except Exception as e:
        logger.error(f"Error checking inventory sync: {e}")
        return jsonify({"error": str(e)}), 500


@inventory_bp.route("/tenant/<tenant_id>/analyze-ad-server")
@require_auth()
def analyze_ad_server_inventory(tenant_id):
    """Analyze ad server to discover audiences, formats, and placements."""
    # Check access
    if session.get("role") == "viewer":
        return jsonify({"error": "Access denied"}), 403

    if session.get("role") == "tenant_admin" and session.get("tenant_id") != tenant_id:
        return jsonify({"error": "Access denied"}), 403

    try:
        # Get tenant config to determine adapter
        config = get_tenant_config_from_db(tenant_id)
        if not config:
            return jsonify({"error": "Tenant not found"}), 404

        # Find enabled adapter from database
        with get_db_session() as db_session:
            tenant = db_session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()

            adapter_type = None
            adapter_config = {}

            # Check database for adapter configuration
            if tenant and tenant.ad_server:
                adapter_type = tenant.ad_server
            elif tenant and tenant.adapter_config and tenant.adapter_config.adapter_type:
                adapter_type = tenant.adapter_config.adapter_type

        if not adapter_type:
            # Return mock data if no adapter configured
            return jsonify(
                {
                    "audiences": [
                        {
                            "id": "tech_enthusiasts",
                            "name": "Tech Enthusiasts",
                            "size": 1200000,
                        },
                        {"id": "sports_fans", "name": "Sports Fans", "size": 800000},
                    ],
                    "formats": [],
                    "placements": [
                        {
                            "id": "homepage_hero",
                            "name": "Homepage Hero",
                            "sizes": ["970x250", "728x90"],
                        }
                    ],
                }
            )

        # Get a principal for API calls
        with get_db_session() as db_session:
            principal_obj = db_session.scalars(select(Principal).filter_by(tenant_id=tenant_id)).first()

            if not principal_obj:
                return jsonify({"error": "No principal found for tenant"}), 404

            # Create principal object
            from src.core.schemas import Principal as PrincipalSchema

            # Handle both string (SQLite) and dict (PostgreSQL JSONB) formats
            mappings = principal_obj.platform_mappings
            if mappings and isinstance(mappings, str):
                mappings = json.loads(mappings)
            elif not mappings:
                mappings = {}
            principal = PrincipalSchema(
                tenant_id=tenant_id,
                principal_id=principal_obj.principal_id,
                name=principal_obj.name,
                access_token=principal_obj.access_token,
                platform_mappings=mappings,
            )

        # TODO: Get adapter instance and call actual discovery methods
        # For now, return mock analysis data
        # from src.adapters import get_adapter
        # adapter = get_adapter(adapter_type, config, principal)

        # Mock analysis (real adapters would implement actual discovery)
        analysis = {
            "audiences": [
                {"id": "auto_intenders", "name": "Auto Intenders", "size": 500000},
                {"id": "travel_enthusiasts", "name": "Travel Enthusiasts", "size": 750000},
            ],
            "formats": [
                {"id": "display_728x90", "name": "Leaderboard", "dimensions": "728x90"},
                {"id": "display_300x250", "name": "Medium Rectangle", "dimensions": "300x250"},
            ],
            "placements": [
                {"id": "homepage_top", "name": "Homepage Top", "formats": ["display_728x90"]},
                {"id": "article_sidebar", "name": "Article Sidebar", "formats": ["display_300x250"]},
            ],
        }

        return jsonify(analysis)

    except Exception as e:
        logger.error(f"Error analyzing ad server: {e}")
        return jsonify({"error": str(e)}), 500


@inventory_bp.route("/api/tenant/<tenant_id>/inventory/sync", methods=["POST"])
@log_admin_action("sync_inventory")
@require_tenant_access(api_mode=True)
def sync_inventory(tenant_id):
    """Start inventory sync in background (non-blocking).

    Returns immediately with sync_id for tracking progress.

    Request body (optional):
    {
        "types": ["ad_units", "placements", "labels", "custom_targeting", "audience_segments"],
        "custom_targeting_limit": 1000,  // Optional: limit number of custom targeting values
        "audience_segment_limit": 500    // Optional: limit number of audience segments
    }

    If no body provided, syncs everything (backwards compatible).

    Returns:
        202 Accepted with sync_id for tracking
        400 Bad Request if GAM not configured or sync already running
        404 Not Found if tenant doesn't exist
    """
    try:
        from src.services.background_sync_service import start_inventory_sync_background

        # Validate tenant exists
        with get_db_session() as db_session:
            tenant = db_session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
            if not tenant:
                return jsonify({"error": "Tenant not found"}), 404

            # Check adapter type - inventory sync is only for GAM
            adapter_type = tenant.ad_server or "mock"
            if adapter_type != "google_ad_manager":
                return (
                    jsonify(
                        {
                            "error": f"Inventory sync is only available for Google Ad Manager. Your tenant is using the '{adapter_type}' adapter which does not require inventory sync."
                        }
                    ),
                    400,
                )

            # Check if GAM is configured
            from src.core.database.models import AdapterConfig

            adapter_config = db_session.scalars(
                select(AdapterConfig).filter_by(tenant_id=tenant_id, adapter_type="google_ad_manager")
            ).first()

            if not adapter_config or not adapter_config.gam_network_code:
                return (
                    jsonify(
                        {
                            "error": "Please connect your GAM account before trying to sync inventory. Go to Ad Server settings to configure GAM."
                        }
                    ),
                    400,
                )

        # Parse request body
        data = request.get_json() or {}
        sync_mode = data.get("mode", "incremental")  # Default to incremental (safer)
        sync_types = data.get("types", None)
        custom_targeting_limit = data.get("custom_targeting_limit")
        audience_segment_limit = data.get("audience_segment_limit")

        # Start background sync
        sync_id = start_inventory_sync_background(
            tenant_id=tenant_id,
            sync_mode=sync_mode,
            sync_types=sync_types,
            custom_targeting_limit=custom_targeting_limit,
            audience_segment_limit=audience_segment_limit,
        )

        # Return 202 Accepted with sync_id
        return (
            jsonify(
                {
                    "sync_id": sync_id,
                    "status": "running",
                    "message": "Sync started in background. Check status at /api/sync/status/{sync_id}",
                }
            ),
            202,
        )

    except ValueError as e:
        # Sync already running or validation error
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.error(f"Error starting inventory sync: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@inventory_bp.route("/api/tenant/<tenant_id>/inventory-stats", methods=["GET"])
@require_tenant_access(api_mode=True)
def get_inventory_stats(tenant_id):
    """Get inventory statistics by type and status for debugging.

    Returns:
        JSON with counts grouped by inventory_type and status
    """
    try:
        with get_db_session() as db_session:
            # Get counts by inventory_type and status
            stmt = (
                select(GAMInventory.inventory_type, GAMInventory.status, func.count())
                .filter(GAMInventory.tenant_id == tenant_id)
                .group_by(GAMInventory.inventory_type, GAMInventory.status)
            )
            results = db_session.execute(stmt).all()

            # Format results
            stats = {}
            for inv_type, status, count in results:
                if inv_type not in stats:
                    stats[inv_type] = {}
                stats[inv_type][status] = count

            return jsonify({"tenant_id": tenant_id, "stats": stats})

    except Exception as e:
        logger.error(f"Error fetching inventory stats: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@inventory_bp.route("/api/tenant/<tenant_id>/sync-status", methods=["GET"])
@require_tenant_access(api_mode=True)
def get_sync_status(tenant_id):
    """Get detailed sync status for each inventory type.

    Returns:
        JSON with per-type sync status including last sync time, counts, and errors
    """
    try:
        with get_db_session() as db_session:
            # Define inventory types we track
            inventory_types = ["ad_unit", "placement", "label", "custom_targeting_key", "audience_segment"]

            sync_status = {}

            for inv_type in inventory_types:
                # Get total count for this type
                count_stmt = (
                    select(func.count())
                    .select_from(GAMInventory)
                    .where(GAMInventory.tenant_id == tenant_id, GAMInventory.inventory_type == inv_type)
                )
                total_count = db_session.scalar(count_stmt) or 0

                # Get count by status
                status_stmt = (
                    select(GAMInventory.status, func.count())
                    .filter(GAMInventory.tenant_id == tenant_id, GAMInventory.inventory_type == inv_type)
                    .group_by(GAMInventory.status)
                )
                status_results = db_session.execute(status_stmt).all()
                status_counts = dict(status_results)

                # Get last sync time for this type
                last_sync_stmt = (
                    select(GAMInventory.last_synced)
                    .filter(GAMInventory.tenant_id == tenant_id, GAMInventory.inventory_type == inv_type)
                    .order_by(GAMInventory.last_synced.desc())
                    .limit(1)
                )
                last_synced = db_session.scalar(last_sync_stmt)

                # Get most recent sync job for this tenant
                from src.core.database.models import SyncJob

                last_job_stmt = (
                    select(SyncJob)
                    .filter(SyncJob.tenant_id == tenant_id, SyncJob.sync_type == "inventory")
                    .order_by(SyncJob.started_at.desc())
                    .limit(1)
                )
                last_job = db_session.scalars(last_job_stmt).first()

                sync_status[inv_type] = {
                    "total_count": total_count,
                    "status_counts": status_counts,
                    "last_synced": last_synced.isoformat() if last_synced else None,
                    "last_job_status": last_job.status if last_job else None,
                    "last_job_time": last_job.started_at.isoformat() if last_job and last_job.started_at else None,
                    "last_job_error": last_job.error_message if last_job and last_job.error_message else None,
                }

            return jsonify({"tenant_id": tenant_id, "sync_status": sync_status})

    except Exception as e:
        logger.error(f"Error fetching sync status: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@inventory_bp.route("/api/tenant/<tenant_id>/inventory/tree", methods=["GET"])
@require_tenant_access(api_mode=True)
def get_inventory_tree(tenant_id):
    """Get ad unit hierarchy tree structure for tree view.

    Query Parameters:
        search (str, optional): Search term to filter ad units by name or path

    Returns:
        JSON with hierarchical tree of ad units including parent-child relationships
    """
    from flask import current_app, request

    search = request.args.get("search", "").strip()

    # Use cache if available (5 minute TTL) - only cache when no search
    cache = getattr(current_app, "cache", None)
    if cache and not search:
        cache_key = f"inventory_tree:v2:{tenant_id}"  # v2: added search_active/matching_count fields
        cached_result = cache.get(cache_key)
        if cached_result:
            logger.info(f"Returning cached inventory tree for tenant: {tenant_id}")
            return cached_result

    logger.info(f"Building inventory tree for tenant: {tenant_id}, search: '{search}'")
    try:
        with get_db_session() as db_session:
            from src.core.database.models import GAMInventory

            # Get all ad units (active only by default)
            stmt = select(GAMInventory).where(
                GAMInventory.tenant_id == tenant_id,
                GAMInventory.inventory_type == "ad_unit",
                GAMInventory.status == "ACTIVE",
            )

            # If search term provided, filter by name or path
            if search:
                stmt = stmt.where(
                    or_(
                        GAMInventory.name.ilike(f"%{search}%"),
                        func.cast(GAMInventory.path, String).ilike(f"%{search}%"),
                    )
                )

            matching_units = db_session.scalars(stmt).all()

            logger.info(f"Found {len(matching_units)} matching ad units")

            # If search is active, we need to include all ancestor nodes
            # to build the proper tree hierarchy
            if search and matching_units:
                # Collect all parent IDs from matching units
                ancestor_ids = set()
                for unit in matching_units:
                    metadata = unit.inventory_metadata or {}
                    if isinstance(metadata, dict):
                        parent_id = metadata.get("parent_id")
                        # Walk up the tree to get all ancestors
                        while parent_id:
                            if parent_id not in ancestor_ids:
                                ancestor_ids.add(parent_id)
                                # Fetch the parent to get its parent_id
                                parent_stmt = select(GAMInventory).where(
                                    GAMInventory.tenant_id == tenant_id,
                                    GAMInventory.inventory_id == parent_id,
                                )
                                parent_unit = db_session.scalars(parent_stmt).first()
                                if parent_unit:
                                    parent_metadata = parent_unit.inventory_metadata or {}
                                    if isinstance(parent_metadata, dict):
                                        parent_id = parent_metadata.get("parent_id")
                                    else:
                                        break
                                else:
                                    break
                            else:
                                break

                # Fetch all ancestor nodes
                if ancestor_ids:
                    ancestor_stmt = select(GAMInventory).where(
                        GAMInventory.tenant_id == tenant_id,
                        GAMInventory.inventory_id.in_(ancestor_ids),
                    )
                    ancestor_units = db_session.scalars(ancestor_stmt).all()
                    all_units = list(matching_units) + list(ancestor_units)
                    logger.info(f"Added {len(ancestor_units)} ancestor nodes for tree structure")
                else:
                    all_units = list(matching_units)
            else:
                all_units = matching_units

            logger.info(f"Building tree from {len(all_units)} total nodes")

            # Build tree structure
            units_by_id = {}
            root_units = []

            # Track which units matched the search (for highlighting)
            matching_ids = {unit.inventory_id for unit in matching_units} if search else set()

            # First pass: create all unit objects
            for unit in all_units:
                metadata = unit.inventory_metadata or {}
                if not isinstance(metadata, dict):
                    metadata = {}

                unit_obj = {
                    "id": unit.inventory_id,
                    "name": unit.name,
                    "status": unit.status,
                    "code": metadata.get("ad_unit_code", ""),
                    "path": unit.path or [unit.name],
                    "parent_id": metadata.get("parent_id"),
                    "has_children": metadata.get("has_children", False),
                    "matched_search": unit.inventory_id in matching_ids,  # Flag for highlighting
                    "sizes": metadata.get("sizes", []),  # Include sizes for format matching
                    "children": [],
                }
                units_by_id[unit.inventory_id] = unit_obj

            # Second pass: build hierarchy
            for _unit_id, unit_obj in units_by_id.items():
                parent_id = unit_obj.get("parent_id")
                if parent_id and parent_id in units_by_id:
                    # This is a child unit
                    units_by_id[parent_id]["children"].append(unit_obj)
                else:
                    # This is a root unit (no parent or parent not found)
                    root_units.append(unit_obj)

            logger.info(f"Built tree with {len(root_units)} root units")

            # Get counts for other inventory types (for Quick Stats)
            placements_stmt = (
                select(func.count())
                .select_from(GAMInventory)
                .where(
                    GAMInventory.tenant_id == tenant_id,
                    GAMInventory.inventory_type == "placement",
                    GAMInventory.status == "ACTIVE",
                )
            )
            placements_count = db_session.scalar(placements_stmt) or 0

            labels_stmt = (
                select(func.count())
                .select_from(GAMInventory)
                .where(
                    GAMInventory.tenant_id == tenant_id,
                    GAMInventory.inventory_type == "label",
                    GAMInventory.status == "ACTIVE",
                )
            )
            labels_count = db_session.scalar(labels_stmt) or 0

            targeting_stmt = (
                select(func.count())
                .select_from(GAMInventory)
                .where(
                    GAMInventory.tenant_id == tenant_id,
                    GAMInventory.inventory_type == "custom_targeting_key",
                    GAMInventory.status == "ACTIVE",
                )
            )
            targeting_count = db_session.scalar(targeting_stmt) or 0

            segments_stmt = (
                select(func.count())
                .select_from(GAMInventory)
                .where(
                    GAMInventory.tenant_id == tenant_id,
                    GAMInventory.inventory_type == "audience_segment",
                    GAMInventory.status == "ACTIVE",
                )
            )
            segments_count = db_session.scalar(segments_stmt) or 0

            logger.info(
                f"Inventory counts - Ad Units: {len(all_units)}, Placements: {placements_count}, "
                f"Labels: {labels_count}, Targeting Keys: {targeting_count}, Audience Segments: {segments_count}"
            )

            # Get last sync time from most recent inventory item
            last_sync_stmt = (
                select(GAMInventory.last_synced)
                .where(GAMInventory.tenant_id == tenant_id)
                .order_by(GAMInventory.last_synced.desc())
                .limit(1)
            )
            last_sync = db_session.scalar(last_sync_stmt)

            result = jsonify(
                {
                    "root_units": root_units,
                    "total_units": len(all_units),
                    "root_count": len(root_units),
                    "placements": placements_count,
                    "labels": labels_count,
                    "custom_targeting_keys": targeting_count,
                    "audience_segments": segments_count,
                    "search_active": bool(search),  # Flag to indicate filtered results
                    "matching_count": len(matching_ids) if search else 0,
                    "last_sync": last_sync.isoformat() if last_sync else None,
                }
            )

            # Cache the result for 5 minutes - only when no search
            if cache and not search:
                cache.set(cache_key, result, timeout=300)

            return result

    except Exception as e:
        logger.error(f"Error building inventory tree for tenant {tenant_id}: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@inventory_bp.route("/api/tenant/<tenant_id>/inventory-list", methods=["GET"])
@require_tenant_access(api_mode=True)
def get_inventory_list(tenant_id):
    """Get list of ad units and placements for picker UI.

    Query Parameters:
        type: Filter by inventory_type ('ad_unit' or 'placement', defaults to both)
        search: Filter by name (case-insensitive partial match)
        status: Filter by status (default: 'ACTIVE', use 'ALL' for all statuses)
        ids: Comma-separated list of inventory_ids to fetch (bypasses 500 limit)

    Returns:
        JSON array of inventory items with id, name, type, path, status
    """
    from flask import current_app

    try:
        inventory_type = request.args.get("type")  # 'ad_unit' or 'placement' or None for both
        search = request.args.get("search", "").strip()
        status = request.args.get("status", "ACTIVE")
        ids_param = request.args.get("ids", "").strip()  # Comma-separated IDs

        # Use cache if available and no search term (5 minute TTL)
        cache = getattr(current_app, "cache", None)
        if cache and not search:
            cache_key = f"inventory_list:{tenant_id}:{inventory_type or 'all'}:{status}"
            cached_result = cache.get(cache_key)
            if cached_result:
                logger.info(f"Returning cached inventory list for tenant: {tenant_id}")
                return cached_result

        logger.info(
            f"Building inventory list: tenant={tenant_id}, type={inventory_type or 'all'}, "
            f"search='{search}', status={status}"
        )

        with get_db_session() as db_session:
            # First, get total counts before filtering (for diagnostics)
            total_stmt = select(func.count()).select_from(GAMInventory).filter(GAMInventory.tenant_id == tenant_id)
            total_count = db_session.scalar(total_stmt) or 0

            # Get counts by type before filtering
            type_counts_stmt = (
                select(GAMInventory.inventory_type, func.count())
                .filter(GAMInventory.tenant_id == tenant_id)
                .group_by(GAMInventory.inventory_type)
            )
            type_counts = dict(db_session.execute(type_counts_stmt).all())

            logger.info(f"Total inventory in DB: {total_count}, by type: {type_counts}")

            # Build query
            stmt = select(GAMInventory).filter(GAMInventory.tenant_id == tenant_id)

            # Filter by specific IDs if provided (bypass search, status, and limit filters)
            if ids_param:
                ids_list = [id.strip() for id in ids_param.split(",") if id.strip()]
                if ids_list:
                    logger.info(f"Filtering by specific IDs: {ids_list}")
                    stmt = stmt.filter(GAMInventory.inventory_id.in_(ids_list))

                    # Still apply type filter if specified
                    if inventory_type:
                        logger.info(f"Applying type filter: {inventory_type}")
                        stmt = stmt.filter(GAMInventory.inventory_type == inventory_type)

                    items = db_session.scalars(stmt).all()

                    # Format response
                    result = []
                    for item in items:
                        result.append(
                            {
                                "id": item.inventory_id,
                                "name": item.name,
                                "type": item.inventory_type,
                                "path": item.path or [],
                                "status": item.status,
                                "metadata": item.inventory_metadata or {},
                            }
                        )

                    logger.info(
                        f"Returned {len(result)} items for specific IDs (requested {len(ids_list)}, found {len(result)})"
                    )
                    return jsonify({"items": result, "total": len(result)})

            # Filter by type if specified
            if inventory_type:
                stmt = stmt.filter(GAMInventory.inventory_type == inventory_type)
            else:
                # Default to ad_unit and placement only
                stmt = stmt.filter(GAMInventory.inventory_type.in_(["ad_unit", "placement"]))

            # Filter by status (allow 'ALL' to skip status filter)
            if status and status.upper() != "ALL":
                stmt = stmt.filter(GAMInventory.status == status)

            # Filter by search term
            if search:
                stmt = stmt.filter(
                    or_(
                        GAMInventory.name.ilike(f"%{search}%"),
                        func.cast(GAMInventory.path, String).ilike(f"%{search}%"),
                    )
                )

            # Order by path/name for better organization
            stmt = stmt.order_by(GAMInventory.inventory_type, GAMInventory.name)

            # Limit results to prevent overwhelming the UI
            stmt = stmt.limit(500)

            items = db_session.scalars(stmt).all()

            logger.info(
                f"Query returned {len(items)} items after filtering "
                f"(filters: type={inventory_type or 'all'}, status={status}, search='{search}')"
            )

            # Format response
            result = []
            for item in items:
                result.append(
                    {
                        "id": item.inventory_id,
                        "name": item.name,
                        "type": item.inventory_type,
                        "path": item.path if item.path else [item.name],
                        "status": item.status,
                        "metadata": item.inventory_metadata or {},
                    }
                )

            logger.info(f"Returning {len(result)} formatted inventory items to UI")
            response = jsonify({"items": result, "count": len(result), "has_more": len(result) >= 500})

            # Cache the result for 5 minutes (only if no search term)
            if cache and not search:
                cache.set(cache_key, response, timeout=300)

            return response

    except Exception as e:
        logger.error(f"Error fetching inventory list: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500
