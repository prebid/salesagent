"""Creative asset helpers: attribute extraction, URL/text extraction, data building.

All pure data-extraction helpers for creative assets live here. This avoids
scattering the same RootModel-unwrapping logic across multiple modules.
"""

import logging
from typing import Any

from adcp.types import CreativeAsset
from adcp.types.generated_poc.creative.list_creatives_response import Creative as LibraryCreative
from pydantic import TypeAdapter, ValidationError

from src.core.exceptions import AdCPAdapterError
from src.core.helpers.creative_helpers import asset_value_attr as _extract_attr_from_asset_value

#: The typed asset map ``Creative.assets`` inherits from the library, as a validator. The one
#: place a stored or agent-produced asset map is checked against the pinned shape: on the
#: row-to-model read (listing) and on the generative write (processing).
ASSET_MAP: TypeAdapter[Any] = TypeAdapter(LibraryCreative.model_fields["assets"].annotation)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shared RootModel unwrapping
# ---------------------------------------------------------------------------


def _generative_assets(value: Any) -> Any:
    """The asset map a creative agent built, checked against the pinned shape before it is stored.

    The listing read validates every stored map and drops one that does not fit, so a
    writer that stores an unchecked map from an external agent would produce rows the
    listing silently hides. Refusing here keeps the row spec-shaped; the agent's output
    is the failure, and it travels as ``internal_detail`` for the server log.
    """
    try:
        return ASSET_MAP.validate_python(value)
    except ValidationError as exc:
        raise AdCPAdapterError(internal_detail=exc) from exc


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


def _build_creative_data(creative: CreativeAsset, url: str | None) -> dict[str, Any]:
    """Build the data dict for a creative from a CreativeAsset model.

    Extracts standard fields (url, click_url, width, height, duration) and
    optional fields (assets, snippet, snippet_type, template_variables).

    Args:
        creative: CreativeAsset model from the sync payload.
        url: Extracted URL (from _extract_url_from_assets).

    Returns:
        Data dict for storing in the creative's data field.
    """
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
    # Store AI provenance metadata (EU AI Act Article 50). The model goes in as it is:
    # the JSON column type serializes it at flush.
    provenance = getattr(creative, "provenance", None)
    if provenance is not None:
        data["provenance"] = provenance
    return data
