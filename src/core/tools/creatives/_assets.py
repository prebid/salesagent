"""Creative asset helpers: URL extraction and data building."""

import logging
from typing import Any

from adcp.types.generated_poc.core.creative_asset import CreativeAsset
from pydantic import BaseModel

logger = logging.getLogger(__name__)


def _extract_url_from_assets(creative: CreativeAsset) -> str | None:
    """Extract the best URL from a creative's assets.

    Checks creative.url first, then iterates asset keys with priority order
    (main, image, video, creative, content), falls back to first available URL.

    Args:
        creative: CreativeAsset model from the sync payload.

    Returns:
        The extracted URL string, or None if no URL found.
    """
    url = getattr(creative, "url", None)
    if url or not creative.assets:
        return url

    assets = creative.assets

    # Priority 1: Try common asset_ids
    for priority_key in ["main", "image", "video", "creative", "content"]:
        if priority_key in assets:
            asset = assets[priority_key]
            url = asset.get("url") if isinstance(asset, dict) else getattr(asset, "url", None)
            if url:
                logger.debug(f"[sync_creatives] Extracted URL from assets.{priority_key}.url")
                return str(url)

    # Priority 2: First available asset URL
    for asset_id, asset_data in assets.items():
        asset_url = asset_data.get("url") if isinstance(asset_data, dict) else getattr(asset_data, "url", None)
        if asset_url:
            logger.debug(f"[sync_creatives] Extracted URL from assets.{asset_id}.url (fallback)")
            return str(asset_url)

    return None


def _build_creative_data(
    creative: CreativeAsset, url: str | None, context: dict[str, Any] | BaseModel | None = None
) -> dict[str, Any]:
    """Build the data dict for a creative from a CreativeAsset model.

    Extracts standard fields (url, click_url, width, height, duration),
    optional fields (assets, snippet, snippet_type, template_variables),
    and context if provided.

    Args:
        creative: CreativeAsset model from the sync payload.
        url: Extracted URL (from _extract_url_from_assets).
        context: Optional application-level context per AdCP spec.

    Returns:
        Data dict for storing in the creative's data field.
    """
    if context is not None and not isinstance(context, dict):
        context = context.model_dump()

    data: dict[str, Any] = {
        "url": url,
        "click_url": getattr(creative, "click_url", None),
        "width": getattr(creative, "width", None),
        "height": getattr(creative, "height", None),
        "duration": getattr(creative, "duration", None),
    }
    if creative.assets:
        data["assets"] = creative.assets
    snippet = getattr(creative, "snippet", None)
    if snippet:
        data["snippet"] = snippet
        data["snippet_type"] = getattr(creative, "snippet_type", None)
    template_variables = getattr(creative, "template_variables", None)
    if template_variables:
        data["template_variables"] = template_variables
    if context is not None:
        data["context"] = context
    return data
