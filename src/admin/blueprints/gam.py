"""Google Ad Manager (GAM) integration blueprint."""

import json
import logging
import os
from datetime import UTC, datetime

from flask import Blueprint, jsonify, render_template, request, session
from googleads import ad_manager, oauth2
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
        # Note: service_account_json is only required on initial setup
        # For network code updates, it's optional (already stored in DB)
        service_account_json = data.get("service_account_json", "").strip()
        if service_account_json:
            # Validate JSON structure only if provided
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
        # Use force=True and silent=True to handle empty/malformed requests gracefully
        data = request.get_json(force=True, silent=True)
        if data is None:
            return jsonify({"success": False, "error": "Invalid or empty request body"}), 400

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
        # Try to get JSON - use force=True to handle potential Content-Type issues
        data = request.get_json(force=True, silent=True)

        # Handle None data (request parsing failed)
        if data is None:
            logger.error(
                f"Failed to parse JSON from request for tenant {tenant_id}. "
                f"Content-Type: {request.content_type}, "
                f"Data length: {len(request.data) if request.data else 0}, "
                f"Data preview: {request.data[:200] if request.data else 'empty'}"
            )
            return (
                jsonify(
                    {
                        "success": False,
                        "error": "Invalid JSON in request body. Please ensure you're sending valid JSON.",
                    }
                ),
                400,
            )

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
                # Only update service_account_json if provided (to allow network code updates without resending JSON)
                if service_account_json:
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


# Removed: /sync-inventory endpoint - use /api/tenant/<tenant_id>/inventory/sync instead
# The new endpoint is in inventory.py and uses background_sync_service.py


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

            # Include real-time progress if available
            if sync_job.progress:
                response["progress"] = sync_job.progress

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


@gam_bp.route("/sync-status/latest", methods=["GET"])
@require_tenant_access()
def get_latest_sync_status(tenant_id):
    """Get the latest running sync job for a tenant."""
    try:
        with get_db_session() as db_session:
            from src.core.database.models import SyncJob

            # Find the most recent running sync
            sync_job = db_session.scalars(
                select(SyncJob)
                .where(SyncJob.tenant_id == tenant_id, SyncJob.status == "running", SyncJob.sync_type == "inventory")
                .order_by(SyncJob.started_at.desc())
            ).first()

            if not sync_job:
                return jsonify({"message": "No running sync found"}), 404

            response = {
                "sync_id": sync_job.sync_id,
                "status": sync_job.status,
                "started_at": sync_job.started_at.isoformat() if sync_job.started_at else None,
            }

            # Include real-time progress if available
            if sync_job.progress:
                response["progress"] = sync_job.progress

            return jsonify(response)

    except Exception as e:
        logger.error(f"Error getting latest sync status: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@gam_bp.route("/reset-stuck-sync", methods=["POST"])
@log_admin_action("reset_stuck_gam_sync")
@require_tenant_access()
def reset_stuck_sync(tenant_id):
    """Reset a stuck inventory sync job.

    Marks any running inventory sync as failed and allows a new sync to start.
    Use this when a sync appears to be hanging or frozen.
    """
    if session.get("role") == "viewer":
        return jsonify({"success": False, "error": "Access denied"}), 403

    try:
        with get_db_session() as db_session:
            from src.core.database.models import SyncJob

            # Find running inventory sync
            running_sync = db_session.scalars(
                select(SyncJob).where(
                    SyncJob.tenant_id == tenant_id, SyncJob.status == "running", SyncJob.sync_type == "inventory"
                )
            ).first()

            if not running_sync:
                return jsonify({"success": False, "message": "No running sync found to reset"}), 404

            # Mark as failed
            running_sync.status = "failed"
            running_sync.completed_at = datetime.now(UTC)
            running_sync.error_message = "Manually reset by admin (sync appeared to be stuck)"

            db_session.commit()

            logger.info(
                f"Admin reset stuck sync {running_sync.sync_id} for tenant {tenant_id} "
                f"(started at {running_sync.started_at})"
            )

            return jsonify(
                {
                    "success": True,
                    "message": "Stuck sync has been reset. You can now start a new sync.",
                    "reset_sync_id": running_sync.sync_id,
                }
            )

    except Exception as e:
        logger.error(f"Error resetting stuck sync: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@gam_bp.route("/create-service-account", methods=["POST"])
@log_admin_action("create_gam_service_account")
@require_tenant_access()
def create_service_account(tenant_id):
    """Create a GCP service account for GAM integration.

    This creates a service account in our GCP project, generates credentials,
    and stores them encrypted in the database. The partner then configures
    this service account email in their GAM.
    """
    if session.get("role") == "viewer":
        return jsonify({"success": False, "error": "Access denied"}), 403

    try:
        # Get GCP project ID from environment or configuration
        gcp_project_id = os.environ.get("GCP_PROJECT_ID")
        if not gcp_project_id:
            return (
                jsonify(
                    {
                        "success": False,
                        "error": "GCP_PROJECT_ID not configured. Please set this environment variable.",
                    }
                ),
                500,
            )

        from src.services.gcp_service_account_service import GCPServiceAccountService

        service = GCPServiceAccountService(gcp_project_id=gcp_project_id)

        # Create service account for tenant
        try:
            service_account_email, _ = service.create_service_account_for_tenant(tenant_id=tenant_id)

            return jsonify(
                {
                    "success": True,
                    "service_account_email": service_account_email,
                    "message": "Service account created successfully. Please add this email as a user in your Google Ad Manager with Trafficker role.",
                }
            )

        except ValueError as e:
            # Tenant not found or already has service account
            return jsonify({"success": False, "error": str(e)}), 400

        except Exception as e:
            logger.error(f"Error creating service account for tenant {tenant_id}: {e}", exc_info=True)
            return jsonify({"success": False, "error": f"Failed to create service account: {str(e)}"}), 500

    except Exception as e:
        logger.error(f"Error in create_service_account endpoint: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@gam_bp.route("/get-service-account-email", methods=["GET"])
@require_tenant_access(api_mode=True)
def get_service_account_email(tenant_id):
    """Get the service account email for a tenant.

    Returns the service account email if one has been created for this tenant.
    """
    try:
        gcp_project_id = os.environ.get("GCP_PROJECT_ID")
        if not gcp_project_id:
            return jsonify({"error": "GCP_PROJECT_ID not configured"}), 500

        from src.services.gcp_service_account_service import GCPServiceAccountService

        service = GCPServiceAccountService(gcp_project_id=gcp_project_id)
        email = service.get_service_account_email(tenant_id)

        if email:
            return jsonify({"success": True, "service_account_email": email})
        else:
            return jsonify({"success": True, "service_account_email": None, "message": "No service account created"})

    except Exception as e:
        logger.error(f"Error getting service account email: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@gam_bp.route("/test-connection", methods=["POST"])
@log_admin_action("test_gam_connection")
@require_tenant_access()
def test_gam_connection(tenant_id):
    """Test GAM connection with refresh token or service account and fetch available resources.

    Request body:
        refresh_token: Optional refresh token (for OAuth authentication)
        auth_method: Optional "oauth" or "service_account" (default: infer from request body)

    If no refresh_token is provided, tries to use service account credentials from tenant's adapter config.
    """
    if session.get("role") == "viewer":
        return jsonify({"success": False, "error": "Access denied"}), 403

    try:
        # Use force=True and silent=True to handle empty/malformed requests gracefully
        data = request.get_json(force=True, silent=True) or {}
        refresh_token = data.get("refresh_token")
        auth_method = data.get("auth_method")

        # If no explicit auth_method, infer from what's provided
        if not auth_method:
            if refresh_token:
                auth_method = "oauth"
            else:
                auth_method = "service_account"

        # Get OAuth credentials from environment variables
        client_id = os.environ.get("GAM_OAUTH_CLIENT_ID")
        client_secret = os.environ.get("GAM_OAUTH_CLIENT_SECRET")

        oauth2_client = None

        if auth_method == "oauth":
            # OAuth flow with refresh token
            if not refresh_token:
                return jsonify({"error": "Refresh token is required for OAuth authentication"}), 400

            if not client_id or not client_secret:
                return (
                    jsonify(
                        {
                            "error": "GAM OAuth credentials not configured. Please set GAM_OAUTH_CLIENT_ID and GAM_OAUTH_CLIENT_SECRET environment variables."
                        }
                    ),
                    400,
                )

            # Test by creating credentials and making a simple API call
            # Create GoogleAds OAuth2 client with refresh token
            oauth2_client = oauth2.GoogleRefreshTokenClient(
                client_id=client_id,
                client_secret=client_secret,
                refresh_token=refresh_token,
            )

            # Test if credentials are valid by trying to refresh
            try:
                oauth2_client.Refresh()
            except Exception as e:
                return jsonify({"error": f"Invalid refresh token: {str(e)}"}), 400

        elif auth_method == "service_account":
            # Service account flow
            with get_db_session() as db_session:
                from src.core.database.models import AdapterConfig

                adapter_config = db_session.scalars(select(AdapterConfig).filter_by(tenant_id=tenant_id)).first()

                if not adapter_config or not adapter_config.gam_service_account_json:
                    return (
                        jsonify(
                            {
                                "error": "No service account configured for this tenant. Please configure service account credentials first."
                            }
                        ),
                        400,
                    )

                # Check if network code is configured (required for service account)
                network_code = adapter_config.gam_network_code
                if not network_code:
                    return (
                        jsonify(
                            {
                                "error": "Network code is required for service account authentication. Please configure the GAM network code first."
                            }
                        ),
                        400,
                    )

                # Parse service account JSON
                try:
                    import json as json_lib

                    service_account_info = json_lib.loads(adapter_config.gam_service_account_json)
                except json_lib.JSONDecodeError as e:
                    return jsonify({"error": f"Invalid service account JSON: {str(e)}"}), 400

                # Create credentials from service account (in-memory, no temp file needed)
                from google.oauth2 import service_account

                credentials = service_account.Credentials.from_service_account_info(
                    service_account_info,
                    scopes=["https://www.googleapis.com/auth/dfp"],
                )
                # Wrap in GoogleCredentialsClient for AdManagerClient compatibility
                oauth2_client = oauth2.GoogleCredentialsClient(credentials)

        else:
            return jsonify({"error": f"Invalid auth_method: {auth_method}"}), 400

        # Initialize GAM client to get network info
        # Note: For service account auth, network_code is required and retrieved from adapter_config
        # For OAuth, we can call getAllNetworks without a network_code
        if auth_method == "service_account":
            client = ad_manager.AdManagerClient(oauth2_client, "AdCP-Sales-Agent-Setup", network_code=network_code)
        else:
            client = ad_manager.AdManagerClient(oauth2_client, "AdCP-Sales-Agent-Setup")

        # Get network service
        network_service = client.GetService("NetworkService", version=GAM_API_VERSION)

        # Get all networks user has access to
        networks = []
        try:
            # Service account auth with network_code already set - use getCurrentNetwork
            if auth_method == "service_account":
                logger.info("Using service account auth - calling getCurrentNetwork()")
                current_network = network_service.getCurrentNetwork()
                logger.info(f"getCurrentNetwork() returned: {current_network}")
                networks = [
                    {
                        "id": current_network["id"],
                        "displayName": current_network["displayName"],
                        "networkCode": current_network["networkCode"],
                    }
                ]
            else:
                # OAuth - try getAllNetworks first
                logger.info("Using OAuth - attempting to call getAllNetworks()")
                all_networks = network_service.getAllNetworks()
                logger.info(f"getAllNetworks() returned: {all_networks}")
                if all_networks:
                    logger.info(f"Processing {len(all_networks)} networks")
                    for network in all_networks:
                        logger.info(f"Network data: {network}")
                        networks.append(
                            {
                                "id": network["id"],
                                "displayName": network["displayName"],
                                "networkCode": network["networkCode"],
                            }
                        )
                else:
                    logger.info("getAllNetworks() returned empty/None")
        except AttributeError as e:
            # getAllNetworks might not be available, fall back to getCurrentNetwork
            logger.info(f"getAllNetworks not available (AttributeError: {e}), falling back to getCurrentNetwork")
            try:
                current_network = network_service.getCurrentNetwork()
                logger.info(f"getCurrentNetwork() returned: {current_network}")
                networks = [
                    {
                        "id": current_network["id"],
                        "displayName": current_network["displayName"],
                        "networkCode": current_network["networkCode"],
                    }
                ]
            except Exception as e:
                logger.error(f"Failed to get network info: {e}")
                networks = []
        except Exception as e:
            logger.error(f"Failed to get networks: {e}")
            logger.exception("Full exception details:")
            networks = []

        result = {
            "success": True,
            "message": "Successfully connected to Google Ad Manager",
            "networks": networks,
        }

        # If we got a network, fetch companies and users
        if networks:
            try:
                # Reinitialize client with network code for subsequent calls
                network_code = networks[0]["networkCode"]
                logger.info(f"Reinitializing client with network code: {network_code}")

                client = ad_manager.AdManagerClient(oauth2_client, "AdCP-Sales-Agent-Setup", network_code=network_code)

                # Get company service for advertisers
                company_service = client.GetService("CompanyService", version=GAM_API_VERSION)

                # Build a statement to get advertisers
                from googleads import ad_manager as gam_utils

                statement_builder = gam_utils.StatementBuilder()
                statement_builder.Where("type = :type")
                statement_builder.WithBindVariable("type", "ADVERTISER")
                statement_builder.Limit(100)

                # Get companies
                logger.info("Calling getCompaniesByStatement for ADVERTISER companies")
                response = company_service.getCompaniesByStatement(statement_builder.ToStatement())
                logger.info(f"getCompaniesByStatement response: {response}")

                companies = []
                if response and hasattr(response, "results"):
                    logger.info(f"Found {len(response.results)} companies")
                    for company in response.results:
                        logger.info(f"Company: id={company.id}, name={company.name}, type={company.type}")
                        companies.append(
                            {
                                "id": company.id,
                                "name": company.name,
                                "type": company.type,
                            }
                        )
                else:
                    logger.info("No companies found in response")

                result["companies"] = companies

                # Get current user info
                user_service = client.GetService("UserService", version=GAM_API_VERSION)
                current_user = user_service.getCurrentUser()
                result["current_user"] = {
                    "id": current_user.id,
                    "name": current_user.name,
                    "email": current_user.email,
                }

            except Exception as e:
                # It's okay if we can't fetch companies/users
                result["warning"] = f"Connected but couldn't fetch all resources: {str(e)}"

        return jsonify(result)

    except Exception as e:
        logger.error(f"Error testing GAM connection: {e}", exc_info=True)
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
