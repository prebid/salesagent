"""API management blueprint."""

import logging
from datetime import UTC, datetime, timedelta

from flask import Blueprint, jsonify, request
from sqlalchemy import select, text

from src.admin.gam_utils import GAMOAuthNotConfigured
from src.admin.utils import require_auth, require_tenant_access
from src.admin.utils.audit_decorator import log_admin_action
from src.core.config import get_settings
from src.core.database.database_session import get_db_session
from src.core.database.models import Product

logger = logging.getLogger(__name__)

# Create blueprint
api_bp = Blueprint("api", __name__)


# Note: /formats/list route moved to format_search.py blueprint
# (registered at /api/formats/list via format_search_bp)
# This avoids route conflicts and uses the proper async registry pattern


@api_bp.route("/health", methods=["GET"])
def api_health():
    """API health check endpoint."""
    with get_db_session() as db_session:
        db_session.execute(text("SELECT 1"))
        return jsonify({"status": "healthy"})


@api_bp.route("/tenant/<tenant_id>/revenue-chart")
@require_tenant_access()
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
        from src.core.database.repositories.principal import PrincipalRepository

        results = PrincipalRepository(db_session, tenant_id).revenue_by_name(
            since=date_start, statuses=["active", "completed"], limit=10
        )

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
    from src.core.logging_config import oauth_structured_logger

    # Check for GAM OAuth credentials using validated configuration
    gam_auth = get_settings().auth
    if not gam_auth.gam_oauth_configured:
        not_configured = GAMOAuthNotConfigured.MESSAGE
        oauth_structured_logger.log_gam_oauth_config_load(success=False, error=not_configured)
        return jsonify(
            {
                "configured": False,
                "error": not_configured,
                "help": "Check GAM_OAUTH_CLIENT_ID and GAM_OAUTH_CLIENT_SECRET environment variables.",
            }
        )
    client_id = gam_auth.gam_oauth_client_id
    client_id_prefix = client_id[:20] + "..." if len(client_id) > 20 else client_id

    # Log configuration check
    oauth_structured_logger.log_gam_oauth_config_load(success=True, client_id_prefix=client_id_prefix)

    # Credentials exist and are validated
    return jsonify(
        {
            "configured": True,
            "client_id_prefix": client_id_prefix,
            "has_secret": True,
            "source": "validated_environment",
        }
    )


@api_bp.route("/tenant/<tenant_id>/products", methods=["GET"])
@require_tenant_access()
def get_tenant_products(tenant_id):
    """API endpoint to list all products for a tenant."""
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


@api_bp.route("/tenant/<tenant_id>/products/suggestions", methods=["GET"])
@require_tenant_access()
def get_product_suggestions(tenant_id):
    """API endpoint to get product suggestions based on industry and criteria."""
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
            or (product.get("price_guidance", {}).get("min", 0) + product.get("price_guidance", {}).get("max", 0)) / 2
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


@api_bp.route("/gam/get-advertisers", methods=["POST"])
@require_auth()
@log_admin_action("gam_get_advertisers")
def gam_get_advertisers():
    """TODO: Extract implementation from admin_ui.py lines 3580-3653.
    GAM advertiser fetching - implement in phase 2."""
    # Placeholder implementation
    return jsonify({"error": "Not yet implemented"}), 501
