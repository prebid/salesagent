"""Naming template utilities for GAM orders and line items.

Supports variable substitution with fallback syntax:
- {campaign_name} - Direct substitution
- {campaign_name|brand_name} - Use campaign_name, fall back to brand_name
- {date_range} - Formatted date range (e.g., "Oct 7-14, 2025")
- {month_year} - Month and year (e.g., "Oct 2025")
- {brand_name} - Brand from brand reference
- {buyer_ref} - Buyer's reference ID
- {product_name} - Product name from database (for line items)
- {package_name} - Package name from MediaPackage.name (for line items)
- {package_count} - Number of packages in order
- {package_index} - Package index in order (1-based, for line items)
"""

import logging
from datetime import datetime

logger = logging.getLogger(__name__)


def format_date_range(start_time: datetime, end_time: datetime) -> str:
    """Format date range for display.

    Examples:
        - Same month: "Oct 7-14, 2025"
        - Different months: "Oct 15 - Nov 5, 2025"
        - Different years: "Dec 28, 2024 - Jan 5, 2025"
    """
    if start_time.year != end_time.year:
        return f"{start_time.strftime('%b %d, %Y')} - {end_time.strftime('%b %d, %Y')}"
    elif start_time.month != end_time.month:
        return f"{start_time.strftime('%b %d')} - {end_time.strftime('%b %d, %Y')}"
    else:
        return f"{start_time.strftime('%b %d')}-{end_time.strftime('%d, %Y')}"


def format_month_year(start_time: datetime) -> str:
    """Format month and year for display.

    Example: "Oct 2025"
    """
    return start_time.strftime("%b %Y")


def apply_naming_template(
    template: str,
    context: dict,
) -> str:
    """Apply naming template with variable substitution and fallback support.

    Args:
        template: Template string with {variable} or {var1|var2|var3} syntax
        context: Dictionary of available variables

    Returns:
        Formatted string with variables substituted

    Examples:
        >>> apply_naming_template("{campaign_name} - {date_range}", {
        ...     "campaign_name": "Q1 Launch",
        ...     "date_range": "Oct 7-14, 2025"
        ... })
        "Q1 Launch - Oct 7-14, 2025"

        >>> apply_naming_template("{campaign_name|brand_name}", {
        ...     "campaign_name": None,
        ...     "brand_name": "Nike Shoes"
        ... })
        "Nike Shoes"
    """
    result = template

    # Find all {variable} or {var1|var2} patterns
    import re

    pattern = r"\{([^}]+)\}"

    for match in re.finditer(pattern, result):
        full_match = match.group(0)  # e.g., "{campaign_name|brand_name}"
        variables = match.group(1).split("|")  # e.g., ["campaign_name", "brand_name"]

        # Try each variable in order until we find a non-None, non-empty value
        value = None
        for var_name in variables:
            var_name = var_name.strip()
            if var_name in context:
                candidate = context[var_name]
                if candidate is not None and candidate != "":
                    value = str(candidate)
                    break

        # If no value found, use empty string (or could use first variable name as placeholder)
        if value is None:
            value = ""

        result = result.replace(full_match, value)

    return result


def build_order_name_context(
    request,
    packages: list,
    start_time: datetime,
    end_time: datetime,
) -> dict:
    """Build context dictionary for order name template.

    Args:
        request: CreateMediaBuyRequest object
        packages: List of MediaPackage objects
        start_time: Order start datetime
        end_time: Order end datetime

    Returns:
        Dictionary of variables available for template substitution
    """
    # Extract brand name from brand (BrandReference)
    brand_name = None
    if hasattr(request, "brand") and request.brand:
        brand = request.brand
        if hasattr(brand, "domain"):
            brand_name = brand.domain
        elif isinstance(brand, dict):
            brand_name = brand.get("domain")

    # campaign_name is no longer on CreateMediaBuyRequest per AdCP spec
    # Use brand_name or generate from buyer_ref as fallback
    campaign_name = brand_name or f"Campaign {request.buyer_ref}"

    return {
        "campaign_name": campaign_name,
        "brand_name": brand_name or "N/A",
        "promoted_offering": brand_name or "N/A",  # Backward compatibility alias
        "buyer_ref": request.buyer_ref,
        "date_range": format_date_range(start_time, end_time),
        "month_year": format_month_year(start_time),
        "package_count": len(packages),
        "start_date": start_time.strftime("%Y-%m-%d"),
        "end_date": end_time.strftime("%Y-%m-%d"),
    }


def build_line_item_name_context(
    order_name: str,
    product_name: str,
    package_name: str = None,
    package_index: int = None,
) -> dict:
    """Build context dictionary for line item name template.

    Args:
        order_name: Name of the parent order
        product_name: Name of the product/package (from database)
        package_name: Name from the package itself (from MediaPackage.name)
        package_index: Optional index of package in order (1-based)

    Returns:
        Dictionary of variables available for template substitution
    """
    context = {
        "order_name": order_name,
        "product_name": product_name,
        "package_name": package_name or product_name,  # Fallback to product_name
    }

    if package_index is not None:
        context["package_index"] = str(package_index)

    return context


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
    import re

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
