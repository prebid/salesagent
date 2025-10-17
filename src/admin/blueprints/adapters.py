"""Adapters management blueprint."""

import logging

from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for
from sqlalchemy import select
from sqlalchemy.orm import attributes

from src.admin.utils import require_tenant_access
from src.admin.utils.audit_decorator import log_admin_action
from src.core.database.database_session import get_db_session
from src.core.database.models import Product

logger = logging.getLogger(__name__)

# Create blueprint
adapters_bp = Blueprint("adapters", __name__)


@adapters_bp.route("/adapters/mock/config/<tenant_id>/<product_id>", methods=["GET", "POST"])
@require_tenant_access()
def mock_config(tenant_id, product_id, **kwargs):
    """Configure mock adapter settings for a product."""
    with get_db_session() as session:
        stmt = select(Product).filter_by(tenant_id=tenant_id, product_id=product_id)
        product = session.scalars(stmt).first()

        if not product:
            flash("Product not found", "error")
            return redirect(url_for("products.list_products", tenant_id=tenant_id))

        if request.method == "POST":
            # Handle form submission to update mock config
            try:
                config = product.implementation_config or {}

                # Traffic simulation
                config["daily_impressions"] = int(request.form.get("daily_impressions", 100000))
                config["fill_rate"] = float(request.form.get("fill_rate", 85))
                config["ctr"] = float(request.form.get("ctr", 0.5))
                config["viewability_rate"] = float(request.form.get("viewability_rate", 70))

                # Performance simulation
                config["latency_ms"] = int(request.form.get("latency_ms", 50))
                config["error_rate"] = float(request.form.get("error_rate", 0.1))

                # Test scenarios
                config["test_mode"] = request.form.get("test_mode", "normal")
                config["price_variance"] = float(request.form.get("price_variance", 10))
                config["seasonal_factor"] = float(request.form.get("seasonal_factor", 1.0))

                # Delivery simulation
                config["delivery_simulation"] = {
                    "enabled": "delivery_simulation_enabled" in request.form,
                    "time_acceleration": int(request.form.get("time_acceleration", 3600)),
                    "update_interval_seconds": float(request.form.get("update_interval_seconds", 1.0)),
                }

                # Note: Creative formats are managed in product.formats (via add/edit product page)
                # NOT in implementation_config - removing format handling to avoid duplication

                # Debug settings
                config["verbose_logging"] = "verbose_logging" in request.form
                config["predictable_ids"] = "predictable_ids" in request.form

                product.implementation_config = config
                attributes.flag_modified(product, "implementation_config")
                session.commit()

                flash("Mock adapter configuration saved successfully!", "success")
                return redirect(url_for("adapters.mock_config", tenant_id=tenant_id, product_id=product_id))
            except Exception as e:
                logger.error(f"Error saving mock config: {e}", exc_info=True)
                flash(f"Error saving configuration: {str(e)}", "error")

        # GET request - render template with product config
        config = product.implementation_config or {}

        return render_template(
            "adapters/mock_product_config.html",
            tenant_id=tenant_id,
            product=product,
            config=config,
        )


@adapters_bp.route("/adapter/<adapter_name>/inventory_schema", methods=["GET"])
@require_tenant_access()
def adapter_adapter_name_inventory_schema(tenant_id, **kwargs):
    """TODO: Extract implementation from admin_ui.py."""
    # Placeholder implementation
    return jsonify({"error": "Not yet implemented"}), 501


@adapters_bp.route("/setup_adapter", methods=["POST"])
@log_admin_action("setup_adapter")
@require_tenant_access()
def setup_adapter(tenant_id, **kwargs):
    """TODO: Extract implementation from admin_ui.py."""
    # Placeholder implementation
    return jsonify({"error": "Not yet implemented"}), 501
