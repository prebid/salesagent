"""
Targeting capabilities configuration.

Defines which targeting dimensions are available for overlay vs managed-only access.
This is critical for AEE (Ad Effectiveness Engine) integration.
"""

from typing import Any

from src.core.schemas import TargetingCapability

# Define targeting capabilities for the platform
TARGETING_CAPABILITIES: dict[str, TargetingCapability] = {
    # Geographic targeting - available for overlay
    "geo_country": TargetingCapability(
        dimension="geo_country", access="overlay", description="Country-level targeting using ISO 3166-1 alpha-2 codes"
    ),
    "geo_region": TargetingCapability(dimension="geo_region", access="overlay", description="State/province targeting"),
    "geo_metro": TargetingCapability(dimension="geo_metro", access="overlay", description="Metro/DMA targeting"),
    "geo_city": TargetingCapability(dimension="geo_city", access="overlay", description="City-level targeting"),
    "geo_zip": TargetingCapability(dimension="geo_zip", access="overlay", description="Postal code targeting"),
    # Device targeting - available for overlay
    "device_type": TargetingCapability(
        dimension="device_type",
        access="overlay",
        description="Device type targeting",
        allowed_values=["mobile", "desktop", "tablet", "ctv", "dooh", "audio"],
    ),
    "device_make": TargetingCapability(
        dimension="device_make", access="overlay", description="Device manufacturer targeting"
    ),
    "os": TargetingCapability(dimension="os", access="overlay", description="Operating system targeting"),
    "browser": TargetingCapability(dimension="browser", access="overlay", description="Browser targeting"),
    # Content targeting - available for overlay
    "content_category": TargetingCapability(
        dimension="content_category", access="overlay", description="IAB content category targeting"
    ),
    "content_language": TargetingCapability(
        dimension="content_language", access="overlay", description="Content language targeting"
    ),
    "content_rating": TargetingCapability(
        dimension="content_rating", access="overlay", description="Content rating targeting"
    ),
    # Media targeting - available for overlay
    "media_type": TargetingCapability(
        dimension="media_type",
        access="overlay",
        description="Media type targeting",
        allowed_values=["video", "display", "native", "audio", "dooh"],
    ),
    # Audience targeting - available for overlay
    "audience_segment": TargetingCapability(
        dimension="audience_segment", access="overlay", description="Third-party audience segments"
    ),
    # Frequency capping - available for overlay
    "frequency_cap": TargetingCapability(
        dimension="frequency_cap", access="overlay", description="Impression frequency limits"
    ),
    # AEE Signal Dimensions - MANAGED ONLY
    "key_value_pairs": TargetingCapability(
        dimension="key_value_pairs",
        access="managed_only",
        description="Key-value pairs for AEE signal integration",
        axe_signal=True,
    ),
    "aee_segment": TargetingCapability(
        dimension="aee_segment", access="managed_only", description="AEE-computed audience segments", axe_signal=True
    ),
    "aee_score": TargetingCapability(
        dimension="aee_score", access="managed_only", description="AEE effectiveness scores", axe_signal=True
    ),
    "aee_context": TargetingCapability(
        dimension="aee_context", access="managed_only", description="AEE contextual signals", axe_signal=True
    ),
    # Platform-specific - both overlay and managed
    "custom": TargetingCapability(dimension="custom", access="both", description="Platform-specific custom targeting"),
}


def get_overlay_dimensions() -> list[str]:
    """Get list of dimensions available for overlay targeting."""
    return [name for name, cap in TARGETING_CAPABILITIES.items() if cap.access in ["overlay", "both"]]


def get_managed_only_dimensions() -> list[str]:
    """Get list of dimensions that are managed-only."""
    return [name for name, cap in TARGETING_CAPABILITIES.items() if cap.access == "managed_only"]


def get_aee_signal_dimensions() -> list[str]:
    """Get list of dimensions used for AEE signals."""
    return [name for name, cap in TARGETING_CAPABILITIES.items() if cap.axe_signal]


# Explicit mapping from Targeting field names to capability dimension names.
# v3 structured fields (geo_countries, geo_regions, etc.) map directly without
# suffix-stripping.  Both inclusion and exclusion variants map to the same
# capability so exclusion fields are validated alongside inclusion fields.
FIELD_TO_DIMENSION: dict[str, str] = {
    # v3 geo fields — inclusion
    "geo_countries": "geo_country",
    "geo_regions": "geo_region",
    "geo_metros": "geo_metro",
    "geo_postal_areas": "geo_zip",
    # v3 geo fields — exclusion
    "geo_countries_exclude": "geo_country",
    "geo_regions_exclude": "geo_region",
    "geo_metros_exclude": "geo_metro",
    "geo_postal_areas_exclude": "geo_zip",
    # Device / OS / Browser
    "device_type_any_of": "device_type",
    "device_type_none_of": "device_type",
    "os_any_of": "os",
    "os_none_of": "os",
    "browser_any_of": "browser",
    "browser_none_of": "browser",
    # Content
    "content_cat_any_of": "content_category",
    "content_cat_none_of": "content_category",
    # Media
    "media_type_any_of": "media_type",
    "media_type_none_of": "media_type",
    # Audience
    "audiences_any_of": "audience_segment",
    "audiences_none_of": "audience_segment",
    # Frequency capping
    "frequency_cap": "frequency_cap",
    # Managed-only fields
    "key_value_pairs": "key_value_pairs",
    # Custom
    "custom": "custom",
}


def validate_overlay_targeting(targeting: dict[str, Any]) -> list[str]:
    """Validate that targeting only uses allowed overlay dimensions.

    Uses an explicit field-to-dimension mapping (FIELD_TO_DIMENSION) instead of
    suffix-stripping heuristics.  Both inclusion and exclusion field variants
    are mapped so that exclusion fields are validated alongside their inclusion
    counterparts.

    Returns list of violations (managed-only dimensions used).
    """
    violations = []
    managed_only = set(get_managed_only_dimensions())

    for key in targeting:
        dimension = FIELD_TO_DIMENSION.get(key)
        if dimension and dimension in managed_only:
            violations.append(f"{key} is managed-only and cannot be set via overlay")

    return violations
