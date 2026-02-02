"""Adapters management blueprint."""

import logging

from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import attributes

from src.adapters import get_adapter_schemas
from src.admin.utils import require_tenant_access
from src.admin.utils.audit_decorator import log_admin_action
from src.core.database.database_session import get_db_session
from src.core.database.models import AdapterConfig, Product

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

                # Helper function to safely parse and validate numeric values
                def parse_int(field_name, default, min_val=None, max_val=None):
                    try:
                        value = int(request.form.get(field_name, default))
                        if min_val is not None and value < min_val:
                            raise ValueError(f"{field_name} must be at least {min_val}")
                        if max_val is not None and value > max_val:
                            raise ValueError(f"{field_name} must be at most {max_val}")
                        return value
                    except (ValueError, TypeError) as e:
                        raise ValueError(f"Invalid value for {field_name}: {e}")

                def parse_float(field_name, default, min_val=None, max_val=None):
                    try:
                        value = float(request.form.get(field_name, default))
                        if min_val is not None and value < min_val:
                            raise ValueError(f"{field_name} must be at least {min_val}")
                        if max_val is not None and value > max_val:
                            raise ValueError(f"{field_name} must be at most {max_val}")
                        return value
                    except (ValueError, TypeError) as e:
                        raise ValueError(f"Invalid value for {field_name}: {e}")

                # Traffic simulation (with validation)
                config["daily_impressions"] = parse_int("daily_impressions", 100000, min_val=0)
                config["fill_rate"] = parse_float("fill_rate", 85, min_val=0, max_val=100)
                config["ctr"] = parse_float("ctr", 0.5, min_val=0, max_val=100)
                config["viewability_rate"] = parse_float("viewability_rate", 70, min_val=0, max_val=100)

                # Performance simulation (with validation)
                config["latency_ms"] = parse_int("latency_ms", 50, min_val=0, max_val=60000)
                config["error_rate"] = parse_float("error_rate", 0.1, min_val=0, max_val=100)

                # Test scenarios (validated choices)
                test_mode = request.form.get("test_mode", "normal")
                valid_modes = ["normal", "high_demand", "degraded", "outage"]
                if test_mode not in valid_modes:
                    raise ValueError(f"Invalid test_mode: {test_mode}")
                config["test_mode"] = test_mode
                config["price_variance"] = parse_float("price_variance", 10, min_val=0, max_val=100)
                config["seasonal_factor"] = parse_float("seasonal_factor", 1.0, min_val=0.1, max_val=10.0)

                # Delivery simulation (with validation)
                config["delivery_simulation"] = {
                    "enabled": "delivery_simulation_enabled" in request.form,
                    "time_acceleration": parse_int("time_acceleration", 3600, min_val=1, max_val=86400),
                    "update_interval_seconds": parse_float("update_interval_seconds", 1.0, min_val=0.1, max_val=60),
                }

                # Note: Creative formats are managed in product.format_ids (via add/edit product page)
                # NOT in implementation_config - removing format handling to avoid duplication

                # Debug settings (boolean - safe)
                config["verbose_logging"] = "verbose_logging" in request.form
                config["predictable_ids"] = "predictable_ids" in request.form

                product.implementation_config = config
                attributes.flag_modified(product, "implementation_config")
                session.commit()

                flash("Mock adapter configuration saved successfully!", "success")
                return redirect(url_for("adapters.mock_config", tenant_id=tenant_id, product_id=product_id))
            except ValueError as e:
                logger.warning(f"Validation error in mock config: {e}")
                flash(f"Invalid configuration: {str(e)}", "error")
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


@adapters_bp.route("/api/adapter-config", methods=["POST"])
@log_admin_action("update_adapter_config")
@require_tenant_access()
def save_adapter_config(tenant_id, **kwargs):
    """Save adapter connection configuration.

    Validates config using Pydantic schema, then writes to both:
    - Legacy columns (for backwards compatibility)
    - config_json column (for schema-driven access)

    Request body:
    {
        "adapter_type": "mock" | "google_ad_manager" | etc,
        "config": { ... adapter-specific config ... }
    }
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "No JSON data provided"}), 400

        adapter_type = data.get("adapter_type")
        config_data = data.get("config", {})

        if not adapter_type:
            return jsonify({"success": False, "error": "adapter_type is required"}), 400

        # Get schema for validation
        schemas = get_adapter_schemas(adapter_type)
        if schemas and schemas.connection_config:
            try:
                # Validate config against schema
                validated_config = schemas.connection_config(**config_data)
                config_data = validated_config.model_dump()
            except ValidationError as e:
                return jsonify({"success": False, "error": f"Validation error: {e}"}), 400

        with get_db_session() as session:
            stmt = select(AdapterConfig).filter_by(tenant_id=tenant_id)
            adapter_config = session.scalars(stmt).first()

            if not adapter_config:
                # Create new adapter config
                adapter_config = AdapterConfig(
                    tenant_id=tenant_id,
                    adapter_type=adapter_type,
                    config_json=config_data,
                )
                session.add(adapter_config)
            else:
                # Update existing
                adapter_config.adapter_type = adapter_type
                adapter_config.config_json = config_data
                attributes.flag_modified(adapter_config, "config_json")

            # Write to legacy columns for backwards compatibility
            if adapter_type == "mock":
                adapter_config.mock_dry_run = config_data.get("dry_run", False)
                adapter_config.mock_manual_approval_required = config_data.get("manual_approval_required", False)
            # Note: GAM, Kevel, Triton will be added as their schemas are created

            session.commit()
            logger.info(f"Saved adapter config for tenant {tenant_id}: {adapter_type}")

        return jsonify({"success": True, "adapter_type": adapter_type})

    except Exception as e:
        logger.error(f"Error saving adapter config: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@adapters_bp.route("/api/adapters/<adapter_type>/capabilities", methods=["GET"])
@require_tenant_access()
def get_adapter_capabilities(adapter_type, tenant_id, **kwargs):
    """Get capabilities for an adapter type.

    Returns the AdapterCapabilities for UI to show/hide sections.
    """
    from dataclasses import asdict

    schemas = get_adapter_schemas(adapter_type)
    if not schemas:
        return jsonify({"error": f"Unknown adapter type: {adapter_type}"}), 404

    if schemas.capabilities:
        return jsonify(asdict(schemas.capabilities))
    else:
        return jsonify({})
