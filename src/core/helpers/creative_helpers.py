"""Creative format parsing and asset conversion helpers."""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fastmcp import Context

    from src.core.schemas import Creative, Package
    from src.core.testing_context import TestingContext

from src.core.schemas import Creative


def _extract_format_namespace(format_value: Any) -> tuple[str, str]:
    """Extract agent_url and format ID from format_id field (AdCP v2.4).

    Args:
        format_value: FormatId dict/object with agent_url+id fields

    Returns:
        Tuple of (agent_url, format_id)

    Raises:
        ValueError: If format_value doesn't have required agent_url and id fields
    """
    if isinstance(format_value, dict):
        agent_url = format_value.get("agent_url")
        format_id = format_value.get("id")
        if not agent_url or not format_id:
            raise ValueError(f"format_id must have both 'agent_url' and 'id' fields. Got: {format_value}")
        return agent_url, format_id
    if hasattr(format_value, "agent_url") and hasattr(format_value, "id"):
        return format_value.agent_url, format_value.id
    if isinstance(format_value, str):
        raise ValueError(
            f"format_id must be an object with 'agent_url' and 'id' fields (AdCP v2.4). "
            f"Got string: '{format_value}'. "
            f"String format_id is no longer supported - all formats must be namespaced."
        )
    raise ValueError(f"Invalid format_id format. Expected object with agent_url and id, got: {type(format_value)}")


def _normalize_format_value(format_value: Any) -> str:
    """Normalize format value to string ID (for legacy code compatibility).

    Args:
        format_value: FormatId dict/object with agent_url+id fields

    Returns:
        String format identifier

    Note: This is a legacy compatibility function. New code should use _extract_format_namespace
    to properly handle the agent_url namespace.
    """
    _, format_id = _extract_format_namespace(format_value)
    return format_id


def _validate_creative_assets(assets: Any) -> dict[str, dict[str, Any]] | None:
    """Validate that creative assets are in AdCP v2.1+ dictionary format.

    AdCP v2.1+ requires assets to be a dictionary keyed by asset_id from the format's
    asset_requirements.

    Args:
        assets: Assets in dict format keyed by asset_id, or None

    Returns:
        Dictionary of assets keyed by asset_id, or None if no assets provided

    Raises:
        ValueError: If assets are not in the correct dict format, or if asset structure is invalid

    Example:
        # Correct format (AdCP v2.1+)
        assets = {
            "main_image": {"asset_type": "image", "url": "https://..."},
            "logo": {"asset_type": "image", "url": "https://..."}
        }
    """
    if assets is None:
        return None

    # Must be a dict
    if not isinstance(assets, dict):
        raise ValueError(
            f"Invalid assets format: expected dict keyed by asset_id (AdCP v2.1+), got {type(assets).__name__}. "
            f"Assets must be a dictionary like: {{'main_image': {{'asset_type': 'image', 'url': '...'}}}}"
        )

    # Validate structure of each asset
    for asset_id, asset_data in assets.items():
        # Asset ID must be a non-empty string
        if not isinstance(asset_id, str):
            raise ValueError(
                f"Asset key must be a string (asset_id from format), got {type(asset_id).__name__}: {asset_id!r}"
            )
        if not asset_id.strip():
            raise ValueError("Asset key (asset_id) cannot be empty or whitespace-only")

        # Asset data must be a dict
        if not isinstance(asset_data, dict):
            raise ValueError(
                f"Asset '{asset_id}' data must be a dict, got {type(asset_data).__name__}. "
                f"Expected format: {{'asset_type': '...', 'url': '...', ...}}"
            )

    return assets


def _convert_creative_to_adapter_asset(creative: Creative, package_assignments: list[str]) -> dict[str, Any]:
    """Convert AdCP v1.3+ Creative object to format expected by ad server adapters."""

    # Base asset object with common fields
    asset = {
        "creative_id": creative.creative_id,
        "name": creative.name,
        "format": creative.get_format_string(),  # Handle both string and FormatId object
        "package_assignments": package_assignments,
    }

    # Determine creative type using AdCP v1.3+ logic
    creative_type = creative.get_creative_type()

    if creative_type == "third_party_tag":
        # Third-party tag creative - use AdCP v1.3+ snippet fields
        snippet = creative.get_snippet_content()
        if not snippet:
            raise ValueError(f"No snippet found for third-party creative {creative.creative_id}")

        asset["snippet"] = snippet
        asset["snippet_type"] = creative.snippet_type or _detect_snippet_type(snippet)
        asset["url"] = creative.url  # Keep URL for fallback

    elif creative_type == "native":
        # Native creative - use AdCP v1.3+ template_variables field
        template_vars = creative.get_template_variables_dict()
        if not template_vars:
            raise ValueError(f"No template_variables found for native creative {creative.creative_id}")

        asset["template_variables"] = template_vars
        asset["url"] = creative.url  # Fallback URL

    elif creative_type == "vast":
        # VAST reference
        asset["snippet"] = creative.get_snippet_content() or creative.url
        asset["snippet_type"] = creative.snippet_type or ("vast_xml" if ".xml" in creative.url else "vast_url")

    else:  # hosted_asset
        # Traditional hosted asset (image/video)
        asset["media_url"] = creative.get_primary_content_url()
        asset["url"] = asset["media_url"]  # For backward compatibility

    # Add common optional fields
    if creative.click_url:
        asset["click_url"] = creative.click_url
    if creative.width:
        asset["width"] = creative.width
    if creative.height:
        asset["height"] = creative.height
    if creative.duration:
        asset["duration"] = creative.duration

    # Always preserve delivery_settings (including tracking_urls) for all creative types
    # This ensures impression trackers from buyers flow through to ad servers
    if creative.delivery_settings:
        asset["delivery_settings"] = creative.delivery_settings

    return asset


def _detect_snippet_type(snippet: str) -> str:
    """Auto-detect snippet type from content for legacy support."""
    if snippet.startswith("<?xml") or ".xml" in snippet:
        return "vast_xml"
    elif snippet.startswith("http") and "vast" in snippet.lower():
        return "vast_url"
    elif snippet.startswith("<script"):
        return "javascript"
    else:
        return "html"  # Default


def process_and_upload_package_creatives(
    packages: list["Package"],
    context: "Context",
    testing_ctx: "TestingContext | None" = None,
) -> tuple[list["Package"], dict[str, list[str]]]:
    """Upload creatives from package.creatives arrays and return updated packages.

    For each package with a non-empty `creatives` array:
    1. Converts Creative objects to dicts
    2. Uploads them via _sync_creatives_impl
    3. Extracts uploaded creative IDs
    4. Creates updated package with merged creative_ids

    This function is immutable - it returns new Package instances instead of
    modifying the input packages.

    Args:
        packages: List of Package objects to process
        context: FastMCP context (for principal_id extraction)
        testing_ctx: Optional testing context for dry_run mode

    Returns:
        Tuple of (updated_packages, uploaded_ids_by_product):
        - updated_packages: New Package instances with creative_ids merged
        - uploaded_ids_by_product: Mapping of product_id -> uploaded creative IDs

    Raises:
        ToolError: If creative upload fails for any package (CREATIVES_UPLOAD_FAILED)

    Example:
        >>> packages = [Package(product_id="p1", creatives=[creative1, creative2])]
        >>> updated_pkgs, uploaded_ids = process_and_upload_package_creatives(packages, ctx)
        >>> # updated_pkgs[0].creative_ids contains uploaded IDs
        >>> assert uploaded_ids["p1"] == ["c1", "c2"]
    """
    import logging

    # Lazy import to avoid circular dependency
    from fastmcp.exceptions import ToolError

    from src.core.tools.creatives import _sync_creatives_impl

    logger = logging.getLogger(__name__)
    uploaded_by_product: dict[str, list[str]] = {}
    updated_packages: list[Package] = []

    for pkg_idx, pkg in enumerate(packages):
        # Skip packages without creatives (type system guarantees this attribute exists)
        if not pkg.creatives:
            updated_packages.append(pkg)  # No changes needed
            continue

        product_id = pkg.product_id or f"package_{pkg_idx}"
        logger.info(f"Processing {len(pkg.creatives)} creatives for package with product_id {product_id}")

        # Convert creatives to dicts with better error handling
        creative_dicts = []
        for creative_idx, creative in enumerate(pkg.creatives):
            try:
                if isinstance(creative, dict):
                    creative_dicts.append(creative)
                elif hasattr(creative, "model_dump"):
                    creative_dicts.append(creative.model_dump(exclude_none=True))
                else:
                    # Fail fast instead of risky conversion
                    raise TypeError(
                        f"Invalid creative type at index {creative_idx}: {type(creative).__name__}. "
                        f"Expected Creative model or dict."
                    )
            except Exception as e:
                raise ValueError(
                    f"Failed to serialize creative at index {creative_idx} for package {product_id}: {e}"
                ) from e

        try:
            # Step 1: Upload creatives to database via sync_creatives
            sync_response = _sync_creatives_impl(
                creatives=creative_dicts,
                patch=False,  # Full upsert for new creatives
                assignments=None,  # Assign separately after creation
                dry_run=testing_ctx.dry_run if testing_ctx else False,
                validation_mode="strict",
                push_notification_config=None,
                context=context,  # For principal_id extraction
            )

            # Extract creative IDs from response
            uploaded_ids = [
                result.creative_id
                for result in sync_response.creatives
                if result.creative_id
            ]

            logger.info(
                f"Synced {len(uploaded_ids)} creatives to database for package "
                f"with product_id {product_id}: {uploaded_ids}"
            )

            # Step 2: Upload creatives to ad server (GAM) to get platform_creative_id
            # This is critical - without platform_creative_id, creatives can't be associated with line items
            from src.core.helpers.adapter_helpers import get_adapter
            from src.core.helpers.principal_helpers import get_principal_from_context

            principal = get_principal_from_context(context)
            adapter = get_adapter(principal, dry_run=testing_ctx.dry_run if testing_ctx else False, testing_context=testing_ctx)

            # Convert Creative schema objects to adapter asset format
            assets = []
            for creative in pkg.creatives:
                try:
                    asset = _convert_creative_to_adapter_asset(creative, package_assignments=[product_id])
                    assets.append(asset)
                except Exception as conv_error:
                    logger.error(f"Failed to convert creative {creative.creative_id} to adapter format: {conv_error}")
                    # Continue with other creatives
                    continue

            # Upload to ad server
            if assets:
                logger.info(f"Uploading {len(assets)} creatives to ad server (GAM) for package {product_id}")
                try:
                    upload_result = adapter.add_creative_assets(assets)
                    logger.info(f"Successfully uploaded {len(assets)} creatives to GAM: {upload_result}")
                except Exception as upload_error:
                    logger.error(f"Failed to upload creatives to GAM: {upload_error}")
                    # Don't fail the entire operation - creatives are in database and can be uploaded later
                    logger.warning("Creatives saved to database but not uploaded to GAM yet. They will need manual upload.")

            # Create updated package with merged creative_ids (immutable)
            existing_ids = pkg.creative_ids or []
            merged_ids = [*existing_ids, *uploaded_ids]
            updated_pkg = pkg.model_copy(update={"creative_ids": merged_ids})
            updated_packages.append(updated_pkg)

            # Track uploads for return value
            uploaded_by_product[product_id] = uploaded_ids

        except Exception as e:
            error_msg = f"Failed to upload creatives for package with product_id {product_id}: {str(e)}"
            logger.error(error_msg)
            # Re-raise as ToolError for consistent error handling
            raise ToolError("CREATIVES_UPLOAD_FAILED", error_msg) from e

    return updated_packages, uploaded_by_product
