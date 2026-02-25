"""API management blueprint."""

import logging
from datetime import UTC, datetime, timedelta

from flask import Blueprint, jsonify, request
from sqlalchemy import func, select, text

from src.adapters.gam.utils.constants import GAM_API_VERSION
from src.admin.utils import require_auth
from src.admin.utils.audit_decorator import log_admin_action
from src.core.database.database_session import get_db_session
from src.core.database.models import MediaBuy, Principal, Product

logger = logging.getLogger(__name__)

# Create blueprint
api_bp = Blueprint("api", __name__)


# Note: /formats/list route moved to format_search.py blueprint
# (registered at /api/formats/list via format_search_bp)
# This avoids route conflicts and uses the proper async registry pattern


@api_bp.route("/health", methods=["GET"])
def api_health():
    """API health check endpoint."""
    try:
        with get_db_session() as db_session:
            db_session.execute(text("SELECT 1"))
            return jsonify({"status": "healthy"})
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return jsonify({"status": "unhealthy"}), 500


@api_bp.route("/tenant/<tenant_id>/revenue-chart")
@require_auth()
def revenue_chart_api(tenant_id):
    """API endpoint for revenue chart data."""
    period = request.args.get("period", "7d")

    # Parse period
    if period == "7d":
        days = 7
    elif period == "30d":
        days = 30
    elif period == "90d":
        days = 90
    else:
        days = 7

    with get_db_session() as db_session:
        # Calculate date range
        date_start = datetime.now(UTC) - timedelta(days=days)

        # Query revenue by principal
        stmt = (
            select(Principal.name, func.sum(MediaBuy.budget).label("revenue"))
            .join(
                MediaBuy,
                (MediaBuy.principal_id == Principal.principal_id) & (MediaBuy.tenant_id == Principal.tenant_id),
            )
            .filter(
                MediaBuy.tenant_id == tenant_id,
                MediaBuy.created_at >= date_start,
                MediaBuy.status.in_(["active", "completed"]),
            )
            .group_by(Principal.name)
            .order_by(func.sum(MediaBuy.budget).desc())
            .limit(10)
        )
        results = db_session.execute(stmt).all()

        labels = []
        values = []
        for name, revenue in results:
            labels.append(name or "Unknown")
            values.append(float(revenue) if revenue else 0.0)

        return jsonify({"labels": labels, "values": values})


@api_bp.route("/oauth/status", methods=["GET"])
@require_auth()
def oauth_status():
    """Check if OAuth credentials are properly configured for GAM."""
    try:
        # Check for GAM OAuth credentials using validated configuration
        try:
            from src.core.config import get_gam_oauth_config
            from src.core.logging_config import oauth_structured_logger

            gam_config = get_gam_oauth_config()
            client_id = gam_config.client_id
            client_secret = gam_config.client_secret

            # Log configuration check
            oauth_structured_logger.log_gam_oauth_config_load(
                success=True, client_id_prefix=client_id[:20] + "..." if len(client_id) > 20 else client_id
            )

            # Credentials exist and are validated
            return jsonify(
                {
                    "configured": True,
                    "client_id_prefix": client_id[:20] + "..." if len(client_id) > 20 else client_id,
                    "has_secret": True,
                    "source": "validated_environment",
                }
            )
        except Exception as config_error:
            # Configuration validation failed
            oauth_structured_logger.log_gam_oauth_config_load(success=False, error=str(config_error))
            return jsonify(
                {
                    "configured": False,
                    "error": f"GAM OAuth configuration error: {str(config_error)}",
                    "help": "Check GAM_OAUTH_CLIENT_ID and GAM_OAUTH_CLIENT_SECRET environment variables.",
                }
            )

    except Exception as e:
        logger.error(f"Error checking OAuth status: {e}")
        return (
            jsonify(
                {
                    "configured": False,
                    "error": f"Error checking OAuth configuration: {str(e)}",
                }
            ),
            500,
        )


@api_bp.route("/tenant/<tenant_id>/products", methods=["GET"])
@require_auth()
def get_tenant_products(tenant_id):
    """API endpoint to list all products for a tenant."""
    try:
        with get_db_session() as db_session:
            from sqlalchemy import select

            from src.core.database.models import Product

            stmt = select(Product).filter_by(tenant_id=tenant_id).order_by(Product.name)
            products = db_session.scalars(stmt).all()

            products_data = []
            for product in products:
                products_data.append(
                    {
                        "product_id": product.product_id,
                        "name": product.name,
                        "description": product.description or "",
                        "delivery_type": product.delivery_type,
                    }
                )

            return jsonify({"products": products_data})

    except Exception as e:
        logger.error(f"Error getting products for tenant {tenant_id}: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@api_bp.route("/tenant/<tenant_id>/products/suggestions", methods=["GET"])
@require_auth()
def get_product_suggestions(tenant_id):
    """API endpoint to get product suggestions based on industry and criteria."""
    try:
        from src.services.default_products import (
            get_default_products,
            get_industry_specific_products,
        )

        # Get query parameters
        industry = request.args.get("industry")
        include_standard = request.args.get("include_standard", "true").lower() == "true"
        delivery_type = request.args.get("delivery_type")  # 'guaranteed', 'non_guaranteed', or None for all
        max_cpm = request.args.get("max_cpm", type=float)
        formats = request.args.getlist("formats")  # Can specify multiple format IDs

        # Get suggestions
        suggestions = []

        # Get industry-specific products if industry specified
        if industry:
            industry_products = get_industry_specific_products(industry)
            suggestions.extend(industry_products)
        elif include_standard:
            # If no industry specified but standard requested, get default products
            suggestions.extend(get_default_products())

        # Filter suggestions based on criteria
        filtered_suggestions = []
        for product in suggestions:
            # Filter by delivery type
            if delivery_type and product.get("delivery_type") != delivery_type:
                continue

            # Filter by max CPM
            if max_cpm:
                if product.get("cpm") and product["cpm"] > max_cpm:
                    continue
                elif product.get("price_guidance"):
                    if product["price_guidance"]["min"] > max_cpm:
                        continue

            # Filter by formats
            if formats:
                product_formats = set(product.get("formats", []))
                requested_formats = set(formats)
                if not product_formats.intersection(requested_formats):
                    continue

            filtered_suggestions.append(product)

        # Sort suggestions by relevance
        # Prioritize: 1) Industry-specific, 2) Lower CPM, 3) More formats
        def sort_key(product):
            is_industry_specific = product["product_id"] not in [p["product_id"] for p in get_default_products()]
            avg_cpm = (
                product.get("cpm", 0)
                or (product.get("price_guidance", {}).get("min", 0) + product.get("price_guidance", {}).get("max", 0))
                / 2
            )
            format_count = len(product.get("formats", []))
            return (-int(is_industry_specific), avg_cpm, -format_count)

        filtered_suggestions.sort(key=sort_key)

        # Check existing products to mark which are already created
        with get_db_session() as db_session:
            stmt = select(Product.product_id).filter_by(tenant_id=tenant_id)
            existing_products = db_session.scalars(stmt).all()
            existing_ids = {product[0] for product in existing_products}

        # Add metadata to suggestions
        for suggestion in filtered_suggestions:
            suggestion["already_exists"] = suggestion["product_id"] in existing_ids
            suggestion["is_industry_specific"] = suggestion["product_id"] not in [
                p["product_id"] for p in get_default_products()
            ]

            # Calculate match score (0-100)
            score = 100
            if delivery_type and suggestion.get("delivery_type") == delivery_type:
                score += 20
            if formats:
                matching_formats = len(set(suggestion.get("formats", [])).intersection(set(formats)))
                score += matching_formats * 10
            if industry and suggestion["is_industry_specific"]:
                score += 30

            suggestion["match_score"] = min(score, 100)

        return jsonify(
            {
                "suggestions": filtered_suggestions,
                "total_count": len(filtered_suggestions),
                "criteria": {
                    "industry": industry,
                    "delivery_type": delivery_type,
                    "max_cpm": max_cpm,
                    "formats": formats,
                },
            }
        )

    except Exception as e:
        logger.error(f"Error getting product suggestions: {e}")
        return jsonify({"error": str(e)}), 500


@api_bp.route("/gam/get-advertisers", methods=["POST"])
@require_auth()
@log_admin_action("gam_get_advertisers")
def gam_get_advertisers():
    """TODO: Extract implementation from admin_ui.py lines 3580-3653.
    GAM advertiser fetching - implement in phase 2."""
    # Placeholder implementation
    return jsonify({"error": "Not yet implemented"}), 501


@api_bp.route("/gam/test-connection", methods=["POST"])
@require_auth()
@log_admin_action("test_gam_connection")
def test_gam_connection():
    """Test GAM connection with refresh token and fetch available resources."""
    try:
        refresh_token = request.json.get("refresh_token")
        if not refresh_token:
            return jsonify({"error": "Refresh token is required"}), 400

        # Get OAuth credentials from environment variables
        import os

        client_id = os.environ.get("GAM_OAUTH_CLIENT_ID")
        client_secret = os.environ.get("GAM_OAUTH_CLIENT_SECRET")

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
        from googleads import ad_manager, oauth2

        # Create GoogleAds OAuth2 client with refresh token
        oauth2_client = oauth2.GoogleRefreshTokenClient(
            client_id=client_id,
            client_secret=client_secret,
            refresh_token=refresh_token,
        )

        # Test if credentials are valid by trying to refresh
        try:
            # This will attempt to refresh the token
            oauth2_client.Refresh()
        except Exception as e:
            return jsonify({"error": f"Invalid refresh token: {str(e)}"}), 400

        # Initialize GAM client to get network info
        # Note: We don't need to specify network_code for getAllNetworks call
        client = ad_manager.AdManagerClient(oauth2_client, "AdCP-Sales-Agent-Setup")

        # Get network service
        network_service = client.GetService("NetworkService", version=GAM_API_VERSION)

        # Get all networks user has access to
        try:
            # Try to get all networks first
            logger.info("Attempting to call getAllNetworks()")
            all_networks = network_service.getAllNetworks()
            logger.info(f"getAllNetworks() returned: {all_networks}")
            networks = []
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

                # Use GoogleAdManager adapter to fetch advertisers (eliminates code duplication)
                from src.adapters.google_ad_manager import GoogleAdManager
                from src.core.schemas import Principal

                # Create mock principal for adapter initialization (not used for get_advertisers)
                mock_principal = Principal(
                    principal_id="system",
                    name="System",
                    platform_mappings={
                        "google_ad_manager": {
                            "advertiser_id": "system_temp",
                            "advertiser_name": "System (temp)",
                        }
                    },
                )

                # Build GAM config from OAuth credentials
                gam_config = {
                    "oauth_credentials": {
                        "client_id": oauth_client_id,
                        "client_secret": oauth_client_secret,
                        "refresh_token": refresh_token,
                    }
                }

                # Initialize adapter
                adapter = GoogleAdManager(
                    config=gam_config,
                    principal=mock_principal,
                    network_code=network_code,
                    advertiser_id=None,
                    trafficker_id=None,
                    dry_run=False,
                    tenant_id=tenant_id,
                )

                # Fetch ALL advertisers using shared implementation (with pagination)
                companies = adapter.get_advertisers(fetch_all=True)
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
        return jsonify({"error": str(e)}), 500
