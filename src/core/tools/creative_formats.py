"""AdCP tool implementation.

This module contains tool implementations following the MCP/A2A shared
implementation pattern from CLAUDE.md.
"""

import logging
import time
from typing import TypeVar

from adcp import FormatId
from adcp.types import Format as AdcpFormat
from adcp.types.generated_poc.core.context import ContextObject
from adcp.types.generated_poc.core.format import Assets, Assets5
from adcp.types.generated_poc.enums.asset_content_type import AssetContentType
from adcp.types.generated_poc.enums.format_category import FormatCategory
from adcp.utils.format_assets import get_format_assets

# TypeVar for Format to preserve subclass type through backward compatibility function
FormatT = TypeVar("FormatT", bound=AdcpFormat)
from fastmcp.exceptions import ToolError
from fastmcp.server.context import Context
from fastmcp.tools.tool import ToolResult
from pydantic import ValidationError

from src.core.tool_context import ToolContext

logger = logging.getLogger(__name__)


def _ensure_backward_compatible_format(f: FormatT) -> FormatT:
    """Pass-through function for backward compatibility.

    Note: adcp 3.2.0 removed the deprecated `assets_required` field from Format.
    The new `assets` field includes both required and optional assets with a `required` boolean.
    This function is kept for API compatibility but now just returns the format unchanged.

    Args:
        f: Format object from creative agent

    Returns:
        Format unchanged (backward compatibility code removed in adcp 3.2.0 upgrade)
    """
    return f


from src.core.audit_logger import get_audit_logger
from src.core.auth import get_principal_from_context
from src.core.config_loader import get_current_tenant, set_current_tenant
from src.core.schemas import ListCreativeFormatsRequest, ListCreativeFormatsResponse
from src.core.validation_helpers import format_validation_error


def _infer_asset_type(asset_id: str) -> str:
    """Infer asset type from asset ID naming convention.

    Args:
        asset_id: Asset identifier (e.g., "front_image", "youtube_url", "headline")

    Returns:
        Asset type string (image, video, text, url)
    """
    asset_lower = asset_id.lower()
    if "image" in asset_lower or "logo" in asset_lower:
        return "image"
    elif "video" in asset_lower or "youtube" in asset_lower:
        return "video"
    elif "url" in asset_lower or "click" in asset_lower:
        return "url"
    elif "html" in asset_lower:
        return "html"
    else:
        return "text"  # Default to text for headlines, body, captions, etc.


def _list_creative_formats_impl(
    req: ListCreativeFormatsRequest | None, context: Context | ToolContext | None
) -> ListCreativeFormatsResponse:
    """List all available creative formats (AdCP spec endpoint).

    Returns formats from all registered creative agents (default + tenant-specific).
    Uses CreativeAgentRegistry for dynamic format discovery with caching.
    Supports optional filtering by type, standard_only, category, and format_ids.
    """
    start_time = time.time()

    # Use default request if none provided
    # All ListCreativeFormatsRequest fields have defaults (None) per AdCP spec
    if req is None:
        req = ListCreativeFormatsRequest()

    # For discovery endpoints, authentication is optional
    # require_valid_token=False means invalid tokens are treated like missing tokens (discovery endpoint behavior)
    principal_id, tenant = get_principal_from_context(
        context, require_valid_token=False
    )  # Returns (None, tenant) if no/invalid auth

    # Set tenant context if returned
    if tenant:
        set_current_tenant(tenant)
    else:
        tenant = get_current_tenant()
    if not tenant:
        raise ToolError("No tenant context available")

    # Get formats from all registered creative agents via registry
    import asyncio

    from src.core.creative_agent_registry import get_creative_agent_registry

    registry = get_creative_agent_registry()

    # Run async operation - check if we're already in an async context
    try:
        # Check if there's already a running event loop
        loop = asyncio.get_running_loop()
        # We're in an async context, run in thread pool to avoid nested loop error
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(lambda: asyncio.run(registry.list_all_formats(tenant_id=tenant["tenant_id"])))
            formats = future.result()
    except RuntimeError:
        # No running loop, safe to create one
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            formats = loop.run_until_complete(registry.list_all_formats(tenant_id=tenant["tenant_id"]))
        finally:
            loop.close()

    # Get formats from adapter if it provides them (e.g., Broadstreet acting as both sales and creative agent)
    # Check adapter type from tenant config and load formats without instantiating the full adapter
    try:
        from sqlalchemy import select

        from src.core.database.database_session import get_db_session
        from src.core.database.models import AdapterConfig

        with get_db_session() as session:
            stmt = select(AdapterConfig).filter_by(tenant_id=tenant["tenant_id"])
            config_row = session.scalars(stmt).first()

            adapter_type = config_row.adapter_type if config_row else None

            if adapter_type == "broadstreet":
                # Import Broadstreet templates and convert to formats
                from src.adapters.broadstreet.config_schema import BROADSTREET_TEMPLATES
                from src.core.schemas import Format, FormatId, url

                agent_url = f"broadstreet://{tenant['tenant_id']}"

                for template_id, template in BROADSTREET_TEMPLATES.items():
                    try:
                        format_id = FormatId(
                            id=f"broadstreet_{template_id}",
                            agent_url=url(agent_url),
                        )

                        # Build assets list
                        assets_list: list[Assets | Assets5] = []
                        for asset_id in template.get("required_assets", []):
                            asset_type = _infer_asset_type(asset_id)
                            assets_list.append(
                                Assets(
                                    item_type="individual",
                                    asset_id=asset_id,
                                    asset_type=AssetContentType(asset_type),
                                    required=True,
                                )
                            )
                        for asset_id in template.get("optional_assets", []):
                            asset_type = _infer_asset_type(asset_id)
                            assets_list.append(
                                Assets(
                                    item_type="individual",
                                    asset_id=asset_id,
                                    asset_type=AssetContentType(asset_type),
                                    required=False,
                                )
                            )

                        fmt = Format(
                            format_id=format_id,
                            name=str(template["name"]),
                            type=FormatCategory.display,
                            description=str(template["description"]) if template.get("description") else None,
                            assets=assets_list if assets_list else None,
                            is_standard=False,
                            platform_config=None,
                            category=None,
                            requirements=None,
                            iab_specification=None,
                            accepts_3p_tags=None,
                        )
                        formats.append(fmt)
                    except Exception as e:
                        logger.warning(f"Failed to parse Broadstreet template {template_id}: {e}")
                        continue

                logger.info(f"Added {len(BROADSTREET_TEMPLATES)} Broadstreet formats")
    except Exception as e:
        # Don't fail if adapter formats can't be retrieved
        logger.debug(f"Could not get adapter formats: {e}")

    # Apply filters from request
    if req.type:
        formats = [f for f in formats if f.type == req.type]

    if req.format_ids:
        # Filter to only the specified format IDs
        # Extract the 'id' field from each FormatId object
        format_ids_set = {fmt.id for fmt in req.format_ids}
        # Compare format_id.id (handle both FormatId objects and strings)
        formats = [
            f for f in formats if (f.format_id.id if hasattr(f.format_id, "id") else f.format_id) in format_ids_set
        ]

    # Helper functions to extract properties from Format structure per AdCP spec
    def is_format_responsive(f) -> bool:
        """Check if format is responsive by examining renders.dimensions.responsive."""
        if not f.renders:
            return False
        for render in f.renders:
            dims = getattr(render, "dimensions", None)
            if dims and getattr(dims, "responsive", None):
                responsive = dims.responsive
                # Responsive if either width or height is fluid
                if getattr(responsive, "width", False) or getattr(responsive, "height", False):
                    return True
        return False

    def get_format_dimensions(f) -> list[tuple[int | None, int | None]]:
        """Get all (width, height) pairs from format renders."""
        dimensions: list[tuple[int | None, int | None]] = []
        if not f.renders:
            return dimensions
        for render in f.renders:
            dims = getattr(render, "dimensions", None)
            if dims:
                w = getattr(dims, "width", None)
                h = getattr(dims, "height", None)
                if w is not None or h is not None:
                    dimensions.append((w, h))
        return dimensions

    def get_format_asset_types(f) -> set[str]:
        """Get all asset types from format's assets.

        Uses adcp.utils.get_format_assets() which handles backward compatibility
        with deprecated assets_required field automatically.
        """
        types: set[str] = set()
        for asset_req in get_format_assets(f):
            # Handle both individual assets and repeatable groups
            asset_type = getattr(asset_req, "asset_type", None)
            if asset_type:
                types.add(asset_type.value if hasattr(asset_type, "value") else str(asset_type))
            # For repeatable groups, check nested assets
            assets = getattr(asset_req, "assets", None)
            if assets:
                for asset in assets:
                    at = getattr(asset, "asset_type", None)
                    if at:
                        types.add(at.value if hasattr(at, "value") else str(at))
        return types

    # Filter by is_responsive (AdCP filter)
    # Checks renders.dimensions.responsive per AdCP spec
    if req.is_responsive is not None:
        formats = [f for f in formats if is_format_responsive(f) == req.is_responsive]

    # Filter by name_search (case-insensitive partial match)
    if req.name_search:
        search_term = req.name_search.lower()
        formats = [f for f in formats if search_term in f.name.lower()]

    # Filter by asset_types - formats must support at least one of the requested types
    if req.asset_types:
        # Normalize requested asset types to string values for comparison
        requested_types = {at.value if hasattr(at, "value") else at for at in req.asset_types}
        formats = [f for f in formats if get_format_asset_types(f) & requested_types]

    # Filter by dimension constraints
    # Per AdCP spec, matches if ANY render has dimensions matching the constraints
    # Formats without dimension info are excluded when dimension filters are applied
    if req.min_width is not None:
        formats = [f for f in formats if any(w and w >= req.min_width for w, h in get_format_dimensions(f))]
    if req.max_width is not None:
        formats = [f for f in formats if any(w and w <= req.max_width for w, h in get_format_dimensions(f))]
    if req.min_height is not None:
        formats = [f for f in formats if any(h and h >= req.min_height for w, h in get_format_dimensions(f))]
    if req.max_height is not None:
        formats = [f for f in formats if any(h and h <= req.max_height for w, h in get_format_dimensions(f))]

    # Sort formats by type and name for consistent ordering
    # Use .value to convert enum to string for sorting (enums don't support < comparison)
    formats.sort(key=lambda f: (f.type.value, f.name))

    # Ensure backward compatibility: populate both assets and assets_required
    # This allows old clients (using assets_required) and new clients (using assets) to work
    formats = [_ensure_backward_compatible_format(f) for f in formats]

    # Log the operation
    audit_logger = get_audit_logger("AdCP", tenant["tenant_id"])
    audit_logger.log_operation(
        operation="list_creative_formats",
        principal_name=principal_id or "anonymous",
        principal_id=principal_id or "anonymous",
        adapter_id="N/A",
        success=True,
        details={
            "format_count": len(formats),
            "standard_formats": len([f for f in formats if f.is_standard]),
            "custom_formats": len([f for f in formats if not f.is_standard]),
            "format_types": list({f.type.value for f in formats}),
        },
    )

    # Create response (no message/specification_version - not in adapter schema)
    # Format list from registry is compatible with library Format type
    response = ListCreativeFormatsResponse(
        formats=formats,
        creative_agents=None,
        errors=None,
        context=req.context,
    )

    # Always return Pydantic model - MCP wrapper will handle serialization
    # Schema enhancement (if needed) should happen in the MCP wrapper, not here
    return response


def list_creative_formats(
    type: FormatCategory | None = None,
    format_ids: list[FormatId] | None = None,
    is_responsive: bool | None = None,
    name_search: str | None = None,
    asset_types: list[AssetContentType] | None = None,
    min_width: int | None = None,
    max_width: int | None = None,
    min_height: int | None = None,
    max_height: int | None = None,
    context: ContextObject | None = None,  # Application level context per adcp spec
    ctx: Context | ToolContext | None = None,
):
    """List all available creative formats (AdCP spec endpoint).

    MCP tool wrapper that delegates to the shared implementation.
    FastMCP automatically validates and coerces JSON inputs to Pydantic models.

    Args:
        type: Filter by format type (audio, video, display)
        format_ids: Filter by FormatId objects
        is_responsive: Filter for responsive formats (True/False)
        name_search: Search formats by name (case-insensitive partial match)
        asset_types: Filter by asset content types (e.g., ["image", "video"])
        min_width: Minimum format width in pixels
        max_width: Maximum format width in pixels
        min_height: Minimum format height in pixels
        max_height: Maximum format height in pixels
        context: Application-level context per AdCP spec
        ctx: FastMCP context (automatically provided)

    Returns:
        ToolResult with ListCreativeFormatsResponse data
    """
    try:
        # Convert typed Pydantic models to values for the request
        # FastMCP already coerced JSON inputs to these types
        type_str = type.value if type else None
        asset_types_strs = [at.value for at in asset_types] if asset_types else None
        context_dict = context.model_dump(mode="json") if context else None

        req = ListCreativeFormatsRequest(
            type=type_str,
            format_ids=format_ids,
            is_responsive=is_responsive,
            name_search=name_search,
            asset_types=asset_types_strs,
            min_width=min_width,
            max_width=max_width,
            min_height=min_height,
            max_height=max_height,
            context=context_dict,
        )
    except ValidationError as e:
        raise ToolError(format_validation_error(e, context="list_creative_formats request")) from e

    response = _list_creative_formats_impl(req, ctx)
    return ToolResult(content=str(response), structured_content=response)


def list_creative_formats_raw(
    req: ListCreativeFormatsRequest | None = None,
    ctx: Context | ToolContext | None = None,
) -> ListCreativeFormatsResponse:
    """List all available creative formats (raw function for A2A server use).

    Delegates to shared implementation.

    Args:
        req: Optional request with filter parameters
        ctx: FastMCP context

    Returns:
        ListCreativeFormatsResponse with all available formats
    """
    return _list_creative_formats_impl(req, ctx)
