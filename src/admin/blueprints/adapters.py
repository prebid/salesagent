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
@require_tenant_access(role=("admin",))
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
@require_tenant_access(role=("admin",))
def setup_adapter(tenant_id, **kwargs):
    """TODO: Extract implementation from admin_ui.py."""
    # Placeholder implementation
    return jsonify({"error": "Not yet implemented"}), 501


@adapters_bp.route("/api/tenant/<tenant_id>/adapter-config", methods=["POST"])
@log_admin_action("update_adapter_config")
@require_tenant_access(role=("admin",))
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

        # For adapters whose schemas mark a field as `secret`, two rules:
        # (a) reject any submitted ciphertext on the wire — a tenant admin must
        #     not be able to authenticate a session by replaying another tenant's
        #     leaked DB-row ciphertext (cross-tenant credential smuggling),
        # (b) preserve the previously-stored value when the caller omits the
        #     field (UX: leave password blank to keep existing credential).
        from src.core.utils.encryption import is_encrypted

        schemas = get_adapter_schemas(adapter_type)
        if schemas and schemas.connection_config:
            secret_fields = [
                name
                for name, field in schemas.connection_config.model_fields.items()
                if isinstance(field.json_schema_extra, dict) and field.json_schema_extra.get("secret")
            ]
            for field_name in secret_fields:
                submitted = config_data.get(field_name)
                if submitted and is_encrypted(submitted):
                    return (
                        jsonify(
                            {
                                "success": False,
                                "error": f"{field_name} must be plaintext (encrypted-token replay rejected)",
                            }
                        ),
                        400,
                    )
            if secret_fields:
                missing = [f for f in secret_fields if not config_data.get(f)]
                if missing:
                    with get_db_session() as session:
                        stmt = select(AdapterConfig).filter_by(tenant_id=tenant_id)
                        existing = session.scalars(stmt).first()
                        if existing and existing.config_json:
                            for field_name in missing:
                                existing_value = existing.config_json.get(field_name)
                                if existing_value:
                                    config_data[field_name] = existing_value

        # Validate config against adapter schema (keeps Pydantic model flowing)
        validated_config = None
        if schemas and schemas.connection_config:
            try:
                validated_config = schemas.connection_config(**config_data)
            except ValidationError as e:
                return jsonify({"success": False, "error": f"Validation error: {e}"}), 400

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
                adapter_config.mock_dry_run = getattr(validated_config, "dry_run", False)
                adapter_config.mock_manual_approval_required = getattr(
                    validated_config, "manual_approval_required", False
                )
            # Note: GAM will be added as its schema is created. Triton + FreeWheel
            # already use config_json via their connection schemas.

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


# FreeWheel-specific endpoints


@adapters_bp.route("/api/tenant/<tenant_id>/adapters/freewheel/test-connection", methods=["POST"])
@require_tenant_access(role=("admin",))
def test_freewheel_connection(tenant_id, **kwargs):
    """Verify FreeWheel OAuth credentials by performing a client_credentials token fetch.

    Accepts ``client_id``, ``network_id``, ``environment`` (production/staging) and
    ``client_secret`` (optional — falls back to the encrypted secret already stored
    on AdapterConfig.config_json).
    """
    from src.core.utils.encryption import is_encrypted

    try:
        data = request.get_json() or {}
        client_id = data.get("client_id")
        client_secret = data.get("client_secret")
        network_id = data.get("network_id")
        environment = data.get("environment", "production")

        if not client_id or not network_id:
            return jsonify({"success": False, "error": "client_id and network_id are required"}), 400

        # Reject submitted ciphertext — only the DB-fallback path is allowed
        # to use the stored ciphertext. See cross-tenant smuggling note in
        # save_adapter_config.
        if client_secret and is_encrypted(client_secret):
            return (
                jsonify(
                    {"success": False, "error": "client_secret must be plaintext (encrypted-token replay rejected)"}
                ),
                400,
            )

        if not client_secret:
            from src.core.database.repositories.adapter_config import AdapterConfigRepository

            with get_db_session() as session:
                existing = AdapterConfigRepository(session, tenant_id).find_by_tenant()
                if existing and existing.config_json:
                    from src.adapters.freewheel import FreeWheelConnectionConfig

                    try:
                        rehydrated = FreeWheelConnectionConfig.model_validate(existing.config_json)
                        client_secret = rehydrated.client_secret
                    except ValidationError:
                        client_secret = None
        if not client_secret:
            return (
                jsonify({"success": False, "error": "client_secret is required for first connection test"}),
                400,
            )

        from src.adapters.freewheel import FreeWheelAPIError, FreeWheelClient
        from src.adapters.freewheel.schemas import FREEWHEEL_HOSTS

        base_url = FREEWHEEL_HOSTS.get(environment, FREEWHEEL_HOSTS["production"])
        client = FreeWheelClient(
            client_id=client_id,
            client_secret=client_secret,
            network_id=network_id,
            base_url=base_url,
        )
        try:
            client._fetch_token()
        except FreeWheelAPIError as exc:
            # Log the full upstream body server-side; return only a generic
            # message to the client to avoid echoing reflected request data
            # or hint messages from the auth provider.
            logger.warning("FreeWheel auth probe failed: %s body=%s", exc, exc.body)
            return jsonify({"success": False, "error": "FreeWheel rejected the credentials"}), 200

        network_name: str | None = None
        try:
            network = client.get_network()
            network_name = network.get("name") or network.get("displayName")
        except FreeWheelAPIError:
            pass  # Token works; network endpoint specifics may vary

        return jsonify({"success": True, "environment": environment, "network_name": network_name})

    except Exception as e:
        logger.error(f"FreeWheel connection test failed: {e}", exc_info=True)
        return jsonify({"success": False, "error": "Connection test failed (see server logs)"}), 500


# Triton-specific endpoints


@adapters_bp.route("/api/tenant/<tenant_id>/adapters/triton/test-connection", methods=["POST"])
@require_tenant_access(role=("admin",))
def test_triton_connection(tenant_id, **kwargs):
    """Verify Triton TAP credentials by performing a JWT login.

    Accepts ``username`` (required) and ``password`` (optional — falls back to
    the encrypted password already stored on AdapterConfig.config_json).
    """
    from src.core.utils.encryption import is_encrypted

    try:
        data = request.get_json() or {}
        username = data.get("username")
        password = data.get("password")
        base_url = data.get("base_url") or "https://mbapi.tritondigital.com"
        login_url = data.get("login_url") or "https://login.tritondigital.com"

        if not username:
            return jsonify({"success": False, "error": "username is required"}), 400

        # https-only — prevent the form from redirecting credential POST to an
        # attacker-controlled host.
        if not base_url.startswith("https://") or not login_url.startswith("https://"):
            return jsonify({"success": False, "error": "base_url and login_url must be https://"}), 400

        # Reject submitted ciphertext — see cross-tenant smuggling note in
        # save_adapter_config.
        if password and is_encrypted(password):
            return (
                jsonify({"success": False, "error": "password must be plaintext (encrypted-token replay rejected)"}),
                400,
            )

        if not password:
            from src.core.database.repositories.adapter_config import AdapterConfigRepository

            with get_db_session() as session:
                existing = AdapterConfigRepository(session, tenant_id).find_by_tenant()
                if existing and existing.config_json:
                    from src.adapters.triton import TritonConnectionConfig

                    try:
                        rehydrated = TritonConnectionConfig.model_validate(existing.config_json)
                        password = rehydrated.password
                    except ValidationError:
                        password = None
        if not password:
            return jsonify({"success": False, "error": "password is required for first connection test"}), 400

        from src.adapters.triton import TritonAPIError, TritonClient

        client = TritonClient(username=username, password=password, base_url=base_url, login_url=login_url)
        try:
            client.login()
        except TritonAPIError as exc:
            logger.warning("Triton auth probe failed: %s body=%s", exc, exc.body)
            return jsonify({"success": False, "error": "Triton rejected the credentials"}), 200

        publisher_name: str | None = None
        try:
            publisher = client.get_publisher()
            publisher_name = publisher.get("name") or publisher.get("displayName")
        except TritonAPIError:
            pass  # JWT works; publisher endpoint specifics may vary

        return jsonify({"success": True, "publisher_name": publisher_name})

    except Exception as e:
        logger.error(f"Triton connection test failed: {e}", exc_info=True)
        return jsonify({"success": False, "error": "Connection test failed (see server logs)"}), 500


# Broadstreet-specific endpoints


@adapters_bp.route("/api/tenant/<tenant_id>/adapters/broadstreet/test-connection", methods=["POST"])
@require_tenant_access(role=("admin",))
def test_broadstreet_connection(tenant_id, **kwargs):
    """Test Broadstreet API connection with provided credentials."""
    try:
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

    except Exception as e:
        logger.error(f"Broadstreet connection test failed: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@adapters_bp.route("/api/tenant/<tenant_id>/adapters/broadstreet/zones", methods=["GET"])
@require_tenant_access()
def list_broadstreet_zones(tenant_id, **kwargs):
    """List available zones from Broadstreet for the tenant's configured account."""
    try:
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
                        {"id": str(zone.get("id")), "name": zone.get("name", f"Zone {zone.get('id')}")}
                        for zone in zones
                    ]
                }
            )

    except Exception as e:
        logger.error(f"Error fetching Broadstreet zones: {e}", exc_info=True)
        return jsonify({"zones": [], "error": str(e)}), 500
