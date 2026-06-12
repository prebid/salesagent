"""AdCP tool implementation.

This module contains tool implementations following the MCP/A2A shared
implementation pattern from CLAUDE.md.
"""

import logging
import time

from adcp.types import Format as AdcpFormat
from adcp.utils.format_assets import get_format_assets

from src.core.exceptions import AdCPAuthenticationError

logger = logging.getLogger(__name__)


def _ensure_backward_compatible_format[FormatT: AdcpFormat](f: FormatT) -> FormatT:
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
from src.core.format_cache import canonical_format_identity, canonical_format_matches
from src.core.resolved_identity import ResolvedIdentity
from src.core.schemas import ListCreativeFormatsRequest, ListCreativeFormatsResponse
from src.core.tracing import traced


@traced
def _list_creative_formats_impl(
    req: ListCreativeFormatsRequest | None, identity: ResolvedIdentity | None
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

    # Extract principal and tenant from resolved identity
    principal_id = identity.principal_id if identity else None
    tenant = identity.tenant if identity else None
    if not tenant:
        raise AdCPAuthenticationError("No tenant context available")

    # Get formats from all registered creative agents via registry
    import asyncio

    from src.core.creative_agent_registry import FormatFetchResult, get_creative_agent_registry

    # Decision: docs/design/error-propagation-in-format-discovery.md
    # Registry creation failure → return empty formats + errors (FD-ERR-03)
    try:
        registry = get_creative_agent_registry()
    except Exception as e:
        from adcp.types import Error as AdCPResponseError

        logger.error(f"Failed to create creative agent registry: {e}", exc_info=True)
        return ListCreativeFormatsResponse(
            formats=[],
            errors=[
                AdCPResponseError(
                    code="REGISTRY_ERROR",
                    message=f"Creative agent registry initialization failed: {e}",
                )
            ],
            context=req.context,
        )

    # Use list_all_formats_with_errors() to get per-agent error reporting (FD-ERR-01, FD-ERR-02)
    try:
        loop = asyncio.get_running_loop()
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(
                lambda: asyncio.run(registry.list_all_formats_with_errors(tenant_id=tenant["tenant_id"]))
            )
            fetch_result: FormatFetchResult = future.result()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            fetch_result = loop.run_until_complete(registry.list_all_formats_with_errors(tenant_id=tenant["tenant_id"]))
        finally:
            loop.close()

    formats = fetch_result.formats
    agent_errors = fetch_result.errors

    # Add adapter-supported canonical formats when Broadstreet is acting as both
    # sales and creative agent. Broadstreet-specific templates are not separate
    # AdCP format IDs; they are creative asset metadata handled by the adapter.
    try:
        from src.core.database.repositories.uow import TenantConfigUoW

        with TenantConfigUoW(tenant["tenant_id"]) as uow:
            assert uow.tenant_config is not None
            config_row = uow.tenant_config.get_adapter_config()
            adapter_type = config_row.adapter_type if config_row else None

            if adapter_type == "broadstreet":
                from src.adapters.broadstreet.formats import broadstreet_creative_format_models

                existing_keys = {canonical_format_identity(f.format_id) for f in formats}
                added_count = 0
                for fmt in broadstreet_creative_format_models():
                    key = canonical_format_identity(fmt.format_id)
                    if key in existing_keys:
                        continue
                    formats.append(fmt)
                    existing_keys.add(key)
                    added_count += 1

                logger.info("Added %s Broadstreet canonical formats", added_count)
    except Exception as e:
        # Don't fail if adapter formats can't be retrieved
        logger.debug(f"Could not get adapter formats: {e}")

    # Apply filters from request
    if req.format_ids:
        formats = [
            f
            for f in formats
            if any(canonical_format_matches(requested_format_id, f.format_id) for requested_format_id in req.format_ids)
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
                types.add(str(asset_type))
            # For repeatable groups, check nested assets
            assets = getattr(asset_req, "assets", None)
            if assets:
                for asset in assets:
                    at = getattr(asset, "asset_type", None)
                    if at:
                        types.add(str(at))
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
        # Normalize requested asset types to string values for comparison.
        # adcp 3.6.0: req.asset_types contains AssetContentType enums; use .value to get string.
        # Format assets now use plain string literals, so must compare using .value not str(enum).
        requested_types = {at.value if hasattr(at, "value") else str(at) for at in req.asset_types}
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

    # Filter by wcag_level - hierarchical: A < AA < AAA
    # Formats must meet at least the requested level; formats without accessibility are excluded
    if req.wcag_level is not None:
        from adcp.types import WcagLevel

        _WCAG_ORDER = {WcagLevel.A: 1, WcagLevel.AA: 2, WcagLevel.AAA: 3}
        min_level = _WCAG_ORDER.get(req.wcag_level, 0)
        formats = [
            f
            for f in formats
            if f.accessibility is not None and _WCAG_ORDER.get(f.accessibility.wcag_level, 0) >= min_level
        ]

    # Filter by output_format_ids / input_format_ids (OR semantics each)
    for req_ids, attr in (
        (req.output_format_ids, "output_format_ids"),
        (req.input_format_ids, "input_format_ids"),
    ):
        if req_ids:
            formats = [
                f
                for f in formats
                if getattr(f, attr)
                and any(
                    canonical_format_matches(requested_format_id, supported_format_id)
                    for requested_format_id in req_ids
                    for supported_format_id in getattr(f, attr)
                )
            ]

    # Sort formats by name for consistent ordering
    # (type field removed in adcp 3.12)
    formats.sort(key=lambda f: f.name or "")

    # Ensure backward compatibility: populate both assets and assets_required
    # This allows old clients (using assets_required) and new clients (using assets) to work
    formats = [_ensure_backward_compatible_format(f) for f in formats]

    # Apply cursor-based pagination (AdCP PaginationRequest spec)
    total_count = len(formats)
    max_results = 50  # AdCP default
    start_index = 0

    if req.pagination is not None:
        if req.pagination.max_results is not None:
            max_results = req.pagination.max_results
        if req.pagination.cursor is not None:
            import base64

            try:
                start_index = int(base64.b64decode(req.pagination.cursor).decode("utf-8"))
            except ValueError:
                start_index = 0

    end_index = start_index + max_results
    has_more = end_index < total_count
    page_formats = formats[start_index:end_index]

    # Build pagination response
    from adcp.types import PaginationResponse

    next_cursor = None
    if has_more:
        import base64

        next_cursor = base64.b64encode(str(end_index).encode("utf-8")).decode("utf-8")

    pagination_response = PaginationResponse(
        has_more=has_more,
        cursor=next_cursor,
        total_count=total_count,
    )

    # Build creative_agents referrals from registry (POST-S4).
    # ListCreativeFormatsResponse expects ``CreativeAgent`` from the
    # media_buy module — there are two same-named classes in 4.4 and the
    # top-level ``adcp.types.CreativeAgent`` resolves to the creative-side
    # variant, which Pydantic rejects as the wrong model type even though
    # they're shape-identical.
    from adcp.types import CreativeAgentCapability
    from adcp.types.generated_poc.media_buy.list_creative_formats_response import (
        CreativeAgent as AdcpCreativeAgent,
    )

    creative_agents_list: list[AdcpCreativeAgent] | None = None
    try:
        agents = registry._get_tenant_agents(tenant["tenant_id"])
        if agents:
            creative_agents_list = []
            for agent in agents:
                creative_agents_list.append(
                    AdcpCreativeAgent(
                        agent_url=agent.agent_url,
                        agent_name=agent.name,
                        capabilities=[
                            CreativeAgentCapability.validation,
                            CreativeAgentCapability.assembly,
                            CreativeAgentCapability.preview,
                            CreativeAgentCapability.delivery,
                        ],
                    )
                )
    except Exception:
        logger.warning("Failed to build agent referrals for tenant %s", tenant["tenant_id"], exc_info=True)

    # Log the operation
    audit_logger = get_audit_logger("AdCP", tenant["tenant_id"])
    audit_logger.log_operation(
        operation="list_creative_formats",
        principal_name=principal_id or "anonymous",
        principal_id=principal_id or "anonymous",
        adapter_id="N/A",
        success=True,
        details={
            "format_count": len(page_formats),
            "total_count": total_count,
            "standard_formats": len([f for f in page_formats if getattr(f, "is_standard", False)]),
            "custom_formats": len([f for f in page_formats if not getattr(f, "is_standard", False)]),
            "format_count_standard": len([f for f in page_formats if getattr(f, "is_standard", False)]),
        },
    )

    # Create response (no message/specification_version - not in adapter schema)
    # Format list from registry is compatible with library Format type
    response = ListCreativeFormatsResponse(
        formats=page_formats,
        creative_agents=creative_agents_list,
        errors=agent_errors if agent_errors else None,
        context=req.context,
        pagination=pagination_response,
    )

    # Always return Pydantic model - MCP wrapper will handle serialization
    # Schema enhancement (if needed) should happen in the MCP wrapper, not here
    return response
