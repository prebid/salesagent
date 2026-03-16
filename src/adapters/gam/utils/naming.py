"""GAM-specific naming utilities.

Shared naming functions (template expansion, date formatting, context building)
live in src.core.utils.naming. This module contains only GAM-specific helpers.
"""

import logging
import re

logger = logging.getLogger(__name__)


def truncate_name_with_suffix(name: str, max_length: int = 255) -> str:
    """Truncate name to fit within max_length while preserving suffix in brackets.

    GAM has a 255-character limit for order and line item names. This function:
    1. Preserves the unique suffix (e.g., [media_buy_123])
    2. Truncates the base name to fit within the limit
    3. Adds ellipsis (...) to indicate truncation

    Args:
        name: Full name (e.g., "Long campaign name... [media_buy_123]")
        max_length: Maximum allowed length (default: 255 for GAM)

    Returns:
        Truncated name that fits within max_length

    Examples:
        >>> truncate_name_with_suffix("Short [id]", 255)
        "Short [id]"

        >>> truncate_name_with_suffix("Very long campaign name " * 20 + " [media_buy_123]", 255)
        "Very long campaign name Very long campaign name Very long campaign name Very long... [media_buy_123]"
    """
    if len(name) <= max_length:
        return name

    # Find the suffix (content in last brackets)
    suffix_match = re.search(r"\[([^\]]+)\]$", name)
    if suffix_match:
        suffix = f"[{suffix_match.group(1)}]"
        base_name = name[: suffix_match.start()].rstrip()
    else:
        # No suffix found, just truncate
        suffix = ""
        base_name = name

    # Calculate available space for base name
    # Reserve space for: suffix + " ... " (5 chars for ellipsis with spaces)
    ellipsis = " ..."
    available_length = max_length - len(suffix) - len(ellipsis)

    if available_length <= 0:
        # Edge case: suffix itself is too long (shouldn't happen with our IDs)
        logger.warning(f"Suffix alone exceeds max length: {suffix} ({len(suffix)} chars)")
        return suffix[:max_length]

    # Truncate base name and add ellipsis
    truncated_base = base_name[:available_length].rstrip()
    result = f"{truncated_base}{ellipsis}{suffix}"

    # Sanity check
    if len(result) > max_length:
        logger.error(f"Truncation failed: {len(result)} > {max_length}")
        return result[:max_length]

    logger.info(f"Truncated name from {len(name)} to {len(result)} chars (limit: {max_length})")
    return result
