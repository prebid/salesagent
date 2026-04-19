"""
Schema endpoint blueprint for serving JSON Schemas for API validation.

This blueprint provides endpoints for clients to fetch JSON Schemas
for AdCP API responses to enable client-side validation.
"""

import logging

from flask import Blueprint, jsonify

from src.core.domain_config import get_sales_agent_url
from src.core.schema_validation import create_schema_registry

logger = logging.getLogger(__name__)

# Create the schemas blueprint
schemas_bp = Blueprint("schemas", __name__, url_prefix="/schemas")


@schemas_bp.route("/adcp/v2.4/<schema_name>.json", methods=["GET"])
def get_schema(schema_name: str):
    """Get JSON Schema for a specific AdCP response type.

    Args:
        schema_name: Name of the schema (e.g., 'listcreativeformats', 'getproducts')

    Returns:
        JSON Schema object for the requested response type
    """
    try:
        # Get the schema registry
        schema_registry = create_schema_registry()

        # Normalize schema name
        normalized_name = schema_name.lower().replace("_", "").replace("-", "")

        # Find matching schema
        for registry_name, schema in schema_registry.items():
            if registry_name.replace("_", "").replace("-", "") == normalized_name:
                # Add metadata to the schema
                base_url = get_sales_agent_url()
                schema_with_meta = {
                    "$schema": "https://json-schema.org/draft/2020-12/schema",
                    "$id": f"{base_url}/schemas/adcp/v2.4/{schema_name}.json",
                    "title": f"AdCP {registry_name.title()} Response Schema",
                    "description": f"JSON Schema for AdCP v2.4 {registry_name} response validation",
                    **schema,
                }

                # Log the schema request
                logger.info(f"Serving schema for: {schema_name} (matched: {registry_name})")

                return jsonify(schema_with_meta)

        # Schema not found
        available_schemas = list(schema_registry.keys())
        return (
            jsonify(
                {
                    "error": "Schema not found",
                    "requested_schema": schema_name,
                    "available_schemas": available_schemas,
                    "note": "Schema names are case-insensitive and support both underscore and hyphen separators",
                }
            ),
            404,
        )

    except Exception as e:
        logger.error(f"Error serving schema {schema_name}: {e}")
        return jsonify({"error": "Internal server error", "message": str(e)}), 500


@schemas_bp.route("/adcp/v2.4/", methods=["GET"])
@schemas_bp.route("/adcp/v2.4/index.json", methods=["GET"])
def list_schemas():
    """List all available schemas for AdCP v2.4.

    Returns:
        JSON object with available schema names and URLs
    """
    try:
        schema_registry = create_schema_registry()
        base_url = f"{get_sales_agent_url()}/schemas/adcp/v2.4"

        schemas_index = {
            "schemas": {},
            "version": "AdCP v2.4",
            "schema_version": "draft-2020-12",
            "base_url": base_url,
            "description": "JSON Schemas for AdCP v2.4 API response validation",
        }

        for schema_name in schema_registry.keys():
            schemas_index["schemas"][schema_name] = {
                "url": f"{base_url}/{schema_name}.json",
                "description": f"Schema for {schema_name} responses",
            }

        return jsonify(schemas_index)

    except Exception as e:
        logger.error(f"Error listing schemas: {e}")
        return jsonify({"error": "Internal server error", "message": str(e)}), 500


@schemas_bp.route("/adcp/", methods=["GET"])
def list_versions():
    """List available AdCP schema versions.

    Returns:
        JSON object with available AdCP versions
    """
    return jsonify(
        {
            "available_versions": ["v2.4"],
            "current_version": "v2.4",
            "description": "Available AdCP schema versions",
            "latest_url": f"{get_sales_agent_url()}/schemas/adcp/v2.4/",
        }
    )


@schemas_bp.route("/", methods=["GET"])
def schemas_root():
    """Root schemas endpoint.

    Returns:
        JSON object with available protocols and versions
    """
    return jsonify(
        {
            "protocols": {
                "adcp": {
                    "description": "Advertising Context Protocol",
                    "versions": ["v2.4"],
                    "current_version": "v2.4",
                    "url": f"{get_sales_agent_url()}/schemas/adcp/",
                }
            },
            "description": "JSON Schema service for API validation",
            "schema_version": "draft-2020-12",
        }
    )


# Health check for schema service
@schemas_bp.route("/health", methods=["GET"])
def schema_health():
    """Health check for schema service.

    Returns:
        JSON object with service status
    """
    try:
        # Test that we can generate schemas
        schema_registry = create_schema_registry()
        schema_count = len(schema_registry)

        return jsonify(
            {
                "status": "healthy",
                "schemas_available": schema_count,
                "service": "AdCP Schema Validation Service",
                "version": "1.0.0",
            }
        )

    except Exception as e:
        logger.error(f"Schema service health check failed: {e}")
        return jsonify({"status": "unhealthy", "error": str(e), "service": "AdCP Schema Validation Service"}), 500


# Error handlers for the blueprint
@schemas_bp.errorhandler(404)
def schema_not_found(error):
    """Handle 404 errors for schema requests."""
    return (
        jsonify(
            {
                "error": "Schema not found",
                "message": "The requested schema does not exist",
                "available_endpoints": [
                    "/schemas/adcp/v2.4/",
                    "/schemas/adcp/v2.4/<schema_name>.json",
                    "/schemas/health",
                ],
            }
        ),
        404,
    )


@schemas_bp.errorhandler(500)
def schema_internal_error(error):
    """Handle 500 errors for schema requests."""
    return (
        jsonify(
            {
                "error": "Internal server error",
                "message": "An error occurred while processing the schema request",
                "service": "AdCP Schema Validation Service",
            }
        ),
        500,
    )
