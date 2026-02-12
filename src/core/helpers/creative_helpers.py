"""Creative format parsing and asset conversion helpers."""

import logging
from typing import TYPE_CHECKING, Any, TypedDict

if TYPE_CHECKING:
    from fastmcp import Context

    from src.core.database.models import Product as DBProduct
    from src.core.schemas import Creative, FormatId, PackageRequest, Product
    from src.core.testing_context import TestingContext
    from src.core.tool_context import ToolContext

from src.core.schemas import Creative

logger = logging.getLogger(__name__)


class FormatParameters(TypedDict, total=False):
    """Optional format parameters for parameterized FormatId (AdCP 2.5)."""

    width: int
    height: int
    duration_ms: float


class FormatInfo(TypedDict):
    """Complete format information extracted from FormatId."""

    agent_url: str
    format_id: str
    parameters: FormatParameters | None


def _extract_format_info(format_value: Any) -> FormatInfo:
    """Extract complete format information from format_id field (AdCP 2.5).

    Args:
        format_value: FormatId dict/object with agent_url, id, and optional parameters

    Returns:
        FormatInfo with agent_url, format_id, and optional parameters (width, height, duration_ms)

    Raises:
        ValueError: If format_value doesn't have required agent_url and id fields

    Note:
        This function supports parameterized format templates (AdCP 2.5).
        Parameters are only included if they are present and non-None.
    """
    agent_url: str
    format_id: str
    parameters: FormatParameters | None = None

    if isinstance(format_value, dict):
        agent_url_val = format_value.get("agent_url")
        format_id_val = format_value.get("id")
        if not agent_url_val or not format_id_val:
            raise ValueError(f"format_id must have both 'agent_url' and 'id' fields. Got: {format_value}")
        agent_url = str(agent_url_val)
        format_id = format_id_val

        # Extract optional parameters
        params: FormatParameters = {}
        if format_value.get("width") is not None:
            params["width"] = int(format_value["width"])
        if format_value.get("height") is not None:
            params["height"] = int(format_value["height"])
        if format_value.get("duration_ms") is not None:
            params["duration_ms"] = float(format_value["duration_ms"])
        if params:
            parameters = params

    elif hasattr(format_value, "agent_url") and hasattr(format_value, "id"):
        agent_url = str(format_value.agent_url)
        format_id = format_value.id

        # Extract optional parameters from object
        params = {}
        if getattr(format_value, "width", None) is not None:
            params["width"] = int(format_value.width)
        if getattr(format_value, "height", None) is not None:
            params["height"] = int(format_value.height)
        if getattr(format_value, "duration_ms", None) is not None:
            params["duration_ms"] = float(format_value.duration_ms)
        if params:
            parameters = params

    elif isinstance(format_value, str):
        raise ValueError(
            f"format_id must be an object with 'agent_url' and 'id' fields (AdCP v2.4). "
            f"Got string: '{format_value}'. "
            f"String format_id is no longer supported - all formats must be namespaced."
        )
    else:
        raise ValueError(f"Invalid format_id format. Expected object with agent_url and id, got: {type(format_value)}")

    return {"agent_url": agent_url, "format_id": format_id, "parameters": parameters}


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
    Supports parameterized format templates (AdCP 2.5) for dimensions.
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

    # Extract dimensions from FormatId parameters (AdCP 2.5 format templates)
    # This is the primary source of truth for parameterized formats
    format_id_obj = creative.format_id
    if hasattr(format_id_obj, "width") and format_id_obj.width is not None:
        asset["width"] = format_id_obj.width
    if hasattr(format_id_obj, "height") and format_id_obj.height is not None:
        asset["height"] = format_id_obj.height
    if hasattr(format_id_obj, "duration_ms") and format_id_obj.duration_ms is not None:
        # Convert to seconds for adapter compatibility
        asset["duration"] = format_id_obj.duration_ms / 1000.0

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
                "tracker_redirect",
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
            if url_type in ["tracker_pixel", "tracker_script"]:
                impression_list = tracking_urls.setdefault("impression", [])
                if isinstance(impression_list, list):
                    impression_list.append(asset_data["url"])
            elif url_type == "tracker_redirect":
                click_list = tracking_urls.setdefault("click", [])
                if isinstance(click_list, list):
                    click_list.append(asset_data["url"])

    # Role name fallback for impression tracker (same pattern as click_url)
    if "impression" not in tracking_urls:
        for role_name in ["impression_tracker", "tracker_pixel", "pixel"]:
            if role_name in assets_dict:
                tracker_asset = assets_dict[role_name]
                if isinstance(tracker_asset, dict) and "url" in tracker_asset:
                    impression_list = tracking_urls.setdefault("impression", [])
                    if isinstance(impression_list, list):
                        impression_list.append(tracker_asset["url"])
                    break

    # Role name fallback for click tracker
    if "click" not in tracking_urls:
        for role_name in ["click_tracker", "tracker_redirect", "redirect_tracker"]:
            if role_name in assets_dict:
                tracker_asset = assets_dict[role_name]
                if isinstance(tracker_asset, dict) and "url" in tracker_asset:
                    click_list = tracking_urls.setdefault("click", [])
                    if isinstance(click_list, list):
                        click_list.append(tracker_asset["url"])
                    break

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


def validate_creative_format_against_product(
    creative_format_id: "FormatId",
    product: "Product | DBProduct",
) -> tuple[bool, str | None]:
    """Validate that a creative's format_id matches the product's supported formats.

    Args:
        creative_format_id: FormatId object with agent_url and id fields
        product: Product or DBProduct object with format_ids field

    Returns:
        Tuple of (is_valid, error_message):
        - is_valid: True if creative format matches the product
        - error_message: Descriptive error message if is_valid is False, None otherwise

    Note:
        Packages have exactly one product, so this is a binary check (matches or doesn't).
        Format IDs should already be normalized before calling this function.

    Example:
        >>> from src.core.schemas import FormatId, Product
        >>> creative_format = FormatId(agent_url="https://creative.example.com", id="banner_300x250")
        >>> is_valid, error = validate_creative_format_against_product(creative_format, product)
        >>> if not is_valid:
        ...     raise ValueError(error)
    """
    # Extract format_ids from product
    product_format_ids = product.format_ids or []
    product_id = product.product_id
    product_name = product.name

    # Products with no format restrictions accept all creatives
    if not product_format_ids:
        return True, None

    # Extract creative's format_id components
    creative_agent_url = creative_format_id.agent_url
    creative_id = creative_format_id.id

    if not creative_agent_url or not creative_id:
        return False, "Creative format_id is missing agent_url or id"

    # Helper to normalize URLs for comparison (strip trailing slashes)
    # Pydantic AnyUrl adds trailing slash when converting to string, causing mismatches
    def normalize_url(url_val: Any) -> str:
        if not url_val:
            return ""
        return str(url_val).rstrip("/")

    # Simple equality check: does creative's format_id match any product format_id?
    for product_format in product_format_ids:
        # Handle both FormatId objects and dicts (database stores as dicts)
        if isinstance(product_format, dict):
            product_agent_url: str | None = product_format.get("agent_url")
            product_fmt_id: str | None = product_format.get("id") or product_format.get("format_id")
        elif hasattr(product_format, "agent_url") and hasattr(product_format, "id"):
            # Convert AnyUrl to string for consistent comparison
            product_agent_url = str(product_format.agent_url) if product_format.agent_url else None
            product_fmt_id = product_format.id
        else:
            # Skip invalid format entries
            continue

        if not product_agent_url or not product_fmt_id:
            continue

        # Format IDs match if both agent_url and id are equal (normalized to strip trailing slashes)
        if normalize_url(creative_agent_url) == normalize_url(product_agent_url) and creative_id == product_fmt_id:
            return True, None

    # Build error message with supported formats
    supported_formats = []
    for fmt in product_format_ids:
        # Handle both FormatId objects and dicts
        if isinstance(fmt, dict):
            agent_url: str | None = fmt.get("agent_url")
            fmt_id: str | None = fmt.get("id") or fmt.get("format_id")
        elif hasattr(fmt, "agent_url") and hasattr(fmt, "id"):
            # Convert AnyUrl to string for consistent handling
            agent_url = str(fmt.agent_url) if fmt.agent_url else None
            fmt_id = fmt.id
        else:
            continue

        if agent_url and fmt_id:
            # Use normalized URL in display to avoid double slashes
            supported_formats.append(f"{normalize_url(agent_url)}/{fmt_id}")

    creative_format_display = f"{normalize_url(creative_agent_url)}/{creative_id}"
    error_msg = (
        f"Creative format '{creative_format_display}' does not match product '{product_name}' ({product_id}). "
        f"Supported formats: {supported_formats}"
    )

    return False, error_msg


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
                    # Use mode='json' to serialize Pydantic types (AnyUrl, etc.) to JSON-compatible primitives
                    creative_dicts.append(creative.model_dump(exclude_none=True, mode="json"))
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
                # AdCP 2.5: Full upsert semantics (no patch parameter)
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


# =============================================================================
# URL Extraction Helpers
# =============================================================================
# These functions extract media URLs, click-through URLs, and impression tracker
# URLs from creative data. They are used by both media_buy_create.py and
# creatives.py to ensure consistent URL extraction logic.
# =============================================================================

# Asset types that contain media content with URLs
# Based on AdCP creative agent format specs:
# - image: banner_image, main_image, thumbnail, billboard_image, etc.
# - video: video_file
# - audio: audio_file
# NOT included:
# - url: Used for clickthrough URLs and trackers (url_type field distinguishes them)
# - html/javascript/vast/text: These have 'content' field, not 'url' field
MEDIA_ASSET_TYPES = {"image", "video", "audio"}

# Known media asset IDs for fallback when format spec is not available
# Based on AdCP creative agent format specs + common conventions
MEDIA_ASSET_FALLBACK_IDS = {
    # image assets
    "banner_image",
    "billboard_image",
    "icon",
    "main_image",
    "product_image",
    "screen_image",
    "thumbnail",
    # video assets
    "video_file",
    # audio assets
    "audio_file",
    # common conventions
    "main",
    "image",
    "video",
    "audio",
}

# Common asset IDs for clickthrough URLs
CLICKTHROUGH_ASSET_IDS = {
    "click_url",
    "clickthrough",
    "click",
    "landing_page",
    "landing_url",
    "destination_url",
}

# Common asset IDs for impression trackers
IMPRESSION_TRACKER_ASSET_IDS = {
    "impression_tracker",
    "tracker_pixel",
    "pixel",
    "impression_pixel",
}


def extract_media_url_and_dimensions(
    creative_data: dict[str, Any], format_spec: Any | None
) -> tuple[str | None, int | None, int | None]:
    """Extract media URL and dimensions from creative data.

    All production creatives now use AdCP v2.4+ format with data.assets[asset_id]
    containing typed asset objects per the creative format specification.

    Extraction priority:
    1. Format spec assets with asset_type in {image, video, audio}
    2. Known media asset IDs (fallback allowlist)
    3. Root-level 'url', 'width', 'height' fields (legacy/simple creative fallback)

    Args:
        creative_data: Creative data dict from database
        format_spec: Format specification with assets (or deprecated assets_required)

    Returns:
        Tuple of (url, width, height). Values are None if not found.

    Note:
        - Media URL extracted from asset types: image, video, audio
        - Dimensions extracted from asset types: image, video
        - Type validation: width/height must be int or coercible to int
        - Uses adcp.utils.get_individual_assets() for backward compatibility with assets_required
    """
    # Lazy import to avoid circular dependencies
    from adcp.types.generated_poc.core.format import Assets
    from adcp.utils import get_individual_assets, has_assets

    url = None
    width = None
    height = None

    # Priority 1: Use format spec to find media assets
    if creative_data.get("assets") and format_spec and has_assets(format_spec):
        for asset_spec in get_individual_assets(format_spec):
            # Type guard: get_individual_assets only returns Assets, not Assets5 (repeatable groups)
            if not isinstance(asset_spec, Assets):
                continue
            asset_type = str(asset_spec.asset_type).lower()
            if asset_type in MEDIA_ASSET_TYPES:
                asset_id = asset_spec.asset_id
                if asset_id in creative_data["assets"]:
                    asset_obj = creative_data["assets"][asset_id]
                    if isinstance(asset_obj, dict):
                        # Extract URL
                        if not url and asset_obj.get("url"):
                            url = asset_obj["url"]
                            logger.debug(f"Extracted media URL from format spec asset '{asset_id}'")

                        # Extract dimensions (only for image/video)
                        if asset_type in ["image", "video"]:
                            raw_width = asset_obj.get("width")
                            raw_height = asset_obj.get("height")
                            if raw_width is not None and not width:
                                try:
                                    width = int(raw_width)
                                except (ValueError, TypeError):
                                    logger.warning(
                                        f"Invalid width type in creative assets: {raw_width} (type={type(raw_width)})"
                                    )
                            if raw_height is not None and not height:
                                try:
                                    height = int(raw_height)
                                except (ValueError, TypeError):
                                    logger.warning(
                                        f"Invalid height type in creative assets: {raw_height} (type={type(raw_height)})"
                                    )

                        # Stop if we found everything
                        if url and width and height:
                            break

    # Priority 2: Fallback to known media asset IDs (allowlist approach)
    if not url or not width or not height:
        if creative_data.get("assets"):
            for asset_id in MEDIA_ASSET_FALLBACK_IDS:
                if asset_id in creative_data["assets"]:
                    asset_obj = creative_data["assets"][asset_id]
                    if isinstance(asset_obj, dict):
                        # Extract URL if not found yet
                        if not url and asset_obj.get("url"):
                            url = asset_obj["url"]
                            logger.debug(f"Extracted media URL from fallback asset '{asset_id}'")

                        # Extract dimensions if not found yet
                        if not width or not height:
                            raw_width = asset_obj.get("width")
                            raw_height = asset_obj.get("height")
                            if raw_width is not None and not width:
                                try:
                                    width = int(raw_width)
                                except (ValueError, TypeError):
                                    pass
                            if raw_height is not None and not height:
                                try:
                                    height = int(raw_height)
                                except (ValueError, TypeError):
                                    pass

                        # Stop if we found everything
                        if url and width and height:
                            break

    # Priority 3: Fallback to root-level fields for simple creatives without assets
    # This supports legacy/simple creative formats that don't use the assets structure
    if not url:
        root_url = creative_data.get("url")
        if root_url:
            url = root_url
            logger.debug("Extracted media URL from root-level 'url' field (legacy fallback)")

    if not width:
        raw_width = creative_data.get("width")
        if raw_width is not None:
            try:
                width = int(raw_width)
            except (ValueError, TypeError):
                pass

    if not height:
        raw_height = creative_data.get("height")
        if raw_height is not None:
            try:
                height = int(raw_height)
            except (ValueError, TypeError):
                pass

    return url, width, height


def extract_click_url(
    creative_data: dict[str, Any],
    format_spec: Any | None,
    apply_macro_substitution: bool = True,
) -> str | None:
    """Extract click-through URL from creative data.

    Extraction priority:
    1. Format spec assets with requirements.url_type == 'clickthrough'
    2. Fallback to known clickthrough asset_id names (click_url, etc.)

    Args:
        creative_data: Creative data dict from database
        format_spec: Format specification with assets
        apply_macro_substitution: If True, apply AdCP-to-GAM macro substitution

    Returns:
        Click-through URL string (optionally with macros substituted), or None if not found.
    """
    # Lazy import to avoid circular dependencies
    from adcp.types.generated_poc.core.format import Assets
    from adcp.utils import get_individual_assets, has_assets

    click_url = None

    # Priority 1: Use format spec to find clickthrough URL (url_type == 'clickthrough')
    if creative_data.get("assets") and format_spec and has_assets(format_spec):
        for asset_spec in get_individual_assets(format_spec):
            if not isinstance(asset_spec, Assets):
                continue
            asset_type = str(asset_spec.asset_type).lower()
            if asset_type == "url":
                requirements = getattr(asset_spec, "requirements", None)
                if requirements:
                    req_url_type = None
                    if isinstance(requirements, dict):
                        req_url_type = requirements.get("url_type")
                    elif hasattr(requirements, "url_type"):
                        req_url_type = requirements.url_type
                    if req_url_type == "clickthrough":
                        asset_id = asset_spec.asset_id
                        if asset_id in creative_data["assets"]:
                            asset_obj = creative_data["assets"][asset_id]
                            if isinstance(asset_obj, dict) and asset_obj.get("url"):
                                click_url = asset_obj["url"]
                                logger.debug(f"Extracted click URL from format spec asset '{asset_id}'")
                                break

    # Priority 2: Fallback to known clickthrough asset_id names
    if not click_url and creative_data.get("assets"):
        for asset_id in CLICKTHROUGH_ASSET_IDS:
            if asset_id in creative_data["assets"]:
                asset_obj = creative_data["assets"][asset_id]
                if isinstance(asset_obj, dict) and asset_obj.get("url"):
                    click_url = asset_obj["url"]
                    logger.debug(f"Extracted click URL from fallback asset '{asset_id}'")
                    break

    # Apply macro substitution if requested
    if click_url and apply_macro_substitution:
        try:
            from src.adapters.gam.utils.macros import substitute_macros

            click_url = substitute_macros(click_url)
        except ImportError:
            # GAM adapter not available, skip macro substitution
            pass

    return click_url


def extract_impression_tracker_url(
    creative_data: dict[str, Any], format_spec: Any | None
) -> str | None:
    """Extract impression tracker URL from creative data.

    Looks for impression tracker URL in the creative's assets, checking:
    1. Format spec assets with asset_type 'url' AND requirements.url_type == 'tracker_pixel'
    2. Assets with url_type 'tracker_pixel'
    3. Assets with common impression tracker asset_id names

    Args:
        creative_data: Creative data dict from database
        format_spec: Format specification with assets (or deprecated assets_required)

    Returns:
        Impression tracker URL string or None if not found.
    """
    # Lazy import to avoid circular dependencies
    from adcp.types.generated_poc.core.format import Assets
    from adcp.utils import get_individual_assets, has_assets

    tracker_url = None

    # Priority 1: Use format spec to find impression tracker
    # Match url assets where requirements.url_type == 'tracker_pixel'
    if creative_data.get("assets") and format_spec and has_assets(format_spec):
        for asset_spec in get_individual_assets(format_spec):
            if not isinstance(asset_spec, Assets):
                continue
            asset_type = str(asset_spec.asset_type).lower()
            if asset_type == "url":
                # Check if this is a tracker_pixel by looking at requirements.url_type
                requirements = getattr(asset_spec, "requirements", None)
                if requirements:
                    req_url_type = None
                    if isinstance(requirements, dict):
                        req_url_type = requirements.get("url_type")
                    elif hasattr(requirements, "url_type"):
                        req_url_type = requirements.url_type
                    # Only match tracker_pixel type
                    if req_url_type == "tracker_pixel":
                        asset_id = asset_spec.asset_id
                        if asset_id in creative_data["assets"]:
                            asset_obj = creative_data["assets"][asset_id]
                            if isinstance(asset_obj, dict) and asset_obj.get("url"):
                                tracker_url = asset_obj["url"]
                                logger.debug(f"Extracted impression tracker from format spec asset '{asset_id}'")
                                break

    # Priority 2: Look for assets with tracker_pixel url_type
    if not tracker_url and creative_data.get("assets"):
        for asset_id, asset_obj in creative_data["assets"].items():
            if isinstance(asset_obj, dict):
                url_type = asset_obj.get("url_type", "")
                if url_type == "tracker_pixel" and asset_obj.get("url"):
                    tracker_url = asset_obj["url"]
                    logger.debug(f"Extracted impression tracker from asset '{asset_id}' with url_type='tracker_pixel'")
                    break

    # Priority 3: Check common impression tracker asset_id names
    if not tracker_url and creative_data.get("assets"):
        for asset_id in IMPRESSION_TRACKER_ASSET_IDS:
            if asset_id in creative_data["assets"]:
                asset_obj = creative_data["assets"][asset_id]
                if isinstance(asset_obj, dict) and asset_obj.get("url"):
                    tracker_url = asset_obj["url"]
                    logger.debug(f"Extracted impression tracker from fallback asset '{asset_id}'")
                    break

    return tracker_url
