"""Inventory and orders management blueprint."""

import json
import logging

from flask import Blueprint, jsonify, render_template, request, session
from sqlalchemy import func

from src.admin.utils import get_tenant_config_from_db, require_auth, require_tenant_access
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
        tenant = db_session.query(Tenant).filter_by(tenant_id=tenant_id).first()
        row = (tenant.tenant_id, tenant.name) if tenant else None
        if not row:
            return "Tenant not found", 404

    tenant = {"tenant_id": row[0], "name": row[1]}

    return render_template(
        "targeting_browser_simple.html",
        tenant=tenant,
        tenant_id=tenant_id,
        tenant_name=row[1],
    )


@inventory_bp.route("/tenant/<tenant_id>/inventory")
@require_tenant_access()
def inventory_browser(tenant_id):
    """Display inventory browser page."""

    with get_db_session() as db_session:
        tenant = db_session.query(Tenant).filter_by(tenant_id=tenant_id).first()
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
        tenant = db_session.query(Tenant).filter_by(tenant_id=tenant_id).first()
        if not tenant:
            return "Tenant not found", 404

        # Get GAM orders from database
        orders = db_session.query(GAMOrder).filter_by(tenant_id=tenant_id).order_by(GAMOrder.updated_at.desc()).all()

        # Calculate summary stats
        total_orders = len(orders)
        active_orders = sum(1 for o in orders if o.status == "ACTIVE")

        # Get total revenue from media buys
        total_revenue = db_session.query(func.sum(MediaBuy.budget)).filter_by(tenant_id=tenant_id).scalar() or 0

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
@require_tenant_access(api_mode=True)
def sync_orders(tenant_id):
    """Sync GAM orders for a tenant."""
    try:
        with get_db_session() as db_session:
            tenant = db_session.query(Tenant).filter_by(tenant_id=tenant_id).first()

            if not tenant:
                return jsonify({"error": "Tenant not found"}), 404

            if not tenant.gam_network_code or not tenant.gam_refresh_token:
                return jsonify({"error": "GAM not configured for this tenant"}), 400

            # Import GAM sync functionality
            from src.adapters.gam_order_sync import sync_gam_orders

            # Perform sync
            result = sync_gam_orders(
                tenant_id=tenant_id,
                network_code=tenant.gam_network_code,
                refresh_token=tenant.gam_refresh_token,
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
            query = db_session.query(GAMOrder).filter_by(tenant_id=tenant_id)

            if status:
                query = query.filter_by(status=status)
            if advertiser:
                query = query.filter_by(advertiser_name=advertiser)

            # Get orders
            orders = query.order_by(GAMOrder.updated_at.desc()).all()

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
            order = db_session.query(GAMOrder).filter_by(tenant_id=tenant_id, order_id=order_id).first()

            if not order:
                return jsonify({"error": "Order not found"}), 404

            # Get line items count (would need GAMLineItem model)
            # line_items_count = db_session.query(GAMLineItem).filter_by(
            #     tenant_id=tenant_id,
            #     order_id=order_id
            # ).count()

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
            inventory_count = db_session.query(GAMInventory).filter_by(tenant_id=tenant_id).count()

            has_inventory = inventory_count > 0

            # Get last sync time if available
            last_sync = None
            if has_inventory:
                latest = (
                    db_session.query(GAMInventory)
                    .filter(GAMInventory.tenant_id == tenant_id)
                    .order_by(GAMInventory.created_at.desc())
                    .first()
                )
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
            tenant = db_session.query(Tenant).filter_by(tenant_id=tenant_id).first()

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
            principal_obj = db_session.query(Principal).filter_by(tenant_id=tenant_id).first()

            if not principal_obj:
                return jsonify({"error": "No principal found for tenant"}), 404

            # Create principal object
            from src.core.schemas import Principal as PrincipalSchema

            mappings = (
                principal_obj.platform_mappings
                if isinstance(principal_obj.platform_mappings, dict)
                else json.loads(principal_obj.platform_mappings)
            )
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
