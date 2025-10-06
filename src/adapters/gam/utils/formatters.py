"""
Formatters for GAM data display and processing.

This module provides utilities for formatting:
- Currency values for GAM API
- Dates for GAM API requirements
- Targeting data for display
- File sizes for human readability
- Data sanitization for logging
"""

import logging
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)


def format_currency(amount: float, currency_code: str = "USD") -> dict[str, Any]:
    """
    Format currency amount for GAM API.

    Args:
        amount: Amount in major currency units (e.g., dollars)
        currency_code: ISO 4217 currency code

    Returns:
        GAM Money object format
    """
    # GAM API expects micro amounts (amount * 1,000,000)
    micro_amount = int(amount * 1_000_000)

    return {"currencyCode": currency_code, "microAmount": str(micro_amount)}  # GAM requires string representation


def format_date_for_gam(date_input: datetime | str) -> dict[str, Any]:
    """
    Format date for GAM API Date object.

    Args:
        date_input: datetime object or ISO date string

    Returns:
        GAM Date object format
    """
    if isinstance(date_input, str):
        date_obj = datetime.fromisoformat(date_input.replace("Z", "+00:00"))
    else:
        date_obj = date_input

    # Ensure UTC timezone
    if date_obj.tzinfo is None:
        date_obj = date_obj.replace(tzinfo=UTC)

    return {"year": date_obj.year, "month": date_obj.month, "day": date_obj.day}


def format_datetime_for_gam(datetime_input: datetime | str) -> dict[str, Any]:
    """
    Format datetime for GAM API DateTime object.

    Args:
        datetime_input: datetime object or ISO datetime string

    Returns:
        GAM DateTime object format
    """
    if isinstance(datetime_input, str):
        dt_obj = datetime.fromisoformat(datetime_input.replace("Z", "+00:00"))
    else:
        dt_obj = datetime_input

    # Ensure UTC timezone
    if dt_obj.tzinfo is None:
        dt_obj = dt_obj.replace(tzinfo=UTC)

    return {
        "date": format_date_for_gam(dt_obj),
        "hour": dt_obj.hour,
        "minute": dt_obj.minute,
        "second": dt_obj.second,
        "timeZoneId": "UTC",  # GAM prefers explicit timezone
    }


def format_targeting_for_display(targeting: dict[str, Any]) -> dict[str, Any]:
    """
    Format targeting data for human-readable display.

    Args:
        targeting: GAM targeting object

    Returns:
        Formatted targeting summary
    """
    display = {}

    # Geography targeting
    if "geoTargeting" in targeting:
        geo = targeting["geoTargeting"]
        geo_display = {}

        if "targetedLocations" in geo:
            locations = [loc.get("displayName", loc.get("id", "Unknown")) for loc in geo["targetedLocations"]]
            geo_display["included"] = locations[:5]  # Show first 5
            if len(locations) > 5:
                geo_display["included_count"] = len(locations)

        if "excludedLocations" in geo:
            excluded = [loc.get("displayName", loc.get("id", "Unknown")) for loc in geo["excludedLocations"]]
            geo_display["excluded"] = excluded[:3]  # Show first 3
            if len(excluded) > 3:
                geo_display["excluded_count"] = len(excluded)

        if geo_display:
            display["geography"] = geo_display

    # Technology targeting
    if "technologyTargeting" in targeting:
        tech = targeting["technologyTargeting"]
        tech_display = {}

        if "deviceCategories" in tech:
            devices = [dev.get("displayName", dev.get("id", "Unknown")) for dev in tech["deviceCategories"]]
            tech_display["devices"] = devices

        if "operatingSystems" in tech:
            os_list = [os.get("displayName", os.get("id", "Unknown")) for os in tech["operatingSystems"]]
            tech_display["operating_systems"] = os_list

        if "browsers" in tech:
            browsers = [browser.get("displayName", browser.get("id", "Unknown")) for browser in tech["browsers"]]
            tech_display["browsers"] = browsers

        if tech_display:
            display["technology"] = tech_display

    # Custom targeting
    if "customTargeting" in targeting:
        custom = targeting["customTargeting"]
        custom_display = {}

        for key_id, value_ids in custom.items():
            # In a real implementation, you'd look up key/value names
            custom_display[f"key_{key_id}"] = f"{len(value_ids)} values"

        if custom_display:
            display["custom"] = custom_display

    # Day/time targeting
    if "dayPartTargeting" in targeting:
        daypart = targeting["dayPartTargeting"]
        if "dayParts" in daypart:
            day_parts = []
            for part in daypart["dayParts"]:
                day = part.get("dayOfWeek", "Unknown")
                start_hour = part.get("startTime", {}).get("hour", 0)
                end_hour = part.get("endTime", {}).get("hour", 24)
                day_parts.append(f"{day} {start_hour:02d}:00-{end_hour:02d}:00")

            display["day_time"] = day_parts

    return display


def format_file_size(size_bytes: int) -> str:
    """
    Format file size in human-readable format.

    Args:
        size_bytes: Size in bytes

    Returns:
        Human-readable size string
    """
    if size_bytes == 0:
        return "0 B"

    size_names = ["B", "KB", "MB", "GB", "TB"]
    size = float(size_bytes)
    i = 0

    while size >= 1024.0 and i < len(size_names) - 1:
        size /= 1024.0
        i += 1

    return f"{size:.1f} {size_names[i]}"


def format_percentage(value: float, decimal_places: int = 1) -> str:
    """
    Format decimal as percentage.

    Args:
        value: Decimal value (0.15 = 15%)
        decimal_places: Number of decimal places

    Returns:
        Formatted percentage string
    """
    percentage = value * 100
    return f"{percentage:.{decimal_places}f}%"


def format_number_with_commas(number: int | float) -> str:
    """
    Format number with thousand separators.

    Args:
        number: Number to format

    Returns:
        Formatted number string
    """
    return f"{number:,}"


def sanitize_for_logging(data: Any, max_length: int = 200) -> str:
    """
    Sanitize data for safe logging.

    Args:
        data: Data to sanitize
        max_length: Maximum string length

    Returns:
        Safe string representation
    """
    # Convert to string
    if isinstance(data, dict):
        # Remove sensitive fields
        safe_data = {k: v for k, v in data.items() if k.lower() not in ["password", "token", "key", "secret", "auth"]}
        data_str = str(safe_data)
    elif isinstance(data, list | tuple):
        # Limit list size for logging
        if len(data) > 10:
            data_str = f"[{len(data)} items: {str(data[:3])}...{str(data[-2:])}]"
        else:
            data_str = str(data)
    else:
        data_str = str(data)

    # Truncate if too long
    if len(data_str) > max_length:
        data_str = data_str[: max_length - 3] + "..."

    return data_str


def format_budget_summary(budget: dict[str, Any]) -> str:
    """
    Format budget data for display.

    Args:
        budget: GAM budget object

    Returns:
        Human-readable budget summary
    """
    if not budget:
        return "No budget set"

    micro_amount = budget.get("microAmount", "0")
    currency_code = budget.get("currencyCode", "USD")

    # Convert micro amount to major currency units
    amount = float(micro_amount) / 1_000_000 if micro_amount else 0

    return f"{currency_code} {amount:,.2f}"


def format_creative_size(width: int | None, height: int | None) -> str:
    """
    Format creative dimensions for display.

    Args:
        width: Width in pixels
        height: Height in pixels

    Returns:
        Formatted dimensions string
    """
    if width is None or height is None:
        return "Unknown size"

    return f"{width}×{height}"


def format_ad_unit_path(ad_unit: dict[str, Any]) -> str:
    """
    Format ad unit for display with full path.

    Args:
        ad_unit: GAM ad unit object

    Returns:
        Formatted ad unit path
    """
    ad_unit_code = ad_unit.get("adUnitCode", "Unknown")
    parent_path = ad_unit.get("parentPath", [])

    if parent_path:
        full_path = " > ".join(parent_path + [ad_unit_code])
    else:
        full_path = ad_unit_code

    return full_path


def truncate_text(text: str, max_length: int = 50, suffix: str = "...") -> str:
    """
    Truncate text to specified length.

    Args:
        text: Text to truncate
        max_length: Maximum length before truncation
        suffix: Suffix to add when truncated

    Returns:
        Truncated text
    """
    if len(text) <= max_length:
        return text

    return text[: max_length - len(suffix)] + suffix


def format_duration(seconds: float) -> str:
    """
    Format duration in human-readable format.

    Args:
        seconds: Duration in seconds

    Returns:
        Formatted duration string
    """
    if seconds < 1:
        return f"{seconds * 1000:.0f}ms"
    elif seconds < 60:
        return f"{seconds:.1f}s"
    elif seconds < 3600:
        minutes = seconds / 60
        return f"{minutes:.1f}m"
    else:
        hours = seconds / 3600
        return f"{hours:.1f}h"
