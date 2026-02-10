"""Broadstreet adapter configuration schemas.

Defines the configuration models for Broadstreet products and adapters.
"""

from typing import Any

from pydantic import BaseModel, Field, field_validator

# Broadstreet template definitions
# Each template has required assets that must be provided
BROADSTREET_TEMPLATES = {
    # Basic types (no template - direct content)
    "static": {
        "name": "Static Image",
        "description": "Basic banner/display image ad",
        "required_assets": ["image"],
        "optional_assets": ["click_url", "alt_text"],
    },
    "html": {
        "name": "HTML/JavaScript",
        "description": "Custom HTML or JavaScript ad",
        "required_assets": ["html"],
        "optional_assets": ["click_url"],
    },
    "text": {
        "name": "Text Ad",
        "description": "Text-only ad",
        "required_assets": ["headline", "body"],
        "optional_assets": ["click_url"],
    },
    # Special templates (use setAdvertisementSource API)
    "cube_3d": {
        "name": "Amazing 3D Cube Gallery",
        "description": "6-sided rotating cube with images",
        "required_assets": [
            "front_image",
            "back_image",
            "left_image",
            "right_image",
            "top_image",
            "bottom_image",
        ],
        "optional_assets": [
            "front_caption",
            "back_caption",
            "left_caption",
            "right_caption",
            "top_caption",
            "bottom_caption",
            "click_url",
            "logo",
            "auto_rotate_ms",
        ],
        "api_source_type": "cube",
    },
    "youtube_video": {
        "name": "YouTube Video with Text",
        "description": "YouTube video embed with optional text overlay",
        "required_assets": ["youtube_url"],
        "optional_assets": ["headline", "body", "click_url", "autoplay"],
        "api_source_type": "youtube",
    },
    "push_pin": {
        "name": "Push Pin Photo",
        "description": "Pop-up bulletin board style ad",
        "required_assets": ["image"],
        "optional_assets": ["click_url", "caption", "pin_color"],
        "api_source_type": "pushpin",
    },
    "gallery": {
        "name": "Image Gallery",
        "description": "Multiple images in a slideshow",
        "required_assets": ["images"],  # List of image URLs
        "optional_assets": ["captions", "click_urls", "auto_rotate_ms"],
        "api_source_type": "gallery",
    },
    "native": {
        "name": "Native Ad",
        "description": "Native content ad matching site style",
        "required_assets": ["headline", "image"],
        "optional_assets": ["body", "sponsor", "click_url", "cta_text"],
        "api_source_type": "native",
    },
}


def get_template_info(template_type: str) -> dict[str, Any] | None:
    """Get template information including required assets.

    Args:
        template_type: Template type name

    Returns:
        Template info dict or None if not found
    """
    return BROADSTREET_TEMPLATES.get(template_type)


def validate_template_assets(template_type: str, assets: dict[str, Any]) -> tuple[bool, str | None]:
    """Validate that assets meet template requirements.

    Args:
        template_type: Template type name
        assets: Asset dictionary

    Returns:
        Tuple of (is_valid, error_message)
    """
    template = BROADSTREET_TEMPLATES.get(template_type)
    if not template:
        return False, f"Unknown template type: {template_type}"

    required = template.get("required_assets", [])
    missing = [asset for asset in required if asset not in assets or not assets[asset]]

    if missing:
        return False, f"Missing required assets for {template_type}: {', '.join(missing)}"

    return True, None


class CreativeSize(BaseModel):
    """Defines expected creative dimensions for a zone."""

    width: int = Field(..., description="Creative width in pixels")
    height: int = Field(..., description="Creative height in pixels")
    expected_count: int = Field(1, ge=1, description="Number of creatives expected for this size")


class ZoneTargeting(BaseModel):
    """Broadstreet zone targeting configuration."""

    zone_id: str = Field(..., description="Broadstreet zone ID")
    zone_name: str | None = Field(None, description="Human-readable zone name")
    sizes: list[CreativeSize] = Field(default_factory=list, description="Supported creative sizes")
    position: str | None = Field(None, description="Ad position (above_fold, below_fold)")


class BroadstreetImplementationConfig(BaseModel):
    """Configuration for creating Broadstreet campaigns/placements.

    This is stored in Product.implementation_config and controls how
    campaigns are created in Broadstreet when a media buy is executed.
    """

    # Zone/placement targeting (core Broadstreet concept)
    targeted_zone_ids: list[str] = Field(
        default_factory=list,
        description="Broadstreet zone IDs to target",
    )
    zone_targeting: list[ZoneTargeting] = Field(
        default_factory=list,
        description="Detailed zone targeting with sizes",
    )

    # Campaign naming
    campaign_name_template: str = Field(
        default="AdCP-{po_number}-{product_name}",
        description="Template for campaign naming. Supports: {po_number}, {product_name}, {advertiser_name}, {timestamp}",
    )

    # Pricing settings
    cost_type: str = Field(
        default="CPM",
        description="Pricing model: CPM or FLAT_RATE",
    )

    # Delivery settings
    delivery_rate: str = Field(
        default="EVEN",
        description="Delivery pacing: EVEN, FRONTLOADED, ASAP",
    )
    frequency_cap: int | None = Field(
        default=None,
        ge=1,
        description="Max impressions per user per day",
    )

    # Creative specifications
    creative_sizes: list[CreativeSize] = Field(
        default_factory=list,
        description="Expected creative sizes for this product",
    )

    # Ad format settings
    ad_format: str = Field(
        default="display",
        description="Primary ad format: display, html, text",
    )
    allow_html_creatives: bool = Field(
        default=True,
        description="Allow HTML/JavaScript creatives",
    )
    allow_text_creatives: bool = Field(
        default=True,
        description="Allow text-only creatives",
    )

    # Automation settings
    automation_mode: str = Field(
        default="manual",
        description="Automation mode: 'automatic', 'confirmation_required', 'manual'",
    )

    @field_validator("cost_type")
    @classmethod
    def validate_cost_type(cls, v: str) -> str:
        """Validate cost type is supported."""
        valid_types = {"CPM", "FLAT_RATE"}
        v_upper = v.upper()
        if v_upper not in valid_types:
            raise ValueError(f"Invalid cost_type '{v}'. Must be one of: {valid_types}")
        return v_upper

    @field_validator("delivery_rate")
    @classmethod
    def validate_delivery_rate(cls, v: str) -> str:
        """Validate delivery rate is supported."""
        valid_rates = {"EVEN", "FRONTLOADED", "ASAP"}
        v_upper = v.upper()
        if v_upper not in valid_rates:
            raise ValueError(f"Invalid delivery_rate '{v}'. Must be one of: {valid_rates}")
        return v_upper

    @field_validator("ad_format")
    @classmethod
    def validate_ad_format(cls, v: str) -> str:
        """Validate ad format is supported."""
        valid_formats = {"display", "html", "text"}
        v_lower = v.lower()
        if v_lower not in valid_formats:
            raise ValueError(f"Invalid ad_format '{v}'. Must be one of: {valid_formats}")
        return v_lower

    @field_validator("automation_mode")
    @classmethod
    def validate_automation_mode(cls, v: str) -> str:
        """Validate automation mode is supported."""
        valid_modes = {"automatic", "confirmation_required", "manual"}
        v_lower = v.lower()
        if v_lower not in valid_modes:
            raise ValueError(f"Invalid automation_mode '{v}'. Must be one of: {valid_modes}")
        return v_lower

    def get_zone_ids(self) -> list[str]:
        """Get all zone IDs from both targeted_zone_ids and zone_targeting."""
        zone_ids = set(self.targeted_zone_ids)
        for zt in self.zone_targeting:
            zone_ids.add(zt.zone_id)
        return list(zone_ids)

    def get_creative_sizes_for_zone(self, zone_id: str) -> list[CreativeSize]:
        """Get creative sizes for a specific zone."""
        for zt in self.zone_targeting:
            if zt.zone_id == zone_id:
                return zt.sizes
        # Fall back to global creative sizes
        return self.creative_sizes


def parse_implementation_config(config: dict[str, Any] | None) -> BroadstreetImplementationConfig:
    """Parse implementation config dict into validated model.

    Args:
        config: Raw implementation config dict

    Returns:
        Validated BroadstreetImplementationConfig
    """
    if not config:
        return BroadstreetImplementationConfig()
    return BroadstreetImplementationConfig.model_validate(config)
