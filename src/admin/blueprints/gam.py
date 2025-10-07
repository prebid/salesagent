"""Google Ad Manager (GAM) integration blueprint."""

import json
import logging
from datetime import UTC, datetime

from flask import Blueprint, jsonify, render_template, request, session

from src.adapters.gam_inventory_discovery import GAMInventoryDiscovery
from src.adapters.gam_reporting_service import GAMReportingService
from src.admin.utils import require_tenant_access
from src.core.database.database_session import get_db_session
from src.core.database.models import GAMLineItem, GAMOrder, Tenant

logger = logging.getLogger(__name__)

# Create blueprint
gam_bp = Blueprint("gam", __name__, url_prefix="/tenant/<tenant_id>/gam")


def validate_gam_network_response(network) -> tuple[bool, str | None]:
    """Validate GAM network response structure."""
    if not network:
        return False, "Network response is None"

    # Check required fields
    required_fields = ["networkCode", "displayName", "id"]
    for field in required_fields:
        if field not in network:
            return False, f"Missing required field: {field}"

    return True, None


def validate_gam_user_response(user) -> tuple[bool, str | None]:
    """Validate GAM user response structure."""
    if not user:
        return False, "User response is None"

    # Check required fields
    if "id" not in user:
        return False, "Missing required field: id"

    return True, None


def validate_gam_config(data: dict) -> list | None:
    """Validate GAM configuration data."""
    errors = []

    # Network code validation
    network_code = data.get("network_code")
    if network_code:
        network_code_str = str(network_code).strip()
        if not network_code_str.isdigit():
            errors.append("Network code must be numeric")
        elif len(network_code_str) > 20:
            errors.append("Network code is too long")

    # Refresh token validation
    refresh_token = data.get("refresh_token", "").strip()
    if not refresh_token:
        errors.append("Refresh token is required")
    elif len(refresh_token) > 1000:
        errors.append("Refresh token is too long")

    # Trafficker ID validation
    trafficker_id = data.get("trafficker_id")
    if trafficker_id:
        trafficker_id_str = str(trafficker_id).strip()
        if not trafficker_id_str.isdigit():
            errors.append("Trafficker ID must be numeric")
        elif len(trafficker_id_str) > 20:
            errors.append("Trafficker ID is too long")

    return errors if errors else None


@gam_bp.route("/detect-network", methods=["POST"])
@require_tenant_access()
def detect_gam_network(tenant_id):
    """Auto-detect GAM network code from refresh token."""
    if session.get("role") == "viewer":
        return jsonify({"success": False, "error": "Access denied"}), 403

    try:
        data = request.get_json()
        refresh_token = data.get("refresh_token")

        if not refresh_token:
            return jsonify({"success": False, "error": "Refresh token required"}), 400

        # Create a temporary GAM client with just the refresh token
        from googleads import ad_manager, oauth2

        # Get OAuth credentials from validated configuration
        try:
            from src.core.config import get_gam_oauth_config

            gam_config = get_gam_oauth_config()
            client_id = gam_config.client_id
            client_secret = gam_config.client_secret

        except Exception as e:
            return (
                jsonify(
                    {
                        "success": False,
                        "error": f"GAM OAuth configuration error: {str(e)}",
                    }
                ),
                500,
            )

        # Create OAuth2 client with refresh token
        oauth2_client = oauth2.GoogleRefreshTokenClient(
            client_id=client_id,
            client_secret=client_secret,
            refresh_token=refresh_token,
        )

        # Test if credentials are valid
        try:
            oauth2_client.Refresh()
        except Exception as e:
            return (
                jsonify({"success": False, "error": f"Invalid refresh token: {str(e)}"}),
                400,
            )

        # Create GAM client
        client = ad_manager.AdManagerClient(oauth2_client, "AdCP-Sales-Agent")

        # Get network service and retrieve network info
        network_service = client.GetService("NetworkService", version="v202505")

        try:
            # Try getAllNetworks first (doesn't require network_code)
            try:
                all_networks = network_service.getAllNetworks()
                if all_networks and len(all_networks) > 0:
                    # Use the first network (most users have access to only one)
                    network = all_networks[0]

                    # Validate network response structure
                    is_valid, error_msg = validate_gam_network_response(network)
                    if not is_valid:
                        logger.error(f"Invalid GAM network response: {error_msg}")
                        return (
                            jsonify(
                                {
                                    "success": False,
                                    "error": f"Invalid network response from GAM: {error_msg}",
                                }
                            ),
                            500,
                        )

                    # Also get the current user ID to use as trafficker_id
                    trafficker_id = None
                    try:
                        # Set the network code in the client so we can get user info
                        client.network_code = str(network["networkCode"])
                        user_service = client.GetService("UserService", version="v202505")
                        current_user = user_service.getCurrentUser()

                        if current_user:
                            # Validate user response
                            is_valid, error_msg = validate_gam_user_response(current_user)
                            if is_valid:
                                trafficker_id = str(current_user["id"])
                                logger.info(f"Detected current user ID: {trafficker_id}")
                            else:
                                logger.warning(f"Invalid user response: {error_msg}")
                    except Exception as e:
                        logger.warning(f"Could not get current user: {e}")

                    return jsonify(
                        {
                            "success": True,
                            "network_code": str(network["networkCode"]),
                            "network_name": network["displayName"],
                            "network_id": str(network["id"]),
                            "network_count": len(all_networks),
                            "trafficker_id": trafficker_id,
                        }
                    )
            except AttributeError:
                # getAllNetworks might not be available in this GAM version
                pass

            # If getAllNetworks didn't work, we can't get the network without a network_code
            return (
                jsonify(
                    {
                        "success": False,
                        "error": "Unable to retrieve network information. The getAllNetworks() API is not available and getCurrentNetwork() requires a network code.",
                    }
                ),
                400,
            )

        except Exception as e:
            logger.error(f"Failed to get network info: {e}")
            return (
                jsonify(
                    {
                        "success": False,
                        "error": f"Failed to retrieve network information: {str(e)}",
                    }
                ),
                500,
            )

    except Exception as e:
        logger.error(f"Error detecting GAM network for tenant {tenant_id}: {e}")
        return (
            jsonify({"success": False, "error": f"Error detecting network: {str(e)}"}),
            500,
        )


@gam_bp.route("/configure", methods=["POST"])
@require_tenant_access()
def configure_gam(tenant_id):
    """Save GAM configuration for a tenant."""
    if session.get("role") == "viewer":
        return jsonify({"success": False, "error": "Access denied"}), 403

    try:
        data = request.get_json()

        # Validate GAM configuration data
        validation_errors = validate_gam_config(data)
        if validation_errors:
            return jsonify({"success": False, "errors": validation_errors}), 400

        # Sanitize input data
        network_code = str(data.get("network_code", "")).strip() if data.get("network_code") else None
        refresh_token = data.get("refresh_token", "").strip()
        trafficker_id = str(data.get("trafficker_id", "")).strip() if data.get("trafficker_id") else None
        order_name_template = data.get("order_name_template", "").strip() or None
        line_item_name_template = data.get("line_item_name_template", "").strip() or None

        # Log what we received (without exposing sensitive token)
        logger.info(
            f"GAM config save - network_code: {network_code}, trafficker_id: {trafficker_id}, token_length: {len(refresh_token)}"
        )

        # If network code or trafficker_id not provided, try to auto-detect them
        if not trafficker_id:
            logger.warning(f"No trafficker_id provided for tenant {tenant_id}")

        with get_db_session() as db_session:
            # Get existing tenant
            tenant = db_session.query(Tenant).filter_by(tenant_id=tenant_id).first()

            if not tenant:
                return jsonify({"success": False, "error": "Tenant not found"}), 404

            # Get or create adapter config
            from src.core.database.models import AdapterConfig

            adapter_config = db_session.query(AdapterConfig).filter_by(tenant_id=tenant_id).first()

            if not adapter_config:
                adapter_config = AdapterConfig(tenant_id=tenant_id, adapter_type="google_ad_manager")
                db_session.add(adapter_config)

            # Update GAM configuration in adapter_config
            adapter_config.gam_network_code = network_code
            adapter_config.gam_refresh_token = refresh_token
            adapter_config.gam_trafficker_id = trafficker_id
            adapter_config.gam_order_name_template = order_name_template
            adapter_config.gam_line_item_name_template = line_item_name_template

            # Also update tenant's ad_server field
            tenant.ad_server = "google_ad_manager"

            db_session.commit()

            logger.info(f"GAM configuration saved for tenant {tenant_id}")

            return jsonify(
                {
                    "success": True,
                    "message": "GAM configuration saved successfully",
                }
            )

    except Exception as e:
        logger.error(f"Error saving GAM configuration for tenant {tenant_id}: {e}")
        return (
            jsonify({"success": False, "error": f"Error saving configuration: {str(e)}"}),
            500,
        )


@gam_bp.route("/line-item/<line_item_id>")
@require_tenant_access()
def view_gam_line_item(tenant_id, line_item_id):
    """View details of a GAM line item."""
    try:
        with get_db_session() as db_session:
            # Get the line item
            line_item = db_session.query(GAMLineItem).filter_by(tenant_id=tenant_id, line_item_id=line_item_id).first()

            if not line_item:
                # Try to fetch from GAM if not in database
                tenant = db_session.query(Tenant).filter_by(tenant_id=tenant_id).first()

                if not tenant:
                    return render_template("error.html", error="Tenant not found"), 404

                # Get GAM configuration from adapter_config
                from src.core.database.models import AdapterConfig

                adapter_config = db_session.query(AdapterConfig).filter_by(tenant_id=tenant_id).first()

                if not adapter_config or not adapter_config.gam_network_code or not adapter_config.gam_refresh_token:
                    return render_template("error.html", error="GAM not configured for this tenant"), 400

                # Initialize GAM reporting service
                reporting_service = GAMReportingService(
                    network_code=adapter_config.gam_network_code,
                    refresh_token=adapter_config.gam_refresh_token,
                )

                # Fetch line item details from GAM
                line_item_data = reporting_service.get_line_item_details(line_item_id)

                if not line_item_data:
                    return render_template("error.html", error="Line item not found in GAM"), 404

                # Create a temporary line item object for display
                line_item = GAMLineItem(
                    tenant_id=tenant_id,
                    line_item_id=line_item_id,
                    name=line_item_data.get("name", "Unknown"),
                    order_id=str(line_item_data.get("orderId", "")),
                    status=line_item_data.get("status", "UNKNOWN"),
                    start_date=line_item_data.get("startDateTime"),
                    end_date=line_item_data.get("endDateTime"),
                    line_item_type=line_item_data.get("lineItemType", "UNKNOWN"),
                    priority=line_item_data.get("priority"),
                    cost_type=line_item_data.get("costType"),
                    cost_per_unit=line_item_data.get("costPerUnit", {}).get("microAmount"),
                    currency_code=line_item_data.get("costPerUnit", {}).get("currencyCode"),
                    goal_type=line_item_data.get("primaryGoal", {}).get("goalType"),
                    goal_units=line_item_data.get("primaryGoal", {}).get("units"),
                    units_delivered=0,
                    impressions_delivered=0,
                    clicks_delivered=0,
                    ctr=0.0,
                    last_synced=datetime.now(UTC),
                    raw_data=json.dumps(line_item_data),
                )

                # Get the order if available
                if line_item.order_id:
                    order = (
                        db_session.query(GAMOrder).filter_by(tenant_id=tenant_id, order_id=line_item.order_id).first()
                    )
                else:
                    order = None

            else:
                # Get the associated order
                order = (
                    db_session.query(GAMOrder).filter_by(tenant_id=tenant_id, order_id=line_item.order_id).first()
                    if line_item.order_id
                    else None
                )

            # Get tenant for template
            tenant_obj = db_session.query(Tenant).filter_by(tenant_id=tenant_id).first()
            if not tenant_obj:
                return render_template("error.html", error="Tenant not found"), 404

            return render_template(
                "gam_line_item_viewer.html",
                tenant={"tenant_id": tenant_obj.tenant_id, "name": tenant_obj.name},
                tenant_id=tenant_id,
                line_item=line_item,
                order=order,
            )

    except Exception as e:
        logger.error(f"Error viewing GAM line item {line_item_id}: {e}")
        return render_template("error.html", error=f"Error loading line item: {str(e)}"), 500


# API endpoints for GAM
@gam_bp.route("/api/custom-targeting-keys", methods=["GET"])
@require_tenant_access(api_mode=True)
def get_gam_custom_targeting_keys(tenant_id):
    """Get GAM custom targeting keys."""
    try:
        with get_db_session() as db_session:
            tenant = db_session.query(Tenant).filter_by(tenant_id=tenant_id).first()

            if not tenant:
                return jsonify({"error": "Tenant not found"}), 404

            # Get GAM configuration from adapter_config
            from src.core.database.models import AdapterConfig

            adapter_config = db_session.query(AdapterConfig).filter_by(tenant_id=tenant_id).first()

            if not adapter_config or not adapter_config.gam_network_code or not adapter_config.gam_refresh_token:
                return jsonify({"error": "GAM not configured for this tenant"}), 400

            # Initialize GAM inventory discovery
            discovery = GAMInventoryDiscovery(
                network_code=adapter_config.gam_network_code,
                refresh_token=adapter_config.gam_refresh_token,
            )

            # Get custom targeting keys
            keys = discovery.discover_custom_targeting()

            return jsonify({"success": True, "keys": keys})

    except Exception as e:
        logger.error(f"Error getting GAM custom targeting keys: {e}")
        return jsonify({"error": str(e)}), 500


@gam_bp.route("/sync-inventory", methods=["POST"])
@require_tenant_access()
def sync_gam_inventory(tenant_id):
    """Trigger GAM inventory sync for a tenant."""
    if session.get("role") == "viewer":
        return jsonify({"success": False, "error": "Access denied"}), 403

    try:
        with get_db_session() as db_session:
            # Get tenant and adapter config
            tenant = db_session.query(Tenant).filter_by(tenant_id=tenant_id).first()
            if not tenant:
                return jsonify({"success": False, "error": "Tenant not found"}), 404

            from src.core.database.models import AdapterConfig

            adapter_config = db_session.query(AdapterConfig).filter_by(tenant_id=tenant_id).first()

            if not adapter_config or not adapter_config.gam_network_code or not adapter_config.gam_refresh_token:
                return jsonify({"success": False, "error": "GAM not configured for this tenant"}), 400

            # Initialize GAM inventory discovery
            discovery = GAMInventoryDiscovery(
                network_code=adapter_config.gam_network_code,
                refresh_token=adapter_config.gam_refresh_token,
            )

            # Perform full inventory discovery
            ad_units = discovery.discover_ad_units()
            placements = discovery.discover_placements()
            targeting = discovery.discover_custom_targeting()

            # Store in database
            from src.services.gam_inventory_service import GAMInventoryService

            inventory_service = GAMInventoryService(db_session)

            # Save ad units
            for ad_unit in ad_units:
                inventory_service.save_ad_unit(
                    tenant_id=tenant_id,
                    ad_unit_id=str(ad_unit["id"]),
                    name=ad_unit["name"],
                    ad_unit_code=ad_unit.get("adUnitCode", ""),
                    parent_id=str(ad_unit.get("parentId", "")),
                    ad_unit_sizes=ad_unit.get("adUnitSizes", []),
                    targeting_preset_id=ad_unit.get("targetingPresetId"),
                    description=ad_unit.get("description", ""),
                    explicitly_targeted=ad_unit.get("explicitlyTargeted", False),
                    status=ad_unit.get("status", "ACTIVE"),
                )

            # Save placements
            for placement in placements:
                inventory_service.save_placement(
                    tenant_id=tenant_id,
                    placement_id=str(placement["id"]),
                    name=placement["name"],
                    description=placement.get("description", ""),
                    placement_code=placement.get("placementCode", ""),
                    status=placement.get("status", "ACTIVE"),
                    targeted_ad_unit_ids=[str(aid) for aid in placement.get("targetedAdUnitIds", [])],
                )

            # Save custom targeting keys
            for key in targeting:
                inventory_service.save_targeting_key(
                    tenant_id=tenant_id,
                    key_id=str(key["id"]),
                    name=key["name"],
                    display_name=key.get("displayName", key["name"]),
                    key_type=key.get("type", "FREEFORM"),
                    status=key.get("status", "ACTIVE"),
                )

            db_session.commit()

            # Update tenant's last sync time
            tenant.last_inventory_sync = datetime.now(UTC)
            db_session.commit()

            logger.info(f"Successfully synced GAM inventory for tenant {tenant_id}")

            return jsonify(
                {
                    "success": True,
                    "message": "Inventory synced successfully",
                    "ad_units_count": len(ad_units),
                    "placements_count": len(placements),
                    "targeting_count": len(targeting),
                }
            )

    except Exception as e:
        logger.error(f"Error syncing GAM inventory for tenant {tenant_id}: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@gam_bp.route("/api/line-item/<line_item_id>", methods=["GET"])
@require_tenant_access(api_mode=True)
def get_gam_line_item_api(tenant_id, line_item_id):
    """API endpoint to get GAM line item details."""
    try:
        with get_db_session() as db_session:
            # Get the line item
            line_item = db_session.query(GAMLineItem).filter_by(tenant_id=tenant_id, line_item_id=line_item_id).first()

            if not line_item:
                # Try to fetch from GAM if not in database
                tenant = db_session.query(Tenant).filter_by(tenant_id=tenant_id).first()

                if not tenant:
                    return jsonify({"error": "Tenant not found"}), 404

                # Get GAM configuration from adapter_config
                from src.core.database.models import AdapterConfig

                adapter_config = db_session.query(AdapterConfig).filter_by(tenant_id=tenant_id).first()

                if not adapter_config or not adapter_config.gam_network_code or not adapter_config.gam_refresh_token:
                    return jsonify({"error": "GAM not configured for this tenant"}), 400

                # Initialize GAM reporting service
                reporting_service = GAMReportingService(
                    network_code=adapter_config.gam_network_code,
                    refresh_token=adapter_config.gam_refresh_token,
                )

                # Fetch line item details from GAM
                line_item_data = reporting_service.get_line_item_details(line_item_id)

                if not line_item_data:
                    return jsonify({"error": "Line item not found"}), 404

                return jsonify(
                    {
                        "success": True,
                        "line_item": line_item_data,
                    }
                )

            # Return the line item data
            return jsonify(
                {
                    "success": True,
                    "line_item": {
                        "id": line_item.line_item_id,
                        "name": line_item.name,
                        "order_id": line_item.order_id,
                        "status": line_item.status,
                        "start_date": line_item.start_date.isoformat() if line_item.start_date else None,
                        "end_date": line_item.end_date.isoformat() if line_item.end_date else None,
                        "type": line_item.line_item_type,
                        "priority": line_item.priority,
                        "cost_type": line_item.cost_type,
                        "cost_per_unit": line_item.cost_per_unit,
                        "currency": line_item.currency_code,
                        "goal_type": line_item.goal_type,
                        "goal_units": line_item.goal_units,
                        "units_delivered": line_item.units_delivered,
                        "impressions_delivered": line_item.impressions_delivered,
                        "clicks_delivered": line_item.clicks_delivered,
                        "ctr": line_item.ctr,
                        "last_synced": line_item.last_synced.isoformat() if line_item.last_synced else None,
                    },
                }
            )

    except Exception as e:
        logger.error(f"Error getting GAM line item {line_item_id}: {e}")
        return jsonify({"error": str(e)}), 500
