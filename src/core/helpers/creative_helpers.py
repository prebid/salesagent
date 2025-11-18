"""Creative format parsing and asset conversion helpers."""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fastmcp import Context

    from src.core.schemas import Creative, PackageRequest
    from src.core.testing_context import TestingContext
    from src.core.tool_context import ToolContext

from src.core.schemas import Creative


def _extract_format_namespace(format_value: Any) -> tuple[str, str]:
    """Extract agent_url and format ID from format_id field (AdCP v2.4).

    Args:
        format_value: FormatId dict/object with agent_url+id fields

    Returns:
        Tuple of (agent_url, format_id) - both as strings

    Raises:
        ValueError: If format_value doesn't have required agent_url and id fields

    Note:
        Converts Pydantic AnyUrl types to strings for database compatibility.
        The adcp library's FormatId.agent_url is typed as AnyUrl, but PostgreSQL
        needs strings.
    """
    if isinstance(format_value, dict):
        agent_url = format_value.get("agent_url")
        format_id = format_value.get("id")
        if not agent_url or not format_id:
            raise ValueError(f"format_id must have both 'agent_url' and 'id' fields. Got: {format_value}")
        # Convert to string in case agent_url is AnyUrl from Pydantic model
        return str(agent_url), format_id
    if hasattr(format_value, "agent_url") and hasattr(format_value, "id"):
        # Convert AnyUrl to string for database compatibility
        return str(format_value.agent_url), format_value.id
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
    """Convert AdCP v1 Creative object to format expected by ad server adapters.

    Extracts data from the assets dict to build adapter-compatible format.
    """

    # Base asset object with common fields
    # Note: creative.format_id returns string via FormatId.__str__() (returns just the id field)
    # creative.format is the actual FormatId object
    format_str = str(creative.format_id)  # Convert FormatId to string ID

    asset: dict[str, Any] = {
        "creative_id": creative.creative_id,
        "name": creative.name,
        "format": format_str,  # Adapter expects string format ID
        "package_assignments": package_assignments,
    }

    # Extract data from assets dict (AdCP v1 spec)
    assets_dict = creative.assets if isinstance(creative.assets, dict) else {}

    # Determine format type from format_id (declarative, not heuristic)
    # Format IDs follow pattern: {type}_{variant} (e.g., display_300x250, video_instream_15s, native_content_feed)
    format_type = format_str.split("_")[0] if "_" in format_str else "display"  # Default to display

    # Find primary media asset based on format type (declarative role mapping)
    primary_asset = None
    primary_role = None

    # Declarative role mapping by format type
    if format_type == "video":
        # Video formats: Look for video asset first
        for role in ["video_file", "video", "main", "creative"]:
            if role in assets_dict:
                primary_asset = assets_dict[role]
                primary_role = role
                break
    elif format_type == "native":
        # Native formats: Look for native content assets
        for role in ["main", "creative", "content"]:
            if role in assets_dict:
                primary_asset = assets_dict[role]
                primary_role = role
                break
    else:  # display (image, html5, javascript, vast)
        # Display formats: Look for image/banner first, then code-based assets
        for role in ["banner_image", "image", "main", "creative", "content"]:
            if role in assets_dict:
                primary_asset = assets_dict[role]
                primary_role = role
                break

    # Fallback: If no asset found with expected roles, use first non-tracking asset
    if not primary_asset and assets_dict:
        for role, asset_data in assets_dict.items():
            # Skip tracking pixels and clickthrough URLs
            if isinstance(asset_data, dict) and asset_data.get("url_type") not in [
                "tracker_pixel",
                "tracker_script",
                "clickthrough",
            ]:
                primary_role = role
                primary_asset = asset_data
                break

    if primary_asset and isinstance(primary_asset, dict) and primary_role:
        # Detect asset type from AdCP v1 spec structure (no asset_type field in spec)
        # Detection based on presence of specific fields per asset schema

        # Check for VAST first (role name hint)
        if "vast" in primary_role.lower():
            # VAST asset (has content XOR url per spec)
            # Per spec: VAST must have EITHER content OR url, never both
            if "content" in primary_asset:
                asset["snippet"] = primary_asset["content"]
                asset["snippet_type"] = "vast_xml"
            elif "url" in primary_asset:
                asset["snippet"] = primary_asset["url"]
                asset["snippet_type"] = "vast_url"

            # Extract VAST duration if present (duration_ms → seconds)
            if "duration_ms" in primary_asset:
                asset["duration"] = primary_asset["duration_ms"] / 1000.0

        elif "content" in primary_asset and "url" not in primary_asset:
            # HTML or JavaScript asset (has content, no url)
            asset["snippet"] = primary_asset["content"]
            # Detect if JavaScript based on role or module_type
            if "javascript" in primary_role.lower() or "module_type" in primary_asset:
                asset["snippet_type"] = "javascript"
            else:
                asset["snippet_type"] = "html"

        elif "url" in primary_asset:
            # Image or Video asset (has url, no content)
            asset["media_url"] = primary_asset["url"]
            asset["url"] = primary_asset["url"]  # For backward compatibility

            # Extract dimensions (common to image and video)
            if "width" in primary_asset:
                asset["width"] = primary_asset["width"]
            if "height" in primary_asset:
                asset["height"] = primary_asset["height"]

            # Extract video duration (duration_ms → seconds)
            if "duration_ms" in primary_asset:
                asset["duration"] = primary_asset["duration_ms"] / 1000.0

    # Extract click URL from assets (URL asset with url_type="clickthrough")
    for _role, asset_data in assets_dict.items():
        if isinstance(asset_data, dict):
            # Check for clickthrough URL (per AdCP spec: url_type="clickthrough")
            if asset_data.get("url_type") == "clickthrough" and "url" in asset_data:
                asset["click_url"] = asset_data["url"]
                break

    # If no url_type found, fall back to role name matching
    if "click_url" not in asset:
        for role in ["click_url", "clickthrough", "click", "landing_page"]:
            if role in assets_dict:
                click_asset = assets_dict[role]
                if isinstance(click_asset, dict) and "url" in click_asset:
                    asset["click_url"] = click_asset["url"]
                    break

    # Extract tracking URLs from assets (per AdCP spec: url_type field)
    tracking_urls: dict[str, list[str] | str] = {}
    for _role, asset_data in assets_dict.items():
        if isinstance(asset_data, dict) and "url" in asset_data:
            url_type = asset_data.get("url_type", "")
            # Per spec: tracker_pixel for impression tracking, tracker_script for SDK
            if url_type in ["tracker_pixel", "tracker_script"]:
                tracking_urls.setdefault("impression", []).append(asset_data["url"])  # type: ignore[union-attr]
            # Note: clickthrough URLs go to asset["click_url"], not tracking_urls
            # (already extracted above in the click URL extraction section)

    if tracking_urls:
        asset["delivery_settings"] = {"tracking_urls": tracking_urls}

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
    packages: list["PackageRequest"],
    context: "Context | ToolContext",
    testing_ctx: "TestingContext | None" = None,
) -> tuple[list["PackageRequest"], dict[str, list[str]]]:
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
        >>> packages = [PackageRequest(product_id="p1", creatives=[creative1, creative2])]
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
    updated_packages: list[PackageRequest] = []

    for pkg_idx, pkg in enumerate(packages):
        # Skip packages without creatives (type system guarantees this attribute exists)
        if not pkg.creatives:
            updated_packages.append(pkg)  # No changes needed
            continue

        product_id = pkg.product_id or f"package_{pkg_idx}"
        logger.info(f"Processing {len(pkg.creatives)} creatives for package with product_id {product_id}")

        # Convert creatives to dicts with better error handling
        creative_dicts: list[dict[Any, Any]] = []
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
                ctx=context,  # For principal_id extraction
            )

            # Extract creative IDs from response
            uploaded_ids = [result.creative_id for result in sync_response.creatives if result.creative_id]

            logger.info(
                f"Synced {len(uploaded_ids)} creatives to database for package "
                f"with product_id {product_id}: {uploaded_ids}"
            )

            # Note: Ad server upload happens later in media buy creation flow
            # This function runs BEFORE media_buy_id exists, so we can't call
            # adapter.add_creative_assets() here (it requires media_buy_id, assets, today).
            # The creatives are synced to database above and will be uploaded to
            # the ad server during media buy creation when media_buy_id is available.

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
