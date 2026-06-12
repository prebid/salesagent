"""Format resolution with product overrides and dynamic creative agent discovery.

Provides layered format lookup:
1. Product-level overrides (from product.implementation_config.format_overrides)
2. Dynamic format discovery from creative agents (via CreativeAgentRegistry)

Note: Tenant custom formats (creative_formats table) were removed in favor of
creative agent-based format discovery per AdCP v2.4.
"""

import json
import logging
from typing import TYPE_CHECKING

from adcp.utils.format_assets import get_format_assets

from src.core.database.database_session import get_db_session
from src.core.exceptions import AdCPNotFoundError
from src.core.format_cache import canonical_format_satisfies
from src.core.schemas import Format
from src.core.validation_helpers import run_async_in_sync_context

if TYPE_CHECKING:
    from src.core.creative_agent_registry import FormatFetchResult

logger = logging.getLogger(__name__)


def _string_value(value: object) -> str:
    return str(value.value) if hasattr(value, "value") else str(value)


def _format_dimensions(fmt: Format) -> list[tuple[int | None, int | None]]:
    dimensions: list[tuple[int | None, int | None]] = []
    if not fmt.renders:
        primary = fmt.get_primary_dimensions()
        return [primary] if primary else []
    for render in fmt.renders:
        dims = getattr(render, "dimensions", None)
        if dims is None:
            continue
        width = getattr(dims, "width", None)
        height = getattr(dims, "height", None)
        if width is not None or height is not None:
            dimensions.append((width, height))
    return dimensions


def _format_is_responsive(fmt: Format) -> bool:
    if not fmt.renders:
        return False
    for render in fmt.renders:
        dims = getattr(render, "dimensions", None)
        responsive = getattr(dims, "responsive", None) if dims else None
        if responsive and (getattr(responsive, "width", False) or getattr(responsive, "height", False)):
            return True
    return False


def _format_asset_types(fmt: Format) -> set[str]:
    asset_types: set[str] = set()
    for asset_req in get_format_assets(fmt):
        asset_type = getattr(asset_req, "asset_type", None)
        if asset_type:
            asset_types.add(_string_value(asset_type))
        nested_assets = getattr(asset_req, "assets", None)
        if nested_assets:
            for asset in nested_assets:
                nested_type = getattr(asset, "asset_type", None)
                if nested_type:
                    asset_types.add(_string_value(nested_type))
    return asset_types


def _format_matches_asset_types(fmt: Format, requested_types: set[str]) -> bool:
    # Be tolerant for existing authoring clients that use asset_type as a
    # format-category filter ("display", "video", "audio") rather than an
    # AdCP asset-content filter ("image", "html", "vast", etc.).
    legacy_category = getattr(fmt, "type", None)
    if legacy_category is not None and _string_value(legacy_category) in requested_types:
        return True
    return bool(_format_asset_types(fmt) & requested_types)


def _format_ref_has_parameters(format_ref: object) -> bool:
    return any(getattr(format_ref, key, None) is not None for key in ("width", "height", "duration_ms"))


def _filter_available_formats(
    formats: list[Format],
    max_width: int | None = None,
    max_height: int | None = None,
    min_width: int | None = None,
    min_height: int | None = None,
    is_responsive: bool | None = None,
    asset_types: list[str] | None = None,
    name_search: str | None = None,
) -> list[Format]:
    """Apply admin/product-form filters locally to a discovered format catalog."""
    filtered = formats
    if is_responsive is not None:
        filtered = [fmt for fmt in filtered if _format_is_responsive(fmt) == is_responsive]
    if name_search:
        search_term = name_search.lower()
        filtered = [fmt for fmt in filtered if search_term in fmt.name.lower()]
    if asset_types:
        requested_types = {_string_value(asset_type) for asset_type in asset_types}
        filtered = [fmt for fmt in filtered if _format_matches_asset_types(fmt, requested_types)]
    if min_width is not None:
        filtered = [
            fmt
            for fmt in filtered
            if any(width is not None and width >= min_width for width, _ in _format_dimensions(fmt))
        ]
    if max_width is not None:
        filtered = [
            fmt
            for fmt in filtered
            if any(width is not None and width <= max_width for width, _ in _format_dimensions(fmt))
        ]
    if min_height is not None:
        filtered = [
            fmt
            for fmt in filtered
            if any(height is not None and height >= min_height for _, height in _format_dimensions(fmt))
        ]
    if max_height is not None:
        filtered = [
            fmt
            for fmt in filtered
            if any(height is not None and height <= max_height for _, height in _format_dimensions(fmt))
        ]
    return filtered


def get_format(
    format_id: str, agent_url: str | None = None, tenant_id: str | None = None, product_id: str | None = None
) -> Format:
    """Resolve format with priority: product override → creative agent discovery.

    Args:
        format_id: Format identifier (e.g., "display_300x250_image")
        agent_url: Optional creative agent URL (defaults to AdCP standard agent)
        tenant_id: Optional tenant ID for agent lookup
        product_id: Optional product ID for product-level overrides

    Returns:
        Format object with all configuration

    Raises:
        AdCPNotFoundError: If format_id not found in any source
    """
    # Check product override first
    if product_id and tenant_id:
        override = _get_product_format_override(tenant_id, product_id, format_id, agent_url=agent_url)
        if override:
            return override

    # Get from creative agent registry
    from src.core.creative_agent_registry import get_creative_agent_registry

    registry = get_creative_agent_registry()

    # If agent_url provided, get format directly from that agent
    # Coerce to str: FormatId.agent_url is Pydantic AnyUrl (not a str subclass)
    if agent_url:
        fmt = run_async_in_sync_context(registry.get_format(str(agent_url), format_id))
        if fmt:
            return fmt
    else:
        # Search all agents for this format
        all_formats = run_async_in_sync_context(registry.list_all_formats(tenant_id=tenant_id))
        for fmt in all_formats:
            discovered_format_ref = fmt.format_id
            discovered_format_id = getattr(discovered_format_ref, "id", discovered_format_ref)
            if discovered_format_id == format_id and not _format_ref_has_parameters(discovered_format_ref):
                return fmt
            if not isinstance(discovered_format_ref, str) and canonical_format_satisfies(
                format_id, discovered_format_ref
            ):
                return fmt

    # Not found anywhere
    error_msg = f"Unknown format_id '{format_id}'"
    if agent_url:
        error_msg += f" from agent {agent_url}"
    if tenant_id:
        error_msg += f" for tenant {tenant_id}"
    raise AdCPNotFoundError(error_msg)


def _get_product_format_override(
    tenant_id: str, product_id: str, format_id: str, agent_url: str | None = None
) -> Format | None:
    """Get product-level format override from product.implementation_config.

    Product can override any format's platform_config. Example:
    {
        "format_overrides": {
            "display_300x250": {
                "platform_config": {
                    "gam": {
                        "creative_placeholder": {
                            "width": 1,
                            "height": 1,
                            "creative_template_id": 12345678
                        }
                    }
                }
            }
        }
    }

    Args:
        tenant_id: Tenant identifier
        product_id: Product identifier
        format_id: Format to look up
        agent_url: Optional creative agent URL (needed to fetch base format)

    Returns:
        Format with overridden config, or None if no override exists
    """
    from sqlalchemy import text

    with get_db_session() as session:
        result = session.execute(
            text(
                "SELECT implementation_config FROM products WHERE tenant_id = :tenant_id AND product_id = :product_id"
            ),
            {"tenant_id": tenant_id, "product_id": product_id},
        )
        row = result.fetchone()
        if not row or not row[0]:
            return None

        # Parse implementation_config JSON
        impl_config = json.loads(row[0]) if isinstance(row[0], str) else row[0]
        format_overrides = impl_config.get("format_overrides", {})

        if format_id not in format_overrides:
            return None

        # Get base format from creative agent registry (WITHOUT product_id to avoid recursion)
        from src.core.creative_agent_registry import get_creative_agent_registry

        registry = get_creative_agent_registry()

        try:
            # format_id is a string key in format_overrides dict
            # Pass agent_url to find the base format from the correct creative agent
            base_format = get_format(format_id, agent_url=agent_url, tenant_id=tenant_id, product_id=None)
        except (AdCPNotFoundError, Exception):
            # Base format not found - cannot apply override
            return None

        # Apply override to base format
        override_config = format_overrides[format_id]

        # Merge platform_config override
        if "platform_config" in override_config:
            # Access platform_config directly from the model, not via model_dump(),
            # because platform_config has exclude=True and model_dump() drops it.
            base_platform_config = base_format.platform_config or {}
            override_platform_config = override_config["platform_config"]

            # Deep merge platform configs (override takes precedence)
            merged_platform_config = {**base_platform_config}
            for platform, config in override_platform_config.items():
                if platform in merged_platform_config:
                    # Merge platform-specific configs
                    merged_platform_config[platform] = {
                        **merged_platform_config[platform],
                        **config,
                    }
                else:
                    merged_platform_config[platform] = config

            return base_format.model_copy(update={"platform_config": merged_platform_config})

        return base_format


def list_available_formats(
    tenant_id: str | None = None,
    max_width: int | None = None,
    max_height: int | None = None,
    min_width: int | None = None,
    min_height: int | None = None,
    is_responsive: bool | None = None,
    asset_types: list[str] | None = None,
    name_search: str | None = None,
) -> list[Format]:
    """List all formats available to a tenant from all registered creative agents.

    Args:
        tenant_id: Optional tenant ID to include tenant-specific agents
        max_width: Maximum width in pixels (inclusive)
        max_height: Maximum height in pixels (inclusive)
        min_width: Minimum width in pixels (inclusive)
        min_height: Minimum height in pixels (inclusive)
        is_responsive: Filter for responsive formats
        asset_types: Filter by asset types
        name_search: Search by name

    Returns:
        List of all available Format objects from all registered agents
    """
    result = list_available_formats_with_errors(
        tenant_id=tenant_id,
        max_width=max_width,
        max_height=max_height,
        min_width=min_width,
        min_height=min_height,
        is_responsive=is_responsive,
        asset_types=asset_types,
        name_search=name_search,
    )
    return result.formats


def list_available_formats_with_errors(
    tenant_id: str | None = None,
    max_width: int | None = None,
    max_height: int | None = None,
    min_width: int | None = None,
    min_height: int | None = None,
    is_responsive: bool | None = None,
    asset_types: list[str] | None = None,
    name_search: str | None = None,
) -> "FormatFetchResult":
    """List formats available to a tenant and preserve discovery errors.

    ``list_available_formats`` is the legacy list-only helper used by existing
    admin UI code. New callers that need to distinguish "empty catalog" from
    "catalog unavailable" should use this helper and inspect ``errors``.
    """
    from adcp.types import Error as AdCPResponseError

    from src.core.creative_agent_registry import FormatFetchResult, get_creative_agent_registry

    logger.info(f"[list_available_formats] Starting format fetch for tenant_id={tenant_id}")

    try:
        registry = get_creative_agent_registry()
    except Exception as e:
        logger.error(f"[list_available_formats] Failed to get creative agent registry: {e}", exc_info=True)
        return FormatFetchResult(
            formats=[],
            errors=[
                AdCPResponseError(
                    code="REGISTRY_ERROR",
                    message=f"Creative agent registry initialization failed: {e}",
                )
            ],
        )

    # Get formats from all agents (default + tenant-specific)
    try:
        result = run_async_in_sync_context(registry.list_all_formats_with_errors(tenant_id=tenant_id))
        if isinstance(result, list):
            result = FormatFetchResult(formats=result, errors=[])
    except Exception as e:
        logger.error(f"[list_available_formats] Error fetching formats: {e}", exc_info=True)
        return FormatFetchResult(
            formats=[],
            errors=[
                AdCPResponseError(
                    code="FORMAT_DISCOVERY_ERROR",
                    message=f"Creative format discovery failed: {e}",
                )
            ],
        )

    result.formats = _filter_available_formats(
        result.formats,
        max_width=max_width,
        max_height=max_height,
        min_width=min_width,
        min_height=min_height,
        is_responsive=is_responsive,
        asset_types=asset_types,
        name_search=name_search,
    )
    logger.info(
        f"[list_available_formats] Successfully fetched {len(result.formats)} matching formats "
        f"with {len(result.errors)} errors"
    )
    return result
