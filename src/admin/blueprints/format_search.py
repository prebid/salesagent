"""Format search API for Admin UI.

Provides endpoints for searching and browsing creative formats across
all registered creative agents (default + tenant-specific).
"""

import asyncio

from flask import Blueprint, jsonify, request
from sqlalchemy import select

from src.admin.utils import require_auth  # type: ignore[attr-defined]
from src.core.creative_agent_registry import get_creative_agent_registry
from src.core.database.database_session import get_db_session
from src.core.database.models import CreativeAgent as CreativeAgentModel
from src.core.database.models import Tenant as TenantModel

bp = Blueprint("format_search", __name__, url_prefix="/api/formats")


@bp.route("/search", methods=["GET"])
@require_auth()
def search_formats():
    """Search formats across all registered creative agents.

    Query parameters:
        q: Search query (matches format_id, name, description)
        tenant_id: Optional tenant ID for tenant-specific agents
        type: Optional format type filter (display, video, etc.)

    Returns:
        JSON array of matching formats with agent_url and format details
    """
    query = request.args.get("q", "")
    tenant_id = request.args.get("tenant_id")
    type_filter = request.args.get("type")

    if not query or len(query) < 2:
        return jsonify({"error": "Query must be at least 2 characters"}), 400

    try:
        # Get registry and search
        registry = get_creative_agent_registry()

        # Run async search in sync context
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            formats = loop.run_until_complete(
                registry.search_formats(query=query, tenant_id=tenant_id, type_filter=type_filter)
            )
        finally:
            loop.close()

        # Convert to dict format for JSON response
        results = []
        for fmt in formats:
            # Handle FormatId object - extract string value
            format_id_str = fmt.format_id.id if hasattr(fmt.format_id, "id") else str(fmt.format_id)

            result = {
                "agent_url": fmt.agent_url,
                "format_id": format_id_str,
                "name": fmt.name,
                "type": fmt.type.value if hasattr(fmt.type, "value") else str(fmt.type),  # Handle Type enum
                "category": fmt.category,
                "description": fmt.description,
                "is_standard": fmt.is_standard,
            }

            # Add dimensions if available
            if fmt.requirements:
                if "width" in fmt.requirements and "height" in fmt.requirements:
                    result["dimensions"] = f"{fmt.requirements['width']}x{fmt.requirements['height']}"
                if "duration" in fmt.requirements:
                    result["duration"] = fmt.requirements["duration"]

            results.append(result)

        return jsonify({"formats": results, "count": len(results)})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route("/list", methods=["GET"])
@require_auth()
def list_all_formats():
    """List all formats from all registered creative agents.

    Query parameters:
        tenant_id: Optional tenant ID for tenant-specific agents
        type: Optional format type filter
        force_refresh: Force refresh cache (default: false)

    Returns:
        JSON array of all formats grouped by agent
    """
    tenant_id = request.args.get("tenant_id")
    type_filter = request.args.get("type")
    force_refresh = request.args.get("force_refresh", "false").lower() == "true"

    try:
        registry = get_creative_agent_registry()

        # Run async list in sync context
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            formats = loop.run_until_complete(
                registry.list_all_formats(tenant_id=tenant_id, force_refresh=force_refresh)
            )
        finally:
            loop.close()

        # Filter by type if requested
        if type_filter:
            formats = [f for f in formats if f.type == type_filter]

        # Group by agent_url
        by_agent = {}
        for fmt in formats:
            agent_url = fmt.agent_url or "unknown"
            if agent_url not in by_agent:
                by_agent[agent_url] = []

            # Keep format_id as nested object (matches library schema)
            # Frontend will access format_id.id when needed
            format_id_obj = fmt.format_id
            if hasattr(format_id_obj, "model_dump"):
                # Pydantic object - serialize to dict
                format_id_value = format_id_obj.model_dump(mode="json")
            elif isinstance(format_id_obj, dict):
                format_id_value = format_id_obj
            else:
                # Fallback for string format_ids (legacy)
                format_id_value = {"id": str(format_id_obj), "agent_url": agent_url}

            # Get dimensions for matching
            dimensions_str = None
            dims = fmt.get_primary_dimensions() if hasattr(fmt, "get_primary_dimensions") else None
            if dims:
                width, height = dims
                dimensions_str = f"{width}x{height}"

            by_agent[agent_url].append(
                {
                    "format_id": format_id_value,  # Nested object, not flattened string
                    "name": fmt.name,
                    "type": fmt.type.value if hasattr(fmt.type, "value") else str(fmt.type),  # Handle Type enum
                    "category": fmt.category,
                    "description": fmt.description,
                    "is_standard": fmt.is_standard,
                    "dimensions": dimensions_str,  # Add dimensions for size matching
                }
            )

        return jsonify({"agents": by_agent, "total_formats": len(formats)})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route("/agents", methods=["GET"])
@require_auth()
def list_creative_agents():
    """List all registered creative agents for a tenant.

    Query parameters:
        tenant_id: Tenant ID (required)

    Returns:
        JSON array of registered creative agents
    """
    tenant_id = request.args.get("tenant_id")

    if not tenant_id:
        return jsonify({"error": "tenant_id is required"}), 400

    try:
        # Get tenant config
        with get_db_session() as session:
            stmt = select(TenantModel).filter_by(tenant_id=tenant_id)
            tenant = session.scalars(stmt).first()

            if not tenant:
                return jsonify({"error": "Tenant not found"}), 404

            # Get creative agents from config
            agents = []

            # Default agent (always present)
            agents.append(
                {
                    "agent_url": "https://creative.adcontextprotocol.org",
                    "name": "AdCP Standard Creative Agent",
                    "enabled": True,
                    "priority": 1,
                    "is_default": True,
                }
            )

            # Tenant-specific agents from database
            stmt = select(CreativeAgentModel).filter_by(tenant_id=tenant_id, enabled=True)
            db_agents = session.scalars(stmt).all()

            for db_agent in db_agents:
                agents.append(
                    {
                        "agent_url": db_agent.agent_url,
                        "name": db_agent.name,
                        "enabled": db_agent.enabled,
                        "priority": db_agent.priority,
                        "is_default": False,
                    }
                )

            # Sort by priority
            agents.sort(key=lambda a: a["priority"])

            return jsonify({"agents": agents})

    except Exception as e:
        return jsonify({"error": str(e)}), 500
