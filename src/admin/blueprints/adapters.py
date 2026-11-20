"""Adapters management blueprint."""

import logging

from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import attributes

from src.adapters import get_adapter_schemas
from src.admin.form_validation import form_number
from src.admin.utils import require_tenant_access
from src.admin.utils.audit_decorator import log_admin_action
from src.core.database.database_session import get_db_session
from src.core.database.models import AdapterConfig, Product
from src.core.operator_errors import FormIssue, InvalidFormInput

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
            # A value the form refuses raises InvalidFormInput, which the admin error handler
            # answers on this page; the session closes without a commit, so nothing is stored.
            config = product.implementation_config or {}

            form = request.form

            # Traffic simulation (with validation)
            config["daily_impressions"] = form_number(
                int, "daily_impressions", form.get("daily_impressions", 100000), 0
            )
            config["fill_rate"] = form_number(float, "fill_rate", form.get("fill_rate", 85.0), 0.0, 100.0)
            config["ctr"] = form_number(float, "ctr", form.get("ctr", 0.5), 0.0, 100.0)
            config["viewability_rate"] = form_number(
                float, "viewability_rate", form.get("viewability_rate", 70.0), 0.0, 100.0
            )

            # Performance simulation (with validation)
            config["latency_ms"] = form_number(int, "latency_ms", form.get("latency_ms", 50), 0, 60000)
            config["error_rate"] = form_number(float, "error_rate", form.get("error_rate", 0.1), 0.0, 100.0)

            # Test scenarios (validated choices)
            test_mode = request.form.get("test_mode", "normal")
            valid_modes = ["normal", "high_demand", "degraded", "outage"]
            if test_mode not in valid_modes:
                raise InvalidFormInput(FormIssue("test_mode", f"test_mode must be one of {', '.join(valid_modes)}"))
            config["test_mode"] = test_mode
            config["price_variance"] = form_number(
                float, "price_variance", form.get("price_variance", 10.0), 0.0, 100.0
            )
            config["seasonal_factor"] = form_number(
                float, "seasonal_factor", form.get("seasonal_factor", 1.0), 0.1, 10.0
            )

            # Delivery simulation (with validation)
            config["delivery_simulation"] = {
                "enabled": "delivery_simulation_enabled" in form,
                "time_acceleration": form_number(
                    int, "time_acceleration", form.get("time_acceleration", 3600), 1, 86400
                ),
                "update_interval_seconds": form_number(
                    float, "update_interval_seconds", form.get("update_interval_seconds", 1.0), 0.1, 60.0
                ),
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


@adapters_bp.route("/api/tenant/<tenant_id>/adapter-config", methods=["POST"])
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
    data = request.get_json()
    if not data:
        return jsonify({"success": False, "error": "No JSON data provided"}), 400

    adapter_type = data.get("adapter_type")
    config_data = data.get("config", {})

    if not adapter_type:
        return jsonify({"success": False, "error": "adapter_type is required"}), 400

    # Validate config against adapter schema (keeps Pydantic model flowing)
    schemas = get_adapter_schemas(adapter_type)
    validated_config = None
    if schemas and schemas.connection_config:
        try:
            validated_config = schemas.connection_config(**config_data)
        except ValidationError as e:
            # Locations and fixed sentences only: pydantic's own text echoes the submitted
            # values, which can be credentials.
            raise InvalidFormInput(*FormIssue.from_validation_error("config", e, config_data)) from e

    # Use the validated model for DB write (JSONType + engine json_serializer
    # handle BaseModel serialization). Fall back to raw dict if no schema.
    config_value = validated_config if validated_config is not None else config_data

    with get_db_session() as session:
        stmt = select(AdapterConfig).filter_by(tenant_id=tenant_id)
        adapter_config = session.scalars(stmt).first()

        if not adapter_config:
            adapter_config = AdapterConfig(
                tenant_id=tenant_id,
                adapter_type=adapter_type,
                config_json=config_value,
            )
            session.add(adapter_config)
        else:
            adapter_config.adapter_type = adapter_type
            adapter_config.config_json = config_value
            attributes.flag_modified(adapter_config, "config_json")

        # Write to legacy columns for backwards compatibility
        if adapter_type == "mock" and validated_config is not None:
            adapter_config.mock_manual_approval_required = getattr(validated_config, "manual_approval_required", False)
        # Note: GAM, Kevel, Triton will be added as their schemas are created

        session.commit()
        logger.info(f"Saved adapter config for tenant {tenant_id}: {adapter_type}")

    return jsonify({"success": True, "adapter_type": adapter_type})


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


# Broadstreet-specific endpoints


@adapters_bp.route("/api/tenant/<tenant_id>/adapters/broadstreet/test-connection", methods=["POST"])
@require_tenant_access()
def test_broadstreet_connection(tenant_id, **kwargs):
    """Test Broadstreet API connection with provided credentials."""
    data = request.get_json()
    if not data:
        return jsonify({"success": False, "error": "No JSON data provided"}), 400

    network_id = data.get("network_id")
    api_key = data.get("api_key")

    if not network_id or not api_key:
        return jsonify({"success": False, "error": "network_id and api_key are required"}), 400

    # Test connection by fetching network info
    from src.adapters.broadstreet import BroadstreetClient

    client = BroadstreetClient(access_token=api_key, network_id=network_id)
    network_info = client.get_network()

    if network_info:
        return jsonify(
            {
                "success": True,
                "network_name": network_info.get("name", "Unknown"),
                "network_id": network_id,
            }
        )
    else:
        return jsonify({"success": False, "error": "Could not retrieve network information"})


@adapters_bp.route("/api/tenant/<tenant_id>/adapters/broadstreet/zones", methods=["GET"])
@require_tenant_access()
def list_broadstreet_zones(tenant_id, **kwargs):
    """List available zones from Broadstreet for the tenant's configured account."""
    # Get adapter config for this tenant
    with get_db_session() as session:
        stmt = select(AdapterConfig).filter_by(tenant_id=tenant_id)
        adapter_config = session.scalars(stmt).first()

        if not adapter_config:
            return jsonify({"zones": [], "error": "No adapter configured"}), 200

        # Get Broadstreet credentials from config_json or legacy columns
        config = adapter_config.config_json or {}
        network_id = config.get("network_id") or getattr(adapter_config, "broadstreet_network_id", None)
        api_key = config.get("api_key") or getattr(adapter_config, "broadstreet_api_key", None)

        if not network_id or not api_key:
            return jsonify({"zones": [], "error": "Broadstreet not configured"}), 200

        # Fetch zones from Broadstreet
        from src.adapters.broadstreet import BroadstreetClient

        client = BroadstreetClient(access_token=api_key, network_id=network_id)
        zones = client.get_zones()

        return jsonify(
            {
                "zones": [
                    {"id": str(zone.get("id")), "name": zone.get("name", f"Zone {zone.get('id')}")} for zone in zones
                ]
            }
        )
