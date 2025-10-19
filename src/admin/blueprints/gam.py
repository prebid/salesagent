"""Google Ad Manager (GAM) integration blueprint."""

import json
import logging
import os
from datetime import UTC, datetime

from flask import Blueprint, jsonify, render_template, request, session
from googleads import ad_manager
from sqlalchemy import select

from src.adapters.gam.utils.constants import GAM_API_VERSION
from src.adapters.gam_inventory_discovery import GAMInventoryDiscovery
from src.adapters.gam_reporting_service import GAMReportingService
from src.admin.utils import require_tenant_access
from src.admin.utils.audit_decorator import log_admin_action
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

    auth_method = data.get("auth_method", "oauth")

    # Network code validation
    network_code = data.get("network_code")
    if network_code:
        network_code_str = str(network_code).strip()
        if not network_code_str.isdigit():
            errors.append("Network code must be numeric")
        elif len(network_code_str) > 20:
            errors.append("Network code is too long")

    # Authentication method specific validation
    if auth_method == "oauth":
        # Refresh token validation
        refresh_token = data.get("refresh_token", "").strip()
        if not refresh_token:
            errors.append("Refresh token is required for OAuth authentication")
        elif len(refresh_token) > 1000:
            errors.append("Refresh token is too long")
    elif auth_method == "service_account":
        # Service account JSON validation
        service_account_json = data.get("service_account_json", "").strip()
        if not service_account_json:
            errors.append("Service account JSON is required for service account authentication")
        else:
            # Validate JSON structure
            try:
                import json

                key_data = json.loads(service_account_json)
                required_fields = ["type", "project_id", "private_key_id", "private_key", "client_email"]
                missing_fields = [field for field in required_fields if field not in key_data]
                if missing_fields:
                    errors.append(f"Service account JSON missing required fields: {', '.join(missing_fields)}")
                elif key_data.get("type") != "service_account":
                    errors.append("Service account JSON must be of type 'service_account'")
            except json.JSONDecodeError as e:
                errors.append(f"Invalid JSON format: {str(e)}")

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
@log_admin_action("detect_gam_network")
@require_tenant_access()
def detect_gam_network(tenant_id):
    """Auto-detect GAM network code from refresh token."""
    if session.get("role") == "viewer":
        return jsonify({"success": False, "error": "Access denied"}), 403

    try:
        data = request.get_json()
        refresh_token = data.get("refresh_token")
        network_code_provided = data.get("network_code")  # For multi-network selection

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

        # If network_code provided (user selected from multiple networks),
        # just get trafficker ID for that network
        if network_code_provided:
            try:
                client.network_code = network_code_provided
                user_service = client.GetService("UserService", version=GAM_API_VERSION)
                current_user = user_service.getCurrentUser()

                trafficker_id = None
                if current_user:
                    is_valid, error_msg = validate_gam_user_response(current_user)
                    if is_valid:
                        trafficker_id = str(current_user["id"])
                    else:
                        logger.warning(f"Invalid user response: {error_msg}")

                return jsonify({"success": True, "network_code": network_code_provided, "trafficker_id": trafficker_id})
            except Exception as e:
                return jsonify({"success": False, "error": f"Error getting trafficker ID: {str(e)}"}), 500

        # Get network service and retrieve network info
        network_service = client.GetService("NetworkService", version=GAM_API_VERSION)

        try:
            # Try getAllNetworks first (doesn't require network_code)
            try:
                all_networks = network_service.getAllNetworks()
                if all_networks and len(all_networks) > 0:
                    # Validate all networks
                    validated_networks = []
                    for network in all_networks:
                        is_valid, error_msg = validate_gam_network_response(network)
                        if is_valid:
                            validated_networks.append(
                                {
                                    "network_code": str(network["networkCode"]),
                                    "network_name": network["displayName"],
                                    "network_id": str(network["id"]),
                                }
                            )
                        else:
                            logger.warning(f"Invalid network in list: {error_msg}")

                    if not validated_networks:
                        return (
                            jsonify({"success": False, "error": "No valid networks found in GAM account"}),
                            500,
                        )

                    # If multiple networks, return list for user to choose
                    if len(validated_networks) > 1:
                        return jsonify(
                            {
                                "success": True,
                                "multiple_networks": True,
                                "networks": validated_networks,
                                "network_count": len(validated_networks),
                            }
                        )

                    # Single network - auto-select and get trafficker ID
                    network = all_networks[0]
                    trafficker_id = None
                    try:
                        # Set the network code in the client so we can get user info
                        client.network_code = str(network["networkCode"])
                        user_service = client.GetService("UserService", version=GAM_API_VERSION)
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
                            "network_count": 1,
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
        logger.error(f"Error detecting GAM network for tenant {tenant_id}: {e}", exc_info=True)
        return (
            jsonify({"success": False, "error": f"Error detecting network: {str(e)}"}),
            500,
        )


@gam_bp.route("/configure", methods=["POST"])
@log_admin_action("configure_gam")
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
        auth_method = data.get("auth_method", "oauth")
        network_code = str(data.get("network_code", "")).strip() if data.get("network_code") else None
        refresh_token = data.get("refresh_token", "").strip() if auth_method == "oauth" else None
        service_account_json = (
            data.get("service_account_json", "").strip() if auth_method == "service_account" else None
        )
        trafficker_id = str(data.get("trafficker_id", "")).strip() if data.get("trafficker_id") else None
        order_name_template = data.get("order_name_template", "").strip() or None
        line_item_name_template = data.get("line_item_name_template", "").strip() or None

        # Log what we received (without exposing sensitive credentials)
        logger.info(
            f"GAM config save - auth_method: {auth_method}, network_code: {network_code}, trafficker_id: {trafficker_id}"
        )

        # If network code or trafficker_id not provided, try to auto-detect them
        if not trafficker_id:
            logger.warning(f"No trafficker_id provided for tenant {tenant_id}")

        with get_db_session() as db_session:
            # Get existing tenant
            tenant = db_session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()

            if not tenant:
                return jsonify({"success": False, "error": "Tenant not found"}), 404

            # Get or create adapter config
            from src.core.database.models import AdapterConfig

            adapter_config = db_session.scalars(select(AdapterConfig).filter_by(tenant_id=tenant_id)).first()

            if not adapter_config:
                adapter_config = AdapterConfig(tenant_id=tenant_id, adapter_type="google_ad_manager")
                db_session.add(adapter_config)

            # Update GAM configuration in adapter_config
            adapter_config.gam_network_code = network_code
            adapter_config.gam_auth_method = auth_method
            adapter_config.gam_trafficker_id = trafficker_id
            adapter_config.gam_order_name_template = order_name_template
            adapter_config.gam_line_item_name_template = line_item_name_template

            # Update authentication credentials based on method
            if auth_method == "oauth":
                adapter_config.gam_refresh_token = refresh_token
                adapter_config.gam_service_account_json = None
            elif auth_method == "service_account":
                adapter_config.gam_service_account_json = service_account_json
                adapter_config.gam_refresh_token = None

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
            line_item = db_session.scalars(
                select(GAMLineItem).filter_by(tenant_id=tenant_id, line_item_id=line_item_id)
            ).first()

            if not line_item:
                # Try to fetch from GAM if not in database
                tenant = db_session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()

                if not tenant:
                    return render_template("error.html", error="Tenant not found"), 404

                # Get GAM configuration from adapter_config
                from src.core.database.models import AdapterConfig

                adapter_config = db_session.scalars(select(AdapterConfig).filter_by(tenant_id=tenant_id)).first()

                if not adapter_config or not adapter_config.gam_network_code or not adapter_config.gam_refresh_token:
                    return (
                        render_template(
                            "error.html",
                            error="Please connect your GAM account first. Go to Ad Server settings to configure GAM.",
                        ),
                        400,
                    )

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
                    order = db_session.scalars(
                        select(GAMOrder).filter_by(tenant_id=tenant_id, order_id=line_item.order_id)
                    ).first()
                else:
                    order = None

            else:
                # Get the associated order
                order = (
                    db_session.scalars(
                        select(GAMOrder).filter_by(tenant_id=tenant_id, order_id=line_item.order_id)
                    ).first()
                    if line_item.order_id
                    else None
                )

            # Get tenant for template
            tenant_obj = db_session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
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
            tenant = db_session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()

            if not tenant:
                return jsonify({"error": "Tenant not found"}), 404

            # Get GAM configuration from adapter_config
            from src.core.database.models import AdapterConfig

            adapter_config = db_session.scalars(select(AdapterConfig).filter_by(tenant_id=tenant_id)).first()

            if not adapter_config or not adapter_config.gam_network_code or not adapter_config.gam_refresh_token:
                return (
                    jsonify(
                        {"error": "Please connect your GAM account first. Go to Ad Server settings to configure GAM."}
                    ),
                    400,
                )

            # Create OAuth2 client
            from googleads import oauth2

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

            # Get custom targeting keys
            keys = discovery.discover_custom_targeting()

            return jsonify({"success": True, "keys": keys})

    except Exception as e:
        logger.error(f"Error getting GAM custom targeting keys: {e}")
        return jsonify({"error": str(e)}), 500


@gam_bp.route("/sync-inventory", methods=["POST"])
@log_admin_action("sync_gam_inventory")
@require_tenant_access()
def sync_gam_inventory(tenant_id):
    """Trigger GAM inventory sync for a tenant (background job)."""
    if session.get("role") == "viewer":
        return jsonify({"success": False, "error": "Access denied"}), 403

    try:
        with get_db_session() as db_session:
            # Get tenant and adapter config
            tenant = db_session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
            if not tenant:
                return jsonify({"success": False, "error": "Tenant not found"}), 404

            from src.core.database.models import AdapterConfig, SyncJob

            adapter_config = db_session.scalars(select(AdapterConfig).filter_by(tenant_id=tenant_id)).first()

            if not adapter_config or not adapter_config.gam_network_code or not adapter_config.gam_refresh_token:
                return (
                    jsonify(
                        {
                            "success": False,
                            "error": "Please connect your GAM account before trying to sync inventory. Go to Ad Server settings to configure GAM.",
                        }
                    ),
                    400,
                )

            # Check for existing running sync
            existing_sync = db_session.scalars(
                select(SyncJob).where(
                    SyncJob.tenant_id == tenant_id, SyncJob.status == "running", SyncJob.sync_type == "inventory"
                )
            ).first()

            if existing_sync:
                return (
                    jsonify(
                        {
                            "success": False,
                            "in_progress": True,
                            "sync_id": existing_sync.sync_id,
                            "message": "Sync already in progress",
                        }
                    ),
                    409,
                )

            # Extract config values before starting background thread (avoid session issues)
            gam_network_code = adapter_config.gam_network_code
            gam_refresh_token = adapter_config.gam_refresh_token

            # Create sync job
            sync_id = f"sync_{tenant_id}_{int(datetime.now(UTC).timestamp())}"
            sync_job = SyncJob(
                sync_id=sync_id,
                tenant_id=tenant_id,
                adapter_type="google_ad_manager",
                sync_type="inventory",
                status="pending",
                started_at=datetime.now(UTC),
                triggered_by="admin_ui",
                triggered_by_id=session.get("user_email", "unknown"),
            )
            db_session.add(sync_job)
            db_session.commit()

            # Start background sync (using threading for now - can upgrade to Celery later)
            import threading

            def run_sync():
                try:
                    with get_db_session() as bg_session:
                        # Update status to running
                        bg_sync_job = bg_session.scalars(select(SyncJob).filter_by(sync_id=sync_id)).first()
                        bg_sync_job.status = "running"
                        bg_session.commit()

                        # Create OAuth2 client
                        from googleads import oauth2

                        oauth2_client = oauth2.GoogleRefreshTokenClient(
                            client_id=os.environ.get("GAM_OAUTH_CLIENT_ID"),
                            client_secret=os.environ.get("GAM_OAUTH_CLIENT_SECRET"),
                            refresh_token=gam_refresh_token,
                        )

                        # Create GAM client
                        client = ad_manager.AdManagerClient(
                            oauth2_client, "AdCP Sales Agent", network_code=gam_network_code
                        )

                        # Initialize GAM inventory discovery
                        discovery = GAMInventoryDiscovery(client=client, tenant_id=tenant_id)

                        # Perform full inventory sync
                        result = discovery.sync_all()

                        # Save to database
                        from src.services.gam_inventory_service import GAMInventoryService

                        inventory_service = GAMInventoryService(bg_session)
                        inventory_service._save_inventory_to_db(tenant_id, discovery)

                        # Update sync job with success
                        bg_sync_job.status = "completed"
                        bg_sync_job.completed_at = datetime.now(UTC)
                        bg_sync_job.summary = json.dumps(result)
                        bg_session.commit()

                        logger.info(f"Successfully synced GAM inventory for tenant {tenant_id}")

                except Exception as e:
                    logger.error(f"Error syncing GAM inventory for tenant {tenant_id}: {e}", exc_info=True)
                    try:
                        with get_db_session() as err_session:
                            err_sync_job = err_session.scalars(select(SyncJob).filter_by(sync_id=sync_id)).first()
                            if err_sync_job:
                                err_sync_job.status = "failed"
                                err_sync_job.completed_at = datetime.now(UTC)
                                err_sync_job.error_message = str(e)
                                err_session.commit()
                    except:
                        pass  # Ignore errors in error handling

            # Start background thread
            sync_thread = threading.Thread(target=run_sync, daemon=True)
            sync_thread.start()

            return jsonify(
                {
                    "success": True,
                    "sync_id": sync_id,
                    "message": "Inventory sync started in background",
                    "status": "pending",
                }
            )

    except Exception as e:
        logger.error(f"Error starting GAM inventory sync for tenant {tenant_id}: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@gam_bp.route("/sync-status/<sync_id>", methods=["GET"])
@require_tenant_access()
def get_sync_status(tenant_id, sync_id):
    """Get status of a sync job."""
    try:
        with get_db_session() as db_session:
            from src.core.database.models import SyncJob

            sync_job = db_session.scalars(select(SyncJob).filter_by(sync_id=sync_id, tenant_id=tenant_id)).first()

            if not sync_job:
                return jsonify({"error": "Sync job not found"}), 404

            response = {
                "sync_id": sync_job.sync_id,
                "status": sync_job.status,
                "started_at": sync_job.started_at.isoformat() if sync_job.started_at else None,
                "completed_at": sync_job.completed_at.isoformat() if sync_job.completed_at else None,
            }

            if sync_job.summary:
                try:
                    response["summary"] = json.loads(sync_job.summary)
                except:
                    response["summary"] = sync_job.summary

            if sync_job.error_message:
                response["error"] = sync_job.error_message

            return jsonify(response)

    except Exception as e:
        logger.error(f"Error getting sync status: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@gam_bp.route("/api/line-item/<line_item_id>", methods=["GET"])
@require_tenant_access(api_mode=True)
def get_gam_line_item_api(tenant_id, line_item_id):
    """API endpoint to get GAM line item details."""
    try:
        with get_db_session() as db_session:
            # Get the line item
            line_item = db_session.scalars(
                select(GAMLineItem).filter_by(tenant_id=tenant_id, line_item_id=line_item_id)
            ).first()

            if not line_item:
                # Try to fetch from GAM if not in database
                tenant = db_session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()

                if not tenant:
                    return jsonify({"error": "Tenant not found"}), 404

                # Get GAM configuration from adapter_config
                from src.core.database.models import AdapterConfig

                adapter_config = db_session.scalars(select(AdapterConfig).filter_by(tenant_id=tenant_id)).first()

                if not adapter_config or not adapter_config.gam_network_code or not adapter_config.gam_refresh_token:
                    return (
                        jsonify(
                            {
                                "error": "Please connect your GAM account first. Go to Ad Server settings to configure GAM."
                            }
                        ),
                        400,
                    )

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
