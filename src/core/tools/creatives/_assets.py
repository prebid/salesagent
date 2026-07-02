"""Creative asset helpers: attribute extraction, URL/text extraction, data building.

All pure data-extraction helpers for creative assets live here. This avoids
scattering the same RootModel-unwrapping logic across multiple modules.
"""

import logging
from typing import Any

from adcp.types import CreativeAsset
from pydantic import BaseModel

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shared RootModel unwrapping
# ---------------------------------------------------------------------------


def _extract_attr_from_asset_value(asset: Any, *attr_names: str) -> str | None:
    """Extract a named attribute from an SDK 5.7 asset value.

    SDK 5.7 wraps asset slot values in an ``Assets`` RootModel containing a
    ``list[AssetVariant]`` where each ``AssetVariant`` is itself a RootModel
    proxying the concrete typed asset.

    This helper walks three paths in priority order:
    1. **dict** -- legacy/test code that passes plain dicts.
    2. **RootModel** -- unwrap ``.root`` to get the variant list, then check
       the first variant's inner ``.root`` object and the variant itself.
    3. **Plain object** -- pre-5.7 single-asset models.

    Multiple *attr_names* are tried left-to-right (e.g. ``"content", "text"``);
    the first truthy value wins.
    """
    # Dict path
    if isinstance(asset, dict):
        for attr in attr_names:
            val = asset.get(attr)
            if val:
                return str(val)
        return None

    # RootModel path: Assets → list[AssetVariant] → concrete asset
    items = getattr(asset, "root", None)
    if isinstance(items, list) and items:
        first = items[0]
        # AssetVariant is also a RootModel wrapping the concrete asset
        inner = getattr(first, "root", first)
        for attr in attr_names:
            val = getattr(inner, attr, None) or getattr(first, attr, None)
            if val:
                return str(val)
        return None

    # Plain object (e.g. a single asset model, not wrapped in list)
    for attr in attr_names:
        val = getattr(asset, attr, None)
        if val:
            return str(val)
    return None


# ---------------------------------------------------------------------------
# Concrete extractors (thin wrappers)
# ---------------------------------------------------------------------------


def _extract_url_from_asset_value(asset: Any) -> str | None:
    """Extract a URL from an asset value (dict, RootModel/list, or object)."""
    return _extract_attr_from_asset_value(asset, "url")


def _extract_text_from_asset_value(asset: Any) -> str | None:
    """Extract text content from an SDK 5.7 asset value.

    Tries ``content`` first, then ``text`` (TextAsset uses ``content``,
    some legacy payloads use ``text``).
    """
    return _extract_attr_from_asset_value(asset, "content", "text")


def _extract_message_from_assets(creative: CreativeAsset) -> str | None:
    """Extract message/brief/prompt from creative assets using role priority.

    Checks 'message', 'brief', 'prompt' roles in priority order.
    Falls through to inputs[0].context_description if no asset role matches.
    Returns None when no message is found.
    """
    if creative.assets:
        for role, asset in creative.assets.items():
            if role in ["message", "brief", "prompt"]:
                text = _extract_text_from_asset_value(asset)
                if text:
                    return text

    if creative.inputs:
        inputs = creative.inputs or []
        if inputs:
            first_input = inputs[0]
            if isinstance(first_input, dict):
                return first_input.get("context_description")
            return getattr(first_input, "context_description", None)

    return None


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
            url = _extract_url_from_asset_value(asset)
            if url:
                logger.debug(f"[sync_creatives] Extracted URL from assets.{priority_key}.url")
                return str(url)

    # Priority 2: First available asset URL
    for asset_id, asset_data in assets.items():
        asset_url = _extract_url_from_asset_value(asset_data)
        if asset_url:
            logger.debug(f"[sync_creatives] Extracted URL from assets.{asset_id}.url (fallback)")
            return str(asset_url)

    return None


def _build_creative_data(
    creative: CreativeAsset,
    url: str | None,
    context: dict[str, Any] | BaseModel | None = None,
    media_buy_brand: dict[str, Any] | BaseModel | None = None,
) -> dict[str, Any]:
    """Build the data dict for a creative from a CreativeAsset model.

    Extracts standard fields (url, click_url, width, height, duration),
    optional fields (assets, snippet, snippet_type, template_variables),
    context if provided, and brand for adapter routing decisions.

    Args:
        creative: CreativeAsset model from the sync payload.
        url: Extracted URL (from _extract_url_from_assets).
        context: Optional application-level context per AdCP spec.
        media_buy_brand: Optional brand — either a plain dict or a BrandReference
            Pydantic model. Serialized with ``exclude_none=True`` before storage
            so only populated fields (e.g. ``{"domain": "acme.com"}``) are written
            to the JSONType column, not the full model with all-None optional fields.

    Returns:
        Data dict for storing in the creative's data field.
    """
    if context is not None and not isinstance(context, dict):
        context = context.model_dump(mode="json")

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
    # Store AI provenance metadata (EU AI Act Article 50)
    provenance = getattr(creative, "provenance", None)
    if provenance is not None:
        if isinstance(provenance, BaseModel):
            data["provenance"] = provenance.model_dump(mode="json")
        elif isinstance(provenance, dict):
            data["provenance"] = provenance
    # Persist brand so adapters can read brand.domain from stored creative data.
    # Accepts either a plain dict or a BrandReference Pydantic model — serialize
    # with exclude_none=True so only populated fields (e.g. {"domain": "acme.com"})
    # are stored, not the full model with all-None optional fields.
    if media_buy_brand is not None:
        if isinstance(media_buy_brand, BaseModel):
            data["brand"] = media_buy_brand.model_dump(mode="json", exclude_none=True)
        else:
            data["brand"] = media_buy_brand
    return data
