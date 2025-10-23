"""Inventory and orders management blueprint."""

import json
import logging

from flask import Blueprint, jsonify, render_template, request, session
from sqlalchemy import String, func, or_, select

from src.admin.utils import get_tenant_config_from_db, require_auth, require_tenant_access
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
                custom_keys.append(
                    {
                        "id": row.inventory_id,
                        "name": row.name,
                        "display_name": metadata.get("display_name") if metadata else row.name,
                        "status": row.status,
                        "type": metadata.get("type") if metadata else None,
                    }
                )

            audiences = []
            for row in audience_segments_rows:
                metadata = row.inventory_metadata or {}
                audiences.append(
                    {
                        "id": row.inventory_id,
                        "name": row.name,
                        "description": metadata.get("description") if metadata else None,
                        "status": row.status,
                        "size": metadata.get("size") if metadata else None,
                    }
                )

            labels = []
            for row in labels_rows:
                metadata = row.inventory_metadata or {}
                labels.append(
                    {
                        "id": row.inventory_id,
                        "name": row.name,
                        "description": metadata.get("description") if metadata else None,
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
        logger.error(f"Error fetching targeting data: {e}")
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

        # Get adapter instance
        from src.adapters import get_adapter

        adapter = get_adapter(adapter_type, principal, config=config, dry_run=False)

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
    """Sync GAM inventory for a tenant with optional selective sync.

    Request body (optional):
    {
        "types": ["ad_units", "placements", "labels", "custom_targeting", "audience_segments"],
        "custom_targeting_limit": 1000,  // Optional: limit number of custom targeting values
        "audience_segment_limit": 500    // Optional: limit number of audience segments
    }

    If no body provided, syncs everything (backwards compatible).
    """
    try:
        with get_db_session() as db_session:
            tenant = db_session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()

            if not tenant:
                return jsonify({"error": "Tenant not found"}), 404

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

            # Check authentication method - support both OAuth and service account
            auth_method = getattr(adapter_config, "gam_auth_method", None)

            if not auth_method:
                # Legacy: infer auth method from available credentials
                if adapter_config.gam_refresh_token:
                    auth_method = "oauth"
                elif hasattr(adapter_config, "gam_service_account_json") and adapter_config.gam_service_account_json:
                    auth_method = "service_account"
                else:
                    return (
                        jsonify(
                            {
                                "error": "Please connect your GAM account before trying to sync inventory. Go to Ad Server settings to configure GAM."
                            }
                        ),
                        400,
                    )

            # Parse request body for selective sync options
            data = request.get_json() or {}
            sync_types = data.get("types", None)  # None means sync all
            custom_targeting_limit = data.get("custom_targeting_limit")
            audience_segment_limit = data.get("audience_segment_limit")

            # Import and use GAM inventory discovery
            import os
            import json
            import tempfile

            from googleads import ad_manager, oauth2
            import google.oauth2.service_account

            from src.adapters.gam_inventory_discovery import GAMInventoryDiscovery

            # Create credentials based on auth method
            if auth_method == "service_account":
                # Service account authentication
                if not hasattr(adapter_config, "gam_service_account_json") or not adapter_config.gam_service_account_json:
                    return jsonify({"error": "Service account credentials not found"}), 400

                # Get service account JSON (already decrypted by model property)
                service_account_json_str = adapter_config.gam_service_account_json

                # Write to temporary file (required for google.oauth2.service_account.Credentials)
                with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
                    f.write(service_account_json_str)
                    temp_keyfile = f.name

                try:
                    # Create service account credentials (same pattern as GAM health check)
                    oauth2_credentials = google.oauth2.service_account.Credentials.from_service_account_file(
                        temp_keyfile, scopes=["https://www.googleapis.com/auth/dfp"]
                    )

                    # Create GAM client with service account credentials
                    client = ad_manager.AdManagerClient(
                        oauth2_credentials, "AdCP Sales Agent", network_code=adapter_config.gam_network_code
                    )
                finally:
                    # Clean up temp file
                    try:
                        os.unlink(temp_keyfile)
                    except Exception:
                        pass
            else:
                # OAuth authentication
                if not adapter_config.gam_refresh_token:
                    return jsonify({"error": "OAuth refresh token not found"}), 400

                # Create OAuth2 client
                oauth2_client = oauth2.GoogleRefreshTokenClient(
                    client_id=os.environ.get("GAM_OAUTH_CLIENT_ID"),
                    client_secret=os.environ.get("GAM_OAUTH_CLIENT_SECRET"),
                    refresh_token=adapter_config.gam_refresh_token,
                )

                # Create GAM client
                client = ad_manager.AdManagerClient(
                    oauth2_client, "AdCP Sales Agent", network_code=adapter_config.gam_network_code
                )

            # Initialize GAM inventory discovery
            discovery = GAMInventoryDiscovery(client=client, tenant_id=tenant_id)

            # Perform selective or full sync
            if sync_types:
                result = discovery.sync_selective(
                    sync_types=sync_types,
                    custom_targeting_limit=custom_targeting_limit,
                    audience_segment_limit=audience_segment_limit,
                )
            else:
                # Full sync (backwards compatible)
                result = discovery.sync_all()

            # Save to database
            from src.services.gam_inventory_service import GAMInventoryService

            inventory_service = GAMInventoryService(db_session)
            inventory_service._save_inventory_to_db(tenant_id, discovery)

            return jsonify(result)

    except Exception as e:
        logger.error(f"Error syncing inventory: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@inventory_bp.route("/api/tenant/<tenant_id>/inventory-list", methods=["GET"])
@require_tenant_access(api_mode=True)
def get_inventory_list(tenant_id):
    """Get list of ad units and placements for picker UI.

    Query Parameters:
        type: Filter by inventory_type ('ad_unit' or 'placement', defaults to both)
        search: Filter by name (case-insensitive partial match)
        status: Filter by status (default: 'ACTIVE')

    Returns:
        JSON array of inventory items with id, name, type, path, status
    """
    try:
        inventory_type = request.args.get("type")  # 'ad_unit' or 'placement' or None for both
        search = request.args.get("search", "").strip()
        status = request.args.get("status", "ACTIVE")

        with get_db_session() as db_session:
            # Build query
            stmt = select(GAMInventory).filter(GAMInventory.tenant_id == tenant_id)

            # Filter by type if specified
            if inventory_type:
                stmt = stmt.filter(GAMInventory.inventory_type == inventory_type)
            else:
                # Default to ad_unit and placement only
                stmt = stmt.filter(GAMInventory.inventory_type.in_(["ad_unit", "placement"]))

            # Filter by status
            if status:
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

            return jsonify({"items": result, "count": len(result), "has_more": len(result) >= 500})

    except Exception as e:
        logger.error(f"Error fetching inventory list: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500
