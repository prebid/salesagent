"""Helper modules for Prebid Sales Agent.

This package contains modular helper functions extracted from main.py for better maintainability:
- adapter_helpers: Adapter instance creation and configuration
- creative_helpers: Creative format parsing and asset conversion
- activity_helpers: Tool activity logging and tracking
- context_helpers: Context extraction for authentication and tenant setup
"""

from src.core.helpers.activity_helpers import log_tool_activity
from src.core.helpers.adapter_helpers import get_adapter
from src.core.helpers.context_helpers import get_principal_id_from_context
from src.core.helpers.creative_helpers import (
    CLICKTHROUGH_ASSET_IDS,
    IMPRESSION_TRACKER_ASSET_IDS,
    MEDIA_ASSET_FALLBACK_IDS,
    MEDIA_ASSET_TYPES,
    FormatInfo,
    FormatParameters,
    _convert_creative_to_adapter_asset,
    _detect_snippet_type,
    _extract_format_info,
    _extract_format_namespace,
    _normalize_format_value,
    _validate_creative_assets,
    extract_click_url,
    extract_impression_tracker_url,
    extract_media_url_and_dimensions,
    validate_creative_format_against_product,
)

__all__ = [
    "get_adapter",
    "log_tool_activity",
    "get_principal_id_from_context",
    "_extract_format_info",
    "_extract_format_namespace",
    "_normalize_format_value",
    "_validate_creative_assets",
    "_convert_creative_to_adapter_asset",
    "_detect_snippet_type",
    "validate_creative_format_against_product",
    "FormatInfo",
    "FormatParameters",
    # URL extraction helpers
    "extract_media_url_and_dimensions",
    "extract_click_url",
    "extract_impression_tracker_url",
    # Constants
    "MEDIA_ASSET_TYPES",
    "MEDIA_ASSET_FALLBACK_IDS",
    "CLICKTHROUGH_ASSET_IDS",
    "IMPRESSION_TRACKER_ASSET_IDS",
]
