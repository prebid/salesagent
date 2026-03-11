"""Google Ad Manager utility functions."""

import logging
from datetime import datetime

logger = logging.getLogger(__name__)


def extract_frequency_caps(line_item):
    """Extract frequency caps from a line item."""
    caps = []
    if hasattr(line_item, "frequencyCaps") and line_item.frequencyCaps:
        for cap in line_item.frequencyCaps:
            caps.append(
                {
                    "maxImpressions": cap.maxImpressions,
                    "numTimeUnits": cap.numTimeUnits,
                    "timeUnit": cap.timeUnit,
                }
            )
    return caps


def extract_creative_formats(line_item, creatives):
    """Extract creative formats from line item and associated creatives."""
    formats = set()

    # Get sizes from line item's creative placeholders
    if hasattr(line_item, "creativePlaceholders") and line_item.creativePlaceholders:
        for placeholder in line_item.creativePlaceholders:
            if hasattr(placeholder, "size"):
                size = placeholder.size
                format_str = f"display_{size.width}x{size.height}"
                formats.add(format_str)

    # Also check the actual creatives
    for creative in creatives:
        if hasattr(creative, "size"):
            format_str = f"display_{creative.size.width}x{creative.size.height}"
            formats.add(format_str)
        # Check for video creatives
        if hasattr(creative, "vastXmlUrl") or hasattr(creative, "vastRedirectUrl"):
            formats.add("video")

    return list(formats)


def convert_line_item_to_product_json(line_item, creatives):
    """Convert a GAM line item to a product JSON structure."""
    # Extract basic information
    product = {
        "product_id": f"gam_line_item_{line_item.id}",
        "name": line_item.name,
        "description": f"Imported from GAM line item: {line_item.name}",
        "formats": extract_creative_formats(line_item, creatives),
        "delivery_type": "guaranteed" if line_item.lineItemType == "STANDARD" else "non_guaranteed",
        "priority": getattr(line_item, "priority", 6),
        "targeting_template": {},
        "countries": [],
    }

    # Extract pricing information
    if hasattr(line_item, "costType"):
        if line_item.costType == "CPM":
            # Convert from micros to dollars (divide by 1,000,000 for micros, then by 1,000 for CPM)
            cpm_value = line_item.costPerUnit.microAmount / 1_000_000_000 if hasattr(line_item, "costPerUnit") else 0
            product["cpm"] = cpm_value
            product["price_model"] = "cpm"
        elif line_item.costType == "CPC":
            cpc_value = line_item.costPerUnit.microAmount / 1_000_000 if hasattr(line_item, "costPerUnit") else 0
            product["cpc"] = cpc_value
            product["price_model"] = "cpc"

    # Extract targeting from the line item
    if hasattr(line_item, "targeting") and line_item.targeting:
        targeting = line_item.targeting

        # Geo targeting
        if hasattr(targeting, "geoTargeting") and targeting.geoTargeting:
            geo = targeting.geoTargeting
            if hasattr(geo, "targetedLocations") and geo.targetedLocations:
                # Extract country codes from targeted locations
                countries = set()
                for location in geo.targetedLocations:
                    if hasattr(location, "type") and location.type == "COUNTRY":
                        if hasattr(location, "countryCode"):
                            countries.add(location.countryCode)
                        elif hasattr(location, "displayName"):
                            # Try to map display name to country code
                            country_map = {"United States": "US", "Canada": "CA", "United Kingdom": "GB"}
                            if location.displayName in country_map:
                                countries.add(country_map[location.displayName])
                product["countries"] = list(countries)

        # Technology targeting
        if hasattr(targeting, "technologyTargeting") and targeting.technologyTargeting:
            tech = targeting.technologyTargeting
            tech_targeting = {}

            if hasattr(tech, "deviceCategoryTargeting"):
                tech_targeting["device_categories"] = getattr(
                    tech.deviceCategoryTargeting, "targetedDeviceCategories", []
                )

            if hasattr(tech, "operatingSystemTargeting"):
                tech_targeting["operating_systems"] = getattr(
                    tech.operatingSystemTargeting, "targetedOperatingSystems", []
                )

            if hasattr(tech, "browserTargeting"):
                tech_targeting["browsers"] = getattr(tech.browserTargeting, "targetedBrowsers", [])

            if tech_targeting:
                product["targeting_template"]["technology"] = tech_targeting

        # Custom targeting
        if hasattr(targeting, "customTargeting") and targeting.customTargeting:
            # This would need to be parsed according to the custom targeting expression
            product["targeting_template"]["custom"] = {"expression": str(targeting.customTargeting)}

    # Extract frequency caps
    frequency_caps = extract_frequency_caps(line_item)
    if frequency_caps:
        product["frequency_caps"] = frequency_caps

    # Add dates
    if hasattr(line_item, "startDateTime"):
        product["start_date"] = datetime(
            line_item.startDateTime.date.year,
            line_item.startDateTime.date.month,
            line_item.startDateTime.date.day,
        ).isoformat()

    if hasattr(line_item, "endDateTime"):
        product["end_date"] = datetime(
            line_item.endDateTime.date.year, line_item.endDateTime.date.month, line_item.endDateTime.date.day
        ).isoformat()

    # Add status
    product["status"] = getattr(line_item, "status", "UNKNOWN")

    # Add implementation config for GAM specifics
    product["implementation_config"] = {
        "gam_line_item_id": line_item.id,
        "gam_order_id": getattr(line_item, "orderId", None),
        "line_item_type": getattr(line_item, "lineItemType", None),
        "cost_type": getattr(line_item, "costType", None),
        "discount_type": getattr(line_item, "discountType", None),
        "creative_rotation_type": getattr(line_item, "creativeRotationType", None),
    }

    return product
